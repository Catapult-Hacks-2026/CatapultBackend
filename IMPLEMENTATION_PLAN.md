# IMPLEMENTATION PLAN — Autonomous AI Procurement Agent

## Context

This is the backend for a hackathon project (Purdue Catapult). The system negotiates B2B procurement contracts via real-time phone calls and text webhooks. The frontend (Next.js/React dashboard) is handled separately.

**Already scaffolded (do not recreate):**
- `app/core/config.py` — pydantic-settings config
- `app/core/database.py` — SQLite init + ChromaDB setup
- `app/models/enums.py` — NegotiationStatus, Strategy, MessageRole
- `app/models/schemas.py` — BuyerConfig, VendorOffer, AgentAction, GuardrailResult, ScoringBreakdown, API request/response models
- `app/services/scoring.py` — NumPy utility scoring with `score_offer()` and `suggest_pivot()`
- `app/services/rag.py` — ChromaDB retrieval stub (functional but minimal)
- `requirements.txt`, `.env.example`, `README.md`

**All new code goes in the existing `app/` package. Use the existing schemas and imports.**

---

## Task 1: FastAPI App Entrypoint (`app/main.py`)

Create the main FastAPI application.

- Import and include all routers (from Tasks 5-7) under appropriate prefixes
- Add CORS middleware allowing all origins (hackathon — no restrictions)
- Add a `lifespan` handler that calls `init_db()` from `app.core.database` on startup
- Mount a basic health check at `GET /`
- Set up a `ConnectionManager` class for WebSocket broadcast:
  - Maintains a dict of `negotiation_id -> list[WebSocket]`
  - Methods: `connect(negotiation_id, ws)`, `disconnect(negotiation_id, ws)`, `broadcast(negotiation_id, data: dict)`
  - Export a singleton instance that routers import

---

## Task 2: Guardrails / Rule Engine (`app/services/guardrails.py`)

Deterministic validation layer — the "air gap" between the LLM and the outside world.

**Input:** `AgentAction` (the LLM's proposed response) + `BuyerConfig` (hard constraints)
**Output:** `GuardrailResult` with pass/fail + violation list

Rules to enforce:
1. If `AgentAction.counter_offer` exists, its `unit_price` must be ≤ `config.max_unit_price`
2. Counter-offer `shipping_cost` must be ≤ `config.max_shipping_cost`
3. Counter-offer `payment_terms_days` must be ≥ `config.min_payment_terms`
4. Counter-offer `delivery_days` must be ≤ `config.max_delivery_days`
5. If `should_accept` is True, score the offer being accepted via `score_offer()` and verify it meets `min_acceptable_utility`
6. The agent must not accept on round 1 (always counter at least once)
7. The message text must not contain phrases like "I accept", "deal", "agreed" unless `should_accept` is True

If any rule fails, set `passed=False`, populate `violations`, and set `adjusted_action` to None (caller must re-prompt the LLM).

---

## Task 3: RAG Service Enhancement (`app/services/rag.py`)

Enhance the existing stub to be more useful.

- Add a function `add_negotiation_to_history(negotiation_id, vendor_name, messages, final_offer, outcome)` that:
  - Constructs a document string summarizing the negotiation
  - Stores it in ChromaDB with metadata: `vendor_name`, `date`, `outcome` (accepted/rejected/escalated), `final_unit_price`, `discount_pct` vs opening price, `deal_type`
- Add `retrieve_competitor_context(product_category, top_k)` that queries without vendor filter to find competing vendor deals
- Update `retrieve_vendor_context` to also return distance scores so the dashboard can show retrieval confidence

Also create `app/seed.py`:
- Generate 25-30 synthetic historical negotiations across 5-6 vendor names
- Vary: vendor names, product categories, price ranges, discount patterns, seasonal behavior (e.g., "Q4 discounts")
- Insert all into ChromaDB via `add_negotiation_to_history`
- Runnable as `python -m app.seed`

---

## Task 4: LLM Orchestrator (`app/services/llm.py`)

Interface with Anthropic Claude API to generate negotiation responses.

**Function: `generate_agent_response(negotiation_context: dict) -> AgentAction`**

`negotiation_context` contains:
- `vendor_name`, `strategy` (from enums), `round_number`
- `buyer_config` (BuyerConfig dict)
- `conversation_history` (list of messages)
- `current_offer` (VendorOffer or None)
- `scoring_breakdown` (ScoringBreakdown dict — current offer's score)
- `pivot_suggestions` (from `suggest_pivot()`)
- `rag_context` (list of historical records from RAG)

Build a system prompt that:
1. Defines the agent's role as a procurement negotiator
2. Injects the strategy persona (use the enum — aggressive/balanced/volume/relationship) with distinct behavioral instructions for each
3. Provides the buyer's constraints and current scoring breakdown
4. Includes the RAG historical context as "your memory of past dealings"
5. Includes pivot suggestions as "mathematical analysis suggests focusing on..."
6. Instructs the model to respond in **structured JSON** matching the `AgentAction` schema: `{ counter_offer, message, reasoning, should_accept, should_escalate }`

Use `anthropic.Anthropic()` client, `model=settings.negotiation_model`, `max_tokens=1024`.
Parse the response into an `AgentAction`. If JSON parsing fails, retry once with a stricter prompt.

---

## Task 5: Negotiation Orchestrator (`app/services/negotiation.py`)

The FSM (finite state machine) that ties everything together.

**Function: `process_vendor_input(negotiation_id: str, vendor_message: str, vendor_offer: VendorOffer | None) -> dict`**

Flow:
1. Load negotiation from SQLite by ID
2. Parse `config` JSON into `BuyerConfig`
3. If `vendor_offer` is None, call the LLM to extract structured offer data from `vendor_message` (separate prompt — "extract the offer from this text as JSON")
4. Score the offer via `score_offer()`
5. Check if `round_number >= max_rounds` — if so, escalate
6. Run `suggest_pivot()` to get optimization suggestions
7. Retrieve RAG context via `retrieve_vendor_context()`
8. Build `negotiation_context` dict and call `generate_agent_response()`
9. Validate the LLM output via guardrails — if fails, re-prompt up to 2 times
10. Save the vendor message and agent response to the `messages` table
11. Update negotiation: `round_number += 1`, `current_offer`, `utility_score`, `status`
12. Return a dict with: `agent_message`, `scoring_breakdown`, `rag_context_used`, `guardrail_log`, `status`

This return dict is what gets broadcast via WebSocket and sent back to the vendor.

---

## Task 6: Voice Service (`app/services/voice.py`)

Real-time phone negotiation using Twilio + Deepgram + TTS.

### Twilio Call Flow:
1. `initiate_call(negotiation_id, vendor_phone_number)` — uses Twilio client to place outbound call, sets webhook URL to `{BASE_URL}/voice/twilio-stream/{negotiation_id}`
2. Twilio webhook handler returns TwiML that:
   - Sends an opening greeting via `<Say>` or `<Play>` (TTS audio URL)
   - Opens a `<Stream>` WebSocket back to our server at `{BASE_URL}/voice/media-stream/{negotiation_id}`

### Media Stream WebSocket (`/voice/media-stream/{negotiation_id}`):
This is the real-time audio processing loop.

1. Receive Twilio media stream events (mulaw audio chunks)
2. Forward audio chunks to Deepgram's streaming STT API for real-time transcription
3. Deepgram returns partial + final transcripts
4. On each final transcript (complete vendor utterance):
   a. Append to `call_sessions.transcript`
   b. Call `process_vendor_input()` from the negotiation orchestrator
   c. Take the agent's response message
   d. Send it to TTS API (ElevenLabs or Cartesia) to get audio
   e. Send the audio back to Twilio via the media stream as mulaw chunks
5. Also broadcast transcript + scoring updates to the dashboard WebSocket

### TTS Helper:
- `text_to_speech(text: str) -> bytes` — calls ElevenLabs/Cartesia API, returns audio bytes
- Convert to mulaw/8000Hz format that Twilio expects (use `audioop` or ffmpeg subprocess)

### Key considerations:
- Use `asyncio` for concurrent STT/TTS streaming
- Twilio media streams send base64-encoded mulaw audio — decode before sending to Deepgram
- Deepgram SDK has a streaming/live transcription mode — use that, not batch
- Keep a silence detection heuristic: if no final transcript for 2+ seconds, the vendor is done talking → trigger agent response

---

## Task 7: API Routers

### `app/routers/negotiations.py`
REST + WebSocket endpoints for the dashboard.

- `POST /negotiations/` — create new negotiation from `CreateNegotiationRequest`, insert into SQLite, return `NegotiationResponse`
- `GET /negotiations/` — list all negotiations with current status and utility scores
- `GET /negotiations/{id}` — full negotiation detail including all messages
- `GET /negotiations/{id}/messages` — message history
- `GET /negotiations/{id}/scoring` — current scoring breakdown + pivot suggestions
- `PATCH /negotiations/{id}` — update config or strategy mid-negotiation
- `POST /negotiations/{id}/approve` — human approves the current deal (sets status=accepted)
- `POST /negotiations/{id}/escalate` — human takes over
- `WebSocket /negotiations/{id}/ws` — live updates (scoring changes, new messages, guardrail events)

### `app/routers/webhooks.py`
Inbound vendor text communication.

- `POST /webhooks/vendor` — receives `InboundVendorMessage`, calls `process_vendor_input()`, returns agent response
- `POST /webhooks/simulate` — convenience endpoint for demos: takes a vendor name + message, auto-creates or finds the negotiation, processes the message. Makes it easy to demo with Postman/curl.

### `app/routers/voice.py`
Twilio voice endpoints.

- `POST /voice/call/{negotiation_id}` — initiates outbound call to vendor
- `POST /voice/twilio-stream/{negotiation_id}` — Twilio webhook callback, returns TwiML
- `WebSocket /voice/media-stream/{negotiation_id}` — Twilio media stream handler (from Task 6)
- `GET /voice/calls/{negotiation_id}` — get call transcript and status

---

## Task 8: Adversarial Demo Mode (`app/services/adversarial.py`)

For the hackathon demo — two AI agents negotiate against each other.

- `run_adversarial_negotiation(buyer_config: BuyerConfig, seller_config: dict, max_rounds: int = 10) -> list[dict]`
- The seller config has: `min_unit_price`, `target_unit_price`, `opening_price`, `willing_to_offer_free_shipping_below`, etc.
- Each round:
  1. Seller agent generates an offer (separate LLM call with seller persona)
  2. Buyer agent processes it through the full pipeline (scoring → RAG → LLM → guardrails)
  3. Buyer's counter goes back to seller
  4. Log each round with both sides' reasoning
- Return the full exchange with scores at each step
- Expose via `POST /negotiations/adversarial` endpoint

This is the most impressive demo moment — show it on the dashboard with live WebSocket updates as rounds play out.

---

## Execution Order

Parallelize across team members:

| Person | Tasks | Depends On |
|--------|-------|------------|
| A | Task 1 (main.py) + Task 7 (routers) | Schemas exist (done) |
| B | Task 4 (LLM) + Task 2 (guardrails) | Schemas + scoring (done) |
| C | Task 5 (orchestrator) | Tasks 2, 3, 4 |
| D | Task 6 (voice) | Task 5 |
| E | Task 3 (RAG + seed) | database.py (done) |
| F | Task 8 (adversarial) | Tasks 4, 5 |

**Critical path:** Tasks 2+4 → Task 5 → Task 6. Get the LLM and guardrails working first, then wire the orchestrator, then voice last.

---

## Testing Without Full Integration

- **Scoring:** Unit test with hardcoded offers and configs — verify utility math
- **Guardrails:** Unit test with intentionally bad AgentActions — verify rejections
- **LLM:** Test standalone with a mock negotiation context — verify JSON output parsing
- **Orchestrator:** Mock the LLM and RAG services, test the flow with a fake vendor message
- **Voice:** Use Twilio's test credentials + ngrok to test call flow locally
- **Adversarial:** Run after orchestrator works — it's just a loop calling the same pipeline

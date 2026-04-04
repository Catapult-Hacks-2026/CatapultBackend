# Orchestration Migration Plan

Migrate the AI orchestration patterns from `catapult_main` into this branch's procurement-domain codebase. The goal is to adopt main's superior architectural patterns (LangGraph state machine, modular voice pipeline, separated LLM concerns) while keeping this branch's database schema, domain models, and service logic intact.

## Guiding Principles

- **No domain regression**: All procurement schemas, enums, tables, and services stay as-is.
- **Adapt, don't copy**: Main's code references hotel-specific APIs, schemas, and OpenAI. Each file must be rewritten for procurement + Anthropic.
- **Incremental**: Each phase produces a working, testable system. No phase depends on a later one.
- **Existing tests must pass** after every phase.
- **Debug statements**: The code should create lots of debug statements to make it far easier to debug later

---

## Phase 1: Foundation — Modular Voice I/O Layer

**Goal**: Replace the monolithic `services/voice.py` media handling with main's clean separation of Twilio, STT, and TTS concerns.

### 1.1 Port `app/voice/twilio_bridge.py` (as-is)

- **Source**: `catapult_main/app/voice/twilio_bridge.py` (64 lines)
- **Target**: `app/voice/twilio_bridge.py`
- **Changes needed**: None — this file is domain-agnostic. Copy directly.
- **Creates**: `TwilioBridge` class (receive_event, send_audio, send_mark, clear_playback)

### 1.2 Port `app/voice/deepgram_stt.py` (adapt from AssemblyAI)

- **Source**: `catapult_main/app/voice/assemblyai_stt.py` (110 lines)
- **Target**: `app/voice/deepgram_stt.py`
- **Changes needed**:
  - Replace AssemblyAI WebSocket protocol with Deepgram's streaming API
  - Update `_WORD_BOOST` list: replace hotel terms ("nightly", "check-in", "check-out") with procurement terms ("unit price", "shipping", "payment terms", "net 30", "bulk discount", "delivery lead time", "MOQ")
  - Use `settings.deepgram_api_key` instead of `settings.assemblyai_api_key`
  - Keep same callback interface: `on_partial(text)`, `on_final(text, confidence, words)`, `on_error(exc)`
- **Creates**: `DeepgramRealtimeSTT` class with identical public API to `AssemblyAIRealtimeSTT`

### 1.3 Port `app/voice/elevenlabs_tts.py` (as-is + Cartesia option)

- **Source**: `catapult_main/app/voice/elevenlabs_tts.py` (95 lines)
- **Target**: `app/voice/tts.py`
- **Changes needed**:
  - Keep ElevenLabs streaming class as-is
  - Add `CartesiaStreamingTTS` with same interface (connect, send_text_chunk, receive_audio, close)
  - Add factory: `get_streaming_tts(provider: str)` that returns the right class based on `settings.tts_provider`
  - Rishu's existing `text_to_speech()` and `stream_tts_chunks()` in services/voice.py become thin wrappers or get replaced by the streaming class
- **Creates**: `ElevenLabsStreamingTTS`, `CartesiaStreamingTTS`, `get_streaming_tts()`

### 1.4 Create `app/voice/__init__.py`

Export public classes for clean imports.

### Validation
- Existing `handle_media_stream()` in services/voice.py still works (not yet replaced)
- New voice modules can be imported and instantiated independently
- Unit test: TwilioBridge encode/decode roundtrip, TTS factory returns correct class

---

## Phase 2: LLM Abstraction Layer

**Goal**: Create a provider-agnostic LLM interface that supports both Anthropic (current) and can accommodate OpenAI patterns from main.

### 2.1 Create `app/llm/client.py` (adapt from openai_client.py)

- **Source**: `catapult_main/app/llm/openai_client.py` (63 lines)
- **Target**: `app/llm/client.py`
- **Changes needed**:
  - Primary backend: Anthropic Claude (using existing `services/llm.py:invoke_json()` logic)
  - Keep same public API: `invoke_json(system, user, model, max_tokens, temperature) -> dict` and `stream_text(system, user, model, max_tokens, temperature) -> AsyncGenerator[str]`
  - Anthropic streaming via `client.messages.stream()` for `stream_text()`
  - Move the Anthropic client singleton from services/llm.py here
- **Creates**: `get_anthropic_client()`, `invoke_json()`, `stream_text()`

### 2.2 Create `app/llm/prompts.py` (adapt from main's prompts.py)

- **Source**: `catapult_main/app/llm/prompts.py` (62 lines)
- **Target**: `app/llm/prompts.py`
- **Changes needed**:
  - Replace hotel rate extraction prompt → procurement offer extraction (unit_price, shipping, payment_terms, delivery, MOQ, bulk discounts)
  - Replace hotel negotiation brain prompt → procurement negotiation prompt (keep the good principles: never reveal budget, anchor low, be persistent, use silence)
  - Keep `RESPONSE_GENERATION_SYSTEM` mostly as-is (natural spoken language guidance)
  - Keep `POST_CALL_ANALYSIS_SYSTEM` mostly as-is (outcome analysis)
  - Add strategy-specific prompt fragments (AGGRESSIVE, BALANCED, VOLUME, RELATIONSHIP) — already exist in services/llm.py, consolidate here
- **Creates**: `FACT_EXTRACTION_SYSTEM`, `NEGOTIATION_BRAIN_SYSTEM`, `RESPONSE_GENERATION_SYSTEM`, `POST_CALL_ANALYSIS_SYSTEM`, `STRATEGY_INSTRUCTIONS`

### 2.3 Create `app/llm/fact_extractor.py` (adapt from main)

- **Source**: `catapult_main/app/llm/fact_extractor.py` (38 lines)
- **Target**: `app/llm/fact_extractor.py`
- **Changes needed**:
  - Return `ExtractedFacts` (from schemas.py) instead of `HotelQuote`
  - Use Anthropic via `client.invoke_json()` instead of OpenAI
  - Extract: unit_price, shipping_cost, payment_terms_days, delivery_days, fees, discount_authority, negotiation_openness, refusals
  - Use procurement fact extraction prompt from 2.2
- **Replaces**: `extract_facts_fast()` from services/llm.py (which is a regex/heuristic fallback — keep as fallback)

### 2.4 Create `app/llm/negotiation_brain.py` (adapt from main)

- **Source**: `catapult_main/app/llm/negotiation_brain.py` (91 lines)
- **Target**: `app/llm/negotiation_brain.py`
- **Changes needed**:
  - Replace `score_hotel_quote()` → `score_offer()` from services/scoring.py
  - Replace `hotel_target` context → `BuyerConfig` context (target_unit_price, max_unit_price, etc.)
  - Replace `MoveType` enum → `NegotiationAction` enum (already in enums.py)
  - Use Anthropic via `client.invoke_json()` / `client.stream_text()`
  - `_build_brain_context()` pulls from: BuyerConfig, current offer, scoring breakdown, vendor priors (memory.py), RAG context, research brief
  - `decide_move()` returns action + reasoning + should_terminate + counter_offer
  - `generate_response_streaming()` returns async generator of text chunks
- **Replaces**: `decide_move_fast()` and `generate_reply_fast()` from services/llm.py

### 2.5 Create `app/llm/__init__.py`

Export public functions.

### 2.6 Refactor `app/services/llm.py`

- Remove `extract_facts_fast()`, `decide_move_fast()`, `generate_reply_fast()` (moved to llm/ modules)
- Remove Anthropic client singleton (moved to llm/client.py)
- Keep `generate_agent_response()` and `extract_offer_from_message()` — these are the text-channel entry points used by `process_vendor_input()`
- Update imports to use new llm/ modules internally

### Validation
- `process_vendor_input()` still works via services/llm.py (text channel unchanged)
- New LLM modules can be called independently for voice channel
- Test: `invoke_json()` returns valid dict, `stream_text()` yields strings, `extract_facts()` returns ExtractedFacts

---

## Phase 3: Voice Pipeline

**Goal**: Port main's `VoicePipeline` class adapted for procurement + Anthropic + Deepgram.

### 3.1 Create `app/voice/pipeline.py` (adapt from main)

- **Source**: `catapult_main/app/voice/pipeline.py` (185 lines)
- **Target**: `app/voice/pipeline.py`
- **Changes needed**:
  - Replace `AssemblyAIRealtimeSTT` → `DeepgramRealtimeSTT` (from Phase 1.2)
  - Replace `ElevenLabsStreamingTTS` → `get_streaming_tts()` (from Phase 1.3)
  - Replace `validate_agent_move()` from hotel.guardrails → `validate_agent_action()` from services/guardrails.py
  - Replace `extract_facts_from_utterance()` call → use `app/llm/fact_extractor.py` (Phase 2.3)
  - Replace `decide_move()` call → use `app/llm/negotiation_brain.py` (Phase 2.4)
  - Replace `generate_response_streaming()` → use negotiation_brain version (Phase 2.4)
  - Keep: `_inbound_loop()`, `_monitor_loop()`, `_process_utterance()`, `_decide_with_guardrails()`, `_speak()`, `signal_done()` — same structure, different backends
  - Use `settings.max_call_duration_seconds` for monitor loop (add to config if missing)
  - Callbacks: `on_quote_received(extracted_facts)`, `on_session_end(outcome)`
- **Creates**: `VoicePipeline` class with same lifecycle as main's

### Validation
- Pipeline can be instantiated with mock TwilioBridge
- STT → LLM → TTS flow works end-to-end in isolation
- Guardrail retry logic triggers correctly on violations

---

## Phase 4: Worker Orchestration (State Machine)

**Goal**: Port main's LangGraph worker graph adapted for procurement domain.

### 4.1 Create `app/orchestration/__init__.py`

### 4.2 Adapt session locking — `app/orchestration/session_lock.py`

- **Source**: `catapult_main/app/orchestration/session_lock.py` (67 lines)
- **Target**: `app/orchestration/session_lock.py`
- **Changes needed**:
  - Replace `hotel_id` key → `vendor_name` or `negotiation_id` key
  - Integrate with existing Redis-based `session_locks` table (database.py already has it)
  - Use `cache.py` for Redis lock ops where available, fall back to in-process asyncio locks
  - Respect `settings.session_lock_ttl` (900s)
- **Replaces**: In-process-only locking from main with hybrid Redis + asyncio approach

### 4.3 Create `app/orchestration/worker_state.py` (new, adapted from main's WorkerSessionState)

- **Purpose**: Define the state schema for the LangGraph state machine
- **Fields** (adapted from hotel to procurement):
  - `negotiation_id: str`
  - `vendor_name: str`
  - `vendor_phone: str`
  - `product_category: str`
  - `campaign_id: str | None`
  - `buyer_config: dict` (serialized BuyerConfig)
  - `session_status: str`
  - `transcript_window: list[str]`
  - `latest_offer: dict | None` (serialized VendorOffer)
  - `extracted_facts: dict | None` (serialized ExtractedFacts)
  - `vendor_priors: dict | None` (from memory.py)
  - `rag_context: list[dict]`
  - `research_brief: dict | None`
  - `scoring_breakdown: dict | None`
  - `objections_used: list[str]`
  - `manager_reached: bool`
  - `callback_requested: bool`
  - `continue_call: bool`
  - `next_action: str | None`
  - `final_outcome: str | None`
  - `call_sid: str | None`
  - `lock_acquired: bool`
- **Creates**: `WorkerSessionState` TypedDict for LangGraph

### 4.4 Create `app/orchestration/worker_nodes.py` (adapt from main)

- **Source**: `catapult_main/app/orchestration/worker_nodes.py` (188 lines)
- **Target**: `app/orchestration/worker_nodes.py`
- **Changes needed per node**:

| Node | Main's version | Procurement adaptation |
|------|---------------|----------------------|
| `load_context_node` | Fetches `/api/hotels/{id}` via httpx | Read directly from SQLite via `get_db()` + load vendor priors from `memory.py:load_vendor_priors()` + RAG context from `rag.py` + research brief from `research.py` |
| `load_memory_node` | Phase 2 stub | Call `memory.py:load_vendor_priors()` and `load_negotiation_working_memory_from_redis()` — already implemented |
| `acquire_lock_node` | In-process asyncio lock by hotel_id | Use Phase 4.2 hybrid lock by negotiation_id |
| `start_voice_node` | Twilio call to hotel phone | Use existing `voice.py:initiate_call(negotiation_id, vendor_phone)` |
| `listen_node` | Marker (VoicePipeline handles) | Same — marker node |
| `extract_facts_node` | Marker | Same — marker, pipeline calls `llm/fact_extractor.py` |
| `sync_quote_node` | POST to `/api/quotes/` | Write directly to `quote_events` and `latest_quotes` tables via `get_db()` |
| `check_cross_session_node` | Phase 4 stub | Query `latest_quotes` for competing offers on same product_category |
| `decide_move_node` | Marker | Same — marker, pipeline calls `llm/negotiation_brain.py` |
| `speak_node` | Marker | Same — marker |
| `check_terminate_node` | Max rounds + acceptance check | Use existing logic: max_rounds, utility threshold, escalation conditions |
| `post_call_node` | OpenAI post-call analysis | Use Anthropic via `llm/client.py:invoke_json()` with POST_CALL_ANALYSIS prompt. Call `memory.py:extract_memory_candidates()` + `store_memory_candidates()`. Archive to RAG via `rag.py:add_negotiation_to_history()`. Refresh Redis working memory. |
| `emit_memory_node` | Phase 2 stub | Call `memory.py:extract_memory_candidates()` — already implemented |
| `release_lock_node` | Release asyncio lock | Use Phase 4.2 hybrid lock release |

### 4.5 Create `app/orchestration/worker_graph.py` (adapt from main)

- **Source**: `catapult_main/app/orchestration/worker_graph.py` (123 lines)
- **Target**: `app/orchestration/worker_graph.py`
- **Changes needed**:
  - Use `WorkerSessionState` from Phase 4.3 instead of hotel state
  - Same graph topology: load_context → load_memory → acquire_lock → (route) → start_voice → [listen → extract → sync → cross_session → decide → speak → check_terminate] loop → post_call → emit_memory → release_lock
  - `WorkerSession` class wraps graph + `VoicePipeline` (Phase 3)
  - `handle_media_stream_connected(websocket)` creates `TwilioBridge` + `VoicePipeline`, wires callbacks
  - Pipeline callbacks wire to node state updates (quote_received → sync_quote, session_end → check_terminate)
  - Registry: `register_worker()`, `get_active_worker()`
- **Dependency**: `langgraph` package — add to requirements.txt

### Validation
- Worker graph can be built and nodes execute in sequence
- Lock acquire/release works with Redis and in-memory fallback
- End-to-end: create negotiation → start worker → graph executes → call completes → post-call analysis stored
- Existing `process_vendor_input()` text path still works independently

---

## Phase 5: Router Integration

**Goal**: Wire the new orchestration layer into the FastAPI routers.

### 5.1 Update `app/routers/voice.py`

- **Current**: Calls `initiate_call()`, `build_twiml_response()`, `handle_media_stream()` from services/voice.py
- **New endpoints** (keep existing, add new):
  - `POST /voice/worker/{negotiation_id}/start` — Creates WorkerSession, registers it, initiates call via graph
  - `POST /voice/twilio-stream/{negotiation_id}` — Returns TwiML (update to use `get_active_worker()` if worker exists, fall back to old path)
  - `WS /voice/media-stream/{negotiation_id}` — Update to delegate to `worker.handle_media_stream_connected(websocket)` when worker exists, fall back to old `handle_media_stream()`
  - `GET /voice/calls/{negotiation_id}` — Keep as-is
- **Backward compatibility**: Old direct-call path still works. Worker path is opt-in via `/worker/start`.

### 5.2 Update `app/services/campaign.py`

- **Current**: `_execute_job()` calls `initiate_call()` directly
- **Change**: When executing a campaign job, create a `WorkerSession` and run the graph instead of calling `initiate_call()` + `handle_media_stream()` directly
- This gives campaigns the full orchestration pipeline (context loading, memory, locking, post-call analysis)

### 5.3 Update `app/main.py`

- No changes needed if router paths don't change

### Validation
- `POST /voice/worker/{negotiation_id}/start` creates worker and initiates graph
- Twilio callback hits `/twilio-stream/` → returns TwiML → media stream connects to worker
- Campaign jobs use worker graph path
- Old direct path still works for backward compatibility

---

## Phase 6: Cleanup and Config

### 6.1 Fix `scoring.py` bug

- Add `compute_campaign_job_priority()` function (exists in main's scoring.py, missing from this branch)
- This fixes the ImportError in campaign.py

### 6.2 Update `requirements.txt`

- Add: `langgraph` (for state machine orchestration)
- Verify: `deepgram-sdk`, `websockets`, `httpx` already present

### 6.3 Update `app/core/config.py`

- Add `max_call_duration_seconds: int = 480` if not present
- Add `worker_concurrency_limit: int = 5` if not present
- Verify all voice/LLM settings are present

### 6.4 Deprecate inlined voice logic

- Mark `WorkerSession` dataclass in services/voice.py as deprecated
- Mark `load_vendor_context()` in services/voice.py as deprecated (replaced by load_context_node)
- Mark `extract_facts_fast()`, `decide_move_fast()`, `generate_reply_fast()` in services/llm.py as deprecated
- Do NOT delete yet — keep for fallback until orchestration path is proven stable

### 6.5 Update `CONTEXT.md`

- Document new `app/voice/` module structure
- Document new `app/llm/` module structure
- Document new `app/orchestration/` module structure
- Update architecture diagram

### Validation
- Full test suite passes
- Campaign smoke test works end-to-end
- Both old (direct) and new (worker graph) voice paths work

---

## Dependency Graph

```
Phase 1 (Voice I/O)     Phase 2 (LLM Layer)
    │                        │
    └────────┬───────────────┘
             │
        Phase 3 (Pipeline)
             │
        Phase 4 (Worker Graph)
             │
        Phase 5 (Router Integration)
             │
        Phase 6 (Cleanup)
```

Phases 1 and 2 are independent and can be worked in parallel.

---

## Files Created/Modified Summary

### New files (13)
| File | Phase | Source |
|------|-------|--------|
| `app/voice/__init__.py` | 1.4 | New |
| `app/voice/twilio_bridge.py` | 1.1 | Direct copy from main |
| `app/voice/deepgram_stt.py` | 1.2 | Adapted from main's assemblyai_stt.py |
| `app/voice/tts.py` | 1.3 | Adapted from main's elevenlabs_tts.py + Cartesia |
| `app/llm/__init__.py` | 2.5 | New |
| `app/llm/client.py` | 2.1 | Adapted from main's openai_client.py |
| `app/llm/prompts.py` | 2.2 | Adapted from main's prompts.py |
| `app/llm/fact_extractor.py` | 2.3 | Adapted from main's fact_extractor.py |
| `app/llm/negotiation_brain.py` | 2.4 | Adapted from main's negotiation_brain.py |
| `app/voice/pipeline.py` | 3.1 | Adapted from main's pipeline.py |
| `app/orchestration/__init__.py` | 4.1 | New |
| `app/orchestration/session_lock.py` | 4.2 | Adapted from main + Redis |
| `app/orchestration/worker_state.py` | 4.3 | New |
| `app/orchestration/worker_nodes.py` | 4.4 | Adapted from main |
| `app/orchestration/worker_graph.py` | 4.5 | Adapted from main |

### Modified files (7)
| File | Phase | Change |
|------|-------|--------|
| `app/services/llm.py` | 2.6 | Remove inlined fast functions, update imports |
| `app/routers/voice.py` | 5.1 | Add worker start endpoint, update media stream |
| `app/services/campaign.py` | 5.2 | Use worker graph for job execution |
| `app/services/scoring.py` | 6.1 | Add compute_campaign_job_priority() |
| `requirements.txt` | 6.2 | Add langgraph |
| `app/core/config.py` | 6.3 | Add missing settings |
| `CONTEXT.md` | 6.5 | Document new modules |

### Not modified (kept as-is)
- `app/core/database.py` — schema already supports all needed tables
- `app/core/cache.py` — Redis layer already implemented
- `app/services/negotiation.py` — text-channel flow unchanged
- `app/services/memory.py` — already has vendor priors, candidates, working memory
- `app/services/events.py` — pub/sub already works
- `app/services/rag.py` — unchanged
- `app/services/research.py` — unchanged
- `app/services/guardrails.py` — unchanged
- `app/models/enums.py` — already has NegotiationAction, CampaignStatus, etc.
- `app/models/schemas.py` — already has ExtractedFacts, WorkerEvent, etc.
- `app/routers/negotiations.py` — unchanged
- `app/routers/campaigns.py` — unchanged
- All test files — unchanged

# CatapultBackend — Project Context

Autonomous AI Procurement Agent built with Python/FastAPI. Automates B2B vendor negotiations via phone calls (Twilio) and text, using LLM orchestration (Anthropic Claude) with guardrails, RAG-powered vendor context, and deterministic scoring.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Framework | FastAPI 0.115.0, Uvicorn |
| Language | Python 3.11+ |
| Database | SQLite (state) + ChromaDB (vector store) |
| Voice | Twilio (calls), Deepgram (STT), ElevenLabs/Cartesia (TTS) |
| AI/LLM | Anthropic Claude (sonnet for negotiation, opus for research) |
| Caching | Redis (optional, in-memory fallback) |
| Scoring | NumPy weighted utility |
| Real-time | WebSockets |

## Project Structure

```
app/
├── main.py                        # FastAPI app, CORS, lifespan, WebSocket manager
├── seed.py                        # Seed ChromaDB with synthetic negotiation history
├── core/
│   ├── config.py                  # Pydantic BaseSettings
│   ├── database.py                # SQLite schema + ChromaDB client
│   └── cache.py                   # Redis cache (async, in-memory fallback)
├── models/
│   ├── schemas.py                 # Pydantic models (BuyerConfig, VendorOffer, etc.)
│   └── enums.py                   # NegotiationStatus, Strategy, CampaignStatus
├── services/
│   ├── negotiation.py             # Core FSM: process_vendor_input()
│   ├── llm.py                     # Anthropic API: generate_agent_response()
│   ├── rag.py                     # ChromaDB retrieval: vendor & competitor context
│   ├── research.py                # Pre-call research brief generation
│   ├── scoring.py                 # NumPy utility scoring: score_offer()
│   ├── guardrails.py              # Action validation & constraint checking
│   ├── voice.py                   # Twilio + Deepgram + TTS integration
│   ├── adversarial.py             # Simulation engine (buyer/seller steps)
│   ├── campaign.py                # Campaign orchestration & job queue
│   ├── memory.py                  # Memory pattern extraction & storage
│   └── events.py                  # Worker event pub/sub (async)
└── routers/
    ├── negotiations.py            # CRUD + WebSocket for negotiations
    ├── campaigns.py               # Campaign management
    ├── webhooks.py                # Inbound vendor messages, simulate
    └── voice.py                   # Twilio webhook + media stream

tests/
├── conftest.py                    # Pytest fixtures (tmp_db, cache reset)
├── test_database.py               # Schema & CRUD tests
└── test_cache.py                  # Cache layer tests

scripts/
└── demo_smoke_test.py             # Integration test against live API

data/
├── chroma/                        # ChromaDB persistent storage
└── negotiations.db                # SQLite database
```

## Database Schema (SQLite)

| Table | Purpose |
|-------|---------|
| `negotiations` | Negotiation state: vendor, config, status, round, utility_score, research_brief |
| `messages` | Message history: role, content, structured_data, utility_score, rag_context |
| `call_sessions` | Voice call tracking: twilio_call_sid, transcript, status |
| `campaigns` | Campaign state: name, max_workers, budget, job counts, status |
| `campaign_jobs` | Jobs within a campaign: status, priority_score, retry_count, deadline |
| `quote_events` | Historical quotes: vendor, unit_price, shipping, payment_terms, delivery, confidence |
| `latest_quotes` | Latest quote per (vendor, product_category) for fast lookups |
| `session_locks` | Redis-backed session locks for concurrent workers |
| `memory_candidates` | Long-term memory patterns: vendor pricing bands, escalation signals |
| `validated_features` | Confirmed vendor behaviors: discount tendency, payment flexibility |

**ChromaDB**: `vendor_history` collection — vector-searchable negotiation summaries.

## Negotiation State Machine

```
PENDING → ACTIVE → AWAITING_VENDOR → (loop) → ACCEPTED | REJECTED | ESCALATED
```

Statuses: PENDING, ACTIVE, AWAITING_VENDOR, AWAITING_APPROVAL, ACCEPTED, REJECTED, ESCALATED

## Core Flow (`process_vendor_input()`)

1. Load negotiation & config from SQLite
2. Extract offer from vendor message (LLM or regex fallback)
3. Score offer using NumPy utility matrix
4. Check max rounds → escalate if reached
5. Suggest pivots (which terms to push on)
6. Retrieve context: ChromaDB RAG + research brief + competing offers
7. Generate agent response (LLM) with guardrails (3 retries, deterministic fallback)
8. Persist turn to SQLite + broadcast via WebSocket
9. Archive to RAG if deal closed

## API Endpoints

**Negotiations** (`/negotiations`): POST `/`, GET `/`, GET `/{id}`, POST `/batch`, WS `/ws/{id}`

**Webhooks** (`/webhooks`): POST `/vendor`, POST `/simulate`

**Voice** (`/voice`): POST `/call/{id}`, POST `/twilio-stream/{id}`, WS `/media-stream/{id}`, GET `/calls/{id}`

**Campaigns** (`/campaigns`): POST `/`, GET `/`, GET `/{id}`, GET `/{id}/jobs`, POST `/{id}/start`, POST `/{id}/pause`, POST `/{id}/resume`, GET `/{id}/summary`, WS `/{id}/ws`

**Health**: GET `/`

## Scoring (services/scoring.py)

Weighted utility: unit_price (0.45), shipping_cost (0.15), payment_terms_days (0.20), delivery_days (0.20). Normalized [best,worst] → [1.0, 0.0]. Accept if utility >= config.min_acceptable_utility.

## LLM Integration (services/llm.py)

Returns `AgentAction`: counter_offer, message, reasoning, should_accept, should_escalate. Strategies: AGGRESSIVE, BALANCED, VOLUME, RELATIONSHIP. Guardrails enforce constraint checking with 3-attempt retry + deterministic fallback.

## Key Patterns

- **Dual Fallback**: LLM → deterministic fallback for all AI operations
- **JSON in SQLite**: BuyerConfig, VendorOffer stored as TEXT, parsed with Pydantic
- **Background Tasks**: Research briefs via `background_tasks.add_task()`
- **WebSocket Broadcasting**: Per-negotiation socket pools for real-time updates
- **Campaign Workers**: Priority-based scheduling, Redis locks, retry with backoff

## Redis Working Memory Contract

Redis is currently a real read-through cache in front of SQLite for vendor priors and negotiation context. SQLite remains the source of truth.

### Key Shapes

- `wm:vendor:{vendor_name}:{product_category}`
- `wm:negotiation:{negotiation_id}`
- `wm:category:{product_category}`
- `wm:campaign:{campaign_id}`

### Cache Helpers

Implemented in [app/core/cache.py](/Users/rishu/Github/CatapultBackend/app/core/cache.py):

- `vendor_working_memory_key(vendor_name, product_category)`
- `negotiation_working_memory_key(negotiation_id)`
- `category_working_memory_key(product_category)`
- `campaign_working_memory_key(campaign_id)`
- `get_working_memory(key)`
- `put_working_memory(key, value, ttl=None)`
- `delete_working_memory(key)`
- `get_vendor_working_memory(...)`
- `put_vendor_working_memory(...)`
- `get_negotiation_working_memory(...)`
- `put_negotiation_working_memory(...)`
- `get_category_working_memory(...)`
- `put_category_working_memory(...)`
- `get_campaign_working_memory(...)`
- `put_campaign_working_memory(...)`

### Memory-Service Integration Points

Implemented as placeholders in [app/services/memory.py](/Users/rishu/Github/CatapultBackend/app/services/memory.py):

- `load_vendor_priors_from_redis(vendor_name, product_category)`
- `store_vendor_priors_in_redis(vendor_name, product_category, vendor_priors, ttl=None)`
- `compile_vendor_working_memory_from_sqlite(vendor_name, product_category)`
- `refresh_vendor_working_memory(vendor_name, product_category, ttl=None)`
- `store_negotiation_working_memory_in_redis(negotiation_id, payload, ttl=None)`
- `store_category_working_memory_in_redis(product_category, payload, ttl=None)`

### Intended Read Path

1. Live worker checks Redis for vendor/category/negotiation working memory.
2. On cache miss, it falls back to SQLite source tables such as `validated_features`, `latest_quotes`, `messages`.
3. The fallback result is immediately written back to Redis with `working_memory_ttl`.
4. Quote writes and post-call updates refresh the affected Redis negotiation/category payloads.
5. Optional future compiler step can replace the SQLite-shaped payload with a high-reasoning distilled one.

### Current Live Read-Through Hooks

- `load_vendor_priors()` in [app/services/memory.py](/Users/rishu/Github/CatapultBackend/app/services/memory.py) now checks Redis first, then SQLite, then backfills Redis.
- `load_vendor_context()` in [app/services/voice.py](/Users/rishu/Github/CatapultBackend/app/services/voice.py) now checks Redis negotiation working memory first, then SQLite, then backfills Redis.
- `write_quote_update()`, `persist_extracted_facts()`, and `post_call_summary()` in [app/services/voice.py](/Users/rishu/Github/CatapultBackend/app/services/voice.py) refresh Redis after SQLite writes.

### Intended Payload Shape

Vendor working memory should stay JSON-serializable and compact. Suggested top-level fields:

- `vendor_name`
- `product_category`
- `vendor_priors`
- `recent_summary`
- `recommended_tactics`
- `compiled_at`
- `source`

## Quick Start

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill API keys
python -m app.seed    # seed ChromaDB
uvicorn app.main:app --reload
```

## Environment Variables

Required: `ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `DEEPGRAM_API_KEY`, `TTS_API_KEY`

Optional: `TTS_PROVIDER` (elevenlabs|cartesia), `TTS_VOICE_ID`, `DATABASE_URL`, `CHROMA_PERSIST_DIR`, `REDIS_URL`, `BASE_URL` (ngrok for Twilio callbacks)

## Testing

```bash
pytest tests/ -v                                          # unit tests
python scripts/demo_smoke_test.py --base-url http://127.0.0.1:8000  # integration
```

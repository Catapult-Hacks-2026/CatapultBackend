# DETAILED IMPLEMENTATION PLAN
## Mapping plan.pdf (Hotel Negotiation System) to Catapult Backend

---

## 1. Context

The PDF describes a production-grade hotel negotiation system with campaign orchestration, concurrent worker sessions, a shared backend, long-term memory, and a dual-model voice pipeline (GPT Realtime 1.5 for audio + GPT-4o for reasoning). This plan maps every component from the PDF onto the existing Catapult codebase, identifying what already exists, what needs modification, and what must be built from scratch.

---

## 2. Gap Analysis: What Exists vs What's Needed

| PDF Component | Current State | Work Needed |
|---|---|---|
| Main Orchestration Workflow | Partially exists (`/negotiations/batch`) | Build campaign system with job queue, prioritization, retry logic |
| Worker Workflow (live call loop) | Basic version in `voice.py` | Major rework: dual-model split, structured fact extraction, cross-session checks |
| Shared Backend (Postgres) | SQLite with basic tables | Migrate to Postgres, add 6 new tables |
| Fast Cache (Redis) | None | Add Redis for locks, quote cache, event pub/sub |
| Long-Term Memory Layer | ChromaDB (basic RAG) | Expand with behavioral features, confidence scoring, offline validation |
| Dual-Model Voice Pipeline | Single-model (Anthropic) + Twilio/Deepgram | Add GPT Realtime 1.5 for audio I/O, GPT-4o for reasoning |
| Campaign Management | None | New service + router |
| Session Locks | None | New Redis-based lock system |
| Cross-Session State | Basic `_load_competing_offers()` | Real-time cross-session quote sharing |
| Market Signals | None | New table + ingestion |
| Post-Call Analysis | None | New offline analysis pipeline |

---

## 3. Database Schema Changes

### 3A. Migrate SQLite to PostgreSQL

**File:** `app/core/database.py`

Update `init_db()` to use PostgreSQL via `asyncpg` or `psycopg2`. Keep ChromaDB for vector search.

**Config change** (`app/core/config.py`):
```python
database_url: str = "postgresql://localhost:5432/catapult"
redis_url: str = "redis://localhost:6379/0"
```

### 3B. New Tables

Add to `init_db()` in `app/core/database.py`:

#### `campaigns` (maps to PDF Section 3: Main State Variables)
```sql
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',       -- pending|running|paused|completed
    max_concurrent_workers INTEGER DEFAULT 5,
    max_budget REAL,
    total_target_jobs INTEGER DEFAULT 0,
    completed_jobs INTEGER DEFAULT 0,
    failed_jobs INTEGER DEFAULT 0,
    market_signals JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### `campaign_jobs` (maps to PDF: Target Jobs, Queued Jobs, Retry Jobs)
```sql
CREATE TABLE IF NOT EXISTS campaign_jobs (
    id TEXT PRIMARY KEY,
    campaign_id TEXT REFERENCES campaigns(id),
    negotiation_id TEXT REFERENCES negotiations(id),
    vendor_name TEXT NOT NULL,
    product_category TEXT DEFAULT 'general',
    priority_score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'queued',         -- queued|active|completed|failed|retry|deferred
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 2,
    deferred_until TIMESTAMP,
    failure_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### `quote_events` (maps to PDF Section 5: Quote Events)
```sql
CREATE TABLE IF NOT EXISTS quote_events (
    id TEXT PRIMARY KEY,
    negotiation_id TEXT REFERENCES negotiations(id),
    vendor_name TEXT NOT NULL,
    product_category TEXT,
    unit_price REAL NOT NULL,
    shipping_cost REAL,
    payment_terms_days INTEGER,
    delivery_days INTEGER,
    confidence_score REAL DEFAULT 1.0,
    restrictions JSON,
    source TEXT DEFAULT 'call',           -- call|email|webhook
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### `latest_quotes` (maps to PDF Section 5: Latest Quotes)
```sql
CREATE TABLE IF NOT EXISTS latest_quotes (
    vendor_name TEXT NOT NULL,
    product_category TEXT NOT NULL,
    unit_price REAL NOT NULL,
    shipping_cost REAL,
    payment_terms_days INTEGER,
    delivery_days INTEGER,
    confidence_score REAL DEFAULT 1.0,
    restrictions JSON,
    quote_timestamp TIMESTAMP,
    negotiation_id TEXT,
    PRIMARY KEY (vendor_name, product_category)
);
```

#### `session_locks` (maps to PDF Section 5: Session Locks)
```sql
CREATE TABLE IF NOT EXISTS session_locks (
    lock_key TEXT PRIMARY KEY,            -- vendor_name:product_category:date_range
    negotiation_id TEXT REFERENCES negotiations(id),
    acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    worker_id TEXT
);
```

#### `memory_candidates` (maps to PDF Section 5: Long-Term Memory Candidates)
```sql
CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT PRIMARY KEY,
    vendor_name TEXT NOT NULL,
    product_category TEXT,
    pattern_type TEXT,                    -- price_band|time_flexibility|fee_waiver|escalation_success
    pattern_description TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    evidence_count INTEGER DEFAULT 1,
    validated BOOLEAN DEFAULT FALSE,
    source_negotiation_ids JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### `validated_features` (maps to PDF Section 5: Validated Hotel Features)
```sql
CREATE TABLE IF NOT EXISTS validated_features (
    id TEXT PRIMARY KEY,
    vendor_name TEXT NOT NULL,
    product_category TEXT,
    feature_type TEXT NOT NULL,           -- acceptance_price_band|time_flexibility|fee_waiver_prob|escalation_success_rate
    feature_value JSON NOT NULL,
    confidence REAL DEFAULT 0.8,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(vendor_name, product_category, feature_type)
);
```

### 3C. Modify Existing Tables

#### `negotiations` - add columns:
```sql
ALTER TABLE negotiations ADD COLUMN campaign_id TEXT REFERENCES campaigns(id);
ALTER TABLE negotiations ADD COLUMN thread_id TEXT;              -- unique session attempt ID
ALTER TABLE negotiations ADD COLUMN worker_status TEXT DEFAULT 'idle';  -- idle|calling|active|post_call
ALTER TABLE negotiations ADD COLUMN manager_reached BOOLEAN DEFAULT FALSE;
ALTER TABLE negotiations ADD COLUMN callback_requested BOOLEAN DEFAULT FALSE;
ALTER TABLE negotiations ADD COLUMN final_outcome TEXT;
ALTER TABLE negotiations ADD COLUMN call_started_at TIMESTAMP;
ALTER TABLE negotiations ADD COLUMN call_ended_at TIMESTAMP;
```

#### `messages` - add column:
```sql
ALTER TABLE messages ADD COLUMN extracted_facts JSON;   -- structured facts from GPT-4o extraction
```

---

## 4. Redis Cache Layer

### 4A. New File: `app/core/cache.py`

```python
import redis.asyncio as redis
from app.core.config import get_settings

_pool = None

async def get_redis():
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = redis.from_url(settings.redis_url, decode_responses=True)
    return _pool
```

### 4B. Redis Key Patterns (maps to PDF Section 5: Fast Cache)

| Purpose | Key Pattern | TTL | Type |
|---|---|---|---|
| Session locks | `lock:{vendor}:{product}:{dates}` | 15 min | STRING with NX |
| Latest quote cache | `quote:{vendor}:{product}` | 5 min | HASH |
| Active worker count | `workers:active:{campaign_id}` | None | SET |
| Event pub/sub | `events:{campaign_id}` | None | PUBSUB channel |
| Worker heartbeat | `heartbeat:{negotiation_id}` | 30 sec | STRING |

---

## 5. Campaign Orchestration Service

### New File: `app/services/campaign.py`

Maps to **PDF Section 3: Main Orchestration Implementation Plan** (all 9 steps).

```python
# Key functions mapping to PDF workflow nodes:

async def ingest_targets(campaign_id, targets: list[dict]) -> list[dict]:
    """PDF Step 1: Load vendors, dates, product categories, priorities, constraints."""

async def deduplicate_targets(jobs: list[dict]) -> list[dict]:
    """PDF Step 2: Remove duplicate jobs, merge overlapping requests."""

async def load_market_state(jobs: list[dict]) -> list[dict]:
    """PDF Step 3: Enrich jobs with latest quotes, active locks, market signals."""

async def score_and_prioritize(jobs: list[dict]) -> list[dict]:
    """PDF Step 4: Rank by expected savings, quote staleness, urgency, success probability."""

async def check_launch_eligibility(campaign_id, jobs: list[dict]) -> tuple[list, list]:
    """PDF Step 5: Filter by concurrency limits, quote recency, budget status.
    Returns (launchable, deferred)."""

async def spawn_workers(campaign_id, jobs: list[dict]) -> list[str]:
    """PDF Step 6: Launch worker sessions, create thread IDs, acquire locks."""

async def monitor_worker_events(campaign_id) -> None:
    """PDF Step 7: Consume Redis pub/sub for worker status updates."""

async def handle_completion(campaign_id, job_id, outcome: dict) -> str:
    """PDF Step 8: Retry/requeue/close based on outcome."""

async def emit_campaign_summary(campaign_id) -> dict:
    """PDF Step 9: Best quotes, pending negotiations, retry schedules, estimated savings."""

async def run_campaign_loop(campaign_id) -> None:
    """PDF Section 6: Main orchestration loop.
    Continuously: ingest → dedupe → market state → score → eligibility → spawn → monitor → retry
    Until campaign complete."""
```

### Campaign State Variables (maps to PDF Section 3 table):

Stored in `campaigns` table + Redis:
- `campaign_id` → `campaigns.id`
- `target_jobs` → `campaign_jobs WHERE status='queued'`
- `active_workers` → Redis SET `workers:active:{campaign_id}`
- `completed_jobs` → `campaign_jobs WHERE status='completed'`
- `failed_jobs` → `campaign_jobs WHERE status='failed'`
- `retry_jobs` → `campaign_jobs WHERE status='retry'`
- `market_signals` → `campaigns.market_signals`
- `launch_budget` → `campaigns.max_budget`

### Priority Scoring Formula (PDF Step 4):

```python
def compute_priority(job, market_state) -> float:
    expected_savings = (market_state.avg_price - job.target_price) * job.quantity
    staleness = hours_since(market_state.last_quote_timestamp)
    urgency = 1.0 / max(days_until_deadline(job), 1)
    success_prob = get_vendor_success_rate(job.vendor_name)  # from validated_features
    importance = job.customer_priority  # 0-1 scale
    time_value = time_of_day_score()  # higher during business hours

    return (
        0.30 * normalize(expected_savings) +
        0.15 * normalize(staleness) +
        0.20 * normalize(urgency) +
        0.15 * normalize(success_prob) +
        0.10 * normalize(importance) +
        0.10 * normalize(time_value)
    )
```

---

## 6. Worker Workflow Rework (Dual-Model)

### Modify: `app/services/voice.py`

Maps to **PDF Section 4: Worker Implementation Plan** (all 15 steps).

### 6A. Worker State (maps to PDF Section 4 state table)

Add to negotiation context or a new in-memory dict per session:

```python
@dataclass
class WorkerSession:
    thread_id: str                    # PDF: Thread ID
    negotiation_id: str               # PDF: Hotel ID → negotiation_id
    vendor_name: str
    product_category: str
    session_status: str = "setup"     # setup|calling|active|post_call|completed
    transcript_window: list = field(default_factory=list)  # last N turns
    latest_quote: Optional[dict] = None
    fees_restrictions: Optional[dict] = None
    objections_used: list = field(default_factory=list)
    negotiation_strategy: Optional[dict] = None
    manager_reached: bool = False
    callback_requested: bool = False
    continue_call: bool = True
    next_action: Optional[str] = None
    final_outcome: Optional[str] = None
```

### 6B. Reworked `handle_media_stream()` (maps to PDF Steps 5-12 loop)

The current implementation handles the full loop in one function. Refactor into discrete steps:

```python
async def handle_media_stream(websocket, negotiation_id):
    session = await setup_worker_session(negotiation_id)  # PDF Steps 1-4

    while session.continue_call:
        # PDF Step 5: Listen for turn (GPT Realtime 1.5 or Deepgram STT)
        utterance = await listen_for_turn(websocket, session)
        if not utterance:
            continue

        # PDF Step 6: Extract structured facts (GPT-4o / Claude Sonnet)
        facts = await extract_structured_facts(utterance, session)

        # PDF Step 7: Write shared quote update
        await write_quote_update(session, facts)

        # PDF Step 8: Check cross-session state
        should_continue = await check_cross_session_state(session)
        if not should_continue:
            break

        # PDF Step 9: Decide negotiation move (GPT-4o / Claude Sonnet)
        action = await decide_negotiation_move(session, facts)

        # PDF Step 10: Generate response text (GPT-4o / Claude Sonnet)
        response_text = await generate_response_text(session, action)

        # PDF Step 11: Speak response (TTS)
        await speak_response(websocket, session, response_text)

        # PDF Step 12: Check continue or terminate
        session.continue_call = evaluate_termination(session)

    # PDF Steps 13-15: Post-call
    await post_call_summary(session)           # Step 13
    await emit_memory_candidates(session)       # Step 14
    await release_session_lock(session)         # Step 15
```

### 6C. New Worker Helper Functions

Each maps to a specific PDF worker step:

```python
# --- PDF Step 1: Load Hotel Context ---
async def load_vendor_context(negotiation_id: str) -> dict:
    """Load from negotiations table + messages + quote_events."""

# --- PDF Step 2: Load Long-Term Memory ---
async def load_behavioral_priors(vendor_name: str, product_category: str) -> dict:
    """Fetch from validated_features table.
    Returns: acceptance_price_bands, time_flexibility, fee_waiver_prob,
             escalation_success_rate, weekday_patterns."""

# --- PDF Step 3: Acquire Session Lock ---
async def acquire_session_lock(vendor_name, product_category, negotiation_id) -> bool:
    """Redis SETNX on lock:{vendor}:{product}.
    Returns False if another worker holds it → terminate early."""

# --- PDF Step 6: Extract Structured Facts ---
async def extract_structured_facts(utterance: str, session: WorkerSession) -> dict:
    """Call GPT-4o/Claude to extract:
    - quoted_rate, taxes, fees, room_type/product_details
    - cancellation_terms, discount_authority
    - refusals, negotiation_openness
    Store in messages.extracted_facts."""

# --- PDF Step 7: Write Shared Quote Update ---
async def write_quote_update(session: WorkerSession, facts: dict):
    """Insert into quote_events, upsert latest_quotes.
    Update Redis quote cache. Publish event on campaign channel."""

# --- PDF Step 8: Check Cross-Session State ---
async def check_cross_session_state(session: WorkerSession) -> bool:
    """Read latest_quotes for same product_category.
    Check if another worker found a better quote.
    Check if campaign budget was met.
    Returns True to continue, False to terminate."""

# --- PDF Step 9: Decide Negotiation Move ---
async def decide_negotiation_move(session: WorkerSession, facts: dict) -> str:
    """GPT-4o/Claude evaluates transcript + priors + shared state.
    Returns action: ask_lower_rate|waive_fee|escalate_manager|
                    mention_competitor|accept|close_politely"""

# --- PDF Step 13: Post-Call Summary ---
async def post_call_summary(session: WorkerSession) -> dict:
    """GPT-4o/Claude summarizes: final quote, negotiation path,
    refusal reasons, candidate learnings.
    Writes to call_outcomes / negotiations.final_outcome."""

# --- PDF Step 14: Emit Long-Term Memory Candidates ---
async def emit_memory_candidates(session: WorkerSession):
    """Extract behavioral patterns from the call.
    Write to memory_candidates table for offline validation.
    Examples: 'parking fee never waived', 'accepts 10% discount on Tuesdays'."""

# --- PDF Step 15: Release Session Lock ---
async def release_session_lock(session: WorkerSession):
    """Delete Redis lock key. Update session_locks table."""
```

---

## 7. Long-Term Memory Service

### New File: `app/services/memory.py`

Maps to **PDF Sections 2D and 5** (Long-Term Memory Candidates + Validated Features).

```python
async def extract_memory_candidates(negotiation_id: str, transcript: list, outcome: dict) -> list[dict]:
    """Post-call: LLM analyzes full transcript to identify behavioral patterns.
    Returns list of {vendor_name, pattern_type, pattern_description, confidence}."""

async def validate_memory_candidates():
    """Offline batch job: reviews unvalidated candidates.
    Promotes to validated_features if evidence_count >= 3 and confidence >= 0.7."""

async def load_vendor_priors(vendor_name: str, product_category: str) -> dict:
    """Retrieves validated behavioral features for a vendor.
    Returns: {
        acceptance_price_bands: [{min, max, confidence}],
        time_flexibility: {best_hours, best_days, confidence},
        fee_waiver_probability: float,
        escalation_success_rate: float,
        weekday_weekend_patterns: dict
    }"""

async def refresh_retrieval_summaries():
    """Periodic: regenerate ChromaDB summaries from latest validated features."""
```

---

## 8. Model Orchestration (Dual-Model)

### Modify: `app/services/llm.py`

Maps to **PDF Section 7: Model Orchestration Plan**.

### 8A. Add OpenAI client for GPT-4o reasoning (alongside existing Anthropic)

```python
# Add to config.py:
openai_api_key: str = ""
reasoning_model: str = "gpt-4o"           # PDF: GPT-4o for logic
realtime_model: str = "gpt-4o-realtime"    # PDF: GPT Realtime 1.5 for audio

# Or keep Claude for reasoning:
# reasoning_model stays as claude-sonnet-4-20250514
# The PDF's dual-model concept applies regardless of vendor
```

### 8B. Fast Loop Functions (used during live call)

```python
async def extract_facts_fast(transcript_window: list[str], context: dict) -> dict:
    """Fast structured extraction. ~500ms target latency.
    Uses GPT-4o or Claude Sonnet."""

async def decide_move_fast(session_state: dict, facts: dict, priors: dict) -> dict:
    """Fast action decision. ~800ms target latency.
    Returns {action, confidence, reasoning}."""

async def generate_reply_fast(action: str, session_state: dict) -> str:
    """Fast text generation. ~500ms target latency.
    Returns natural spoken text."""
```

### 8C. Slow Loop Functions (post-call, no latency constraint)

```python
async def summarize_call_deep(full_transcript: list, negotiation_context: dict) -> dict:
    """Deep analysis with larger model (Opus/GPT-4o).
    Returns comprehensive summary."""

async def extract_memory_patterns(full_transcript: list, outcome: dict) -> list[dict]:
    """Identify behavioral patterns for long-term memory."""

async def evaluate_strategy_effectiveness(negotiation_id: str) -> dict:
    """Analyze what worked/didn't for strategy refinement."""
```

---

## 9. Campaign Router

### New File: `app/routers/campaigns.py`

```python
# Endpoints:

POST   /campaigns/                    # Create campaign with target list
GET    /campaigns/                    # List all campaigns
GET    /campaigns/{id}                # Campaign detail + job status breakdown
POST   /campaigns/{id}/start          # Begin orchestration loop
POST   /campaigns/{id}/pause          # Pause launching new workers
POST   /campaigns/{id}/resume         # Resume campaign
GET    /campaigns/{id}/summary        # PDF Step 9: emit campaign summary
GET    /campaigns/{id}/jobs           # List all jobs with status
WebSocket /campaigns/{id}/ws          # Live campaign progress updates
```

### Register in `app/main.py`:
```python
from app.routers.campaigns import router as campaigns_router
app.include_router(campaigns_router, prefix="/campaigns", tags=["campaigns"])
```

---

## 10. Event Bus

### New File: `app/services/events.py`

Maps to **PDF Section 8: Deployment Plan, item 5**.

```python
# Worker → Orchestrator events (via Redis pub/sub):
EVENT_TYPES = [
    "call_started",
    "quote_received",
    "transferred_to_manager",
    "callback_requested",
    "deal_closed",
    "failed",
    "retry_recommended",
    "terminated",
]

async def publish_worker_event(campaign_id: str, event: dict):
    """Publish to Redis channel events:{campaign_id}."""

async def subscribe_worker_events(campaign_id: str):
    """Async generator yielding worker events.
    Used by campaign monitor loop and WebSocket broadcast."""
```

---

## 11. Updated Schemas

### Modify: `app/models/schemas.py`

Add new models:

```python
class CampaignTarget(BaseModel):
    vendor_name: str
    vendor_phone: Optional[str] = None
    product_category: str = "general"
    priority: float = 0.5              # 0-1 customer importance
    deadline: Optional[datetime] = None
    config: BuyerConfig

class CreateCampaignRequest(BaseModel):
    name: str
    targets: list[CampaignTarget]
    max_concurrent_workers: int = 5
    max_budget: Optional[float] = None
    auto_start: bool = False

class CampaignResponse(BaseModel):
    id: str
    name: str
    status: str
    total_jobs: int
    active_workers: int
    completed_jobs: int
    failed_jobs: int

class CampaignSummary(BaseModel):
    campaign_id: str
    best_quotes: list[dict]
    pending_negotiations: int
    retry_schedules: list[dict]
    unreached_vendors: list[str]
    estimated_savings: float

class WorkerEvent(BaseModel):
    event_type: str
    negotiation_id: str
    campaign_id: str
    data: dict
    timestamp: datetime

class ExtractedFacts(BaseModel):
    quoted_rate: Optional[float] = None
    taxes: Optional[float] = None
    fees: Optional[dict] = None
    product_details: Optional[str] = None
    cancellation_terms: Optional[str] = None
    discount_authority: Optional[str] = None  # "front desk" | "manager" | "none"
    refusals: list[str] = []
    negotiation_openness: float = 0.5  # 0=firm refusal, 1=very flexible

class MemoryCandidate(BaseModel):
    vendor_name: str
    product_category: str
    pattern_type: str
    pattern_description: str
    confidence: float
    source_negotiation_id: str
```

### Modify: `app/models/enums.py`

```python
class CampaignStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"

class WorkerEventType(str, Enum):
    CALL_STARTED = "call_started"
    QUOTE_RECEIVED = "quote_received"
    TRANSFERRED_TO_MANAGER = "transferred_to_manager"
    CALLBACK_REQUESTED = "callback_requested"
    DEAL_CLOSED = "deal_closed"
    FAILED = "failed"
    RETRY_RECOMMENDED = "retry_recommended"
    TERMINATED = "terminated"

class NegotiationAction(str, Enum):
    ASK_LOWER_RATE = "ask_lower_rate"
    WAIVE_FEE = "waive_fee"
    ESCALATE_MANAGER = "escalate_manager"
    MENTION_COMPETITOR = "mention_competitor"
    ACCEPT = "accept"
    CLOSE_POLITELY = "close_politely"
```

---

## 12. Updated Config

### Modify: `app/core/config.py`

```python
# Add:
redis_url: str = "redis://localhost:6379/0"
database_url: str = "postgresql://localhost:5432/catapult"
openai_api_key: str = ""
reasoning_model: str = "gpt-4o"             # or keep claude-sonnet
max_concurrent_campaigns: int = 3
worker_heartbeat_interval: int = 10          # seconds
session_lock_ttl: int = 900                  # 15 minutes
quote_cache_ttl: int = 300                   # 5 minutes
memory_validation_threshold: int = 3         # evidence count to promote
memory_confidence_threshold: float = 0.7
```

---

## 13. New Dependencies

### Modify: `requirements.txt`

```
# Add:
redis>=5.0.0
asyncpg>=0.29.0        # or psycopg2-binary for sync
openai>=1.0.0           # if using GPT-4o/Realtime
```

---

## 14. File Summary

| File | Action | Maps to PDF Section |
|---|---|---|
| `app/core/config.py` | Modify | Redis, Postgres, OpenAI config |
| `app/core/database.py` | Major modify | Section 5: all new tables |
| `app/core/cache.py` | **New** | Section 5: Fast Cache |
| `app/services/campaign.py` | **New** | Section 3: Main Orchestration (Steps 1-9) |
| `app/services/events.py` | **New** | Section 8: Event Bus |
| `app/services/memory.py` | **New** | Sections 2D, 5: Long-Term Memory |
| `app/services/voice.py` | Major rework | Section 4: Worker Workflow (Steps 1-15) |
| `app/services/llm.py` | Modify | Section 7: Dual-model fast/slow paths |
| `app/services/negotiation.py` | Modify | Wire campaign_id, thread_id, worker events |
| `app/services/rag.py` | Modify | Add behavioral feature retrieval |
| `app/services/scoring.py` | Modify | Add campaign job priority scoring |
| `app/routers/campaigns.py` | **New** | Section 3: Campaign API |
| `app/routers/voice.py` | Modify | Updated media stream handler |
| `app/models/schemas.py` | Modify | New models for campaigns, events, memory |
| `app/models/enums.py` | Modify | New enums for campaigns, actions, events |
| `app/main.py` | Modify | Register campaign router |
| `requirements.txt` | Modify | Redis, asyncpg, openai |

---

## 15. Engineering Phases (maps to PDF Section 9)

### Phase 1: Single Worker Proof of Concept
**Goal:** Prove the refactored voice loop works with discrete steps.
- Refactor `handle_media_stream()` into step functions (Section 6B above)
- Add `extract_structured_facts()` using existing Claude
- Add `write_quote_update()` to new `quote_events` table
- Test with single Twilio call via `/voice/call/{id}`
- **Verify:** Call completes, quote_events populated, facts extracted

### Phase 2: Shared Backend & Synchronization
**Goal:** Workers can read each other's updates.
- Add PostgreSQL tables (Section 3)
- Add Redis cache layer (Section 4)
- Implement session locks (`acquire_session_lock`, `release_session_lock`)
- Implement `latest_quotes` upsert + cache
- Implement `check_cross_session_state()`
- **Verify:** Run 2 concurrent negotiations for same product_category, confirm cross-session quote visibility

### Phase 3: Campaign Launcher
**Goal:** Automated campaign orchestration.
- Build `app/services/campaign.py` (Section 5)
- Build `app/routers/campaigns.py` (Section 9)
- Implement job ingestion, deduplication, priority scoring
- Implement `spawn_workers()` with concurrency limits
- **Verify:** POST campaign with 5 targets, confirm workers launch in priority order with concurrency limits

### Phase 4: Multi-Worker Concurrency
**Goal:** Run many parallel calls safely.
- Implement event bus (Section 10)
- Implement campaign monitor loop
- Implement retry/requeue logic
- Add worker heartbeats
- Add duplicate prevention (lock conflicts → defer)
- **Verify:** Campaign with 10 targets, max 3 concurrent, confirm no duplicate calls, proper retry on failure

### Phase 5: Long-Term Memory
**Goal:** Workers learn from past calls.
- Build `app/services/memory.py` (Section 7)
- Implement `emit_memory_candidates()` post-call
- Implement offline validation batch job
- Wire `load_vendor_priors()` into worker setup (Step 2)
- **Verify:** After 5+ calls to same vendor, validated_features populated, next call uses priors

### Phase 6: Optimization
**Goal:** Reduce latency and cost.
- Implement prompt caching for repeated vendor contexts
- Optimize fact extraction to <500ms
- Add streaming TTS (already partially implemented in `stream_tts_chunks`)
- Profile and reduce Redis round-trips
- **Verify:** End-to-end voice response latency <3s

---

## 16. Verification Checklist

- [ ] PostgreSQL migration: all tables created, data migrated from SQLite
- [ ] Redis: locks, quote cache, pub/sub all functional
- [ ] Single worker: refactored voice loop completes a call end-to-end
- [ ] Fact extraction: structured data parsed from vendor speech
- [ ] Quote sync: `latest_quotes` updated in real-time, visible cross-session
- [ ] Session locks: no duplicate workers on same vendor/product
- [ ] Campaign creation: targets ingested, deduplicated, prioritized
- [ ] Campaign execution: workers spawned respecting concurrency limits
- [ ] Event bus: worker events flow to campaign monitor
- [ ] Retry logic: failed calls requeued with backoff
- [ ] Campaign summary: accurate counts, best quotes, estimated savings
- [ ] Memory candidates: extracted from completed calls
- [ ] Memory validation: promoted after sufficient evidence
- [ ] Behavioral priors: loaded and used in negotiation strategy
- [ ] Adversarial mode: still works with new architecture
- [ ] WebSocket dashboard: campaign + negotiation updates broadcast
- [ ] Voice latency: <3s end-to-end response time

# Galileo Dashboard API — Implementation Plan

> Parallelizable work breakdown for Codex agents. Each agent owns one section and can work independently.

## Architecture Decisions

- **Framework**: FastAPI (existing stack)
- **Database**: SQLite via aiosqlite (existing pattern in `app/core/database.py`)
- **Validation**: Pydantic v2 models (existing pattern in `app/models/schemas.py`, `app/hotel/schemas.py`)
- **Real-time**: SSE via `sse-starlette` (new dependency — replaces WebSocket for dashboard streams)
- **Router prefix**: `/api/galileo` to namespace all new endpoints away from existing `/negotiations`, `/voice`, etc.
- **No auth**: Enterprise ID passed explicitly in URL params (matches existing no-auth pattern)

---

## Agent 1 — Enums, Types & Pydantic Models

**Goal**: Create all 23 types from SPECS.md as Pydantic models + enums.

**Files to create**:
- `app/galileo/__init__.py`
- `app/galileo/enums.py`
- `app/galileo/schemas.py`

**Enums** (`enums.py`):
```python
# Agent Lifecycle (v5 status model)
class AgentLifecycleStatus(str, Enum):
    INITIALIZING = "INITIALIZING"   # UI: "Queued"
    RINGING = "RINGING"             # UI: "Ringing"
    ACTIVE = "ACTIVE"               # UI: "Negotiating"
    WRAPPING_UP = "WRAPPING_UP"     # UI: "Finalizing"
    COMPLETED = "COMPLETED"         # UI: "Completed"
    FAILED = "FAILED"               # UI: "Failed"

# Negotiation Outcome (v5 — separate from lifecycle)
class NegotiationOutcome(str, Enum):
    RATE_CONFIRMED = "RATE_CONFIRMED"           # UI: "Deal Closed"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"   # UI: "Callback requested"
    NO_AVAILABILITY = "NO_AVAILABILITY"         # UI: "No Availability"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"   # UI: "Moved to higher up"
    FAILED = "FAILED"                           # UI: "Failure"
    TIMED_OUT = "TIMED_OUT"                     # UI: "Timed Out"

# Legacy status enum (for backward compat with v3 spec UI labels)
class AgentStatus(str, Enum):
    NEGOTIATING = "Negotiating"
    REVIEWING = "Reviewing"
    OPTIMIZED = "Optimized"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    QUEUED = "Queued"

class ServiceType(str, Enum):
    HOTEL = "Hotel"
    AIRLINE = "Airline"
    BOTH = "Both"

class EventStatus(str, Enum):
    ACTIVE = "Active"
    COMPLETED = "Completed"

class PricePointType(str, Enum):
    OFFER = "offer"
    NEGOTIATED = "negotiated"
    CURRENT = "current"

class BookingWindowStatus(str, Enum):
    BEST_DEAL = "Best Deal"
    GOOD = "Good"
    PEAK = "Peak"
```

**Pydantic Models** (`schemas.py`) — implement all of these exactly matching the SPECS.md field tables:

| Model | Category | Key Fields |
|-------|----------|------------|
| `Enterprise` | Core | id, name, description, totalSavedHotels, totalSavedAirlines, totalSaved, yoyChange, hotelContractCount, airlineContractCount |
| `Company` | Core | id, name, initials, description, phone, website, industry, badge, locations |
| `Location` | Core | id, companyId, name, address, phone |
| `GalileoEvent` | Core | id, enterpriseId, name, location, startDate, endDate, attendees, service, status, agents, requirements?, budgetPerPerson? |
| `Agent` | Core | id, enterpriseId, eventId, companyId, companyName, segment, type, status, lifecycleStatus, outcome, idealPrice, ceilingPrice, originalPrice, currentPrice, delta, potentialSavings, savingsToDate, distanceToGoal, isAccepted, pricePath, activityStream, transcript, previousNegotiations |
| `EnterpriseCompanyView` | Derived | companyId, enterpriseId, locationId?, lifetimeSavings, savingsDelta, agreementsCount, agreementsSummary, avgDelta, totalBookings, totalSavings, yoyChange, pricingTrends, bookingWindow, linkedEvents |
| `PricingTrend` | Derived | month, year, range, negotiatedPrice, marketPrice |
| `BookingWindowEntry` | Derived | month, score, status |
| `PricePoint` | Supporting | label, price, type, round |
| `ActivityStreamItem` | Stream | id, agentId, price, badge?, badgeType, detail, detailType?, timestamp, active |
| `Message` | Stream | id, agentId, message, sender, timestamp |
| `PreviousNegotiation` | Supporting | id, contractId, region, duration, finalRate, totalSavings, status, startDate, endDate |
| `MarketPricingResult` | Response | service, hotel (marketPrice, predictedWinPrice, unit), airline (marketPrice, predictedWinPrice, unit) |
| `EventWindowResult` | Response | label, startDate, endDate, explanation, hotel?, airline?, negotiationConfidence |
| `AcceptOfferRequest` | Request | enterpriseId |
| `LaunchNegotiationRequest` | Request | enterpriseId, eventName, service, startDate, endDate, location, attendees, budgetPerPerson?, requirements?, guardrails? |
| `InterventionResult` | Response | agentId, status, callRoutingInfo?, transferredAt |

**Acceptance criteria**:
- All models have proper `model_config` with `from_attributes = True`
- Optional fields use `X | None = None`
- Agent model includes BOTH legacy `status: AgentStatus` AND new v5 `lifecycleStatus: AgentLifecycleStatus` + `outcome: NegotiationOutcome | None`
- Nested models (e.g. `MarketPricingResult.hotel`) use inner Pydantic models, not dicts
- All ISO 8601 date fields typed as `str` (not datetime) for JSON passthrough

---

## Agent 2 — Database Schema & CRUD Layer

**Goal**: Create SQLite tables for all Galileo entities and a data access layer.

**Files to create**:
- `app/galileo/database.py` — table creation + CRUD functions
- `app/galileo/seed.py` — seed script for demo data

**Tables to create** (prefix all with `galileo_`):

```sql
galileo_enterprises (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    total_saved_hotels REAL DEFAULT 0,
    total_saved_airlines REAL DEFAULT 0,
    total_saved REAL DEFAULT 0,
    yoy_change REAL DEFAULT 0,
    hotel_contract_count INTEGER DEFAULT 0,
    airline_contract_count INTEGER DEFAULT 0
)

galileo_companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    initials TEXT,
    description TEXT,
    phone TEXT,
    website TEXT,
    industry TEXT,
    badge TEXT
)

galileo_locations (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES galileo_companies(id),
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT
)

galileo_events (
    id TEXT PRIMARY KEY,
    enterprise_id TEXT NOT NULL REFERENCES galileo_enterprises(id),
    name TEXT NOT NULL,
    location TEXT,
    start_date TEXT,
    end_date TEXT,
    attendees INTEGER,
    service TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active',
    requirements TEXT,
    budget_per_person REAL
)

galileo_agents (
    id TEXT PRIMARY KEY,
    enterprise_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES galileo_events(id),
    company_id TEXT NOT NULL REFERENCES galileo_companies(id),
    company_name TEXT,
    segment TEXT,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Queued',
    lifecycle_status TEXT NOT NULL DEFAULT 'INITIALIZING',
    outcome TEXT,
    ideal_price REAL,
    ceiling_price REAL,
    original_price REAL,
    current_price REAL,
    delta REAL,
    potential_savings REAL,
    savings_to_date REAL,
    distance_to_goal REAL,
    is_accepted INTEGER DEFAULT 0
)

galileo_price_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
    label TEXT,
    price REAL,
    type TEXT,
    round INTEGER
)

galileo_activity_stream (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
    price REAL,
    badge TEXT,
    badge_type TEXT,
    detail TEXT,
    detail_type TEXT,
    timestamp TEXT,
    active INTEGER DEFAULT 0
)

galileo_messages (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
    message TEXT,
    sender TEXT,
    timestamp TEXT
)

galileo_previous_negotiations (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
    contract_id TEXT,
    region TEXT,
    duration TEXT,
    final_rate REAL,
    total_savings REAL,
    status TEXT,
    start_date TEXT,
    end_date TEXT
)
```

**CRUD functions** (all async, using `aiosqlite`):
- `init_galileo_db()` — CREATE TABLE IF NOT EXISTS for all tables above. Call from app lifespan.
- `get_enterprise(enterprise_id) -> dict`
- `get_agents(enterprise_id, status?, event_id?, limit?) -> list[dict]`
- `get_agent(agent_id) -> dict` (with price_path, activity_stream, transcript, previous_negotiations joined)
- `get_events(enterprise_id, status?) -> list[dict]` (with nested agents)
- `get_event(event_id) -> dict` (with nested agents, transcript for completed)
- `get_companies(q?, limit?) -> list[dict]`
- `get_company(company_id) -> dict` (with locations)
- `get_enterprise_company_view(enterprise_id, company_id, location_id?) -> dict` — compute analytics from linked events
- `accept_offer(event_id, agent_id, enterprise_id)` — atomically: set isAccepted, cancel competing agents, update enterprise totals, maybe flip event to Completed
- `create_event_with_agents(launch_request) -> dict` — insert event + agents
- `intervene_agent(agent_id) -> dict`

**Seed data** (`seed.py`):
- 1 enterprise
- 6-8 companies (mix of hotels and airlines) with 2-3 locations each
- 4-5 events (mix of Active/Completed) with 2-4 agents each
- Realistic price paths, activity streams, transcripts, and previous negotiations
- Should be idempotent (check before insert)

**Acceptance criteria**:
- Follow the existing pattern in `app/core/database.py` (use `get_db()` to get connection)
- `get_enterprise_company_view` must dynamically compute all analytics from linked events (agents where `is_accepted=1` OR `status='Negotiating'` for that company)
- `accept_offer` must be atomic (use a transaction)
- All functions return dicts that can be passed directly to Pydantic model constructors

---

## Agent 3 — REST API Endpoints

**Goal**: Implement all 14 Galileo endpoints as a FastAPI router.

**Files to create**:
- `app/galileo/router.py`

**File to modify**:
- `app/main.py` — register the new router with prefix `/api/galileo`

**Endpoints to implement**:

| # | Method | Path | Handler | Notes |
|---|--------|------|---------|-------|
| 1 | GET | `/enterprises/{enterpriseId}` | `get_enterprise` | Simple DB fetch → Enterprise model |
| 2 | GET | `/enterprises/{enterpriseId}/agents` | `get_agents` | Query params: status, eventId, limit (default 50) |
| 3 | GET | `/agents/{agentId}` | `get_agent_detail` | Full agent with pricePath, previousNegotiations, activityStream, transcript |
| 4 | GET | `/enterprises/{enterpriseId}/events` | `get_events` | Query param: status (Active\|Completed) |
| 5 | GET | `/events/{eventId}` | `get_event_detail` | Full event with agents; completed events include winner transcript + price path |
| 6 | GET | `/companies` | `get_companies` | Query params: q, limit (default 100). No enterprise scope. |
| 7 | GET | `/companies/{companyId}` | `get_company_general` | General profile + locations. No enterprise analytics. |
| 8 | GET | `/enterprises/{enterpriseId}/companies/{companyId}` | `get_enterprise_company_view` | Query param: locationId. Derived analytics from linked events. |
| 9 | POST | `/events/{eventId}/agents/{agentId}/accept` | `accept_offer` | Body: AcceptOfferRequest. Side effects: cancel competing, update totals, maybe flip event. |
| 10 | POST | `/agents/{agentId}/intervene` | `intervene` | Only when status=Negotiating. Returns InterventionResult. |
| 11 | SSE | `/agents/{agentId}/activity-stream` | `activity_stream` | SSE endpoint streaming ActivityStreamItem |
| 12 | SSE | `/agents/{agentId}/transcript` | `transcript_stream` | SSE endpoint streaming Message |
| 13 | POST | `/market/pricing` | `get_market_pricing` | Body: service, location, dates, attendees → MarketPricingResult |
| 14 | POST | `/negotiations/launch` | `launch_negotiations` | Body: LaunchNegotiationRequest → creates event + agents |
| 15 | POST | `/market/event-window` | `find_event_window` | Body: location, eventType, timing, attendees, nights → EventWindowResult[3] |

**SSE implementation pattern**:
```python
from sse_starlette.sse import EventSourceResponse

@router.get("/agents/{agent_id}/activity-stream")
async def activity_stream(agent_id: str):
    async def event_generator():
        # Poll DB or subscribe to event bus for new ActivityStreamItems
        # yield dict(data=json.dumps(item), event="activity")
        ...
    return EventSourceResponse(event_generator())
```

**Market endpoints** (13, 15): These can return mock/computed data for now since there's no real market data source. Generate realistic-looking results based on the input params.

**Acceptance criteria**:
- All endpoints return proper Pydantic models (automatic JSON serialization)
- HTTP 404 for missing resources
- SSE endpoints use `sse-starlette` EventSourceResponse
- POST `/accept` is idempotent (re-accepting same agent is a no-op)
- Router registered in `app/main.py` under prefix `/api/galileo`
- Import and use CRUD functions from `app/galileo/database.py`
- Import models from `app/galileo/schemas.py`

---

## Agent 4 — Seed Data & Integration Wiring

**Goal**: Create rich seed data, wire everything together, and verify the full flow works.

**Files to create**:
- `app/galileo/seed.py` — comprehensive seed data

**Files to modify**:
- `app/main.py` — add `init_galileo_db()` to lifespan startup, register router, optionally run seed
- `requirements.txt` — add `sse-starlette>=1.6.0`

**Seed data requirements**:

Create a `seed_galileo_data()` async function that populates:

1. **Enterprise**: "Meridian Technologies" — a Fortune 500 tech company
   - totalSaved: ~$2.4M, hotels: ~$1.6M, airlines: ~$800K
   - 12 hotel contracts, 6 airline contracts, yoyChange: 18.5%

2. **Companies** (6-8):
   - Hotels: "Lumina Hospitality Group", "Apex Hotels & Resorts", "Vanguard Suites", "Coastal Inn Collection"
   - Airlines: "Atlas Airways", "Pinnacle Airlines"
   - Each with badge (Strategic Partner / Preferred Supplier / Standard)
   - Each with 2-4 locations with realistic addresses

3. **Events** (5):
   - 2 Active: "Q4 Sales Kickoff — Chicago" (Hotel+Airline), "EMEA Leadership Summit — London" (Hotel)
   - 3 Completed: "Annual Company Retreat — Austin", "Engineering Offsite — Denver", "Customer Conference — San Francisco"
   - Each with 2-4 agents targeting different companies

4. **Agents** (~15):
   - Mix of all statuses: Negotiating, Reviewing, Optimized, Completed, Cancelled, Queued
   - Mix of lifecycle statuses and outcomes (v5 model)
   - Realistic price paths (4-6 rounds each, showing negotiation progression)
   - Activity stream items (3-8 per agent)
   - Transcript messages (4-10 per agent)
   - Previous negotiations (1-3 per agent for completed ones)
   - Realistic pricing: hotels $150-350/night, airlines $200-800/seat
   - Deltas between -2% and -15%

5. **Acceptance rule**: At least 2 agents should have `is_accepted=True` with `outcome=RATE_CONFIRMED`, and their competing agents should be `Cancelled`

**Wiring in `app/main.py`**:
```python
# In lifespan startup, after existing init_database():
from app.galileo.database import init_galileo_db
from app.galileo.seed import seed_galileo_data
await init_galileo_db()
await seed_galileo_data()

# Register router:
from app.galileo.router import router as galileo_router
app.include_router(galileo_router, prefix="/api/galileo", tags=["galileo"])
```

**Acceptance criteria**:
- Seed is idempotent (safe to run on every startup — check if data exists first)
- All seed data is internally consistent (foreign keys valid, linked event logic correct)
- After seeding, every endpoint returns valid data
- `sse-starlette` added to requirements.txt
- App starts cleanly with `uvicorn app.main:app`

---

## Dependency Graph

```
Agent 1 (Types/Enums)  ─── no deps, start immediately
         │
         ▼
Agent 2 (Database)     ─── depends on Agent 1 (imports models)
         │
         ▼
Agent 3 (Endpoints)    ─── depends on Agent 1 + Agent 2
         │
         ▼
Agent 4 (Seed + Wire)  ─── depends on all above
```

**Parallel execution**: Agents 1 and 2 can start simultaneously (Agent 2 can define tables without models; CRUD functions need models). Agent 3 needs types from Agent 1 and CRUD from Agent 2. Agent 4 is final integration.

In practice, **Agents 1 and 2 run in parallel**, then **Agent 3**, then **Agent 4**.

---

## File Map Summary

| File | Owner | Action |
|------|-------|--------|
| `app/galileo/__init__.py` | Agent 1 | Create (empty) |
| `app/galileo/enums.py` | Agent 1 | Create |
| `app/galileo/schemas.py` | Agent 1 | Create |
| `app/galileo/database.py` | Agent 2 | Create |
| `app/galileo/router.py` | Agent 3 | Create |
| `app/galileo/seed.py` | Agent 4 | Create |
| `app/main.py` | Agent 4 | Modify (add imports, lifespan init, router registration) |
| `requirements.txt` | Agent 4 | Modify (add sse-starlette) |

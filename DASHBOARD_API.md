# Dashboard API

This document covers the SQL-backed API surface the dashboard can use today.

Base URL:

```text
http://localhost:8000
```

Auth:

```text
No auth is currently enforced on these routes.
```

## Scope

Included here:

- endpoints backed by `data/negotiations.db`
- endpoints that return stored SQLite data or SQL-derived aggregates
- dashboard mutation endpoints that update SQLite state

Explicitly out of scope:

- `/api/campaigns/*` because campaign targets, summaries, and runtime state are stored in process memory, not SQL
- derived calculators that do not read SQL data, including:
  - `POST /api/galileo/market/pricing`
  - `POST /api/galileo/market/event-window`

## Database Map

The app uses a single SQLite file:

```text
data/negotiations.db
```

Main dashboard tables:

| Table | Used By | Notes |
|---|---|---|
| `negotiations` | `/negotiations` | root record for each negotiation |
| `messages` | `/negotiations/{id}`, `/negotiations/{id}/messages` | timeline for each negotiation |
| `historic_pricing` | `/api/hotel-data/historic-pricing` | hotel market history |
| `past_negotiations` | `/api/hotel-data/past-negotiations` | prior negotiated outcomes |
| `galileo_enterprises` | `/api/galileo/enterprises/{id}` | enterprise summary cards |
| `galileo_events` | `/api/galileo/enterprises/{id}/events`, `/api/galileo/events/{id}` | event list/detail |
| `galileo_agents` | `/api/galileo/enterprises/{id}/agents`, `/api/galileo/agents/{id}` | negotiation agents |
| `galileo_companies` | `/api/galileo/companies`, `/api/galileo/companies/{id}` | supplier list/detail |
| `galileo_locations` | `/api/galileo/companies/{id}`, `/api/galileo/enterprises/{enterprise_id}/companies/{company_id}` | supplier locations |
| `galileo_price_points` | embedded in `/api/galileo/agents/{id}` and `/api/galileo/events/{id}` | agent price chart |
| `galileo_activity_stream` | embedded in agent detail and streamed from `/api/galileo/agents/{id}/activity-stream` | SQL-backed SSE feed |
| `galileo_messages` | embedded in agent detail and streamed from `/api/galileo/agents/{id}/transcript` | SQL-backed transcript feed |
| `galileo_previous_negotiations` | embedded in `/api/galileo/agents/{id}` | supplier history |

Not exposed as stable dashboard read APIs yet:

- `call_sessions`
- `quote_events`
- `latest_quotes`
- `session_locks`

## Recommended Pull Map

If the dashboard needs to pull all relevant stored data, use this route map.

### Negotiations dashboard

- list page: `GET /negotiations`
- negotiation detail page: `GET /negotiations/{negotiation_id}`
- message-only polling: `GET /negotiations/{negotiation_id}/messages`
- scoring panel: `GET /negotiations/{negotiation_id}/scoring`
- market comps:
  - `GET /api/hotel-data/historic-pricing`
  - `GET /api/hotel-data/past-negotiations`

### Galileo dashboard

- enterprise summary header: `GET /api/galileo/enterprises/{enterprise_id}`
- enterprise event table: `GET /api/galileo/enterprises/{enterprise_id}/events`
- enterprise agent table: `GET /api/galileo/enterprises/{enterprise_id}/agents`
- event detail page: `GET /api/galileo/events/{event_id}`
- agent detail drawer/page: `GET /api/galileo/agents/{agent_id}`
- supplier search: `GET /api/galileo/companies?q=...`
- supplier detail: `GET /api/galileo/companies/{company_id}`
- enterprise-specific supplier view: `GET /api/galileo/enterprises/{enterprise_id}/companies/{company_id}`
- live activity stream: `GET /api/galileo/agents/{agent_id}/activity-stream`
- live transcript stream: `GET /api/galileo/agents/{agent_id}/transcript`

## Negotiations API

Mounted at `/negotiations`.

### `GET /negotiations`

Returns all rows from `negotiations`, ordered by `updated_at DESC, created_at DESC`.

Response shape:

```json
[
  {
    "id": "9f7c3b5b-5d30-4e2b-8fbc-3180cb098f4e",
    "vendor_name": "Hilton Hotels",
    "product_category": "hotel",
    "status": "pending",
    "strategy": "balanced",
    "round_number": 0,
    "utility_score": null,
    "current_offer": null
  }
]
```

### `GET /negotiations/{negotiation_id}`

Returns one `negotiations` row plus all related `messages`.

Response shape:

```json
{
  "negotiation": {
    "id": "9f7c3b5b-5d30-4e2b-8fbc-3180cb098f4e",
    "vendor_name": "Hilton Hotels",
    "product_category": "hotel",
    "status": "pending",
    "strategy": "balanced",
    "round_number": 0,
    "utility_score": 0.71,
    "current_offer": {
      "unit_price": 189,
      "shipping_cost": 0,
      "payment_terms_days": 30,
      "delivery_days": 14,
      "notes": "Rate includes breakfast"
    }
  },
  "research_brief": {},
  "config": {
    "target_unit_price": 150,
    "max_unit_price": 200,
    "target_shipping_cost": 0,
    "max_shipping_cost": 0,
    "preferred_payment_terms": 60,
    "min_payment_terms": 30,
    "preferred_delivery_days": 14,
    "max_delivery_days": 30,
    "quantity": 2,
    "weight_price": 0.45,
    "weight_shipping": 0.15,
    "weight_payment_terms": 0.2,
    "weight_delivery": 0.2,
    "min_acceptable_utility": 0.6
  },
  "messages": [
    {
      "id": 1,
      "role": "system",
      "content": "Negotiation initialized.",
      "structured_data": null,
      "utility_score": null,
      "rag_context": null,
      "guardrail_log": null,
      "created_at": "2026-04-04 14:22:31"
    }
  ]
}
```

### `GET /negotiations/{negotiation_id}/messages`

Returns only the `messages` rows for one negotiation, ordered by `created_at, id`.

### `GET /negotiations/{negotiation_id}/scoring`

Returns a computed scoring object from the stored `negotiations.config` and `negotiations.current_offer`.

Important:

- this route is SQL-backed because it reads stored negotiation data
- `scoring_breakdown` is computed at request time, not stored as a table row

Response shape when there is no current offer:

```json
{
  "scoring_breakdown": null,
  "pivot_suggestions": {},
  "status": "pending"
}
```

### Write endpoints that update SQL

- `POST /negotiations`
  - inserts one row into `negotiations`
- `PATCH /negotiations/{negotiation_id}`
  - updates `strategy`, `config`, and `updated_at` on `negotiations`
- `POST /negotiations/{negotiation_id}/approve`
  - updates `negotiations.status` to `accepted`
  - inserts a system row into `messages`
- `POST /negotiations/{negotiation_id}/escalate`
  - updates `negotiations.status` to `escalated`
  - inserts a system row into `messages`
- `POST /negotiations/batch`
  - inserts multiple `negotiations` rows
- `POST /negotiations/import-market-data`
  - imports CSV rows into `historic_pricing` and `past_negotiations`

## Hotel Market Data API

Mounted at `/api/hotel-data`.

### `GET /api/hotel-data/historic-pricing`

Reads from `historic_pricing`.

Query params:

- `hotel`
- `location`

Declared response model:

```json
[
  {
    "id": 1,
    "hotel": "Peninsula Hotels",
    "location": "Magnificent Mile",
    "month": 7,
    "year": 2025,
    "price_per_night": 825,
    "created_at": "2026-04-04 14:22:31"
  }
]
```

### `GET /api/hotel-data/historic-pricing/{id}`

Reads one row from `historic_pricing`.

### `GET /api/hotel-data/past-negotiations`

Reads from `past_negotiations`.

Query params:

- `hotel`
- `location`

Declared response model:

```json
[
  {
    "id": 1,
    "hotel": "Hilton Hotels",
    "location": "The Loop",
    "month": 10,
    "year": 2025,
    "starting_price": 380,
    "negotiation_price": 310,
    "proposed_price": 285,
    "created_at": "2026-04-04 14:22:31"
  }
]
```

### `GET /api/hotel-data/past-negotiations/{id}`

Reads one row from `past_negotiations`.

### Write endpoints that update SQL

- `POST /api/hotel-data/historic-pricing`
- `POST /api/hotel-data/past-negotiations`
- `POST /negotiations/import-market-data`

## Galileo API

Mounted at `/api/galileo`.

### `GET /api/galileo/enterprises/{enterprise_id}`

Reads one row from `galileo_enterprises`.

Response shape:

```json
{
  "id": "ent_demo",
  "name": "Acme Travel",
  "description": "Enterprise account",
  "totalSavedHotels": 240000,
  "totalSavedAirlines": 180000,
  "totalSaved": 420000,
  "yoyChange": 14.5,
  "hotelContractCount": 8,
  "airlineContractCount": 4
}
```

### `GET /api/galileo/enterprises/{enterprise_id}/events`

Reads `galileo_events` and embeds matching `galileo_agents`.

Query params:

- `status`

### `GET /api/galileo/events/{event_id}`

Reads one event plus all matching agents, price points, activity items, transcript messages, and previous negotiations.

If the event is completed, the response may also include:

- `winnerAgentId`
- `winnerTranscript`
- `winnerPricePath`

### `GET /api/galileo/enterprises/{enterprise_id}/agents`

Reads `galileo_agents`.

Query params:

- `status`
- `eventId`
- `limit` default `50`, max `500`

This is the best table view endpoint for an enterprise-level agent grid.

### `GET /api/galileo/agents/{agent_id}`

Reads one `galileo_agents` row and embeds:

- `pricePath` from `galileo_price_points`
- `activityStream` from `galileo_activity_stream`
- `transcript` from `galileo_messages`
- `previousNegotiations` from `galileo_previous_negotiations`

### `GET /api/galileo/companies`

Reads supplier rows from `galileo_companies`.

Query params:

- `q`
- `limit` default `100`, max `500`

### `GET /api/galileo/companies/{company_id}`

Reads one supplier from `galileo_companies` and embeds `locations` from `galileo_locations`.

### `GET /api/galileo/enterprises/{enterprise_id}/companies/{company_id}`

Returns an enterprise-specific supplier summary assembled from SQL joins across:

- `galileo_agents`
- `galileo_events`
- `galileo_companies`
- `galileo_locations`

This endpoint is the right source for:

- linked event history
- enterprise savings totals
- accepted agreement counts
- booking-window data
- pricing trend charts

Important:

- these metrics are computed from stored rows at request time
- they are not persisted as a dedicated materialized table

### SQL-backed stream endpoints

- `GET /api/galileo/agents/{agent_id}/activity-stream`
  - Server-Sent Events from rows in `galileo_activity_stream`
- `GET /api/galileo/agents/{agent_id}/transcript`
  - Server-Sent Events from rows in `galileo_messages`

### Write endpoints that update SQL

- `POST /api/galileo/negotiations/launch`
  - inserts one `galileo_events` row
  - inserts related rows into `galileo_agents`, `galileo_price_points`, `galileo_activity_stream`, and `galileo_messages`
- `POST /api/galileo/events/{event_id}/agents/{agent_id}/accept`
  - updates `galileo_agents`
  - may update `galileo_enterprises`
  - may update `galileo_events`
- `POST /api/galileo/agents/{agent_id}/intervene`
  - updates `galileo_agents`
  - inserts one row into `galileo_activity_stream`

## Known Schema Caveat

The live SQLite file currently contains legacy schemas for the hotel market tables:

```text
historic_pricing(id, hotel, location, date, price_per_night, created_at)
past_negotiations(id, hotel, location, date, starting_price, negotiation_price, proposed_price, created_at)
```

But the current FastAPI response models for those routes expect:

```text
month, year
```

Implication for the dashboard:

- the intended API contract is `month` and `year`
- the CSV importer already supports both schema variants
- if the local DB has not been migrated yet, those hotel-data endpoints may not serialize cleanly until the schema is aligned

## Gaps

There is still no stable dashboard read API for:

- `quote_events`
- `latest_quotes`
- `call_sessions`
- `session_locks`

If the dashboard needs those datasets, the clean next additions would be:

1. `GET /api/quotes`
2. `GET /api/quotes/latest`
3. `GET /api/call-sessions`
4. `GET /api/call-sessions/{id}`
5. `GET /api/session-locks`

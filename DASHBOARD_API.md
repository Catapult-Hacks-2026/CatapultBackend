# Dashboard API

This document maps the current API surface to the main SQLite-backed tables and dashboard data flows.

Base URL:

```text
http://localhost:8000
```

There is currently no auth layer on these routes.

## Table Map

| Table / Data Set | Pull | Push |
|---|---|---|
| `negotiations` | `GET /negotiations`, `GET /negotiations/{id}` | `POST /negotiations`, `PATCH /negotiations/{id}`, `POST /negotiations/{id}/approve`, `POST /negotiations/{id}/escalate`, `POST /negotiations/batch` |
| `messages` | `GET /negotiations/{id}`, `GET /negotiations/{id}/messages` | written indirectly by negotiation actions and escalation/approval flows |
| `historic_pricing` | `GET /api/hotel-data/historic-pricing`, `GET /api/hotel-data/historic-pricing/{id}` | `POST /api/hotel-data/historic-pricing`, `POST /negotiations/import-market-data` |
| `past_negotiations` | `GET /api/hotel-data/past-negotiations`, `GET /api/hotel-data/past-negotiations/{id}` | `POST /api/hotel-data/past-negotiations`, `POST /negotiations/import-market-data` |
| campaign targets | `GET /api/campaigns/{campaign_id}/targets` | `PUT /api/campaigns/{campaign_id}/targets` |
| campaign summary | `GET /api/campaigns/{campaign_id}/summary` | `POST /api/campaigns/{campaign_id}/summary` |
| campaign runtime state | `GET /api/campaigns/{campaign_id}/status` | `POST /api/campaigns/{campaign_id}/start`, `DELETE /api/campaigns/{campaign_id}` |
| Galileo dashboard data | `GET /api/galileo/...` routes below | `POST /api/galileo/...` action routes below |

Internal-only today:

- `call_sessions`
- `quote_events`
- `latest_quotes`
- `session_locks`

Those tables are written by background flows, but there are no stable dashboard CRUD endpoints for them yet.

## Negotiations

### Create one negotiation

`POST /negotiations`

```json
{
  "vendor_name": "Hilton Hotels",
  "strategy": "balanced",
  "product_category": "hotel",
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
  }
}
```

### List negotiations

`GET /negotiations`

### Get one negotiation with messages

`GET /negotiations/{negotiation_id}`

### Get messages only

`GET /negotiations/{negotiation_id}/messages`

### Update strategy/config

`PATCH /negotiations/{negotiation_id}`

```json
{
  "strategy": "aggressive",
  "config": {
    "target_unit_price": 145,
    "max_unit_price": 190,
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
  }
}
```

### Approve a negotiation

`POST /negotiations/{negotiation_id}/approve`

This updates the `negotiations` row and inserts an approval message into `messages`.

### Escalate a negotiation

`POST /negotiations/{negotiation_id}/escalate`

This updates the `negotiations` row and inserts a system message into `messages`.

### Create a batch and optionally auto-call

`POST /negotiations/batch`

```json
{
  "product_category": "hotel",
  "auto_call": true,
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
  "vendors": [
    {
      "vendor_name": "Hilton Hotels",
      "strategy": "balanced",
      "vendor_phone_number": "+15551111111"
    }
  ]
}
```

## Historic Pricing

Mounted under `/api/hotel-data`.

### Create one row

`POST /api/hotel-data/historic-pricing`

```json
{
  "hotel": "Peninsula Hotels",
  "location": "Magnificent Mile",
  "month": 7,
  "year": 2025,
  "price_per_night": 825
}
```

### List rows

`GET /api/hotel-data/historic-pricing`

Optional query params:

- `hotel`
- `location`

Examples:

```text
GET /api/hotel-data/historic-pricing?hotel=Peninsula%20Hotels
GET /api/hotel-data/historic-pricing?location=Magnificent%20Mile
```

### Get one row

`GET /api/hotel-data/historic-pricing/{id}`

## Past Negotiations

Mounted under `/api/hotel-data`.

### Create one row

`POST /api/hotel-data/past-negotiations`

```json
{
  "hotel": "Hilton Hotels",
  "location": "The Loop",
  "month": 10,
  "year": 2025,
  "starting_price": 380,
  "negotiation_price": 310,
  "proposed_price": 285
}
```

### List rows

`GET /api/hotel-data/past-negotiations`

Optional query params:

- `hotel`
- `location`

### Get one row

`GET /api/hotel-data/past-negotiations/{id}`

## CSV Import

### Import the two project CSVs into hotel data tables

`POST /negotiations/import-market-data`

Default payload:

```json
{}
```

Explicit payload:

```json
{
  "filenames": [
    "San Francisco Hospitality Market Data - Chicago Historic Pricing.csv",
    "San Francisco Hospitality Market Data - Chicago Past Negotiations.csv"
  ],
  "truncate_existing": true
}
```

Notes:

- This imports into `historic_pricing` and `past_negotiations`.
- The importer supports both the current `month`/`year` schema and older `date`-column variants still present in some local DBs.

## Campaigns

These routes drive the current voice campaign flow.

Important:

- Campaign state is currently in-memory, not durable.
- A server restart clears running campaign tasks, targets, and summaries.

### Store campaign targets

`PUT /api/campaigns/{campaign_id}/targets`

```json
{
  "targets": [
    {
      "hotel_id": "hotel-1",
      "phone_number": "+15551111111",
      "check_in": "2025-06-01",
      "check_out": "2025-06-03",
      "room_type": "standard king",
      "target_rate": 150,
      "max_rate": 200,
      "priority_score": 1.0,
      "market_context": {}
    }
  ]
}
```

### Read campaign targets

`GET /api/campaigns/{campaign_id}/targets`

### Start campaign

`POST /api/campaigns/{campaign_id}/start`

### Read campaign runtime status

`GET /api/campaigns/{campaign_id}/status`

### Cancel campaign

`DELETE /api/campaigns/{campaign_id}`

### Read or write campaign summary

- `GET /api/campaigns/{campaign_id}/summary`
- `POST /api/campaigns/{campaign_id}/summary`

## Galileo Dashboard APIs

These are already shaped for dashboard usage and backed by the Galileo tables.

### Pull

- `GET /api/galileo/enterprises/{enterprise_id}`
- `GET /api/galileo/enterprises/{enterprise_id}/agents`
- `GET /api/galileo/agents/{agent_id}`
- `GET /api/galileo/enterprises/{enterprise_id}/events`
- `GET /api/galileo/events/{event_id}`
- `GET /api/galileo/companies`
- `GET /api/galileo/companies/{company_id}`
- `GET /api/galileo/enterprises/{enterprise_id}/companies/{company_id}`
- `GET /api/galileo/agents/{agent_id}/activity-stream`
- `GET /api/galileo/agents/{agent_id}/transcript`

### Push / actions

- `POST /api/galileo/events/{event_id}/agents/{agent_id}/accept`
- `POST /api/galileo/agents/{agent_id}/intervene`
- `POST /api/galileo/market/pricing`
- `POST /api/galileo/negotiations/launch`
- `POST /api/galileo/market/event-window`

## Known Gaps

Not yet documented as stable dashboard CRUD APIs:

- quote event ingestion and latest quote reads
- call session list/detail endpoints
- session lock inspection or release
- direct dashboard CRUD for `messages`
- direct dashboard CRUD for Galileo tables outside the existing read/action routes

If you want full dashboard CRUD coverage, the next clean additions would be:

1. `GET /api/quotes` and `GET /api/quotes/latest`
2. `GET /api/call-sessions` and `GET /api/call-sessions/{id}`
3. `GET /api/session-locks` and `DELETE /api/session-locks/{lock_key}`
4. `POST/PUT/DELETE` for `historic_pricing` and `past_negotiations`
5. direct list/detail endpoints for `quote_events`

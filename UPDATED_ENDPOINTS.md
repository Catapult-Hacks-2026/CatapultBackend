# Updated Endpoints

This file documents the endpoints currently mounted in [app/main.py](/Users/nathanielkemmenash/Desktop/Catapult26/CatapultBackend/app/main.py).

## Base

### `GET /`
Input:
- No path params
- No query params
- No request body

Output:
```json
{
  "status": "ok"
}
```


## Hotel Data

### `POST /api/hotel-data/historic-pricing`
Input:
```json
{
  "hotel": "string",
  "location": "string",
  "month": 1,
  "year": 2026,
  "price_per_night": 0
}
```

Output:
```json
{
  "id": 1,
  "hotel": "string",
  "location": "string",
  "month": 1,
  "year": 2026,
  "price_per_night": 0,
  "created_at": "string"
}
```

### `GET /api/hotel-data/historic-pricing`
Input:
- Query params:
  - `hotel: string | null`
  - `location: string | null`

Output:
```json
[
  {
    "id": 1,
    "hotel": "string",
    "location": "string",
    "month": 1,
    "year": 2026,
    "price_per_night": 0,
    "created_at": "string"
  }
]
```

### `GET /api/hotel-data/historic-pricing/{item_id}`
Input:
- Path params:
  - `item_id: integer`

Output:
```json
{
  "id": 1,
  "hotel": "string",
  "location": "string",
  "month": 1,
  "year": 2026,
  "price_per_night": 0,
  "created_at": "string"
}
```

Error:
- `404` when the record does not exist

### `POST /api/hotel-data/past-negotiations`
Input:
```json
{
  "hotel": "string",
  "location": "string",
  "month": 1,
  "year": 2026,
  "starting_price": 0,
  "negotiation_price": 0,
  "proposed_price": 0
}
```

Output:
```json
{
  "id": 1,
  "hotel": "string",
  "location": "string",
  "month": 1,
  "year": 2026,
  "starting_price": 0,
  "negotiation_price": 0,
  "proposed_price": 0,
  "created_at": "string"
}
```

### `GET /api/hotel-data/past-negotiations`
Input:
- Query params:
  - `hotel: string | null`
  - `location: string | null`

Output:
```json
[
  {
    "id": 1,
    "hotel": "string",
    "location": "string",
    "month": 1,
    "year": 2026,
    "starting_price": 0,
    "negotiation_price": 0,
    "proposed_price": 0,
    "created_at": "string"
  }
]
```

### `GET /api/hotel-data/past-negotiations/{item_id}`
Input:
- Path params:
  - `item_id: integer`

Output:
```json
{
  "id": 1,
  "hotel": "string",
  "location": "string",
  "month": 1,
  "year": 2026,
  "starting_price": 0,
  "negotiation_price": 0,
  "proposed_price": 0,
  "created_at": "string"
}
```

Error:
- `404` when the record does not exist

## Galileo

### `GET /api/galileo/enterprises/{enterprise_id}`
Input:
- Path params:
  - `enterprise_id: string`

Output:
```json
{
  "id": "string",
  "name": "string",
  "description": "string",
  "totalSavedHotels": 0,
  "totalSavedAirlines": 0,
  "totalSaved": 0,
  "yoyChange": 0,
  "hotelContractCount": 0,
  "airlineContractCount": 0
}
```

### `GET /api/galileo/enterprises/{enterprise_id}/agents`
Input:
- Path params:
  - `enterprise_id: string`
- Query params:
  - `status: string | null`
  - `eventId: string | null`
  - `limit: integer`, default `50`, min `1`, max `500`

Output:
```json
[
  {
    "id": "string",
    "enterpriseId": "string",
    "eventId": "string",
    "companyId": "string",
    "companyName": "string",
    "status": "string",
    "outcome": "string",
    "idealPrice": 0,
    "ceilingPrice": 0,
    "marketPrice": 0,
    "currentPrice": 0,
    "isAccepted": false
  }
]
```

### `GET /api/galileo/agents/{agent_id}`
Input:
- Path params:
  - `agent_id: string`

Output:
```json
{
  "id": "string",
  "enterpriseId": "string",
  "eventId": "string",
  "companyId": "string",
  "companyName": "string",
  "status": "string",
  "outcome": "string",
  "idealPrice": 0,
  "ceilingPrice": 0,
  "marketPrice": 0,
  "currentPrice": 0,
  "isAccepted": false
}
```

### `GET /api/galileo/enterprises/{enterprise_id}/events`
Input:
- Path params:
  - `enterprise_id: string`
- Query params:
  - `status: string | null`

Output:
```json
[
  {
    "id": "string",
    "enterpriseId": "string",
    "name": "string",
    "location": "string",
    "startDate": "string",
    "endDate": "string",
    "attendees": 0,
    "service": "Hotel",
    "status": "Active",
    "agents": [
      {
        "id": "string",
        "enterpriseId": "string",
        "eventId": "string",
        "companyId": "string",
        "companyName": "string",
        "status": "string",
        "outcome": "string",
        "idealPrice": 0,
        "ceilingPrice": 0,
        "marketPrice": 0,
        "currentPrice": 0,
        "isAccepted": false
      }
    ],
    "requirements": "string",
    "budgetPerPerson": 0,
    "winnerAgentId": "string",
    "winnerTranscript": [
      {
        "id": "string",
        "agentId": "string",
        "message": "string",
        "sender": "Galileo",
        "timestamp": "string"
      }
    ],
    "winnerPricePath": [
      {
        "label": "string",
        "price": 0,
        "type": "offer",
        "round": 1
      }
    ]
  }
]
```

### `GET /api/galileo/events/{event_id}`
Input:
- Path params:
  - `event_id: string`

Output:
- Same object shape as one item from `GET /api/galileo/enterprises/{enterprise_id}/events`

### `GET /api/galileo/companies`
Input:
- Query params:
  - `q: string | null`
  - `limit: integer`, default `100`, min `1`, max `500`

Output:
```json
[
  {
    "id": "string",
    "name": "string",
    "initials": "string",
    "description": "string",
    "phone": "string",
    "website": "string",
    "industry": "string",
    "badge": "string",
    "locations": [
      {
        "id": "string",
        "companyId": "string",
        "name": "string",
        "address": "string",
        "phone": "string"
      }
    ]
  }
]
```

### `GET /api/galileo/companies/{company_id}`
Input:
- Path params:
  - `company_id: string`

Output:
- Same object shape as one item from `GET /api/galileo/companies`

### `GET /api/galileo/enterprises/{enterprise_id}/companies/{company_id}`
Input:
- Path params:
  - `enterprise_id: string`
  - `company_id: string`
- Query params:
  - `locationId: string | null`

Output:
```json
{
  "companyId": "string",
  "enterpriseId": "string",
  "locationId": "string",
  "lifetimeSavings": 0,
  "savingsDelta": 0,
  "agreementsCount": 0,
  "agreementsSummary": "string",
  "avgDelta": 0,
  "totalBookings": 0,
  "totalSavings": 0,
  "yoyChange": 0,
  "pricingTrends": [
    {
      "month": "JAN",
      "year": 2026,
      "range": "1Y",
      "negotiatedPrice": 0,
      "marketPrice": 0
    }
  ],
  "bookingWindow": [
    {
      "month": "JAN",
      "score": 0,
      "status": "Best Deal"
    }
  ],
  "linkedEvents": [
    {
      "id": "string",
      "enterpriseId": "string",
      "name": "string",
      "location": "string",
      "startDate": "string",
      "endDate": "string",
      "attendees": 0,
      "service": "Hotel",
      "status": "Active",
      "agents": [],
      "requirements": "string",
      "budgetPerPerson": 0,
      "winnerAgentId": "string",
      "winnerTranscript": [],
      "winnerPricePath": []
    }
  ]
}
```

### `POST /api/galileo/events/{event_id}/agents/{agent_id}/accept`
Input:
- Path params:
  - `event_id: string`
  - `agent_id: string`
- Body:
```json
{
  "enterpriseId": "string"
}
```

Output:
- Same object shape as `GET /api/galileo/events/{event_id}`

### `POST /api/galileo/agents/{agent_id}/intervene`
Input:
- Path params:
  - `agent_id: string`

Output:
```json
{
  "agentId": "string",
  "status": "Routed",
  "callRoutingInfo": "string",
  "transferredAt": "string"
}
```

Errors:
- `404` when the agent does not exist
- `409` when the agent status is not `Negotiating`

### `GET /api/galileo/agents/{agent_id}/activity-stream`
Input:
- Path params:
  - `agent_id: string`

Output:
- Server-sent events stream
- Event name: `activity`
- Each event `data` is:
```json
{
  "id": "string",
  "agentId": "string",
  "price": 0,
  "badge": "string",
  "badgeType": "neutral",
  "detail": "string",
  "detailType": "neutral",
  "timestamp": "string",
  "active": true
}
```

### `GET /api/galileo/agents/{agent_id}/transcript`
Input:
- Path params:
  - `agent_id: string`

Output:
- Server-sent events stream
- Event name: `message`
- Each event `data` is:
```json
{
  "id": "string",
  "agentId": "string",
  "message": "string",
  "sender": "Galileo",
  "timestamp": "string"
}
```

### `POST /api/galileo/market/pricing`
Input:
```json
{
  "service": "Hotel",
  "location": "string",
  "startDate": "2026-01-01",
  "endDate": "2026-01-03",
  "attendees": 100
}
```

Output:
```json
{
  "service": "Hotel",
  "hotel": {
    "marketPrice": 0,
    "predictedWinPrice": 0,
    "unit": "per night"
  },
  "airline": {
    "marketPrice": 0,
    "predictedWinPrice": 0,
    "unit": "per seat"
  }
}
```

### `POST /api/galileo/negotiations/launch`
Input:
```json
{
  "enterpriseId": "string",
  "eventName": "string",
  "service": "Hotel",
  "startDate": "2026-01-01",
  "endDate": "2026-01-03",
  "location": "string",
  "attendees": 100,
  "budgetPerPerson": 0,
  "requirements": "string",
  "guardrails": {
    "hotel": {
      "idealPrice": 0,
      "ceilingPrice": 0
    },
    "airline": {
      "idealPrice": 0,
      "ceilingPrice": 0
    }
  }
}
```

Output:
- Same object shape as `GET /api/galileo/events/{event_id}`

### `POST /api/galileo/market/event-window`
Input:
```json
{
  "location": "string",
  "eventType": "string",
  "preferredTiming": "Q2 2026",
  "attendees": 100,
  "nights": 3,
  "eventDetails": "string"
}
```

Output:
```json
[
  {
    "label": "Best Overall",
    "startDate": "2026-04-01",
    "endDate": "2026-04-04",
    "explanation": "string",
    "hotel": {
      "marketCost": 0,
      "negotiatedPrice": 0,
      "savings": 0
    },
    "airline": {
      "marketCost": 0,
      "negotiatedPrice": 0,
      "savings": 0
    },
    "negotiationConfidence": 0
  }
]
```

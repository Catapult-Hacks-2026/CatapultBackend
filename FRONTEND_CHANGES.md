# Frontend Integration Guide

All endpoints are prefixed with `/api/galileo` unless noted otherwise.

---

## 1. Launch Negotiations

### Request

**POST /api/galileo/negotiations/launch**

```typescript
interface LaunchNegotiationRequest {
  enterpriseId: string;
  eventName: string;
  service: "Hotel";
  startDate: string;       // ISO date "YYYY-MM-DD"
  endDate: string;
  location: string;
  attendees: number;
  idealPrice?: number;     // NEW -- top-level, preferred over guardrails
  ceilingPrice?: number;   // NEW -- top-level, preferred over guardrails
  budgetPerPerson?: number;
  requirements?: string;
  guardrails?: {           // still supported as fallback
    hotel?: { idealPrice: number; ceilingPrice: number };
  };
}
```

**Priority for ideal/ceiling price resolution (first non-null wins):**

1. Top-level `idealPrice` / `ceilingPrice`
2. `guardrails.hotel.idealPrice` / `guardrails.hotel.ceilingPrice`
3. `budgetPerPerson`
4. Random (service-appropriate range)

### Response

```typescript
interface GalileoEvent {
  id: string;
  enterpriseId: string;
  name: string;
  location: string;
  startDate: string;
  endDate: string;
  attendees: number;
  service: "Hotel";
  status: "Active" | "Completed";
  agents: Agent[];
  requirements?: string;
  budgetPerPerson?: number;
  winnerAgentId?: string;
  winnerTranscript?: Message[];
  winnerPricePath?: PricePoint[];
}
```

The first agent is automatically set to `status: "Negotiating"` and a Twilio call
is triggered. The agent's `idealPrice` and `ceilingPrice` are passed to the voice
pipeline as `target_rate` and `max_rate`.

---

## 2. WebSocket -- Live Transcript and Price Updates

### Connect

```
WS /api/galileo/agents/{agentId}/transcript/ws?last_index=-1
```

The connection polls for up to 15 seconds waiting for the call to start. If no
active call is found, the server sends an error and closes with code `4004`.

### Server Messages

All messages include `type`, `session_id`, and `timestamp` (ISO string).

#### Backfill (sent once on connect)

```typescript
{
  type: "backfill";
  session_id: string;
  entries: { role: string; content: string; index: number }[];
}
```

#### Transcript Updates

```typescript
// Partial (streaming, may be overwritten)
{
  type: "transcript_partial";
  role: "hotel" | "agent";
  content: string;
  index: number;
}

// Final (committed utterance)
{
  type: "transcript_final";
  role: "hotel" | "agent";
  content: string;
  index: number;
}
```

#### Price Changed (NEW)

Fired when either side proposes a new price during negotiation.

```typescript
{
  type: "price_changed";
  galileo_agent_id: string;
  price: number;
  source: "galileo" | "hotel_rep";   // who proposed this price
  session_id: string;
  timestamp: string;
}
```

**Frontend actions:**
- Append a new point to the price path chart
- Style by `source`: use one color/icon for `"galileo"` and another for `"hotel_rep"`
- Update the agent's `currentPrice` in local state

#### Deal Finalized (NEW)

Fired when a deal is closed.

```typescript
{
  type: "deal_finalized";
  galileo_agent_id: string;
  final_price: number;
  market_price: number;
  savings: number;
  session_id: string;
  timestamp: string;
}
```

**Frontend actions:**
- Transition agent card to completed/accepted state
- Show final price and savings prominently
- Show toast notification
- Re-fetch agent and enterprise data to update savings totals

#### Call Ended

Outcome is derived deterministically from the agent's actual moves during the call
(e.g. an explicit ACCEPT move produces `"rate_confirmed"`). This is always populated.

```typescript
{
  type: "call_ended";
  outcome: string;   // "rate_confirmed" | "callback_requested" | "no_availability" | "failed" | "timed_out"
  final_price: number | null;
  session_id: string;
  timestamp: string;
}
```

| outcome | Meaning |
|---------|---------|
| `"rate_confirmed"` | Agent accepted a rate -- deal closed |
| `"callback_requested"` | Hotel asked to call back later |
| `"no_availability"` | Hotel had no rooms / rejected |
| `"escalated_to_human"` | Routed to human representative |
| `"failed"` | Call ended without agreement |
| `"timed_out"` | Max call duration or turns reached |

**Frontend actions:**
- Close the WebSocket connection
- If `outcome === "rate_confirmed"` and `final_price` is set, show deal-closed UI immediately (no need to wait for re-fetch)
- Re-fetch agent via `GET /agents/{agentId}` for full final state
- Update agent card status badge using the outcome

### Client Messages

```typescript
// Keep-alive ping (server responds with { type: "pong" })
{ type: "ping" }
```

---

## 3. Price Change Webhook (NEW)

Record a confirmed price change from either party. Called automatically during
voice calls; can also be called manually for human intervention scenarios.

**POST /api/galileo/agents/{agentId}/price-change**

```typescript
// Request
interface PriceChangeRequest {
  price: number;
  source: "galileo" | "hotel_rep";
  round?: number;  // auto-increments if omitted
}

// Response
interface PriceChangeResult {
  agentId: string;
  price: number;
  previousPrice: number;
  marketPrice: number;
  source: "galileo" | "hotel_rep";
  round: number;
}
```

Agent must be in `"Negotiating"` or `"Reviewing"` status; returns `409` otherwise.

**Side effects:**
- Updates `galileo_agents.current_price`
- Inserts a price point labeled `"Galileo Counter"` (type `"negotiated"`) or `"Hotel Offer"` (type `"offer"`)
- Inserts an activity stream entry with badge `"Price Down"` (savings) or `"Price Up"` (neutral)
- Publishes `PRICE_CHANGED` event to all connected WebSocket clients

---

## 4. Close Deal Webhook (NEW)

Finalize a negotiation with the accepted price.

**POST /api/galileo/agents/{agentId}/close-deal**

```typescript
// Request
interface FinalOfferRequest {
  finalPrice: number;
  enterpriseId: string;
}

// Response
interface FinalOfferResult {
  agentId: string;
  finalPrice: number;
  marketPrice: number;
  savings: number;
}
```

**Side effects:**
- Sets agent `status: "Completed"`, `outcome: "RATE_CONFIRMED"`, `is_accepted: true`
- Inserts a `"Final Accepted"` price point (type `"final"`)
- Inserts a `"Deal Closed"` activity entry with savings vs market
- Updates `galileo_enterprises` totals (total_saved, hotel_contract_count)
- Publishes `DEAL_FINALIZED` event to all connected WebSocket clients

---

## 5. Activity Stream

### Source labels

Activity entries created by the price tracking system use these badge values:

| Scenario | badge | badgeType | detailType |
|----------|-------|-----------|------------|
| Hotel rep lowers price | `"Price Down"` | `"savings"` | `"positive"` |
| Hotel rep raises price | `"Price Up"` | `"neutral"` | `"negative"` |
| Galileo counters lower | `"Price Down"` | `"savings"` | `"positive"` |
| Galileo counters higher | `"Price Up"` | `"neutral"` | `"negative"` |
| Deal closed | `"Deal Closed"` | `"savings"` | `"positive"` |
| Rate confirmed | `"Rate Confirmed"` | `"positive"` | `"positive"` |
| Callback requested | `"Callback Requested"` | `"neutral"` | `"neutral"` |
| No availability | `"No Availability"` | `"negative"` | `"negative"` |
| Call failed | `"Call Failed"` | `"negative"` | `"negative"` |

The `detail` field includes the source label, e.g.:
- `"$185.00/night (Hotel Rep) | saved $15.00"`
- `"$175.00/night (Galileo) | saved $10.00"`
- `"Final rate: $179.00/night | Saved $31.00 vs market"`

### Polling via SSE

**GET /api/galileo/agents/{agentId}/activity-stream**

Returns Server-Sent Events. Each event:
```
event: activity
id: <activity_id>
data: <JSON ActivityStreamItem>
```

New entries from price changes and deal closures appear automatically.

---

## 6. Price Points

Price points returned in agent detail (`GET /agents/{agentId}`) under `pricePath`:

```typescript
interface PricePoint {
  label: string;
  price: number;
  type: "offer" | "negotiated" | "current" | "final";
  round: number;
}
```

During a live negotiation, new points are added with:

| Action | label | type |
|--------|-------|------|
| Hotel rep offers | `"Hotel Offer"` | `"offer"` |
| Galileo counters | `"Galileo Counter"` | `"negotiated"` |
| Deal finalized | `"Final Accepted"` | `"final"` |

Use `type` and `label` to color-code the price path chart by source.

---

## 7. Agent Status Reference

### Status values

| Status | Meaning |
|--------|---------|
| `"Queued"` | Waiting to negotiate |
| `"Negotiating"` | Active call in progress |
| `"Reviewing"` | Human review / escalated |
| `"Completed"` | Negotiation finished |
| `"Optimized"` | Post-completion optimization |
| `"Cancelled"` | Dropped in favor of another agent |
| `"Awaiting Callback"` | Hotel requested a callback |
| `"Failed"` | Call or negotiation failed |

### Outcome values

| Outcome | Meaning |
|---------|---------|
| `"RATE_CONFIRMED"` | Deal closed at agreed rate |
| `"CALLBACK_REQUESTED"` | Hotel asked for a callback |
| `"NO_AVAILABILITY"` | Hotel has no rooms |
| `"ESCALATED_TO_HUMAN"` | Routed to human rep |
| `"FAILED"` | Negotiation failed |
| `"TIMED_OUT"` | Max turns reached |

---

## 8. Suggested UI Behaviors

### On `price_changed` WebSocket message
1. Animate a new point onto the price path chart
2. Flash the agent card's current price
3. Show source attribution: Galileo icon or Hotel icon next to the price

### On `deal_finalized` WebSocket message
1. Transition agent card to green/completed state
2. Display savings badge: `"Saved $X vs market"`
3. Show toast: `"Deal closed with [Hotel] at $X/night"`
4. Re-fetch enterprise data to update dashboard savings

### On `call_ended` WebSocket message
1. Close the WebSocket connection
2. Re-fetch `GET /agents/{agentId}` for final state
3. Map `outcome` to status badge color:
   - `RATE_CONFIRMED` -> green
   - `CALLBACK_REQUESTED` -> yellow
   - `NO_AVAILABILITY` -> gray
   - `ESCALATED_TO_HUMAN` -> orange
   - `FAILED` / `TIMED_OUT` -> red
4. If all agents for the event are done, refresh event status

### On launching a negotiation
1. Send `idealPrice` and `ceilingPrice` at top level in the request body
2. After response, connect to `WS /agents/{agentId}/transcript/ws` for the first agent with `status: "Negotiating"`
3. Show the price path chart with initial market price point

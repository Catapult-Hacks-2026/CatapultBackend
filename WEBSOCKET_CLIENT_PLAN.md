# WebSocket Transcript Streaming -- React Client Integration Plan

This document describes the backend WebSocket endpoint for live call transcripts and everything needed to consume it from a React frontend.

---

## Backend Endpoint

```
ws(s)://<backend-host>/api/galileo/agents/{agent_id}/transcript/ws?last_index=-1
```

- `agent_id`: The Galileo agent/negotiation ID whose live call you want to stream.
- `last_index` (query param, default `-1`): The highest transcript entry index the client has already seen. On first connect, use `-1` to get the full transcript. On reconnect, pass the last index you received so the server only sends entries you missed.

---

## Message Protocol

All messages are JSON with a `type` discriminator field.

### Server -> Client

#### `backfill`
Sent immediately after connection. Contains all transcript entries with `index > last_index`.

```json
{
  "type": "backfill",
  "session_id": "sess_abc123",
  "entries": [
    { "role": "hotel", "content": "Front desk, how can I help?", "index": 0 },
    { "role": "agent", "content": "Hi, I'm calling about corporate rates.", "index": 1 }
  ]
}
```

#### `transcript_partial`
Ephemeral partial transcript from speech-to-text. Replaces the previous partial. Always from the hotel side (the person on the phone). Not indexed -- these are not persisted.

```json
{
  "type": "transcript_partial",
  "text": "We could possibly do two hun",
  "session_id": "sess_abc123",
  "timestamp": "2026-04-05T18:30:01.123Z"
}
```

#### `transcript_final`
A committed transcript entry. Has a monotonically increasing `index` (position in the full transcript array). Both hotel and agent utterances produce finals.

```json
{
  "type": "transcript_final",
  "role": "hotel",
  "content": "We could possibly do two hundred per night.",
  "index": 4,
  "session_id": "sess_abc123",
  "timestamp": "2026-04-05T18:30:02.456Z"
}
```

#### `call_ended`
The call has finished. Close the WebSocket after receiving this.

```json
{
  "type": "call_ended",
  "session_id": "sess_abc123",
  "timestamp": "2026-04-05T18:35:00.000Z"
}
```

#### `error`
Something went wrong server-side.

```json
{
  "type": "error",
  "message": "No active call"
}
```

If the server closes with code `4004`, there is no active call for that agent. Do not reconnect.

#### `pong`
Response to a client `ping`.

```json
{ "type": "pong" }
```

### Client -> Server

#### `ping`
Send periodically (every 30s) to keep the connection alive.

```json
{ "type": "ping" }
```

---

## Implementation Steps

### 1. Types

Create a types file with:

```typescript
export interface TranscriptEntry {
  role: "hotel" | "agent";
  content: string;
  index: number;
}

export type ConnectionState = "connecting" | "connected" | "disconnected" | "error";

export interface TranscriptState {
  entries: TranscriptEntry[];
  partial: string | null;
  connectionState: ConnectionState;
  lastIndex: number;
  error: string | null;
}

export type ServerMessage =
  | { type: "backfill"; entries: TranscriptEntry[]; session_id: string }
  | { type: "transcript_partial"; text: string; session_id: string; timestamp: string }
  | { type: "transcript_final"; role: "hotel" | "agent"; content: string; index: number; session_id: string; timestamp: string }
  | { type: "call_ended"; session_id: string; timestamp: string }
  | { type: "error"; message: string }
  | { type: "pong" };
```

### 2. State Reducer

Create a reducer for `TranscriptState`. Actions:

| Action | Behavior |
|---|---|
| `CONNECTING` | Set `connectionState` to `"connecting"` |
| `CONNECTED` | Set `connectionState` to `"connected"`, clear `error` |
| `DISCONNECTED` | Set `connectionState` to `"disconnected"` |
| `ERROR` | Set `connectionState` to `"error"`, store error message |
| `BACKFILL` | Filter out entries with `index <= state.lastIndex`, append the rest, update `lastIndex` to the max index seen |
| `PARTIAL` | Set `state.partial` to the text (replaces any previous partial) |
| `FINAL` | Append entry only if `entry.index > state.lastIndex`, clear `partial`, update `lastIndex` |
| `CALL_ENDED` | Set `connectionState` to `"disconnected"`, clear `partial` |
| `RESET` | Return initial state |

### 3. Custom Hook: `useTranscriptStream`

```typescript
function useTranscriptStream(agentId: string | null): {
  state: TranscriptState;
  disconnect: () => void;
}
```

Implementation details:

- **URL construction:** `ws(s)://${BACKEND_HOST}/api/galileo/agents/${agentId}/transcript/ws?last_index=${lastIndexRef.current}`
- Use a `useRef` for `lastIndex` that stays in sync with the reducer state so reconnects use the latest value.
- **`useEffect` keyed on `agentId`:**
  1. If `agentId` is null, do nothing.
  2. Create `new WebSocket(url)`, dispatch `CONNECTING`.
  3. `onopen`: dispatch `CONNECTED`, start a 30-second ping interval (`setInterval` sending `{"type":"ping"}`).
  4. `onmessage`: `JSON.parse` the data (wrap in try/catch, ignore malformed), switch on `type`:
     - `"backfill"` -> dispatch `BACKFILL` with entries
     - `"transcript_partial"` -> dispatch `PARTIAL` with text
     - `"transcript_final"` -> dispatch `FINAL` with `{ role, content, index }`
     - `"call_ended"` -> dispatch `CALL_ENDED`, then `ws.close()`
     - `"error"` -> dispatch `ERROR` with message
     - `"pong"` -> no-op
  5. `onclose(event)`: Clear ping interval. If `event.code === 4004`, dispatch `ERROR` with "No active call" -- do not reconnect. Otherwise dispatch `DISCONNECTED` and schedule reconnect.
  6. `onerror`: dispatch `ERROR`.
- **Reconnection:** Exponential backoff starting at 1s, doubling to max 30s. Max 10 retries before giving up (dispatch `ERROR`). Reset backoff counter on successful connection. Use `setTimeout` stored in a ref so cleanup can cancel it.
- **Cleanup function:** Close WebSocket, clear ping interval, cancel reconnect timeout.

### 4. Rendering

Use the hook in whatever component displays the live transcript:

- Render `state.entries` as a message list (hotel = left-aligned, agent = right-aligned, or however your UI works).
- Render `state.partial` as a visually distinct entry at the bottom (e.g., italic, reduced opacity, pulsing dots) -- this is the hotel mid-sentence. Clear it when the next `FINAL` arrives.
- Show connection state: green dot for `"connected"`, yellow for `"connecting"`, gray for `"disconnected"`, red for `"error"`.
- Auto-scroll to bottom on new entries (ref on a sentinel div, `scrollIntoView({ behavior: "smooth" })` in a `useEffect` triggered by `entries.length` or `partial`).

### 5. Error Handling Summary

| Scenario | Behavior |
|---|---|
| Server unreachable | `onerror` fires, reconnect with backoff |
| Server closes with 4004 | No active call -- show error, do not reconnect |
| Network drops mid-call | `onclose` fires, reconnect with `last_index` for dedup |
| Malformed JSON from server | Catch in `onmessage`, log and ignore |
| Component unmounts | Cleanup closes socket, clears timers |
| Index gap after reconnect | Backfill fills the gap; `last_index` query param handles this |

---

## Sequence Diagram

```
React Client                       Backend (FastAPI)
  |                                   |
  |-- WS connect (?last_index=-1) --->|
  |                                   |-- lookup agent -> session
  |                                   |-- snapshot transcript
  |<-- backfill [entries 0..N] -------|
  |                                   |-- subscribe EventBus
  |                                   |
  |        [hotel speaks]             |
  |<-- transcript_partial "I can" ----|
  |<-- transcript_partial "I can of" -|
  |<-- transcript_final {index:4} ----|  (partial replaced)
  |                                   |
  |        [agent responds]           |
  |<-- transcript_final {index:5} ----|
  |                                   |
  |-- ping --------------------------->|
  |<-- pong ----------------------------|
  |                                   |
  |        [call ends]                |
  |<-- call_ended ---------------------|
  |-- close --------------------------->|
```

# Catapult26 — Hotel Rate Negotiation Agent

An AI-powered system that autonomously calls hotels, negotiates room rates via live phone conversation, and manages multi-hotel campaigns at scale.

## Architecture

```
Hotel (Phone)
      │
      ▼
┌─────────────┐     mulaw audio      ┌─────────────────┐
│   Twilio    │ ──────────────────── │  AssemblyAI STT  │
│  (WebSocket)│                      │  (real-time)     │
└─────────────┘                      └────────┬─────────┘
                                              │ transcript
                                              ▼
                                     ┌─────────────────┐
                                     │   VoicePipeline  │
                                     │                  │
                                     │  FactExtractor   │ ──► GPT-4o-mini
                                     │  NegotiationBrain│ ──► GPT-4o
                                     │  Guardrails      │
                                     └────────┬─────────┘
                                              │ tokens
                                              ▼
                                     ┌─────────────────┐
                                     │  ElevenLabs TTS  │
                                     │  (WebSocket)     │
                                     └────────┬─────────┘
                                              │ ulaw audio
                                              ▼
                                           Twilio → Hotel

Campaign Controller (LangGraph)
  └── Scores + prioritizes hotels
  └── Spawns concurrent WorkerSessions
  └── Monitors outcomes, retries, deduplicates

Memory Layer (ChromaDB + Backend API)
  └── Loads behavioral priors before each call
  └── Extracts and stores patterns after each call
```

## Tech Stack

- **Backend:** Python 3.11+ / FastAPI
- **Orchestration:** LangGraph StateGraph (worker + campaign graphs)
- **LLM:** OpenAI GPT-4o (negotiation brain) + GPT-4o-mini (fact extraction)
- **STT:** AssemblyAI real-time WebSocket
- **TTS:** ElevenLabs WebSocket streaming
- **Phone:** Twilio Programmable Voice
- **Memory:** ChromaDB (vector) + backend REST API (structured)

## API Keys Required

Create a `.env` file in the project root with the following:

```env
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# AssemblyAI (real-time STT)
ASSEMBLYAI_API_KEY=...

# ElevenLabs (TTS)
TTS_API_KEY=...
TTS_VOICE_ID=...
ELEVENLABS_MODEL_ID=eleven_turbo_v2_5

# Twilio (outbound calls)
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
TWILIO_TO_PHONE_NUMBER=+1...
TWILIO_TO_PHONE_NUMBERS=+1..., +1...

# Public URL — must be reachable by Twilio (see Testing section)
BASE_URL=https://your-ngrok-url.ngrok.io
```

Where to get each key:
- **OpenAI:** platform.openai.com
- **AssemblyAI:** assemblyai.com
- **ElevenLabs:** elevenlabs.io (also grab a Voice ID from the voices library)
- **Twilio:** console.twilio.com (buy a phone number with Voice capability)

## Prerequisites

- Python 3.11+
- If you are using Python 3.14, install from the current `requirements.txt`; older NumPy pins such as `2.0.x` do not build cleanly there on macOS arm64
- [Redis](https://redis.io/docs/getting-started/) running locally (used for session locking and coordination)

## Getting Started

1. **Clone the repo and create a virtual environment:**

   ```bash
   git clone <repo-url>
   cd catapult_main
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**

   ```bash
   cp .env.example .env
   ```

   Open `.env` and fill in the required API keys (see [Environment Variables](#environment-variables) below).

4. **Start Redis** (if not already running):

   ```bash
   redis-server
   ```

5. **Seed the database with synthetic history:**

   ```bash
   python -m app.seed
   ```

6. **Run the server:**

   ```bash
   uvicorn app.main:app --reload
   ```

   The API will be available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for the interactive Swagger UI.

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key for the negotiation LLM |
| `ASSEMBLYAI_API_KEY` | AssemblyAI key for speech-to-text |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Twilio phone number for outbound calls |
| `TWILIO_TO_PHONE_NUMBER` | Default destination phone number for outbound Twilio calls |
| `TWILIO_TO_PHONE_NUMBERS` | Comma-separated destination phone numbers for ad hoc multi-call launch scripts |
| `TTS_API_KEY` | ElevenLabs API key for text-to-speech |
| `TTS_VOICE_ID` | ElevenLabs voice ID |
| `DATABASE_URL` | SQLite connection string (default: `sqlite+aiosqlite:///data/negotiations.db`) |
| `REDIS_URL` | Redis connection string (default: `redis://localhost:6379/0`) |
| `BASE_URL` | Public URL for Twilio callbacks (e.g. an ngrok URL for local dev) |

### Exposing to Twilio (local development)

For Twilio to reach your local server, use [ngrok](https://ngrok.com/):

```bash
ngrok http 8000
```

Then set `BASE_URL` in your `.env` to the ngrok forwarding URL.

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys from above
uvicorn app.main:app --reload --port 8000
```

## Testing with a Real Phone Number

Twilio must be able to POST back to your server when a call connects. This requires a public URL — localhost will not work.

**Step 1 — Expose your local server:**

```bash
# Install ngrok: https://ngrok.com
ngrok http 8000
# Copy the https URL it gives you, e.g. https://abc123.ngrok.io
```

Set `BASE_URL=https://abc123.ngrok.io` in your `.env`, then restart the server.

**Step 2 — Start a campaign:**

```bash
curl -X POST http://localhost:8000/api/campaigns/test-campaign-1/start
```

This requires the backend to serve `GET /api/campaigns/test-campaign-1/targets` with hotel targets. If the backend is not yet available, use the script below instead.

**Step 3 — Direct worker smoke test (no backend required):**

```python
# test_worker.py
import asyncio
from app.hotel.schemas import HotelTarget, WorkerSessionState
from app.orchestration.worker_graph import WorkerSession, register_worker

async def main():
    target = HotelTarget(
        hotel_id="hotel-test-1",
        phone_number="+1XXXXXXXXXX",   # real hotel phone number
        check_in="2026-05-01",
        check_out="2026-05-03",
        room_type="king",
        target_rate=180.0,
        max_rate=220.0,
    )
    state = WorkerSessionState(
        session_id="test-session-1",
        hotel_target=target,
    )
    session = WorkerSession(state)
    register_worker(session)
    result = await session.run()
    print(result)

asyncio.run(main())
```

When Twilio dials the hotel and the call connects, it will POST to `{BASE_URL}/voice/twilio-stream/test-session-1`, which opens the WebSocket and starts the full STT → LLM → TTS pipeline.

## Campaign API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/campaigns/{id}/start` | Start a campaign |
| `GET` | `/api/campaigns/{id}/status` | Check campaign status |
| `DELETE` | `/api/campaigns/{id}` | Cancel a running campaign |

## Project Structure

```
app/
├── main.py
├── core/
│   ├── config.py              # env vars and settings
│   ├── events.py              # async event bus
│   └── shared_clients.py      # pooled HTTP + OpenAI clients
├── hotel/
│   ├── enums.py               # SessionStatus, NegotiationOutcome, MoveType
│   ├── schemas.py             # HotelTarget, HotelQuote, AgentMove, WorkerSessionState
│   ├── scoring.py             # rate scoring vs target/max
│   └── guardrails.py          # negotiation safety rules
├── llm/
│   ├── openai_client.py       # GPT-4o async client
│   ├── prompts.py             # all system prompts
│   ├── fact_extractor.py      # extract rate quotes from transcript
│   ├── negotiation_brain.py   # decide move + stream response
│   └── post_call_analyzer.py  # post-call analysis and lessons
├── voice/
│   ├── assemblyai_stt.py      # AssemblyAI real-time WebSocket STT
│   ├── elevenlabs_tts.py      # ElevenLabs WebSocket streaming TTS
│   ├── twilio_bridge.py       # Twilio media stream adapter
│   ├── pipeline.py            # full-duplex voice pipeline coordinator
│   └── interruption.py        # barge-in detection
├── memory/
│   ├── feature_schemas.py     # HotelBehavioralFeature, HotelBehavioralProfile
│   ├── behavioral_store.py    # ChromaDB + backend API memory layer
│   └── memory_candidates.py   # post-call feature extraction
├── orchestration/
│   ├── session_lock.py        # per-hotel asyncio lock
│   ├── worker_graph.py        # LangGraph worker StateGraph
│   ├── worker_nodes.py        # worker node functions
│   ├── worker_events.py       # lifecycle event emitter
│   ├── campaign_graph.py      # LangGraph campaign StateGraph
│   ├── campaign_nodes.py      # campaign node functions
│   └── job_scorer.py          # priority scoring for job queue
└── routers/
    ├── voice.py               # Twilio WebSocket + TwiML endpoints
    └── campaigns.py           # campaign start/status/cancel
```

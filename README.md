# Autonomous AI Procurement Agent

An automated negotiation platform that handles B2B procurement via real-time phone calls (Twilio) and text-based communication. Uses deterministic scoring, RAG-powered historical context, and LLM orchestration with strict guardrails.

## Architecture

```
Vendor (Phone/Webhook)
        │
        ▼
┌─────────────────┐
│  Twilio Voice    │──── STT (Deepgram) ────┐
│  / Webhook API   │                         │
└─────────────────┘                         │
                                            ▼
                                 ┌─────────────────────┐
                                 │   FastAPI Backend     │
                                 │                       │
                                 │  ├─ Negotiation FSM   │
                                 │  ├─ Scoring Engine     │
                                 │  ├─ RAG (ChromaDB)     │
                                 │  ├─ LLM Orchestrator   │
                                 │  ├─ Rule Engine        │
                                 │  └─ Session Manager    │
                                 └─────────┬─────────────┘
                                           │
                              ┌────────────┼────────────┐
                              ▼            ▼            ▼
                         SQLite      ChromaDB     LLM API
                        (state)     (history)   (Anthropic)
                              
        Dashboard (Next.js) ◄──── WebSocket ────┘
```

## Tech Stack

- **Backend:** Python 3.11+ / FastAPI
- **Database:** SQLite (session state) + ChromaDB (vector store)
- **Voice:** Twilio (phone calls) + Deepgram (STT) + ElevenLabs or Cartesia (TTS)
- **AI:** Anthropic Claude API (negotiation LLM)
- **Math:** NumPy (utility scoring)
- **Realtime:** FastAPI WebSockets
- **Frontend:** Next.js/React (separate repo)

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in API keys
python -m app.seed    # seed ChromaDB with synthetic data
uvicorn app.main:app --reload
```

## Project Structure

```
app/
├── main.py                 # FastAPI app, CORS, lifespan
├── core/
│   ├── config.py           # env vars, settings
│   └── database.py         # SQLite + ChromaDB init
├── models/
│   ├── schemas.py          # Pydantic models
│   └── enums.py            # negotiation states, strategies
├── services/
│   ├── scoring.py          # NumPy utility matrix
│   ├── rag.py              # ChromaDB retrieval
│   ├── llm.py              # Anthropic API interface
│   ├── guardrails.py       # rule engine / validation
│   ├── negotiation.py      # FSM orchestrator
│   └── voice.py            # Twilio + Deepgram + TTS
├── routers/
│   ├── negotiations.py     # CRUD + WebSocket endpoints
│   ├── webhooks.py         # vendor inbound (text)
│   └── voice.py            # Twilio voice routes
├── seed.py                 # seed synthetic history
data/
├── chroma/                 # ChromaDB persistent storage
└── negotiations.db         # SQLite database
```

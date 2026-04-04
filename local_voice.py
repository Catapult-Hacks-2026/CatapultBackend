#!/usr/bin/env python
"""Run a voice negotiation session using your local mic and speakers.

Bypasses Twilio entirely — you speak as the hotel, the agent responds through
your speakers. Still requires AssemblyAI + ElevenLabs API keys in .env.

Usage:
    python local_voice.py                          # sensible defaults
    python local_voice.py --target-rate 180 --max-rate 220 --room king
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

# Load .env before any app imports
from dotenv import load_dotenv

load_dotenv()

from app.hotel.schemas import HotelTarget, WorkerSessionState  # noqa: E402
from app.voice.local_bridge import LocalBridge  # noqa: E402
from app.voice.pipeline import VoicePipeline  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("local_voice")


def build_session(args: argparse.Namespace) -> WorkerSessionState:
    hotel_id = args.hotel_id or f"local-hotel-{uuid.uuid4().hex[:6]}"
    target = HotelTarget(
        hotel_id=hotel_id,
        phone_number="local",
        check_in=args.check_in,
        check_out=args.check_out,
        room_type=args.room,
        target_rate=args.target_rate,
        max_rate=args.max_rate,
        market_context={
            "hotel_name": args.hotel_name or hotel_id,
            "location": args.location,
            "market": args.location,
        },
    )
    return WorkerSessionState(
        session_id=f"local-{uuid.uuid4().hex[:8]}",
        hotel_target=target,
    )


async def on_quote(quote):  # noqa: ANN001
    logger.info("Quote received: $%.2f/night ($%.2f total)", quote.nightly_rate, quote.total_rate)


async def on_end(state: WorkerSessionState):
    logger.info("Session ended — %d turns, outcome=%s", len(state.transcript), state.outcome)
    if state.transcript:
        print("\n--- Transcript ---")
        for turn in state.transcript:
            role = turn.get("role", "?").upper()
            print(f"  [{role}] {turn.get('content', '')}")
        print("---")


async def main(args: argparse.Namespace) -> None:
    session = build_session(args)
    bridge = LocalBridge()

    pipeline = VoicePipeline(
        twilio_bridge=bridge,  # type: ignore[arg-type]  # duck-typed
        session_state=session,
        on_quote_received=on_quote,
        on_session_end=on_end,
    )

    # Seed market data into DB + Redis so the brain has RAG context
    from app.core.database import init_db
    init_db()

    print(f"\nSession {session.session_id}")
    print(f"  Hotel: {args.hotel_name or session.hotel_target.hotel_id}  |  Location: {args.location}")
    print(f"  Target rate: ${args.target_rate}/night  |  Max: ${args.max_rate}/night")
    print(f"  Room: {args.room}  |  {args.check_in} → {args.check_out}")
    print("\nSpeak into your mic as the hotel representative. Press Ctrl+C to stop.\n")

    try:
        await pipeline.start()
    except KeyboardInterrupt:
        pass
    finally:
        await bridge.stop()
        print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local voice negotiation (no Twilio)")
    parser.add_argument("--target-rate", type=float, default=150.0, help="Target nightly rate (default: 150)")
    parser.add_argument("--max-rate", type=float, default=200.0, help="Max acceptable rate (default: 200)")
    parser.add_argument("--room", default="standard king", help="Room type (default: standard king)")
    parser.add_argument("--check-in", default="2025-06-01", help="Check-in date")
    parser.add_argument("--check-out", default="2025-06-03", help="Check-out date")
    parser.add_argument("--hotel-id", default=None, help="Hotel ID (auto-generated if omitted)")
    parser.add_argument("--hotel-name", default="Hilton Hotels", help="Hotel chain name for market data lookup (default: Hilton Hotels)")
    parser.add_argument("--location", default="The Loop", help="Location for market data lookup (default: The Loop)")
    args = parser.parse_args()
    asyncio.run(main(args))

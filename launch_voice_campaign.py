#!/usr/bin/env python
"""Create a two-target campaign and start outbound Twilio voice workers."""

from __future__ import annotations

import argparse
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv(".env")

from app.core.config import get_settings  # noqa: E402


def _parse_phone_numbers(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


_DEFAULT_HOTELS = [
    {"hotel_name": "Hilton Hotels", "location": "The Loop"},
    {"hotel_name": "Marriott Bonvoy", "location": "River North"},
]


def _build_targets(args: argparse.Namespace, phone_numbers: list[str]) -> list[dict]:
    targets: list[dict] = []
    hotels = args.hotels if args.hotels else _DEFAULT_HOTELS
    for index, phone_number in enumerate(phone_numbers[:2], start=1):
        hotel_info = hotels[index - 1] if index - 1 < len(hotels) else hotels[0]
        hotel_name = hotel_info["hotel_name"]
        location = hotel_info["location"]
        hotel_id = f"{hotel_name.lower().replace(' ', '-')}-{location.lower().replace(' ', '-')}"
        targets.append(
            {
                "hotel_id": hotel_id,
                "phone_number": phone_number,
                "check_in": args.check_in,
                "check_out": args.check_out,
                "room_type": args.room,
                "target_rate": args.target_rate,
                "max_rate": args.max_rate,
                "priority_score": 1.0,
                "market_context": {
                    "hotel_name": hotel_name,
                    "location": location,
                    "market": location,
                },
            }
        )
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a campaign and start two outbound Twilio voice workers.")
    parser.add_argument("--campaign-id", default=f"voice-campaign-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--api-base-url", default="http://localhost:8000", help="FastAPI base URL")
    parser.add_argument("--target-rate", type=float, default=150.0)
    parser.add_argument("--max-rate", type=float, default=200.0)
    parser.add_argument("--room", default="standard king")
    parser.add_argument("--check-in", default="2025-06-01")
    parser.add_argument("--check-out", default="2025-06-03")
    parser.add_argument(
        "--hotels", nargs="*", default=None,
        help="Hotel targets as 'Name:Location' pairs (e.g. 'Hilton Hotels:The Loop' 'Marriott Bonvoy:River North'). "
             "Defaults to Hilton Hotels (The Loop) and Marriott Bonvoy (River North).",
    )
    args = parser.parse_args()

    # Parse --hotels into dicts
    if args.hotels:
        parsed = []
        for h in args.hotels:
            if ":" not in h:
                raise SystemExit(f"Invalid hotel format '{h}'. Use 'Hotel Name:Location'.")
            name, loc = h.split(":", 1)
            parsed.append({"hotel_name": name.strip(), "location": loc.strip()})
        args.hotels = parsed

    settings = get_settings()
    raw_phone_numbers = (
        os.getenv("TWILIO_TO_PHONE_NUMBERS")
        or settings.twilio_to_phone_numbers
        or os.getenv("TWILIO_TO_PHONE_NUMBER")
        or settings.twilio_to_phone_number
    )
    phone_numbers = _parse_phone_numbers(raw_phone_numbers)
    if len(phone_numbers) < 2:
        raise SystemExit(
            "Set TWILIO_TO_PHONE_NUMBERS in .env with at least two comma-separated phone numbers. "
            "The script also accepts a comma-separated TWILIO_TO_PHONE_NUMBER as a fallback."
        )

    targets = _build_targets(args, phone_numbers)
    base_url = args.api_base_url.rstrip("/")

    with httpx.Client(timeout=30.0) as client:
        store_response = client.put(
            f"{base_url}/api/campaigns/{args.campaign_id}/targets",
            json={"targets": targets},
        )
        store_response.raise_for_status()

        start_response = client.post(f"{base_url}/api/campaigns/{args.campaign_id}/start")
        start_response.raise_for_status()

    print(f"Campaign {args.campaign_id} created with {len(targets)} targets.")
    for target in targets:
        print(f"  {target['hotel_id']}: {target['phone_number']}")
    print("Campaign start requested.")
    print(f"Check status with: curl {base_url}/api/campaigns/{args.campaign_id}/status")


if __name__ == "__main__":
    main()

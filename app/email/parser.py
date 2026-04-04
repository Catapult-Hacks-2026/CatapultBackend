from __future__ import annotations

import re

_THREAD_BREAK_PATTERNS = [
    re.compile(r"^On .+wrote:$", re.IGNORECASE),
    re.compile(r"^From:\s", re.IGNORECASE),
    re.compile(r"^Sent:\s", re.IGNORECASE),
    re.compile(r"^>"),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
]
_WHITESPACE = re.compile(r"\n{3,}")


def extract_latest_reply(text: str) -> str:
    if not text:
        return ""

    lines: list[str] = []
    for line in text.splitlines():
        if any(pattern.match(line.strip()) for pattern in _THREAD_BREAK_PATTERNS):
            break
        lines.append(line.rstrip())

    cleaned = "\n".join(lines).strip()
    return _WHITESPACE.sub("\n\n", cleaned)


def contains_booking_language(text: str) -> bool:
    lowered = text.lower()
    keywords = (
        "credit card",
        "card authorization",
        "passport",
        "guest name",
        "confirm the reservation",
        "finalize the booking",
        "billing address",
        "signed contract",
    )
    return any(keyword in lowered for keyword in keywords)


def contains_policy_or_legal_language(text: str) -> bool:
    lowered = text.lower()
    keywords = (
        "terms and conditions",
        "msa",
        "contract",
        "legal",
        "liability",
        "indemn",
        "privacy policy",
    )
    return any(keyword in lowered for keyword in keywords)


def build_default_subject(check_in: str, check_out: str, room_type: str) -> str:
    return f"Hotel rate request for {room_type} stay {check_in} to {check_out}"

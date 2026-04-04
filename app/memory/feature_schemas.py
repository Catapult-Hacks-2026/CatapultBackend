from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class HotelBehavioralFeature(BaseModel):
    hotel_id: str
    feature_type: str   # e.g. "price_flexibility", "authority_limit", "timing"
    feature_key: str    # e.g. "weekday_rate_floor", "front_desk_authority_ceiling"
    value: Any          # str, float, or bool depending on feature_type
    confidence: float = 1.0   # 0-1, decays over time
    source_session_id: str = ""
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decay_rate: float = 0.05  # confidence loss per day without reinforcement


class HotelBehavioralProfile(BaseModel):
    hotel_id: str
    features: list[HotelBehavioralFeature] = Field(default_factory=list)
    last_call_date: datetime | None = None
    total_calls: int = 0
    success_rate: float = 0.0           # fraction of calls ending in rate_confirmed
    avg_negotiated_discount: float = 0.0  # avg % below initial quoted rate
    best_rate: float | None = None
    worst_rate: float | None = None
    common_objections: list[str] = Field(default_factory=list)
    escalation_success_rate: float = 0.0  # rate of success when escalated to manager

    def to_prompt_context(self) -> str:
        """Concise text summary for injection into the negotiation brain prompt."""
        if self.total_calls == 0:
            return "No prior call history for this hotel."

        lines = [
            f"Prior calls: {self.total_calls} (success rate: {self.success_rate:.0%})",
        ]

        if self.best_rate is not None:
            lines.append(f"Best rate ever negotiated: ${self.best_rate:.2f}/night")

        if self.avg_negotiated_discount > 0:
            lines.append(
                f"Average discount achieved: {self.avg_negotiated_discount:.0%} below initial quote"
            )

        if self.escalation_success_rate > 0:
            lines.append(
                f"Escalation to manager success rate: {self.escalation_success_rate:.0%}"
            )

        if self.common_objections:
            lines.append(f"Common objections: {', '.join(self.common_objections[:3])}")

        # Include high-confidence features only
        high_conf = [f for f in self.features if f.confidence >= 0.6]
        for feat in high_conf[:5]:
            lines.append(f"Observed pattern — {feat.feature_key}: {feat.value}")

        return "\n".join(lines)

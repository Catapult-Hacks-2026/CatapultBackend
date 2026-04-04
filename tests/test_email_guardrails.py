import unittest

from app.email.enums import EmailDirection
from app.email.guardrails import validate_email_turn
from app.email.schemas import EmailExtractionResult, EmailMessage, EmailSessionState, EmailTarget
from app.hotel.enums import MoveType
from app.hotel.schemas import AgentMove


class EmailGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = EmailTarget(
            hotel_id="hotel-1",
            email_address="sales@example.com",
            check_in="2026-05-01",
            check_out="2026-05-03",
            room_type="king",
            target_rate=180,
            max_rate=220,
        )
        self.state = EmailSessionState(session_id="session-1", email_target=self.target)

    def test_attachments_require_human_review(self) -> None:
        inbound = EmailMessage(
            provider="mailgun",
            direction=EmailDirection.INBOUND,
            subject="Quote",
            text="See attached contract.",
            from_address="sales@example.com",
            to_address="session-1@reply.example.com",
            attachment_count=1,
        )
        result = validate_email_turn(self.state, inbound, EmailExtractionResult(confidence=0.9), AgentMove(move_type=MoveType.PROBE, response_text=""))
        self.assertFalse(result.allow_auto_send)
        self.assertEqual(result.terminal_outcome, "needs_human")

    def test_booking_confirmation_escalates(self) -> None:
        inbound = EmailMessage(
            provider="mailgun",
            direction=EmailDirection.INBOUND,
            subject="Quote",
            text="We can confirm the reservation once you send guest names.",
            from_address="sales@example.com",
            to_address="session-1@reply.example.com",
        )
        result = validate_email_turn(
            self.state,
            inbound,
            EmailExtractionResult(confidence=0.9, booking_ready=True),
            AgentMove(move_type=MoveType.ACCEPT, response_text=""),
        )
        self.assertFalse(result.allow_auto_send)
        self.assertEqual(result.terminal_outcome, "booking_ready")

    def test_routine_quote_can_auto_send(self) -> None:
        inbound = EmailMessage(
            provider="mailgun",
            direction=EmailDirection.INBOUND,
            subject="Quote",
            text="We can offer $195 including breakfast.",
            from_address="sales@example.com",
            to_address="session-1@reply.example.com",
        )
        result = validate_email_turn(
            self.state,
            inbound,
            EmailExtractionResult(confidence=0.9),
            AgentMove(move_type=MoveType.COUNTER, response_text="Would you consider $185?"),
        )
        self.assertTrue(result.allow_auto_send)


if __name__ == "__main__":
    unittest.main()

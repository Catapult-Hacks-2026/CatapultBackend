import unittest
from unittest.mock import patch

from app.email.schemas import EmailExtractionResult, EmailSessionState, EmailTarget
from app.hotel.enums import MoveType
from app.hotel.schemas import AgentMove, HotelQuote
from app.llm.email_negotiation import generate_email_reply


class EmailNegotiationReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.target = EmailTarget(
            hotel_id="hotel-1",
            email_address="sales@example.com",
            contact_name="Jordan",
            check_in="2026-05-01",
            check_out="2026-05-03",
            room_type="King",
            target_rate=180.0,
            max_rate=220.0,
        )
        self.state = EmailSessionState(
            session_id="session-1",
            email_target=self.target,
            transcript=[{"role": "hotel", "content": "We can offer 195 before taxes."}],
        )

    async def test_generate_email_reply_falls_back_to_counter_message(self) -> None:
        move = AgentMove(move_type=MoveType.COUNTER, response_text="", counter_rate=185.0)
        extraction = EmailExtractionResult(
            quote=HotelQuote(nightly_rate=195.0, total_rate=390.0, rate_type="email_quote")
        )

        with patch("app.llm.email_negotiation.invoke_text", side_effect=RuntimeError("llm unavailable")):
            reply = await generate_email_reply(move, self.state, extraction)

        self.assertIn("Hello Jordan,", reply)
        self.assertIn("$185 per night", reply)
        self.assertIn("2026-05-01 to 2026-05-03", reply)
        self.assertIn("Please let me know if that is workable.", reply)

    async def test_generate_email_reply_falls_back_to_accept_message(self) -> None:
        move = AgentMove(move_type=MoveType.ACCEPT, response_text="")
        extraction = EmailExtractionResult(
            quote=HotelQuote(nightly_rate=180.0, total_rate=360.0, rate_type="email_quote")
        )

        with patch("app.llm.email_negotiation.invoke_text", side_effect=RuntimeError("llm unavailable")):
            reply = await generate_email_reply(move, self.state, extraction)

        self.assertIn("Thank you for confirming the rate.", reply)
        self.assertIn("$180 per night works", reply)
        self.assertIn("Please send over the next non-payment steps", reply)


if __name__ == "__main__":
    unittest.main()

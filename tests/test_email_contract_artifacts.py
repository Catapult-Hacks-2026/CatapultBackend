import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.artifacts.schemas import NegotiatedRateAgreement
from app.email.contract_artifacts import (
    build_contract_artifacts,
    build_receipt_email_body,
    build_receipt_email_subject,
    render_contract_pdf_lines,
    resolve_receipt_recipient,
)
from app.email.schemas import EmailSessionState, EmailTarget
from app.hotel.schemas import HotelQuote


class EmailContractArtifactTests(unittest.IsolatedAsyncioTestCase):
    def test_receipt_email_body_is_human_readable_summary(self) -> None:
        contract = NegotiatedRateAgreement.model_validate({
            "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
            "galileoReferenceId": "GAL-8492-ORD",
            "parties": {"clientName": "Acme Travel", "vendorName": "Ord Hotel"},
            "term": {"startDate": "2026-06-01", "endDate": "2026-06-03"},
            "rateMatrix": [{"roomOrFareType": "Deluxe King", "negotiatedRateUSD": 189, "discountFromBAR": "12%"}],
            "criticalClauses": {
                "inventoryGuarantee": "LRA (Last Room Availability)",
                "blackoutDates": ["None"],
                "cancellationPolicy": "48 hours prior",
            },
            "concessions": ["Breakfast included"],
            "billingAndSettlement": {"method": "Transient - Employee Corporate Card"},
        })

        body = build_receipt_email_body(contract)

        self.assertIn("Hello,", body)
        self.assertIn("After negotiating with the hotel, this is the best deal we have gotten:", body)
        self.assertIn("Rate: $189 USD/night", body)
        self.assertIn("Dates: 2026-06-01 to 2026-06-03", body)
        self.assertIn("If you wish to know more, view details at https://example.com/galileo/reports.", body)
        self.assertTrue(body.endswith("Best,\nGalileo"))
        self.assertEqual(
            build_receipt_email_subject(contract, "rate_confirmed"),
            "Galileo negotiation report | rate_confirmed | GAL-8492-ORD",
        )

    def test_receipt_email_body_uses_report_summary_and_omits_unknown_fields(self) -> None:
        contract = NegotiatedRateAgreement.model_validate({
            "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
            "galileoReferenceId": "GAL-9000-ORD",
            "parties": {"clientName": "Acme Travel", "vendorName": "Ord Hotel"},
            "term": {"startDate": "2026-06-01", "endDate": "2026-06-03"},
            "rateMatrix": [{"roomOrFareType": "King", "negotiatedRateUSD": 205, "discountFromBAR": "N/A"}],
            "criticalClauses": {
                "inventoryGuarantee": "NLRA (Non-Last Room Availability)",
                "blackoutDates": ["None"],
                "cancellationPolicy": "N/A",
            },
            "concessions": ["None"],
            "billingAndSettlement": {"method": "N/A"},
        })
        state = EmailSessionState(
            session_id="session-summary-email",
            email_target=EmailTarget(
                hotel_id="hotel-ord",
                email_address="sales@example.com",
                check_in="2026-06-01",
                check_out="2026-06-03",
                room_type="King",
                target_rate=180.0,
                max_rate=230.0,
            ),
            report_summary="Hotel held firm at $205/night and would not add breakfast, so Galileo recommends passing on this option.",
        )

        body = build_receipt_email_body(contract, session_state=state, outcome="failed")

        self.assertIn("Conversation Summary: Hotel held firm at $205/night and would not add breakfast, so Galileo recommends passing on this option.", body)
        self.assertNotIn("Cancellation Policy:", body)
        self.assertNotIn("Billing Method:", body)
        self.assertNotIn("Concessions:", body)

    def test_pdf_lines_include_pricing_rationale_and_notes(self) -> None:
        contract = NegotiatedRateAgreement.model_validate({
            "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
            "galileoReferenceId": "GAL-8492-ORD",
            "parties": {"clientName": "Acme Travel", "vendorName": "Ord Hotel"},
            "term": {"startDate": "2026-06-01", "endDate": "2026-06-03"},
            "rateMatrix": [{"roomOrFareType": "Deluxe King", "negotiatedRateUSD": 189, "discountFromBAR": "12%"}],
            "criticalClauses": {
                "inventoryGuarantee": "LRA (Last Room Availability)",
                "blackoutDates": ["None"],
                "cancellationPolicy": "48 hours prior",
            },
            "concessions": ["Breakfast included"],
            "billingAndSettlement": {"method": "Transient - Employee Corporate Card"},
        })
        target = EmailTarget(
            hotel_id="hotel-ord",
            email_address="sales@example.com",
            check_in="2026-06-01",
            check_out="2026-06-03",
            room_type="Deluxe King",
            target_rate=180.0,
            max_rate=230.0,
            market_context={"weather": "Warm weather and convention traffic were supporting demand."},
        )
        state = EmailSessionState(
            session_id="session-artifact-test",
            email_target=target,
            transcript=[
                {"role": "agent", "content": "We can confirm if you can do 189 with breakfast."},
                {"role": "hotel", "content": "We can offer 189 USD with Wi-Fi and breakfast included."},
            ],
        )

        lines = render_contract_pdf_lines(contract, state, "rate_confirmed")
        joined = "\n".join(lines)

        self.assertIn("Pricing Rationale", joined)
        self.assertIn("Weather or temperature signal: Warm weather and convention traffic were supporting demand.", joined)
        self.assertIn("Negotiation Notes", joined)
        self.assertIn("Executive Commercial Summary", joined)

    async def test_build_contract_artifacts_uses_summary_when_llm_and_quotes_are_missing(self) -> None:
        target = EmailTarget(
            hotel_id="hotel-summary",
            email_address="sales@example.com",
            check_in="2026-08-01",
            check_out="2026-08-03",
            room_type="Double Queen",
            target_rate=180.0,
            max_rate=240.0,
            campaign_metadata={"client_name": "Acme Travel"},
        )
        state = EmailSessionState(
            session_id="session-summary-fallback",
            email_target=target,
            report_summary="Hotel offered $212 per night with breakfast included and 48 hours prior cancellation.",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.email.contract_artifacts.ARTIFACT_ROOT", Path(temp_dir)):
                with patch("app.email.contract_artifacts.invoke_json", side_effect=RuntimeError("llm unavailable")):
                    artifacts = await build_contract_artifacts(state)

        self.assertEqual(artifacts.contract_json["rateMatrix"][0]["negotiatedRateUSD"], 212.0)
        self.assertEqual(artifacts.contract_json["criticalClauses"]["cancellationPolicy"], "48 hours prior")
        self.assertIn("Breakfast included", artifacts.contract_json["concessions"])

    def test_resolve_receipt_recipient_defaults_to_demo_email(self) -> None:
        state = EmailSessionState(
            session_id="session-summary-fallback",
            email_target=EmailTarget(
                hotel_id="hotel-summary",
                email_address="sales@example.com",
                check_in="2026-08-01",
                check_out="2026-08-03",
                room_type="Double Queen",
                target_rate=180.0,
                max_rate=240.0,
            ),
        )

        self.assertEqual(resolve_receipt_recipient(state), "jeffreytseng07@gmail.com")

    async def test_build_contract_artifacts_writes_json_and_pdf(self) -> None:
        target = EmailTarget(
            hotel_id="hotel-ord",
            email_address="sales@example.com",
            check_in="2026-06-01",
            check_out="2026-06-03",
            room_type="Deluxe King",
            target_rate=180.0,
            max_rate=230.0,
            campaign_metadata={"client_name": "Acme Travel", "receipt_email": "agent@example.com"},
        )
        state = EmailSessionState(
            session_id="session-artifact-test",
            email_target=target,
            transcript=[
                {"role": "agent", "content": "We can confirm if you can do 189 with breakfast."},
                {"role": "hotel", "content": "We can offer 189 USD with Wi-Fi and breakfast included."},
            ],
            quotes_received=[
                HotelQuote(
                    nightly_rate=189.0,
                    total_rate=378.0,
                    inclusions={"wifi": True, "breakfast": True},
                    cancellation_policy="48 hours prior",
                    rate_type="corporate",
                    fees=0.0,
                )
            ],
        )
        llm_response = {
            "galileoReferenceId": "GAL-8492-ORD",
            "parties": {"clientName": "Acme Travel", "vendorName": "Ord Hotel"},
            "term": {"startDate": "2026-06-01", "endDate": "2026-06-03"},
            "rateMatrix": [
                {
                    "roomOrFareType": "Deluxe King",
                    "negotiatedRateUSD": 189,
                    "discountFromBAR": "12%",
                }
            ],
            "criticalClauses": {
                "inventoryGuarantee": "LRA (Last Room Availability)",
                "blackoutDates": ["None"],
                "cancellationPolicy": "48 hours prior",
            },
            "concessions": ["Breakfast included"],
            "billingAndSettlement": {"method": "Transient - Employee Corporate Card"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.email.contract_artifacts.ARTIFACT_ROOT", Path(temp_dir)):
                with patch("app.email.contract_artifacts.invoke_json", return_value=llm_response):
                    artifacts = await build_contract_artifacts(state)

            self.assertEqual(artifacts.contract_json["galileoReferenceId"], "GAL-8492-ORD")
            self.assertTrue(Path(artifacts.json_path).exists())
            self.assertTrue(Path(artifacts.pdf_path).exists())
            self.assertEqual(json.loads(Path(artifacts.json_path).read_text(encoding="utf-8"))["parties"]["clientName"], "Acme Travel")
            self.assertTrue(Path(artifacts.pdf_path).read_bytes().startswith(b"%PDF-1.4"))


if __name__ == "__main__":
    unittest.main()

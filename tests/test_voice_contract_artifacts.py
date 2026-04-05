import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.artifacts.schemas import NegotiatedRateAgreement, ReceiptArtifacts
from app.hotel.enums import NegotiationOutcome
from app.hotel.schemas import HotelTarget, WorkerSessionState
from app.llm.post_call_analyzer import PostCallAnalysis
from app.orchestration.worker_nodes import post_call_node


class VoiceContractArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_call_node_builds_artifacts_for_contract_ready_outcome(self) -> None:
        target = HotelTarget(
            hotel_id="hotel-lax",
            phone_number="+15555550123",
            check_in="2026-07-01",
            check_out="2026-07-03",
            room_type="Executive King",
            target_rate=210.0,
            max_rate=260.0,
            campaign_metadata={"client_name": "Acme Travel", "receipt_email": "agent@example.com"},
        )
        state = WorkerSessionState(
            session_id="voice-session-test",
            hotel_target=target,
            transcript=[
                {"role": "hotel", "content": "We can do 219 and include Wi-Fi."},
                {"role": "agent", "content": "Please confirm the cancellation policy."},
            ],
        )
        contract = NegotiatedRateAgreement.model_validate({
            "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
            "galileoReferenceId": "GAL-1111-LAX",
            "parties": {"clientName": "Acme Travel", "vendorName": "LAX Hotel"},
            "term": {"startDate": "2026-07-01", "endDate": "2026-07-03"},
            "rateMatrix": [{"roomOrFareType": "Executive King", "negotiatedRateUSD": 219, "discountFromBAR": "10%"}],
            "criticalClauses": {
                "inventoryGuarantee": "NLRA (Non-Last Room Availability)",
                "blackoutDates": ["None"],
                "cancellationPolicy": "48 hours prior",
            },
            "concessions": ["Complimentary Wi-Fi"],
            "billingAndSettlement": {"method": "Transient - Employee Corporate Card"},
        })

        analysis = PostCallAnalysis(
            summary="Confirmed rate by phone.",
            outcome=NegotiationOutcome.RATE_CONFIRMED,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "agreement.pdf"
            artifact_path.write_bytes(b"%PDF-1.4\n")
            artifacts = ReceiptArtifacts(
                contract_json=contract.model_dump(),
                json_path=str(Path(temp_dir) / "agreement.json"),
                pdf_path=str(artifact_path),
                email_sent_to="",
            )
            async def build_artifacts(_state: WorkerSessionState) -> ReceiptArtifacts:
                _state.contract_details = contract
                _state.receipt_artifacts = artifacts
                return artifacts

            with patch("app.llm.post_call_analyzer.analyze_call", AsyncMock(return_value=analysis)):
                with patch("app.orchestration.worker_nodes.build_contract_artifacts", AsyncMock(side_effect=build_artifacts)):
                    with patch("app.orchestration.worker_nodes._send_voice_receipt_email", AsyncMock()) as send_receipt:
                        with patch("app.memory.behavioral_store.get_behavioral_store") as store_factory:
                            store_factory.return_value.store_call_summary = AsyncMock()
                            with patch("app.orchestration.worker_nodes.httpx.AsyncClient") as client_factory:
                                client = client_factory.return_value.__aenter__.return_value
                                client.patch = AsyncMock()
                                result = await post_call_node(state)

        self.assertEqual(result["outcome"], NegotiationOutcome.RATE_CONFIRMED)
        self.assertEqual(state.receipt_artifacts.email_sent_to, "agent@example.com")
        send_receipt.assert_awaited_once()

    async def test_post_call_node_sends_report_even_when_rate_is_not_worth_it(self) -> None:
        target = HotelTarget(
            hotel_id="hotel-jfk",
            phone_number="+15555550124",
            check_in="2026-07-10",
            check_out="2026-07-12",
            room_type="Standard King",
            target_rate=180.0,
            max_rate=220.0,
            campaign_metadata={"client_name": "Acme Travel", "receipt_email": "agent@example.com"},
        )
        state = WorkerSessionState(
            session_id="voice-session-failed-test",
            hotel_target=target,
            transcript=[
                {"role": "hotel", "content": "Best available is 349 plus fees."},
                {"role": "agent", "content": "Understood, that is above budget."},
            ],
        )
        contract = NegotiatedRateAgreement.model_validate({
            "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
            "galileoReferenceId": "GAL-2222-JFK",
            "parties": {"clientName": "Acme Travel", "vendorName": "JFK Hotel"},
            "term": {"startDate": "2026-07-10", "endDate": "2026-07-12"},
            "rateMatrix": [{"roomOrFareType": "Standard King", "negotiatedRateUSD": 349, "discountFromBAR": "N/A"}],
            "criticalClauses": {
                "inventoryGuarantee": "NLRA (Non-Last Room Availability)",
                "blackoutDates": ["None"],
                "cancellationPolicy": "N/A",
            },
            "concessions": ["None"],
            "billingAndSettlement": {"method": "N/A"},
        })

        analysis = PostCallAnalysis(
            summary="Rate was too high to pursue.",
            outcome=NegotiationOutcome.FAILED,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = Path(temp_dir) / "agreement.pdf"
            artifact_path.write_bytes(b"%PDF-1.4\n")
            artifacts = ReceiptArtifacts(
                contract_json=contract.model_dump(),
                json_path=str(Path(temp_dir) / "agreement.json"),
                pdf_path=str(artifact_path),
                email_sent_to="",
            )

            async def build_artifacts(_state: WorkerSessionState) -> ReceiptArtifacts:
                _state.contract_details = contract
                _state.receipt_artifacts = artifacts
                return artifacts

            with patch("app.llm.post_call_analyzer.analyze_call", AsyncMock(return_value=analysis)):
                with patch("app.orchestration.worker_nodes.build_contract_artifacts", AsyncMock(side_effect=build_artifacts)):
                    with patch("app.orchestration.worker_nodes._send_voice_receipt_email", AsyncMock()) as send_receipt:
                        with patch("app.memory.behavioral_store.get_behavioral_store") as store_factory:
                            store_factory.return_value.store_call_summary = AsyncMock()
                            with patch("app.orchestration.worker_nodes.httpx.AsyncClient") as client_factory:
                                client = client_factory.return_value.__aenter__.return_value
                                client.patch = AsyncMock()
                                result = await post_call_node(state)

        self.assertEqual(result["outcome"], NegotiationOutcome.FAILED)
        self.assertEqual(state.receipt_artifacts.email_sent_to, "agent@example.com")
        send_receipt.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

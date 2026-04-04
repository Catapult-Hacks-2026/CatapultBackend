import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

if "httpx" not in sys.modules:
    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            self.is_closed = False

        async def aclose(self) -> None:
            self.is_closed = True

    class _FakeLimits:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _FakeTimeout:
        def __init__(self, *args, **kwargs) -> None:
            pass

    sys.modules["httpx"] = SimpleNamespace(
        AsyncClient=_FakeAsyncClient,
        Limits=_FakeLimits,
        Timeout=_FakeTimeout,
    )

if "openai" not in sys.modules:
    class _FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

    sys.modules["openai"] = SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI)

from app.llm import openai_client
from app.services import llm


def _build_negotiation_context(total_utility: float, round_number: int = 2) -> dict:
    return {
        "current_offer": {
            "unit_price": 110.0,
            "shipping_cost": 40.0,
            "payment_terms_days": 45,
            "delivery_days": 12,
        },
        "buyer_config": {
            "target_unit_price": 100.0,
            "max_unit_price": 125.0,
            "max_shipping_cost": 50.0,
            "min_payment_terms": 30,
            "max_delivery_days": 14,
            "min_acceptable_utility": 0.7,
        },
        "scoring_breakdown": {"total_utility": total_utility},
        "round_number": round_number,
    }


class ServiceLlmTests(unittest.TestCase):
    def test_extract_json_handles_wrapped_text(self) -> None:
        payload = llm._extract_json('Result:\n```json\n{"ok": true}\n```')
        self.assertEqual(payload, {"ok": True})

    def test_invoke_json_retries_when_first_response_is_not_valid_json(self) -> None:
        bad_response = SimpleNamespace(content=[SimpleNamespace(text="not json")])
        good_response = SimpleNamespace(content=[SimpleNamespace(text='{"ok": true}')])
        client = MagicMock()
        client.messages.create.side_effect = [bad_response, good_response]

        with (
            patch.object(llm.settings, "anthropic_api_key", "test-key"),
            patch("app.services.llm._client", return_value=client),
        ):
            payload = llm.invoke_json("system", "prompt")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(client.messages.create.call_count, 2)
        retried_prompt = client.messages.create.call_args_list[1].kwargs["messages"][0]["content"]
        self.assertIn("Return valid JSON only", retried_prompt)

    def test_generate_agent_response_uses_fallback_accept_path(self) -> None:
        with patch.object(llm.settings, "anthropic_api_key", ""):
            action = llm.generate_agent_response(_build_negotiation_context(0.8, round_number=3))

        self.assertTrue(action.should_accept)
        self.assertIn("acceptable", action.message.lower())

    def test_generate_agent_response_uses_fallback_counter_path(self) -> None:
        with patch.object(llm.settings, "anthropic_api_key", ""):
            action = llm.generate_agent_response(_build_negotiation_context(0.4, round_number=1))

        self.assertFalse(action.should_accept)
        self.assertEqual(action.counter_offer.unit_price, 103.4)
        self.assertEqual(action.counter_offer.shipping_cost, 40.0)
        self.assertEqual(action.counter_offer.payment_terms_days, 45)
        self.assertEqual(action.counter_offer.delivery_days, 12)

    def test_extract_offer_from_message_uses_fallback_parser(self) -> None:
        with patch.object(llm.settings, "anthropic_api_key", ""):
            offer = llm.extract_offer_from_message(
                "We can do 95.5 per unit, 10 shipping, net 45, delivery in 7 days."
            )

        self.assertEqual(offer.unit_price, 95.5)
        self.assertEqual(offer.shipping_cost, 10.0)
        self.assertEqual(offer.payment_terms_days, 45)
        self.assertEqual(offer.delivery_days, 7)


class OpenAIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_json_parses_completion_content(self) -> None:
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"score": 1}'))]
        )
        create = AsyncMock(return_value=response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.llm.openai_client.get_openai_client", return_value=client):
            payload = await openai_client.invoke_json("system", "user", model="gpt-test")

        self.assertEqual(payload, {"score": 1})
        self.assertEqual(create.await_count, 1)
        self.assertEqual(create.await_args.kwargs["model"], "gpt-test")
        self.assertEqual(create.await_args.kwargs["response_format"], {"type": "json_object"})

    async def test_stream_text_yields_non_empty_deltas(self) -> None:
        async def fake_stream():
            for value in ("Hello", None, " world"):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=value))]
                )

        create = AsyncMock(return_value=fake_stream())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.llm.openai_client.get_openai_client", return_value=client):
            chunks = [chunk async for chunk in openai_client.stream_text("system", "user")]

        self.assertEqual(chunks, ["Hello", " world"])
        self.assertTrue(create.await_args.kwargs["stream"])


if __name__ == "__main__":
    unittest.main()

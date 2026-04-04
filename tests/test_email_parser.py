import unittest

from app.email.parser import (
    build_default_subject,
    contains_booking_language,
    contains_policy_or_legal_language,
    extract_latest_reply,
)


class EmailParserTests(unittest.TestCase):
    def test_extract_latest_reply_removes_quoted_history(self) -> None:
        text = (
            "Thanks for reaching out. We can offer $189 per night.\n\n"
            "On Tue, someone wrote:\n"
            "> Older quoted content\n"
        )
        self.assertEqual(
            extract_latest_reply(text),
            "Thanks for reaching out. We can offer $189 per night.",
        )

    def test_booking_language_detection(self) -> None:
        self.assertTrue(contains_booking_language("Please confirm the reservation and send the guest name."))
        self.assertFalse(contains_booking_language("We can discuss rate flexibility next week."))

    def test_policy_language_detection(self) -> None:
        self.assertTrue(contains_policy_or_legal_language("Please review our contract and liability terms."))
        self.assertFalse(contains_policy_or_legal_language("Breakfast and wifi are included."))

    def test_default_subject_shape(self) -> None:
        self.assertIn("2026-05-01", build_default_subject("2026-05-01", "2026-05-03", "king"))


if __name__ == "__main__":
    unittest.main()

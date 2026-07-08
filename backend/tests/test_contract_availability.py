import unittest
from datetime import date

from app.services.contract_availability import assess_contract_availability, parse_deadline_date


class ContractAvailabilityTests(unittest.TestCase):
    def test_parses_human_deadline(self):
        self.assertEqual(parse_deadline_date("Deadline: 15 July 2026 at noon"), date(2026, 7, 15))

    def test_future_deadline_is_available(self):
        result = assess_contract_availability(deadline="2026-07-15", today=date(2026, 7, 8))

        self.assertEqual(result.status, "Available")
        self.assertEqual(result.deadline_date, date(2026, 7, 15))

    def test_past_deadline_is_unavailable(self):
        result = assess_contract_availability(deadline="2026-07-01", today=date(2026, 7, 8))

        self.assertEqual(result.status, "Unavailable")
        self.assertIn("Deadline passed", result.reason)


if __name__ == "__main__":
    unittest.main()

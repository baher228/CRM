import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.confirmation import Confirmation


class ConfirmationGateTests(unittest.TestCase):
    def test_explicit_confirmation_header_is_required(self):
        app = FastAPI()

        @app.post("/financial-action")
        def financial_action(_confirmation: None = Confirmation):
            return {"accepted": True}

        client = TestClient(app)
        missing = client.post("/financial-action")
        self.assertEqual(428, missing.status_code)
        accepted = client.post(
            "/financial-action", headers={"X-CRM-Confirmed": "true"}
        )
        self.assertEqual(200, accepted.status_code)
        self.assertTrue(accepted.json()["accepted"])


if __name__ == "__main__":
    unittest.main()

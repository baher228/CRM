from __future__ import annotations

import os
import time
import unittest

from app.integrations_v1.google import GoogleWorkspaceAdapter
from app.integrations_v1.secrets import application_credential_store
from app.integrations_v1.stripe import StripeAdapter


LIVE = os.getenv("CRM_LIVE_SMOKE", "").strip().lower() in {"1", "true", "yes"}


@unittest.skipUnless(LIVE, "Set CRM_LIVE_SMOKE=1 to run operator-managed provider smoke tests")
class LiveProviderSmokeTests(unittest.TestCase):
    def test_google_can_list_one_thread_without_writing(self) -> None:
        credentials = application_credential_store(fake=False)
        google = GoogleWorkspaceAdapter(
            client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI", ""),
            credentials=credentials,
            fake=False,
        )
        self.assertTrue(google.connected(), "Connect the isolated Google test account first")
        response = google.list_gmail_threads(max_results=1, days=1)
        self.assertIsInstance(response.get("threads", []), list)

    def test_stripe_can_list_recent_paid_test_sessions_without_writing(self) -> None:
        credentials = application_credential_store(fake=False)
        stripe = StripeAdapter(credentials=credentials, fake=False)
        self.assertTrue(stripe.configured(), "Save an isolated Stripe test key first")
        payments = stripe.list_paid(created_after_epoch=int(time.time()) - 86_400)
        self.assertIsInstance(payments, list)


if __name__ == "__main__":
    unittest.main()

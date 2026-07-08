import unittest

from fastapi.testclient import TestClient

from app.main import app


class DiscoveryPortalTests(unittest.TestCase):
    def test_portal_metadata_endpoint_returns_backend_registry(self):
        response = TestClient(app).get("/api/discovery/portals", params={"niche": "nhs facilities", "region": "London"})

        self.assertEqual(response.status_code, 200)
        portals = response.json()
        self.assertGreater(len(portals), 0)
        self.assertTrue({"name", "domains", "default_selected", "priority", "label"} <= set(portals[0]))
        self.assertIn("Find a Tender Service", {portal["name"] for portal in portals})


if __name__ == "__main__":
    unittest.main()

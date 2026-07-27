from __future__ import annotations

import unittest

from app.integrations_v1.secrets import CredentialStore, MemoryCredentialBackend, TAVILY_API_KEY
from app.lead_discovery.models import DiscoveryCompanyResult, ExtractedCompanyPage
from app.lead_discovery.parse_company_profile import parseCompanyProfile
from app.lead_enrichment.classify_lead import classifyLead
from app.lead_enrichment.config import EnrichmentSettings
from app.lead_enrichment.models import ExtractedPage, LeadSource


class FailingGemini:
    async def generate_json(self, *_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    async def classify(self, *_args, **_kwargs):
        raise RuntimeError("provider unavailable")


class IntelligenceFallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_live_progress_accepts_the_saving_phase(self) -> None:
        result = DiscoveryCompanyResult(domain="contracts.example", status="saving", message="Saving")
        self.assertEqual("saving", result.status)

    async def test_tavily_is_required_but_gemini_is_optional(self) -> None:
        credentials = CredentialStore(
            "fallback-test",
            backend=MemoryCredentialBackend({("fallback-test", TAVILY_API_KEY): "tvly-valid-for-test"}),
        )
        settings = EnrichmentSettings(_credential_store=credentials)
        settings.require_discovery_keys()
        self.assertFalse(settings.gemini_configured)

    async def test_discovery_falls_back_to_public_evidence(self) -> None:
        profile = await parseCompanyProfile(
            "digital transformation",
            "London",
            "contracts.example",
            [ExtractedCompanyPage(
                url="https://contracts.example/notice/42",
                domain="contracts.example",
                page_type="contract_notice",
                title="Council digital transformation programme",
                content="Buyer: North Borough Council\nContract value: £250,000\nDeadline: 30 July 2026\nThis tender is open for supplier submissions.",
            )],
            FailingGemini(),
        )
        self.assertEqual("North Borough Council", profile.buyer_name)
        self.assertEqual("£250,000", profile.contract_value)
        self.assertEqual("Open", profile.contract_status)
        self.assertEqual(45, profile.confidence_score)

    async def test_enrichment_falls_back_when_gemini_rejects_the_key(self) -> None:
        classification = await classifyLead(
            LeadSource(object_slug="opportunity", record_id="1", name="Council programme"),
            [ExtractedPage(
                url="https://example.test/notice",
                title="Open framework",
                content="Procurement framework with a submission deadline for suppliers.",
            )],
            FailingGemini(),
        )
        self.assertIn("procurement", classification.procurement_signals)
        self.assertGreater(classification.confidence_score, 0)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.lead_discovery.models import DiscoveryCompanyResult
from app.services.lead_discovery_mapper import result_key, result_to_lead
from app.services.lead_sources import canonical_url_key, clean_source_urls


class LeadDedupeTests(unittest.TestCase):
    def test_contracts_finder_search_urls_are_not_keys(self):
        self.assertEqual(canonical_url_key("https://www.contractsfinder.service.gov.uk/search/results"), "")

    def test_notice_ocds_id_becomes_stable_key(self):
        key = canonical_url_key("https://www.contractsfinder.service.gov.uk/notice/ocds-abc-123?utm=1")

        self.assertEqual(key, "contractsfinder.service.gov.uk|ocds-abc-123")

    def test_discovery_result_uses_best_notice_url_after_extraction(self):
        result = DiscoveryCompanyResult(
            domain="contractsfinder.service.gov.uk",
            company_name="Example Buyer",
            status="dry_run",
            message="parsed",
            contract_title="Repairs framework",
            buyer_name="Example Council",
            portal_name="Contracts Finder",
            contract_url="https://www.contractsfinder.service.gov.uk/search/results",
            source_urls=[
                "https://www.contractsfinder.service.gov.uk/search/results",
                "https://www.contractsfinder.service.gov.uk/notice/ocds-abc-123",
            ],
            deadline="2099-01-01",
        )

        lead = result_to_lead(result, 42)

        self.assertEqual(result_key(result), "contractsfinder.service.gov.uk|ocds-abc-123")
        self.assertEqual(lead.contract_url, "https://contractsfinder.service.gov.uk/notice/ocds-abc-123")
        self.assertEqual(clean_source_urls(result.source_urls), [lead.contract_url])


if __name__ == "__main__":
    unittest.main()

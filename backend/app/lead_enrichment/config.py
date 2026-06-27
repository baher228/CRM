from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnrichmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    attio_api_token: str = ""
    attio_base_url: str = "https://api.attio.com"
    attio_lead_list_id: str = ""
    attio_lead_object: str = "companies"
    attio_company_object: str = "companies"
    attio_person_object: str = "people"

    attio_company_name_attribute: str = "name"
    attio_company_domain_attribute: str = "domains"
    attio_person_email_attribute: str = "email_addresses"
    attio_enrichment_summary_attribute: str = "lead_enrichment_summary"
    attio_fit_score_attribute: str = "lead_fit_score"
    attio_urgency_score_attribute: str = "lead_urgency_score"
    attio_confidence_score_attribute: str = "lead_enrichment_confidence"
    attio_fingerprint_attribute: str = "lead_enrichment_fingerprint"
    attio_source_urls_attribute: str = "lead_enrichment_source_urls"
    attio_enriched_at_attribute: str = "lead_enriched_at"

    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    tavily_max_search_results: int = 8
    tavily_max_extract_urls: int = 8
    tavily_search_country: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    @field_validator("gemini_model")
    @classmethod
    def normalize_frontier_gemini_model(cls, value: str) -> str:
        if _is_placeholder(value) or value.strip() == "gemini-2.5-flash":
            return "gemini-3.5-flash"
        return value.strip()

    enrichment_classifier_version: str = "gemini-v1"
    enrichment_timeout_seconds: float = 30.0
    enrichment_max_retries: int = 3
    enrichment_attio_concurrency: int = 2
    enrichment_tavily_concurrency: int = 3
    enrichment_llm_concurrency: int = 2
    enrichment_create_tasks: bool = True
    enrichment_task_urgency_threshold: int = 75
    enrichment_default_limit: int = Field(default=10, ge=1, le=100)

    discovery_parser_version: str = "gemini-discovery-v1"
    discovery_default_limit: int = Field(default=10, ge=1, le=100)
    discovery_max_search_results: int = Field(default=8, ge=1, le=20)
    discovery_max_extract_urls: int = Field(default=8, ge=1, le=12)
    discovery_extract_mode: str = "mixed"
    discovery_search_depth: str = "advanced"
    discovery_basic_extract_depth: str = "basic"
    discovery_advanced_extract_depth: str = "advanced"
    discovery_company_object: str = "companies"
    discovery_domain_matching_attribute: str = "domains"
    attio_discovery_write_custom_attributes: bool = False
    attio_discovery_summary_attribute: str = ""
    attio_discovery_confidence_attribute: str = ""
    attio_discovery_source_urls_attribute: str = ""
    attio_discovery_fingerprint_attribute: str = ""
    attio_discovery_niche_attribute: str = ""
    attio_discovery_region_attribute: str = ""

    def require_read_keys(self) -> None:
        missing = []
        if _is_placeholder(self.attio_api_token):
            missing.append("ATTIO_API_TOKEN")
        if _is_placeholder(self.tavily_api_key):
            missing.append("TAVILY_API_KEY")
        if _is_placeholder(self.gemini_api_key):
            missing.append("GEMINI_API_KEY")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    def require_discovery_keys(self) -> None:
        missing = []
        if _is_placeholder(self.attio_api_token):
            missing.append("ATTIO_API_TOKEN")
        if _is_placeholder(self.tavily_api_key):
            missing.append("TAVILY_API_KEY")
        if _is_placeholder(self.gemini_api_key):
            missing.append("GEMINI_API_KEY")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or normalized.startswith("your_") or normalized in {"changeme", "todo"}

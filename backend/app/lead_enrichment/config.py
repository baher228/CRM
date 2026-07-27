from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.integrations_v1.secrets import (
    GEMINI_API_KEY,
    TAVILY_API_KEY,
    CredentialStore,
    CredentialStoreUnavailable,
    application_credential_store,
)


class EnrichmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    tavily_max_search_results: int = 8
    tavily_max_extract_urls: int = 8
    tavily_search_country: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    def __init__(self, **values) -> None:
        credential_store = values.pop("_credential_store", None)
        # Provider secrets are never accepted from process or dotenv
        # configuration; Windows Credential Manager is authoritative.
        values.pop("tavily_api_key", None)
        values.pop("gemini_api_key", None)
        super().__init__(**values)
        self.tavily_api_key = ""
        self.gemini_api_key = ""
        self._load_credentials(credential_store or application_credential_store())

    @field_validator("gemini_model")
    @classmethod
    def normalize_frontier_gemini_model(cls, value: str) -> str:
        if _is_placeholder(value) or value.strip() == "gemini-2.5-flash":
            return "gemini-3.5-flash"
        return value.strip()

    enrichment_classifier_version: str = "gemini-v1"
    enrichment_timeout_seconds: float = 30.0
    enrichment_max_retries: int = 3
    enrichment_local_concurrency: int = 2
    enrichment_tavily_concurrency: int = 3
    enrichment_llm_concurrency: int = 2
    enrichment_create_tasks: bool = True
    enrichment_task_urgency_threshold: int = 75
    enrichment_default_limit: int = Field(default=10, ge=1, le=100)
    crm_include_demo_leads: bool = False

    discovery_parser_version: str = "gemini-discovery-v1"
    discovery_default_limit: int = Field(default=10, ge=1, le=100)
    discovery_max_search_results: int = Field(default=8, ge=1, le=20)
    discovery_max_extract_urls: int = Field(default=8, ge=1, le=12)
    discovery_extract_mode: str = "mixed"
    discovery_search_depth: str = "advanced"
    discovery_basic_extract_depth: str = "basic"
    discovery_advanced_extract_depth: str = "advanced"

    def require_read_keys(self) -> None:
        self._require_tavily_key()

    def require_discovery_keys(self) -> None:
        self._require_tavily_key()

    def _require_tavily_key(self) -> None:
        if _is_placeholder(self.tavily_api_key):
            raise ValueError("Missing required credential: TAVILY_API_KEY")

    @property
    def gemini_configured(self) -> bool:
        return not _is_placeholder(self.gemini_api_key)

    def _load_credentials(self, store: CredentialStore) -> None:
        try:
            tavily = store.get(TAVILY_API_KEY)
            gemini = store.get(GEMINI_API_KEY)
        except CredentialStoreUnavailable:
            return
        if tavily:
            self.tavily_api_key = tavily
        if gemini:
            self.gemini_api_key = gemini


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or normalized.startswith("your_") or normalized in {"changeme", "todo"}

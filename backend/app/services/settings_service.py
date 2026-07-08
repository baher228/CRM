import os

from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import IntegrationStatus, SettingsHealthResponse
from app.services import emails_service
from app.services.crm_store import db_path


def get_settings_health() -> SettingsHealthResponse:
    settings = EnrichmentSettings()
    return SettingsHealthResponse(
        database_path=str(db_path()),
        daybreak_enabled=_truthy(os.getenv("VITE_ENABLE_DAYBREAK")),
        integrations=[
            _status("Attio", settings.attio_api_token, "CRM sync and task writeback"),
            _status("Tavily", settings.tavily_api_key, "Lead discovery and contact lookup"),
            _status("Gemini", settings.gemini_api_key, "Lead parsing and email drafting"),
            _status("Mail", "configured" if emails_service.get_mail_settings().configured else "", "IMAP inbox for the Emails tab"),
            _status("n8n", os.getenv("N8N_WEBHOOK_URL", ""), "Optional external scheduling webhook"),
        ],
    )


def _status(name: str, value: str, detail: str) -> IntegrationStatus:
    configured = bool(value and value.strip() and not value.strip().lower().startswith("your_"))
    return IntegrationStatus(name=name, configured=configured, detail=detail if configured else f"{detail} not configured")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


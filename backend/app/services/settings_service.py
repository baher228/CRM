from app import platform_db
from app.integrations_v1.state import IntegrationStateStore
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import IntegrationStatus, SettingsHealthResponse


def get_settings_health() -> SettingsHealthResponse:
    settings = EnrichmentSettings()
    state = IntegrationStateStore()
    google = state.get_connection("google")
    stripe = state.get_connection("stripe")
    return SettingsHealthResponse(
        database_path=str(platform_db.db_path()),
        integrations=[
            _connection_status("Google Workspace", google, "Gmail, Calendar, Drive and Docs"),
            _connection_status("Stripe", stripe, "Invoice payment collection and reconciliation"),
            _status("Tavily", settings.tavily_api_key, "Tender discovery and contact research"),
            _status("Gemini", settings.gemini_api_key, "Tender parsing, summaries and drafts"),
        ],
    )


def _connection_status(name, connection, detail: str) -> IntegrationStatus:
    configured = bool(connection and connection.status == "connected")
    state_detail = detail if configured else f"{detail} not connected"
    if connection and connection.last_error:
        state_detail = connection.last_error
    return IntegrationStatus(name=name, configured=configured, detail=state_detail)


def _status(name: str, value: str, detail: str) -> IntegrationStatus:
    configured = bool(value and value.strip() and not value.strip().lower().startswith("your_"))
    return IntegrationStatus(name=name, configured=configured, detail=detail if configured else f"{detail} not configured")

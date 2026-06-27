from app.schemas import HealthResponse


def get_health() -> HealthResponse:
    return HealthResponse(status="ok", service="crm-scaffold-api")


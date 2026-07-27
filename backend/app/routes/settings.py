from fastapi import APIRouter

from app.schemas import SettingsHealthResponse
from app.services import settings_service


router = APIRouter()


@router.get("/settings/health", response_model=SettingsHealthResponse)
def get_settings_health() -> SettingsHealthResponse:
    return settings_service.get_settings_health()

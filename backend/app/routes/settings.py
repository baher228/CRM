from fastapi import APIRouter, HTTPException

from app.schemas import MailSettingsRequest, MailSettingsResponse, SettingsHealthResponse
from app.services import emails_service, settings_service


router = APIRouter()


@router.get("/settings/health", response_model=SettingsHealthResponse)
def get_settings_health() -> SettingsHealthResponse:
    return settings_service.get_settings_health()


@router.get("/settings/mail", response_model=MailSettingsResponse)
def get_mail_settings() -> MailSettingsResponse:
    return emails_service.get_mail_settings()


@router.post("/settings/mail", response_model=MailSettingsResponse)
def save_mail_settings(request: MailSettingsRequest) -> MailSettingsResponse:
    try:
        return emails_service.save_mail_settings(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

from fastapi import APIRouter, HTTPException

from app.schemas import EmailMessage
from app.services import emails_service


router = APIRouter()


@router.get("/emails", response_model=list[EmailMessage])
def get_emails(limit: int = 25) -> list[EmailMessage]:
    try:
        return emails_service.list_emails(limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

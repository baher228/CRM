from fastapi import APIRouter

from app.schemas import EmailMessage
from app.services import emails_service


router = APIRouter()


@router.get("/emails", response_model=list[EmailMessage])
def get_emails() -> list[EmailMessage]:
    return emails_service.list_emails()


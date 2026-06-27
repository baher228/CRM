from app.data import EMAILS
from app.schemas import EmailMessage


def list_emails() -> list[EmailMessage]:
    return EMAILS


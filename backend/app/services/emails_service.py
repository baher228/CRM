from __future__ import annotations

import email
import imaplib
import os
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from app.schemas import EmailMessage, MailSettingsRequest, MailSettingsResponse
from app.schemas import Priority
from app.services import crm_store


def list_emails(limit: int = 25) -> list[EmailMessage]:
    settings = get_mail_settings()
    password = _setting("password")
    if not settings.host or not settings.username or not password:
        raise RuntimeError("Mail IMAP is not configured. Set MAIL_IMAP_HOST, MAIL_IMAP_USERNAME, and MAIL_IMAP_PASSWORD.")

    try:
        connection_cls = imaplib.IMAP4_SSL if settings.use_ssl else imaplib.IMAP4
        with connection_cls(settings.host, settings.port) as mailbox:
            mailbox.login(settings.username, password)
            mailbox.select(settings.folder, readonly=True)
            status, data = mailbox.search(None, "ALL")
            if status != "OK" or not data:
                return []

            ids = data[0].split()[-limit:]
            messages: list[EmailMessage] = []
            for index, message_id in enumerate(reversed(ids), start=1):
                status, fetched = mailbox.fetch(message_id, "(FLAGS BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = next((part[1] for part in fetched if isinstance(part, tuple)), b"")
                flags = next((part.decode(errors="ignore") for part in fetched if isinstance(part, bytes)), "")
                parsed = email.message_from_bytes(raw)
                messages.append(_to_email_message(index, parsed, "\\Seen" not in flags))
            return messages
    except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
        raise RuntimeError(f"Could not load mailbox: {_error_message(exc)}") from exc


def get_mail_settings() -> MailSettingsResponse:
    password = _setting("password")
    host = _setting("host")
    username = _setting("username")
    return MailSettingsResponse(
        host=host,
        port=int(_setting("port", "993") or "993"),
        username=username,
        folder=_setting("folder", "INBOX") or "INBOX",
        use_ssl=_truthy(_setting("use_ssl", "true")),
        configured=bool(host and username and password),
        password_saved=bool(password),
    )


def save_mail_settings(request: MailSettingsRequest) -> MailSettingsResponse:
    values = {
        "mail.host": request.host.strip(),
        "mail.port": str(request.port),
        "mail.username": request.username.strip(),
        "mail.folder": request.folder.strip() or "INBOX",
        "mail.use_ssl": "true" if request.use_ssl else "false",
    }
    if request.password:
        values["mail.password"] = request.password
    crm_store.save_settings(values)
    settings = get_mail_settings()
    if not settings.configured:
        raise RuntimeError("Password is required the first time you save mail settings.")
    return settings


def _to_email_message(message_id: int, message: Message, unread: bool) -> EmailMessage:
    from_name, from_email = parseaddr(_header(message.get("From", "")))
    received_at = _message_date(message.get("Date", ""))
    subject = _header(message.get("Subject", "")) or "(No subject)"
    preview = _preview(message)
    return EmailMessage(
        id=message_id,
        subject=subject,
        from_name=from_name or from_email or "Unknown",
        from_email=from_email or "unknown@example.com",
        preview=preview,
        received_at=received_at,
        unread=unread,
        priority=Priority.HIGH if _looks_important(subject, preview) else Priority.MEDIUM,
    )


def _header(value: str) -> str:
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def _message_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _preview(message: Message) -> str:
    body = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart" or part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                body = _decode_payload(part)
                break
    else:
        body = _decode_payload(message)
    return " ".join(body.split())[:220]


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _looks_important(subject: str, preview: str) -> bool:
    text = f"{subject} {preview}".lower()
    return any(word in text for word in ("urgent", "deadline", "proposal", "contract", "invoice"))


def _setting(name: str, default: str = "") -> str:
    env_name = f"MAIL_IMAP_{name.upper()}"
    if name == "use_ssl":
        env_name = "MAIL_IMAP_USE_SSL"
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    return crm_store.get_setting(f"mail.{name}") or default


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() not in {"0", "false", "no", "off"}


def _error_message(exc: Exception) -> str:
    if exc.args and isinstance(exc.args[0], bytes):
        return exc.args[0].decode(errors="replace")
    return str(exc)

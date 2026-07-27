from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from typing import Any, MutableMapping

from .secrets import CredentialStore


class StripeApiUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StripePaymentState:
    remote_id: str
    invoice_id: str
    amount_minor: int
    currency: str
    url: str
    status: str
    payment_status: str
    idempotency_key: str

    @property
    def paid(self) -> bool:
        return self.payment_status == "paid"


class StripeAdapter:
    """Checkout-session collection while the local CRM remains invoice authority."""

    API_KEY = "stripe.api_key"

    def __init__(
        self,
        *,
        credentials: CredentialStore | None = None,
        fake: bool = False,
        fake_state: MutableMapping[str, Any] | None = None,
        success_url: str = "http://127.0.0.1/payment-complete?session_id={CHECKOUT_SESSION_ID}",
        cancel_url: str = "http://127.0.0.1/payment-cancelled",
    ) -> None:
        self.credentials = credentials or CredentialStore()
        self.fake = fake
        self.success_url, self.cancel_url = success_url, cancel_url
        self._fake = fake_state if fake_state is not None else {}
        self._fake.setdefault("sessions", {})
        self._fake.setdefault("idempotency", {})

    def save_api_key(self, api_key: str) -> None:
        self.credentials.set(self.API_KEY, api_key)

    def configured(self) -> bool:
        return self.credentials.has(self.API_KEY)

    def create_payment_link(
        self,
        *,
        invoice_id: str | int,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
        description: str = "",
        customer_email: str | None = None,
    ) -> StripePaymentState:
        invoice_id, currency = str(invoice_id), currency.lower()
        if not invoice_id.strip() or amount_minor <= 0:
            raise ValueError("invoice_id and a positive outstanding amount are required")
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        if not idempotency_key.strip() or len(idempotency_key) > 255:
            raise ValueError("Stripe idempotency key must be between 1 and 255 characters")
        if self.fake:
            previous_id = self._fake["idempotency"].get(idempotency_key)
            if previous_id:
                return self._state(self._fake["sessions"][previous_id])
            remote_id = f"cs_test_{uuid.uuid4().hex}"
            session = {
                "id": remote_id,
                "url": f"https://checkout.stripe.test/{remote_id}",
                "status": "open",
                "payment_status": "unpaid",
                "amount_total": amount_minor,
                "currency": currency,
                "metadata": {
                    "crm_invoice_id": invoice_id,
                    "crm_idempotency_key": idempotency_key,
                },
            }
            self._fake["sessions"][remote_id] = session
            self._fake["idempotency"][idempotency_key] = remote_id
            return self._state(session)
        stripe = self._stripe()
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": currency,
                        "unit_amount": amount_minor,
                        "product_data": {"name": description or f"Invoice {invoice_id}"},
                    },
                }
            ],
            "metadata": {
                "crm_invoice_id": invoice_id,
                "crm_idempotency_key": idempotency_key,
            },
            "payment_intent_data": {"metadata": {"crm_invoice_id": invoice_id}},
            "success_url": self.success_url,
            "cancel_url": self.cancel_url,
            "invoice_creation": {"enabled": False},
            "automatic_tax": {"enabled": False},
            "idempotency_key": idempotency_key,
            "api_key": self._api_key(),
        }
        if customer_email:
            params["customer_email"] = customer_email
        return self._state(stripe.checkout.Session.create(**params))

    def reconcile_payment(
        self,
        *,
        invoice_id: str | int,
        remote_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> StripePaymentState | None:
        invoice_id = str(invoice_id)
        if self.fake:
            if remote_id:
                session = self._fake["sessions"].get(remote_id)
                return self._state(session) if session else None
            if idempotency_key and idempotency_key in self._fake["idempotency"]:
                return self._state(
                    self._fake["sessions"][self._fake["idempotency"][idempotency_key]]
                )
            for session in self._fake["sessions"].values():
                if session["metadata"].get("crm_invoice_id") == invoice_id:
                    return self._state(session)
            return None
        stripe = self._stripe()
        if remote_id:
            return self._state(
                stripe.checkout.Session.retrieve(remote_id, api_key=self._api_key())
            )
        sessions = stripe.checkout.Session.list(limit=100, api_key=self._api_key())
        for session in sessions.auto_paging_iter():
            metadata = dict(session.get("metadata") or {})
            if metadata.get("crm_invoice_id") == invoice_id and (
                not idempotency_key or metadata.get("crm_idempotency_key") == idempotency_key
            ):
                return self._state(session)
        return None

    def list_paid(self, *, created_after_epoch: int | None = None) -> list[StripePaymentState]:
        if self.fake:
            return [
                self._state(session)
                for session in self._fake["sessions"].values()
                if session.get("payment_status") == "paid"
            ]
        kwargs: dict[str, Any] = {"limit": 100, "api_key": self._api_key()}
        if created_after_epoch is not None:
            kwargs["created"] = {"gte": created_after_epoch}
        sessions = self._stripe().checkout.Session.list(**kwargs)
        return [
            self._state(session)
            for session in sessions.auto_paging_iter()
            if session.get("payment_status") == "paid"
        ]

    def mark_fake_paid(self, remote_id: str) -> StripePaymentState:
        if not self.fake:
            raise RuntimeError("mark_fake_paid is only available in fake mode")
        session = self._fake["sessions"].get(remote_id)
        if session is None:
            raise KeyError(remote_id)
        session.update(status="complete", payment_status="paid")
        return self._state(session)

    def _api_key(self) -> str:
        key = self.credentials.get(self.API_KEY)
        if not key:
            raise ValueError("Stripe API key is not configured")
        return key

    @staticmethod
    def _state(session: Any) -> StripePaymentState:
        metadata = dict(session.get("metadata") or {})
        return StripePaymentState(
            remote_id=str(session["id"]),
            invoice_id=str(metadata.get("crm_invoice_id") or ""),
            amount_minor=int(session.get("amount_total") or 0),
            currency=str(session.get("currency") or "").lower(),
            url=str(session.get("url") or ""),
            status=str(session.get("status") or "unknown"),
            payment_status=str(session.get("payment_status") or "unpaid"),
            idempotency_key=str(metadata.get("crm_idempotency_key") or ""),
        )

    @staticmethod
    def _stripe() -> Any:
        try:
            return importlib.import_module("stripe")
        except ImportError as exc:
            raise StripeApiUnavailable("Install stripe to enable payment collection") from exc

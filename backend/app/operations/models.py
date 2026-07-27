from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ProjectStatus = Literal["Planned", "Active", "Blocked", "Complete", "Cancelled"]
ProposalStatus = Literal["Draft", "Sent", "Accepted", "Rejected", "Expired", "Void"]
ContractStatus = Literal["Draft", "Sent", "Signed", "Active", "Expired", "Terminated"]
InvoiceStatus = Literal["Draft", "Sent", "Part-paid", "Paid", "Overdue", "Void"]
HealthStatus = Literal["Healthy", "Watch", "At risk"]
BillingType = Literal["fixed", "milestone", "hourly", "retainer"]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CommercialLineInput(InputModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=4)
    unit_price_pence: int = Field(ge=0)
    tax_rate_bps: int = Field(default=0, ge=0, le=10_000)
    discount_bps: int = Field(default=0, ge=0, le=10_000)
    catalog_item_id: int | None = None


class CatalogItemCreate(InputModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    unit: str = Field(default="item", max_length=50)
    unit_price_pence: int = Field(default=0, ge=0)
    tax_rate_bps: int = Field(default=0, ge=0, le=10_000)
    active: bool = True


class CatalogItemUpdate(InputModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=50)
    unit_price_pence: int | None = Field(default=None, ge=0)
    tax_rate_bps: int | None = Field(default=None, ge=0, le=10_000)
    active: bool | None = None


class ProjectCreate(InputModel):
    name: str = Field(min_length=1, max_length=200)
    account_id: int | None = None
    opportunity_id: int | None = None
    contract_id: int | None = None
    status: ProjectStatus = "Planned"
    billing_type: BillingType = "fixed"
    budget_pence: int = Field(default=0, ge=0)
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    starts_on: date | None = None
    due_on: date | None = None
    notes: str = ""

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.starts_on and self.due_on and self.due_on < self.starts_on:
            raise ValueError("due_on must be on or after starts_on")
        return self


class ProjectUpdate(InputModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: ProjectStatus | None = None
    billing_type: BillingType | None = None
    budget_pence: int | None = Field(default=None, ge=0)
    starts_on: date | None = None
    due_on: date | None = None
    notes: str | None = None


class MilestoneCreate(InputModel):
    title: str = Field(min_length=1, max_length=200)
    due_on: date | None = None
    amount_pence: int = Field(default=0, ge=0)
    status: Literal["Planned", "In progress", "Complete", "Cancelled"] = "Planned"
    sort_order: int = 0


class MilestoneUpdate(InputModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_on: date | None = None
    amount_pence: int | None = Field(default=None, ge=0)
    status: Literal["Planned", "In progress", "Complete", "Cancelled"] | None = None
    sort_order: int | None = None


class TimeEntryCreate(InputModel):
    project_id: int
    entry_date: date
    minutes: int = Field(gt=0, le=24 * 60)
    description: str = Field(default="", max_length=500)
    billable: bool = True
    hourly_rate_pence: int = Field(default=0, ge=0)


class TimeEntryUpdate(InputModel):
    version: int = Field(ge=1)
    entry_date: date | None = None
    minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    description: str | None = Field(default=None, max_length=500)
    billable: bool | None = None
    hourly_rate_pence: int | None = Field(default=None, ge=0)


class ExpenseCreate(InputModel):
    project_id: int | None = None
    account_id: int | None = None
    expense_date: date
    vendor: str = Field(default="", max_length=200)
    description: str = Field(min_length=1, max_length=500)
    net_pence: int = Field(ge=0)
    tax_rate_bps: int = Field(default=0, ge=0, le=10_000)
    billable: bool = False
    receipt_file_id: int | None = None


class ExpenseUpdate(InputModel):
    version: int = Field(ge=1)
    expense_date: date | None = None
    vendor: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    net_pence: int | None = Field(default=None, ge=0)
    tax_rate_bps: int | None = Field(default=None, ge=0, le=10_000)
    billable: bool | None = None
    receipt_file_id: int | None = None


class ProposalCreate(InputModel):
    account_id: int
    opportunity_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    valid_until: date | None = None
    notes: str = ""
    lines: list[CommercialLineInput] = Field(min_length=1)


class ProposalUpdate(InputModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    valid_until: date | None = None
    notes: str | None = None
    lines: list[CommercialLineInput] | None = Field(default=None, min_length=1)


class ProposalDecision(InputModel):
    reason: str = ""


class ContractCreate(InputModel):
    account_id: int
    proposal_id: int | None = None
    opportunity_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    starts_on: date | None = None
    ends_on: date | None = None
    value_pence: int = Field(default=0, ge=0)
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    notes: str = ""

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        return self


class ContractUpdate(InputModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    starts_on: date | None = None
    ends_on: date | None = None
    value_pence: int | None = Field(default=None, ge=0)
    notes: str | None = None


class ContractSign(InputModel):
    signed_at: datetime | None = None
    signed_file_id: int | None = None


class InvoiceCreate(InputModel):
    account_id: int
    project_id: int | None = None
    contract_id: int | None = None
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    due_on: date
    customer_name: str = Field(min_length=1, max_length=200)
    customer_address: str = ""
    notes: str = ""
    lines: list[CommercialLineInput] = Field(min_length=1)


class InvoiceUpdate(InputModel):
    version: int = Field(ge=1)
    due_on: date | None = None
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    customer_address: str | None = None
    notes: str | None = None
    lines: list[CommercialLineInput] | None = Field(default=None, min_length=1)


class CreditNoteCreate(InputModel):
    invoice_id: int
    reason: str = Field(min_length=1, max_length=500)
    lines: list[CommercialLineInput] = Field(min_length=1)


class PaymentCreate(InputModel):
    amount_pence: int = Field(gt=0)
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    received_at: datetime | None = None
    method: Literal["bank", "card", "cash", "stripe", "other"] = "bank"
    reference: str = Field(default="", max_length=200)
    invoice_id: int | None = None


class PaymentAllocationCreate(InputModel):
    invoice_id: int
    amount_pence: int = Field(gt=0)


class PaymentRefundCreate(InputModel):
    amount_pence: int = Field(gt=0)
    invoice_id: int | None = None
    reason: str = Field(default="", max_length=500)


class ClientSuccessUpsert(InputModel):
    account_id: int | None = None
    manual_health: HealthStatus | None = None
    open_risks: int = Field(default=0, ge=0)
    onboarding_status: Literal["Not started", "In progress", "Complete"] = "Not started"
    next_review_on: date | None = None
    renewal_on: date | None = None
    notes: str = ""
    version: int | None = Field(default=None, ge=1)


class RenewalProcessRequest(InputModel):
    days: int = Field(default=90, ge=0, le=365)


class ArchiveRequest(InputModel):
    version: int = Field(ge=1)


class ActionReason(InputModel):
    reason: str = Field(default="", max_length=500)


class ListResponse(BaseModel):
    items: list[dict]
    next_cursor: str | None = None

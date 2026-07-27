from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute

from app.operations import service
from app.confirmation import Confirmation
from app.operations.models import (
    ActionReason,
    ArchiveRequest,
    CatalogItemCreate,
    CatalogItemUpdate,
    ClientSuccessUpsert,
    ContractCreate,
    ContractSign,
    ContractUpdate,
    CreditNoteCreate,
    ExpenseCreate,
    ExpenseUpdate,
    InvoiceCreate,
    InvoiceUpdate,
    MilestoneCreate,
    MilestoneUpdate,
    PaymentAllocationCreate,
    PaymentCreate,
    PaymentRefundCreate,
    ProjectCreate,
    ProjectUpdate,
    ProposalCreate,
    ProposalDecision,
    ProposalUpdate,
    RenewalProcessRequest,
    TimeEntryCreate,
    TimeEntryUpdate,
)


class OperationRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except HTTPException as exc:
                detail = exc.detail
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "code": "not_found" if exc.status_code == 404 else "conflict" if exc.status_code == 409 else "operation_error",
                        "message": detail if isinstance(detail, str) else detail.get("message", "The operation could not be completed"),
                        "field_errors": detail.get("field_errors", {}) if isinstance(detail, dict) else {},
                        "current_record": detail.get("current_record") if isinstance(detail, dict) else None,
                        "current_version": detail.get("current_version") if isinstance(detail, dict) else None,
                        "request_id": request.headers.get("x-request-id", ""),
                    },
                    headers=exc.headers,
                )
            except RequestValidationError as exc:
                fields = {
                    ".".join(str(part) for part in error["loc"] if part != "body"): error["msg"]
                    for error in exc.errors()
                }
                return JSONResponse(
                    status_code=422,
                    content={
                        "code": "validation_error",
                        "message": "Request validation failed",
                        "field_errors": fields,
                        "request_id": request.headers.get("x-request-id", ""),
                    },
                )

        return handler


router = APIRouter(route_class=OperationRoute)
IdempotencyKey = Header(..., alias="Idempotency-Key", min_length=1, max_length=200)


# Catalog
@router.get("/catalog")
def catalog_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), include_inactive: bool = False):
    return service.list_catalog(cursor, limit, include_inactive)


@router.post("/catalog", status_code=status.HTTP_201_CREATED)
def catalog_create(request: CatalogItemCreate):
    return service.create_catalog_item(request)


@router.get("/catalog/{item_id}")
def catalog_get(item_id: int):
    return service.get_catalog_item(item_id)


@router.patch("/catalog/{item_id}")
def catalog_update(item_id: int, request: CatalogItemUpdate):
    return service.update_catalog_item(item_id, request)


@router.post("/catalog/{item_id}/archive")
def catalog_archive(item_id: int, request: ArchiveRequest):
    return service.archive_catalog_item(item_id, request.version)


# Delivery
@router.get("/projects")
def projects_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), project_status: str | None = Query(None, alias="status"), q: str = ""):
    return service.list_projects(cursor, limit, project_status, q)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def projects_create(request: ProjectCreate):
    return service.create_project(request)


@router.get("/projects/{project_id}")
def projects_get(project_id: int):
    return service.get_project(project_id)


@router.patch("/projects/{project_id}")
def projects_update(project_id: int, request: ProjectUpdate):
    return service.update_project(project_id, request)


@router.post("/projects/{project_id}/archive")
def projects_archive(project_id: int, request: ArchiveRequest):
    return service.archive_project(project_id, request.version)


@router.post("/contracts/{contract_id}/project", status_code=status.HTTP_201_CREATED)
def projects_from_contract(contract_id: int, idempotency_key: str = IdempotencyKey):
    return service.create_project_from_contract(contract_id, idempotency_key)


@router.post("/projects/{project_id}/milestones", status_code=status.HTTP_201_CREATED)
def milestones_create(project_id: int, request: MilestoneCreate):
    return service.create_milestone(project_id, request)


@router.get("/milestones")
def milestones_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), project_id: int | None = None, q: str = ""):
    return service.list_milestones(cursor, limit, project_id, q)


@router.get("/milestones/{milestone_id}")
def milestones_get(milestone_id: int):
    return service.get_milestone(milestone_id)


@router.patch("/milestones/{milestone_id}")
def milestones_update(milestone_id: int, request: MilestoneUpdate):
    return service.update_milestone(milestone_id, request)


@router.post("/milestones/{milestone_id}/archive")
def milestones_archive(milestone_id: int, request: ArchiveRequest):
    return service.archive_milestone(milestone_id, request.version)


@router.get("/time-entries")
def time_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), project_id: int | None = None, q: str = ""):
    return service.list_time_entries(cursor, limit, project_id, q)


@router.post("/time-entries", status_code=status.HTTP_201_CREATED)
def time_create(request: TimeEntryCreate):
    return service.create_time_entry(request)


@router.get("/time-entries/{entry_id}")
def time_get(entry_id: int):
    return service.get_time_entry(entry_id)


@router.patch("/time-entries/{entry_id}")
def time_update(entry_id: int, request: TimeEntryUpdate):
    return service.update_time_entry(entry_id, request)


@router.post("/time-entries/{entry_id}/archive")
def time_archive(entry_id: int, request: ArchiveRequest):
    return service.archive_time_entry(entry_id, request.version)


@router.get("/expenses")
def expenses_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), project_id: int | None = None):
    return service.list_expenses(cursor, limit, project_id)


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def expenses_create(request: ExpenseCreate):
    return service.create_expense(request)


@router.get("/expenses/{expense_id}")
def expenses_get(expense_id: int):
    return service.get_expense(expense_id)


@router.patch("/expenses/{expense_id}")
def expenses_update(expense_id: int, request: ExpenseUpdate):
    return service.update_expense(expense_id, request)


@router.post("/expenses/{expense_id}/archive")
def expenses_archive(expense_id: int, request: ArchiveRequest):
    return service.archive_expense(expense_id, request.version)


# Commercial documents
@router.get("/proposals")
def proposals_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), proposal_status: str | None = Query(None, alias="status"), q: str = ""):
    return service.list_proposals(cursor, limit, proposal_status, q)


@router.post("/proposals", status_code=status.HTTP_201_CREATED)
def proposals_create(request: ProposalCreate):
    return service.create_proposal(request)


@router.get("/proposals/{proposal_id}")
def proposals_get(proposal_id: int):
    return service.get_proposal(proposal_id)


@router.patch("/proposals/{proposal_id}")
def proposals_update(proposal_id: int, request: ProposalUpdate):
    return service.update_proposal(proposal_id, request)


@router.post("/proposals/{proposal_id}/send")
def proposals_send(proposal_id: int, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.send_proposal(proposal_id, idempotency_key)


@router.post("/proposals/{proposal_id}/accept")
def proposals_accept(proposal_id: int, idempotency_key: str = IdempotencyKey):
    return service.accept_proposal(proposal_id, idempotency_key)


@router.post("/proposals/{proposal_id}/reject")
def proposals_reject(proposal_id: int, request: ProposalDecision, idempotency_key: str = IdempotencyKey):
    return service.reject_proposal(proposal_id, request.reason, idempotency_key)


@router.post("/proposals/{proposal_id}/archive")
def proposals_archive(proposal_id: int, request: ArchiveRequest):
    return service.archive_proposal(proposal_id, request.version)


@router.get("/contracts")
def contracts_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), contract_status: str | None = Query(None, alias="status"), q: str = ""):
    return service.list_contracts(cursor, limit, contract_status, q)


@router.post("/contracts", status_code=status.HTTP_201_CREATED)
def contracts_create(request: ContractCreate):
    return service.create_contract(request)


@router.get("/contracts/{contract_id}")
def contracts_get(contract_id: int):
    return service.get_contract(contract_id)


@router.patch("/contracts/{contract_id}")
def contracts_update(contract_id: int, request: ContractUpdate):
    return service.update_contract(contract_id, request)


@router.post("/contracts/{contract_id}/send")
def contracts_send(contract_id: int, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.contract_action(contract_id, "Sent", idempotency_key)


@router.post("/contracts/{contract_id}/sign")
def contracts_sign(contract_id: int, request: ContractSign | None = None, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.contract_action(contract_id, "Signed", idempotency_key, request or ContractSign())


@router.post("/contracts/{contract_id}/activate")
def contracts_activate(contract_id: int, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.contract_action(contract_id, "Active", idempotency_key)


@router.post("/contracts/{contract_id}/archive")
def contracts_archive(contract_id: int, request: ArchiveRequest):
    return service.archive_contract(contract_id, request.version)


# Finance
@router.get("/invoices")
def invoices_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), invoice_status: str | None = Query(None, alias="status"), q: str = ""):
    return service.list_invoices(cursor, limit, invoice_status, q)


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def invoices_create(request: InvoiceCreate):
    return service.create_invoice(request)


@router.get("/invoices/{invoice_id}")
def invoices_get(invoice_id: int):
    return service.get_invoice(invoice_id)


@router.get("/invoices/{invoice_id}/pdf")
def invoices_pdf(invoice_id: int):
    invoice = service.get_invoice(invoice_id)
    if not invoice.get("pdf_path"):
        from app.operations.invoice_pdf import render_invoice

        path, _ = render_invoice(invoice_id)
    else:
        path = invoice["pdf_path"]
    return FileResponse(path, media_type="application/pdf", filename=f"{invoice['number']}.pdf")


@router.patch("/invoices/{invoice_id}")
def invoices_update(invoice_id: int, request: InvoiceUpdate):
    return service.update_invoice(invoice_id, request)


@router.post("/invoices/{invoice_id}/issue")
def invoices_issue(invoice_id: int, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.issue_invoice(invoice_id, idempotency_key)


@router.post("/invoices/{invoice_id}/void")
def invoices_void(invoice_id: int, _request: ActionReason, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.void_invoice(invoice_id, idempotency_key)


@router.get("/credit-notes")
def credit_notes_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100)):
    return service.list_credit_notes(cursor, limit)


@router.post("/credit-notes", status_code=status.HTTP_201_CREATED)
def credit_notes_create(request: CreditNoteCreate):
    return service.create_credit_note(request)


@router.get("/credit-notes/{credit_id}")
def credit_notes_get(credit_id: int):
    return service.get_credit_note(credit_id)


@router.post("/credit-notes/{credit_id}/issue")
def credit_notes_issue(credit_id: int, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.issue_credit_note(credit_id, idempotency_key)


@router.get("/payments")
def payments_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100)):
    return service.list_payments(cursor, limit)


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def payments_create(request: PaymentCreate, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.create_payment(request, idempotency_key)


@router.get("/payments/{payment_id}")
def payments_get(payment_id: int):
    return service.get_payment(payment_id)


@router.post("/payments/{payment_id}/allocate")
def payments_allocate(payment_id: int, request: PaymentAllocationCreate, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.allocate_payment(payment_id, request, idempotency_key)


@router.post("/payments/{payment_id}/refund")
def payments_refund(payment_id: int, request: PaymentRefundCreate, idempotency_key: str = IdempotencyKey, _confirmation: None = Confirmation):
    return service.refund_payment(payment_id, request, idempotency_key)


@router.get("/ledger")
def ledger(cursor: int | None = None, limit: int = Query(50, ge=1, le=100), q: str = "", source_type: str = ""):
    return service.ledger_report(cursor, limit, q, source_type)


# Client success and curated reports
@router.get("/client-success")
def client_success_list(cursor: int | None = None, limit: int = Query(50, ge=1, le=100)):
    return service.list_client_success(cursor, limit)


@router.post("/client-success/renewals/process")
def client_success_process_renewals(request: RenewalProcessRequest, idempotency_key: str = IdempotencyKey):
    return service.process_renewals(request.days, idempotency_key)


@router.put("/client-success/{account_id}")
def client_success_upsert(account_id: int, request: ClientSuccessUpsert):
    request = request.model_copy(update={"account_id": account_id})
    return service.upsert_client_success(request)


@router.get("/client-success/{account_id}")
def client_success_get(account_id: int):
    return service.get_client_success(account_id)


@router.get("/reports/finance")
def reports_finance():
    return service.finance_report()


@router.get("/reports")
def reports_overview():
    return service.reports_overview()


@router.get("/reports/projects")
def reports_projects():
    return service.project_report()


@router.get("/reports/renewals")
def reports_renewals(days: int = Query(90, ge=1, le=365)):
    return service.renewal_report(days)


@router.get("/reports/{report_name}.csv")
def report_csv(report_name: str):
    builders = {
        "overview": service.reports_overview,
        "finance": service.finance_report,
        "projects": service.project_report,
        "renewals": service.renewal_report,
        "ledger": lambda: service.ledger_report(limit=100),
    }
    builder = builders.get(report_name)
    if builder is None:
        raise HTTPException(status_code=404, detail="Curated report not found")
    payload = builder()
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list):
        rows = items
    else:
        rows = []
        for key, value in payload.items():
            if isinstance(value, dict):
                rows.extend(
                    {"metric": f"{key}.{child_key}", "value": child_value}
                    for child_key, child_value in value.items()
                )
            elif not isinstance(value, list):
                rows.append({"metric": key, "value": value})
    output = io.StringIO(newline="")
    columns = list(dict.fromkeys(key for row in rows for key in row)) or ["metric", "value"]
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: json.dumps(value, separators=(",", ":")) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            }
        )
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report_name}-report.csv"'},
    )

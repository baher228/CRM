from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Iterator

from fastapi import APIRouter, Depends, Query

from app import platform_db
from app.v1 import core_service as service
from app.v1 import models


router = APIRouter()


def database() -> Iterator:
    with platform_db.connect() as conn:
        yield conn


Db = Depends(database)


@router.get("/health")
def health(conn=Db):
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    return {
        "status": "ok" if integrity == "ok" else "degraded",
        "service": "CRM Workspace",
        "database": integrity,
        "schema_version": conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0],
    }


@router.get("/dashboard")
def dashboard(conn=Db):
    return service.dashboard(conn)


@router.get("/search")
def search(q: str = Query(min_length=1), limit: int = Query(default=30, ge=1, le=100), conn=Db):
    return {"items": service.global_search(conn, q, limit), "next_cursor": None}


@router.get("/accounts")
def accounts(
    q: str = "",
    status: str = "",
    health: str = "",
    archived: bool = False,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    conn=Db,
):
    return service.list_accounts(conn, query=q, status=status, health=health, archived=archived, include_archived=include_archived, cursor=cursor, limit=limit)


@router.post("/accounts", status_code=201)
def create_account(payload: models.AccountCreate, conn=Db):
    return service.create_account(conn, payload)


@router.get("/accounts/{account_id}")
def account(account_id: int, conn=Db):
    return service.get_account(conn, account_id, include_archived=True)


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, payload: models.AccountUpdate, conn=Db):
    return service.update_account(conn, account_id, payload)


@router.post("/accounts/{account_id}/archive")
def archive_account(account_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "accounts", account_id, expected_version=payload.version if payload else None)


@router.post("/accounts/{account_id}/restore")
def restore_account(account_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "accounts", account_id, restore=True, expected_version=payload.version if payload else None)


@router.post("/accounts/merge")
def merge_accounts(payload: models.MergeRequest, conn=Db):
    return service.merge_accounts(conn, payload.source_id, payload.target_id, payload.source_version, payload.target_version)


@router.get("/contacts")
def contacts(
    q: str = "",
    status: str = "",
    account_id: int | None = None,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    conn=Db,
):
    return service.list_contacts(conn, query=q, status=status, account_id=account_id, include_archived=include_archived, cursor=cursor, limit=limit)


@router.post("/contacts", status_code=201)
def create_contact(payload: models.ContactCreate, conn=Db):
    return service.create_contact(conn, payload)


@router.get("/contacts/{contact_id}")
def contact(contact_id: int, conn=Db):
    return service.get_contact(conn, contact_id, include_archived=True)


@router.patch("/contacts/{contact_id}")
def update_contact(contact_id: int, payload: models.ContactUpdate, conn=Db):
    return service.update_contact(conn, contact_id, payload)


@router.post("/contacts/{contact_id}/archive")
def archive_contact(contact_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "contacts", contact_id, expected_version=payload.version if payload else None)


@router.post("/contacts/{contact_id}/restore")
def restore_contact(contact_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "contacts", contact_id, restore=True, expected_version=payload.version if payload else None)


@router.post("/contacts/merge")
def merge_contacts(payload: models.MergeRequest, conn=Db):
    return service.merge_contacts(conn, payload.source_id, payload.target_id, payload.source_version, payload.target_version)


@router.get("/leads")
def leads(
    q: str = "",
    status: str = "",
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    conn=Db,
):
    return service.list_leads(conn, query=q, status=status, cursor=cursor, limit=limit)


@router.post("/leads", status_code=201)
def create_lead(payload: models.LeadCreate, conn=Db):
    return service.create_lead(conn, payload)


@router.get("/leads/{lead_id}")
def lead(lead_id: int, conn=Db):
    return service._get(conn, "sales_leads", lead_id)


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: int, payload: models.LeadUpdate, conn=Db):
    return service.update_lead(conn, lead_id, payload)


@router.post("/leads/{lead_id}/qualify")
def qualify_lead(lead_id: int, payload: models.QualificationRequest, conn=Db):
    return service.qualify_lead(conn, lead_id, payload)


@router.post("/leads/{lead_id}/archive")
def archive_lead(lead_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "leads", lead_id, expected_version=payload.version if payload else None)


@router.get("/pipeline/stages")
def pipeline_stages(conn=Db):
    return {"items": service.list_pipeline_stages(conn), "next_cursor": None}


@router.get("/opportunities")
def opportunities(
    q: str = "",
    stage_id: int | None = None,
    status: str = "",
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    conn=Db,
):
    return service.list_opportunities(conn, query=q, stage_id=stage_id, status=status, cursor=cursor, limit=limit)


@router.post("/opportunities", status_code=201)
def create_opportunity(payload: models.OpportunityCreate, conn=Db):
    return service.create_opportunity(conn, payload)


@router.get("/opportunities/{opportunity_id}")
def opportunity(opportunity_id: int, conn=Db):
    return service.get_opportunity(conn, opportunity_id)


@router.patch("/opportunities/{opportunity_id}")
def update_opportunity(opportunity_id: int, payload: models.OpportunityUpdate, conn=Db):
    return service.update_opportunity(conn, opportunity_id, payload)


@router.post("/opportunities/{opportunity_id}/transition")
def transition_opportunity(opportunity_id: int, payload: models.TransitionRequest, conn=Db):
    return service.transition_opportunity(conn, opportunity_id, payload)


@router.post("/opportunities/{opportunity_id}/archive")
def archive_opportunity(opportunity_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "opportunities", opportunity_id, expected_version=payload.version if payload else None)


@router.get("/tenders")
def tenders(
    q: str = "",
    status: str = "",
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    conn=Db,
):
    return service.list_tenders(conn, query=q, status=status, cursor=cursor, limit=limit)


@router.post("/tenders", status_code=201)
def create_tender(payload: models.TenderCreate, conn=Db):
    return service.create_tender(conn, payload)


@router.get("/tenders/{tender_id}")
def tender(tender_id: int, conn=Db):
    return service.get_tender(conn, tender_id)


@router.post("/tenders/{tender_id}/qualify")
def qualify_tender(tender_id: int, payload: models.QualificationRequest, conn=Db):
    return service.qualify_tender(conn, tender_id, payload)


@router.post("/tenders/{tender_id}/reject")
def reject_tender(tender_id: int, payload: models.TenderDecision, conn=Db):
    return service.decide_tender(conn, tender_id, "Rejected", payload)


@router.post("/tenders/{tender_id}/snooze")
def snooze_tender(tender_id: int, payload: models.TenderDecision, conn=Db):
    return service.decide_tender(conn, tender_id, "Snoozed", payload)


@router.post("/tenders/{tender_id}/reopen")
def reopen_tender(tender_id: int, payload: models.TenderDecision, conn=Db):
    return service.decide_tender(conn, tender_id, "Reviewing", payload)


@router.post("/tenders/{tender_id}/archive")
def archive_tender(tender_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "tenders", tender_id, expected_version=payload.version if payload else None)


@router.get("/activities")
def activities(
    entity_type: str = "",
    entity_id: int | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    conn=Db,
):
    return service.list_activities(conn, entity_type=entity_type, entity_id=entity_id, cursor=cursor, limit=limit)


@router.post("/activities", status_code=201)
def create_activity(payload: models.ActivityCreate, conn=Db):
    return service.add_activity(conn, payload)


@router.get("/tasks")
def tasks(
    q: str = "",
    status: str = "",
    entity_type: str = "",
    entity_id: int | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    conn=Db,
):
    return service.list_tasks(conn, query=q, status=status, entity_type=entity_type, entity_id=entity_id, cursor=cursor, limit=limit)


@router.post("/tasks", status_code=201)
def create_task(payload: models.TaskCreate, conn=Db):
    return service.create_task(conn, payload)


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: models.TaskUpdate, conn=Db):
    return service.update_task(conn, task_id, payload)


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, payload: models.VersionedUpdate, conn=Db):
    return service.update_task(conn, task_id, models.TaskUpdate(version=payload.version, status="Done"))


@router.post("/tasks/{task_id}/archive")
def archive_task(task_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "tasks", task_id, expected_version=payload.version if payload else None)


@router.get("/calendar/events")
def calendar_events(
    start: date | None = None,
    end: date | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=100),
    conn=Db,
):
    return service.list_events(conn, start=start, end=end, cursor=cursor, limit=limit)


@router.post("/calendar/events", status_code=201)
def create_calendar_event(payload: models.CalendarEventCreate, conn=Db):
    return service.create_event(conn, payload)


@router.patch("/calendar/events/{event_id}")
def update_calendar_event(event_id: int, payload: models.CalendarEventUpdate, conn=Db):
    return service.update_event(conn, event_id, payload)


@router.post("/calendar/events/{event_id}/archive")
def archive_calendar_event(event_id: int, payload: models.VersionedUpdate | None = None, conn=Db):
    return service.archive_record(conn, "events", event_id, expected_version=payload.version if payload else None)


@router.get("/tags")
def tags(conn=Db):
    return {"items": service.list_tags(conn), "next_cursor": None}


@router.post("/tags", status_code=201)
def create_tag(payload: models.TagCreate, conn=Db):
    return service.create_tag(conn, payload)


@router.put("/{entity_type}/{entity_id}/tags")
def set_tags(entity_type: str, entity_id: int, tag_ids: list[int], conn=Db):
    return service.set_entity_tags(conn, entity_type, entity_id, tag_ids)


@router.get("/{entity_type}/{entity_id}/tags")
def entity_tags(entity_type: str, entity_id: int, conn=Db):
    return {"items": service.get_entity_tags(conn, entity_type, entity_id), "next_cursor": None}


@router.get("/custom-fields")
def custom_fields(entity_type: str = "", conn=Db):
    return {"items": service.list_custom_fields(conn, entity_type), "next_cursor": None}


@router.post("/custom-fields", status_code=201)
def create_custom_field(payload: models.CustomFieldCreate, conn=Db):
    return service.create_custom_field(conn, payload)


@router.get("/saved-views")
def saved_views(entity_type: str = "", conn=Db):
    return {"items": service.list_saved_views(conn, entity_type), "next_cursor": None}


@router.post("/saved-views", status_code=201)
def create_saved_view(payload: models.SavedViewCreate, conn=Db):
    return service.create_saved_view(conn, payload)


@router.get("/settings/business")
def business_profile(conn=Db):
    return service.get_business_profile(conn)


@router.patch("/settings/business")
def update_business_profile(payload: models.BusinessProfileUpdate, conn=Db):
    return service.update_business_profile(conn, payload)


@router.post("/settings/integrity")
def full_integrity_check(conn=Db):
    result = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    return {
        "status": "ok" if result.lower() == "ok" else "degraded",
        "database": result,
        "check": "integrity_check",
        "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
    }

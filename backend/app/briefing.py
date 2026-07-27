"""Local Today briefing built from CRM Workspace records."""

import json
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app import platform_db
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings
from app.v1 import core_service
from app.v1.models import TaskCreate


class BriefingSignal(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: Literal["internal", "external"]
    source: Literal["local", "tavily"]
    company_name: str
    headline: str
    detail: str
    source_url: str | None = None
    entity_type: str = ""
    entity_id: int | None = None


class BriefingItem(BaseModel):
    rank: int
    signal: BriefingSignal
    drafted_action: str
    reasoning: str
    urgency: int = Field(default=50, ge=0, le=100)


class Briefing(BaseModel):
    generated_at: datetime
    total_signals_gathered: int
    items: list[BriefingItem]


class GenerateBriefingRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    include_external: bool = True


class ApproveActionRequest(BaseModel):
    item_index: int
    action_text: str | None = None


class ApproveActionResponse(BaseModel):
    success: bool
    message: str
    task_created: bool = False


def get_latest_briefing() -> Briefing | None:
    with platform_db.connect() as conn:
        row = conn.execute("SELECT value_json FROM app_settings WHERE key='latest_briefing'").fetchone()
    if not row:
        return None
    try:
        return Briefing.model_validate_json(row["value_json"])
    except ValueError:
        return None


async def generate_briefing(request: GenerateBriefingRequest) -> Briefing:
    signals = _internal_signals(request.limit)
    if request.include_external:
        signals.extend(await _external_signals(signals, request.limit))
    signals.sort(key=_signal_priority, reverse=True)
    items = [
        BriefingItem(
            rank=index,
            signal=signal,
            drafted_action=_action_for(signal),
            reasoning="Ranked from the current CRM workload, deadlines, revenue and relationship risk.",
            urgency=min(100, _signal_priority(signal)),
        )
        for index, signal in enumerate(signals[: request.limit], start=1)
    ]
    briefing = Briefing(
        generated_at=platform_db.utc_now(),
        total_signals_gathered=len(signals),
        items=items,
    )
    with platform_db.connect() as conn:
        conn.execute(
            """INSERT INTO app_settings(key,value_json,updated_at) VALUES('latest_briefing',?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (briefing.model_dump_json(), platform_db.utc_now().isoformat()),
        )
    return briefing


async def approve_action(request: ApproveActionRequest) -> ApproveActionResponse:
    briefing = get_latest_briefing()
    if not briefing:
        return ApproveActionResponse(success=False, message="No briefing generated yet")
    if request.item_index < 0 or request.item_index >= len(briefing.items):
        return ApproveActionResponse(success=False, message="Invalid item index")
    item = briefing.items[request.item_index]
    title = (request.action_text or item.drafted_action).strip()
    with platform_db.connect() as conn:
        task = core_service.create_task(
            conn,
            TaskCreate(
                entity_type=item.signal.entity_type,
                entity_id=item.signal.entity_id,
                title=title,
                priority="High" if item.urgency >= 75 else "Medium",
            ),
        )
    return ApproveActionResponse(success=True, message=f"Task #{task['id']} created", task_created=True)


def _internal_signals(limit: int) -> list[BriefingSignal]:
    with platform_db.connect() as conn:
        summary = core_service.dashboard(conn)
        signals = []
        for item in summary["action_items"][:limit]:
            item_type = item.get("type", "record")
            signals.append(
                BriefingSignal(
                    type="internal",
                    source="local",
                    company_name=item.get("buyer_name") or item.get("title") or "CRM Workspace",
                    headline=item.get("title") or item.get("subject") or "Action required",
                    detail=item.get("reason") or item.get("description") or item.get("next_action") or item.get("deadline") or "Open the linked record for context.",
                    entity_type=item_type,
                    entity_id=item.get("id"),
                )
            )
        if not signals:
            for row in conn.execute(
                """SELECT o.id,o.title,a.name,o.next_action,o.value_minor
                   FROM opportunities o JOIN accounts a ON a.id=o.account_id
                   JOIN pipeline_stages s ON s.id=o.stage_id
                   WHERE o.archived_at IS NULL AND s.kind='open'
                   ORDER BY o.value_minor DESC LIMIT ?""",
                (limit,),
            ):
                signals.append(
                    BriefingSignal(
                        type="internal", source="local", company_name=row["name"],
                        headline=row["title"], detail=row["next_action"] or "Review the next deal action",
                        entity_type="opportunity", entity_id=row["id"],
                    )
                )
    return signals


async def _external_signals(internal: list[BriefingSignal], limit: int) -> list[BriefingSignal]:
    settings = EnrichmentSettings()
    if not settings.tavily_api_key or settings.tavily_api_key.lower().startswith("your_"):
        return []
    client = TavilyClient(settings)
    result: list[BriefingSignal] = []
    try:
        for name in list(dict.fromkeys(signal.company_name for signal in internal))[: min(limit, 5)]:
            try:
                rows = await client.search(f"{name} recent procurement business news", max_results=2)
            except Exception:
                continue
            for row in rows:
                if row.get("title"):
                    result.append(
                        BriefingSignal(
                            type="external", source="tavily", company_name=name,
                            headline=row["title"], detail=(row.get("content") or row.get("snippet") or "")[:500],
                            source_url=row.get("url"),
                        )
                    )
    finally:
        await client.close()
    return result


def _signal_priority(signal: BriefingSignal) -> int:
    text = f"{signal.headline} {signal.detail}".lower()
    score = 50
    if any(word in text for word in ("overdue", "deadline", "blocked", "at risk")):
        score += 35
    if any(word in text for word in ("invoice", "payment", "proposal", "tender")):
        score += 10
    return min(score, 100)


def _action_for(signal: BriefingSignal) -> str:
    if signal.entity_type == "task":
        return f"Complete or reschedule: {signal.headline}"
    if signal.entity_type == "tender":
        return f"Review the tender deadline and decide the next bid action for {signal.company_name}"
    if signal.entity_type == "opportunity":
        return f"Advance the next deal action for {signal.company_name}"
    return f"Review and act on: {signal.headline}"

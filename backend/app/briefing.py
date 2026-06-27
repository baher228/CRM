"""Daybreak morning briefing — gather, rank, draft, present.

Bounties: Attio (internal CRM), Tavily (external signals), Gemini (rank + draft),
n8n (trigger via webhook), Superlinked (TODO: ranking), SLNG (TODO: voice).
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.lead_enrichment.clients.attio_client import AttioClient
from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BriefingSignal(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: Literal["internal", "external"]
    source: Literal["attio", "tavily"]
    company_name: str
    headline: str
    detail: str
    source_url: str | None = None
    attio_record_id: str | None = None
    attio_object_slug: str | None = None


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
    superlinked_ranking_applied: bool = False  # TODO: Superlinked integration
    slng_audio_url: str | None = None  # TODO: SLNG voice briefing
    n8n_triggered: bool = False


class GenerateBriefingRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50, description="Max companies to scan from Attio")
    n8n_triggered: bool = Field(default=False, description="Set true when called from n8n webhook")
    write_actions_to_attio: bool = Field(default=False, description="Auto-create tasks in Attio for approved items")


class ApproveActionRequest(BaseModel):
    item_index: int
    action_text: str | None = None  # override the drafted action


class ApproveActionResponse(BaseModel):
    success: bool
    message: str
    attio_task_created: bool = False


# ---------------------------------------------------------------------------
# In-memory store for latest briefing (hackathon shortcut)
# ---------------------------------------------------------------------------

_latest_briefing: Briefing | None = None


def get_latest_briefing() -> Briefing | None:
    return _latest_briefing


# ---------------------------------------------------------------------------
# Gather internal signals from Attio
# ---------------------------------------------------------------------------

async def _gather_internal_signals(
    settings: EnrichmentSettings,
    attio_client: AttioClient,
    limit: int,
) -> list[BriefingSignal]:
    """Pull companies from Attio and surface staleness / pipeline signals."""
    signals: list[BriefingSignal] = []

    try:
        if settings.attio_lead_list_id and settings.attio_lead_list_id != "your_attio_list_id":
            raw_records = await attio_client.query_list_entries(settings.attio_lead_list_id, limit)
        else:
            raw_records = await attio_client.query_records(settings.attio_lead_object, limit)
    except Exception:
        raw_records = []

    for item in raw_records:
        record = item.get("parent_record") or item.get("record") or item
        values = record.get("values", {}) if isinstance(record, dict) else {}
        record_id = _extract_record_id(record, item)
        object_slug = (
            record.get("object_slug")
            or record.get("object")
            or item.get("parent_object")
            or settings.attio_lead_object
        )
        name = _first_text(values, settings.attio_company_name_attribute) or record.get("name") or "Unknown"

        # Check for enrichment staleness
        enriched_at = _first_text(values, settings.attio_enriched_at_attribute)
        summary = _first_text(values, settings.attio_enrichment_summary_attribute)

        if summary:
            headline = f"Enriched lead in pipeline: {name}"
            detail = summary[:500] if len(summary) > 500 else summary
        elif enriched_at:
            headline = f"Lead {name} was enriched but may need refresh"
            detail = f"Last enriched at {enriched_at}. Consider re-running enrichment."
        else:
            headline = f"Lead {name} has not been enriched yet"
            detail = f"Company '{name}' is in your CRM but lacks enrichment data. Consider researching."

        signals.append(BriefingSignal(
            type="internal",
            source="attio",
            company_name=name,
            headline=headline,
            detail=detail,
            attio_record_id=str(record_id) if record_id else None,
            attio_object_slug=str(object_slug),
        ))

    return signals


def _extract_record_id(record: dict, item: dict) -> Any:
    rid = record.get("id")
    if isinstance(rid, dict):
        return rid.get("record_id")
    return rid or record.get("record_id") or item.get("parent_record_id")


def _first_text(values: dict, slug: str) -> str | None:
    val = values.get(slug)
    if val is None:
        return None
    if isinstance(val, list):
        if not val:
            return None
        val = val[0]
    if isinstance(val, dict):
        for key in ("value", "domain", "email_address", "name", "title"):
            if key in val:
                return str(val[key])
        if "option" in val and isinstance(val["option"], dict):
            return val["option"].get("title")
    return str(val) if val else None


# ---------------------------------------------------------------------------
# Gather external signals from Tavily
# ---------------------------------------------------------------------------

async def _gather_external_signals(
    company_names: list[str],
    tavily_client: TavilyClient,
) -> list[BriefingSignal]:
    """Search Tavily for recent news/signals about CRM companies."""
    signals: list[BriefingSignal] = []

    for name in company_names[:10]:  # cap to avoid rate limits
        try:
            results = await tavily_client.search(
                f"{name} recent news funding partnership announcement 2026",
                max_results=3,
            )
            for result in results:
                url = result.get("url", "")
                title = result.get("title", "")
                snippet = result.get("content") or result.get("snippet") or ""
                if title:
                    signals.append(BriefingSignal(
                        type="external",
                        source="tavily",
                        company_name=name,
                        headline=title,
                        detail=snippet[:500],
                        source_url=url,
                    ))
        except Exception:
            continue  # don't let one company failure break the whole briefing

    return signals


# ---------------------------------------------------------------------------
# Rank + draft actions with Gemini (placeholder for Superlinked)
# ---------------------------------------------------------------------------

async def _rank_and_draft(
    signals: list[BriefingSignal],
    gemini_client: GeminiClient,
) -> list[BriefingItem]:
    """Use Gemini to rank top 5 signals and draft an action for each.

    TODO: Replace ranking with Superlinked vector ranking when integrated.
    """
    if not signals:
        return []

    # Build the prompt with all signals
    signal_texts = []
    for i, sig in enumerate(signals):
        signal_texts.append(
            f"Signal {i}: [{sig.type.upper()}] [{sig.source}] "
            f"Company: {sig.company_name}\n"
            f"  Headline: {sig.headline}\n"
            f"  Detail: {sig.detail[:300]}\n"
            f"  URL: {sig.source_url or 'N/A'}"
        )

    prompt = f"""You are Daybreak, an AI chief-of-staff that prepares a morning briefing.

Below are {len(signals)} signals gathered from internal CRM (Attio) and external news (Tavily).

Your job:
1. Pick the TOP 5 most important/actionable signals.
2. For each, draft a specific, concise action the user should take.
3. Explain why this signal matters (1 sentence).
4. Rate urgency 0-100.

Return a JSON array of exactly 5 objects (or fewer if less signals), each with:
- "signal_index": the index number from the signals below
- "drafted_action": a specific 1-2 sentence action to take
- "reasoning": why this matters (1 sentence)
- "urgency": integer 0-100

Signals:
{chr(10).join(signal_texts)}

Return ONLY the JSON array, no other text."""

    try:
        from google.genai import types
        response = await gemini_client.client.aio.models.generate_content(
            model=gemini_client.settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )
        raw = json.loads(response.text or "[]")
    except Exception:
        # Fallback: just take first 5 signals with generic actions
        raw = [
            {"signal_index": i, "drafted_action": "Review this signal and take action.", "reasoning": "Surfaced in your morning scan.", "urgency": 50}
            for i in range(min(5, len(signals)))
        ]

    items: list[BriefingItem] = []
    for rank, entry in enumerate(raw[:5], start=1):
        idx = entry.get("signal_index", 0)
        if idx < 0 or idx >= len(signals):
            idx = 0
        items.append(BriefingItem(
            rank=rank,
            signal=signals[idx],
            drafted_action=entry.get("drafted_action", "Review and act."),
            reasoning=entry.get("reasoning", "Flagged by Daybreak."),
            urgency=max(0, min(100, entry.get("urgency", 50))),
        ))

    return items


# ---------------------------------------------------------------------------
# Main briefing flow
# ---------------------------------------------------------------------------

async def generate_briefing(request: GenerateBriefingRequest) -> Briefing:
    """Full Daybreak pipeline: gather → rank → draft → present.

    Called by the /api/briefing/generate endpoint.
    Can be triggered by n8n webhook on a schedule (e.g. 6am daily).
    """
    global _latest_briefing

    settings = EnrichmentSettings()
    # Only require tavily + gemini for briefing (attio can gracefully degrade)
    missing = []
    if not settings.tavily_api_key:
        missing.append("TAVILY_API_KEY")
    if not settings.gemini_api_key:
        missing.append("GEMINI_API_KEY")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    attio_client = AttioClient(settings)
    tavily_client = TavilyClient(settings)
    gemini_client = GeminiClient(settings)

    try:
        # 1. Gather internal signals from Attio
        internal_signals = await _gather_internal_signals(settings, attio_client, request.limit)

        # 2. Gather external signals from Tavily
        company_names = list({sig.company_name for sig in internal_signals if sig.company_name != "Unknown"})
        external_signals = await _gather_external_signals(company_names, tavily_client)

        # 3. Combine all signals
        all_signals = internal_signals + external_signals

        # ----- SUPERLINKED HOOK -----
        # TODO: When Superlinked is integrated, replace the Gemini ranking below
        # with Superlinked vector-based ranking:
        #   ranked_signals = await superlinked_rank(all_signals, user_context)
        # For now, Gemini does the ranking.
        # -----------------------------

        # 4. Rank top 5 and draft actions with Gemini
        items = await _rank_and_draft(all_signals, gemini_client)

        # ----- SLNG HOOK -----
        # TODO: When SLNG is integrated, generate voice audio:
        #   audio_url = await slng_generate_voice_briefing(items, settings.slng_api_key)
        # For now, text only.
        # ----------------------

        briefing = Briefing(
            generated_at=datetime.now(timezone.utc),
            total_signals_gathered=len(all_signals),
            items=items,
            n8n_triggered=request.n8n_triggered,
        )

        _latest_briefing = briefing
        return briefing

    finally:
        await attio_client.close()
        await tavily_client.close()


# ---------------------------------------------------------------------------
# Approve action → write back to Attio
# ---------------------------------------------------------------------------

async def approve_action(request: ApproveActionRequest) -> ApproveActionResponse:
    """Approve a briefing item's action and optionally create an Attio task."""
    briefing = get_latest_briefing()
    if not briefing:
        return ApproveActionResponse(success=False, message="No briefing generated yet.")
    if request.item_index < 0 or request.item_index >= len(briefing.items):
        return ApproveActionResponse(success=False, message="Invalid item index.")

    item = briefing.items[request.item_index]
    action_text = request.action_text or item.drafted_action

    # Write task to Attio if we have a record ID
    if item.signal.attio_record_id and item.signal.attio_object_slug:
        settings = EnrichmentSettings()
        if settings.attio_api_token:
            attio_client = AttioClient(settings)
            try:
                await attio_client.create_task(
                    item.signal.attio_object_slug,
                    item.signal.attio_record_id,
                    f"Daybreak action: {item.signal.company_name}",
                    action_text,
                )
                return ApproveActionResponse(
                    success=True,
                    message=f"Task created in Attio for {item.signal.company_name}",
                    attio_task_created=True,
                )
            finally:
                await attio_client.close()

    return ApproveActionResponse(
        success=True,
        message=f"Action approved: {action_text}",
        attio_task_created=False,
    )

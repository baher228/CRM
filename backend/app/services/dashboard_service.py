from app.schemas import ActivityItem, DashboardSummary
from app.services import crm_store


def get_dashboard() -> DashboardSummary:
    counts = crm_store.dashboard_counts()
    recent: list[ActivityItem] = []
    for note in crm_store.list_notes()[:5]:
        recent.append(
            ActivityItem(
                id=f"note-{note.id}",
                type="note",
                title="Note added",
                detail=note.body,
                occurred_at=note.created_at,
                related_type=note.related_type,
                related_id=note.related_id,
            )
        )
    return DashboardSummary(**counts, recent_activity=recent)

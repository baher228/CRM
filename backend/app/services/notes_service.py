from app.schemas import ActivityItem, Note, NoteCreateRequest
from app.services import crm_store


def list_notes(related_type: str | None = None, related_id: int | None = None) -> list[Note]:
    return crm_store.list_notes(related_type, related_id)


def create_note(request: NoteCreateRequest) -> Note:
    return crm_store.create_note(request.related_type.strip(), request.related_id, request.body.strip())


def delete_note(note_id: int) -> bool:
    return crm_store.delete_note(note_id)


def activity_for(related_type: str, related_id: int) -> list[ActivityItem]:
    items: list[ActivityItem] = []
    for note in crm_store.list_notes(related_type, related_id):
        items.append(
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
    for task in crm_store.list_tasks():
        if task.related_type == related_type and task.related_id == related_id and task.created_at:
            items.append(
                ActivityItem(
                    id=f"task-{task.id}",
                    type="task",
                    title=task.title,
                    detail=f"{task.status} follow-up",
                    occurred_at=task.created_at,
                    related_type=related_type,
                    related_id=related_id,
                )
            )
    return sorted(items, key=lambda item: item.occurred_at, reverse=True)

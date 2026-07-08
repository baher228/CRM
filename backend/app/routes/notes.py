from fastapi import APIRouter, HTTPException

from app.schemas import ActivityItem, Note, NoteCreateRequest
from app.services import notes_service


router = APIRouter()


@router.get("/notes", response_model=list[Note])
def get_notes(related_type: str | None = None, related_id: int | None = None) -> list[Note]:
    return notes_service.list_notes(related_type, related_id)


@router.post("/notes", response_model=Note)
def create_note(request: NoteCreateRequest) -> Note:
    return notes_service.create_note(request)


@router.delete("/notes/{note_id}")
def delete_note(note_id: int) -> dict[str, bool]:
    deleted = notes_service.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": True}


@router.get("/activity/{related_type}/{related_id}", response_model=list[ActivityItem])
def get_activity(related_type: str, related_id: int) -> list[ActivityItem]:
    return notes_service.activity_for(related_type, related_id)

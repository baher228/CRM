from fastapi import APIRouter

from app.schemas import Lead
from app.services import leads_service


router = APIRouter()


@router.get("/leads", response_model=list[Lead])
def get_leads() -> list[Lead]:
    return leads_service.list_leads()


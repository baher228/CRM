from fastapi import APIRouter, HTTPException

from app.schemas import Lead, LeadUpdateRequest
from app.services import leads_service


router = APIRouter()


@router.get("/leads", response_model=list[Lead])
def get_leads() -> list[Lead]:
    return leads_service.list_leads()


@router.patch("/leads/{lead_id}", response_model=Lead)
def update_lead(lead_id: int, request: LeadUpdateRequest) -> Lead:
    lead = leads_service.update_lead(lead_id, request)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.post("/leads/{lead_id}/confirm", response_model=Lead)
async def confirm_lead(lead_id: int) -> Lead:
    lead = await leads_service.confirm_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.post("/leads/{lead_id}/reject", response_model=Lead)
def reject_lead(lead_id: int) -> Lead:
    lead = leads_service.reject_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead

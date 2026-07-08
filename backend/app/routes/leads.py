from fastapi import APIRouter, HTTPException

from app.schemas import Lead, LeadBulkRequest, LeadBulkResponse, LeadCreateRequest, LeadUpdateRequest
from app.services import leads_service


router = APIRouter()


@router.get("/leads", response_model=list[Lead])
def get_leads() -> list[Lead]:
    return leads_service.list_leads()


@router.post("/leads", response_model=Lead)
def create_lead(request: LeadCreateRequest) -> Lead:
    return leads_service.create_lead(request)


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


@router.post("/leads/bulk", response_model=LeadBulkResponse)
def bulk_update_leads(request: LeadBulkRequest) -> LeadBulkResponse:
    return leads_service.bulk_update_leads(request)


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: int) -> dict[str, bool]:
    deleted = leads_service.delete_lead(lead_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"deleted": True}

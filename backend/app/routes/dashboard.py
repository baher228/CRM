from fastapi import APIRouter

from app.schemas import DashboardSummary
from app.services import dashboard_service


router = APIRouter()


@router.get("/dashboard", response_model=DashboardSummary)
def get_dashboard() -> DashboardSummary:
    return dashboard_service.get_dashboard()

from fastapi import APIRouter

from app.schemas import HealthResponse
from app.services import health_service


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return health_service.get_health()


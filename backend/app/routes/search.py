from fastapi import APIRouter

from app.schemas import SearchResult
from app.services import search_service


router = APIRouter()


@router.get("/search", response_model=list[SearchResult])
def search(q: str = "") -> list[SearchResult]:
    return search_service.search(q)

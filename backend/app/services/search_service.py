from app.schemas import SearchResult
from app.services import crm_store


def search(query: str) -> list[SearchResult]:
    cleaned = query.strip()
    if not cleaned:
        return []
    return [SearchResult.model_validate(item) for item in crm_store.search(cleaned)]

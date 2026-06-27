from app.data import LEADS
from app.schemas import Lead


def list_leads() -> list[Lead]:
    return LEADS


from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DiscoveryRunRequest(BaseModel):
    niche: str = Field(min_length=2, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=10, ge=1, le=100)
    portals: list[str] = Field(default_factory=list, max_length=50)
    deadline_window: str = Field(default="", max_length=120)
    minimum_value: str = Field(default="", max_length=120)
    open_notices_only: bool = True

    @field_validator("niche")
    @classmethod
    def clean_niche(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("must contain at least two non-space characters")
        return cleaned


EntityType = Literal["accounts", "contacts", "leads", "opportunities"]


class CsvImportRequest(BaseModel):
    entity_type: EntityType
    csv_text: str = Field(min_length=1, max_length=10_000_000)
    mapping: dict[str, str] = Field(default_factory=dict)
    filename: str = Field(default="import.csv", min_length=1, max_length=255)

    @field_validator("csv_text")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value.lstrip("\ufeff\r\n\t "):
            raise ValueError("CSV content must not be blank")
        return value


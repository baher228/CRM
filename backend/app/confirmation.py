from typing import Annotated

from fastapi import Depends, Header, HTTPException


def require_confirmation(
    value: Annotated[str | None, Header(alias="X-CRM-Confirmed")] = None,
) -> None:
    if str(value or "").strip().lower() != "true":
        raise HTTPException(
            status_code=428,
            detail="Explicit operator confirmation is required for this action",
        )


Confirmation = Depends(require_confirmation)

import json
import logging
from typing import Any


logger = logging.getLogger("lead_enrichment")


def configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_event(event: str, **fields: Any) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if "key" not in key.lower() and "token" not in key.lower() and "secret" not in key.lower()
    }
    logger.info(json.dumps({"event": event, **safe_fields}, default=str))


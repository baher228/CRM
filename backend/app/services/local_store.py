import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)


def load_model_list(path: Path, model: type[ModelT]) -> list[ModelT]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []

    items: list[ModelT] = []
    for item in payload:
        try:
            items.append(model.model_validate(item))
        except ValueError:
            continue
    return items


def save_model_list(path: Path, items: list[BaseModel]) -> None:
    payload = [item.model_dump(mode="json") for item in items]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

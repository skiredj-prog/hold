"""Représentation canonique partagée par la bibliothèque et HOLD Studio.

Une même valeur doit produire la même empreinte en Python et dans le navigateur :
clés triées, aucun espace, Unicode conservé, flottants entiers écrits comme des entiers
(Python écrit 300.0, JavaScript écrit 300).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Nombre non fini interdit dans une valeur canonique")
        return int(value) if value.is_integer() else value
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump(mode="json", exclude_none=True))
    raise TypeError(f"Type non canonicalisable : {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    return sha256_hex(canonical_json(value))

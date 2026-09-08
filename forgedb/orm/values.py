"""Serialization helpers between python values and SQLite storage.

JSON-ish fields (dict, list, nested models) are stored as JSON text through
``encode_json`` / ``decode_json``. Every other value is coerced to a scalar
sqlite3 can bind directly (ISO-8601 for dates/times, values for enums, ...).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from .schema import Column


def json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def encode_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=json_default)


def decode_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def coerce_to_sql(column: Column, value: Any) -> Any:
    """Coerce a python value to something sqlite3 can bind for a column."""
    if value is None:
        return None
    if column.json:
        return encode_json(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    return value


def decode_from_sql(column: Column, value: Any) -> Any:
    if column.json:
        return decode_json(value)
    return value
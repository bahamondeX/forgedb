"""Filter condition DSL.

Conditions form a small tree that combines boolean logic (and_/or_) with
leaf operators (``=``, ``>``, ``IN``, ``LIKE``, ...). Rendering a condition
produces a SQL WHERE fragment with every value bound as a named parameter,
plus the parameter dict, so nothing user-controlled is ever interpolated
into SQL text.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from enum import Enum
from typing import Any

from .errors import UnknownFieldError, UnknownOperatorError
from .schema import TableSchema
from .values import coerce_to_sql


def _scalar_sql(value: Any) -> Any:
    """Bind a value as a raw SQLite scalar (no JSON encoding)."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (_datetime, _date, _time)):
        return value.isoformat()
    return value

_SQL_OPS = {
    "eq",
    "ne",
    "gt",
    "ge",
    "gte",
    "lt",
    "le",
    "lte",
}

_SQL_SYMBOLS = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "ge": ">=",
    "gte": ">=",
    "lt": "<",
    "le": "<=",
    "lte": "<=",
}

_SPECIAL_OPS = {
    "in",
    "not_in",
    "like",
    "not_like",
    "contains",
    "startswith",
    "endswith",
    "isnull",
    "json",
    "has_key",
}


class Condition:
    __slots__ = ("children", "field", "kind", "op", "value")

    def __init__(self, kind: str, **kwargs: Any) -> None:
        self.kind = kind
        self.op = kwargs.get("op", "")
        self.field = kwargs.get("field", "")
        self.value = kwargs.get("value")
        self.children = kwargs.get("children", [])

    def __and__(self, other: Condition) -> Condition:
        return and_(self, other)

    def __or__(self, other: Condition) -> Condition:
        return or_(self, other)

    def __repr__(self) -> str:
        if self.kind in ("and", "or"):
            return f"Condition('{self.kind}', children={self.children!r})"
        return f"Condition('{self.op}', {self.field}={self.value!r})"


def and_(*conditions: Condition | None, **kwargs: Any) -> Condition:
    children = [c for c in conditions if c is not None]
    children.extend(_leaf_from_key(key, value) for key, value in kwargs.items())
    if len(children) == 1:
        return children[0]
    return Condition("and", children=children)


def or_(*conditions: Condition | None, **kwargs: Any) -> Condition:
    children = [c for c in conditions if c is not None]
    children.extend(_leaf_from_key(key, value) for key, value in kwargs.items())
    if len(children) == 1:
        return children[0]
    return Condition("or", children=children)


def _split_key(key: str) -> tuple[str, str]:
    if "__" in key:
        field, op = key.rsplit("__", 1)
    else:
        field, op = key, "eq"
    return field, op


def _leaf_from_key(key: str, value: Any) -> Condition:
    field, op = _split_key(key)
    if op not in _SQL_OPS and op not in _SPECIAL_OPS:
        raise UnknownOperatorError(f"unknown operator {op!r} for field {field!r}")
    if value is None and op == "eq":
        return Condition("op", op="isnull", field=field, value=True)
    return Condition("op", op=op, field=field, value=value)


def parse_filters(schema: TableSchema, kwargs: dict[str, Any]) -> list[Condition]:
    """Turn ``name=value`` / ``name__op=value`` kwargs into leaf conditions."""
    conditions = []
    for key, value in kwargs.items():
        _check_field(schema, _split_key(key)[0])
        conditions.append(_leaf_from_key(key, value))
    return conditions


def _check_field(schema: TableSchema, field: str) -> None:
    if field not in schema._column_map:
        raise UnknownFieldError(f"{schema.model.__name__} has no field {field!r}")


def _placeholder(state: dict[str, Any]) -> str:
    name = f"p{state['n']}"
    state["n"] += 1
    return name


def _bind(state: dict[str, Any], value: Any) -> str:
    name = _placeholder(state)
    state["params"][name] = value
    return name


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def render_condition(schema: TableSchema, cond: Condition, state: dict[str, Any]) -> str:
    """Render a condition tree to a WHERE fragment; values go in ``state['params']``."""
    if cond.kind in ("and", "or"):
        parts = [render_condition(schema, child, state) for child in cond.children]
        keyword = " AND " if cond.kind == "and" else " OR "
        return "(" + keyword.join(parts) + ")"

    try:
        column = schema._column_map[cond.field]
    except KeyError:
        raise UnknownFieldError(
            f"{schema.model.__name__} has no field {cond.field!r}"
        ) from None
    quoted = f'"{cond.field}"'
    op = cond.op

    if op in _SQL_SYMBOLS:
        value = coerce_to_sql(column, cond.value)
        return f"{quoted} {_SQL_SYMBOLS[op]} :{_bind(state, value)}"

    if op in ("in", "not_in"):
        values = list(cond.value) if isinstance(cond.value, Iterable) and not isinstance(cond.value, (str, bytes)) else [cond.value]
        placeholders = ", ".join(f":{_bind(state, coerce_to_sql(column, v))}" for v in values)
        keyword = "NOT IN" if op == "not_in" else "IN"
        return f"{quoted} {keyword} ({placeholders})"

    if op == "like":
        value = coerce_to_sql(column, cond.value)
        return f"{quoted} LIKE :{_bind(state, value)} ESCAPE '\\'"

    if op in ("contains", "startswith", "endswith"):
        pattern = _escape_like(str(cond.value))
        if op == "contains":
            pattern = f"%{pattern}%"
        elif op == "startswith":
            pattern = f"{pattern}%"
        else:
            pattern = f"%{pattern}"
        return f"{quoted} LIKE :{_bind(state, pattern)} ESCAPE '\\'"

    if op == "not_like":
        value = coerce_to_sql(column, cond.value)
        return f"{quoted} NOT LIKE :{_bind(state, value)} ESCAPE '\\'"

    if op == "isnull":
        keyword = "IS NULL" if cond.value else "IS NOT NULL"
        return f"{quoted} {keyword}"

    if op == "json":
        path, expected = cond.value if isinstance(cond.value, (tuple, list)) else (cond.value, None)
        left = f'json_extract({quoted}, :{_bind(state, str(path))})'
        if expected is None:
            return f"{left} IS NOT NULL"
        return f"{left} = :{_bind(state, _scalar_sql(expected))}"

    if op == "has_key":
        path = cond.value
        return f'json_extract({quoted}, :{_bind(state, str(path))}) IS NOT NULL'

    raise UnknownOperatorError(f"unknown operator {op!r}")


def render(schema: TableSchema, cond: Condition | None) -> tuple[str | None, dict[str, Any]]:
    """Render a full condition tree, returning ``(fragment, params)``."""
    if cond is None:
        return None, {}
    state: dict[str, Any] = {"n": 0, "params": {}}
    return render_condition(schema, cond, state), state["params"]
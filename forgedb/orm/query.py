"""Query builder: a small, composable read/write API over a model's table."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .conditions import Condition, and_, or_, parse_filters, render
from .errors import ForgeDBError, UnknownFieldError
from .schema import TableSchema
from .templating import render as render_sql
from .values import coerce_to_sql, decode_from_sql

if TYPE_CHECKING:
    from .model import Model

M = TypeVar("M", bound="Model")

_ORDER_DIRS = ("ASC", "DESC")


def _normalize_order(value: Any) -> tuple[str, str]:
    """Accept ``"field"``, ``"-field"``, ``"field DESC"`` or ``("field", dir)``."""
    if isinstance(value, str):
        text = value.strip()
        lower = text.lower()
        if text.startswith("-"):
            return text[1:].split()[0].strip(), "DESC"
        if lower.endswith((" desc", " descending")):
            field = text.rsplit(" ", 1)[0].strip()
            return field, "DESC"
        return text.split()[0].strip(), "ASC"
    if isinstance(value, (tuple, list)) and len(value) == 2:
        field, direction_raw = value
        if direction_raw in (True, 1):
            return str(field), "DESC"
        if direction_raw in (False, 0):
            return str(field), "ASC"
        direction = str(direction_raw).upper()
        if direction in ("DESC", "DESCENDING"):
            return str(field), "DESC"
        if direction in ("ASC", "ASCENDING"):
            return str(field), "ASC"
    raise ForgeDBError(f"invalid order_by item: {value!r}")


class Query(Generic[M]):
    """Chainable query: ``Model.filter(...).or_where(...).order_by(...).first()``.

    Generic in the model it queries, so ``Query[Post].all()`` is ``list[Post]``.
    """

    def __init__(
        self,
        model: type[M],
        *,
        condition: Condition | None = None,
        order_by: Sequence[Any] = (),
        limit: int | None = None,
        offset: int | None = None,
        distinct: bool = False,
    ) -> None:
        self.model = model
        self._schema: TableSchema = model.__schema__
        self._condition = condition
        self._order_by: list[tuple[str, str]] = [_normalize_order(o) for o in order_by]
        self._limit = limit
        self._offset = offset
        self._distinct = distinct

    def _db(self) -> Any:
        return self.model._db()

    def _clone(self, **changes: Any) -> Query[M]:
        return Query(
            self.model,
            condition=changes.get("condition", self._condition),
            order_by=changes.get("order_by", self._order_by),
            limit=changes.get("limit", self._limit),
            offset=changes.get("offset", self._offset),
            distinct=changes.get("distinct", self._distinct),
        )

    # ------------------------------------------------------------- building

    def filter(self, *conditions: Condition | None, **kwargs: Any) -> Query[M]:
        added = self._make_condition(conditions, kwargs)
        if added is None:
            return self
        combined = and_(self._condition, added) if self._condition is not None else added
        return self._clone(condition=combined)

    def or_where(self, *conditions: Condition | None, **kwargs: Any) -> Query[M]:
        added = self._make_condition(conditions, kwargs)
        if added is None:
            return self
        combined = or_(self._condition, added) if self._condition is not None else added
        return self._clone(condition=combined)

    def _make_condition(self, conditions: Sequence[Condition | None], kwargs: dict[str, Any]) -> Condition | None:
        items: list[Condition] = []
        for cond in conditions:
            if isinstance(cond, Condition):
                items.append(cond)
            elif cond is None:
                continue
            else:
                raise TypeError(f"expected Condition or None, got {type(cond).__name__}")
        items.extend(parse_filters(self._schema, kwargs))
        if not items:
            return None
        return and_(*items)

    def order_by(self, *fields: Any) -> Query[M]:
        normalized = self._order_by + [_normalize_order(f) for f in fields]
        seen: set[tuple[str, str]] = set()
        merged: list[tuple[str, str]] = []
        for field, direction in normalized:
            key = (field, direction)
            if key not in seen:
                seen.add(key)
                merged.append(key)
        return self._clone(order_by=merged)

    def limit(self, n: int | None) -> Query[M]:
        if n is not None and n < 0:
            raise ValueError(f"limit must be non-negative, got {n}")
        return self._clone(limit=n)

    def offset(self, n: int | None) -> Query[M]:
        if n is not None and n < 0:
            raise ValueError(f"offset must be non-negative, got {n}")
        return self._clone(offset=n)

    def distinct(self) -> Query[M]:
        return self._clone(distinct=True)

    # ----------------------------------------------------------- rendering

    def _where(self) -> tuple[str | None, dict[str, Any]]:
        return render(self._schema, self._condition)

    def _order_clause(self) -> str | None:
        if not self._order_by:
            return None
        parts: list[str] = []
        for field, direction in self._order_by:
            if field not in self._schema._column_map:
                raise UnknownFieldError(f"{self.model.__name__} has no field {field!r}")
            parts.append(f'"{field}" {direction}')
        return ", ".join(parts)

    def _select_sql(self, columns: Sequence[str] | None = None) -> tuple[str, dict[str, Any]]:
        where, params = self._where()
        return (
            render_sql(
                "select.sql.j2",
                table=self._schema.table,
                columns=columns,
                distinct=self._distinct,
                where=where,
                order_by=self._order_clause(),
                limit=self._limit,
                offset=self._offset,
            ),
            params,
        )

    # ------------------------------------------------------------ reading

    def values(self, *fields: str) -> list[dict[str, Any]]:
        """Return rows as plain dicts (decoded), only the requested columns."""
        for f in fields:
            if f not in self._schema._column_map:
                raise UnknownFieldError(f"{self.model.__name__} has no field {f!r}")
        sql, params = self._select_sql(columns=fields or None)
        rows = self._db().execute(sql, params).fetchall()
        return [
            {name: decode_from_sql(self._schema.column(name), row[name]) for name in fields or row.keys()}
            for row in rows
        ]

    def all(self) -> list[M]:
        sql, params = self._select_sql()
        rows = self._db().execute(sql, params).fetchall()
        return [self._to_model(dict(row)) for row in rows]

    def first(self) -> M | None:
        q = self._clone(limit=1)
        rows = q.all()
        return rows[0] if rows else None

    def count(self) -> int:
        where, params = self._where()
        sql = render_sql("count.sql.j2", table=self._schema.table, where=where)
        return self._db().execute(sql, params).fetchone()[0]

    def exists(self) -> bool:
        where, params = self._where()
        sql = render_sql("exists.sql.j2", table=self._schema.table, where=where)
        return bool(self._db().execute(sql, params).fetchone()[0])

    def _to_model(self, row: dict[str, Any]) -> M:
        decoded = {name: decode_from_sql(self._schema.column(name), value) for name, value in row.items()}
        return self.model.model_validate(decoded)

    def __iter__(self) -> Iterator[M]:
        return iter(self.all())

    # ------------------------------------------------------------ writing

    def update(self, **data: Any) -> int:
        """Bulk update every row matching this query; returns the affected count."""
        if self._condition is None:
            raise ForgeDBError("bulk update requires at least one filter condition")
        known = self._schema._column_map
        for field in data:
            if field not in known:
                raise UnknownFieldError(f"{self.model.__name__} has no field {field!r}")
            if self._schema.pk == field:
                raise ForgeDBError("cannot bulk-update the primary key field")
        set_columns = list(data)
        values = {f"set_{field}": coerce_to_sql(known[field], value) for field, value in data.items()}
        where, where_params = self._where()
        params: dict[str, Any] = {**where_params, **values}
        sql = render_sql("update.sql.j2", table=self._schema.table, set_columns=set_columns, where=where)
        return self._db().execute(sql, params).rowcount

    def delete(self) -> int:
        """Delete every row matching this query; returns the affected count."""
        if self._condition is None:
            raise ForgeDBError("refusing to delete every row without a filter condition")
        where, params = self._where()
        sql = render_sql("delete.sql.j2", table=self._schema.table, where=where)
        return self._db().execute(sql, params).rowcount
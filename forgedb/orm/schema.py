"""Pydantic model -> SQLite schema mapping.

The pydantic model is the single source of truth for
table/column/type/index/foreign-key information. This module is responsible for:

* deriving the table name from the class name
* mapping pydantic/python annotations to SQLite storage types
* deciding nullability, primary keys, defaults, unique/check constraints
* collecting index and foreign key definitions
"""

from __future__ import annotations

import re
import types
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from functools import cache
from typing import Any, Literal, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .errors import SchemaError

ReferentialAction = Literal["CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"]

_ACTIONS = frozenset({"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"})

_MISSING = object()

TEXT = "TEXT"
INTEGER = "INTEGER"
REAL = "REAL"
BLOB = "BLOB"

_CONTAINER_ORIGINS = {list, set, tuple, dict, Iterable, Sequence, Mapping}
_CONTAINER_BASES = {list, dict}


def snake_case(name: str) -> str:
    """Convert a CamelCase class name to snake_case."""
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


_UNION_ORIGINS = (Union, types.UnionType)


def is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin in _UNION_ORIGINS and type(None) in get_args(annotation)


def bare_type(annotation: Any) -> Any:
    """Strip a single Optional[...] wrapper, recursing if nested."""
    origin = get_origin(annotation)
    if origin in _UNION_ORIGINS:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1 and is_optional(args[0]):
            return bare_type(args[0])
        if len(args) == 1:
            return args[0]
    return annotation


def is_json_field(annotation: Any) -> bool:
    """True when the field must be stored as JSON text."""
    bare = bare_type(annotation)
    if bare is Any or bare is object:
        return True
    origin = get_origin(annotation)
    if origin in _CONTAINER_ORIGINS:
        return True
    if isinstance(bare, type):
        if issubclass(bare, (list, dict)) or issubclass(bare, BaseModel):
            return True
        if issubclass(bare, Enum):
            return False
    return False


def sqlite_type(annotation: Any) -> str:
    bare = bare_type(annotation)
    if isinstance(bare, type):
        if issubclass(bare, bool):
            return INTEGER
        if issubclass(bare, int):
            return INTEGER
        if issubclass(bare, float):
            return REAL
        if issubclass(bare, bytes):
            return BLOB
        if issubclass(bare, Enum):
            values = {member.value for member in bare}
            if not values:
                return TEXT
            if all(isinstance(v, bool) for v in values):
                return INTEGER
            if all(isinstance(v, int) for v in values):
                return INTEGER
            if all(isinstance(v, float) for v in values):
                return REAL
            return TEXT
        if issubclass(bare, (str, datetime, date, time, Decimal, UUID)):
            return TEXT
    return TEXT


def sql_default_literal(value: Any) -> str | None:
    """Render a constant python default as a SQL literal, or None to skip."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


@dataclass(frozen=True)
class Index:
    columns: tuple[str, ...]
    unique: bool = False
    name: str | None = None

    def index_name(self, table: str) -> str:
        if self.name:
            return self.name
        prefix = "ux" if self.unique else "ix"
        cols = "_".join(self.columns)
        return f"{prefix}_{table}_{cols}"


@dataclass(frozen=True)
class ForeignKeySpec:
    """Declarative foreign key metadata attached to a pydantic field."""

    target: type[BaseModel] | Literal["self"]
    column: str | None = None
    on_delete: str | None = None
    on_update: str | None = None


@dataclass(frozen=True)
class ForeignKeyConstraint:
    """A resolved foreign key constraint of a table."""

    column: str
    target: type[Any]
    target_table: str
    target_column: str
    on_delete: str | None = None
    on_update: str | None = None


def ForeignKey(
    target: type[BaseModel] | Literal["self"],
    *,
    column: str | None = None,
    on_delete: ReferentialAction | None = None,
    on_update: ReferentialAction | None = None,
    default: Any = PydanticUndefined,
    **field_kwargs: Any,
) -> Any:
    """Declare a column as a real SQLite foreign key.

    ``author_id: int = ForeignKey(User, on_delete="CASCADE")``

    ``target`` is the referenced model class, or ``"self"`` for a self reference.

    The returned value is a pydantic ``FieldInfo`` carrying the foreign key in
    its metadata, so the model class stays the single source of truth.
    """
    for action in (on_delete, on_update):
        if action is not None and action.upper() not in _ACTIONS:
            raise SchemaError(f"invalid referential action {action!r}")
    field_info = Field(default=default, **field_kwargs)
    field_info.metadata.append(
        ForeignKeySpec(
            target=target,
            column=column,
            on_delete=on_delete.upper() if on_delete else None,
            on_update=on_update.upper() if on_update else None,
        )
    )
    return field_info


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    pk: bool = False
    autoincrement: bool = False
    not_null: bool = False
    unique: bool = False
    default: str | None = None
    check: str | None = None
    json: bool = False
    annotation: Any = None
    foreign_key: ForeignKeyConstraint | None = None


@dataclass
class TableSchema:
    model: type
    table: str
    columns: tuple[Column, ...]
    pk: str | None
    indexes: tuple[Index, ...] = ()

    def __post_init__(self) -> None:
        self._column_map = {c.name: c for c in self.columns}
        self.json_columns = frozenset(c.name for c in self.columns if c.json)

    @property
    def foreign_keys(self) -> tuple[ForeignKeyConstraint, ...]:
        """Foreign keys of this table, derived from its columns."""
        return tuple(c.foreign_key for c in self.columns if c.foreign_key is not None)

    @property
    def column_map(self) -> dict[str, Column]:
        return self._column_map

    @property
    def pk_column(self) -> Column | None:
        if self.pk is None:
            return None
        return self._column_map.get(self.pk)

    def column(self, name: str) -> Column:
        try:
            return self._column_map[name]
        except KeyError:
            raise KeyError(f"unknown column {name!r}") from None


def _field_extra(field_info: FieldInfo) -> dict[str, Any]:
    extra = field_info.json_schema_extra
    return dict(extra) if isinstance(extra, dict) else {}


def table_name_for(model: type) -> str:
    """Table name of a model without building its whole schema."""
    return getattr(model, "__tablename__", None) or snake_case(model.__name__)


def pk_name_for(model: type[BaseModel]) -> str:
    """Primary key field of a model without building its whole schema.

    Kept separate from :func:`build_schema` so a foreign key may point at a
    model whose schema is still being built (self references).
    """
    pk_marker = getattr(model, "__pk__", _MISSING)
    pk = ("id" if "id" in model.model_fields else None) if pk_marker is _MISSING else pk_marker
    if pk is None or pk not in model.model_fields:
        raise SchemaError(f"{model.__name__} has no primary key to reference")
    return str(pk)


def foreign_key_spec(field_info: FieldInfo) -> ForeignKeySpec | None:
    for item in field_info.metadata:
        if isinstance(item, ForeignKeySpec):
            return item
    return None


def _resolve_foreign_key(name: str, spec: ForeignKeySpec, model: type[BaseModel]) -> ForeignKeyConstraint:
    model_name = model.__name__
    target = model if spec.target == "self" else spec.target
    if not (isinstance(target, type) and issubclass(target, BaseModel)):
        raise SchemaError(f"foreign key {model_name}.{name} must target a model class, got {target!r}")
    target_column = spec.column or pk_name_for(target)
    if target_column not in target.model_fields:
        raise SchemaError(f"foreign key {model_name}.{name} references unknown column {target_column!r}")
    return ForeignKeyConstraint(
        column=name,
        target=target,
        target_table=table_name_for(target),
        target_column=target_column,
        on_delete=spec.on_delete,
        on_update=spec.on_update,
    )


def build_schema(model: type[BaseModel]) -> TableSchema:
    """Build a TableSchema from a pydantic model class."""
    fields = model.model_fields
    abstract = model.__dict__.get("__abstract__", False)
    if abstract or not fields:
        raise SchemaError(f"{model.__name__} is abstract or defines no fields and cannot be mapped to a table")

    pk_marker = getattr(model, "__pk__", _MISSING)
    pk: str | None
    if pk_marker is _MISSING:
        pk = "id" if "id" in fields else None
    elif pk_marker is None:
        pk = None
    else:
        pk = str(pk_marker)
        if pk not in fields:
            raise SchemaError(f"__pk__={pk!r} is not a field of {model.__name__}")

    if pk is None:
        raise SchemaError(
            f"{model.__name__} has no primary key: declare an `id` field or set `__pk__` to one of its fields"
        )

    columns: list[Column] = []
    for name, field_info in fields.items():
        annotation = field_info.annotation
        json_field = is_json_field(annotation)
        col_type = sqlite_type(annotation)
        extra = _field_extra(field_info)

        is_pk = name == pk
        autoincrement = is_pk and col_type == INTEGER
        required = field_info.is_required()
        not_null = required and not is_pk and not is_optional(annotation)
        spec = foreign_key_spec(field_info)
        foreign_key = _resolve_foreign_key(name, spec, model) if spec is not None else None

        default = None
        if not _is_undefined(field_info.default):
            default = sql_default_literal(field_info.default)

        columns.append(
            Column(
                name=name,
                sql_type=col_type,
                pk=is_pk,
                autoincrement=autoincrement,
                not_null=not_null,
                unique=bool(extra.get("unique")),
                default=default,
                check=(extra.get("check") or None),
                json=json_field,
                annotation=annotation,
                foreign_key=foreign_key,
            )
        )

    single_indexes: list[Index] = []
    for column in columns:
        extra = _field_extra(fields[column.name])
        if extra.get("index") and not column.pk:
            single_indexes.append(Index(columns=(column.name,)))

    table = table_name_for(model)
    configured = getattr(model, "__indexes__", ())
    indexes: list[Index] = list(single_indexes)
    for entry in configured:
        if isinstance(entry, str):
            indexes.append(Index(columns=(entry,)))
        elif isinstance(entry, (tuple, list)):
            indexes.append(Index(columns=tuple(entry)))
        elif isinstance(entry, dict):
            cols = tuple(entry["fields"])
            indexes.append(
                Index(
                    columns=cols,
                    unique=bool(entry.get("unique")),
                    name=entry.get("name"),
                )
            )
        elif isinstance(entry, Index):
            indexes.append(entry)
        else:
            raise SchemaError(f"invalid __indexes__ entry for {model.__name__}: {entry!r}")
        _validate_index_columns(pk, columns, indexes[-1], model.__name__)

    return TableSchema(model=model, table=table, columns=tuple(columns), pk=pk, indexes=tuple(indexes))


@cache
def schema_for(model: type[BaseModel]) -> TableSchema:
    """Return the (cached) TableSchema for a model class."""
    return build_schema(model)


def _validate_index_columns(pk: str | None, columns: list[Column], index: Index, model_name: str) -> None:
    known = {c.name for c in columns}
    for name in index.columns:
        if name not in known:
            raise SchemaError(f"index on unknown field {name!r} of {model_name}")


def _is_undefined(value: Any) -> bool:
    return value is PydanticUndefined
"""ForgeDB ORM: a small, Pydantic v2 + Jinja2 powered SQLite ORM."""

from .conditions import Condition, and_, or_
from .database import Database, default_database, set_default_database
from .errors import (
    ForgeDBError,
    MissingPrimaryKeyError,
    SchemaError,
    UnknownFieldError,
    UnknownOperatorError,
)
from .model import Model
from .query import Query
from .relations import Relation, relation
from .schema import (
    ForeignKey,
    ForeignKeyConstraint,
    ForeignKeySpec,
    Index,
    ReferentialAction,
    TableSchema,
    schema_for,
)

__all__ = [
    "Condition",
    "Database",
    "ForeignKey",
    "ForeignKeyConstraint",
    "ForeignKeySpec",
    "ForgeDBError",
    "Index",
    "MissingPrimaryKeyError",
    "Model",
    "Query",
    "ReferentialAction",
    "Relation",
    "SchemaError",
    "TableSchema",
    "UnknownFieldError",
    "UnknownOperatorError",
    "and_",
    "default_database",
    "or_",
    "relation",
    "schema_for",
    "set_default_database",
]
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
from .schema import Index, TableSchema, schema_for

__all__ = [
    "Condition",
    "Database",
    "ForgeDBError",
    "Index",
    "MissingPrimaryKeyError",
    "Model",
    "Query",
    "SchemaError",
    "TableSchema",
    "UnknownFieldError",
    "UnknownOperatorError",
    "and_",
    "default_database",
    "or_",
    "schema_for",
    "set_default_database",
]
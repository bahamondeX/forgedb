"""Model: a Pydantic v2 model that maps to a SQLite table.

Class-level conventions:

* ``__tablename__``  - table name (default: snake_case of the class name)
* ``__pk__``         - primary key field (default: ``id`` when present)
* ``__indexes__``    - list of index definitions
* ``__abstract__``   - mark intermediate base classes to skip mapping
* ``Model.bind(db)`` - attach a specific :class:`~forgedb.orm.database.Database`
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from pydantic import BaseModel

from .conditions import Condition
from .database import Database, default_database
from .errors import ForgeDBError, MissingPrimaryKeyError
from .query import Query
from .schema import TableSchema, schema_for
from .templating import render as render_sql
from .values import coerce_to_sql

_NOARG = object()


class _SchemaDescriptor:
    def __get__(self, instance: Any, owner: type) -> TableSchema:
        return schema_for(owner)


class Model(BaseModel):
    """Base class for all ForgeDB models."""

    __abstract__: ClassVar[bool] = True
    __bound_db__: ClassVar[Database | None] = None
    __schema__ = _SchemaDescriptor()

    # ------------------------------------------------------------ plumbing

    @classmethod
    def bind(cls, database: Database) -> type[Model]:
        cls.__bound_db__ = database
        return cls

    @classmethod
    def _db(cls) -> Database:
        bound = cls.__bound_db__
        return bound if bound is not None else default_database()

    @classmethod
    def table_name(cls) -> str:
        return cls.__schema__.table

    # ------------------------------------------------------------- queries

    @classmethod
    def filter(cls, *conditions: Condition | None, **kwargs: Any) -> Query:
        return Query(cls).filter(*conditions, **kwargs)

    @classmethod
    def all(
        cls,
        *conditions: Condition | None,
        order_by: Any = (),
        limit: int | None = None,
        offset: int | None = None,
        **kwargs: Any,
    ) -> list[Model]:
        if isinstance(order_by, (list, tuple)):
            order_fields = list(order_by)
        else:
            order_fields = [order_by]
        return cls.filter(*conditions, **kwargs).order_by(*order_fields).limit(limit).offset(offset).all()

    @classmethod
    def get(cls, *conditions: Condition | None, **kwargs: Any) -> Model | None:
        return cls.filter(*conditions, **kwargs).first()

    @classmethod
    def first(
        cls,
        *conditions: Condition | None,
        order_by: Any = (),
        offset: int | None = None,
        **kwargs: Any,
    ) -> Model | None:
        if isinstance(order_by, (list, tuple)):
            order_fields = list(order_by)
        else:
            order_fields = [order_by]
        return cls.filter(*conditions, **kwargs).order_by(*order_fields).offset(offset).first()

    @classmethod
    def count(cls, *conditions: Condition | None, **kwargs: Any) -> int:
        return cls.filter(*conditions, **kwargs).count()

    @classmethod
    def exists(cls, *conditions: Condition | None, **kwargs: Any) -> bool:
        return cls.filter(*conditions, **kwargs).exists()

    # ------------------------------------------------------------- writing

    @classmethod
    def create(cls, **data: Any) -> Model:
        db = cls._db()
        db.ensure_schema(cls)
        schema = cls.__schema__
        pk = schema.pk
        pk_column = schema.pk_column

        if pk and pk not in data:
            if pk_column and pk_column.autoincrement:
                data[pk] = None
            elif pk_column and pk_column.sql_type == "TEXT":
                data[pk] = uuid.uuid4().hex
            else:
                raise ForgeDBError(
                    f"create() needs a value for the primary key {pk!r} of {cls.__name__}"
                )

        instance = cls.model_validate(data)
        raw = instance.model_dump()
        encoded = {col.name: coerce_to_sql(col, raw.get(col.name)) for col in schema.columns}
        columns = [col.name for col in schema.columns]
        params = [encoded[name] for name in columns]
        db.execute(render_sql("insert.sql.j2", table=schema.table, columns=columns), params)

        if pk is None:
            return instance
        pk_value = raw[pk]
        if pk_value is None and pk_column is not None and pk_column.autoincrement:
            pk_value = db.last_insert_rowid()
        return cls.get(**{pk: pk_value})

    @classmethod
    def update(cls, pk_value: Any = _NOARG, **kwargs: Any) -> Model | None:
        schema = cls.__schema__
        if schema.pk is None:
            raise MissingPrimaryKeyError(f"{cls.__name__} has no primary key")
        pk = schema.pk
        if pk_value is not _NOARG:
            if pk in kwargs:
                raise ForgeDBError("primary key given twice")
            where = {pk: pk_value}
        elif pk in kwargs:
            where = {pk: kwargs.pop(pk)}
        else:
            raise ForgeDBError(
                f"update() needs a primary key, e.g. {cls.__name__}.update({pk}=..., title=...) "
                f"or {cls.__name__}.update(<{pk}_value>, title=...)"
            )
        if kwargs:
            cls.filter(**where).update(**kwargs)
        return cls.get(**where)

    @classmethod
    def delete(cls, pk_value: Any = _NOARG, **kwargs: Any) -> int:
        schema = cls.__schema__
        if pk_value is not _NOARG:
            if schema.pk is None or pk_value is None:
                raise MissingPrimaryKeyError(f"{cls.__name__} has no primary key")
            where: dict[str, Any] = {schema.pk: pk_value}
        elif kwargs:
            where = dict(kwargs)
        else:
            raise ForgeDBError("delete() needs a primary key or a filter to know which rows to remove")
        return cls.filter(**where).delete()

    # --------------------------------------------------- instance operations

    def save(self) -> Model:
        cls = type(self)
        cls._db().ensure_schema(cls)
        schema = cls.__schema__
        if schema.pk is None:
            raise MissingPrimaryKeyError(f"{cls.__name__} has no primary key")
        pk = schema.pk
        pk_value = getattr(self, pk, None)
        raw = self.model_dump()
        if pk_value is not None and cls.filter(**{pk: pk_value}).exists():
            cls.filter(**{pk: pk_value}).update(**{k: v for k, v in raw.items() if k != pk})
            return self
        if pk_value is None:
            raw.pop(pk, None)
        return cls.create(**raw)

    def refresh(self) -> Model | None:
        cls = type(self)
        schema = cls.__schema__
        if schema.pk is None:
            raise MissingPrimaryKeyError(f"{cls.__name__} has no primary key")
        fresh = cls.get(**{schema.pk: getattr(self, schema.pk)})
        if fresh is None:
            return None
        for name, value in fresh.model_dump().items():
            setattr(self, name, value)
        return self
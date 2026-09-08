"""Model: a Pydantic v2 model that maps to a SQLite table.

Class-level conventions:

* ``__tablename__``  - table name (default: snake_case of the class name)
* ``__pk__``         - primary key field (default: ``id`` when present)
* ``__indexes__``    - list of index definitions
* ``__abstract__``   - mark intermediate base classes to skip mapping
* ``Model.bind(db)`` - attach a specific :class:`~forgedb.orm.database.Database`

Query-returning APIs are typed with :data:`typing.Self`, so ``Post.filter().all()``
is inferred as ``list[Post]`` without any cast.
"""

from __future__ import annotations

import sys
import uuid
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict
from pydantic._internal._model_construction import ModelMetaclass

from .conditions import Condition
from .database import Database, default_database
from .errors import ForgeDBError, MissingPrimaryKeyError
from .query import Query
from .relations import Relation
from .schema import TableSchema, schema_for
from .templating import render as render_sql
from .values import coerce_to_sql

if TYPE_CHECKING:
    from typing import Self

_NOARG = object()


class _SchemaDescriptor:
    def __get__(self, instance: Any, owner: type) -> TableSchema:
        return schema_for(owner)


def _is_relation_annotation(annotation: Any, module: str | None) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, Relation):
        return True
    if isinstance(annotation, str):
        head = annotation.split("[", 1)[0].strip()
        namespace = vars(sys.modules[module]) if module in sys.modules else {}
        resolved = namespace.get(head.rsplit(".", 1)[-1])
        if isinstance(resolved, type) and issubclass(resolved, Relation):
            return True
        return resolved is None and head.rsplit(".", 1)[-1] == "Relation"
    origin = getattr(annotation, "__origin__", None)
    return isinstance(origin, type) and issubclass(origin, Relation)


def _defining_namespace() -> dict[str, Any]:
    """Locals of the frame defining the class, so nested models resolve too."""
    frame = sys._getframe(2)
    return dict(frame.f_locals) if frame is not None else {}


class ModelMeta(ModelMetaclass):
    """Keeps ``Relation`` annotations out of the pydantic field set."""

    def __new__(mcls, name: str, bases: tuple[type, ...], namespace: dict[str, Any], **kwargs: Any) -> type:
        module = namespace.get("__module__")
        annotations = namespace.get("__annotations__", {})
        relations: dict[str, Any] = {}
        for field, annotation in list(annotations.items()):
            if not _is_relation_annotation(annotation, module):
                continue
            relations[field] = annotation
            del annotations[field]
            if not isinstance(namespace.get(field), Relation):
                namespace[field] = Relation()
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        if relations:
            cls.__relation_namespace__ = _defining_namespace()  # type: ignore[attr-defined]
        inherited: dict[str, Any] = {}
        for base in reversed(cls.__mro__[1:]):
            inherited.update(getattr(base, "__relation_annotations__", {}))
        cls.__relation_annotations__ = {**inherited, **relations}  # type: ignore[attr-defined]
        return cls


class Model(BaseModel, metaclass=ModelMeta):
    """Base class for all ForgeDB models."""

    model_config = ConfigDict(ignored_types=(Relation,))

    __abstract__: ClassVar[bool] = True
    __bound_db__: ClassVar[Database | None] = None
    __relation_annotations__: ClassVar[dict[str, Any]] = {}
    __relation_namespace__: ClassVar[dict[str, Any]] = {}
    __schema__ = _SchemaDescriptor()

    # ------------------------------------------------------------ plumbing

    @classmethod
    def bind(cls, database: Database) -> type[Self]:
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
    def filter(cls, *conditions: Condition | None, **kwargs: Any) -> Query[Self]:
        return Query(cls).filter(*conditions, **kwargs)

    @classmethod
    def all(
        cls,
        *conditions: Condition | None,
        order_by: Any = (),
        limit: int | None = None,
        offset: int | None = None,
        **kwargs: Any,
    ) -> list[Self]:
        if isinstance(order_by, (list, tuple)):
            order_fields = list(order_by)
        else:
            order_fields = [order_by]
        return cls.filter(*conditions, **kwargs).order_by(*order_fields).limit(limit).offset(offset).all()

    @classmethod
    def get(cls, *conditions: Condition | None, **kwargs: Any) -> Self | None:
        return cls.filter(*conditions, **kwargs).first()

    @classmethod
    def first(
        cls,
        *conditions: Condition | None,
        order_by: Any = (),
        offset: int | None = None,
        **kwargs: Any,
    ) -> Self | None:
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
    def create(cls, **data: Any) -> Self:
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
        created = cls.get(**{pk: pk_value})
        if created is None:
            raise ForgeDBError(f"{cls.__name__} row disappeared right after insert")
        return created

    @classmethod
    def update(cls, pk_value: Any = _NOARG, **kwargs: Any) -> Self | None:
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

    def save(self) -> Self:
        cls = type(self)
        cls._db().ensure_schema(cls)
        schema = cls.__schema__
        if schema.pk is None:
            raise MissingPrimaryKeyError(f"{cls.__name__} has no primary key")
        pk = schema.pk
        pk_value = getattr(self, pk, None)
        raw = self.model_dump()
        if pk_value is not None and cls.filter(**{pk: pk_value}).exists():
            validated = cls.model_validate(raw)
            cls.filter(**{pk: pk_value}).update(**{k: v for k, v in validated.model_dump().items() if k != pk})
            return self
        if pk_value is None:
            raw.pop(pk, None)
        return cls.create(**raw)

    def refresh(self) -> Self | None:
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
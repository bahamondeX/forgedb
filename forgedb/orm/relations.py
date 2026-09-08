"""Typed relationships between models.

A relationship is declared as an annotation whose type is ``Relation[...]``::

    class Post(Model):
        id: int | None = None
        author_id: int = ForeignKey(User, on_delete="CASCADE")
        author: Relation[User] = relation()

    class User(Model):
        id: int | None = None
        posts: Relation[list["Post"]] = relation()

Calling the accessor runs the query::

    post.author()   # User | None (statically ``User``)
    user.posts()    # list[Post]

``Relation`` attributes are not columns: the model metaclass removes them from
the pydantic field set (``model_config["ignored_types"]``) and keeps them as
descriptors. The foreign key column is inferred from the schema — the
declarative ``ForeignKey(...)`` metadata stays the single source of truth —
and can be pinned explicitly with ``relation(field="author_id")``.
"""

from __future__ import annotations

import sys
from typing import (
    TYPE_CHECKING,
    Any,
    ForwardRef,
    Generic,
    TypeVar,
    get_args,
    get_origin,
)

from .errors import SchemaError
from .schema import bare_type

if TYPE_CHECKING:
    from .model import Model

T = TypeVar("T")


class Relation(Generic[T]):
    """Typed accessor for a related model (or list of models)."""

    def __init__(self, *, field: str | None = None) -> None:
        self._field = field
        self._name: str | None = None
        self._owner: type[Model] | None = None
        self._instance: Model | None = None

    # ------------------------------------------------------------ descriptor

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, instance: Any, owner: type | None = None) -> Relation[T]:
        bound = Relation[T](field=self._field)
        bound._name = self._name
        bound._owner = owner if owner is not None else type(instance)
        bound._instance = instance
        return bound

    # -------------------------------------------------------------- resolving

    @property
    def name(self) -> str:
        if self._name is None:
            raise SchemaError("relation is not attached to a model")
        return self._name

    def _namespace(self) -> dict[str, Any]:
        owner = self._owner
        if owner is None:
            raise SchemaError(f"relation {self._name!r} is not attached to a model")
        namespace = dict(vars(sys.modules[owner.__module__]))
        namespace.update(getattr(owner, "__relation_namespace__", {}))
        namespace[owner.__name__] = owner
        return namespace

    def _evaluate(self, annotation: Any) -> Any:
        if isinstance(annotation, ForwardRef):
            annotation = annotation.__forward_arg__
        if isinstance(annotation, str):
            # evaluated in the model's own module namespace, like pydantic does
            return eval(annotation, self._namespace())
        return annotation

    def _annotation(self) -> Any:
        owner = self._owner
        if owner is None:
            raise SchemaError(f"relation {self._name!r} is not attached to a model")
        annotation = getattr(owner, "__relation_annotations__", {}).get(self.name)
        if annotation is None:
            raise SchemaError(f"{owner.__name__}.{self.name} has no relation annotation")
        return self._evaluate(annotation)

    def _target(self) -> tuple[type[Model], bool]:
        """Return ``(target_model, is_many)``."""
        args = get_args(self._annotation())
        if not args:
            raise SchemaError(f"{self.name} must be annotated as Relation[Model] or Relation[list[Model]]")
        inner = self._evaluate(args[0])
        if get_origin(inner) is list:
            (item,) = get_args(inner)
            return self._evaluate(item), True
        return bare_type(self._evaluate(inner)), False

    def _fk_column(self, source: type[Model], target: type[Model]) -> str:
        """Foreign key column on ``source`` that points at ``target``."""
        matches = [
            fk.column
            for fk in source.__schema__.foreign_keys
            if fk.target is target and (self._field is None or fk.column == self._field)
        ]
        if self._field is not None and self._field in {c.name for c in source.__schema__.columns} and not matches:
            return self._field
        if not matches:
            raise SchemaError(f"{source.__name__} has no foreign key to {target.__name__}")
        if len(matches) > 1:
            raise SchemaError(
                f"{source.__name__} has several foreign keys to {target.__name__}: "
                f"disambiguate with relation(field=...)"
            )
        return matches[0]

    # ---------------------------------------------------------------- calling

    def __call__(self) -> T:
        instance = self._instance
        owner = self._owner
        if instance is None or owner is None:
            raise SchemaError(f"relation {self._name!r} must be accessed on a model instance")
        target, many = self._target()
        if many:
            column = self._fk_column(target, owner)
            pk = _pk(owner)
            return target.filter(**{column: getattr(instance, pk)}).all()  # type: ignore[return-value]
        column = self._fk_column(owner, target)
        value = getattr(instance, column)
        if value is None:
            return None  # type: ignore[return-value]
        fk = owner.__schema__.column(column).foreign_key
        target_column = fk.target_column if fk is not None else _pk(target)
        return target.get(**{target_column: value})  # type: ignore[return-value]

    def __repr__(self) -> str:
        owner = self._owner.__name__ if self._owner is not None else "?"
        return f"<Relation {owner}.{self._name}>"


def _pk(model: type[Model]) -> str:
    pk = model.__schema__.pk
    if pk is None:
        raise SchemaError(f"{model.__name__} has no primary key")
    return pk


def relation(*, field: str | None = None) -> Any:
    """Declare a relationship accessor; ``field`` pins the foreign key column."""
    return Relation(field=field)

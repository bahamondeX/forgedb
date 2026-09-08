"""Tests for pydantic -> SQLite schema mapping."""

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest
from pydantic import Field

from forgedb import Model, SchemaError
from forgedb.orm.schema import schema_for, snake_case


def test_snake_case() -> None:
    assert snake_case("Document") == "document"
    assert snake_case("UserProfile") == "user_profile"
    assert snake_case("APIKey") == "api_key"
    assert snake_case("ForgeDB") == "forge_db"


def test_default_table_name_from_class() -> None:
    class UserProfile(Model):
        id: str

    schema = schema_for(UserProfile)
    assert schema.table == "user_profile"


def test_custom_tablename() -> None:
    from tests.models import Book

    assert schema_for(Book).table == "reading_list"


def test_scalar_type_mapping() -> None:
    class Types(Model):
        id: str
        flag: bool
        counter: int
        ratio: float
        blob: bytes
        when: datetime
        day: date
        moment: time
        uid: UUID
        amount: Decimal
        note: str | None = None

    schema = schema_for(Types)
    expected = {
        "id": "TEXT",
        "flag": "INTEGER",
        "counter": "INTEGER",
        "ratio": "REAL",
        "blob": "BLOB",
        "when": "TEXT",
        "day": "TEXT",
        "moment": "TEXT",
        "uid": "TEXT",
        "amount": "TEXT",
        "note": "TEXT",
    }
    for column in schema.columns:
        assert column.sql_type == expected[column.name], column.name


def test_json_fields_are_detected() -> None:
    class Doc(Model):
        id: str
        payload: dict
        tags: list[str]
        meta: object

    schema = schema_for(Doc)
    assert schema.column("payload").json
    assert schema.column("tags").json
    assert schema.column("meta").json
    assert schema.column("id").json is False


def test_nullability() -> None:
    class Req(Model):
        id: str
        required_field: str
        optional_field: str | None = None
        defaulted: int = 5

    schema = schema_for(Req)
    assert schema.column("required_field").not_null
    assert not schema.column("optional_field").not_null
    assert not schema.column("defaulted").not_null


def test_pk_detection_and_custom_pk() -> None:
    class DefaultPk(Model):
        id: int | None = None

    assert schema_for(DefaultPk).pk == "id"

    class Custom(Model):
        __pk__ = "slug"
        slug: str
        title: str

    assert schema_for(Custom).pk == "slug"
    assert schema_for(Custom).pk_column.autoincrement is False


def test_missing_custom_pk_raises() -> None:
    class Bad(Model):
        __pk__ = "nope"
        id: str

    with pytest.raises(SchemaError):
        schema_for(Bad)


def test_int_pk_autoincrement() -> None:
    class Row(Model):
        id: int | None = None
        value: str

    col = schema_for(Row).pk_column
    assert col is not None
    assert col.autoincrement


def test_defaults_extracted() -> None:
    class WithDefaults(Model):
        id: str
        enabled: bool = True
        quantity: int = 3
        ratio: float = 0.5
        label: str = "hi"
        dynamic: datetime = Field(default_factory=datetime.utcnow)

    schema = schema_for(WithDefaults)
    defaults = {c.name: c.default for c in schema.columns}
    assert defaults["enabled"] == "1"
    assert defaults["quantity"] == "3"
    assert defaults["ratio"] == "0.5"
    assert defaults["label"] == "'hi'"
    assert defaults["dynamic"] is None  # default_factory has no static SQL literal


def test_unique_and_index_flags_via_field_extra() -> None:
    class Thing(Model):
        id: str
        sku: str = Field(default="", json_schema_extra={"unique": True, "index": True})

    schema = schema_for(Thing)
    assert schema.column("sku").unique
    index_names = {idx.index_name(schema.table) for idx in schema.indexes}
    assert "ix_thing_sku" in index_names


def test_loads_composite_and_named_indexes() -> None:
    from tests.models import Book

    schema = schema_for(Book)
    names = {idx.index_name(schema.table) for idx in schema.indexes}
    assert "ix_reading_list_author" in names
    assert "ux_book_year_title" in names
    for idx in schema.indexes:
        assert idx.columns == ("author",) or idx.columns == ("year", "title")


def test_enum_column_type_maps_to_value_type() -> None:
    class Color(Enum):
        RED = "red"
        BLUE = "blue"

    class Shade(Model):
        id: str
        color: Color = Color.RED

    schema = schema_for(Shade)
    assert schema.column("color").sql_type == "TEXT"
    assert schema.column("color").json is False


def test_abstract_base_with_fields_is_forbidden() -> None:
    class BaseThing(Model):
        __abstract__ = True
        id: str

    with pytest.raises(SchemaError):
        schema_for(BaseThing)


def test_abstract_parent_with_concrete_child() -> None:
    class BaseThing(Model):
        __abstract__ = True
        id: str

    class Concrete(BaseThing):
        __abstract__ = False

    assert schema_for(Concrete).table == "concrete"


def test_model_with_no_fields_is_forbidden() -> None:
    class Empty(Model):
        pass

    with pytest.raises(SchemaError):
        schema_for(Empty)


def test_model_without_primary_key_field_is_forbidden() -> None:
    class NoPk(Model):
        name: str

    with pytest.raises(SchemaError):
        schema_for(NoPk)


def test_index_on_unknown_field_raises() -> None:
    class BadIndex(Model):
        __indexes__ = (("missing",),)

        id: str

    with pytest.raises(SchemaError):
        schema_for(BadIndex)
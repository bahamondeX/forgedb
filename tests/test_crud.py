"""End-to-end CRUD behaviour against a real SQLite database."""

from datetime import datetime, timezone

import pytest

from forgedb import ForgeDBError, Model
from forgedb.orm.errors import SchemaError


def test_create_and_get_by_pk(db):
    class Item(Model):
        id: str
        name: str = ""

    Item.bind(db)
    item = Item.create(id="a1", name="first")
    assert item.id == "a1"
    assert Item.get(id="a1") == item
    assert Item.get(name="first").id == "a1"
    assert Item.get(id="nope") is None


def test_str_pk_is_auto_generated_when_omitted(db):
    class Item(Model):
        id: str
        name: str = ""

    Item.bind(db)
    item = Item.create(name="no id given")
    assert item.id
    assert Item.count() == 1


def test_int_pk_autoincrement(db):
    from tests.models import Article

    first = Article.create(title="one")
    second = Article.create(title="two")
    assert first.id == 1
    assert second.id == 2
    assert Article.get(id=1).title == "one"


def test_explicit_int_pk_respected(db):
    from tests.models import Article

    Article.create(id=77, title="manual")
    assert Article.get(id=77).title == "manual"


def test_all_get_first_count_exists(db):
    from tests.models import Document

    Document.create(id="1", title="a")
    Document.create(id="2", title="b")
    assert Document.count() == 2
    assert {d.id for d in Document.all()} == {"1", "2"}
    assert Document.first(order_by="id").id == "1"
    assert Document.exists(title="a") is True
    assert Document.exists(title="zzz") is False


def test_instance_save_inserts_then_updates(db):
    from tests.models import Document

    doc = Document.model_construct(id="x", title="t0", content="")
    doc.save()
    assert Document.count() == 1

    doc.title = "t1"
    doc.save()
    assert Document.get(id="x").title == "t1"
    assert Document.count() == 1


def test_class_update_by_pk_value(db):
    from tests.models import Document

    Document.create(id="1", title="a")
    updated = Document.update("1", title="b")
    assert updated is not None
    assert updated.title == "b"
    assert Document.get(id="1").title == "b"


def test_class_update_by_kwargs(db):
    from tests.models import Document

    Document.create(id="1", title="a")
    Document.update(id="1", content="hello")
    assert Document.get(id="1").content == "hello"


def test_class_update_missing_returns_none(db):
    from tests.models import Document

    Document.bind(db)
    Document.create(id="seed", title="s")
    assert Document.update("ghost", title="x") is None
    assert Document.update("seed", title="y").title == "y"


def test_bulk_update_via_query(db):
    from tests.models import Document

    for i in range(3):
        Document.create(id=str(i), title="same")
    changed = Document.filter(title="same").update(title="changed")
    assert changed == 3
    assert Document.count(title="changed") == 3


def test_bulk_update_without_filter_raises(db):
    from tests.models import Document

    Document.create(id="1", title="a")
    with pytest.raises(ForgeDBError):
        Document.filter().update(title="b")

    from forgedb import Query

    with pytest.raises(ForgeDBError):
        Query(Document).update(title="b")


def test_bulk_update_unknown_field_raises(db):
    from tests.models import Document

    Document.create(id="1", title="a")
    with pytest.raises(ForgeDBError):
        Document.filter(title="a").update(nope="x")


def test_delete_by_pk_and_by_filter(db):
    from tests.models import Document

    Document.create(id="1", title="a")
    Document.create(id="2", title="a")
    Document.create(id="3", title="b")

    assert Document.delete("3") == 1
    assert Document.delete(title="a") == 2
    assert Document.count() == 0


def test_delete_with_no_filter_raises(db):
    from tests.models import Document

    Document.create(id="1", title="a")
    with pytest.raises(ForgeDBError):
        Document.delete()


def test_dates_roundtrip(db):
    from tests.models import Article

    stamp = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    Article.create(title="dated", published_at=stamp)
    got = Article.get(title="dated")
    assert got.published_at == stamp


def test_bool_roundtrip(db):
    from tests.models import Article

    Article.create(title="on", active=True)
    Article.create(title="off", active=False)
    assert Article.get(title="on").active is True
    assert Article.get(title="off").active is False
    assert Article.filter(active=True).count() == 1


def test_not_null_enforced_at_db_level(db):
    from tests.models import Document

    Document.bind(db)
    db.ensure_schema(Document)
    with pytest.raises(Exception) as excinfo:
        db.execute('INSERT INTO "document" ("id", "title") VALUES (?, ?)', ("x", None))
    assert "NOT NULL" in str(excinfo.value) or "constraint" in str(excinfo.value)


def test_unique_field_enforced(db):
    import sqlite3

    from tests.models import Indexed

    Indexed.bind(db)
    Indexed.create(id="1", name="n", email="e@x.dev")
    with pytest.raises(sqlite3.IntegrityError):
        Indexed.create(id="2", name="n2", email="e@x.dev")


def test_create_ignores_unknown_kwargs(db):
    from tests.models import Document

    doc = Document.create(id="1", title="a", totally_unexpected=True)
    assert doc.title == "a"


def test_model_without_pk_refuses_instance_ops(db):
    from forgedb.orm.schema import schema_for

    class NoPk(Model):
        title: str = ""

    with pytest.raises(SchemaError):
        schema_for(NoPk)


def test_table_and_indexes_created(db):
    from tests.models import Book

    Book.bind(db)
    db.ensure_schema(Book)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "reading_list" in tables
    indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "ix_reading_list_author" in indexes
    assert "ux_book_year_title" in indexes


def test_refresh_reloads_sql_defaults(db):
    from tests.models import Article

    article = Article.create(title="fresh")
    article.title = "local"
    refreshed = article.refresh()
    assert refreshed is not None
    assert refreshed.title == "fresh"
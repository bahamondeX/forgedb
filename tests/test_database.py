"""Tests for the Database layer: connections, transactions, SQL functions, DDL."""

import sqlite3

import pytest

from forgedb import Database, ForgeDBError, Model


def test_connection_and_row_factory(db):
    assert isinstance(db.connection, sqlite3.Connection)
    row = db.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
    assert dict(row) == {"one": 1}


def test_wal_applied_for_file_databases(tmp_path):
    path = tmp_path / "test.db"
    file_db = Database(str(path), journal_mode="WAL")
    mode = file_db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    file_db.close()


def test_foreign_keys_enabled(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transaction_commits(db, articles):
    db.create_tables(articles)
    with db.transaction():
        articles.create(title="in-tx")
    assert articles.count() == 1


def test_transaction_rolls_back_on_error(db, articles):
    db.create_tables(articles)
    with pytest.raises(RuntimeError), db.transaction():
        articles.create(title="doomed")
        raise RuntimeError("boom")
    assert articles.count() == 0


def test_nested_transactions_use_savepoints(db, articles):
    db.create_tables(articles)
    with db.transaction():
        articles.create(title="outer")
        with pytest.raises(RuntimeError), db.transaction():
            articles.create(title="inner")
            raise RuntimeError("inner boom")
        articles.create(title="still-here")
    # inner savepoint rolled back, outer transaction committed
    assert articles.count() == 2
    assert articles.filter(title="inner").count() == 0


def test_nested_transaction_success_commits_all(db, articles):
    db.create_tables(articles)
    with db.transaction():
        articles.create(title="outer")
        with db.transaction():
            articles.create(title="inner")
    assert articles.count() == 2


def test_bulk_writes_within_transaction_are_atomic(db, articles):
    db.create_tables(articles)
    try:
        with db.transaction():
            articles.create(title="a")
            articles.create(title="b")
            raise ForgeDBError("stop")
    except ForgeDBError:
        pass
    assert articles.count() == 0


def test_register_custom_function(db):
    db.register_function("shout", 1, lambda s: (s or "").upper())
    result = db.execute("SELECT shout('hi')").fetchone()[0]
    assert result == "HI"


def test_json_functions_available_through_json1(db):
    result = db.execute("SELECT json_extract('{\"a\": 5}', '$.a')").fetchone()[0]
    assert result == 5


def test_create_and_drop_tables(db, documents):
    db.ensure_schema(documents)
    assert db.table_exists(documents)
    db.drop_tables(documents)
    assert not db.table_exists(documents)


def test_create_tables_is_idempotent(db, documents):
    db.create_tables(documents)
    db.create_tables(documents)
    assert documents.count() == 0


def test_default_database_is_shared():
    from forgedb import set_default_database

    shared = Database(":memory:")
    set_default_database(shared)

    class SharedThing(Model):
        id: str

    SharedThing.create(id="1")
    assert shared.table_exists(SharedThing)


def test_close_marks_connection_invalid(db):
    conn = db.connection
    db.close()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_execute_reconnects_after_close(db):
    db.execute("SELECT 1")
    db.close()
    assert db.execute("SELECT 2").fetchone()[0] == 2
    db.close()
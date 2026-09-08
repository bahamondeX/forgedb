"""Tests for real SQLite foreign keys."""

from __future__ import annotations

import sqlite3

import pytest

from forgedb import Database, ForeignKey, Model, SchemaError, set_default_database
from forgedb.orm.schema import schema_for


class User(Model):
    id: int | None = None
    name: str = ""


class Post(Model):
    id: int | None = None
    title: str = ""
    author_id: int = ForeignKey(User, on_delete="CASCADE", on_update="CASCADE")


class Comment(Model):
    id: int | None = None
    body: str = ""
    author_id: int | None = ForeignKey(User, on_delete="SET NULL", default=None)


class Node(Model):
    id: int | None = None
    parent_id: int | None = ForeignKey("self", default=None)


@pytest.fixture
def database() -> Database:
    for model in (User, Post, Comment):
        model.__bound_db__ = None
    db = Database(":memory:")
    set_default_database(db)
    db.create_tables(User, Post, Comment)
    return db


def test_schema_exposes_foreign_keys() -> None:
    schema = schema_for(Post)
    (fk,) = schema.foreign_keys
    assert fk.column == "author_id"
    assert fk.target is User
    assert fk.target_table == "user"
    assert fk.target_column == "id"
    assert fk.on_delete == "CASCADE"
    assert fk.on_update == "CASCADE"
    assert schema.column("author_id").foreign_key is fk


def test_create_table_declares_the_foreign_key(database: Database) -> None:
    ddl = database.execute("SELECT sql FROM sqlite_master WHERE name = 'post'").fetchone()[0]
    assert 'FOREIGN KEY ("author_id") REFERENCES "user" ("id") ON DELETE CASCADE ON UPDATE CASCADE' in ddl

    rows = database.execute("PRAGMA foreign_key_list('post')").fetchall()
    assert [(row["table"], row["from"], row["to"], row["on_delete"]) for row in rows] == [
        ("user", "author_id", "id", "CASCADE")
    ]


def test_foreign_keys_pragma_is_on(database: Database) -> None:
    assert database.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_referenced_table_is_created_first() -> None:
    User.__bound_db__ = None
    Post.__bound_db__ = None
    db = Database(":memory:")
    set_default_database(db)
    db.create_tables(Post)
    assert db.table_exists(User)


def test_referential_integrity_is_enforced(database: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        Post.create(title="orphan", author_id=999)


def test_on_delete_cascade_removes_children(database: Database) -> None:
    user = User.create(name="Oscar")
    Post.create(title="ForgeDB", author_id=user.id)
    Post.create(title="Second", author_id=user.id)
    assert Post.count() == 2

    User.delete(user.id)
    assert Post.count() == 0


def test_nullable_foreign_key_and_set_null(database: Database) -> None:
    assert schema_for(Comment).column("author_id").not_null is False

    user = User.create(name="Oscar")
    comment = Comment.create(body="hi", author_id=user.id)
    assert Comment.create(body="anonymous").author_id is None

    User.delete(user.id)
    refreshed = Comment.get(id=comment.id)
    assert refreshed is not None
    assert refreshed.author_id is None


def test_self_referencing_foreign_key() -> None:
    Node.__bound_db__ = None
    db = Database(":memory:")
    set_default_database(db)
    db.create_tables(Node)

    root = Node.create()
    child = Node.create(parent_id=root.id)
    assert child.parent_id == root.id
    with pytest.raises(sqlite3.IntegrityError):
        Node.create(parent_id=999)


def test_invalid_referential_action_is_rejected() -> None:
    with pytest.raises(SchemaError):
        ForeignKey(User, on_delete="EXPLODE")


def test_foreign_key_to_a_non_model_is_rejected() -> None:
    class Broken(Model):
        id: int | None = None
        other_id: int = ForeignKey(int)

    with pytest.raises(SchemaError):
        schema_for(Broken)

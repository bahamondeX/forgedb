"""Tests for typed relationships."""

from __future__ import annotations

import pytest

from forgedb import (
    Database,
    ForeignKey,
    Model,
    Relation,
    SchemaError,
    relation,
    set_default_database,
)
from forgedb.orm.schema import schema_for


class Author(Model):
    id: int | None = None
    name: str = ""
    articles: Relation[list[Story]] = relation()


class Story(Model):
    id: int | None = None
    title: str = ""
    author_id: int | None = ForeignKey(Author, on_delete="CASCADE", default=None)
    author: Relation[Author] = relation()


class Tree(Model):
    id: int | None = None
    parent_id: int | None = ForeignKey("self", default=None)
    parent: Relation[Tree] = relation()
    children: Relation[list[Tree]] = relation()


@pytest.fixture
def database() -> Database:
    for model in (Author, Story, Tree):
        model.__bound_db__ = None
    db = Database(":memory:")
    set_default_database(db)
    db.create_tables(Author, Story, Tree)
    return db


def test_relations_are_not_columns() -> None:
    assert "author" not in Story.model_fields
    assert "articles" not in Author.model_fields
    assert {c.name for c in schema_for(Story).columns} == {"id", "title", "author_id"}


def test_forward_relation_returns_the_target(database: Database) -> None:
    author = Author.create(name="Oscar")
    story = Story.create(title="ForgeDB", author_id=author.id)
    assert story.author() == author


def test_forward_relation_is_none_when_the_key_is_null(database: Database) -> None:
    assert Story.create(title="orphan").author() is None


def test_reverse_relation_returns_a_list(database: Database) -> None:
    author = Author.create(name="Oscar")
    other = Author.create(name="Someone")
    Story.create(title="one", author_id=author.id)
    Story.create(title="two", author_id=author.id)
    Story.create(title="three", author_id=other.id)

    assert [s.title for s in author.articles()] == ["one", "two"]
    assert [s.title for s in other.articles()] == ["three"]


def test_self_referencing_relations(database: Database) -> None:
    root = Tree.create()
    child = Tree.create(parent_id=root.id)

    assert child.parent() == root
    assert [n.id for n in root.children()] == [child.id]


def test_relation_without_foreign_key_is_reported() -> None:
    class Unrelated(Model):
        id: int | None = None

    class Dangling(Model):
        id: int | None = None
        other: Relation[Unrelated] = relation()

    with pytest.raises(SchemaError):
        Dangling(id=1).other()


def test_relation_field_can_be_pinned() -> None:
    class Editor(Model):
        id: int | None = None

    class Page(Model):
        id: int | None = None
        writer_id: int | None = ForeignKey(Editor, default=None)
        reviewer_id: int | None = ForeignKey(Editor, default=None)
        reviewer: Relation[Editor] = relation(field="reviewer_id")

    db = Database(":memory:")
    set_default_database(db)
    db.create_tables(Editor, Page)

    editor = Editor.create()
    page = Page.create(reviewer_id=editor.id)
    assert page.reviewer() == editor

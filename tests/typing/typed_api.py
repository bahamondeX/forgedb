"""Static typing expectations, checked by mypy in ``tests/test_typing.py``.

This module is never imported at runtime; the ``reveal_type`` calls below are
mypy assertions about the inferred types of the public API.
"""

from __future__ import annotations

from forgedb import ForeignKey, Model, Query, Relation, relation


class User(Model):
    id: int | None = None
    name: str = ""
    posts: Relation[list[Post]] = relation()


class Post(Model):
    id: int | None = None
    title: str = ""
    author_id: int = ForeignKey(User, on_delete="CASCADE")
    author: Relation[User] = relation()


def check_query_api() -> None:
    reveal_type(Post.filter(title="x"))  # noqa: F821
    reveal_type(Post.filter(title="x").all())  # noqa: F821
    reveal_type(Post.filter(title="x").first())  # noqa: F821
    reveal_type(Post.all())  # noqa: F821
    reveal_type(Post.get(id=1))  # noqa: F821
    reveal_type(Post.first())  # noqa: F821
    reveal_type(Post.create(title="x", author_id=1))  # noqa: F821
    reveal_type(Post.update(1, title="y"))  # noqa: F821
    reveal_type(Post(title="x", author_id=1).save())  # noqa: F821
    reveal_type(Post(title="x", author_id=1).refresh())  # noqa: F821
    reveal_type(Post.filter().values("title"))  # noqa: F821


def check_relations() -> None:
    post = Post(title="x", author_id=1)
    reveal_type(post.author())  # noqa: F821
    reveal_type(User(name="Oscar").posts())  # noqa: F821


def check_assignments() -> None:
    posts: list[Post] = Post.filter(author_id=1).all()
    post: Post | None = Post.get(id=1)
    query: Query[Post] = Post.filter()
    author: User = Post(title="x", author_id=1).author()
    del posts, post, query, author

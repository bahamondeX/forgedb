"""Tests for the filter DSL, ordering, pagination and safety."""

import pytest

from forgedb import Model, UnknownFieldError, UnknownOperatorError, and_, or_


@pytest.fixture
def seed(documents):
    docs = [
        ("1", "Alpha", "hello world", 10),
        ("2", "Beta", "hello there", 20),
        ("3", "Gamma", "goodbye", 30),
    ]
    for doc_id, title, content, score in docs:
        documents.create(id=doc_id, title=title, content=content, metadata={"score": score})
    return documents


def test_equality_and_inequality(seed):
    assert seed.filter(title="Alpha").count() == 1
    assert seed.filter(title__ne="Alpha").count() == 2
    assert seed.filter(title="nope").count() == 0


def test_comparison_operators(seed):
    assert seed.filter(title__gt="Alpha").count() == 2
    assert seed.filter(title__ge="Alpha").count() == 3
    assert seed.filter(content__lt="hea").count() == 1  # only "goodbye" < "hea"
    assert seed.filter(content__lte="hello there").count() == 2


def test_in_and_not_in(seed):
    assert seed.filter(title__in=["Alpha", "Gamma"]).count() == 2
    assert seed.filter(title__not_in=["Alpha", "Gamma"]).count() == 1


def test_like_operators(seed):
    assert seed.filter(title__like="%mm%").count() == 1
    assert seed.filter(title__contains="pha").count() == 1
    assert seed.filter(title__startswith="Al").count() == 1
    assert seed.filter(title__endswith="mma").count() == 1


def test_isnull(db):
    class Thing(Model):
        id: str
        note: str | None = None

    Thing.bind(db)
    Thing.create(id="1", note=None)
    Thing.create(id="2", note="x")
    Thing.create(id="3", note="")
    assert Thing.filter(note__isnull=True).count() == 1
    assert Thing.filter(note__isnull=False).count() == 2
    assert Thing.filter(note=None).count() == 1


def test_multiple_kwargs_are_and(seed):
    assert seed.filter(title__contains="ph", content__contains="hello").count() == 1


def test_and_or_tree(seed):
    q = seed.filter(title__contains="a").filter(or_(title="Alpha", content="goodbye"))
    assert q.count() == 2


def test_and_or_builder_kwargs(seed):
    assert seed.filter(or_(title="Alpha", content="goodbye")).count() == 2
    assert seed.filter(and_(title="Alpha", content__contains="hello")).count() == 1


def test_or_where_chains(seed):
    q = seed.filter(title="Alpha").or_where(title="Gamma")
    assert q.count() == 2


def test_order_by_variants(db):
    class Row(Model):
        id: int | None = None
        name: str = ""

    Row.bind(db)
    for name in ("c", "a", "b"):
        Row.create(name=name)

    def names(q):
        rows = q.all() if not isinstance(q, list) else q
        return [r.name for r in rows]

    assert names(Row.all(order_by="name")) == ["a", "b", "c"]
    assert names(Row.all(order_by="-name")) == ["c", "b", "a"]
    assert names(Row.all(order_by="name DESC")) == ["c", "b", "a"]
    assert names(Row.filter().order_by(("name", "desc"))) == ["c", "b", "a"]
    assert names(Row.filter().order_by(("name", True))) == ["c", "b", "a"]


def test_limit_offset(seed):
    assert [d.id for d in seed.all(order_by="title", limit=2)] == ["1", "2"]
    assert [d.id for d in seed.all(order_by="title", limit=1, offset=2)] == ["3"]
    assert [d.id for d in seed.filter().order_by("title").limit(1).offset(1).all()] == ["2"]


def test_first_respects_order(seed):
    assert seed.first(order_by="-title").title == "Gamma"


def test_values_returns_decoded_dicts(seed):
    rows = seed.filter(title="Alpha").values("id", "title")
    assert rows == [{"id": "1", "title": "Alpha"}]


def test_unknown_field_raises(seed):
    with pytest.raises(UnknownFieldError):
        seed.filter(nope="x").all()
    with pytest.raises(UnknownFieldError):
        seed.get(nope="x")
    with pytest.raises(UnknownFieldError):
        seed.all(order_by="nope")
    with pytest.raises(UnknownFieldError):
        seed.filter(nope__gt=1).count()


def test_unknown_operator_raises(seed):
    with pytest.raises(UnknownOperatorError):
        seed.filter(title__bogus="x")


def test_sql_injection_value_is_safe(db, seed):
    payload = "x'); DROP TABLE document; --"
    assert seed.filter(title=payload).count() == 0
    assert seed.count() == 3
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "document" in tables


def test_sql_injection_in_field_name_rejected(seed):
    evil_key = "title'); DROP TABLE document; --"
    with pytest.raises(UnknownFieldError):
        seed.filter(**{evil_key: "x"}).all()


def test_empty_query_returns_all(seed):
    assert seed.filter().count() == 3
    assert seed.count() == 3


def test_iteration_on_query(seed):
    iterator = iter(seed.filter(title__contains="a"))
    titles = sorted(model.title for model in iterator)
    assert titles == ["Alpha", "Beta", "Gamma"]


def test_condition_repr(seed):
    cond = and_(or_(content="y"), title="x")
    assert "and" in repr(cond)
    assert "title='x'" in repr(cond)
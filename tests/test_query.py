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


def test_distinct(seed):
    class Dup(Model):
        id: int | None = None
        group: str = ""

    Dup.bind(seed._db())
    for g in ("a", "a", "b"):
        Dup.create(group=g)
    groups = [row["group"] for row in Dup.filter().distinct().order_by("group").values("group")]
    assert groups == ["a", "b"]


def test_distinct_count_counts_distinct_rows(seed):
    class Dup(Model):
        id: str
        group: str = ""

    Dup.bind(seed._db())
    Dup.create(id="1", group="a")
    Dup.create(id="2", group="a")
    Dup.create(id="3", group="b")
    assert Dup.count() == 3
    assert Dup.filter().distinct().count() == 3


def test_limit_zero_returns_nothing(seed):
    assert seed.filter().limit(0).all() == []
    assert seed.filter().limit(0).values() == []


def test_count_ignores_limit_and_offset(seed):
    assert seed.filter().limit(1).count() == 3
    assert seed.filter().offset(1).count() == 3


def test_limit_negative_raises(seed):
    with pytest.raises(ValueError, match="non-negative"):
        seed.filter().limit(-1)


def test_offset_negative_raises(seed):
    with pytest.raises(ValueError, match="non-negative"):
        seed.filter().offset(-1)


def test_offset_zero_is_noop(seed):
    all_docs = seed.all(order_by="id")
    offset_docs = seed.filter().offset(0).order_by("id").all()
    assert [d.id for d in all_docs] == [d.id for d in offset_docs]


def test_offset_without_limit_works(seed):
    docs = seed.filter().offset(1).all()
    assert len(docs) == 2
    first_with_offset = seed.first(order_by="id", offset=1)
    assert first_with_offset is not None
    assert first_with_offset.id == "2"


def test_condition_and_or_operators(seed):
    c1 = seed.filter(title="Alpha")._condition
    c2 = seed.filter(title="Beta")._condition
    combined = c1 & c2
    assert seed.filter(combined).count() == 0
    combined_or = c1 | c2
    assert seed.filter(combined_or).count() == 2


def test_or_where_with_conditions(seed):
    from forgedb import and_

    q = seed.filter(title="Alpha").or_where(and_(title="Gamma", content="goodbye"))
    assert q.count() == 2


def test_like_escape_special_characters(db):
    class Pattern(Model):
        id: str
        text: str = ""

    Pattern.bind(db)
    Pattern.create(id="1", text="100% done")
    Pattern.create(id="2", text="a_b test")
    Pattern.create(id="3", text="normal")
    Pattern.create(id="4", text="100% and a_b")

    assert Pattern.filter(text__contains="100%").count() == 2
    assert Pattern.filter(text__contains="a_b").count() == 2
    assert Pattern.filter(text__like="%100\\%%").count() == 2
    assert Pattern.filter(text__startswith="100%").count() == 2
    assert Pattern.filter(text__endswith="a_b").count() == 1


def test_empty_in_list(db):
    class Thing(Model):
        id: str
        name: str = ""

    Thing.bind(db)
    Thing.create(id="1", name="a")
    assert Thing.filter(name__in=[]).count() == 0
    assert Thing.filter(name__not_in=[]).count() == 1


def test_get_returns_none_for_nonexistent(seed):
    assert seed.get(id="nonexistent") is None


def test_first_returns_first_in_order(seed):
    first = seed.first(order_by="id")
    assert first is not None
    assert first.id == "1"


def test_first_returns_none_when_empty(db):
    class Empty(Model):
        id: str

    Empty.bind(db)
    db.ensure_schema(Empty)
    assert Empty.first() is None


def test_count_with_conditions(seed):
    assert seed.filter(title="Alpha").count() == 1
    assert seed.filter(title__ne="Alpha").count() == 2
    assert seed.filter().count() == 3


def test_exists_with_conditions(seed):
    assert seed.exists(title="Alpha") is True
    assert seed.exists(title="nope") is False
    assert seed.exists() is True
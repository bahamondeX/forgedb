"""Tests for JSON serialization of complex pydantic fields."""


from pydantic import BaseModel, Field

from forgedb import Model, or_
from tests.models import Article, Document


def test_dict_field_roundtrip(db):
    Document.bind(db)
    Document.create(id="1", title="t", metadata={"lang": "es", "n": 1})
    got = Document.get(id="1")
    assert got.metadata == {"lang": "es", "n": 1}


def test_mutable_default_isolated_per_instance(db):
    Document.bind(db)
    a = Document.create(id="1", title="a")
    b = Document.create(id="2", title="b")
    assert a.metadata == {} and b.metadata == {}
    b.metadata["x"] = 1
    b.save()
    assert Document.get(id="1").metadata == {}
    assert Document.get(id="2").metadata == {"x": 1}


def test_nested_model_roundtrip(db):
    from tests.models import MetaData

    Article.bind(db)
    Article.create(title="nested", meta=MetaData(lang="fr", tags=["a", "b"]))
    got = Article.get(title="nested")
    assert isinstance(got.meta, MetaData)
    assert got.meta.tags == ["a", "b"]


def test_list_and_nested_lists(db):
    class Layer(BaseModel):
        values: list[int]

    class Canvas(Model):
        id: str
        grid: list[list[int]] = Field(default_factory=list)
        layer: Layer

    Canvas.bind(db)
    Canvas.create(id="1", grid=[[1, 2], [3]], layer=Layer(values=[7]))
    got = Canvas.get(id="1")
    assert got.grid == [[1, 2], [3]]
    assert got.layer.values == [7]


def test_json_equality_filter(db):
    Document.bind(db)
    Document.create(id="1", title="a", metadata={"lang": "es"})
    Document.create(id="2", title="b", metadata={"lang": "en"})
    assert Document.filter(metadata={"lang": "es"}).count() == 1
    assert Document.filter(metadata={"lang": "fr"}).count() == 0


def test_json_extract_path_filter(db):
    Document.bind(db)
    for i, lang in enumerate(("es", "en", "es")):
        Document.create(id=str(i), title=f"d{i}", metadata={"lang": lang, "meta": {"k": i}})

    assert Document.filter(metadata__has_key="$.lang").count() == 3
    assert Document.filter(metadata__has_key="$.meta.k").count() == 3
    assert Document.filter(metadata__json=("$.lang", "es")).count() == 2
    assert Document.filter(metadata__json=("$.meta.k", 2)).count() == 1
    assert Document.filter(metadata__json=("$.absent", "x")).count() == 0


def test_json_in_filter(db):
    Document.bind(db)
    Document.create(id="1", title="a", metadata={"lang": "es"})
    Document.create(id="2", title="b", metadata={"lang": "en"})
    assert Document.filter(metadata__in=[{"lang": "es"}, {"lang": "en"}]).count() == 2


def test_json_updated_via_query(db):
    Document.bind(db)
    Document.create(id="1", title="a", metadata={"lang": "es"})
    Document.filter(id="1").update(metadata={"lang": "de"})
    assert Document.get(id="1").metadata == {"lang": "de"}


def test_or_with_json_and_scalar(db):
    Document.bind(db)
    Document.create(id="1", title="a", metadata={"lang": "es"})
    Document.create(id="2", title="b", metadata={"lang": "en"})
    q = Document.filter(or_(title="zzz", metadata__json=("$.lang", "es")))
    assert q.count() == 1
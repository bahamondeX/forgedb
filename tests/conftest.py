import pytest

from forgedb import Database, set_default_database


@pytest.fixture
def db() -> Database:
    from tests import models as test_models

    for cls in (test_models.Document, test_models.Article, test_models.Book, test_models.Indexed):
        cls.__bound_db__ = None

    database = Database(":memory:")
    set_default_database(database)
    return database


@pytest.fixture(autouse=True)
def _reset_default_database():
    yield
    set_default_database(None)


@pytest.fixture
def documents(db):
    from tests.models import Document

    Document.bind(db)
    return Document


@pytest.fixture
def articles(db):
    from tests.models import Article

    Article.bind(db)
    return Article
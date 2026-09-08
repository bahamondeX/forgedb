"""Shared model definitions used across the test suite."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from forgedb import Model


class Document(Model):
    id: str
    title: str
    content: str = ""
    metadata: dict = Field(default_factory=dict)

    def summary(self) -> str:
        return self.title


class MetaData(BaseModel):
    lang: str = "en"
    tags: list[str] = Field(default_factory=list)


class Article(Model):
    id: int | None = None
    title: str = ""
    author: str | None = None
    score: float = 0.0
    active: bool = True
    published_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    meta: MetaData = Field(default_factory=MetaData)


class Status(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Note(Model):
    id: str
    status: Status = Status.DRAFT
    priority: int = 0


class Book(Model):
    __tablename__ = "reading_list"
    __pk__ = "isbn"
    __indexes__ = (("author",), {"fields": ("year", "title"), "name": "ux_book_year_title"})

    isbn: str
    author: str
    title: str
    year: int = 0


class Indexed(Model):
    __indexes__ = (("name",),)

    id: str
    name: str = ""
    email: str = Field(default="", json_schema_extra={"unique": True, "index": True})
    age: int = Field(default=0, json_schema_extra={"check": "age >= 0"})
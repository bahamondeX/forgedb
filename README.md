# ForgeDB

A lightweight SQLite ORM for Python, built on three things you already have:

- [`sqlite3`](https://docs.python.org/3/library/sqlite3.html) — the standard library SQLite driver
- [Pydantic v2](https://docs.pydantic.dev/) — your models are validated data classes, no schema duplication
- [Jinja2](https://jinja.palletsprojects.com/) — every SQL statement is generated from small, inspectable
  templates, never hand-concatenated strings

ForgeDB maps a Pydantic model directly to a SQLite table, exposes a chainable
query API, stores JSON fields natively, and generates all SQL through templates
with every value bound as a parameter.

> **Status:** phase 1. Focus is a correct, self-contained core; heavier SQLite
> features (FTS5, views, virtual tables, migrations, connection pooling) are
> explicitly deferred. See [SQLite feature scope](#sqlite-feature-scope).

## Requirements

- Python 3.10+
- `pydantic>=2`
- `jinja2>=3`

## Installation

```bash
pip install -e ".[dev]"
```

Run the test suite, linter and type checker:

```bash
python -m pytest -q
ruff check forgedb tests
mypy
```

## Quickstart

```python
from datetime import datetime, UTC
from forgedb import Database, Model

class Article(Model):
    id: int | None = None          # INTEGER PRIMARY KEY AUTOINCREMENT
    title: str = ""
    author: str | None = None
    score: float = 0.0
    active: bool = True
    published_at: datetime | None = None

db = Database("articles.db")   # or Database(":memory:")
Article.bind(db)

# create -> returns a fresh instance whose autoincrement id is loaded back
a = Article.create(title="ForgeDB", author="Oscar", score=9.5)
a.published_at = datetime.now(UTC)
a.save()

Article.count()                          # 1
Article.filter(score__gte=9.0).all()     # [Article(title='ForgeDB', ...)]
Article.get(id=a.id).title               # 'ForgeDB'

# update / delete through the query API (bulk update needs a filter)
Article.filter(active=True).update(active=False)
Article.filter(title__like="%forge%").delete()
```

## Models

A ForgeDB model is a Pydantic v2 model subclassed from `forgedb.Model`.
The class is the single source of truth for the table, columns, types,
defaults, indexes and constraints. Tables (and their indexes) are created
automatically on first write via `Model.create()` / `Model.save()`, or eagerly
with `db.create_tables(Article)`.

### Class-level options

| Description | Field |
|---|---|
| Table name (default: `snake_case` of the class name) | `__tablename__` |
| Primary key field (default: `id` when present; required) | `__pk__` |
| Index definitions — `("col",)`, `("col1", "col2")`, `{"fields": (...), "name": ..., "unique": bool}` | `__indexes__` |
| Skip mapping for intermediate base classes | `__abstract__` |

```python
class Book(Model):
    __tablename__ = "reading_list"
    __pk__ = "isbn"
    __indexes__ = (
        ("author",),
        {"fields": ("year", "title"), "name": "ux_book_year_title", "unique": True},
    )
    isbn: str
    author: str
    title: str
    year: int = 0
```

Abstract bases let you share fields while keeping each concrete model its own
table:

```python
class BaseArticle(Model):
    __abstract__ = True
    id: int | None = None
    title: str = ""

class BlogPost(BaseArticle):
    __abstract__ = False
```

### Field-level constraints

Use `Field(..., json_schema_extra=...)` for per-column extras:

```python
from pydantic import Field

class User(Model):
    id: str
    email: str = Field(default="", json_schema_extra={"unique": True, "index": True})
    age: int = Field(default=0, json_schema_extra={"check": "age >= 0"})
```

Supported extras: `unique: bool`, `index: bool`, `check: "SQL expression"`.

### Foreign keys

`ForeignKey(...)` declares a real SQLite foreign key on a column. It returns a
Pydantic field, so the model class stays the single source of truth for columns,
primary key, indexes and constraints:

```python
from forgedb import ForeignKey, Model

class User(Model):
    id: int | None = None
    name: str

class Post(Model):
    id: int | None = None
    title: str
    author_id: int = ForeignKey(User, on_delete="CASCADE", on_update="CASCADE")
```

```sql
CREATE TABLE IF NOT EXISTS "post" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT DEFAULT NULL,
    "title" TEXT NOT NULL,
    "author_id" INTEGER NOT NULL,
    FOREIGN KEY ("author_id") REFERENCES "user" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
```

| Argument | Meaning |
|---|---|
| `target` | referenced model class, or `"self"` for a self reference |
| `column` | referenced column (default: the target's primary key) |
| `on_delete`, `on_update` | `CASCADE`, `SET NULL`, `SET DEFAULT`, `RESTRICT`, `NO ACTION` |
| `default`, other `Field(...)` kwargs | forwarded to Pydantic |

A nullable foreign key is an optional annotation with a default:

```python
class Comment(Model):
    id: int | None = None
    author_id: int | None = ForeignKey(User, on_delete="SET NULL", default=None)
```

Constraints are enforced by SQLite itself: every connection runs
`PRAGMA foreign_keys = ON` (disable with `Database(..., foreign_keys=False)`), a
dangling reference raises `sqlite3.IntegrityError`, and referenced tables are
created before the tables that point at them.

### Relationships

`Relation[...]` declares a typed accessor for related rows. It is not a column:
relations are excluded from the Pydantic field set and resolved through the
foreign key metadata above.

```python
from forgedb import ForeignKey, Model, Relation, relation

class User(Model):
    id: int | None = None
    posts: Relation[list["Post"]] = relation()

class Post(Model):
    id: int | None = None
    author_id: int = ForeignKey(User, on_delete="CASCADE")
    author: Relation[User] = relation()

post.author()     # User   (None when the foreign key is NULL)
user.posts()      # list[Post]
```

Each call runs one query; there is no lazy-loading cache yet. The foreign key
column is inferred from the schema and can be pinned with
`relation(field="reviewer_id")` when a model has several foreign keys to the
same target.

### Primary keys

| Declaration | Behaviour |
|---|---|
| `id: int | None = None` | `INTEGER PRIMARY KEY AUTOINCREMENT`; value generated by SQLite and loaded back after insert |
| `id: str` | `TEXT PRIMARY KEY`; a `uuid4().hex` is generated when the value is omitted |
| `__pk__ = "other_field"` | any single field becomes the primary key |
| no pk field | `SchemaError` at schema build — every table needs a primary key |

### Type mapping

| Python annotation | SQLite storage |
|---|---|
| `str`, `UUID`, `Decimal`, `datetime`, `date`, `time`, `Enum(str)` | `TEXT` |
| `int`, `bool`, `Enum` of ints/bools | `INTEGER` |
| `float` | `REAL` |
| `bytes` | `BLOB` |
| `dict`, `list`, nested `pydantic.BaseModel`, `Any`, `object` | `TEXT` (JSON) |

Dates/times are stored as ISO-8601 strings; enums store their `.value`;
`Decimal`/`UUID` store their string form. Values are always decoded back into
the annotated Python type — both when rows are materialized into Pydantic
models and when read through `Query.values()`.

## Databases

Bind a model to a specific `Database`, or rely on the process-wide default:

```python
from forgedb import Database, default_database, set_default_database

set_default_database(Database("app.sqlite"))
db = default_database()

db = Database(":memory:", journal_mode="WAL", foreign_keys=True, busy_timeout_ms=5000)
Article.bind(db)          # this database (`Article` overrides the default)

with db:                   # closes the connection on exit
    db.execute("SELECT 1")
    Article.create(title="x")
```

A model uses the database it is bound to, falling back to the default
database otherwise.

### Transactions

Explicit and automatically-nested transactions via `db.transaction()`.
Inner blocks become SQLite savepoints; the outer block controls the final
COMMIT/ROLLBACK. An exception rolls back the active savepoint level:

```python
with db.transaction():
    Article.create(title="one")
    with db.transaction():
        Article.create(title="two")
        raise RuntimeError("rolled back")     # only "two" is undone
    # outer block still commits "one"
```

> Note: DDL runs inside transactions too, so create tables before or via the
> same transaction you intend to keep.

### Escape hatch

`Database` exposes the raw connection primitives (`execute`, `executemany`,
`executescript`, `last_insert_rowid`) and `register_function()` to add custom
SQLite scalar functions.

## Querying

`Model.filter(...)` starts a chainable `Query`; steps build up conditions,
ordering and pagination, then a terminal method executes it.

Every model API preserves the concrete model type, so no cast is needed:

```python
posts: list[Post] = Post.filter(author_id=user.id).all()   # list[Post]
post: Post | None = Post.get(id=1)                         # Post | None
```

`Query` is generic (`Query[Post]`) and the `Model` classmethods are typed with
`Self`; `mypy`/`pyright` infer `list[Post]` and `Post | None` from
`Post.filter().all()` and `Post.filter().first()`. See `tests/typing/typed_api.py`.

```python
# building
q = Article.filter(active=True).or_where(score__gte=9.0).order_by("-score").limit(10).offset(0)

# terminal methods
q.all()                      # list[Article]
q.first()                    # Article | None  (LIMIT 1, no error when empty)
Article.get(id=x)            # Article | None  (shortcut for filter(...).first())
q.count()                    # int
q.exists()                   # bool
q.values("title", "score")   # list[dict] rows decoded to their Python types
```

`Model.all(...)`, `first(...)`, `count(...)`, `exists(...)`, `get(...)` accept
the same conditions as `filter`, plus `order_by` / `limit` / `offset` where
relevant.

`limit` and `offset` must be non-negative integers; a negative value raises
`ValueError`. `count()` counts the rows matching the filters and ignores any
`limit`/`offset` on the query. An `offset` without a `limit` is valid and
renders as `LIMIT -1 OFFSET n`.

### Ordering

`order_by` accepts flexible forms:

```python
Article.filter().order_by("score")            # ASC
Article.filter().order_by("-score")           # DESC
Article.filter().order_by("score DESC")       # DESC by keyword
Article.filter().order_by(("score", True))    # (field, truthy) => DESC
Article.filter().order_by("score", "-year")   # ascending keys, chained
```

### Operators

Keyword filters accept `field__operator=value`:

| Operator | SQL |
|---|---|
| `eq`, `ne` | `=`, `!=` |
| `gt`, `ge` / `gte`, `lt`, `le` / `lte` | `>`, `>=`, `<`, `<=` |
| `in`, `not_in` | `IN (...)` / `NOT IN (...)` |
| `like`, `not_like` | `LIKE` / `NOT LIKE` (values are passed through, so include your own `%`) |
| `contains`, `startswith`, `endswith` | `LIKE` with `%`/wildcards and `%`/`_` escapes added automatically |
| `isnull` | `IS NULL` (value `True`/`False`; a bare `field=None` in a filter means `IS NULL`) |
| `json` | `json_extract(col, '$.path')` — equality or presence |
| `has_key` | `json_extract(col, '$.path') IS NOT NULL` |

Guards:

- `filter(title__gte=...)` on an unknown field raises `UnknownFieldError`.
- An unknown operator raises `UnknownOperatorError`.
- All values are bound as parameters — user input never becomes SQL text.

### Complex conditions

`and_` / `or_` accept field filters as kwargs (matching `filter(...)`'s
operator syntax) and return `Condition` objects, which support the `&` and `|`
combinators. Compose them into arbitrary boolean trees:

```python
from forgedb import and_, or_

topic = and_(active=True, score__gte=9.0) | or_(author="Ada")
Article.filter(topic).all()
# -> WHERE ((("active" = :p0 AND "score" >= :p1) OR "author" = :p2))
```

Conditions mix with inline filters on the same query, so you can combine a
reusable sub-expression with per-query kwargs:

```python
Article.filter(published=True).or_where(and_(score__lt=3.0, status="draft")).all()
```

### JSON fields

`dict` / `list` / nested pydantic-model fields are stored as JSON text and
queried with the `json` / `has_key` operators:

```python
Article.filter(metadata__json=("$.lang", "es")).all()
Article.filter(meta__has_key="$.tags[0]").all()
Article.create(title="t", metadata={"lang": "es", "nested": [1, 2]})
```

## Writing data

### Class-level

```python
Article.create(title="new")                        # returns the saved instance
Article.update(5, title="renamed")                 # by pk value
Article.update(id=5, title="renamed")              # by pk keyword
Article.delete(5)                                  # by pk value
Article.delete(status="draft")                     # by any filter
```

For bulk updates/deletes, go through a filtered `Query` — a fully unfiltered
update or delete is refused (`ForgeDBError`), and the primary key cannot be
bulk-updated:

```python
Article.filter(active=True).update(active=False)   # returns affected count
Article.filter(published_at__lt="2024-01-01T00:00:00").delete()
```

### Instance-level

```python
a = Article.get(id=5)
a.title = "changed"
a.save()                        # INSERT when new, UPDATE by pk when existing
a.refresh()                     # reload defaults / SQL side-effect columns
```

`save()` inserts a fresh row when the primary key is unset or absent from the
table, otherwise it updates in place. Both paths run the current instance state
through Pydantic validation before writing.

Writing operations create the model's table on demand.

## SQLite feature scope

- **core:** transactions with savepoint nesting, WAL journal mode, JSON1
  (`json`/`json_extract`), parameter binding for every statement, custom SQL
  function registration, indexes, UNIQUE/CHECK constraints, rowid integer
  primary keys, foreign keys with `ON DELETE`/`ON UPDATE` actions.
- **future:** FTS5 full-text search, views, virtual tables, UPSERT, migrations,
  connection pooling / multi-threaded access.
- **out of scope (later ForgeDB phases):** RocksDB, MinIO, embeddings, FAISS/HNSW,
  document processing.

## Architecture

```
forgedb/
  __init__.py            # public API
  orm/
    model.py             # Model base class, CRUD, instance save()/refresh()
    database.py          # Database, connections, transactions, DDL
    query.py             # chainable query builder, generic in the model
    relations.py         # Relation[...] typed relationship accessors
    schema.py            # pydantic -> TableSchema mapping (types, pk, constraints, FKs)
    conditions.py        # filter DSL: and_/or_/>>> operators, rendering
    values.py            # python <-> SQLite serialization (JSON, ISO dates, ...)
    templating.py        # Jinja2 environment over templates/*.sql.j2
    templates/           # 8 SQL skeletons (create_table, insert, select, ...)
    errors.py            # ForgeDBError & typed subclasses
```

All SQL lives in `forgedb/orm/templates/*.sql.j2`; none is written inside
application code.

## Errors

| Exception | Raised when |
|---|---|
| `ForgeDBError` | generic misuse (e.g. guarded bulk update/delete) |
| `SchemaError` | invalid model → table mapping (e.g. no primary key) |
| `UnknownFieldError` | filtering/ordering on a field the model doesn't have |
| `UnknownOperatorError` | an operator name that isn't supported |
| `MissingPrimaryKeyError` | instance ops on a model without a primary key |

## License

Not yet assigned.
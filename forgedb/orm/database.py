"""Database: connection management, transactions and schema DDL.

SQLite feature classification for this phase:

* core            - transactions (explicit + savepoint nesting), WAL journal,
                    JSON1 (json/json_extract/-> used by conditions), parameter
                    binding everywhere, custom SQL function registration,
                    indexes, check/unique constraints, rowid integer pks
* future          - FTS5 full-text search, views, virtual tables, UPSERT,
                    migrations, connection pooling / multi-thread safety
* out of scope    - RocksDB, MinIO, embeddings, FAISS/HNSW, document
                    processing (ForgeDB later phases)
"""

from __future__ import annotations

import sqlite3
import types
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from .templating import render

if TYPE_CHECKING:
    from typing import Self

    from .model import Model


class Database:
    """Owns a single sqlite3 connection and knows how to materialize model schemas."""

    def __init__(
        self,
        path: str = ":memory:",
        *,
        journal_mode: str = "WAL",
        foreign_keys: bool = True,
        busy_timeout_ms: int = 5000,
        check_same_thread: bool = True,
    ) -> None:
        self.path = path
        self.journal_mode = journal_mode
        self.foreign_keys = foreign_keys
        self.busy_timeout_ms = busy_timeout_ms
        self.check_same_thread = check_same_thread
        self._connection: sqlite3.Connection | None = None
        self._created: set[type[Model]] = set()
        self._creating: set[type[Model]] = set()
        self._tx_depth = 0

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connect()
        assert self._connection is not None
        return self._connection

    def _connect(self) -> None:
        conn = sqlite3.connect(
            self.path,
            isolation_level=None,
            check_same_thread=self.check_same_thread,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        if self.foreign_keys:
            conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:" and self.journal_mode:
            conn.execute(f"PRAGMA journal_mode = {self.journal_mode}")
        self._connection = conn

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> Self:
        _ = self.connection
        return self

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        cursor = self.connection.execute(sql, params)
        return cursor

    def executemany(self, sql: str, seq_of_params: Any) -> sqlite3.Cursor:
        return self.connection.executemany(sql, seq_of_params)

    def executescript(self, sql: str) -> None:
        self.connection.executescript(sql)

    def last_insert_rowid(self) -> int:
        return self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]

    def register_function(self, name: str, nargs: int, func: Any, *, deterministic: bool = False) -> None:
        self.connection.create_function(name, nargs, func, deterministic=deterministic)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Explicit transaction; nests using SQLite savepoints."""
        if self._tx_depth == 0:
            self.connection.execute("BEGIN")
        else:
            savepoint = f"forgedb_sp_{self._tx_depth}"
            self.connection.execute(f"SAVEPOINT {savepoint}")
        self._tx_depth += 1
        try:
            yield
        except BaseException:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                self.connection.execute("ROLLBACK")
            else:
                self.connection.execute(f"ROLLBACK TO {savepoint}")
                self.connection.execute(f"RELEASE {savepoint}")
            raise
        else:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                self.connection.execute("COMMIT")
            else:
                self.connection.execute(f"RELEASE {savepoint}")

    # ------------------------------------------------------------------ DDL

    def ensure_schema(self, model: type[Model]) -> None:
        """Create the table and indexes for a model if not done for this database.

        Referenced tables are created first so foreign keys always resolve.
        """
        if model in self._created or model in self._creating:
            return
        schema = model.__schema__
        self._creating.add(model)
        try:
            for fk in schema.foreign_keys:
                self.ensure_schema(fk.target)
        finally:
            self._creating.discard(model)
        ddl = render(
            "create_table.sql.j2",
            table=schema.table,
            columns=schema.columns,
            foreign_keys=schema.foreign_keys,
        )
        self.execute(ddl)
        for index in schema.indexes:
            self.execute(
                render(
                    "create_index.sql.j2",
                    table=schema.table,
                    index=index,
                    index_name=index.index_name(schema.table),
                )
            )
        self._created.add(model)

    def create_tables(self, *models: type[Model]) -> None:
        for model in models:
            self.ensure_schema(model)

    def drop_tables(self, *models: type[Model]) -> None:
        for model in models:
            schema = model.__schema__
            self.execute(f'DROP TABLE IF EXISTS "{schema.table}"')
            self._created.discard(model)

    def table_exists(self, model: type[Model]) -> bool:
        schema = model.__schema__
        row = self.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? ORDER BY 1",
            (schema.table,),
        ).fetchone()
        return row is not None


_default_database: Database | None = None


def set_default_database(database: Database) -> None:
    global _default_database
    _default_database = database


def default_database() -> Database:
    """Return the module-level default database (an in-memory DB on first use)."""
    global _default_database
    if _default_database is None:
        _default_database = Database(":memory:")
    return _default_database
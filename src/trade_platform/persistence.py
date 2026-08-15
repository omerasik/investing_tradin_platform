"""Backend-neutral transactional boundary for durable repositories.

Domain code receives a transaction/repository capability rather than a database
flavour. SQLite remains for local deterministic tests; PostgreSQL is selected
explicitly for production only. No adapter contains financial or risk logic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


class PersistenceError(RuntimeError):
    pass


class PersistenceTarget(StrEnum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class Transaction(Protocol):
    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> Any: ...


class TransactionalDatabase(Protocol):
    def transaction(self) -> AbstractContextManager[Transaction]: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class SQLiteDatabase:
    database_path: str | Path = ":memory:"
    connection: sqlite3.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.connection = sqlite3.connect(str(self.database_path), check_same_thread=False)
        self.connection.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.connection:
                yield self.connection
        except PersistenceError:
            raise
        except sqlite3.Error as error:
            raise PersistenceError("sqlite_transaction_failed") from error

    def close(self) -> None:
        self.connection.close()


@dataclass(slots=True)
class PostgresDatabase:
    dsn: str
    connection: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.dsn.startswith(("postgres://", "postgresql://")):
            raise PersistenceError("postgres_dsn_required")
        try:
            import psycopg
        except ImportError as error:  # keeps local/unit installation lightweight
            raise PersistenceError("postgres_driver_unavailable") from error
        self.connection = psycopg.connect(self.dsn)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        try:
            with self.connection.transaction():
                yield self.connection
        except PersistenceError:
            raise
        except Exception as error:
            # Roll back every exception, but preserve domain fail-closed errors
            # so callers retain the exact integrity/risk rejection reason.
            import psycopg

            if not isinstance(error, psycopg.Error):
                raise
            raise PersistenceError("postgres_transaction_failed") from error

    def close(self) -> None:
        self.connection.close()


def open_database(target: PersistenceTarget, location: str | Path) -> TransactionalDatabase:
    if target is PersistenceTarget.SQLITE:
        return SQLiteDatabase(location)
    if target is PersistenceTarget.POSTGRES:
        return PostgresDatabase(str(location))
    raise PersistenceError("unsupported_persistence_target")

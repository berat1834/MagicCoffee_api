"""Veritabani baglanti/depo katmani.

Uretimde PostgreSQL (psycopg_pool.ConnectionPool), test ve yerel gelistirmede
SQLite (DATABASE_URL bos, MK_SQLITE_PATH dolu) kullanilir. SQL metinleri psycopg
bicimindeki `%s` yer tutucusuyla yazilir; SQLite'ta `?`'ye cevrilir ve
`FOR UPDATE` kilit ekleri atilir (SQLite'ta yazma islemleri BEGIN IMMEDIATE ile
tek yazar olarak calisir).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

BUSY_MESSAGE = "Sunucu meşgul, lütfen tekrar deneyin."
APPLICATION_NAME = "magickiosk-api"

ISOLATION_MARKER = "full" + "moon"
FORBIDDEN_DATABASE_MARKERS = frozenset({ISOLATION_MARKER, "kasadb_dev"})


class DatabaseBusy(Exception):
    """Havuzdan belirtilen surede baglanti alinamadi."""


class DatabaseNotConfigured(RuntimeError):
    """Ne DATABASE_URL ne de MK_SQLITE_PATH tanimli."""


def _integrity_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [sqlite3.IntegrityError]
    try:
        import psycopg

        errors.append(psycopg.IntegrityError)
    except ImportError:  # pragma: no cover - psycopg requirements'ta var
        pass
    return tuple(errors)


# Benzersizlik/yabanci anahtar ihlalleri (iki motor icin).
INTEGRITY_ERRORS = _integrity_errors()


def allowed_database_host() -> str:
    return os.getenv("DATABASE_ALLOWED_HOST", "").strip().lower().rstrip(".")


def validate_database_url(database_url: str, allowed_host: str | None = None) -> None:
    if not database_url:
        return
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.username or parsed.path in {"", "/"}:
        raise RuntimeError("DATABASE_URL must identify a dedicated MagicCoffee PostgreSQL database and user.")
    identity = f"{parsed.hostname}/{parsed.username}{parsed.path}".lower()
    if any(marker in identity for marker in FORBIDDEN_DATABASE_MARKERS):
        raise RuntimeError("DATABASE_URL points to a forbidden external project.")
    host = allowed_database_host() if allowed_host is None else allowed_host
    if host and parsed.hostname.lower().rstrip(".") != host:
        raise RuntimeError("DATABASE_URL does not point to the approved MagicCoffee database host.")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


_PLACEHOLDER = re.compile(r"%s")
_FOR_UPDATE = re.compile(r"\s+FOR\s+UPDATE\b", re.IGNORECASE)


class Result:
    def __init__(self, rows: list[dict[str, Any]] | None, rowcount: int):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class Conn:
    """Iki motor icin ortak, ince baglanti sarmalayici."""

    def __init__(self, raw: Any, dialect: str):
        self.raw = raw
        self.dialect = dialect

    @property
    def is_postgres(self) -> bool:
        return self.dialect == "postgres"

    def adapt(self, sql: str) -> str:
        if self.is_postgres:
            return sql
        return _PLACEHOLDER.sub("?", _FOR_UPDATE.sub("", sql))

    def execute(self, sql: str, params: tuple | list = ()) -> Result:
        if self.is_postgres:
            cursor = self.raw.execute(sql, tuple(params))
            rows = cursor.fetchall() if cursor.description else None
            return Result(rows, cursor.rowcount)
        cursor = self.raw.execute(self.adapt(sql), tuple(params))
        rows = None
        if cursor.description:
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return Result(rows, cursor.rowcount)

    def table_exists(self, name: str) -> bool:
        if self.is_postgres:
            row = self.execute("SELECT to_regclass(%s) AS oid", (name,)).fetchone()
            return bool(row and row["oid"])
        row = self.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = %s", (name,)).fetchone()
        return bool(row)


class Database:
    def __init__(self) -> None:
        self.dialect: str | None = None
        self.pool: Any = None
        self.sqlite_path: str | None = None
        self._sqlite_slots: threading.BoundedSemaphore | None = None
        self._sqlite_max = 0
        self.timeout = 10.0

    # ---- yasam dongusu -------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.dialect is not None

    def open(self) -> None:
        if self.is_open:
            return
        database_url = os.getenv("DATABASE_URL", "").strip()
        sqlite_path = os.getenv("MK_SQLITE_PATH", "").strip()
        self.timeout = _env_float("DB_POOL_TIMEOUT", 10.0)
        min_size = max(0, _env_int("DB_POOL_MIN_SIZE", 1))
        max_size = max(1, _env_int("DB_POOL_MAX_SIZE", 5), min_size)
        if database_url:
            validate_database_url(database_url)
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            statement_timeout = _env_int("DB_STATEMENT_TIMEOUT_MS", 15000)
            self.pool = ConnectionPool(
                database_url,
                min_size=min_size,
                max_size=max_size,
                timeout=self.timeout,
                max_idle=_env_float("DB_POOL_MAX_IDLE", 300.0),
                max_lifetime=_env_float("DB_POOL_MAX_LIFETIME", 1800.0),
                check=ConnectionPool.check_connection,
                kwargs={
                    "row_factory": dict_row,
                    "application_name": APPLICATION_NAME,
                    "options": f"-c statement_timeout={statement_timeout}",
                },
                name=APPLICATION_NAME,
                open=False,
            )
            self.pool.open(wait=False)
            self.dialect = "postgres"
            return
        if sqlite_path:
            self.sqlite_path = sqlite_path
            self._sqlite_max = max_size
            self._sqlite_slots = threading.BoundedSemaphore(max_size)
            raw = sqlite3.connect(sqlite_path, timeout=30)
            try:
                raw.execute("PRAGMA journal_mode=WAL")
            finally:
                raw.close()
            self.dialect = "sqlite"
            return
        raise DatabaseNotConfigured(
            "DATABASE_URL tanımlı değil. PostgreSQL bağlantı adresini (veya yerel test için MK_SQLITE_PATH) ayarlayın."
        )

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
        self.pool = None
        self.dialect = None
        self.sqlite_path = None
        self._sqlite_slots = None

    # ---- baglanti -------------------------------------------------------
    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[Conn]:
        """Kisa omurlu baglanti/transaction. Blok sonunda commit, hata olursa rollback.

        `write=True` SQLite'ta BEGIN IMMEDIATE ile yazma kilidini en basta alir;
        PostgreSQL'de satir kilitleri SELECT ... FOR UPDATE ile alinir.
        """
        if not self.is_open:
            raise DatabaseNotConfigured("Veritabanı bağlantısı açılmadı.")
        if self.dialect == "postgres":
            from psycopg_pool import PoolTimeout

            try:
                raw = self.pool.getconn(timeout=self.timeout)
            except PoolTimeout as error:
                raise DatabaseBusy(BUSY_MESSAGE) from error
            try:
                with raw.transaction():
                    yield Conn(raw, "postgres")
            finally:
                self.pool.putconn(raw)
            return

        slots = self._sqlite_slots
        if slots is None or not slots.acquire(timeout=self.timeout):
            raise DatabaseBusy(BUSY_MESSAGE)
        try:
            raw = sqlite3.connect(self.sqlite_path, timeout=30, isolation_level=None)
            try:
                raw.execute("PRAGMA busy_timeout = 30000")
                raw.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    yield Conn(raw, "sqlite")
                except BaseException:
                    raw.execute("ROLLBACK")
                    raise
                else:
                    raw.execute("COMMIT")
            finally:
                raw.close()
        finally:
            slots.release()

    def stats(self) -> dict[str, int]:
        if self.dialect == "postgres" and self.pool is not None:
            stats = self.pool.get_stats()
            return {
                "size": int(stats.get("pool_size", 0)),
                "available": int(stats.get("pool_available", 0)),
                "waiting": int(stats.get("requests_waiting", 0)),
            }
        if self.dialect == "sqlite" and self._sqlite_slots is not None:
            available = getattr(self._sqlite_slots, "_value", self._sqlite_max)
            return {"size": self._sqlite_max, "available": int(available), "waiting": 0}
        return {"size": 0, "available": 0, "waiting": 0}


db = Database()

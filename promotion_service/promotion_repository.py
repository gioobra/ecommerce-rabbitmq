"""Persistência de interesses de promoções em SQLite."""

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB: Path = Path(__file__).resolve().parent / "promotion.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS interesses (
    email       TEXT    NOT NULL,
    categoria   TEXT    NOT NULL,
    client_id   TEXT,
    criado_em   INTEGER NOT NULL,
    PRIMARY KEY (email, categoria)
);
CREATE INDEX IF NOT EXISTS idx_interesses_categoria ON interesses (categoria);
"""


def _now() -> int:
    return int(time.time() * 1000)


class PromotionRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or os.getenv("PROMOTION_DB") or DEFAULT_DB)
        self._mem_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", timeout=10, isolation_level=None, check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_quietly(self, conn: sqlite3.Connection) -> None:
        if self._mem_conn is None:
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            self._close_quietly(conn)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            if self._mem_conn is None:
                conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
        finally:
            self._close_quietly(conn)

    def save_interests(self, email: str, categorias: list[str], client_id: str | None = None) -> list[str]:
        """Salva ou atualiza as categorias de interesse de um e-mail de forma atômica."""
        email_clean = email.strip().lower()
        now = _now()
        cats_clean = sorted(list({c.strip() for c in categorias if c.strip()}))

        with self._tx() as conn:
            for cat in cats_clean:
                conn.execute(
                    "INSERT INTO interesses (email, categoria, client_id, criado_em) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(email, categoria) DO UPDATE SET client_id = excluded.client_id, criado_em = excluded.criado_em",
                    (email_clean, cat, client_id, now),
                )
        return cats_clean

    def list_subscribers_by_category(self, categoria: str) -> list[str]:
        """Retorna a lista de e-mails interessados em uma determinada categoria."""
        cat_clean = categoria.strip().lower()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT email FROM interesses WHERE LOWER(categoria) = ? ORDER BY email",
                (cat_clean,),
            ).fetchall()
            return [str(r["email"]) for r in rows]
        finally:
            self._close_quietly(conn)

    def list_categories_with_subscribers(self) -> list[str]:
        """Retorna todas as categorias que possuem pelo menos um cliente inscrito."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT categoria FROM interesses ORDER BY categoria"
            ).fetchall()
            return [str(r["categoria"]) for r in rows]
        finally:
            self._close_quietly(conn)

    def get_interests_by_email(self, email: str) -> list[str]:
        """Retorna as categorias de interesse de um e-mail."""
        email_clean = email.strip().lower()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT categoria FROM interesses WHERE LOWER(email) = ? ORDER BY categoria",
                (email_clean,),
            ).fetchall()
            return [str(r["categoria"]) for r in rows]
        finally:
            self._close_quietly(conn)

    def list_all_interests(self) -> list[dict[str, Any]]:
        """Lista todas as inscrições registradas."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT email, categoria, client_id, criado_em FROM interesses ORDER BY criado_em DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            self._close_quietly(conn)

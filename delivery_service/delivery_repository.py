"""Microservicço entrega gera uma nota fiscal por pedido"""

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB: Path = Path(__file__).resolve().parent / "delivery.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entregas (
    numero            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          TEXT NOT NULL UNIQUE,
    charge_id         TEXT,
    valor             REAL,
    enviado_publicado INTEGER NOT NULL DEFAULT 0,
    criado_em         INTEGER NOT NULL,
    atualizado_em     INTEGER NOT NULL
);
"""


def _now() -> int:
    return int(time.time() * 1000)


def _view(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["nota_fiscal"] = f"NF-{d['numero']:06d}"
    d["codigo_rastreio"] = f"BR{d['numero']:09d}"
    return d


class DeliveryRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or os.getenv("DELIVERY_DB") or DEFAULT_DB)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

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
            conn.close()

    def get(self, order_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM entregas WHERE order_id = ?", (order_id,)).fetchone()
            return _view(row) if row else None
        finally:
            conn.close()

    def emit_invoice(self, order_id: str, valor: float | None = None, charge_id: str | None = None) -> dict[str, Any]:
        """Emite a nota fiscal do pedido. Idempotente: o mesmo pedido sempre recebe a mesma nota."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM entregas WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO entregas (order_id, charge_id, valor, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?)",
                    (order_id, charge_id, valor, _now(), _now()),
                )
                row = conn.execute("SELECT * FROM entregas WHERE order_id = ?", (order_id,)).fetchone()
            return _view(row)

    def mark_published(self, order_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE entregas SET enviado_publicado = 1, atualizado_em = ? WHERE order_id = ?",
                         (_now(), order_id))
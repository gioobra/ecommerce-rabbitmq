"""Persistência do MS Pagamento (SQLite): uma cobrança por pedido."""

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB: Path = Path(__file__).resolve().parent / "payment.db"

PENDENTE, APROVADO, RECUSADO, CANCELADO = "PENDENTE", "APROVADO", "RECUSADO", "CANCELADO"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cobrancas (
    order_id            TEXT PRIMARY KEY,
    valor               REAL,
    charge_id           TEXT,
    checkout_url        TEXT,
    callback_token      TEXT,
    status              TEXT NOT NULL CHECK (status IN ('PENDENTE','APROVADO','RECUSADO','CANCELADO')),
    link_publicado      INTEGER NOT NULL DEFAULT 0,
    resultado_publicado INTEGER NOT NULL DEFAULT 0,
    criado_em           INTEGER NOT NULL,
    atualizado_em       INTEGER NOT NULL
);
"""


def _now() -> int:
    return int(time.time() * 1000)


class PaymentRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or os.getenv("PAYMENT_DB") or DEFAULT_DB)
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
            row = conn.execute("SELECT * FROM cobrancas WHERE order_id = ?", (order_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def create(self, order_id: str, valor: float, charge_id: str, checkout_url: str, token: str) -> bool:
        """Grava a cobrança. Retorna False se já existia linha (duplicado ou pedido cancelado)."""
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO cobrancas (order_id, valor, charge_id, checkout_url, callback_token,"
                " status, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, 'PENDENTE', ?, ?)",
                (order_id, valor, charge_id, checkout_url, token, _now(), _now()),
            )
            return cur.rowcount == 1

    def mark_link_published(self, order_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE cobrancas SET link_publicado = 1, atualizado_em = ? WHERE order_id = ?",
                         (_now(), order_id))

    def mark_result_published(self, order_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE cobrancas SET resultado_publicado = 1, atualizado_em = ? WHERE order_id = ?",
                         (_now(), order_id))

    def cancel(self, order_id: str) -> str:
        """Pedido cancelado. Retorna 'CANCELADA', 'MARCADO' (pedido ainda
        sem cobrança) ou 'IGNORADO'"""
        with self._tx() as conn:
            row = conn.execute("SELECT status FROM cobrancas WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO cobrancas (order_id, status, criado_em, atualizado_em)"
                             " VALUES (?, 'CANCELADO', ?, ?)", (order_id, _now(), _now()))
                return "MARCADO"
            if row["status"] == PENDENTE:
                conn.execute("UPDATE cobrancas SET status = 'CANCELADO', atualizado_em = ? WHERE order_id = ?",
                             (_now(), order_id))
                return "CANCELADA"
            return "IGNORADO"

    def decide(self, order_id: str, new_status: str) -> str:
        """Aplica a decisão do provedor. Resultados: APLICADO, REPETIDO, CONFLITO, CANCELADO, DESCONHECIDO."""
        with self._tx() as conn:
            row = conn.execute("SELECT status FROM cobrancas WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                return "DESCONHECIDO"
            if row["status"] == PENDENTE:
                conn.execute("UPDATE cobrancas SET status = ?, atualizado_em = ? WHERE order_id = ?",
                             (new_status, _now(), order_id))
                return "APLICADO"
            if row["status"] == CANCELADO:
                return "CANCELADO"
            return "REPETIDO" if row["status"] == new_status else "CONFLITO"
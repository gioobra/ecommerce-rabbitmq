"""Persistência do estoque em SQLite: produtos, pedidos processados e reservas."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB: Path = Path(__file__).resolve().parent / "inventory.db"

SEED: list[tuple[str, str, float, int]] = [
    ("notebook", "Computadores", 4500.00, 5),
    ("mouse", "Periféricos", 80.00, 10),
    ("teclado", "Periféricos", 150.00, 8),
    ("monitor", "Monitores", 1200.00, 2),
    ("headset", "Periféricos", 250.00, 6),
    ("webcam", "Periféricos", 180.00, 4),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS produtos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nome       TEXT    NOT NULL UNIQUE,
    categoria  TEXT    NOT NULL,
    preco      REAL    NOT NULL CHECK (preco >= 0),
    quantidade INTEGER NOT NULL CHECK (quantidade >= 0)
);
CREATE TABLE IF NOT EXISTS pedidos (
    order_id  TEXT PRIMARY KEY,
    status    TEXT NOT NULL CHECK (status IN ('RESERVADO', 'INDISPONIVEL', 'ESTORNADO')),
    resultado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservas (
    order_id   TEXT    NOT NULL REFERENCES pedidos(order_id),
    produto_id INTEGER NOT NULL REFERENCES produtos(id),
    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
    PRIMARY KEY (order_id, produto_id)
);
"""


def _normalize_items(itens: Any) -> dict[int, int] | None:
    """Converte a lista de itens em {produto_id: quantidade}, somando repetidos.
    Retorna None se algum item for inválido."""
    if not isinstance(itens, list) or not itens:
        return None
    wanted: dict[int, int] = {}
    for item in itens:
        try:
            pid, qtd = int(item["produto_id"]), int(item["quantidade"])
        except (KeyError, TypeError, ValueError):
            return None
        if qtd <= 0:
            return None
        wanted[pid] = wanted.get(pid, 0) + qtd
    return wanted


class InventoryRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or os.getenv("INVENTORY_DB") or DEFAULT_DB)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Transação de escrita: BEGIN IMMEDIATE trava outros escritores até o COMMIT."""
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

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            if conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO produtos (nome, categoria, preco, quantidade) VALUES (?, ?, ?, ?)",
                    SEED,
                )
        finally:
            conn.close()

    # ---------------------------------------------------------------- leitura

    def list_available(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, nome, categoria, preco, quantidade FROM produtos "
                "WHERE quantidade > 0 ORDER BY nome"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_categories(self) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT DISTINCT categoria FROM produtos ORDER BY categoria").fetchall()
            return [r["categoria"] for r in rows]
        finally:
            conn.close()

    def get_product(self, produto_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM produtos WHERE id = ?", (produto_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # --------------------------------------------------------------- escrita

    def reserve(self, order_id: str, itens: Any) -> tuple[str, dict[str, Any]]:
        """Reserva todos os itens ou nenhum (atômico) e é idempotente por order_id.

        Retorna (status, payload), com status 'RESERVADO' ou 'INDISPONIVEL'.
        Se o pedido já foi processado, devolve o resultado guardado, sem baixar de novo.
        """
        with self._tx() as conn:
            row = conn.execute(
                "SELECT status, resultado FROM pedidos WHERE order_id = ?", (order_id,)
            ).fetchone()
            if row:
                status = "RESERVADO" if row["status"] in ("RESERVADO", "ESTORNADO") else "INDISPONIVEL"
                return status, json.loads(row["resultado"])

            wanted = _normalize_items(itens)
            faltantes: list[dict[str, Any]] = []
            produtos: dict[int, sqlite3.Row] = {}

            if wanted is None:
                faltantes.append({"motivo": "itens inválidos"})
            else:
                for pid, qtd in wanted.items():
                    p = conn.execute(
                        "SELECT id, nome, categoria, preco, quantidade FROM produtos WHERE id = ?", (pid,)
                    ).fetchone()
                    if p is None:
                        faltantes.append({"produto_id": pid, "solicitado": qtd, "disponivel": 0,
                                          "motivo": "produto inexistente"})
                    elif p["quantidade"] < qtd:
                        faltantes.append({"produto_id": pid, "solicitado": qtd,
                                          "disponivel": p["quantidade"], "motivo": "estoque insuficiente"})
                    else:
                        produtos[pid] = p

            if faltantes:
                status = "INDISPONIVEL"
                payload: dict[str, Any] = {
                    "order_id": order_id,
                    "motivo": "Produtos indisponíveis no estoque",
                    "itens_faltantes": faltantes,
                }
                conn.execute(
                    "INSERT INTO pedidos (order_id, status, resultado) VALUES (?, ?, ?)",
                    (order_id, status, json.dumps(payload)),
                )
                return status, payload

            itens_ok: list[dict[str, Any]] = []
            total = 0.0
            for pid, qtd in wanted.items():
                p = produtos[pid]
                subtotal = round(p["preco"] * qtd, 2)
                total += subtotal
                itens_ok.append({
                    "produto_id": pid, "nome": p["nome"], "categoria": p["categoria"],
                    "quantidade": qtd, "preco_unitario": p["preco"], "subtotal": subtotal,
                })
            payload = {"order_id": order_id, "itens": itens_ok, "valor_total": round(total, 2)}

            conn.execute(
                "INSERT INTO pedidos (order_id, status, resultado) VALUES (?, 'RESERVADO', ?)",
                (order_id, json.dumps(payload)),
            )
            for pid, qtd in wanted.items():
                conn.execute("UPDATE produtos SET quantidade = quantidade - ? WHERE id = ?", (qtd, pid))
                conn.execute(
                    "INSERT INTO reservas (order_id, produto_id, quantidade) VALUES (?, ?, ?)",
                    (order_id, pid, qtd),
                )
            return "RESERVADO", payload

    def restore(self, order_id: str) -> bool:
        """Devolve ao estoque os itens reservados. Idempotente: retorna False se não havia
        reserva ativa (pedido desconhecido, sem estoque ou já estornado)."""
        with self._tx() as conn:
            row = conn.execute("SELECT status FROM pedidos WHERE order_id = ?", (order_id,)).fetchone()
            if row is None or row["status"] != "RESERVADO":
                return False
            reservas = conn.execute(
                "SELECT produto_id, quantidade FROM reservas WHERE order_id = ?", (order_id,)
            ).fetchall()
            for r in reservas:
                conn.execute(
                    "UPDATE produtos SET quantidade = quantidade + ? WHERE id = ?",
                    (r["quantidade"], r["produto_id"]),
                )
            conn.execute("DELETE FROM reservas WHERE order_id = ?", (order_id,))
            conn.execute("UPDATE pedidos SET status = 'ESTORNADO' WHERE order_id = ?", (order_id,))
            return True
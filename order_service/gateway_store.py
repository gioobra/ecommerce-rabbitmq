import logging
import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Callable

log = logging.getLogger("gateway")

CRIADO = "CRIADO"
ESTOQUE_CONFIRMADO = "ESTOQUE_CONFIRMADO"
ESTOQUE_INDISPONIVEL = "ESTOQUE_INDISPONIVEL"
AGUARDANDO_PAGAMENTO = "AGUARDANDO_PAGAMENTO"
PAGAMENTO_APROVADO = "PAGAMENTO_APROVADO"
PAGAMENTO_RECUSADO = "PAGAMENTO_RECUSADO"
ENVIADO = "ENVIADO"
CANCELADO = "CANCELADO"

CANCELAVEIS = {CRIADO, ESTOQUE_CONFIRMADO, AGUARDANDO_PAGAMENTO}

CAMPOS_PERMITIDOS = {"valor_total", "itens_confirmados", "itens_faltantes",
                     "checkout_url", "nota_fiscal", "motivo"}

APLICADO, IGNORADO, DESCONHECIDO = "applied", "ignored", "unknown"

Listener = Callable[[dict[str, Any], dict[str, Any]], None]


def _now_ms() -> int:
    return int(time.time() * 1000)


class OrderStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._orders: dict[str, dict[str, Any]] = {}
        self._listeners: list[Listener] = []


    def add_listener(self, fn: Listener) -> None:
        """fn(pedido, mudanca) é chamada a cada mudança de estado (usado pelo SSE no Bloco 3)."""
        self._listeners.append(fn)

    def _notify(self, order: dict[str, Any]) -> None:
        change = deepcopy(order["historico"][-1])
        for fn in list(self._listeners):
            try:
                fn(deepcopy(order), change)
            except Exception:
                log.exception("erro em listener de pedido")


    def get(self, order_id: str) -> dict[str, Any] | None:
        with self._lock:
            order = self._orders.get(order_id)
            return deepcopy(order) if order else None

    def list_by_client(self, client_id: str) -> list[dict[str, Any]]:
        with self._lock:
            orders = [o for o in self._orders.values() if o["client_id"] == client_id]
            return deepcopy(sorted(orders, key=lambda o: o["criado_em"], reverse=True))


    def create(self, client_id: str, itens: list[dict[str, Any]]) -> dict[str, Any]:
        now = _now_ms()
        order_id = uuid.uuid4().hex[:10]
        order = {
            "order_id": order_id, "client_id": client_id, "itens": deepcopy(itens),
            "status": CRIADO, "valor_total": None, "itens_confirmados": None,
            "itens_faltantes": None, "checkout_url": None, "nota_fiscal": None, "motivo": None,
            "criado_em": now, "atualizado_em": now,
            "historico": [{"status": CRIADO, "timestamp": now, "detalhe": "Pedido criado"}],
        }
        with self._lock:
            self._orders[order_id] = order
            return deepcopy(order)

    def discard(self, order_id: str) -> None:
        with self._lock:
            self._orders.pop(order_id, None)

    def transition(
        self, order_id: str, new_status: str, allowed_from: set[str], detalhe: str,
        notify: bool = True, **fields: Any,
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                return DESCONHECIDO, None, None
            previous = order["status"]
            if previous not in allowed_from:
                return IGNORADO, deepcopy(order), previous
            now = _now_ms()
            order["status"] = new_status
            order["atualizado_em"] = now
            for key, value in fields.items():
                if key in CAMPOS_PERMITIDOS:
                    order[key] = value
            order["historico"].append({"status": new_status, "timestamp": now, "detalhe": detalhe})
            snapshot = deepcopy(order)
        if notify:
            self._notify(snapshot)
        return APLICADO, snapshot, previous

    def announce(self, order_id: str) -> None:
        """Notifica a última mudança (usado quando transition(notify=False) foi confirmada)."""
        with self._lock:
            order = self._orders.get(order_id)
            snapshot = deepcopy(order) if order else None
        if snapshot:
            self._notify(snapshot)

    def rollback(self, order_id: str, previous_status: str) -> None:
        """Desfaz a última transição (cancelamento cuja publicação falhou)."""
        with self._lock:
            order = self._orders.get(order_id)
            if order and len(order["historico"]) > 1:
                order["historico"].pop()
                order["status"] = previous_status
                order["atualizado_em"] = order["historico"][-1]["timestamp"]

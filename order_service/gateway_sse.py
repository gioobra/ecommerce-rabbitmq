import asyncio
import json
import logging
import threading
from typing import Any

log = logging.getLogger("gateway.sse")

MAX_PER_CLIENT = 10
QUEUE_SIZE = 100


class TooManyConnections(Exception):
    pass


def format_sse(event: str, data: Any) -> str:
    """Formata um evento SSE. O JSON compacto não tem quebras de linha."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def order_event(order: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Notificação enviada ao cliente: a mudança e o pedido completo (o front só faz upsert)."""
    return {
        "order_id": order["order_id"],
        "status": change["status"],
        "detalhe": change["detalhe"],
        "timestamp": change["timestamp"],
        "pedido": order,
    }


class Subscription:
    def __init__(self, client_id: str, loop: asyncio.AbstractEventLoop, maxsize: int = QUEUE_SIZE) -> None:
        self.client_id = client_id
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)


def _put_dropping_oldest(queue: asyncio.Queue, item: dict[str, Any]) -> None:
    """Executa no loop do FastAPI. Cliente lento perde os eventos mais antigos
    (o estado atual é recuperável pelo snapshot da reconexão)."""
    while True:
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass


class SSEHub:
    def __init__(self, max_per_client: int = MAX_PER_CLIENT) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, set[Subscription]] = {}
        self._max = max_per_client

    def count(self, client_id: str | None = None) -> int:
        with self._lock:
            if client_id is not None:
                return len(self._subs.get(client_id, ()))
            return sum(len(s) for s in self._subs.values())

    def is_full(self, client_id: str) -> bool:
        return self.count(client_id) >= self._max

    def subscribe(self, client_id: str, loop: asyncio.AbstractEventLoop | None = None) -> Subscription:
        """Deve ser chamado dentro do event loop (ou receber o loop)."""
        loop = loop or asyncio.get_running_loop()
        with self._lock:
            subs = self._subs.setdefault(client_id, set())
            if len(subs) >= self._max:
                raise TooManyConnections(client_id)
            sub = Subscription(client_id, loop)
            subs.add(sub)
            return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            subs = self._subs.get(sub.client_id)
            if subs:
                subs.discard(sub)
                if not subs:
                    del self._subs[sub.client_id]

    def publish(self, client_id: str, event: str, data: Any) -> None:
        """Seguro para chamar de qualquer thread."""
        with self._lock:
            targets = list(self._subs.get(client_id, ()))
        item = {"event": event, "data": data}
        for sub in targets:
            try:
                sub.loop.call_soon_threadsafe(_put_dropping_oldest, sub.queue, item)
            except RuntimeError:  # loop já fechado
                self.unsubscribe(sub)
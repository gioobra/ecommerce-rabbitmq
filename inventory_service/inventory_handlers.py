"""Regras de negócio do MS Estoque ao receber eventos do RabbitMQ."""

import logging
from typing import Any, Callable

from events import PEDIDO_CRIADO, PEDIDO_EXCLUIDO, PEDIDO_ESTOQUE_OK, ESTOQUE_INDISPONIVEL
from messaging import TransientError

log = logging.getLogger("inventory")

Publish = Callable[[str, dict[str, Any]], Any]


def make_handler(repo, publish: Publish):
    """Cria o handler passado ao consumer. `publish(routing_key, payload)` assina e publica."""

    def handler(event_type: str, payload: dict[str, Any], envelope: dict[str, Any]) -> None:
        order_id = payload.get("order_id")
        if not order_id:
            log.warning("evento %s sem order_id; descartado", event_type)
            return
        order_id = str(order_id)

        if event_type == PEDIDO_CRIADO:
            status, result = repo.reserve(order_id, payload.get("itens"))
            routing_key = PEDIDO_ESTOQUE_OK if status == "RESERVADO" else ESTOQUE_INDISPONIVEL
            log.info("pedido %s -> %s", order_id, status)
            try:
                publish(routing_key, result)
            except Exception as exc:
                # A reserva já está gravada. Ao reentregar, reserve() devolve o mesmo
                # resultado (idempotente) e a publicação é refeita.
                raise TransientError(f"falha ao publicar {routing_key}: {exc}") from exc

        elif event_type == PEDIDO_EXCLUIDO:
            restored = repo.restore(order_id)
            log.info("pedido %s excluído -> %s", order_id,
                     "estoque devolvido" if restored else "nada a devolver")

    return handler
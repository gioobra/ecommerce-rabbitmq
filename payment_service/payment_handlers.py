"""Regras do MS Pagamento ao consumir eventos do RabbitMQ."""

import logging
import secrets
from typing import Any, Callable

from events import PEDIDO_ESTOQUE_OK, PEDIDO_EXCLUIDO, PAGAMENTO_LINK_CRIADO
from messaging import TransientError
from payment_provider import ProviderUnavailable, ProviderRejected
from payment_repository import CANCELADO

log = logging.getLogger("payment")

Publish = Callable[[str, dict[str, Any]], Any]


def make_handler(repo, publish: Publish, provider, webhook_url: str):

    def on_estoque_ok(order_id: str, payload: dict[str, Any]) -> None:
        valor = payload.get("valor_total")
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor <= 0:
            log.error("pedido %s com valor_total inválido (%r); ignorado", order_id, valor)
            return

        row = repo.get(order_id)
        if row is None:
            token = secrets.token_urlsafe(24)  # segredo desta cobrança, usado só pelo webhook
            try:
                charge = provider.create_charge(order_id, float(valor), webhook_url, token)
            except ProviderUnavailable as exc:
                raise TransientError(f"provedor de pagamento indisponível: {exc}") from exc
            except ProviderRejected as exc:
                log.error("provedor rejeitou a cobrança do pedido %s: %s", order_id, exc)
                return
            repo.create(order_id, float(valor), charge["charge_id"], charge["checkout_url"], token)
            row = repo.get(order_id)

        if row["status"] == CANCELADO:
            log.info("pedido %s foi cancelado; nenhuma cobrança será criada", order_id)
            return

        if not row["link_publicado"]:
            try:
                publish(PAGAMENTO_LINK_CRIADO, {
                    "order_id": order_id, "charge_id": row["charge_id"],
                    "checkout_url": row["checkout_url"], "valor": row["valor"],
                })
            except Exception as exc:
                raise TransientError(f"falha ao publicar {PAGAMENTO_LINK_CRIADO}: {exc}") from exc
            repo.mark_link_published(order_id)
            log.info("pedido %s -> cobrança %s criada, link publicado", order_id, row["charge_id"])

    def handler(event_type: str, payload: dict[str, Any], envelope: dict[str, Any]) -> None:
        order_id = str(payload.get("order_id") or "")
        if not order_id:
            log.warning("evento %s sem order_id; descartado", event_type)
            return
        if event_type == PEDIDO_ESTOQUE_OK:
            on_estoque_ok(order_id, payload)
        elif event_type == PEDIDO_EXCLUIDO:
            log.info("pedido %s excluído -> %s", order_id, repo.cancel(order_id))

    return handler
"""Consumo dos eventos de Estoque, Pagamento e Entrega pelo Gateway."""

import logging
from typing import Any, Callable

from events import (
    PEDIDO_ESTOQUE_OK, ESTOQUE_INDISPONIVEL as EV_ESTOQUE_INDISPONIVEL, PAGAMENTO_LINK_CRIADO,
    PAGAMENTO_APROVADO as EV_PAGAMENTO_APROVADO, PAGAMENTO_RECUSADO as EV_PAGAMENTO_RECUSADO,
    PEDIDO_ENVIADO, PEDIDO_EXCLUIDO,
)
from messaging import TransientError
from gateway_store import (
    CRIADO, ESTOQUE_CONFIRMADO, ESTOQUE_INDISPONIVEL, AGUARDANDO_PAGAMENTO,
    PAGAMENTO_APROVADO, PAGAMENTO_RECUSADO, ENVIADO, DESCONHECIDO, IGNORADO,
)

log = logging.getLogger("gateway")

Publish = Callable[[str, dict[str, Any]], Any]

# evento -> (novo status, status de origem permitidos, texto padrão)
REGRAS: dict[str, tuple[str, set[str], str]] = {
    PEDIDO_ESTOQUE_OK: (ESTOQUE_CONFIRMADO, {CRIADO}, "Estoque confirmado"),
    EV_ESTOQUE_INDISPONIVEL: (ESTOQUE_INDISPONIVEL, {CRIADO}, "Estoque indisponível"),
    PAGAMENTO_LINK_CRIADO: (AGUARDANDO_PAGAMENTO, {ESTOQUE_CONFIRMADO}, "Aguardando pagamento"),
    EV_PAGAMENTO_APROVADO: (PAGAMENTO_APROVADO, {ESTOQUE_CONFIRMADO, AGUARDANDO_PAGAMENTO}, "Pagamento aprovado"),
    EV_PAGAMENTO_RECUSADO: (PAGAMENTO_RECUSADO, {ESTOQUE_CONFIRMADO, AGUARDANDO_PAGAMENTO}, "Pagamento recusado"),
    PEDIDO_ENVIADO: (ENVIADO, {PAGAMENTO_APROVADO}, "Pedido enviado"),
}


def _fields_from(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == PEDIDO_ESTOQUE_OK:
        return {"valor_total": payload.get("valor_total"), "itens_confirmados": payload.get("itens")}
    if event_type == EV_ESTOQUE_INDISPONIVEL:
        return {"motivo": payload.get("motivo"), "itens_faltantes": payload.get("itens_faltantes")}
    if event_type == PAGAMENTO_LINK_CRIADO:
        return {"checkout_url": payload.get("checkout_url")}
    if event_type == PEDIDO_ENVIADO:
        return {"nota_fiscal": payload.get("nota_fiscal")}
    return {}


def make_handler(store, publish: Publish):
    """Handler passado ao consumer do Gateway."""

    def handler(event_type: str, payload: dict[str, Any], envelope: dict[str, Any]) -> None:
        order_id = str(payload.get("order_id") or "")
        regra = REGRAS.get(event_type)
        if not order_id or regra is None:
            log.warning("evento %s inválido ou sem order_id; descartado", event_type)
            return

        novo_status, origens, detalhe = regra
        resultado, pedido, _ = store.transition(
            order_id, novo_status, origens, detalhe, **_fields_from(event_type, payload)
        )

        if resultado == DESCONHECIDO:
            log.warning("evento %s para pedido desconhecido %s; descartado", event_type, order_id)
            return
        if resultado == IGNORADO:
            log.info("evento %s ignorado para %s (estado atual: %s)", event_type, order_id, pedido["status"])
        else:
            log.info("pedido %s -> %s", order_id, novo_status)

        if event_type == EV_PAGAMENTO_RECUSADO and pedido["status"] == PAGAMENTO_RECUSADO:
            try:
                publish(PEDIDO_EXCLUIDO, {"order_id": order_id, "motivo": "pagamento recusado"})
            except Exception as exc:
                raise TransientError(f"falha ao publicar {PEDIDO_EXCLUIDO}: {exc}") from exc

    return handler
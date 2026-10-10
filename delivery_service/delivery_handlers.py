"""pagamento aprovado -> nota fiscal -> despacho -> pedido.enviado."""

import logging
import time
from typing import Any, Callable

from events import PAGAMENTO_APROVADO, PEDIDO_ENVIADO
from messaging import TransientError

log = logging.getLogger("delivery")

Publish = Callable[[str, dict[str, Any]], Any]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def make_handler(repo, publish: Publish, delay_seconds: float = 0.0, sleep: Callable[[float], None] = time.sleep):
    """Handler do consumer. Idempotente: um pagamento.aprovado repetido não gera 2ª nota nem 2º envio."""

    def handler(event_type: str, payload: dict[str, Any], envelope: dict[str, Any]) -> None:
        if event_type != PAGAMENTO_APROVADO:
            return
        order_id = str(payload.get("order_id") or "")
        if not order_id:
            log.warning("evento %s sem order_id; descartado", event_type)
            return

        entrega = repo.emit_invoice(order_id, _number(payload.get("valor")), payload.get("charge_id"))
        if entrega["enviado_publicado"]:
            log.info("pedido %s já foi enviado (%s); evento repetido ignorado", order_id, entrega["nota_fiscal"])
            return

        log.info("pedido %s: nota fiscal %s emitida, despachando...", order_id, entrega["nota_fiscal"])
        if delay_seconds > 0:
            sleep(delay_seconds)  # simula o tempo de separação e despacho

        try:
            publish(PEDIDO_ENVIADO, {
                "order_id": order_id,
                "nota_fiscal": entrega["nota_fiscal"],
                "codigo_rastreio": entrega["codigo_rastreio"],
                "despachado_em": int(time.time() * 1000),
            })
        except Exception as exc:
            # A nota já está gravada: ao reentregar, emit_invoice devolve a mesma e só falta publicar.
            raise TransientError(f"falha ao publicar {PEDIDO_ENVIADO}: {exc}") from exc
        repo.mark_published(order_id)
        log.info("pedido %s enviado (%s)", order_id, entrega["codigo_rastreio"])

    return handler
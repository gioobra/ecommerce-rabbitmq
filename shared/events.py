"""Contrato de eventos do sistema: nomes, produtor autorizado e filas."""

EXCHANGE_NAME: str = "eCommerce"

PEDIDO_CRIADO = "pedido.criado"
PEDIDO_EXCLUIDO = "pedido.excluido"
PEDIDO_ESTOQUE_OK = "pedido.estoque_ok"
ESTOQUE_INDISPONIVEL = "estoque.indisponivel"
PAGAMENTO_LINK_CRIADO = "pagamento.link_criado"
PAGAMENTO_APROVADO = "pagamento.aprovado"
PAGAMENTO_RECUSADO = "pagamento.recusado"
PEDIDO_ENVIADO = "pedido.enviado"
INTERESSE_PROMOCAO = "interesse.promocao"


EVENT_PRODUCERS: dict[str, str] = {
    PEDIDO_CRIADO: "order",
    PEDIDO_EXCLUIDO: "order",
    INTERESSE_PROMOCAO: "order",
    PEDIDO_ESTOQUE_OK: "inventory",
    ESTOQUE_INDISPONIVEL: "inventory",
    PAGAMENTO_LINK_CRIADO: "payment",
    PAGAMENTO_APROVADO: "payment",
    PAGAMENTO_RECUSADO: "payment",
    PEDIDO_ENVIADO: "delivery",
}

SERVICE_QUEUES: dict[str, str] = {
    "order": "order_updates_queue",
    "inventory": "inventory_events_queue",
    "payment": "payment_events_queue",
    "delivery": "delivery_events_queue",
    "promotion": "promotion_events_queue",
}

QUEUES: dict[str, list[str]] = {
    "order_updates_queue": [
        PEDIDO_ESTOQUE_OK, ESTOQUE_INDISPONIVEL, PAGAMENTO_LINK_CRIADO,
        PAGAMENTO_APROVADO, PAGAMENTO_RECUSADO, PEDIDO_ENVIADO,
    ],
    "inventory_events_queue": [PEDIDO_CRIADO, PEDIDO_EXCLUIDO],
    "payment_events_queue": [PEDIDO_ESTOQUE_OK, PEDIDO_EXCLUIDO],
    "delivery_events_queue": [PAGAMENTO_APROVADO],
    "promotion_events_queue": [INTERESSE_PROMOCAO],
}
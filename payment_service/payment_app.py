"""
MS Pagamento: consumer de eventos + endpoint de Webhook do provedor.
"""

import hmac
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Literal

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path.insert(0, str(BASE_DIR / "shared"))
sys.path.insert(0, str(HERE))

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from utils import ensure_keys
import messaging as m
from events import PAGAMENTO_APROVADO, PAGAMENTO_RECUSADO
from payment_repository import PaymentRepository
from payment_provider import MockPaymentClient
from payment_handlers import make_handler

SERVICE_NAME = "payment"
log = logging.getLogger("payment")


class WebhookBody(BaseModel):
    charge_id: str
    order_id: str
    status: Literal["APROVADO", "RECUSADO"]


def create_app(
    repo: PaymentRepository | None = None,
    publish: Callable[[str, dict[str, Any]], Any] | None = None,
    provider: MockPaymentClient | None = None,
    webhook_url: str | None = None,
    enable_messaging: bool = True,
) -> FastAPI:
    publish_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        m.configure_logging()
        if app.state.repo is None:
            app.state.repo = PaymentRepository()
        publisher = None
        if enable_messaging:
            private_key = ensure_keys(SERVICE_NAME, BASE_DIR)
            publisher = m.Publisher(SERVICE_NAME, private_key)
            app.state.publish = publisher.publish
            m.start_consumer_thread(SERVICE_NAME, BASE_DIR, make_handler(
                app.state.repo, publisher.publish, app.state.provider, app.state.webhook_url))
            log.info("MS Pagamento no ar (consumer + webhook)")
        yield
        if publisher:
            publisher.close()

    app = FastAPI(title="MS Pagamento", lifespan=lifespan)
    app.state.repo = repo
    app.state.publish = publish or (lambda rk, p: None)
    app.state.provider = provider or MockPaymentClient()
    app.state.webhook_url = webhook_url or os.getenv("PAYMENT_WEBHOOK_URL", "http://localhost:8002/webhook")

    @app.post("/webhook")
    def webhook(body: WebhookBody, x_webhook_token: str | None = Header(default=None)):
        """Recebe a decisão do provedor (APROVADO ou RECUSADO) e publica o evento correspondente."""
        repo = app.state.repo
        row = repo.get(body.order_id)

        # Um único 401 para token ausente, token errado ou cobrança desconhecida: não revela nada
        autorizado = (
            row is not None and row["callback_token"] and row["charge_id"] == body.charge_id
            and x_webhook_token
            and hmac.compare_digest(x_webhook_token.encode(), row["callback_token"].encode())
        )
        if not autorizado:
            log.warning("webhook rejeitado (não autorizado) para o pedido %s", body.order_id)
            raise HTTPException(401, "Webhook não autorizado")

        resultado = repo.decide(body.order_id, body.status)
        if resultado == "CANCELADO":
            raise HTTPException(409, "Pedido cancelado; pagamento ignorado")
        if resultado == "CONFLITO":
            raise HTTPException(409, "Pagamento já finalizado com outro status")

        routing_key = PAGAMENTO_APROVADO if body.status == "APROVADO" else PAGAMENTO_RECUSADO
        with publish_lock:  # evita publicar duas vezes se o provedor reenviar em paralelo
            atual = repo.get(body.order_id)
            if not atual["resultado_publicado"]:
                try:
                    app.state.publish(routing_key, {
                        "order_id": body.order_id, "charge_id": atual["charge_id"], "valor": atual["valor"],
                    })
                except Exception:
                    log.exception("falha ao publicar %s", routing_key)
                    # a decisão já está gravada: o reenvio do webhook só precisa publicar
                    raise HTTPException(503, "Mensageria indisponível; tente novamente")
                repo.mark_result_published(body.order_id)
                log.info("pedido %s -> %s", body.order_id, body.status)
        return {"order_id": body.order_id, "status": body.status, "processado": resultado == "APLICADO"}

    @app.get("/cobrancas/{order_id}")
    def consultar(order_id: str):
        """Consulta (sem expor o token do webhook)."""
        row = app.state.repo.get(order_id)
        if row is None:
            raise HTTPException(404, "Cobrança não encontrada")
        row.pop("callback_token", None)
        return row

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
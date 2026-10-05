import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path.insert(0, str(BASE_DIR / "shared"))
sys.path.insert(0, str(HERE))

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from utils import ensure_keys
import messaging as m
from events import PEDIDO_CRIADO, PEDIDO_EXCLUIDO
from gateway_store import OrderStore, CANCELAVEIS, CANCELADO, APLICADO, DESCONHECIDO
from gateway_handlers import make_handler
from gateway_sse import SSEHub, TooManyConnections, format_sse, order_event

SERVICE_NAME = "order"
CLIENT_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
log = logging.getLogger("gateway")


class ItemPedido(BaseModel):
    produto_id: int = Field(ge=1)
    quantidade: int = Field(ge=1, le=100)


class NovoPedido(BaseModel):
    client_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    itens: list[ItemPedido] = Field(min_length=1, max_length=50)


def _no_publish(routing_key: str, payload: dict[str, Any]) -> None:
    return None


def create_app(
    store: OrderStore | None = None,
    publish: Callable[[str, dict[str, Any]], Any] | None = None,
    inventory_url: str | None = None,
    http_client: httpx.Client | None = None,
    enable_messaging: bool = True,
    heartbeat_seconds: float | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        m.configure_logging()
        publisher = None
        if enable_messaging:
            private_key = ensure_keys(SERVICE_NAME, BASE_DIR)
            publisher = m.Publisher(SERVICE_NAME, private_key)
            app.state.publish = publisher.publish
            m.start_consumer_thread(SERVICE_NAME, BASE_DIR, make_handler(app.state.store, publisher.publish))
            log.info("API Gateway no ar (REST + consumer)")
        yield
        if publisher:
            publisher.close()
        app.state.http.close()

    app = FastAPI(title="API Gateway", lifespan=lifespan)

    origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins],
                       allow_methods=["*"], allow_headers=["*"])

    app.state.store = store or OrderStore()
    app.state.publish = publish or _no_publish
    app.state.inventory_url = (inventory_url or os.getenv("INVENTORY_URL", "http://localhost:8001")).rstrip("/")
    app.state.http = http_client or httpx.Client(timeout=3.0)

    # SSE: cada mudança de estado de um pedido vira um evento para o dono do pedido
    app.state.hub = SSEHub()
    app.state.store.add_listener(
        lambda order, change: app.state.hub.publish(order["client_id"], "pedido", order_event(order, change))
    )
    hb = heartbeat_seconds if heartbeat_seconds is not None else float(os.getenv("SSE_HEARTBEAT_SECONDS", "15"))

    def _owned_order(order_id: str, client_id: str) -> dict[str, Any]:
        order = app.state.store.get(order_id)
        if order is None:
            raise HTTPException(404, "Pedido não encontrado")
        if order["client_id"] != client_id:
            raise HTTPException(403, "Pedido pertence a outro cliente")
        return order

    @app.get("/produtos")
    def listar_produtos():
        """Lista produtos em estoque, consultando o MS Estoque diretamente por REST."""
        try:
            resp = app.state.http.get(f"{app.state.inventory_url}/produtos")
        except httpx.HTTPError:
            raise HTTPException(503, "Serviço de estoque indisponível")
        if resp.status_code != 200:
            raise HTTPException(502, "Resposta inválida do serviço de estoque")
        try:
            return resp.json()
        except ValueError:
            raise HTTPException(502, "Resposta inválida do serviço de estoque")

    @app.post("/pedidos", status_code=202)
    def criar_pedido(body: NovoPedido):
        """Cria o pedido e publica pedido.criado. O andamento chega por eventos (SSE no Bloco 3)."""
        itens = [i.model_dump() for i in body.itens]
        order = app.state.store.create(body.client_id, itens)
        try:
            app.state.publish(PEDIDO_CRIADO, {
                "order_id": order["order_id"], "client_id": body.client_id, "itens": itens,
            })
        except Exception:
            log.exception("falha ao publicar pedido.criado")
            app.state.store.discard(order["order_id"])
            raise HTTPException(503, "Mensageria indisponível. Tente novamente.")
        return order

    @app.get("/pedidos")
    def listar_pedidos(client_id: str = Query(..., min_length=1, max_length=64)):
        return app.state.store.list_by_client(client_id)

    @app.get("/pedidos/{order_id}")
    def obter_pedido(order_id: str, client_id: str = Query(..., min_length=1, max_length=64)):
        return _owned_order(order_id, client_id)

    @app.delete("/pedidos/{order_id}")
    def cancelar_pedido(order_id: str, client_id: str = Query(..., min_length=1, max_length=64)):
        """Cancela (antes do pagamento ser aprovado) e publica pedido.excluido."""
        _owned_order(order_id, client_id)
        store = app.state.store
        resultado, pedido, anterior = store.transition(
            order_id, CANCELADO, CANCELAVEIS, "Pedido cancelado pelo cliente", notify=False,
            motivo="cancelado pelo cliente",
        )
        if resultado == DESCONHECIDO:
            raise HTTPException(404, "Pedido não encontrado")
        if resultado != APLICADO:
            raise HTTPException(409, f"Pedido não pode ser cancelado no estado {pedido['status']}")
        try:
            app.state.publish(PEDIDO_EXCLUIDO, {"order_id": order_id, "motivo": "cancelado pelo cliente"})
        except Exception:
            log.exception("falha ao publicar pedido.excluido")
            store.rollback(order_id, anterior)
            raise HTTPException(503, "Mensageria indisponível. O pedido não foi cancelado.")
        store.announce(order_id)
        return store.get(order_id)

    @app.get("/stream/{client_id}")
    async def stream(request: Request, client_id: str = PathParam(..., pattern=CLIENT_ID_PATTERN)):
        """SSE: envia `snapshot` ao conectar, depois um evento `pedido` a cada mudança de estado."""
        hub = app.state.hub
        if hub.is_full(client_id):
            raise HTTPException(429, "Conexões SSE demais para este cliente")

        async def event_stream():
            try:
                sub = hub.subscribe(client_id)  
            except TooManyConnections:
                return
            try:
                yield "retry: 3000\n\n"
                yield format_sse("snapshot", {"pedidos": app.state.store.list_by_client(client_id)})
                while True:
                    try:
                        item = await asyncio.wait_for(sub.queue.get(), timeout=hb)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield ": ping\n\n"  # ping para manter a conexão viva
                        continue
                    yield format_sse(item["event"], item["data"])
            finally:
                hub.unsubscribe(sub)

        return StreamingResponse(
            event_stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
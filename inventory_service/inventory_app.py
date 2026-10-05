"""MS Estoque: API REST (FastAPI) + consumer de eventos (RabbitMQ) no mesmo processo.

Execução (na raiz do projeto):
    uvicorn inventory_service.inventory_app:app --port 8001
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path.insert(0, str(BASE_DIR / "shared"))
sys.path.insert(0, str(HERE))

from fastapi import FastAPI
from pydantic import BaseModel

from utils import ensure_keys
import messaging as m
from inventory_repository import InventoryRepository
from inventory_handlers import make_handler

SERVICE_NAME = "inventory"
log = logging.getLogger("inventory")


class Produto(BaseModel):
    id: int
    nome: str
    categoria: str
    preco: float
    quantidade: int


def create_app(repo: InventoryRepository | None = None, enable_messaging: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        m.configure_logging()
        if app.state.repo is None:
            app.state.repo = InventoryRepository()
        publisher = None
        if enable_messaging:
            private_key = ensure_keys(SERVICE_NAME, BASE_DIR)
            publisher = m.Publisher(SERVICE_NAME, private_key)
            m.start_consumer_thread(SERVICE_NAME, BASE_DIR, make_handler(app.state.repo, publisher.publish))
            log.info("MS Estoque no ar (REST + consumer)")
        yield
        if publisher:
            publisher.close()

    app = FastAPI(title="MS Estoque", lifespan=lifespan)
    app.state.repo = repo

    @app.get("/produtos", response_model=list[Produto])
    def listar_produtos():
        """Produtos com quantidade disponível (consumido pelo API Gateway)."""
        return app.state.repo.list_available()

    @app.get("/categorias", response_model=list[str])
    def listar_categorias():
        return app.state.repo.list_categories()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
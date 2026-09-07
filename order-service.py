from typing import Any
import pika
import uuid
import json
import threading

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'eCommerce'

class OrderService:
    def __init__(self) -> None:
        # Estrutura em memória: { "order_id": {"id": str, "itens": list, "status": str} }
        self._orders: dict[str, dict[str, Any]] = {}
    
    def create_order(self, itens: list[str, dict[str, Any]]) -> dict[str, Any]:
        # Gera um ID único para o pedido
        order_id: str = str(uuid.uuid4())[:8]
        order: dict[str, Any] = {
            "id": order_id,
            "itens": itens,
            "status": "CRIADO"
        }
        self._orders[order_id] = order
        return order






def cli_menu(service: OrderService)-> None:
    pass
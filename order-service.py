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
        ''' 
        Gera um ID único para o pedido
        '''
        order_id = str(uuid.uuid4())[:8]
        order = {
            "id": order_id,
            "itens": itens,
            "status": "CRIADO"
        }
        self._orders[order_id] = order
        return order

    def update_status(self, order_id: str, new_status: str) -> bool:
        ''' 
        Atualiza o status de um pedido
        '''
        order = self._orders.get(order_id)
        if order:
            order["status"] = new_status
            print(f"\n[EVENTO] Pedido {order_id} atualizado para: {new_status}")
            print("> Escolha uma opcao: ", end="", flush=True)
            return True
        return False

    def delete_order(self, order_id: str) -> bool:
        ''' 
        Remove um pedido
        '''
        return self._orders.pop(order_id, None) is not None
    
    def get_order(self, order_id: str) -> dict[str, Any] | None:
        '''
        Retorna um pedido específico
        '''
        return self._orders.get(order_id)

    def list_orders(self) -> list[dict[str, Any]]:
        ''' 
        Retorna todos os pedidos
        '''
        return list(self._orders.values())

def publish_event(routing_key: str, payload: dict[str, Any]) -> None:
    ''' 
    Publica um evento no RabbitMQ
    '''
    # Conexão com o RabbitMQ
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT))
    channel = connection.channel()
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    channel.basic_publish(
        exchange=EXCHANGE_NAME,
        routing_key=routing_key,
        body=json.dumps(payload).encode('utf-8'),
        properties=pika.BasicProperties(
            delivery_mode=2,  # Mensagem persistente
            content_type='application/json'
            )
    )
    connection.close()

def start_consumer(service: OrderService) -> None:
    ''' 
    Inicia o consumidor para receber eventos do RabbitMQ
    '''
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT))
    channel = connection.channel()

    queue_name = 'order_updates_queue'
    channel.queue_declare(queue=queue_name, durable=True)
    channel.queue_bind(
        exchange=EXCHANGE_NAME,
        queue=queue_name,
        routing_key='pedido.status_atualizado'
    )
    
    def callback(ch, method, properties, body: bytes) -> None:
        '''
        Função para definir o que fazer quando uma mensagem nova chegar na fila
        '''
        event_data = json.loads(body.decode('utf-8'))
        order_id = event_data.get("order_id")
        novo_status = event_data.get("status")

        if order_id and novo_status:
            service.update_status(order_id, novo_status)
        
        ch.basic_ack(delivery_tag=method.delivery_tag)
    
    channel.basic_consume(queue=queue_name, on_message_callback=callback)
    channel.start_consuming()

def cli_menu(service: OrderService)-> None:
    pass
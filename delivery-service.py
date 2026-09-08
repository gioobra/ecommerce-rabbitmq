import json
import time
import uuid
from typing import Any

import pika

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'eCommerce'

class DeliveryService:
    def issue_invoice(self, order_id: str) -> str:
        '''
        Simula a geração e emissão da Nota Fiscal Eletrônica
        '''
        nota_fiscal: str = f"NF-{uuid.uuid4().hex[:8].upper()}"
        print(f"[NOTA FISCAL] Emitida {nota_fiscal} para o Pedido {order_id}")
        return nota_fiscal

    def prepare_dispatch(self, order_id: str) -> dict[str, Any]:
        '''
        Simula a separação em centro de distribuição e empacotamento.
        '''
        print(f"[LOGÍSTICA] Separando e embalando itens do Pedido {order_id}...")
        
        time.sleep(2.0)  

        codigo_rastreio: str = f"BR{uuid.uuid4().hex[:9].upper()}XP"
        print(f"[LOGÍSTICA] Pedido {order_id} coletado pela transportadora. Rastreio: {codigo_rastreio}")

        return {
            "order_id": order_id,
            "status": "ENVIADO",
            "nota_fiscal": self.issue_invoice(order_id),
            "codigo_rastreio": codigo_rastreio,
            "despachado_em": time.strftime("%Y-%m-%d %H:%M:%S")
        }

def publish_event(routing_key: str, payload: dict[str, Any]) -> None:
    '''
    Publica os eventos de resultado na exchange.
    '''
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    channel.basic_publish(
        exchange=EXCHANGE_NAME,
        routing_key=routing_key,
        body=json.dumps(payload).encode('utf-8'),
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type='application/json'
        )
    )
    connection.close()

def start_consumer(service: DeliveryService) -> None:
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    queue_name: str = 'delivery_events_queue'
    channel.queue_declare(queue=queue_name, durable=True)

    channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key='pagamento.aprovado')
    def callback(ch, method, properties, body: bytes) -> None:
        '''
        Função para definir o que fazer quando uma mensagem nova chegar na fila
        '''
        event_data = json.loads(body.decode('utf-8'))
        order_id = event_data["order_id"]
        
        print(f"\n[RECEBIDO] Pagamento aprovado para o Pedido {order_id}. Iniciando expedição...")

        detalhes_envio: dict[str, Any] = service.prepare_dispatch(order_id)

        publish_event(
            routing_key='pedido.enviado',
            payload=detalhes_envio
        )

        print(f"[PUBLICADO] 'pedido.enviado' para o Pedido {order_id}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
    
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    print(" [*] Microsserviço de Entrega ativo e aguardando 'pagamento.aprovado'.")
    channel.start_consuming()

if __name__ == '__main__':
    delivery_service = DeliveryService()
    start_consumer(delivery_service)
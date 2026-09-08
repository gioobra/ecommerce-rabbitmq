import json
import random
import time
from typing import Any

import pika

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'eCommerce'

class PaymentService:
    def __init__ (self, aprovado_chance: float = 0.6) -> None:
        # Define a probabilidade de aprovação (0.6 = 60% de chance de aprovação)
        self.aprovado_chance: float = aprovado_chance

    def process_payment(self, order_id: str) -> bool:
        '''
        Simula a comunicação com a operadora de cartão/gateway.
        '''
        print(f"\n [PROCESSANDO] Cobrança do Pedido {order_id}...")

        time.sleep(1.5)

        aprovado: bool = random.random() < self.aprovado_chance
        return aprovado


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

def start_consumer(service: PaymentService) -> None:
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    queue_name: str = 'payment_events_queue'
    channel.queue_declare(queue=queue_name, durable=True)

    channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key='pedido.estoque_ok')
    def callback(ch, method, properties, body: bytes) -> None:
        '''
        Função para definir o que fazer quando uma mensagem nova chegar na fila
        '''
        event_data = json.loads(body.decode('utf-8'))
        order_id = event_data["order_id"]
        
        print(f"\n[RECEBIDO] Pedido {order_id} com estoque liberado. Iniciando transação...")

        sucesso: bool = service.process_payment(order_id)

        if sucesso:
            publish_event(
                routing_key='pagamento.aprovado',
                payload={
                    "order_id": order_id,
                    "status": "PAGAMENTO_APROVADO",
                    "timestamp": time.time()
                }
            )
            print(f"[SUCESSO] 'pagamento.aprovado' publicado para Pedido {order_id}")
        else:
            publish_event(
                routing_key='pagamento.recusado',
                payload={
                    "order_id": order_id,
                    "status": "PAGAMENTO_RECUSADO",
                    "motivo": "Saldo insuficiente ou transação negada pela operadora"
                }
            )
            print(f"[FALHA] 'pagamento.recusado' publicado para Pedido {order_id}")
        
        ch.basic_ack(delivery_tag=method.delivery_tag)
    
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    print(" [*] Microsserviço de Pagamento aguardando eventos 'pedido.estoque_ok'.")
    channel.start_consuming()

if __name__ == '__main__':
    payment_service = PaymentService(aprovado_chance=0.6)
    start_consumer(payment_service)
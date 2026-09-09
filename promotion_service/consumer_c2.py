import json
from typing import Any

import pika

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'Promoções'

class PromotionConsumer:
    def __init__(self, nome: str) -> None:
        self.nome: str = nome

    def handle_promotion(self, routing_key: str, payload: dict[str, Any]) -> None:
        '''
        Processa (exibe) a promoção recebida.
        '''
        print(f"\n[{self.nome}] Nova promoção recebida ({routing_key})")
        print(f"  -> ID: {payload.get('promocao_id')}")
        print(f"  -> Categoria: {payload.get('categoria')}")
        print(f"  -> Produto: {payload.get('produto')}")
        print(f"  -> Desconto: {payload.get('desconto')}%")
        print(f"  -> Gerada em: {payload.get('gerada_em')}")

def start_consumer(consumer: PromotionConsumer, queue_name: str, binding_keys: list[str]) -> None:
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='topic', durable=True)

    channel.queue_declare(queue=queue_name, durable=True)

    for binding_key in binding_keys:
        channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key=binding_key)

    def callback(ch, method, properties, body: bytes) -> None:
        '''
        Função para definir o que fazer quando uma promoção nova chegar na fila
        '''
        event_data = json.loads(body.decode('utf-8'))
        routing_key = method.routing_key

        consumer.handle_promotion(routing_key, event_data)

        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    print(f" [*] {consumer.nome} ativo, escutando: {binding_keys}. Aguardando...")
    channel.start_consuming()

if __name__ == '__main__':
    consumer_c2 = PromotionConsumer(nome="Consumidor C2")
    start_consumer(
        consumer=consumer_c2,
        queue_name='promocao_c2_queue',
        binding_keys=['promocao.categoria.#']
    )
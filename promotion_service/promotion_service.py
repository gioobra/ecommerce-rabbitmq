import json
import random
import time
import uuid
from typing import Any

import pika

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'Promoções'

class PromotionService:
    def __init__(self) -> None:
        # Estrutura em memória: { "categoria": ["produto1", "produto2", ...] }
        self._catalogo: dict[str, list[str]] = {
            "A": ["notebook", "monitor"],
            "B": ["mouse", "teclado"],
            "C": ["headset", "webcam"]
        }
        self._descontos_possiveis: list[int] = [10, 15, 20, 25, 30, 40, 50]

    def generate_promotion(self) -> dict[str, Any]:
        '''
        Sorteia uma categoria, um produto dessa categoria e um desconto.
        '''
        categoria: str = random.choice(list(self._catalogo.keys()))
        produto: str = random.choice(self._catalogo[categoria])
        desconto: int = random.choice(self._descontos_possiveis)

        promocao_id: str = f"PROMO-{uuid.uuid4().hex[:8].upper()}"

        print(f"[PROMOÇÃO GERADA] {promocao_id} | Categoria {categoria} | {produto} | {desconto}% OFF")

        return {
            "promocao_id": promocao_id,
            "categoria": categoria,
            "produto": produto,
            "desconto": desconto,
            "gerada_em": time.strftime("%Y-%m-%d %H:%M:%S")
        }

def publish_event(routing_key: str, payload: dict[str, Any]) -> None:
    '''
    Publica os eventos de promoção na exchange (tipo topic).
    '''
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='topic', durable=True)

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

def run_promotion_loop(service: PromotionService, intervalo_segundos: float = 5.0) -> None:
    '''
    Loop principal: gera e publica uma promoção aleatória a cada intervalo de tempo.
    '''
    print(" [*] Microsserviço de Promoções ativo. Gerando promoções periodicamente...")

    while True:
        promocao: dict[str, Any] = service.generate_promotion()
        categoria: str = promocao["categoria"]

        routing_key: str = f"promocao.categoria.{categoria}"

        publish_event(
            routing_key=routing_key,
            payload=promocao
        )

        print(f"[PUBLICADO] '{routing_key}' | Promoção {promocao['promocao_id']}")

        time.sleep(intervalo_segundos)

if __name__ == '__main__':
    promotion_service = PromotionService()
    run_promotion_loop(promotion_service, intervalo_segundos=5.0)
import sys
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from utils import ensure_keys, sign_payload

import pika

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'Promoções'

SERVICE_NAME: str = 'promotion'

BASE_DIR: Path = Path(__file__).resolve().parent.parent

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

def publish_event(routing_key: str, payload: dict[str, Any], private_key: RSAPrivateKey) -> None:
    
    assinatura: str = sign_payload(private_key, payload)

    envelope: dict[str, Any] = {
        "event_type": routing_key,
        "producer": SERVICE_NAME,
        "payload": payload,
        "signature": assinatura,
    }

    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='topic', durable=True)

    channel.basic_publish(
        exchange=EXCHANGE_NAME,
        routing_key=routing_key,
        body=json.dumps(envelope).encode('utf-8'),
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type='application/json'
        )
    )
    connection.close()

def run_promotion_loop(service: PromotionService, private_key: RSAPrivateKey, intervalo_segundos: float = 5.0) -> None:
    '''
    Loop gera e publica uma promoção aleatória a cada 5s.
    '''
    print(" [*] Microsserviço de Promoções ativo. Gerando promoções periodicamente...")

    while True:
        promocao: dict[str, Any] = service.generate_promotion()
        categoria: str = promocao["categoria"]

        routing_key: str = f"promocao.categoria.{categoria}"

        publish_event(
            routing_key=routing_key,
            payload=promocao,
            private_key=private_key
        )

        print(f"[PUBLICADO] '{routing_key}' | Promoção {promocao['promocao_id']}")

        time.sleep(intervalo_segundos)

if __name__ == '__main__':
    private_key: RSAPrivateKey = ensure_keys(service_name=SERVICE_NAME, base_dir=BASE_DIR)

    promotion_service = PromotionService()
    run_promotion_loop(promotion_service, private_key, intervalo_segundos=5.0)
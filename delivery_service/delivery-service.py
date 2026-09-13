import sys
import json
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

import pika
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from utils import ensure_keys, sign_payload, verify_signature, load_public_key

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'eCommerce'

SERVICE_NAME: str = 'delivery'

BASE_DIR: Path = Path(__file__).resolve().parent.parent

_public_key_cache: dict[str, Any] = {}

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

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

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

def _get_producer_public_key(producer: str):
    '''
    Retorna a chave pública de um produtor, usando cache em memória
    '''
    if producer not in _public_key_cache:
        _public_key_cache[producer] = load_public_key(
            service_name=producer,
            requester_service=SERVICE_NAME,
            base_dir=BASE_DIR,
        )
    return _public_key_cache[producer]

def start_consumer(service: DeliveryService, private_key: RSAPrivateKey) -> None:
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
        envelope = json.loads(body.decode('utf-8'))
        routing_key = method.routing_key

        producer: str = envelope.get("producer", "")
        payload: dict[str, Any] = envelope.get("payload", {})
        signature: str = envelope.get("signature", "")

        public_key = _get_producer_public_key(producer)

        if public_key is None:
            print(f"[SEGURANÇA] Chave pública de '{producer}' não encontrada. Evento descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if not verify_signature(public_key, payload, signature):
            print(f"[SEGURANÇA] Assinatura inválida de '{producer}' em '{routing_key}'. Evento descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        event_data = payload
        order_id = event_data["order_id"]
        
        print(f"\n[RECEBIDO] Pagamento aprovado para o Pedido {order_id}. Iniciando expedição...")

        detalhes_envio: dict[str, Any] = service.prepare_dispatch(order_id)

        publish_event(
            routing_key='pedido.enviado',
            payload=detalhes_envio,
            private_key=private_key
        )

        print(f"[PUBLICADO] 'pedido.enviado' para o Pedido {order_id}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
    
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    print(" [*] Microsserviço de Entrega ativo e aguardando 'pagamento.aprovado'.")
    channel.start_consuming()

if __name__ == '__main__':
    private_key: RSAPrivateKey = ensure_keys(service_name=SERVICE_NAME, base_dir=BASE_DIR)

    delivery_service = DeliveryService()
    start_consumer(delivery_service, private_key)
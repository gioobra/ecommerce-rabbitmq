import sys
import json
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

SERVICE_NAME: str = 'inventory'

BASE_DIR: Path = Path(__file__).resolve().parent.parent

_public_key_cache: dict[str, Any] = {}

class InventoryService:
    def __init__(self) -> None:
        # Estrutura em memória: { "nome_do_item": quantidade }
        self._estoque: dict[str, int] = {
            "notebook" : 5,
            "mouse" : 10,
            "teclado": 8,
            "monitor":2
        }
        # Histórico de reservas ativas por pedido: { "order_id": [{"item": str, "quantidade": int}] }
        self._reservas: dict[str, list[dict[str, Any]]] = {}

    def reserve(self, order_id: str, itens: list[dict[str, Any]]) -> bool:
        '''
        Verifica se há quantidade suficiente de todos os itens e realiza a baixa.
        '''
        for item in itens:
            nome: str = item["item"].lower()
            qtd_solicidada: int = int(item["quantidade"])
            qtd_atual = self._estoque.get(nome, 0)
            
            if qtd_atual < qtd_solicidada:
                print(f"[RECUSADO] Item: {nome} | Solicitado: {qtd_solicidada} | Disponível: {qtd_atual}")
                return False

        for item in itens:
            nome = item["item"].lower()
            qtd_solicidada = int(item["quantidade"])
            self._estoque[nome] -= qtd_solicidada
            print(f"[RESERVADO] {nome} X{qtd_solicidada} (Restante: {self._estoque[nome]})")
        
        self._reservas[order_id] = itens
        return True

    def restore(self, order_id: str) -> bool:
        '''
        Devolve os produtos de um pedido cancelado
        '''
        itens_reservados: list[dict[str, Any]] | None = self._reservas.pop(order_id, None)

        if not itens_reservados:
            print(f"[INFO] Pedido {order_id} não possuía reserva ativa para ser estornada.")
            return False

        for item in itens_reservados:
            nome: str = item["item"].lower()
            qtd: int = int(item["quantidade"])
            self._estoque[nome] = self._estoque.get(nome, 0) + qtd
            print(f"[DEVOLVIDO] {nome} X{qtd} estornado o Pedido {order_id} (Nova Quantidade: {self._estoque[nome]})")

        return True
    
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

def start_consumer(service: InventoryService, private_key: RSAPrivateKey) -> None:
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    queue_name: str = 'inventory_events_queue'
    channel.queue_declare(queue=queue_name, durable=True)

    channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key='pedido.criado')
    channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key='pedido.excluido')

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
        
        if routing_key == 'pedido.criado':
            itens = event_data.get("itens", [])
            print(f"\n[EVENTO] Analisando reserva para o Pedido: {order_id}")

            if service.reserve(order_id, itens):
                publish_event(
                    routing_key='pedido.estoque_ok',
                    payload={
                        "order_id": order_id,
                        "itens": itens,
                        "status": "ESTOQUE_OK"
                    },
                    private_key=private_key
                )
                print(f"[PUBLICADO] 'pedido.estoque_ok' para Pedido {order_id}")
            else:
                publish_event(
                    routing_key='estoque.indisponivel',
                    payload={
                        "order_id": order_id,
                        "status": "ESTOQUE_INDISPONIVEL",
                        "motivo": "Produtos indisponíveis no estoque"
                    },
                    private_key=private_key
                )
                print(f"[PUBLICADO] 'estoque.indisponivel' para Pedido {order_id}")
        
        elif routing_key == 'pedido.excluido':
            print(f"\n[EVENTO] Processando estorno para o Pedido: {order_id}")
            service.restore(order_id)

        ch.basic_ack(delivery_tag=method.delivery_tag)
    
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    print(" [*] Estoque ativo, escutando 'pedido.criado' e 'pedido.excluido'. Aguardando...")
    channel.start_consuming()

if __name__ == '__main__':
    private_key: RSAPrivateKey = ensure_keys(service_name=SERVICE_NAME, base_dir=BASE_DIR)

    service = InventoryService()
    start_consumer(service, private_key)
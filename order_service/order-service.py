import sys
import json
import threading
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

SERVICE_NAME: str = 'order'

BASE_DIR: Path = Path(__file__).resolve().parent.parent

_public_key_cache: dict[str, Any] = {}


class OrderService:
    def __init__(self) -> None:
        # Estrutura em memória: { "order_id": {"id": str, "itens": list, "status": str} }
        self._orders: dict[str, dict[str, Any]] = {}
    
    def create_order(self, itens: list[dict[str, Any]]) -> dict[str, Any]:
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


def publish_event(routing_key: str, payload: dict[str, Any], private_key: RSAPrivateKey) -> None:
   
    assinatura: str = sign_payload(private_key, payload)

    envelope: dict[str, Any] = {
        "event_type": routing_key,
        "producer": SERVICE_NAME,
        "payload": payload,
        "signature": assinatura,
    }

    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT))
    channel = connection.channel()
    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    channel.basic_publish(
        exchange=EXCHANGE_NAME,
        routing_key=routing_key,
        body=json.dumps(envelope).encode('utf-8'),
        properties=pika.BasicProperties(
            delivery_mode=2,  # Mensagem persistente
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


def start_consumer(service: OrderService, private_key: RSAPrivateKey) -> None:
    ''' 
    Inicia o consumidor para receber eventos do RabbitMQ
    '''
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection: pika.BlockingConnection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    queue_name: str = 'order_updates_queue'
    channel.queue_declare(queue=queue_name, durable=True)

    routing_keys: list[str] = [
        'pedido.estoque_ok',
        'estoque.indisponivel',
        'pagamento.aprovado',
        'pagamento.recusado',
        'pedido.enviado'
    ]

    for rk in routing_keys:
        channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key=rk)
    
    status_mapping: dict[str, str] = {
        'pedido.estoque_ok': 'ESTOQUE_OK',
        'estoque.indisponivel': 'ESTOQUE_INDISPONIVEL',
        'pagamento.aprovado': 'PAGAMENTO_APROVADO',
        'pagamento.recusado': 'PAGAMENTO_RECUSADO',
        'pedido.enviado': 'ENVIADO'
    }

    def callback(ch, method, properties, body: bytes) -> None:
        
        envelope = json.loads(body.decode('utf-8'))
        routing_key = method.routing_key

        producer: str = envelope.get("producer", "")
        payload: dict[str, Any] = envelope.get("payload", {})
        signature: str = envelope.get("signature", "")

        public_key = _get_producer_public_key(producer)

        if public_key is None:
            print(f"\n[SEGURANÇA] Chave pública de '{producer}' não encontrada. Evento descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if not verify_signature(public_key, payload, signature):
            print(f"\n[SEGURANÇA] Assinatura inválida de '{producer}' em '{routing_key}'. Evento descartado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        event_data = payload
        order_id = event_data.get("order_id")

        if order_id:
            novo_status: str = event_data.get("status") or status_mapping.get(routing_key, routing_key)
            service.update_status(order_id, novo_status)

            if routing_key in ('estoque.indisponivel', 'pagamento.recusado'):
                publish_event(
                    routing_key='pedido.excluido',
                    payload={
                        "order_id": order_id,
                        "motivo": f"Falha detectada via {routing_key}"
                    },
                    private_key=private_key
                )
                print(f"\n[COMPENSAÇÃO] 'pedido.excluido' disparado para Pedido {order_id}")
                print("> Escolha uma opcao: ", end="", flush=True)

        ch.basic_ack(delivery_tag=method.delivery_tag) 

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)
    channel.start_consuming()


def cli_menu(service: OrderService, private_key: RSAPrivateKey) -> None:
    while True:
        print("\n ### Painel de Pedidos ###")
        print("1. Fazer Pedido")
        print("2. Listar Meus Pedidos")
        print("3. Cancelar Pedido")
        print("0. Sair")

        opcao = input("\n> Escolha uma opcao: ").strip()

        if opcao == "1":
            nome_item = input("\nInforme o nome do item: ").strip()
            qtd_item = input("Quantidade: ").strip()

            try:
                qtd = int(qtd_item)
            except ValueError:
                print("\n[!] Quantidade Inválida. \n")
                time.sleep(1.5)
                continue
                
            item = {"item": nome_item, "quantidade": qtd}
            pedido = service.create_order([item])

            publish_event(
                routing_key='pedido.criado',
                payload={
                    "order_id": pedido["id"],
                    "itens": pedido["itens"],
                    "status": pedido["status"],
                    "criado_em": time.time()
                },
                private_key=private_key
            )
            print(f"\n[OK] Pedido {pedido['id']} feito! \n ")
            time.sleep(1.5)
        
        elif opcao == "2":
            pedidos = service.list_orders()
            if not pedidos:
                print("\n Nenhum pedido cadastrado. ")
                time.sleep(1.5)
            else:
                for p in pedidos:
                    itens_str = ", ".join(f"{i['item']} (x{i['quantidade']})" for i in p["itens"])
                    print(f"\n ID: {p['id']} | Status: {p['status']:<12} | Itens: {itens_str}\n")
                input("\nPressione [Enter] para voltar ao menu...")
        
        elif opcao == "3":
            pid = input("\nID do pedido a cancelar: ").strip()
            if service.delete_order(pid):
                publish_event(
                    routing_key='pedido.excluido',
                    payload={"order_id": pid, "motivo": "Cancelamento manual pelo usuario"},
                    private_key=private_key
                )
                print(f"\n[OK] Pedido {pid} cancelado e evento 'pedido.excluido' enviado.\n")
                time.sleep(1.5)
            else:
                print("\n[!] Pedido não encontrado.\n")
                time.sleep(1.5)
            
        elif opcao == "0":
            print("\n Encerrando aplicação...\n")
            break


if __name__ == '__main__':
    private_key: RSAPrivateKey = ensure_keys(service_name=SERVICE_NAME, base_dir=BASE_DIR)

    order_service = OrderService()

    consumer_thread = threading.Thread(
        target=start_consumer,
        args=(order_service, private_key),
        daemon=True
    )
    consumer_thread.start()

    cli_menu(order_service, private_key)
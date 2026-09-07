import json
import threading
import time
import uuid
from typing import Any

import pika

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
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection: pika.BlockingConnection = pika.BlockingConnection(parameters)
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
                print("\n [!] Quantidade Inválida. \n")
                time.sleep(1.5)
                continue
                
            item = {"item": nome_item, "quantidade": qtd}
            pedido = service.create_order([item])

            publish_event(
                routing_key='pedido.criado',
                payload=pedido
            )
            print(f"\n [OK] Pedido {pedido['id']} feito! \n ")
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
                    routing_key='pedido.cancelado',
                    payload={"order_id": pid}
                )
                print(f"\n [OK] Pedido {pid} cancelado.\n")
                time.sleep(1.5)
            else:
                print("\n [!] Pedido não encontrado.\n")
                time.sleep(1.5)
            
        elif opcao == "0":
            print("\n Encerrando aplicação...\n")
            break

if __name__ == '__main__':
    order_service = OrderService()

    consumer_thread = threading.Thread(
        target=start_consumer,
        args=(order_service,),
        daemon=True
    )
    consumer_thread.start()

    cli_menu(order_service)
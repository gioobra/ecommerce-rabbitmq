import json
from typing import Any

import pika

# Configuração do RabbitMQ
RABBITMQ_HOST: str = 'localhost'
RABBITMQ_PORT: int = 5672
EXCHANGE_NAME: str = 'eCommerce'

class StockService:
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

def start_consumer(service: StockService) -> None:
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type='direct', durable=True)

    queue_name: str = 'stock_events_queue'
    channel.queue_declare(queue=queue_name, durable=True)

    channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key='pedido.criado')
    channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name, routing_key='pedido.excluido')

    def callback(ch, method, properties, body: bytes) -> None:
        '''
        Função para definir o que fazer quando uma mensagem nova chegar na fila
        '''
        event_data = json.loads(body.decode('utf-8'))
        routing_key = method.routing_key
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
                    }
                )
                print(f"[PUBLICADO] 'pedido.estoque_ok' para Pedido {order_id}")
            else:
                publish_event(
                    routing_key='estoque.indisponivel',
                    payload={
                        "order_id": order_id,
                        "status": "ESTOQUE_INDISPONIVEL",
                        "motivo": "Produtos indisponíveis no estoque"
                    }
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
    service = StockService()
    start_consumer(service)
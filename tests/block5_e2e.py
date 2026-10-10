import json
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "shared"))

import httpx
import pika
from utils import ensure_keys
from events import *
import messaging as m

GW, INV = "http://localhost:8000", "http://localhost:8001"
CLIENT = f"cli-{uuid.uuid4().hex[:6]}"
ESPERA_ENVIO = 15.0  # a Entrega simula 3s de despacho


class Spy:
    """Fila exclusiva ligada a pedido.enviado: conta quantos eventos saíram de cada pedido."""

    def __init__(self):
        self.conn = pika.BlockingConnection(m._conn_params())
        self.ch = self.conn.channel()
        self.ch.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="direct", durable=True)
        self.queue = self.ch.queue_declare(queue="", exclusive=True).method.queue
        self.ch.queue_bind(exchange=EXCHANGE_NAME, queue=self.queue, routing_key=PEDIDO_ENVIADO)
        self.counts: Counter = Counter()

    def drain(self) -> Counter:
        while True:
            method, _, body = self.ch.basic_get(self.queue, auto_ack=True)
            if method is None:
                return self.counts
            self.counts[json.loads(body)["payload"]["order_id"]] += 1


def poll(fn, timeout=6.0, every=0.2):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(every)
    return None


def order(oid):
    return httpx.get(f"{GW}/pedidos/{oid}", params={"client_id": CLIENT}, timeout=5).json()


def wait_status(oid, wanted, timeout=6.0):
    return poll(lambda: order(oid)["status"] == wanted, timeout)


def new_order(pid, qty=1):
    r = httpx.post(f"{GW}/pedidos", json={"client_id": CLIENT, "itens": [{"produto_id": pid, "quantidade": qty}]}, timeout=5)
    return r.json()["order_id"]


def click(oid, action):
    return httpx.post(f"{order(oid)['checkout_url']}/{action}", timeout=8)


def nf_number(oid):
    nf = order(oid)["nota_fiscal"] or ""
    return int(nf[3:]) if re.fullmatch(r"NF-\d{6}", nf) else None


def main() -> int:
    m.configure_logging()
    payment_pub = m.Publisher("payment", ensure_keys("payment", BASE_DIR))
    for svc in ("order", "inventory", "delivery"):
        ensure_keys(svc, BASE_DIR)
    spy = Spy()

    candidatos = [p for p in httpx.get(f"{GW}/produtos", timeout=5).json() if p["quantidade"] >= 5]
    if not candidatos:
        print("Nenhum produto com pelo menos 5 unidades. Reponha: pare o Estoque, apague "
              "inventory_service/inventory.db* e suba de novo.")
        return 2
    alvo = candidatos[0]
    pid = alvo["id"]
    print(f"Produto de teste: {alvo['nome']} (id={pid}, estoque={alvo['quantidade']}), cliente={CLIENT}")
    res = []

    # ---- pedido 1: fluxo feliz inteiro, sem nenhum evento simulado
    o1 = new_order(pid)
    res.append(("pedido chega sozinho a AGUARDANDO_PAGAMENTO", bool(wait_status(o1, "AGUARDANDO_PAGAMENTO"))))
    click(o1, "aprovar")
    res.append(("clique em Aprovado leva a PAGAMENTO_APROVADO", bool(wait_status(o1, "PAGAMENTO_APROVADO"))))
    res.append(("pedido chega sozinho a ENVIADO (Entrega real)", bool(wait_status(o1, "ENVIADO", ESPERA_ENVIO))))
    n1 = nf_number(o1)
    res.append(("nota fiscal no formato NF-000123", n1 is not None))
    hist = [h["status"] for h in order(o1)["historico"]]
    res.append(("histórico completo em ordem", hist == [
        "CRIADO", "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO", "PAGAMENTO_APROVADO", "ENVIADO"]))

    # ---- pedido 2: nota fiscal diferente e maior
    o2 = new_order(pid)
    wait_status(o2, "AGUARDANDO_PAGAMENTO")
    click(o2, "aprovar")
    res.append(("segundo pedido também chega a ENVIADO", bool(wait_status(o2, "ENVIADO", ESPERA_ENVIO))))
    n2 = nf_number(o2)
    res.append(("segundo pedido recebe nota fiscal diferente e maior", n1 is not None and n2 is not None and n2 > n1))

    # ---- idempotência: pagamento.aprovado repetido não gera segundo envio
    payment_pub.publish(PAGAMENTO_APROVADO, {"order_id": o1, "charge_id": "repetido", "valor": 1.0})
    time.sleep(2.0)
    res.append(("pagamento.aprovado repetido não muda o pedido",
                order(o1)["status"] == "ENVIADO" and nf_number(o1) == n1 and len(order(o1)["historico"]) == 5))

    # ---- pedido 3: pagamento recusado nunca é enviado
    o3 = new_order(pid)
    wait_status(o3, "AGUARDANDO_PAGAMENTO")
    click(o3, "recusar")
    wait_status(o3, "PAGAMENTO_RECUSADO")
    time.sleep(4.5)
    res.append(("pedido recusado NÃO é enviado", order(o3)["status"] == "PAGAMENTO_RECUSADO"))

    # ---- pedido 4: cancelado antes do pagamento nunca é enviado
    o4 = new_order(pid)
    wait_status(o4, "AGUARDANDO_PAGAMENTO")
    httpx.delete(f"{GW}/pedidos/{o4}", params={"client_id": CLIENT}, timeout=5)
    wait_status(o4, "CANCELADO")
    click(o4, "aprovar")
    time.sleep(4.5)
    res.append(("pedido cancelado NÃO é enviado", order(o4)["status"] == "CANCELADO"))

    # ---- contagem direta no RabbitMQ (fila espiã)
    contagem = spy.drain()
    res.append(("exatamente 1 pedido.enviado por pedido enviado", contagem[o1] == 1 and contagem[o2] == 1))
    res.append(("nenhum pedido.enviado para recusado/cancelado", contagem[o3] == 0 and contagem[o4] == 0))

    for name, ok in res:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in res) else 1


if __name__ == "__main__":
    sys.exit(main())
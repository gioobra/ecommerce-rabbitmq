"""Gate do Bloco 2. Exige, todos no ar:
    docker compose up -d
    uvicorn inventory_service.inventory_app:app --port 8001
    uvicorn order_service.gateway_app:app --port 8000
Pagamento e Entrega ainda não existem: este script publica os eventos deles.
Rode na raiz: python scripts/block2_e2e.py
"""
import sys
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "shared"))

import httpx
from utils import ensure_keys
from events import *
import messaging as m

GW = "http://localhost:8000"
INV = "http://localhost:8001"
CLIENT = f"cli-{uuid.uuid4().hex[:6]}"


def poll(fn, timeout=5.0, every=0.25):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(every)
    return None


def order(oid, client=CLIENT):
    return httpx.get(f"{GW}/pedidos/{oid}", params={"client_id": client}, timeout=5).json()


def wait_status(oid, status, timeout=5.0):
    return poll(lambda: order(oid)["status"] == status, timeout)


def stock(pid):
    return next((p["quantidade"] for p in httpx.get(f"{INV}/produtos", timeout=5).json() if p["id"] == pid), 0)


def new_order(pid, qty):
    r = httpx.post(f"{GW}/pedidos", json={"client_id": CLIENT, "itens": [{"produto_id": pid, "quantidade": qty}]}, timeout=5)
    return r.status_code, r.json()


def main() -> int:
    m.configure_logging()
    payment = m.Publisher("payment", ensure_keys("payment", BASE_DIR))
    delivery = m.Publisher("delivery", ensure_keys("delivery", BASE_DIR))
    ensure_keys("order", BASE_DIR)
    ensure_keys("inventory", BASE_DIR)

    produtos = httpx.get(f"{GW}/produtos", timeout=5)
    alvo = next(p for p in produtos.json() if p["quantidade"] >= 4)
    pid, q0, preco = alvo["id"], alvo["quantidade"], alvo["preco"]
    print(f"Produto de teste: {alvo['nome']} (id={pid}, estoque={q0}), cliente={CLIENT}")
    res = []

    res.append(("GET /produtos do Gateway == Estoque", produtos.status_code == 200
                and produtos.json() == httpx.get(f"{INV}/produtos", timeout=5).json()))

    bad = httpx.post(f"{GW}/pedidos", json={"client_id": CLIENT, "itens": [{"produto_id": pid, "quantidade": 0}]}, timeout=5)
    res.append(("quantidade 0 devolve 422", bad.status_code == 422))

    # fluxo feliz
    code, o = new_order(pid, 2)
    oid = o["order_id"]
    res.append(("POST /pedidos devolve 202 e status CRIADO", code == 202 and o["status"] == "CRIADO"))
    res.append(("pedido vai a ESTOQUE_CONFIRMADO com valor_total",
                bool(wait_status(oid, "ESTOQUE_CONFIRMADO")) and order(oid)["valor_total"] == preco * 2))
    res.append(("estoque baixou 2", poll(lambda: stock(pid) == q0 - 2) is True))

    payment.publish(PAGAMENTO_LINK_CRIADO, {"order_id": oid, "checkout_url": "http://localhost:9000/checkout/x"})
    res.append(("link_criado -> AGUARDANDO_PAGAMENTO com checkout_url",
                bool(wait_status(oid, "AGUARDANDO_PAGAMENTO")) and order(oid)["checkout_url"].endswith("/checkout/x")))
    payment.publish(PAGAMENTO_APROVADO, {"order_id": oid})
    res.append(("pagamento.aprovado -> PAGAMENTO_APROVADO", bool(wait_status(oid, "PAGAMENTO_APROVADO"))))
    cancel = httpx.delete(f"{GW}/pedidos/{oid}", params={"client_id": CLIENT}, timeout=5)
    res.append(("cancelar após aprovado devolve 409", cancel.status_code == 409))
    delivery.publish(PEDIDO_ENVIADO, {"order_id": oid, "nota_fiscal": "NF-TESTE"})
    res.append(("pedido.enviado -> ENVIADO", bool(wait_status(oid, "ENVIADO")) and order(oid)["nota_fiscal"] == "NF-TESTE"))
    hist = [h["status"] for h in order(oid)["historico"]]
    res.append(("histórico na ordem certa", hist == ["CRIADO", "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO",
                                                    "PAGAMENTO_APROVADO", "ENVIADO"]))

    # recusa: estoque volta por compensação
    base = stock(pid)
    _, o2 = new_order(pid, 1)
    wait_status(o2["order_id"], "ESTOQUE_CONFIRMADO")
    payment.publish(PAGAMENTO_RECUSADO, {"order_id": o2["order_id"]})
    res.append(("pagamento.recusado -> PAGAMENTO_RECUSADO", bool(wait_status(o2["order_id"], "PAGAMENTO_RECUSADO"))))
    res.append(("recusa devolve o estoque (compensação)", poll(lambda: stock(pid) == base) is True))

    # estoque insuficiente
    _, o3 = new_order(pid, 100)
    res.append(("acima do estoque -> ESTOQUE_INDISPONIVEL", bool(wait_status(o3["order_id"], "ESTOQUE_INDISPONIVEL"))))

    # cancelamento
    base = stock(pid)
    _, o4 = new_order(pid, 1)
    wait_status(o4["order_id"], "ESTOQUE_CONFIRMADO")
    c1 = httpx.delete(f"{GW}/pedidos/{o4['order_id']}", params={"client_id": CLIENT}, timeout=5)
    res.append(("DELETE cancela (200, CANCELADO)", c1.status_code == 200 and c1.json()["status"] == "CANCELADO"))
    res.append(("cancelamento devolve o estoque", poll(lambda: stock(pid) == base) is True))
    c2 = httpx.delete(f"{GW}/pedidos/{o4['order_id']}", params={"client_id": CLIENT}, timeout=5)
    res.append(("cancelar de novo devolve 409", c2.status_code == 409))

    # isolamento entre clientes
    outros = httpx.get(f"{GW}/pedidos", params={"client_id": "outro-cliente"}, timeout=5).json()
    mine = httpx.get(f"{GW}/pedidos", params={"client_id": CLIENT}, timeout=5).json()
    res.append(("outro cliente não vê meus pedidos", outros == [] and len(mine) == 4))
    res.append(("outro cliente não acessa meu pedido (403)",
                httpx.get(f"{GW}/pedidos/{oid}", params={"client_id": "outro-cliente"}, timeout=5).status_code == 403))

    for name, ok in res:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in res) else 1


if __name__ == "__main__":
    sys.exit(main())
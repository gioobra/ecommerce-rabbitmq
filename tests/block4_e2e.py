"""Gate do Bloco 4 (Mock de Pagamento + MS Pagamento). Exige:
    docker compose up -d
    uvicorn inventory_service.inventory_app:app --port 8001
    uvicorn order_service.gateway_app:app --port 8000 --timeout-graceful-shutdown 3
    uvicorn payment_service.payment_app:app --port 8002
    uvicorn mock_payment.mock_app:app --port 9000
 Entrega continua simulada.
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

GW, INV, PAY, MOCK = "http://localhost:8000", "http://localhost:8001", "http://localhost:8002", "http://localhost:9000"
CLIENT = f"cli-{uuid.uuid4().hex[:6]}"


def poll(fn, timeout=6.0, every=0.15):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(every)
    return None


def order(oid):
    return httpx.get(f"{GW}/pedidos/{oid}", params={"client_id": CLIENT}, timeout=5).json()


def status(oid):
    return order(oid)["status"]


def wait_status(oid, wanted, timeout=6.0):
    return poll(lambda: status(oid) == wanted, timeout)


def stock(pid):
    return next((p["quantidade"] for p in httpx.get(f"{INV}/produtos", timeout=5).json() if p["id"] == pid), 0)


def new_order(pid, qty=1):
    r = httpx.post(f"{GW}/pedidos", json={"client_id": CLIENT, "itens": [{"produto_id": pid, "quantidade": qty}]}, timeout=5)
    return r.json()["order_id"]


def charge_id_of(checkout_url):
    return checkout_url.rsplit("/", 1)[1]


def click(checkout_url, action):
    return httpx.post(f"{checkout_url}/{action}", timeout=8)


def main() -> int:
    m.configure_logging()
    inventory_pub = m.Publisher("inventory", ensure_keys("inventory", BASE_DIR))
    for svc in ("order", "payment"):
        ensure_keys(svc, BASE_DIR)

    candidatos = [p for p in httpx.get(f"{GW}/produtos", timeout=5).json() if p["quantidade"] >= 4]
    if not candidatos:
        print("Nenhum produto com pelo menos 4 unidades. Reponha: pare o Estoque, apague "
              "inventory_service/inventory.db* e suba de novo.")
        return 2
    alvo = candidatos[0]
    pid, preco = alvo["id"], alvo["preco"]
    print(f"Produto de teste: {alvo['nome']} (id={pid}, estoque={alvo['quantidade']}), cliente={CLIENT}")
    res = []

    # 1) fluxo real até o link de pagamento, sem nenhum evento simulado
    o1 = new_order(pid)
    res.append(("pedido chega sozinho a AGUARDANDO_PAGAMENTO", bool(wait_status(o1, "AGUARDANDO_PAGAMENTO"))))
    url1 = order(o1)["checkout_url"] or ""
    res.append(("checkout_url aponta para o Mock", url1.startswith(f"{MOCK}/checkout/ch_")))

    # 2) página de checkout
    page = httpx.get(url1, timeout=5)
    res.append(("página de checkout tem os dois botões", page.status_code == 200
                and "Pagamento Aprovado" in page.text and "Pagamento Recusado" in page.text))

    # 3) idempotência de estoque_ok duplicado: não cria 2ª cobrança
    cobranca_antes = httpx.get(f"{PAY}/cobrancas/{o1}", timeout=5).json()
    inventory_pub.publish(PEDIDO_ESTOQUE_OK, {"order_id": o1, "valor_total": preco, "itens": []})
    time.sleep(1.2)
    cobranca_depois = httpx.get(f"{PAY}/cobrancas/{o1}", timeout=5).json()
    res.append(("estoque_ok duplicado não cria outra cobrança",
                cobranca_antes["charge_id"] == cobranca_depois["charge_id"] == charge_id_of(url1)))

    # 4) webhook protegido
    corpo = {"charge_id": charge_id_of(url1), "order_id": o1, "status": "APROVADO"}
    res.append(("webhook sem token devolve 401", httpx.post(f"{PAY}/webhook", json=corpo, timeout=5).status_code == 401))
    res.append(("webhook com token errado devolve 401",
                httpx.post(f"{PAY}/webhook", json=corpo, headers={"X-Webhook-Token": "errado"}, timeout=5).status_code == 401))
    res.append(("pedido segue aguardando após webhook falso", status(o1) == "AGUARDANDO_PAGAMENTO"))

    # 5) clique em Aprovado
    r = click(url1, "aprovar")
    res.append(("clicar em Aprovado avisa a loja", r.status_code == 200 and "A loja foi avisada" in r.text))
    res.append(("pedido vai a PAGAMENTO_APROVADO", bool(wait_status(o1, "PAGAMENTO_APROVADO"))))
    tamanho = len(order(o1)["historico"])
    click(url1, "aprovar")
    time.sleep(1.0)
    res.append(("segundo clique não duplica evento", status(o1) == "PAGAMENTO_APROVADO"
                and len(order(o1)["historico"]) == tamanho == 4))
    res.append(("trocar a decisão depois não é permitido", "já finalizado" in click(url1, "recusar").text
                and status(o1) == "PAGAMENTO_APROVADO"))

    # 6) recusa devolve o estoque (compensação)
    base = stock(pid)
    o2 = new_order(pid)
    wait_status(o2, "AGUARDANDO_PAGAMENTO")
    estoque_reservado = poll(lambda: stock(pid) == base - 1)
    click(order(o2)["checkout_url"], "recusar")
    res.append(("pedido vai a PAGAMENTO_RECUSADO", bool(wait_status(o2, "PAGAMENTO_RECUSADO"))))
    res.append(("recusa devolve o estoque", bool(estoque_reservado) and bool(poll(lambda: stock(pid) == base))))

    # 7) cancelar durante o pagamento: o clique posterior é rejeitado e nada é publicado
    o3 = new_order(pid)
    wait_status(o3, "AGUARDANDO_PAGAMENTO")
    url3 = order(o3)["checkout_url"]
    httpx.delete(f"{GW}/pedidos/{o3}", params={"client_id": CLIENT}, timeout=5)
    res.append(("pedido vai a CANCELADO", bool(wait_status(o3, "CANCELADO"))))
    res.append(("Pagamento marca a cobrança como CANCELADO",
                bool(poll(lambda: httpx.get(f"{PAY}/cobrancas/{o3}", timeout=5).json()["status"] == "CANCELADO"))))
    r = click(url3, "aprovar")
    time.sleep(1.0)
    res.append(("clique após cancelar é rejeitado pela loja", "não aceitou este pagamento" in r.text))
    res.append(("pedido cancelado continua CANCELADO", status(o3) == "CANCELADO"))

    for name, ok in res:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in res) else 1


if __name__ == "__main__":
    sys.exit(main())
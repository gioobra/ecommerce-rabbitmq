import json
import sys
import threading
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
TAG = uuid.uuid4().hex[:6]
CLIENT, OTHER = f"cli-{TAG}", f"outro-{TAG}"


class Reader(threading.Thread):
    """Conexão SSE em thread: guarda todos os eventos recebidos."""

    def __init__(self, client_id):
        super().__init__(daemon=True)
        self.url = f"{GW}/stream/{client_id}"
        self.events, self.lock = [], threading.Lock()
        self.start()

    def run(self):
        try:
            with httpx.stream("GET", self.url, timeout=httpx.Timeout(10, read=None)) as r:
                name = None
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:") and name:
                        with self.lock:
                            self.events.append((name, json.loads(line[5:].strip()), time.time()))
                    elif line == "":
                        name = None
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            return next((d["pedidos"] for n, d, _ in self.events if n == "snapshot"), None)

    def event(self, order_id, status):
        with self.lock:
            return next(((d, t) for n, d, t in self.events
                         if n == "pedido" and d["order_id"] == order_id and d["status"] == status), None)

    def statuses(self, order_id):
        with self.lock:
            return [d["status"] for n, d, _ in self.events if n == "pedido" and d["order_id"] == order_id]

    def pedido_events(self):
        with self.lock:
            return [d for n, d, _ in self.events if n == "pedido"]


def poll(fn, timeout=5.0, every=0.1):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(every)
    return None


def new_order(pid, qty, client=CLIENT):
    r = httpx.post(f"{GW}/pedidos", json={"client_id": client, "itens": [{"produto_id": pid, "quantidade": qty}]}, timeout=5)
    return r.json()["order_id"]


def main() -> int:
    m.configure_logging()
    payment = m.Publisher("payment", ensure_keys("payment", BASE_DIR))
    delivery = m.Publisher("delivery", ensure_keys("delivery", BASE_DIR))
    ensure_keys("order", BASE_DIR)
    ensure_keys("inventory", BASE_DIR)

    produtos = httpx.get(f"{GW}/produtos", timeout=5).json()
    alvo = next(p for p in produtos if p["quantidade"] >= 5)
    pid, preco = alvo["id"], alvo["preco"]
    print(f"Produto de teste: {alvo['nome']} (id={pid}), cliente={CLIENT}")
    res = []

    tab1, outro = Reader(CLIENT), Reader(OTHER)
    res.append(("snapshot inicial vazio para cliente novo",
                poll(lambda: tab1.snapshot() is not None) is not None and tab1.snapshot() == []))
    poll(lambda: outro.snapshot() is not None)

    t0 = time.time()
    oid = new_order(pid, 2)
    ev = poll(lambda: tab1.event(oid, "ESTOQUE_CONFIRMADO"))
    res.append(("SSE: ESTOQUE_CONFIRMADO com valor_total", bool(ev) and ev[0]["pedido"]["valor_total"] == preco * 2))
    if ev:
        print(f"  latência POST -> evento SSE: {(ev[1] - t0) * 1000:.0f} ms")

    payment.publish(PAGAMENTO_LINK_CRIADO, {"order_id": oid, "checkout_url": "http://localhost:9000/checkout/x"})
    ev = poll(lambda: tab1.event(oid, "AGUARDANDO_PAGAMENTO"))
    res.append(("SSE: AGUARDANDO_PAGAMENTO com checkout_url",
                bool(ev) and ev[0]["pedido"]["checkout_url"].endswith("/checkout/x")))

    payment.publish(PAGAMENTO_APROVADO, {"order_id": oid})
    res.append(("SSE: PAGAMENTO_APROVADO", bool(poll(lambda: tab1.event(oid, "PAGAMENTO_APROVADO")))))

    delivery.publish(PEDIDO_ENVIADO, {"order_id": oid, "nota_fiscal": "NF-SSE"})
    ev = poll(lambda: tab1.event(oid, "ENVIADO"))
    res.append(("SSE: ENVIADO com nota_fiscal", bool(ev) and ev[0]["pedido"]["nota_fiscal"] == "NF-SSE"))
    res.append(("SSE: ordem dos eventos correta", tab1.statuses(oid) == [
        "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO", "PAGAMENTO_APROVADO", "ENVIADO"]))

    # recusa
    o2 = new_order(pid, 1)
    poll(lambda: tab1.event(o2, "ESTOQUE_CONFIRMADO"))
    payment.publish(PAGAMENTO_RECUSADO, {"order_id": o2})
    res.append(("SSE: PAGAMENTO_RECUSADO", bool(poll(lambda: tab1.event(o2, "PAGAMENTO_RECUSADO")))))

    # indisponível
    o3 = new_order(pid, 100)
    res.append(("SSE: ESTOQUE_INDISPONIVEL", bool(poll(lambda: tab1.event(o3, "ESTOQUE_INDISPONIVEL")))))

    # dois pedidos ao mesmo tempo
    a, b = new_order(pid, 1), new_order(pid, 1)
    ok = poll(lambda: tab1.event(a, "ESTOQUE_CONFIRMADO") and tab1.event(b, "ESTOQUE_CONFIRMADO"))
    res.append(("dois pedidos simultâneos não se misturam", bool(ok)
                and tab1.event(a, "ESTOQUE_CONFIRMADO")[0]["pedido"]["order_id"] == a
                and tab1.event(b, "ESTOQUE_CONFIRMADO")[0]["pedido"]["order_id"] == b))

    # cancelamento
    httpx.delete(f"{GW}/pedidos/{a}", params={"client_id": CLIENT}, timeout=5)
    res.append(("SSE: CANCELADO após DELETE", bool(poll(lambda: tab1.event(a, "CANCELADO")))))
    httpx.delete(f"{GW}/pedidos/{b}", params={"client_id": CLIENT}, timeout=5)

    # outro cliente não recebe nada
    time.sleep(0.5)
    res.append(("outro cliente não recebeu eventos de pedido", outro.pedido_events() == []))

    # reconexão : snapshot traz o estado atual
    tab2 = Reader(CLIENT)
    poll(lambda: tab2.snapshot() is not None)
    snap = {p["order_id"]: p["status"] for p in (tab2.snapshot() or [])}
    res.append(("nova conexão recebe snapshot com estado atual",
                snap.get(oid) == "ENVIADO" and snap.get(o2) == "PAGAMENTO_RECUSADO" and snap.get(a) == "CANCELADO"))

    # segunda aba também recebe eventos novos
    o5 = new_order(pid, 1)
    res.append(("duas abas recebem o mesmo evento", bool(poll(lambda: tab1.event(o5, "ESTOQUE_CONFIRMADO")
                                                              and tab2.event(o5, "ESTOQUE_CONFIRMADO")))))
    httpx.delete(f"{GW}/pedidos/{o5}", params={"client_id": CLIENT}, timeout=5)

    for name, ok in res:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in res) else 1


if __name__ == "__main__":
    sys.exit(main())
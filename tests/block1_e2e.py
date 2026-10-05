"""Gate do Bloco 1. Exige RabbitMQ e o MS Estoque no ar:
    uvicorn inventory_service.inventory_app:app --port 8001
Rode na raiz: python scripts/block1_e2e.py
"""
import queue
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

URL = "http://localhost:8001"
events: "queue.Queue[tuple[str, dict]]" = queue.Queue()


def stock_of(produto_id: int) -> int:
    produtos = httpx.get(f"{URL}/produtos", timeout=5).json()
    return next((p["quantidade"] for p in produtos if p["id"] == produto_id), 0)


def wait_event(routing_key: str, order_id: str, timeout: float = 5.0):
    """Espera um evento do pedido; eventos de outros pedidos são ignorados."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            et, payload = events.get(timeout=0.3)
        except queue.Empty:
            continue
        if et == routing_key and payload.get("order_id") == order_id:
            return payload
    return None


def wait_stock(produto_id: int, expected: int, timeout: float = 4.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if stock_of(produto_id) == expected:
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    m.configure_logging()
    order_key = ensure_keys("order", BASE_DIR)
    ensure_keys("inventory", BASE_DIR)
    pub = m.Publisher("order", order_key)
    m.start_consumer_thread("order", BASE_DIR, lambda et, p, e: events.put((et, p)))
    time.sleep(1)

    produtos = httpx.get(f"{URL}/produtos", timeout=5).json()
    alvo = next(p for p in produtos if p["quantidade"] >= 3)
    pid, q0 = alvo["id"], alvo["quantidade"]
    print(f"Produto de teste: {alvo['nome']} (id={pid}, estoque={q0})")

    tag = uuid.uuid4().hex[:6]
    o1, o2, o3 = f"b1-{tag}-a", f"b1-{tag}-b", f"b1-{tag}-c"
    res = []

    pub.publish(PEDIDO_CRIADO, {"order_id": o1, "itens": [{"produto_id": pid, "quantidade": 1}]})
    ev = wait_event(PEDIDO_ESTOQUE_OK, o1)
    res.append(("pedido válido gera estoque_ok com valor_total", ev is not None and ev["valor_total"] == alvo["preco"]))
    res.append(("estoque diminuiu em 1", wait_stock(pid, q0 - 1)))

    pub.publish(PEDIDO_CRIADO, {"order_id": o2, "itens": [{"produto_id": pid, "quantidade": q0 + 1000}]})
    res.append(("pedido acima do estoque gera estoque.indisponivel", wait_event(ESTOQUE_INDISPONIVEL, o2) is not None))
    res.append(("estoque não mudou após indisponível", stock_of(pid) == q0 - 1))

    pub.publish(PEDIDO_EXCLUIDO, {"order_id": o1, "motivo": "teste"})
    res.append(("pedido.excluido devolve o estoque", wait_stock(pid, q0)))
    pub.publish(PEDIDO_EXCLUIDO, {"order_id": o1, "motivo": "teste duplicado"})
    time.sleep(1.5)
    res.append(("excluido repetido não devolve em dobro", stock_of(pid) == q0))

    msg = {"order_id": o3, "itens": [{"produto_id": pid, "quantidade": 1}]}
    pub.publish(PEDIDO_CRIADO, msg)
    pub.publish(PEDIDO_CRIADO, msg)
    ok1 = wait_event(PEDIDO_ESTOQUE_OK, o3) is not None
    ok2 = wait_event(PEDIDO_ESTOQUE_OK, o3) is not None
    res.append(("criado duplicado: 2 respostas, baixa uma única vez", ok1 and ok2 and wait_stock(pid, q0 - 1)))
    pub.publish(PEDIDO_EXCLUIDO, {"order_id": o3, "motivo": "limpeza"})
    wait_stock(pid, q0)

    for name, ok in res:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in res) else 1


if __name__ == "__main__":
    sys.exit(main())
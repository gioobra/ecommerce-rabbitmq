import asyncio
import json
import socket
import threading
import time
from pathlib import Path
import sys

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "order_service"))

from events import *
import gateway_store as gs
from gateway_store import OrderStore
from gateway_handlers import make_handler
from gateway_app import create_app
from gateway_sse import SSEHub, TooManyConnections, format_sse

ITENS = [{"produto_id": 1, "quantidade": 2}]


# ------------------------------------------------------------- utilitários

class LiveServer:
    """Sobe o app em um uvicorn real: o TestClient não consome SSE infinito."""

    def __init__(self, app):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        cfg = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                             timeout_graceful_shutdown=2)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        end = time.time() + 10
        while not self.server.started and time.time() < end:
            time.sleep(0.05)
        assert self.server.started, "uvicorn não subiu"
        self.url = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(8)


class Stream:
    """Cliente SSE em thread: acumula eventos e comentários recebidos."""

    def __init__(self, url, headers=None):
        self.events, self.comments, self.headers = [], 0, {}
        self.lock = threading.Lock()
        self.opened = threading.Event()
        self._client = httpx.Client(timeout=httpx.Timeout(10, read=None))
        self._thread = threading.Thread(target=self._run, args=(url, headers), daemon=True)
        self._thread.start()

    def _run(self, url, headers):
        try:
            with self._client.stream("GET", url, headers=headers) as r:
                self.headers = dict(r.headers)
                self.opened.set()
                name = None
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:") and name:
                        with self.lock:
                            self.events.append((name, json.loads(line[5:].strip())))
                    elif line.startswith(":"):
                        with self.lock:
                            self.comments += 1
                    elif line == "":
                        name = None
        except Exception:
            pass
        finally:
            self.opened.set()

    def wait(self, pred, timeout=4.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                found = [e for e in self.events if pred(e)]
            if found:
                return found
            time.sleep(0.03)
        return []

    def statuses(self):
        with self.lock:
            return [d["status"] for n, d in self.events if n == "pedido"]

    def close(self):
        self._client.close()


def poll(fn, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.03)
    return False


class Live:
    def __init__(self, heartbeat=0.2):
        self.store = OrderStore()
        self.sent = []

        def inv(request):
            return httpx.Response(200, json=[])

        self.app = create_app(store=self.store, publish=lambda rk, p: self.sent.append((rk, p)),
                              http_client=httpx.Client(transport=httpx.MockTransport(inv)),
                              enable_messaging=False, heartbeat_seconds=heartbeat)
        self.srv = LiveServer(self.app)
        self.streams = []

    def stream(self, client_id, **kw):
        s = Stream(f"{self.srv.url}/stream/{client_id}", **kw)
        self.streams.append(s)
        assert s.opened.wait(5)
        return s

    def close(self):
        for s in self.streams:
            s.close()
        self.srv.stop()


@pytest.fixture()
def live():
    lv = Live()
    yield lv
    lv.close()


def snapshot_of(stream):
    got = stream.wait(lambda e: e[0] == "snapshot")
    assert got, "snapshot não chegou"
    return got[0][1]["pedidos"]


# ------------------------------------------------------------------- unidade

def test_format_sse():
    out = format_sse("pedido", {"a": "ç", "b": [1]})
    assert out == 'event: pedido\ndata: {"a": "ç", "b": [1]}\n\n'


def test_hub_publish_from_another_thread_reaches_loop_queue():
    async def run():
        hub = SSEHub()
        sub = hub.subscribe("c1")
        threading.Thread(target=hub.publish, args=("c1", "pedido", {"n": 1})).start()
        item = await asyncio.wait_for(sub.queue.get(), 2)
        hub.unsubscribe(sub)
        return item, hub.count()

    item, count = asyncio.run(run())
    assert item == {"event": "pedido", "data": {"n": 1}} and count == 0


def test_hub_only_delivers_to_matching_client():
    async def run():
        hub = SSEHub()
        a, b = hub.subscribe("a"), hub.subscribe("b")
        hub.publish("a", "pedido", {"n": 1})
        await asyncio.sleep(0.05)
        return a.queue.qsize(), b.queue.qsize()

    assert asyncio.run(run()) == (1, 0)


def test_hub_slow_consumer_drops_oldest():
    async def run():
        hub = SSEHub()
        sub = hub.subscribe("c1")
        for i in range(150):
            hub.publish("c1", "pedido", {"n": i})
        await asyncio.sleep(0.2)
        items = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait()["data"]["n"])
        return items

    items = asyncio.run(run())
    assert len(items) == 100 and items[-1] == 149 and items[0] == 50


def test_hub_connection_limit():
    async def run():
        hub = SSEHub(max_per_client=2)
        hub.subscribe("c1"); hub.subscribe("c1")
        with pytest.raises(TooManyConnections):
            hub.subscribe("c1")
        hub.subscribe("outro")

    asyncio.run(run())


def test_hub_publish_after_loop_closed_drops_subscription():
    hub = SSEHub()
    loop = asyncio.new_event_loop()
    hub.subscribe("c1", loop=loop)
    loop.close()
    hub.publish("c1", "pedido", {})
    assert hub.count("c1") == 0


def test_stream_rejects_invalid_client_id():
    client = TestClient(create_app(enable_messaging=False))
    assert client.get("/stream/a b").status_code == 422
    assert client.get("/stream/" + "x" * 65).status_code == 422


# --------------------------------------------------------------- SSE ao vivo

def test_snapshot_on_connect_only_has_own_orders(live):
    mine = live.store.create("c1", ITENS)
    live.store.create("c2", ITENS)
    pedidos = snapshot_of(live.stream("c1"))
    assert [p["order_id"] for p in pedidos] == [mine["order_id"]]


def test_snapshot_empty_for_new_client(live):
    assert snapshot_of(live.stream("novo")) == []


def test_sse_headers_and_cors(live):
    s = live.stream("c1", headers={"Origin": "http://localhost:5173"})
    assert s.headers["content-type"].startswith("text/event-stream")
    assert "no-cache" in s.headers["cache-control"]
    assert s.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_state_change_is_pushed_in_real_time(live):
    oid = live.store.create("c1", ITENS)["order_id"]
    s = live.stream("c1")
    snapshot_of(s)
    live.store.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "Estoque confirmado", valor_total=9000.0)
    got = s.wait(lambda e: e[0] == "pedido")
    assert got
    data = got[0][1]
    assert data["order_id"] == oid and data["status"] == "ESTOQUE_CONFIRMADO"
    assert data["pedido"]["valor_total"] == 9000.0


def test_other_client_does_not_receive_events(live):
    oid = live.store.create("c1", ITENS)["order_id"]
    s1, s2 = live.stream("c1"), live.stream("c2")
    snapshot_of(s1); snapshot_of(s2)
    live.store.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok")
    assert s1.wait(lambda e: e[0] == "pedido")
    time.sleep(0.5)
    assert s2.statuses() == []


def test_two_tabs_of_same_client_both_receive(live):
    oid = live.store.create("c1", ITENS)["order_id"]
    a, b = live.stream("c1"), live.stream("c1")
    snapshot_of(a); snapshot_of(b)
    live.store.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok")
    assert a.wait(lambda e: e[0] == "pedido") and b.wait(lambda e: e[0] == "pedido")


def test_full_flow_through_handler_reaches_stream_in_order(live):
    oid = live.store.create("c1", ITENS)["order_id"]
    s = live.stream("c1")
    snapshot_of(s)
    h = make_handler(live.store, lambda rk, p: None)
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid, "valor_total": 5.0}, {})
    h(PAGAMENTO_LINK_CRIADO, {"order_id": oid, "checkout_url": "http://mock/x"}, {})
    h(PAGAMENTO_APROVADO, {"order_id": oid}, {})
    h(PEDIDO_ENVIADO, {"order_id": oid, "nota_fiscal": "NF-1"}, {})
    assert poll(lambda: len(s.statuses()) == 4)
    assert s.statuses() == ["ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO", "PAGAMENTO_APROVADO", "ENVIADO"]


def test_duplicate_event_does_not_produce_duplicate_notification(live):
    oid = live.store.create("c1", ITENS)["order_id"]
    s = live.stream("c1")
    snapshot_of(s)
    h = make_handler(live.store, lambda rk, p: None)
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid}, {})
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid}, {})
    time.sleep(0.5)
    assert s.statuses() == ["ESTOQUE_CONFIRMADO"]


def test_events_triggered_from_background_thread(live):
    """Simula o consumer do pika: outra thread altera o estado."""
    oid = live.store.create("c1", ITENS)["order_id"]
    s = live.stream("c1")
    snapshot_of(s)
    t = threading.Thread(target=lambda: live.store.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok"))
    t.start(); t.join()
    assert s.wait(lambda e: e[0] == "pedido")


def test_cancel_via_rest_is_pushed(live):
    r = httpx.post(f"{live.srv.url}/pedidos", json={"client_id": "c1", "itens": ITENS})
    oid = r.json()["order_id"]
    s = live.stream("c1")
    snapshot_of(s)
    d = httpx.delete(f"{live.srv.url}/pedidos/{oid}", params={"client_id": "c1"})
    assert d.status_code == 200
    got = s.wait(lambda e: e[0] == "pedido")
    assert got and got[0][1]["status"] == "CANCELADO"


def test_concurrent_orders_do_not_mix(live):
    ids = [live.store.create("c1", ITENS)["order_id"] for _ in range(2)]
    s = live.stream("c1")
    snapshot_of(s)
    threads = [threading.Thread(target=live.store.transition,
                                args=(i, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok"), kwargs={"valor_total": float(n)})
               for n, i in enumerate(ids, 1)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert poll(lambda: len(s.statuses()) == 2)
    with s.lock:
        seen = {d["order_id"]: d["pedido"]["valor_total"] for n, d in s.events if n == "pedido"}
    assert seen == {ids[0]: 1.0, ids[1]: 2.0}


def test_heartbeat_comments_are_sent(live):
    s = live.stream("c1")
    snapshot_of(s)
    assert poll(lambda: s.comments >= 2, timeout=3)


def test_disconnect_removes_subscription(live):
    s = live.stream("c1")
    snapshot_of(s)
    assert poll(lambda: live.app.state.hub.count("c1") == 1)
    s.close()
    assert poll(lambda: live.app.state.hub.count("c1") == 0, timeout=5), "assinatura vazou"


def test_reconnect_receives_current_state_in_snapshot(live):
    oid = live.store.create("c1", ITENS)["order_id"]
    s1 = live.stream("c1")
    snapshot_of(s1)
    live.store.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok")
    s1.close()
    poll(lambda: live.app.state.hub.count("c1") == 0, timeout=5)
    live.store.transition(oid, gs.AGUARDANDO_PAGAMENTO, {gs.ESTOQUE_CONFIRMADO}, "link", checkout_url="http://m/x")
    pedidos = snapshot_of(live.stream("c1"))
    assert pedidos[0]["status"] == "AGUARDANDO_PAGAMENTO" and pedidos[0]["checkout_url"] == "http://m/x"


def test_connection_limit_returns_429():
    lv = Live()
    try:
        lv.app.state.hub._max = 2
        lv.stream("c1"); lv.stream("c1")
        assert poll(lambda: lv.app.state.hub.count("c1") == 2)
        assert httpx.get(f"{lv.srv.url}/stream/c1", timeout=3).status_code == 429
    finally:
        lv.close()
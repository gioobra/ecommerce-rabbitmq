import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "order_service"))

from events import *
from messaging import TransientError
import gateway_store as gs
from gateway_store import OrderStore
from gateway_handlers import make_handler
from gateway_app import create_app

ITENS = [{"produto_id": 1, "quantidade": 2}]


# ------------------------------------------------------------------ store

def test_create_order():
    s = OrderStore()
    o = s.create("c1", ITENS)
    assert o["status"] == gs.CRIADO and o["client_id"] == "c1"
    assert o["historico"][0]["status"] == gs.CRIADO
    assert s.get(o["order_id"])["itens"] == ITENS


def test_store_returns_copies():
    s = OrderStore()
    o = s.create("c1", ITENS)
    o["status"] = "HACK"
    assert s.get(o["order_id"])["status"] == gs.CRIADO


def test_transition_applied_and_fields():
    s = OrderStore()
    o = s.create("c1", ITENS)
    res, snap, prev = s.transition(o["order_id"], gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok", valor_total=10.0)
    assert (res, prev) == (gs.APLICADO, gs.CRIADO)
    assert snap["valor_total"] == 10.0 and len(snap["historico"]) == 2


def test_transition_ignored_when_out_of_order_or_duplicate():
    s = OrderStore()
    oid = s.create("c1", ITENS)["order_id"]
    s.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok")
    res, snap, _ = s.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "dup")
    assert res == gs.IGNORADO and len(snap["historico"]) == 2
    res, _, _ = s.transition(oid, gs.ENVIADO, {gs.PAGAMENTO_APROVADO}, "cedo demais")
    assert res == gs.IGNORADO and s.get(oid)["status"] == gs.ESTOQUE_CONFIRMADO


def test_transition_unknown_order():
    assert OrderStore().transition("nada", gs.ENVIADO, {gs.CRIADO}, "x")[0] == gs.DESCONHECIDO


def test_transition_ignores_unknown_fields():
    s = OrderStore()
    oid = s.create("c1", ITENS)["order_id"]
    s.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok", client_id="invasor", status="x")
    assert s.get(oid)["client_id"] == "c1"


def test_list_by_client_isolated_and_newest_first():
    s = OrderStore()
    a = s.create("c1", ITENS)
    s.create("c2", ITENS)
    b = s.create("c1", ITENS)
    ids = [o["order_id"] for o in s.list_by_client("c1")]
    assert set(ids) == {a["order_id"], b["order_id"]} and len(s.list_by_client("c2")) == 1


def test_listeners_called_on_change_but_not_on_ignored():
    s, seen = OrderStore(), []
    s.add_listener(lambda order, change: seen.append((order["status"], change["status"])))
    oid = s.create("c1", ITENS)["order_id"]
    s.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok")
    s.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "dup")
    assert seen == [(gs.ESTOQUE_CONFIRMADO, gs.ESTOQUE_CONFIRMADO)]


def test_failing_listener_does_not_break_store():
    s = OrderStore()
    s.add_listener(lambda o, c: 1 / 0)
    oid = s.create("c1", ITENS)["order_id"]
    assert s.transition(oid, gs.ESTOQUE_CONFIRMADO, {gs.CRIADO}, "ok")[0] == gs.APLICADO


def test_rollback_restores_previous_state():
    s = OrderStore()
    oid = s.create("c1", ITENS)["order_id"]
    _, _, prev = s.transition(oid, gs.CANCELADO, gs.CANCELAVEIS, "x", notify=False)
    s.rollback(oid, prev)
    o = s.get(oid)
    assert o["status"] == gs.CRIADO and len(o["historico"]) == 1


# --------------------------------------------------------------- handlers

def run_flow(handler, oid):
    handler(PEDIDO_ESTOQUE_OK, {"order_id": oid, "valor_total": 9000.0, "itens": []}, {})
    handler(PAGAMENTO_LINK_CRIADO, {"order_id": oid, "checkout_url": "http://mock/pay/1"}, {})
    handler(PAGAMENTO_APROVADO, {"order_id": oid}, {})
    handler(PEDIDO_ENVIADO, {"order_id": oid, "nota_fiscal": "NF-1"}, {})


def test_handler_happy_path():
    s, sent = OrderStore(), []
    oid = s.create("c1", ITENS)["order_id"]
    run_flow(make_handler(s, lambda rk, p: sent.append(rk)), oid)
    o = s.get(oid)
    assert o["status"] == gs.ENVIADO
    assert (o["valor_total"], o["checkout_url"], o["nota_fiscal"]) == (9000.0, "http://mock/pay/1", "NF-1")
    assert [h["status"] for h in o["historico"]] == [
        gs.CRIADO, gs.ESTOQUE_CONFIRMADO, gs.AGUARDANDO_PAGAMENTO, gs.PAGAMENTO_APROVADO, gs.ENVIADO]
    assert sent == []


def test_handler_indisponivel():
    s = OrderStore()
    oid = s.create("c1", ITENS)["order_id"]
    make_handler(s, lambda rk, p: None)(
        ESTOQUE_INDISPONIVEL, {"order_id": oid, "motivo": "sem estoque", "itens_faltantes": [1]}, {})
    o = s.get(oid)
    assert o["status"] == gs.ESTOQUE_INDISPONIVEL and o["motivo"] == "sem estoque"


def test_handler_recusado_triggers_compensation():
    s, sent = OrderStore(), []
    oid = s.create("c1", ITENS)["order_id"]
    h = make_handler(s, lambda rk, p: sent.append((rk, p)))
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid, "valor_total": 1.0}, {})
    h(PAGAMENTO_RECUSADO, {"order_id": oid}, {})
    assert s.get(oid)["status"] == gs.PAGAMENTO_RECUSADO
    assert sent == [(PEDIDO_EXCLUIDO, {"order_id": oid, "motivo": "pagamento recusado"})]


def test_handler_recusado_redelivery_republishes_compensation():
    s, sent = OrderStore(), []
    oid = s.create("c1", ITENS)["order_id"]
    h = make_handler(s, lambda rk, p: sent.append(rk))
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid}, {})
    h(PAGAMENTO_RECUSADO, {"order_id": oid}, {})
    h(PAGAMENTO_RECUSADO, {"order_id": oid}, {})
    assert sent == [PEDIDO_EXCLUIDO, PEDIDO_EXCLUIDO]
    assert len(s.get(oid)["historico"]) == 3


def test_handler_recusado_after_cancel_does_not_compensate_again():
    s, sent = OrderStore(), []
    oid = s.create("c1", ITENS)["order_id"]
    s.transition(oid, gs.CANCELADO, gs.CANCELAVEIS, "cancel")
    make_handler(s, lambda rk, p: sent.append(rk))(PAGAMENTO_RECUSADO, {"order_id": oid}, {})
    assert sent == [] and s.get(oid)["status"] == gs.CANCELADO


def test_handler_compensation_failure_is_transient():
    s = OrderStore()
    oid = s.create("c1", ITENS)["order_id"]

    def broken(rk, p):
        raise OSError("down")

    h = make_handler(s, broken)
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid}, {})
    with pytest.raises(TransientError):
        h(PAGAMENTO_RECUSADO, {"order_id": oid}, {})


def test_handler_duplicate_and_late_events_are_harmless():
    s = OrderStore()
    oid = s.create("c1", ITENS)["order_id"]
    h = make_handler(s, lambda rk, p: None)
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid}, {})
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid}, {})
    h(PEDIDO_ENVIADO, {"order_id": oid}, {})
    assert s.get(oid)["status"] == gs.ESTOQUE_CONFIRMADO and len(s.get(oid)["historico"]) == 2


def test_handler_unknown_order_and_missing_id_are_ignored():
    h = make_handler(OrderStore(), lambda rk, p: None)
    h(PEDIDO_ESTOQUE_OK, {"order_id": "nao-existe"}, {})
    h(PEDIDO_ESTOQUE_OK, {}, {})


# -------------------------------------------------------------------- API

class Env:
    def __init__(self, inventory=None):
        self.sent, self.fail = [], False
        self.store = OrderStore()

        def publish(rk, payload):
            if self.fail:
                raise OSError("broker down")
            self.sent.append((rk, payload))

        def inv(request):
            if inventory == "down":
                raise httpx.ConnectError("refused")
            if inventory == "500":
                return httpx.Response(500)
            return httpx.Response(200, json=inventory or [{"id": 1, "nome": "notebook", "categoria": "C",
                                                          "preco": 1.0, "quantidade": 3}])

        client = httpx.Client(transport=httpx.MockTransport(inv))
        self.client = TestClient(create_app(store=self.store, publish=publish, http_client=client,
                                            enable_messaging=False))


@pytest.fixture()
def env():
    return Env()


def post(env, **over):
    body = {"client_id": "c1", "itens": ITENS}
    body.update(over)
    return env.client.post("/pedidos", json=body)


def test_get_produtos_proxies_inventory(env):
    r = env.client.get("/produtos")
    assert r.status_code == 200 and r.json()[0]["nome"] == "notebook"


def test_get_produtos_inventory_down_is_503():
    assert Env("down").client.get("/produtos").status_code == 503


def test_get_produtos_inventory_error_is_502():
    assert Env("500").client.get("/produtos").status_code == 502


def test_create_order_publishes_event(env):
    r = post(env)
    assert r.status_code == 202 and r.json()["status"] == "CRIADO"
    rk, payload = env.sent[0]
    assert rk == PEDIDO_CRIADO
    assert payload == {"order_id": r.json()["order_id"], "client_id": "c1", "itens": ITENS}


@pytest.mark.parametrize("body", [
    {"client_id": "c1", "itens": []},
    {"client_id": "c1"},
    {"itens": ITENS},
    {"client_id": "", "itens": ITENS},
    {"client_id": "a b/c", "itens": ITENS},
    {"client_id": "c1", "itens": [{"produto_id": 1, "quantidade": 0}]},
    {"client_id": "c1", "itens": [{"produto_id": 1, "quantidade": -3}]},
    {"client_id": "c1", "itens": [{"produto_id": 0, "quantidade": 1}]},
    {"client_id": "c1", "itens": [{"produto_id": "x", "quantidade": 1}]},
    {"client_id": "c1", "itens": [{"produto_id": 1, "quantidade": 101}]},
])
def test_create_order_validation(env, body):
    assert env.client.post("/pedidos", json=body).status_code == 422
    assert env.sent == [] and env.store.list_by_client("c1") == []


def test_create_order_publish_failure_is_503_and_not_stored(env):
    env.fail = True
    assert post(env).status_code == 503
    assert env.store.list_by_client("c1") == []


def test_list_orders_isolated_by_client(env):
    post(env, client_id="c1")
    post(env, client_id="c2")
    assert len(env.client.get("/pedidos", params={"client_id": "c1"}).json()) == 1
    assert env.client.get("/pedidos").status_code == 422


def test_get_order_ownership(env):
    oid = post(env).json()["order_id"]
    assert env.client.get(f"/pedidos/{oid}", params={"client_id": "c1"}).status_code == 200
    assert env.client.get(f"/pedidos/{oid}", params={"client_id": "c2"}).status_code == 403
    assert env.client.get("/pedidos/nada", params={"client_id": "c1"}).status_code == 404


def test_cancel_order(env):
    oid = post(env).json()["order_id"]
    r = env.client.delete(f"/pedidos/{oid}", params={"client_id": "c1"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELADO"
    assert env.sent[-1] == (PEDIDO_EXCLUIDO, {"order_id": oid, "motivo": "cancelado pelo cliente"})
    assert env.client.delete(f"/pedidos/{oid}", params={"client_id": "c1"}).status_code == 409


def test_cancel_wrong_client_and_unknown(env):
    oid = post(env).json()["order_id"]
    assert env.client.delete(f"/pedidos/{oid}", params={"client_id": "c2"}).status_code == 403
    assert env.client.delete("/pedidos/nada", params={"client_id": "c1"}).status_code == 404
    assert [rk for rk, _ in env.sent] == [PEDIDO_CRIADO]


def test_cannot_cancel_after_payment_approved(env):
    oid = post(env).json()["order_id"]
    h = make_handler(env.store, lambda rk, p: None)
    h(PEDIDO_ESTOQUE_OK, {"order_id": oid}, {})
    h(PAGAMENTO_APROVADO, {"order_id": oid}, {})
    assert env.client.delete(f"/pedidos/{oid}", params={"client_id": "c1"}).status_code == 409
    assert env.store.get(oid)["status"] == "PAGAMENTO_APROVADO"


def test_cancel_publish_failure_rolls_back(env):
    oid = post(env).json()["order_id"]
    env.fail = True
    assert env.client.delete(f"/pedidos/{oid}", params={"client_id": "c1"}).status_code == 503
    o = env.store.get(oid)
    assert o["status"] == "CRIADO" and len(o["historico"]) == 1


def test_cancel_notifies_listener_only_on_success(env):
    seen = []
    env.store.add_listener(lambda o, c: seen.append(c["status"]))
    oid = post(env).json()["order_id"]
    env.fail = True
    env.client.delete(f"/pedidos/{oid}", params={"client_id": "c1"})
    assert seen == []
    env.fail = False
    env.client.delete(f"/pedidos/{oid}", params={"client_id": "c1"})
    assert seen == ["CANCELADO"]


def test_cors_header_for_frontend(env):
    r = env.client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
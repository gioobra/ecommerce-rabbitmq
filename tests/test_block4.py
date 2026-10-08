import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
for sub in ("shared", "payment_service", "mock_payment"):
    sys.path.insert(0, str(ROOT / sub))

from events import *
from messaging import TransientError
from payment_repository import PaymentRepository
from payment_provider import MockPaymentClient, ProviderUnavailable, ProviderRejected
from payment_handlers import make_handler
import payment_app
import mock_app


# =========================================================== repositório

@pytest.fixture()
def repo(tmp_path):
    return PaymentRepository(tmp_path / "p.db")


def test_repo_create_and_get(repo):
    assert repo.create("o1", 10.0, "ch_1", "http://m/ch_1", "tok") is True
    row = repo.get("o1")
    assert row["status"] == "PENDENTE" and row["charge_id"] == "ch_1" and row["link_publicado"] == 0
    assert repo.get("nada") is None


def test_repo_create_twice_keeps_first(repo):
    repo.create("o1", 10.0, "ch_1", "u1", "tok1")
    assert repo.create("o1", 10.0, "ch_2", "u2", "tok2") is False
    assert repo.get("o1")["charge_id"] == "ch_1"


def test_repo_cancel_cases(repo):
    assert repo.cancel("sem-cobranca") == "MARCADO"
    assert repo.create("sem-cobranca", 5.0, "ch", "u", "t") is False  # marca impede criar depois
    repo.create("o1", 10.0, "ch_1", "u", "t")
    assert repo.cancel("o1") == "CANCELADA" and repo.get("o1")["status"] == "CANCELADO"
    assert repo.cancel("o1") == "IGNORADO"
    repo.create("o2", 10.0, "ch_2", "u", "t")
    repo.decide("o2", "APROVADO")
    assert repo.cancel("o2") == "IGNORADO" and repo.get("o2")["status"] == "APROVADO"


def test_repo_decide_outcomes(repo):
    assert repo.decide("nada", "APROVADO") == "DESCONHECIDO"
    repo.create("o1", 10.0, "ch", "u", "t")
    assert repo.decide("o1", "APROVADO") == "APLICADO"
    assert repo.decide("o1", "APROVADO") == "REPETIDO"
    assert repo.decide("o1", "RECUSADO") == "CONFLITO"
    repo.create("o2", 10.0, "ch2", "u", "t")
    repo.cancel("o2")
    assert repo.decide("o2", "APROVADO") == "CANCELADO"


def test_repo_persistence(tmp_path):
    PaymentRepository(tmp_path / "x.db").create("o1", 10.0, "ch", "u", "t")
    assert PaymentRepository(tmp_path / "x.db").get("o1")["charge_id"] == "ch"


# ================================================================== mock

class Capture:
    """Cliente httpx falso: registra os webhooks e devolve a resposta configurada."""

    def __init__(self, status=200, body=None, error=False):
        self.calls, self.status, self.body, self.error = [], status, body or {}, error
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request):
        if self.error:
            raise httpx.ConnectError("loja fora do ar")
        self.calls.append(request)
        return httpx.Response(self.status, json=self.body)


def new_mock(cap=None):
    cap = cap or Capture()
    return TestClient(mock_app.create_app(http_client=cap.client, public_url="http://mock.test")), cap


def make_charge(client, order_id="o1", valor=4500.0):
    r = client.post("/cobrancas", json={"order_id": order_id, "valor": valor,
                                        "webhook_url": "http://loja/webhook", "webhook_token": "segredo-123"})
    assert r.status_code == 201
    return r.json()


def test_mock_create_charge_returns_public_checkout_url():
    client, _ = new_mock()
    data = make_charge(client)
    assert data["checkout_url"] == f"http://mock.test/checkout/{data['charge_id']}"
    assert data["status"] == "PENDENTE"


@pytest.mark.parametrize("body", [
    {"order_id": "o", "valor": 0, "webhook_url": "http://x/w", "webhook_token": "12345678"},
    {"order_id": "o", "valor": -5, "webhook_url": "http://x/w", "webhook_token": "12345678"},
    {"order_id": "o", "valor": 5, "webhook_url": "ftp://x/w", "webhook_token": "12345678"},
    {"order_id": "o", "valor": 5, "webhook_url": "http://x/w", "webhook_token": "curto"},
    {"order_id": "", "valor": 5, "webhook_url": "http://x/w", "webhook_token": "12345678"},
    {"valor": 5, "webhook_url": "http://x/w", "webhook_token": "12345678"},
])
def test_mock_create_charge_validation(body):
    client, _ = new_mock()
    assert client.post("/cobrancas", json=body).status_code == 422


def test_mock_checkout_page_has_both_buttons():
    client, _ = new_mock()
    c = make_charge(client)
    r = client.get(f"/checkout/{c['charge_id']}")
    assert r.status_code == 200 and "Pagamento Aprovado" in r.text and "Pagamento Recusado" in r.text
    assert "R$ 4.500,00" in r.text


def test_mock_checkout_unknown_charge_is_404():
    client, _ = new_mock()
    assert client.get("/checkout/ch_nada").status_code == 404


def test_mock_checkout_escapes_html():
    client, _ = new_mock()
    c = make_charge(client, order_id="<script>alert(1)</script>")
    page = client.get(f"/checkout/{c['charge_id']}").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_mock_approve_calls_webhook_with_token_and_body():
    client, cap = new_mock()
    c = make_charge(client)
    r = client.post(f"/checkout/{c['charge_id']}/aprovar")
    assert r.status_code == 200 and "Pagamento aprovado" in r.text
    assert len(cap.calls) == 1
    call = cap.calls[0]
    assert str(call.url) == "http://loja/webhook" and call.headers["x-webhook-token"] == "segredo-123"
    assert call.read() and httpx.Response(200, content=call.content).json() == {
        "charge_id": c["charge_id"], "order_id": "o1", "status": "APROVADO"}
    assert client.get(f"/cobrancas/{c['charge_id']}").json()["webhook"] == "ENTREGUE"


def test_mock_refuse_calls_webhook():
    client, cap = new_mock()
    c = make_charge(client)
    assert "Pagamento recusado" in client.post(f"/checkout/{c['charge_id']}/recusar").text
    assert httpx.Response(200, content=cap.calls[0].content).json()["status"] == "RECUSADO"


def test_mock_double_click_sends_webhook_once():
    client, cap = new_mock()
    c = make_charge(client)
    client.post(f"/checkout/{c['charge_id']}/aprovar")
    client.post(f"/checkout/{c['charge_id']}/aprovar")
    assert len(cap.calls) == 1


def test_mock_decision_cannot_be_changed():
    client, cap = new_mock()
    c = make_charge(client)
    client.post(f"/checkout/{c['charge_id']}/aprovar")
    r = client.post(f"/checkout/{c['charge_id']}/recusar")
    assert "já finalizado" in r.text and len(cap.calls) == 1
    assert client.get(f"/cobrancas/{c['charge_id']}").json()["status"] == "APROVADO"


def test_mock_checkout_after_decision_shows_result_not_buttons():
    client, _ = new_mock()
    c = make_charge(client)
    client.post(f"/checkout/{c['charge_id']}/aprovar")
    page = client.get(f"/checkout/{c['charge_id']}").text
    assert "Pagamento aprovado" in page and "<button class='no'" not in page


def test_mock_webhook_failure_allows_retry():
    cap = Capture(error=True)
    client, _ = new_mock(cap)
    c = make_charge(client)
    r = client.post(f"/checkout/{c['charge_id']}/aprovar")
    assert "não foi possível avisar a loja" in r.text
    assert client.get(f"/cobrancas/{c['charge_id']}").json()["webhook"] == "FALHOU"
    cap.error = False
    r = client.post(f"/checkout/{c['charge_id']}/aprovar")
    assert "A loja foi avisada" in r.text and len(cap.calls) == 1


def test_mock_webhook_rejected_by_store_is_reported():
    cap = Capture(status=409, body={"detail": "Pedido cancelado; pagamento ignorado"})
    client, _ = new_mock(cap)
    c = make_charge(client)
    r = client.post(f"/checkout/{c['charge_id']}/aprovar")
    assert "Pedido cancelado" in r.text
    assert client.get(f"/cobrancas/{c['charge_id']}").json()["webhook"] == "REJEITADO"


# ================================================ MS Pagamento: handler

class FakeProvider:
    def __init__(self):
        self.calls, self.fail = [], None

    def create_charge(self, order_id, valor, webhook_url, webhook_token):
        if self.fail:
            raise self.fail
        self.calls.append((order_id, valor, webhook_url, webhook_token))
        return {"charge_id": f"ch_{len(self.calls)}", "checkout_url": f"http://mock/checkout/ch_{len(self.calls)}"}


class Pub:
    def __init__(self):
        self.sent, self.fail = [], False

    def __call__(self, rk, payload):
        if self.fail:
            raise OSError("broker fora do ar")
        self.sent.append((rk, payload))


@pytest.fixture()
def h(repo):
    prov, pub = FakeProvider(), Pub()
    return make_handler(repo, pub, prov, "http://loja/webhook"), repo, prov, pub


def test_estoque_ok_creates_charge_and_publishes_link(h):
    handler, repo, prov, pub = h
    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 9000.0}, {})
    assert prov.calls[0][:3] == ("o1", 9000.0, "http://loja/webhook")
    assert len(prov.calls[0][3]) >= 16  # token por cobrança
    rk, payload = pub.sent[0]
    assert rk == PAGAMENTO_LINK_CRIADO
    assert payload["checkout_url"] == "http://mock/checkout/ch_1" and payload["order_id"] == "o1"
    assert repo.get("o1")["link_publicado"] == 1


def test_estoque_ok_duplicate_creates_single_charge(h):
    handler, repo, prov, pub = h
    for _ in range(3):
        handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 9000.0}, {})
    assert len(prov.calls) == 1 and len(pub.sent) == 1


def test_provider_unavailable_is_transient_and_retry_works(h):
    handler, repo, prov, pub = h
    prov.fail = ProviderUnavailable("down")
    with pytest.raises(TransientError):
        handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 1.0}, {})
    assert repo.get("o1") is None and pub.sent == []
    prov.fail = None
    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 1.0}, {})
    assert len(pub.sent) == 1


def test_provider_rejected_is_dropped_without_retry(h):
    handler, repo, prov, pub = h
    prov.fail = ProviderRejected("422")
    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 1.0}, {})
    assert repo.get("o1") is None and pub.sent == []


def test_link_publish_failure_retries_without_new_charge(h):
    handler, repo, prov, pub = h
    pub.fail = True
    with pytest.raises(TransientError):
        handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 1.0}, {})
    assert len(prov.calls) == 1 and repo.get("o1")["link_publicado"] == 0
    pub.fail = False
    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 1.0}, {})
    assert len(prov.calls) == 1 and len(pub.sent) == 1


@pytest.mark.parametrize("valor", [None, 0, -1, "10", True])
def test_invalid_valor_is_ignored(h, valor):
    handler, repo, prov, pub = h
    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": valor}, {})
    assert prov.calls == [] and pub.sent == []


def test_excluido_before_estoque_ok_prevents_charge(h):
    handler, repo, prov, pub = h
    handler(PEDIDO_EXCLUIDO, {"order_id": "o1"}, {})
    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 5.0}, {})
    assert prov.calls == [] and pub.sent == []


def test_excluido_after_charge_cancels_it(h):
    handler, repo, prov, pub = h
    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 5.0}, {})
    handler(PEDIDO_EXCLUIDO, {"order_id": "o1"}, {})
    assert repo.get("o1")["status"] == "CANCELADO"


def test_event_without_order_id_is_ignored(h):
    handler, repo, prov, pub = h
    handler(PEDIDO_ESTOQUE_OK, {}, {})
    assert prov.calls == []


# ================================================ MS Pagamento: webhook

class Hook:
    def __init__(self, repo):
        self.pub = Pub()
        self.repo = repo
        self.app = payment_app.create_app(repo=repo, publish=self.pub, provider=FakeProvider(),
                                          enable_messaging=False)
        self.client = TestClient(self.app)
        repo.create("o1", 4500.0, "ch_1", "http://mock/checkout/ch_1", "token-secreto")

    def call(self, status="APROVADO", token="token-secreto", charge="ch_1", order="o1"):
        headers = {"X-Webhook-Token": token} if token is not None else {}
        return self.client.post("/webhook", json={"charge_id": charge, "order_id": order, "status": status},
                                headers=headers)


@pytest.fixture()
def hook(repo):
    return Hook(repo)


def test_webhook_without_token_is_401(hook):
    assert hook.call(token=None).status_code == 401 and hook.pub.sent == []


def test_webhook_wrong_token_is_401(hook):
    assert hook.call(token="errado").status_code == 401 and hook.pub.sent == []


def test_webhook_wrong_charge_or_unknown_order_is_401(hook):
    assert hook.call(charge="ch_999").status_code == 401
    assert hook.call(order="nada").status_code == 401
    assert hook.pub.sent == []


def test_webhook_invalid_status_is_422(hook):
    assert hook.call(status="aprovado").status_code == 422
    assert hook.call(status="TALVEZ").status_code == 422


def test_webhook_approved_publishes_event(hook):
    r = hook.call("APROVADO")
    assert r.status_code == 200 and r.json()["processado"] is True
    rk, payload = hook.pub.sent[0]
    assert rk == PAGAMENTO_APROVADO and payload["order_id"] == "o1" and payload["valor"] == 4500.0


def test_webhook_refused_publishes_event(hook):
    assert hook.call("RECUSADO").status_code == 200
    assert hook.pub.sent[0][0] == PAGAMENTO_RECUSADO


def test_webhook_repeated_does_not_duplicate_event(hook):
    hook.call("APROVADO")
    r = hook.call("APROVADO")
    assert r.status_code == 200 and r.json()["processado"] is False
    assert len(hook.pub.sent) == 1


def test_webhook_conflicting_status_is_409(hook):
    hook.call("APROVADO")
    assert hook.call("RECUSADO").status_code == 409
    assert len(hook.pub.sent) == 1


def test_webhook_for_cancelled_order_is_409_and_publishes_nothing(hook):
    hook.repo.cancel("o1")
    r = hook.call("APROVADO")
    assert r.status_code == 409 and "cancelado" in r.json()["detail"].lower()
    assert hook.pub.sent == []


def test_webhook_publish_failure_is_503_then_retry_publishes_once(hook):
    hook.pub.fail = True
    assert hook.call("APROVADO").status_code == 503
    hook.pub.fail = False
    assert hook.call("APROVADO").status_code == 200
    assert hook.call("APROVADO").status_code == 200
    assert len(hook.pub.sent) == 1


def test_consultar_does_not_leak_token(hook):
    r = hook.client.get("/cobrancas/o1")
    assert r.status_code == 200 and "callback_token" not in r.json() and r.json()["charge_id"] == "ch_1"
    assert hook.client.get("/cobrancas/nada").status_code == 404


# ============================== integração em processo: Mock <-> Pagamento

def test_full_flow_mock_and_payment_talking_to_each_other(repo):
    pub = Pub()
    pay_app = payment_app.create_app(repo=repo, publish=pub, provider=FakeProvider(), enable_messaging=False)
    pay_client = TestClient(pay_app)
    mk_client = TestClient(mock_app.create_app(http_client=pay_client, public_url="http://mock.test"))
    pay_app.state.provider = MockPaymentClient("http://mock.test", client=mk_client)
    handler = make_handler(repo, pub, pay_app.state.provider, "http://payment.test/webhook")

    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 250.0}, {})
    rk, link = pub.sent[0]
    assert rk == PAGAMENTO_LINK_CRIADO and link["checkout_url"].startswith("http://mock.test/checkout/ch_")

    charge_id = link["checkout_url"].rsplit("/", 1)[1]
    page = mk_client.get(f"/checkout/{charge_id}")
    assert "R$ 250,00" in page.text
    assert "A loja foi avisada" in mk_client.post(f"/checkout/{charge_id}/aprovar").text
    assert [rk for rk, _ in pub.sent] == [PAGAMENTO_LINK_CRIADO, PAGAMENTO_APROVADO]
    assert pub.sent[1][1]["order_id"] == "o1"


def test_full_flow_cancelled_before_click_is_rejected_by_store(repo):
    pub = Pub()
    pay_app = payment_app.create_app(repo=repo, publish=pub, provider=FakeProvider(), enable_messaging=False)
    mk_client = TestClient(mock_app.create_app(http_client=TestClient(pay_app), public_url="http://mock.test"))
    pay_app.state.provider = MockPaymentClient("http://mock.test", client=mk_client)
    handler = make_handler(repo, pub, pay_app.state.provider, "http://payment.test/webhook")

    handler(PEDIDO_ESTOQUE_OK, {"order_id": "o1", "valor_total": 10.0}, {})
    charge_id = pub.sent[0][1]["checkout_url"].rsplit("/", 1)[1]
    handler(PEDIDO_EXCLUIDO, {"order_id": "o1", "motivo": "cancelado pelo cliente"}, {})
    page = mk_client.post(f"/checkout/{charge_id}/aprovar").text
    assert "não aceitou este pagamento" in page
    assert [rk for rk, _ in pub.sent] == [PAGAMENTO_LINK_CRIADO]  # nada de pagamento.aprovado
"""Testes automatizados do Bloco 6: Inscrição de Promoções no Gateway e MS Promoções com Resend."""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
for sub in ("shared", "order_service", "promotion_service"):
    sys.path.insert(0, str(ROOT / sub))

from events import INTERESSE_PROMOCAO
from gateway_app import create_app
from gateway_store import OrderStore
from promotion_repository import PromotionRepository
from email_provider import ResendEmailClient, EmailServiceError
from promotion_handlers import PromotionEngine, make_handler


# ==================================================================== Gateway: API de Promoções

class GatewayEnv:
    def __init__(self, inventory=None):
        self.sent, self.fail = [], False
        self.store = OrderStore()

        def publish(rk, payload):
            if self.fail:
                raise OSError("broker indisponivel")
            self.sent.append((rk, payload))

        def inv(request):
            if inventory == "down":
                raise httpx.ConnectError("refused")
            if inventory == "500":
                return httpx.Response(500)
            return httpx.Response(200, json=inventory or ["Computadores", "Monitores", "Periféricos"])

        client = httpx.Client(transport=httpx.MockTransport(inv))
        self.client = TestClient(
            create_app(store=self.store, publish=publish, http_client=client, enable_messaging=False)
        )


@pytest.fixture()
def gw():
    return GatewayEnv()


def test_gateway_get_categorias_proxies_inventory(gw):
    r = gw.client.get("/categorias")
    assert r.status_code == 200
    assert r.json() == ["Computadores", "Monitores", "Periféricos"]


def test_gateway_get_categorias_down_is_503():
    env = GatewayEnv("down")
    assert env.client.get("/categorias").status_code == 503


def test_gateway_post_interesse_promocao_publishes_event(gw):
    body = {
        "email": "cliente@teste.com",
        "categorias": ["Computadores", "Periféricos"],
        "client_id": "c1",
    }
    r = gw.client.post("/promocoes/interesse", json=body)
    assert r.status_code == 202
    assert r.json()["status"] == "registrado"
    assert len(gw.sent) == 1
    rk, payload = gw.sent[0]
    assert rk == INTERESSE_PROMOCAO
    assert payload["email"] == "cliente@teste.com"
    assert payload["categorias"] == ["Computadores", "Periféricos"]
    assert payload["client_id"] == "c1"


@pytest.mark.parametrize("body", [
    {"email": "invalido", "categorias": ["Computadores"]},
    {"email": "", "categorias": ["Computadores"]},
    {"email": "sem-arroba.com", "categorias": ["Computadores"]},
    {"email": "teste@exemplo.com", "categorias": []},
    {"categorias": ["Computadores"]},
])
def test_gateway_post_interesse_validation(gw, body):
    r = gw.client.post("/promocoes/interesse", json=body)
    assert r.status_code == 422
    assert gw.sent == []


def test_gateway_post_interesse_broker_down_is_503(gw):
    gw.fail = True
    body = {"email": "cliente@teste.com", "categorias": ["Computadores"]}
    r = gw.client.post("/promocoes/interesse", json=body)
    assert r.status_code == 503


# ==================================================================== MS Promoções: Repositório

@pytest.fixture()
def promo_repo(tmp_path):
    return PromotionRepository(tmp_path / "promo.db")


def test_promo_repo_save_and_retrieve_by_category(promo_repo):
    promo_repo.save_interests("user1@loja.com", ["Computadores", "Periféricos"], client_id="c1")
    promo_repo.save_interests("user2@loja.com", ["Monitores", "Computadores"], client_id="c2")

    comp = promo_repo.list_subscribers_by_category("Computadores")
    assert set(comp) == {"user1@loja.com", "user2@loja.com"}

    perif = promo_repo.list_subscribers_by_category("Periféricos")
    assert perif == ["user1@loja.com"]

    monit = promo_repo.list_subscribers_by_category("monitores")  # case-insensitive
    assert monit == ["user2@loja.com"]

    vazio = promo_repo.list_subscribers_by_category("Games")
    assert vazio == []


def test_promo_repo_idempotence_and_update(promo_repo):
    promo_repo.save_interests("user1@loja.com", ["Computadores"], client_id="c1")
    promo_repo.save_interests("user1@loja.com", ["Computadores", "Monitores"], client_id="c1")

    assert set(promo_repo.get_interests_by_email("user1@loja.com")) == {"Computadores", "Monitores"}
    assert promo_repo.list_subscribers_by_category("Computadores") == ["user1@loja.com"]


def test_promo_repo_persistence(tmp_path):
    db_path = tmp_path / "persistent.db"
    r1 = PromotionRepository(db_path)
    r1.save_interests("p@teste.com", ["Monitores"])

    r2 = PromotionRepository(db_path)
    assert r2.list_subscribers_by_category("Monitores") == ["p@teste.com"]


# ==================================================================== MS Promoções: Resend Email Client

def test_email_client_simulated_mode_when_no_api_key():
    client = ResendEmailClient(api_key="")
    assert not client.is_live
    res = client.send_email(to="cliente@teste.com", subject="Promoção", html_content="<p>10% OFF</p>")
    assert res["simulated"] is True
    assert res["to"] == ["cliente@teste.com"]


def test_email_client_live_mode_calls_resend_api():
    captured = []

    def mock_transport(request: httpx.Request):
        captured.append(request)
        return httpx.Response(200, json={"id": "resend_msg_123"})

    client = ResendEmailClient(
        api_key="re_teste_12345",
        client=httpx.Client(transport=httpx.MockTransport(mock_transport)),
    )
    assert client.is_live

    res = client.send_email(
        to=["cliente1@teste.com", "cliente2@teste.com"],
        subject="Super Oferta",
        html_content="<p>50% OFF</p>",
    )
    assert res["id"] == "resend_msg_123"
    assert len(captured) == 1
    req = captured[0]
    assert req.headers["authorization"] == "Bearer re_teste_12345"
    assert req.url == "https://api.resend.com/emails"


def test_email_client_api_error_raises_email_service_error():
    def mock_error(request: httpx.Request):
        return httpx.Response(422, text='{"error": "domain not verified"}')

    client = ResendEmailClient(
        api_key="re_invalid",
        client=httpx.Client(transport=httpx.MockTransport(mock_error)),
    )
    with pytest.raises(EmailServiceError) as exc:
        client.send_email(to="cliente@teste.com", subject="Oferta", html_content="<p>OFF</p>")
    assert "422" in str(exc.value)


# ==================================================================== MS Promoções: Engine e Handlers

def test_promotion_engine_generates_and_notifies_only_interested():
    emails_sent = []

    def fake_send(request: httpx.Request):
        import json
        body = json.loads(request.content.decode("utf-8"))
        emails_sent.append(body)
        return httpx.Response(200, json={"id": f"mail_{len(emails_sent)}"})

    client = ResendEmailClient(
        api_key="re_fake",
        client=httpx.Client(transport=httpx.MockTransport(fake_send)),
    )
    repo = PromotionRepository(":memory:")
    repo.save_interests("fan_computadores@teste.com", ["Computadores"])
    repo.save_interests("fan_monitores@teste.com", ["Monitores"])

    engine = PromotionEngine(repo=repo, email_client=client)

    # Gera promoção especificamente para Computadores
    promo, disparos = engine.generate_and_notify(categoria="Computadores")

    assert promo["categoria"] == "Computadores"
    assert promo["desconto"] >= 10
    assert len(disparos) == 1
    assert disparos[0]["email"] == "fan_computadores@teste.com"
    assert disparos[0]["sucesso"] is True

    assert len(emails_sent) == 1
    assert emails_sent[0]["to"] == ["fan_computadores@teste.com"]
    assert "Computadores" in emails_sent[0]["subject"]


def test_make_handler_consumes_interesse_promocao_event():
    repo = PromotionRepository(":memory:")
    handler = make_handler(repo)

    payload = {
        "email": "interessado@teste.com",
        "categorias": ["Monitores", "Periféricos"],
        "client_id": "c100",
    }
    envelope = {"event_type": INTERESSE_PROMOCAO, "payload": payload}

    handler(INTERESSE_PROMOCAO, payload, envelope)

    subscribers_monitores = repo.list_subscribers_by_category("Monitores")
    assert subscribers_monitores == ["interessado@teste.com"]

    subscribers_perifericos = repo.list_subscribers_by_category("Periféricos")
    assert subscribers_perifericos == ["interessado@teste.com"]

    subscribers_computadores = repo.list_subscribers_by_category("Computadores")
    assert subscribers_computadores == []


def test_make_handler_ignores_unknown_or_malformed_event():
    repo = PromotionRepository(":memory:")
    handler = make_handler(repo)

    # Evento desconhecido
    handler("outro.evento", {"email": "a@b.com", "categorias": ["Monitores"]}, {})
    assert repo.list_all_interests() == []

    # E-mail inválido
    handler(INTERESSE_PROMOCAO, {"email": "invalido", "categorias": ["Monitores"]}, {})
    assert repo.list_all_interests() == []

    # Categorias inválidas
    handler(INTERESSE_PROMOCAO, {"email": "ok@teste.com", "categorias": []}, {})
    assert repo.list_all_interests() == []

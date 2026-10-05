import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from utils import ensure_keys, sign_payload, verify_signature, load_public_key
from events import *
import messaging as m


@pytest.fixture()
def env(tmp_path):
    keys = {n: ensure_keys(n, tmp_path) for n in ("order", "inventory", "payment")}
    return tmp_path, keys


def check(envelope, rk, base, requester="inventory"):
    return m.verify_envelope(envelope, rk, requester, base)


def test_sign_and_verify(env):
    base, keys = env
    pub = load_public_key("order", "inventory", base)
    sig = sign_payload(keys["order"], {"a": 1})
    assert verify_signature(pub, {"a": 1}, sig)


def test_tampered_payload_fails(env):
    base, keys = env
    pub = load_public_key("order", "inventory", base)
    sig = sign_payload(keys["order"], {"a": 1})
    assert not verify_signature(pub, {"a": 2}, sig)


def test_valid_envelope(env):
    base, keys = env
    e = m.build_envelope("order", keys["order"], PEDIDO_CRIADO, {"order_id": "1"})
    assert check(e, PEDIDO_CRIADO, base) == (True, "ok")


def test_tampered_envelope_payload(env):
    base, keys = env
    e = m.build_envelope("order", keys["order"], PEDIDO_CRIADO, {"qtd": 1})
    e["payload"]["qtd"] = 999
    assert check(e, PEDIDO_CRIADO, base) == (False, "assinatura inválida")


def test_event_type_swap_is_rejected(env):
    base, keys = env
    e = m.build_envelope("order", keys["order"], PEDIDO_CRIADO, {})
    ok, reason = check(e, PEDIDO_EXCLUIDO, base)
    assert not ok and "diverge" in reason


def test_producer_not_authorized(env):
    base, keys = env
    e = m.build_envelope("order", keys["order"], PAGAMENTO_APROVADO, {"order_id": "1"})
    ok, reason = check(e, PAGAMENTO_APROVADO, base, requester="inventory")
    assert not ok and "não pode publicar" in reason


def test_signed_with_wrong_key(env):
    base, keys = env
    e = m.build_envelope("payment", keys["order"], PAGAMENTO_APROVADO, {})
    assert check(e, PAGAMENTO_APROVADO, base) == (False, "assinatura inválida")


def test_missing_public_key(env):
    base, keys = env
    e = m.build_envelope("delivery", keys["order"], PEDIDO_ENVIADO, {})
    ok, reason = check(e, PEDIDO_ENVIADO, base)
    assert not ok and "ausente" in reason


def test_malformed_envelope(env):
    base, _ = env
    assert check({"foo": "bar"}, PEDIDO_CRIADO, base) == (False, "envelope malformado")
    assert check("lixo", PEDIDO_CRIADO, base) == (False, "envelope malformado")


def test_publisher_refuses_foreign_event(env):
    _, keys = env
    with pytest.raises(ValueError):
        m.Publisher("order", keys["order"]).publish(PAGAMENTO_APROVADO, {})


def test_existing_trab1_keys_work_with_new_envelope():
    """Integração: as chaves já existentes do projeto assinam e validam o novo envelope."""
    base = Path(__file__).resolve().parent.parent
    order_key = ensure_keys("order", base)
    e = m.build_envelope("order", order_key, PEDIDO_CRIADO, {"order_id": "t1"})
    assert m.verify_envelope(e, PEDIDO_CRIADO, "inventory", base) == (True, "ok")
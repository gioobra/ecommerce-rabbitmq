import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for sub in ("shared", "delivery_service"):
    sys.path.insert(0, str(ROOT / sub))

from events import *
from messaging import TransientError
from delivery_repository import DeliveryRepository
from delivery_handlers import make_handler


@pytest.fixture()
def repo(tmp_path):
    return DeliveryRepository(tmp_path / "d.db")


class Pub:
    def __init__(self):
        self.sent, self.fail = [], False

    def __call__(self, rk, payload):
        if self.fail:
            raise OSError("broker fora do ar")
        self.sent.append((rk, payload))


# ------------------------------------------------------------ repositório

def test_invoice_numbers_are_sequential_and_formatted(repo):
    a, b = repo.emit_invoice("o1", 10.0), repo.emit_invoice("o2", 20.0)
    assert (a["nota_fiscal"], b["nota_fiscal"]) == ("NF-000001", "NF-000002")
    assert re.fullmatch(r"BR\d{9}", a["codigo_rastreio"])


def test_emit_invoice_is_idempotent(repo):
    first = repo.emit_invoice("o1", 10.0)
    again = repo.emit_invoice("o1", 99.0)
    assert first["nota_fiscal"] == again["nota_fiscal"] and again["valor"] == 10.0
    assert repo.emit_invoice("o2")["nota_fiscal"] == "NF-000002"


def test_persistence_keeps_counter(tmp_path):
    DeliveryRepository(tmp_path / "x.db").emit_invoice("o1")
    r2 = DeliveryRepository(tmp_path / "x.db")
    assert r2.get("o1")["nota_fiscal"] == "NF-000001"
    assert r2.emit_invoice("o2")["nota_fiscal"] == "NF-000002"


def test_get_unknown_and_mark_published(repo):
    assert repo.get("nada") is None
    repo.emit_invoice("o1")
    assert repo.get("o1")["enviado_publicado"] == 0
    repo.mark_published("o1")
    assert repo.get("o1")["enviado_publicado"] == 1


def test_concurrent_orders_get_unique_numbers(repo):
    results = []
    lock = threading.Lock()

    def work(i):
        nf = repo.emit_invoice(f"o{i}")["nota_fiscal"]
        with lock:
            results.append(nf)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(set(results)) == 20


def test_concurrent_same_order_creates_single_invoice(repo):
    results = []
    lock = threading.Lock()

    def work():
        nf = repo.emit_invoice("mesmo")["nota_fiscal"]
        with lock:
            results.append(nf)

    threads = [threading.Thread(target=work) for _ in range(10)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert set(results) == {"NF-000001"} and repo.emit_invoice("outro")["nota_fiscal"] == "NF-000002"


# --------------------------------------------------------------- handler

def make(repo, delay=0.0):
    pub, slept = Pub(), []
    return make_handler(repo, pub, delay_seconds=delay, sleep=slept.append), pub, slept


def test_approved_payment_ships_order(repo):
    h, pub, _ = make(repo)
    h(PAGAMENTO_APROVADO, {"order_id": "o1", "charge_id": "ch_1", "valor": 250.0}, {})
    rk, payload = pub.sent[0]
    assert rk == PEDIDO_ENVIADO
    assert payload["order_id"] == "o1" and payload["nota_fiscal"] == "NF-000001"
    assert payload["codigo_rastreio"].startswith("BR") and payload["despachado_em"] > 0
    row = repo.get("o1")
    assert row["enviado_publicado"] == 1 and row["valor"] == 250.0 and row["charge_id"] == "ch_1"


def test_duplicate_event_ships_only_once(repo):
    h, pub, slept = make(repo, delay=2.0)
    for _ in range(3):
        h(PAGAMENTO_APROVADO, {"order_id": "o1"}, {})
    assert len(pub.sent) == 1 and slept == [2.0]
    assert repo.emit_invoice("o2")["nota_fiscal"] == "NF-000002"  # nenhuma nota extra


def test_dispatch_delay_is_applied_before_publishing(repo):
    pub, order = Pub(), []
    h = make_handler(repo, lambda rk, p: order.append("publicou"), delay_seconds=1.5,
                     sleep=lambda s: order.append(f"sleep {s}"))
    h(PAGAMENTO_APROVADO, {"order_id": "o1"}, {})
    assert order == ["sleep 1.5", "publicou"]


def test_no_sleep_when_delay_is_zero(repo):
    h, pub, slept = make(repo, delay=0)
    h(PAGAMENTO_APROVADO, {"order_id": "o1"}, {})
    assert slept == [] and len(pub.sent) == 1


def test_publish_failure_is_transient_and_retry_keeps_same_invoice(repo):
    h, pub, _ = make(repo)
    pub.fail = True
    with pytest.raises(TransientError):
        h(PAGAMENTO_APROVADO, {"order_id": "o1"}, {})
    assert repo.get("o1")["enviado_publicado"] == 0 and repo.get("o1")["nota_fiscal"] == "NF-000001"
    pub.fail = False
    h(PAGAMENTO_APROVADO, {"order_id": "o1"}, {})
    assert len(pub.sent) == 1 and pub.sent[0][1]["nota_fiscal"] == "NF-000001"
    assert repo.emit_invoice("o2")["nota_fiscal"] == "NF-000002"


def test_event_without_order_id_is_ignored(repo):
    h, pub, _ = make(repo)
    h(PAGAMENTO_APROVADO, {}, {})
    assert pub.sent == []


def test_other_event_types_are_ignored(repo):
    h, pub, _ = make(repo)
    h(PAGAMENTO_RECUSADO, {"order_id": "o1"}, {})
    assert pub.sent == [] and repo.get("o1") is None


@pytest.mark.parametrize("valor", [None, "abc", True, -1])
def test_weird_valor_does_not_break_shipping(repo, valor):
    h, pub, _ = make(repo)
    h(PAGAMENTO_APROVADO, {"order_id": "o1", "valor": valor}, {})
    assert len(pub.sent) == 1


def test_main_module_imports_without_side_effects():
    import delivery_main
    assert callable(delivery_main.main)
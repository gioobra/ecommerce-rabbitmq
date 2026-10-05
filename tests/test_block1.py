import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "inventory_service"))

from events import *
from messaging import TransientError
from inventory_repository import InventoryRepository
from inventory_handlers import make_handler


@pytest.fixture()
def repo(tmp_path):
    return InventoryRepository(tmp_path / "t.db")


def pid(repo, nome):
    return next(p["id"] for p in repo.list_available() if p["nome"] == nome)


def stock(repo, produto_id):
    return repo.get_product(produto_id)["quantidade"]


def test_seed_and_listing(repo):
    nomes = {p["nome"] for p in repo.list_available()}
    assert {"notebook", "mouse", "teclado", "monitor"} <= nomes
    assert "Periféricos" in repo.list_categories()


def test_listing_hides_out_of_stock(repo):
    m = pid(repo, "monitor")
    repo.reserve("o1", [{"produto_id": m, "quantidade": 2}])
    assert "monitor" not in {p["nome"] for p in repo.list_available()}


def test_reserve_success(repo):
    n = pid(repo, "notebook")
    status, payload = repo.reserve("o1", [{"produto_id": n, "quantidade": 2}])
    assert status == "RESERVADO"
    assert payload["valor_total"] == 9000.0
    assert stock(repo, n) == 3


def test_reserve_is_all_or_nothing(repo):
    mouse, mon = pid(repo, "mouse"), pid(repo, "monitor")
    status, payload = repo.reserve("o1", [
        {"produto_id": mouse, "quantidade": 3},
        {"produto_id": mon, "quantidade": 99},
    ])
    assert status == "INDISPONIVEL"
    assert stock(repo, mouse) == 10
    assert payload["itens_faltantes"][0]["disponivel"] == 2


def test_nonexistent_product(repo):
    status, payload = repo.reserve("o1", [{"produto_id": 9999, "quantidade": 1}])
    assert status == "INDISPONIVEL"
    assert payload["itens_faltantes"][0]["motivo"] == "produto inexistente"


def test_duplicate_items_are_summed(repo):
    m = pid(repo, "monitor")
    status, _ = repo.reserve("o1", [{"produto_id": m, "quantidade": 2}, {"produto_id": m, "quantidade": 1}])
    assert status == "INDISPONIVEL"
    assert stock(repo, m) == 2


@pytest.mark.parametrize("itens", [[], None, "x", [{"produto_id": 1, "quantidade": 0}],
                                   [{"produto_id": 1, "quantidade": -1}], [{"produto_id": 1}],
                                   [{"produto_id": 1, "quantidade": "abc"}]])
def test_invalid_items(repo, itens):
    before = {p["id"]: p["quantidade"] for p in repo.list_available()}
    status, _ = repo.reserve("o1", itens)
    assert status == "INDISPONIVEL"
    assert before == {p["id"]: p["quantidade"] for p in repo.list_available()}


def test_reserve_is_idempotent(repo):
    n = pid(repo, "notebook")
    first = repo.reserve("o1", [{"produto_id": n, "quantidade": 1}])
    second = repo.reserve("o1", [{"produto_id": n, "quantidade": 1}])
    assert first == second
    assert stock(repo, n) == 4


def test_restore_returns_stock_once(repo):
    n = pid(repo, "notebook")
    repo.reserve("o1", [{"produto_id": n, "quantidade": 2}])
    assert repo.restore("o1") is True
    assert stock(repo, n) == 5
    assert repo.restore("o1") is False
    assert stock(repo, n) == 5


def test_restore_noop_cases(repo):
    assert repo.restore("desconhecido") is False
    repo.reserve("o2", [{"produto_id": 9999, "quantidade": 1}])
    assert repo.restore("o2") is False


def test_no_reserve_after_restore(repo):
    n = pid(repo, "notebook")
    repo.reserve("o1", [{"produto_id": n, "quantidade": 1}])
    repo.restore("o1")
    repo.reserve("o1", [{"produto_id": n, "quantidade": 1}])
    assert stock(repo, n) == 5


def test_persistence_across_instances(tmp_path):
    db = tmp_path / "p.db"
    r1 = InventoryRepository(db)
    n = pid(r1, "notebook")
    r1.reserve("o1", [{"produto_id": n, "quantidade": 2}])
    r2 = InventoryRepository(db)
    assert stock(r2, n) == 3
    assert r2.reserve("o1", [{"produto_id": n, "quantidade": 2}])[0] == "RESERVADO"
    assert stock(r2, n) == 3


def test_handler_publishes_estoque_ok(repo):
    sent = []
    h = make_handler(repo, lambda rk, p: sent.append((rk, p)))
    n = pid(repo, "notebook")
    h(PEDIDO_CRIADO, {"order_id": "o1", "itens": [{"produto_id": n, "quantidade": 1}]}, {})
    assert sent[0][0] == PEDIDO_ESTOQUE_OK and sent[0][1]["valor_total"] == 4500.0


def test_handler_publishes_indisponivel(repo):
    sent = []
    h = make_handler(repo, lambda rk, p: sent.append((rk, p)))
    h(PEDIDO_CRIADO, {"order_id": "o1", "itens": [{"produto_id": 9999, "quantidade": 1}]}, {})
    assert sent[0][0] == ESTOQUE_INDISPONIVEL


def test_handler_excluido_restores(repo):
    h = make_handler(repo, lambda rk, p: None)
    n = pid(repo, "notebook")
    h(PEDIDO_CRIADO, {"order_id": "o1", "itens": [{"produto_id": n, "quantidade": 2}]}, {})
    h(PEDIDO_EXCLUIDO, {"order_id": "o1"}, {})
    assert stock(repo, n) == 5


def test_publish_failure_is_transient_and_retry_republishes(repo):
    n = pid(repo, "notebook")
    msg = {"order_id": "o1", "itens": [{"produto_id": n, "quantidade": 1}]}

    def broken(rk, p):
        raise OSError("broker fora do ar")

    with pytest.raises(TransientError):
        make_handler(repo, broken)(PEDIDO_CRIADO, msg, {})
    assert stock(repo, n) == 4

    sent = []
    make_handler(repo, lambda rk, p: sent.append(rk))(PEDIDO_CRIADO, msg, {})
    assert sent == [PEDIDO_ESTOQUE_OK]
    assert stock(repo, n) == 4


def test_handler_ignores_event_without_order_id(repo):
    sent = []
    make_handler(repo, lambda rk, p: sent.append(rk))(PEDIDO_CRIADO, {}, {})
    assert sent == []


def test_rest_endpoints(repo):
    from fastapi.testclient import TestClient
    from inventory_app import create_app
    client = TestClient(create_app(repo=repo, enable_messaging=False))
    r = client.get("/produtos")
    assert r.status_code == 200
    assert {"id", "nome", "categoria", "preco", "quantidade"} <= set(r.json()[0])
    assert client.get("/categorias").json() == ["Computadores", "Monitores", "Periféricos"]
    assert client.get("/health").json() == {"status": "ok"}
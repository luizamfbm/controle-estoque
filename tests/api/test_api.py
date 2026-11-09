"""Testes da API HTTP: contratos, status codes e formato de erro."""

from __future__ import annotations

import pytest


@pytest.fixture
def sku(client):
    assert client.post("/products", json={"sku": "SKU-API", "name": "Produto API"}).status_code == 201
    assert client.post("/products/SKU-API/entries", json={"quantity": 10}).status_code == 200
    return "SKU-API"


def get_stock(client, sku):
    body = client.get(f"/products/{sku}").json()
    return {k: body[k] for k in ("total", "available", "reserved", "blocked")}


def test_cenario_completo(client, sku):
    r = client.post(f"/products/{sku}/reservations", json={"quantity": 3, "reference": "PEDIDO-1"})
    assert r.status_code == 201
    reserva = r.json()
    assert (reserva["type"], reserva["status"], reserva["quantity"]) == ("RESERVATION", "ACTIVE", 3)

    b = client.post(f"/products/{sku}/blocks", json={"quantity": 2, "reference": "AVARIA"})
    assert b.status_code == 201
    bloqueio = b.json()

    assert client.post(f"/products/{sku}/exits", json={"quantity": 5}).json()["available"] == 0
    assert get_stock(client, sku) == {"total": 5, "available": 0, "reserved": 3, "blocked": 2}

    assert client.get(f"/reservations/{reserva['id']}").json()["reference"] == "PEDIDO-1"
    assert client.post(f"/reservations/{reserva['id']}/release").json()["status"] == "RELEASED"
    assert client.post(f"/blocks/{bloqueio['id']}/release").json()["status"] == "RELEASED"
    assert get_stock(client, sku) == {"total": 5, "available": 5, "reserved": 0, "blocked": 0}

    movimentos = client.get(f"/products/{sku}/movements").json()
    assert [m["type"] for m in movimentos] == [
        "RELEASE_BLOCK", "RELEASE_RESERVATION", "EXIT", "BLOCK", "RESERVE", "ENTRY",
    ]


@pytest.mark.parametrize("path", ["exits", "reservations", "blocks"])
def test_quantidade_acima_do_disponivel_retorna_409(client, sku, path):
    r = client.post(f"/products/{sku}/{path}", json={"quantity": 11})
    assert r.status_code == 409
    assert r.json() == {
        "error": {
            "code": "INSUFFICIENT_AVAILABLE_STOCK",
            "message": "Quantidade disponível insuficiente para o produto 'SKU-API': solicitado 11, disponível 10.",
            "details": {"sku": "SKU-API", "requested": 11, "available": 10},
        }
    }
    assert get_stock(client, sku) == {"total": 10, "available": 10, "reserved": 0, "blocked": 0}


@pytest.mark.parametrize(
    "body",
    [
        {"quantity": 0},
        {"quantity": -5},
        {"quantity": 2.5},
        {"quantity": "3"},
        {"quantity": True},
        {"quantity": 1_000_000_001},
        {},
        {"quantidade": 3},  # campo com nome errado
        {"quantity": 3, "extra": "x"},
        {"quantity": 3, "reference": ""},
    ],
)
@pytest.mark.parametrize("path", ["entries", "exits", "reservations", "blocks"])
def test_entrada_invalida_retorna_422_padronizado(client, sku, path, body):
    r = client.post(f"/products/{sku}/{path}", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert get_stock(client, sku)["total"] == 10


def test_produto_inexistente_retorna_404(client):
    r = client.get("/products/NAO-EXISTE")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert client.post("/products/NAO-EXISTE/entries", json={"quantity": 1}).status_code == 404


def test_sku_invalido_retorna_422(client):
    assert client.post("/products", json={"sku": "com espaço", "name": "X"}).status_code == 422
    assert client.get("/products/sku%20invalido").status_code == 422


def test_produto_duplicado_retorna_409(client, sku):
    r = client.post("/products", json={"sku": sku, "name": "De novo"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PRODUCT_ALREADY_EXISTS"


def test_reserva_inexistente_retorna_404(client):
    for path in ("/reservations/nao-existe", "/blocks/nao-existe"):
        assert client.get(path).status_code == 404
        assert client.post(f"{path}/release").json()["error"]["code"] == "HOLD_NOT_FOUND"


def test_id_de_bloqueio_na_rota_de_reserva_retorna_404(client, sku):
    bloqueio = client.post(f"/products/{sku}/blocks", json={"quantity": 1}).json()
    assert client.post(f"/reservations/{bloqueio['id']}/release").status_code == 404
    assert get_stock(client, sku)["blocked"] == 1


@pytest.mark.parametrize("collection", ["reservations", "blocks"])
def test_liberar_duas_vezes_e_idempotente(client, sku, collection):
    hold = client.post(f"/products/{sku}/{collection}", json={"quantity": 1}).json()
    primeira = client.post(f"/{collection}/{hold['id']}/release")
    segunda = client.post(f"/{collection}/{hold['id']}/release")

    assert primeira.status_code == segunda.status_code == 200
    assert segunda.json() == primeira.json()
    assert get_stock(client, sku) == {"total": 10, "available": 10, "reserved": 0, "blocked": 0}


def test_listar_reservas_com_filtro_de_status(client, sku):
    r1 = client.post(f"/products/{sku}/reservations", json={"quantity": 1}).json()
    r2 = client.post(f"/products/{sku}/reservations", json={"quantity": 2}).json()
    client.post(f"/reservations/{r1['id']}/release")

    ativas = client.get(f"/products/{sku}/reservations", params={"status": "ACTIVE"}).json()
    todas = client.get(f"/products/{sku}/reservations").json()
    assert [r["id"] for r in ativas] == [r2["id"]]
    assert len(todas) == 2
    assert client.get(f"/products/{sku}/reservations", params={"status": "XPTO"}).status_code == 422


def test_erro_inesperado_nao_vaza_detalhes(client, sku, monkeypatch):
    from inventory.service import InventoryService

    def explode(*_a, **_k):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(InventoryService, "get_product", explode)
    client_sem_raise = type(client)(client.app, raise_server_exceptions=False)
    r = client_sem_raise.get(f"/products/{sku}")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "segredo" not in r.text


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}

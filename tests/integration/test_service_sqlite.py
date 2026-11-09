"""Testes de integração: serviço + SQLite real (arquivo temporário)."""

from __future__ import annotations

import sqlite3

import pytest

from inventory.domain import HoldKind, HoldStatus, MovementType
from inventory.errors import (
    HoldNotFoundError,
    InsufficientAvailableStockError,
    ProductAlreadyExistsError,
    ProductNotFoundError,
)
from inventory.service import InventoryService
from inventory.sqlite_store import SqliteDatabase, SqliteMovementRepository


def stock(service: InventoryService, sku: str = "SKU-1") -> tuple[int, int, int, int]:
    p = service.get_product(sku)
    return (p.total, p.available, p.reserved, p.blocked)


@pytest.fixture
def product(service):
    service.register_product("SKU-1", "Produto 1")
    service.receive("SKU-1", 10, "NF-1")
    return "SKU-1"


def test_fluxo_completo_persistido(service, product):
    r = service.reserve(product, 3, "PEDIDO-1")
    b = service.block(product, 2, "AVARIA")
    service.dispatch(product, 4, "PEDIDO-0")
    assert stock(service) == (6, 1, 3, 2)

    service.release(r.id, HoldKind.RESERVATION)
    service.release(b.id, HoldKind.BLOCK)
    assert stock(service) == (6, 6, 0, 0)


def test_estado_sobrevive_a_nova_instancia_do_servico(db_path, service, product):
    hold = service.reserve(product, 3)
    novo_service = InventoryService(SqliteDatabase(db_path))
    assert stock(novo_service) == (10, 7, 3, 0)
    assert novo_service.get_hold(hold.id, HoldKind.RESERVATION).status is HoldStatus.ACTIVE


def test_produto_duplicado_e_recusado(service, product):
    with pytest.raises(ProductAlreadyExistsError):
        service.register_product(product, "Outro nome")


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.get_product("NAO-EXISTE"),
        lambda s: s.receive("NAO-EXISTE", 1),
        lambda s: s.dispatch("NAO-EXISTE", 1),
        lambda s: s.reserve("NAO-EXISTE", 1),
        lambda s: s.block("NAO-EXISTE", 1),
        lambda s: s.list_holds("NAO-EXISTE", HoldKind.RESERVATION),
        lambda s: s.list_movements("NAO-EXISTE"),
    ],
)
def test_operacoes_em_produto_inexistente(service, call):
    with pytest.raises(ProductNotFoundError):
        call(service)


def test_consultar_e_liberar_retencao(service, product):
    r = service.reserve(product, 2, "PEDIDO-9")
    consultada = service.get_hold(r.id, HoldKind.RESERVATION)
    assert (consultada.quantity, consultada.reference, consultada.status) == (2, "PEDIDO-9", HoldStatus.ACTIVE)

    liberada = service.release(r.id, HoldKind.RESERVATION)
    assert liberada.status is HoldStatus.RELEASED
    assert service.get_hold(r.id, HoldKind.RESERVATION).released_at is not None


@pytest.mark.parametrize("kind", list(HoldKind))
def test_liberar_duas_vezes_e_idempotente(service, product, kind):
    hold = service.reserve(product, 2) if kind is HoldKind.RESERVATION else service.block(product, 2)
    primeira = service.release(hold.id, kind)
    segunda = service.release(hold.id, kind)

    assert segunda == primeira  # mesma retenção, mesmo released_at
    assert stock(service) == (10, 10, 0, 0)  # devolveu ao disponível só uma vez
    tipos = [m.type for m in service.list_movements(product)]
    assert tipos.count(MovementType.RELEASE_RESERVATION) + tipos.count(MovementType.RELEASE_BLOCK) == 1


def test_id_de_bloqueio_nao_e_aceito_como_reserva(service, product):
    b = service.block(product, 2)
    with pytest.raises(HoldNotFoundError):
        service.release(b.id, HoldKind.RESERVATION)
    with pytest.raises(HoldNotFoundError):
        service.get_hold(b.id, HoldKind.RESERVATION)
    assert stock(service) == (10, 8, 0, 2)


def test_listar_retencoes_por_status(service, product):
    r1 = service.reserve(product, 1, "P1")
    r2 = service.reserve(product, 2, "P2")
    service.block(product, 3)
    service.release(r1.id, HoldKind.RESERVATION)

    ativas = service.list_holds(product, HoldKind.RESERVATION, HoldStatus.ACTIVE)
    todas = service.list_holds(product, HoldKind.RESERVATION)
    assert [h.id for h in ativas] == [r2.id]
    assert {h.id for h in todas} == {r1.id, r2.id}


def test_movimentacoes_registram_cada_operacao_com_saldo_apos(service, product):
    r = service.reserve(product, 3, "PEDIDO-1")
    service.release(r.id, HoldKind.RESERVATION)
    with pytest.raises(InsufficientAvailableStockError):
        service.dispatch(product, 999)  # operação recusada não gera movimentação

    movs = service.list_movements(product)  # mais recentes primeiro
    assert [m.type for m in movs] == [MovementType.RELEASE_RESERVATION, MovementType.RESERVE, MovementType.ENTRY]
    assert (movs[1].hold_id, movs[1].reserved_after) == (r.id, 3)
    assert movs[0].reserved_after == 0


def test_falha_no_meio_da_operacao_desfaz_tudo(service, product, monkeypatch):
    """Atomicidade: se a gravação da movimentação falhar depois de o produto
    e a reserva já terem sido gravados, nada pode ficar persistido."""

    def falha(*_args, **_kwargs):
        raise RuntimeError("disco cheio (simulado)")

    monkeypatch.setattr(SqliteMovementRepository, "add", falha)
    with pytest.raises(RuntimeError):
        service.reserve(product, 4)
    monkeypatch.undo()

    assert stock(service) == (10, 10, 0, 0)
    assert service.list_holds(product, HoldKind.RESERVATION) == []


def test_banco_recusa_estado_inconsistente_mesmo_sem_passar_pelo_dominio(db_path, product):
    """Defesa em profundidade: as CHECK constraints do schema barram um
    estado inválido mesmo que um bug (ou um script manual) tente gravá-lo."""
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE products SET reserved = 11 WHERE sku = ?", (product,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE products SET total = -1, reserved = 0, blocked = 0 WHERE sku = ?", (product,))
    conn.close()


def test_memory_database_nao_e_suportado():
    with pytest.raises(ValueError):
        SqliteDatabase(":memory:")

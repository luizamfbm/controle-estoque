"""Testes unitários das regras de negócio (domínio puro, sem banco)."""

from __future__ import annotations

import dataclasses

import pytest

from inventory.domain import MAX_QUANTITY_PER_OPERATION, HoldKind, HoldStatus, Product
from inventory.errors import (
    HoldAlreadyReleasedError,
    InsufficientAvailableStockError,
    InvalidQuantityError,
)


def make_product(total: int = 0) -> Product:
    p = Product(sku="SKU-1", name="Produto")
    if total:
        p.receive(total)
    return p


def state(p: Product) -> tuple[int, int, int, int]:
    return (p.total, p.available, p.reserved, p.blocked)


class TestEntrada:
    def test_entrada_soma_ao_total_e_ao_disponivel(self):
        p = make_product()
        p.receive(10)
        p.receive(5)
        assert state(p) == (15, 15, 0, 0)


class TestSaida:
    def test_saida_retira_do_total_e_do_disponivel(self):
        p = make_product(10)
        p.dispatch(4)
        assert state(p) == (6, 6, 0, 0)

    def test_saida_de_todo_o_disponivel_e_permitida(self):
        p = make_product(10)
        p.dispatch(10)
        assert state(p) == (0, 0, 0, 0)

    def test_saida_maior_que_disponivel_e_recusada_sem_alterar_estado(self):
        p = make_product(10)
        with pytest.raises(InsufficientAvailableStockError) as exc:
            p.dispatch(11)
        assert exc.value.details == {"sku": "SKU-1", "requested": 11, "available": 10}
        assert state(p) == (10, 10, 0, 0)

    def test_saida_nao_pode_consumir_unidades_reservadas_ou_bloqueadas(self):
        p = make_product(10)
        p.reserve(4)
        p.block(3)
        # total = 10, mas só 3 estão disponíveis
        with pytest.raises(InsufficientAvailableStockError):
            p.dispatch(4)
        p.dispatch(3)
        assert state(p) == (7, 0, 4, 3)


class TestReservaEBloqueio:
    @pytest.mark.parametrize("operation, kind", [("reserve", HoldKind.RESERVATION), ("block", HoldKind.BLOCK)])
    def test_cria_retencao_ativa_com_id(self, operation, kind):
        p = make_product(10)
        hold = getattr(p, operation)(3, "PEDIDO-1")
        assert hold.id
        assert hold.kind is kind
        assert hold.status is HoldStatus.ACTIVE
        assert (hold.sku, hold.quantity, hold.reference) == ("SKU-1", 3, "PEDIDO-1")

    def test_reserva_move_do_disponivel_para_reservado(self):
        p = make_product(10)
        p.reserve(3)
        assert state(p) == (10, 7, 3, 0)

    def test_bloqueio_move_do_disponivel_para_bloqueado(self):
        p = make_product(10)
        p.block(2)
        assert state(p) == (10, 8, 0, 2)

    @pytest.mark.parametrize("operation", ["reserve", "block"])
    def test_maior_que_disponivel_e_recusado_sem_alterar_estado(self, operation):
        p = make_product(5)
        p.reserve(2)
        before = state(p)
        with pytest.raises(InsufficientAvailableStockError):
            getattr(p, operation)(4)
        assert state(p) == before

    def test_varias_reservas_e_bloqueios_simultaneos_de_solicitacoes_diferentes(self):
        p = make_product(20)
        r1 = p.reserve(3, "PEDIDO-1")
        r2 = p.reserve(4, "PEDIDO-2")
        r3 = p.reserve(1, "PEDIDO-1")  # mesmo pedido pode ter mais de uma reserva
        b1 = p.block(2, "AVARIA")
        b2 = p.block(5, "INVENTARIO")
        assert len({r1.id, r2.id, r3.id, b1.id, b2.id}) == 5
        assert state(p) == (20, 5, 8, 7)

    def test_bloquear_produto_sem_estoque_e_recusado(self):
        with pytest.raises(InsufficientAvailableStockError):
            make_product().block(1)


class TestLiberacao:
    def test_liberar_reserva_devolve_ao_disponivel(self):
        p = make_product(10)
        r = p.reserve(4)
        p.release(r)
        assert state(p) == (10, 10, 0, 0)
        assert r.status is HoldStatus.RELEASED
        assert r.released_at is not None

    def test_liberar_bloqueio_devolve_ao_disponivel(self):
        p = make_product(10)
        b = p.block(4)
        p.release(b)
        assert state(p) == (10, 10, 0, 0)

    def test_liberar_uma_retencao_nao_afeta_as_outras(self):
        p = make_product(10)
        r1 = p.reserve(2)
        p.reserve(3)
        p.block(1)
        p.release(r1)
        assert state(p) == (10, 6, 3, 1)

    def test_liberar_duas_vezes_e_recusado_sem_alterar_estado(self):
        p = make_product(10)
        r = p.reserve(4)
        p.release(r)
        with pytest.raises(HoldAlreadyReleasedError):
            p.release(r)
        assert state(p) == (10, 10, 0, 0)

    def test_liberar_retencao_de_outro_produto_e_erro_de_programacao(self):
        p1 = make_product(10)
        p2 = dataclasses.replace(make_product(10), sku="OUTRO")
        r = p1.reserve(1)
        with pytest.raises(ValueError):
            p2.release(r)


class TestQuantidadeInvalida:
    @pytest.mark.parametrize("quantity", [0, -1, 1.5, "3", None, True, MAX_QUANTITY_PER_OPERATION + 1])
    @pytest.mark.parametrize("operation", ["receive", "dispatch", "reserve", "block"])
    def test_quantidade_invalida_e_recusada_sem_alterar_estado(self, operation, quantity):
        p = make_product(10)
        with pytest.raises(InvalidQuantityError):
            getattr(p, operation)(quantity)
        assert state(p) == (10, 10, 0, 0)

    def test_limite_maximo_por_operacao_e_aceito(self):
        p = make_product()
        p.receive(MAX_QUANTITY_PER_OPERATION)
        assert p.total == MAX_QUANTITY_PER_OPERATION

"""Teste baseado em propriedades (Hypothesis).

Em vez de escolher exemplos à mão, o Hypothesis gera milhares de sequências
aleatórias de operações (válidas e inválidas) e verifica, depois de cada
passo, que as regras de consistência continuam valendo. Se encontrar uma
sequência que quebra a regra, ele a reduz ao menor exemplo possível.
"""

from __future__ import annotations

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from inventory.domain import Hold, HoldKind, Product
from inventory.errors import HoldAlreadyReleasedError, InsufficientAvailableStockError

quantities = st.integers(min_value=1, max_value=50)


class InventoryMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.product = Product(sku="SKU-PROP", name="Produto")
        self.active: list[Hold] = []
        self.released: list[Hold] = []
        self.expected_total = 0  # modelo de referência, o mais simples possível

    @rule(q=quantities)
    def receive(self, q):
        self.product.receive(q)
        self.expected_total += q

    @rule(q=quantities)
    def dispatch(self, q):
        before = self.product.available
        try:
            self.product.dispatch(q)
            self.expected_total -= q
        except InsufficientAvailableStockError:
            assert q > before

    @rule(q=quantities, kind=st.sampled_from(list(HoldKind)))
    def place_hold(self, q, kind):
        before = self.product.available
        try:
            if kind is HoldKind.RESERVATION:
                hold = self.product.reserve(q)
            else:
                hold = self.product.block(q)
            self.active.append(hold)
        except InsufficientAvailableStockError:
            assert q > before

    @precondition(lambda self: self.active)
    @rule(index=st.integers(min_value=0))
    def release(self, index):
        hold = self.active.pop(index % len(self.active))
        self.product.release(hold)
        self.released.append(hold)

    @precondition(lambda self: self.released)
    @rule(index=st.integers(min_value=0))
    def release_again_is_rejected(self, index):
        hold = self.released[index % len(self.released)]
        try:
            self.product.release(hold)
            raise AssertionError("liberar duas vezes deveria falhar")
        except HoldAlreadyReleasedError:
            pass

    @invariant()
    def counters_are_consistent(self):
        p = self.product
        assert p.total == self.expected_total
        assert p.reserved == sum(h.quantity for h in self.active if h.kind is HoldKind.RESERVATION)
        assert p.blocked == sum(h.quantity for h in self.active if h.kind is HoldKind.BLOCK)
        assert p.available == p.total - p.reserved - p.blocked
        assert min(p.total, p.available, p.reserved, p.blocked) >= 0


TestInventoryInvariants = InventoryMachine.TestCase
TestInventoryInvariants.settings = settings(max_examples=200, stateful_step_count=40, deadline=None)

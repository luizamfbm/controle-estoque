"""Modelo de domínio do estoque.

Conceitos:

- **Product**: guarda os contadores do estoque de um SKU.
    total      = unidades fisicamente em estoque
    reserved   = soma das reservas ativas
    blocked    = soma dos bloqueios ativos
    available  = total - reserved - blocked   (sempre calculado, nunca armazenado)

- **Hold** (retenção): uma reserva ou um bloqueio individual, com id próprio.
  Um produto pode ter várias retenções ativas ao mesmo tempo, de solicitações
  diferentes. Liberar uma retenção devolve a quantidade ao disponível.

Regra central: toda operação valida *antes* de alterar o estado. Se a
validação falhar, o objeto fica exatamente como estava.

Este módulo não conhece banco de dados nem HTTP: é Python puro, fácil de
testar isoladamente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from inventory.errors import (
    HoldAlreadyReleasedError,
    InsufficientAvailableStockError,
    InvalidQuantityError,
    InventoryInvariantViolation,
)

# Limite por operação. Evita valores absurdos (erro de digitação, ataque) e
# mantém os números bem dentro do INTEGER de 64 bits do banco.
MAX_QUANTITY_PER_OPERATION = 1_000_000_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HoldKind(str, Enum):
    RESERVATION = "RESERVATION"
    BLOCK = "BLOCK"

    @property
    def label(self) -> str:
        return "Reserva" if self is HoldKind.RESERVATION else "Bloqueio"

    @property
    def is_feminine(self) -> bool:  # só para concordância nas mensagens
        return self is HoldKind.RESERVATION


class HoldStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"


class MovementType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    RESERVE = "RESERVE"
    BLOCK = "BLOCK"
    RELEASE_RESERVATION = "RELEASE_RESERVATION"
    RELEASE_BLOCK = "RELEASE_BLOCK"


def ensure_valid_quantity(quantity: object) -> int:
    # bool é subclasse de int em Python (True == 1); rejeitamos explicitamente.
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity <= 0
        or quantity > MAX_QUANTITY_PER_OPERATION
    ):
        raise InvalidQuantityError(quantity)
    return quantity


@dataclass
class Hold:
    id: str
    sku: str
    kind: HoldKind
    quantity: int
    reference: str | None
    status: HoldStatus
    created_at: datetime
    released_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status is HoldStatus.ACTIVE


@dataclass(frozen=True)
class Movement:
    """Registro imutável de uma operação (trilha de auditoria)."""

    sku: str
    type: MovementType
    quantity: int
    reference: str | None
    hold_id: str | None
    occurred_at: datetime
    total_after: int
    reserved_after: int
    blocked_after: int


@dataclass
class Product:
    sku: str
    name: str
    total: int = 0
    reserved: int = 0
    blocked: int = 0
    created_at: datetime = field(default_factory=utcnow)

    @property
    def available(self) -> int:
        return self.total - self.reserved - self.blocked

    # ---------------------------------------------------------------- entrada
    def receive(self, quantity: int) -> None:
        ensure_valid_quantity(quantity)
        self.total += quantity
        self._check_invariants()

    # ------------------------------------------------------------------ saída
    def dispatch(self, quantity: int) -> None:
        ensure_valid_quantity(quantity)
        self._ensure_available(quantity)
        self.total -= quantity
        self._check_invariants()

    # ------------------------------------------------------ reserva/bloqueio
    def reserve(self, quantity: int, reference: str | None = None, *, now: datetime | None = None) -> Hold:
        return self._place_hold(HoldKind.RESERVATION, quantity, reference, now or utcnow())

    def block(self, quantity: int, reference: str | None = None, *, now: datetime | None = None) -> Hold:
        return self._place_hold(HoldKind.BLOCK, quantity, reference, now or utcnow())

    def release(self, hold: Hold, *, now: datetime | None = None) -> None:
        if hold.sku != self.sku:
            # Erro de programação (o serviço sempre carrega o produto da retenção).
            raise ValueError(f"Retenção {hold.id} pertence a {hold.sku}, não a {self.sku}.")
        if not hold.is_active:
            raise HoldAlreadyReleasedError(hold.id, hold.kind.label, feminine=hold.kind.is_feminine)

        if hold.kind is HoldKind.RESERVATION:
            self.reserved -= hold.quantity
        else:
            self.blocked -= hold.quantity
        hold.status = HoldStatus.RELEASED
        hold.released_at = now or utcnow()
        self._check_invariants()

    # -------------------------------------------------------------- internos
    def _place_hold(self, kind: HoldKind, quantity: int, reference: str | None, now: datetime) -> Hold:
        ensure_valid_quantity(quantity)
        self._ensure_available(quantity)

        if kind is HoldKind.RESERVATION:
            self.reserved += quantity
        else:
            self.blocked += quantity
        self._check_invariants()

        return Hold(
            id=str(uuid.uuid4()),
            sku=self.sku,
            kind=kind,
            quantity=quantity,
            reference=reference,
            status=HoldStatus.ACTIVE,
            created_at=now,
        )

    def _ensure_available(self, quantity: int) -> None:
        if quantity > self.available:
            raise InsufficientAvailableStockError(self.sku, quantity, self.available)

    def _check_invariants(self) -> None:
        if self.total < 0 or self.reserved < 0 or self.blocked < 0 or self.available < 0:
            raise InventoryInvariantViolation(
                "Estado de estoque inconsistente detectado.",
                sku=self.sku,
                total=self.total,
                reserved=self.reserved,
                blocked=self.blocked,
            )

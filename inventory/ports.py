"""Contratos (interfaces) entre o serviço e a persistência.

O serviço depende apenas destes Protocols, não do SQLite. Trocar o banco
(ex.: PostgreSQL) significa escrever outra implementação destes contratos,
sem tocar no domínio nem no serviço.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from inventory.domain import Hold, HoldKind, HoldStatus, Movement, Product


class ProductRepository(Protocol):
    def get(self, sku: str) -> Product | None: ...
    def add(self, product: Product) -> None: ...
    def update(self, product: Product) -> None: ...


class HoldRepository(Protocol):
    def get(self, hold_id: str) -> Hold | None: ...
    def add(self, hold: Hold) -> None: ...
    def update(self, hold: Hold) -> None: ...
    def list_by_sku(self, sku: str, kind: HoldKind, status: HoldStatus | None = None) -> list[Hold]: ...


class MovementRepository(Protocol):
    def add(self, movement: Movement) -> None: ...
    def list_by_sku(self, sku: str, limit: int = 100) -> list[Movement]: ...


class UnitOfWork(Protocol):
    """Tudo feito dentro de uma unidade de trabalho é confirmado junto ou
    desfeito junto (uma transação)."""

    products: ProductRepository
    holds: HoldRepository
    movements: MovementRepository


class Database(Protocol):
    def unit_of_work(self, *, read_only: bool = False) -> AbstractContextManager[UnitOfWork]: ...

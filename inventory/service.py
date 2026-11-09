"""Casos de uso do estoque.

O serviço orquestra: abre a transação, carrega o produto, delega a regra ao
domínio, persiste o novo estado e registra a movimentação. Tudo dentro da
mesma unidade de trabalho: ou tudo é gravado, ou nada é.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from inventory.domain import (
    Hold,
    HoldKind,
    HoldStatus,
    Movement,
    MovementType,
    Product,
    utcnow,
)
from inventory.errors import HoldNotFoundError, ProductAlreadyExistsError, ProductNotFoundError
from inventory.ports import Database, UnitOfWork

_HOLD_MOVEMENT = {HoldKind.RESERVATION: MovementType.RESERVE, HoldKind.BLOCK: MovementType.BLOCK}
_RELEASE_MOVEMENT = {
    HoldKind.RESERVATION: MovementType.RELEASE_RESERVATION,
    HoldKind.BLOCK: MovementType.RELEASE_BLOCK,
}


class InventoryService:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._db = db
        self._clock = clock

    # ------------------------------------------------------------- produtos
    def register_product(self, sku: str, name: str) -> Product:
        with self._db.unit_of_work() as uow:
            if uow.products.get(sku) is not None:
                raise ProductAlreadyExistsError(sku)
            product = Product(sku=sku, name=name, created_at=self._clock())
            uow.products.add(product)
            return product

    def get_product(self, sku: str) -> Product:
        with self._db.unit_of_work(read_only=True) as uow:
            return self._load_product(uow, sku)

    # ------------------------------------------------------- entrada/saída
    def receive(self, sku: str, quantity: int, reference: str | None = None) -> Product:
        with self._db.unit_of_work() as uow:
            product = self._load_product(uow, sku)
            product.receive(quantity)
            self._save(uow, product, MovementType.ENTRY, quantity, reference)
            return product

    def dispatch(self, sku: str, quantity: int, reference: str | None = None) -> Product:
        with self._db.unit_of_work() as uow:
            product = self._load_product(uow, sku)
            product.dispatch(quantity)
            self._save(uow, product, MovementType.EXIT, quantity, reference)
            return product

    # --------------------------------------------------- reserva/bloqueio
    def reserve(self, sku: str, quantity: int, reference: str | None = None) -> Hold:
        return self._place_hold(HoldKind.RESERVATION, sku, quantity, reference)

    def block(self, sku: str, quantity: int, reference: str | None = None) -> Hold:
        return self._place_hold(HoldKind.BLOCK, sku, quantity, reference)

    def get_hold(self, hold_id: str, kind: HoldKind) -> Hold:
        with self._db.unit_of_work(read_only=True) as uow:
            return self._load_hold(uow, hold_id, kind)

    def list_holds(self, sku: str, kind: HoldKind, status: HoldStatus | None = None) -> list[Hold]:
        with self._db.unit_of_work(read_only=True) as uow:
            self._load_product(uow, sku)
            return uow.holds.list_by_sku(sku, kind, status)

    def release(self, hold_id: str, kind: HoldKind) -> Hold:
        with self._db.unit_of_work() as uow:
            hold = self._load_hold(uow, hold_id, kind)
            if not hold.is_active:
                # Idempotente: repetir a liberação (ex.: retry após timeout)
                # devolve a retenção como está, sem alterar o estoque nem
                # gerar nova movimentação.
                return hold
            product = self._load_product(uow, hold.sku)
            product.release(hold, now=self._clock())
            uow.holds.update(hold)
            self._save(uow, product, _RELEASE_MOVEMENT[kind], hold.quantity, hold.reference, hold.id)
            return hold

    # ------------------------------------------------------- movimentações
    def list_movements(self, sku: str, limit: int = 100) -> list[Movement]:
        with self._db.unit_of_work(read_only=True) as uow:
            self._load_product(uow, sku)
            return uow.movements.list_by_sku(sku, limit)

    # ------------------------------------------------------------ internos
    def _place_hold(self, kind: HoldKind, sku: str, quantity: int, reference: str | None) -> Hold:
        with self._db.unit_of_work() as uow:
            product = self._load_product(uow, sku)
            if kind is HoldKind.RESERVATION:
                hold = product.reserve(quantity, reference, now=self._clock())
            else:
                hold = product.block(quantity, reference, now=self._clock())
            uow.holds.add(hold)
            self._save(uow, product, _HOLD_MOVEMENT[kind], quantity, reference, hold.id)
            return hold

    @staticmethod
    def _load_product(uow: UnitOfWork, sku: str) -> Product:
        product = uow.products.get(sku)
        if product is None:
            raise ProductNotFoundError(sku)
        return product

    @staticmethod
    def _load_hold(uow: UnitOfWork, hold_id: str, kind: HoldKind) -> Hold:
        hold = uow.holds.get(hold_id)
        # Um id de bloqueio consultado na rota de reservas é tratado como
        # "não encontrado": cada recurso só enxerga o seu tipo.
        if hold is None or hold.kind is not kind:
            raise HoldNotFoundError(hold_id, kind.label, feminine=kind.is_feminine)
        return hold

    def _save(
        self,
        uow: UnitOfWork,
        product: Product,
        movement_type: MovementType,
        quantity: int,
        reference: str | None,
        hold_id: str | None = None,
    ) -> None:
        uow.products.update(product)
        uow.movements.add(
            Movement(
                sku=product.sku,
                type=movement_type,
                quantity=quantity,
                reference=reference,
                hold_id=hold_id,
                occurred_at=self._clock(),
                total_after=product.total,
                reserved_after=product.reserved,
                blocked_after=product.blocked,
            )
        )

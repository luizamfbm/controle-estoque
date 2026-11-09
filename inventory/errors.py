"""Erros de negócio do domínio de estoque.

Cada erro carrega um `code` estável (usado pela API e por clientes) e
`details` estruturados, para que o chamador não precise interpretar a
mensagem em texto.
"""

from __future__ import annotations

from typing import Any


class InventoryError(Exception):
    """Base de todos os erros de negócio do estoque."""

    code = "INVENTORY_ERROR"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidQuantityError(InventoryError):
    code = "INVALID_QUANTITY"

    def __init__(self, quantity: object) -> None:
        super().__init__(
            "A quantidade deve ser um número inteiro maior que zero.",
            quantity=repr(quantity),
        )


class ProductNotFoundError(InventoryError):
    code = "PRODUCT_NOT_FOUND"

    def __init__(self, sku: str) -> None:
        super().__init__(f"Produto '{sku}' não encontrado.", sku=sku)


class ProductAlreadyExistsError(InventoryError):
    code = "PRODUCT_ALREADY_EXISTS"

    def __init__(self, sku: str) -> None:
        super().__init__(f"Produto '{sku}' já está cadastrado.", sku=sku)


class InsufficientAvailableStockError(InventoryError):
    code = "INSUFFICIENT_AVAILABLE_STOCK"

    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(
            f"Quantidade disponível insuficiente para o produto '{sku}': "
            f"solicitado {requested}, disponível {available}.",
            sku=sku,
            requested=requested,
            available=available,
        )


class HoldNotFoundError(InventoryError):
    code = "HOLD_NOT_FOUND"

    def __init__(self, hold_id: str, kind_label: str, *, feminine: bool) -> None:
        ending = "a" if feminine else "o"
        super().__init__(f"{kind_label} '{hold_id}' não encontrad{ending}.", hold_id=hold_id)


class HoldAlreadyReleasedError(InventoryError):
    code = "HOLD_ALREADY_RELEASED"

    def __init__(self, hold_id: str, kind_label: str, *, feminine: bool) -> None:
        ending = "a" if feminine else "o"
        super().__init__(f"{kind_label} '{hold_id}' já foi liberad{ending}.", hold_id=hold_id)


class InventoryInvariantViolation(InventoryError):
    """Nunca deveria acontecer: indica bug. Existe como rede de segurança."""

    code = "INVARIANT_VIOLATION"

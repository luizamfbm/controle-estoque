"""Contratos HTTP (entrada e saída) da API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from inventory.domain import MAX_QUANTITY_PER_OPERATION, Hold, HoldKind, HoldStatus, Movement, MovementType, Product

SKU_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"

Sku = Annotated[str, StringConstraints(pattern=SKU_PATTERN)]
# strict=True: recusa "5" (texto), 5.0 (float) e true (booleano).
Quantity = Annotated[int, Field(strict=True, gt=0, le=MAX_QUANTITY_PER_OPERATION, examples=[10])]
Reference = Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class _Request(BaseModel):
    # extra="forbid": um campo com nome errado (ex.: "quantidade") vira erro
    # 422 em vez de ser ignorado em silêncio.
    model_config = ConfigDict(extra="forbid")


class ProductCreate(_Request):
    sku: Sku = Field(examples=["SKU-001"])
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] = Field(
        examples=["Camiseta Azul M"]
    )


class StockOperation(_Request):
    quantity: Quantity
    reference: Reference = Field(default=None, description="Ex.: nota fiscal, pedido.", examples=["NF-123"])


class HoldCreate(_Request):
    quantity: Quantity
    reference: Reference = Field(
        default=None, description="Pedido/solicitação ou motivo do bloqueio.", examples=["PEDIDO-42"]
    )


class StockResponse(BaseModel):
    sku: str
    name: str
    total: int
    available: int
    reserved: int
    blocked: int

    @classmethod
    def of(cls, p: Product) -> StockResponse:
        return cls(sku=p.sku, name=p.name, total=p.total, available=p.available, reserved=p.reserved,
                   blocked=p.blocked)


class HoldResponse(BaseModel):
    id: str
    sku: str
    type: HoldKind
    quantity: int
    reference: str | None
    status: HoldStatus
    created_at: datetime
    released_at: datetime | None

    @classmethod
    def of(cls, h: Hold) -> HoldResponse:
        return cls(id=h.id, sku=h.sku, type=h.kind, quantity=h.quantity, reference=h.reference,
                   status=h.status, created_at=h.created_at, released_at=h.released_at)


class MovementResponse(BaseModel):
    type: MovementType
    quantity: int
    reference: str | None
    hold_id: str | None
    occurred_at: datetime
    total_after: int
    reserved_after: int
    blocked_after: int

    @classmethod
    def of(cls, m: Movement) -> MovementResponse:
        return cls(type=m.type, quantity=m.quantity, reference=m.reference, hold_id=m.hold_id,
                   occurred_at=m.occurred_at, total_after=m.total_after, reserved_after=m.reserved_after,
                   blocked_after=m.blocked_after)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody

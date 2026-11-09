"""API HTTP (FastAPI).

Execução:  uvicorn inventory.api:create_app --factory
Documentação interativa: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from inventory import errors
from inventory.domain import HoldKind, HoldStatus
from inventory.schemas import (
    SKU_PATTERN,
    ErrorResponse,
    HoldCreate,
    HoldResponse,
    MovementResponse,
    ProductCreate,
    StockOperation,
    StockResponse,
)
from inventory.service import InventoryService
from inventory.sqlite_store import SqliteDatabase

logger = logging.getLogger("inventory")

DEFAULT_DB_PATH = "data/estoque.db"

# Mapeamento erro de negócio -> status HTTP.
# 404: recurso não existe | 409: conflito com o estado atual | 422: entrada inválida
_STATUS_BY_ERROR: dict[type[errors.InventoryError], int] = {
    errors.ProductNotFoundError: 404,
    errors.HoldNotFoundError: 404,
    errors.ProductAlreadyExistsError: 409,
    errors.InsufficientAvailableStockError: 409,
    errors.HoldAlreadyReleasedError: 409,
    errors.InvalidQuantityError: 422,
}

SkuPath = Annotated[str, Path(pattern=SKU_PATTERN, description="Identificação do produto (SKU).")]
_ERRORS = {
    404: {"model": ErrorResponse, "description": "Não encontrado"},
    409: {"model": ErrorResponse, "description": "Conflito com o estado atual do estoque"},
    422: {"model": ErrorResponse, "description": "Entrada inválida"},
}


def get_service(request: Request) -> InventoryService:
    return request.app.state.service


Service = Annotated[InventoryService, Depends(get_service)]

router = APIRouter()


# ---------------------------------------------------------------- produtos
@router.post("/products", status_code=201, response_model=StockResponse, responses=_ERRORS, tags=["Produtos"],
             summary="Cadastrar produto")
def register_product(body: ProductCreate, service: Service) -> StockResponse:
    return StockResponse.of(service.register_product(body.sku, body.name))


@router.get("/products/{sku}", response_model=StockResponse, responses=_ERRORS, tags=["Produtos"],
            summary="Consultar situação do estoque")
def get_product(sku: SkuPath, service: Service) -> StockResponse:
    return StockResponse.of(service.get_product(sku))


@router.post("/products/{sku}/entries", response_model=StockResponse, responses=_ERRORS, tags=["Movimentação"],
             summary="Entrada de unidades")
def receive(sku: SkuPath, body: StockOperation, service: Service) -> StockResponse:
    return StockResponse.of(service.receive(sku, body.quantity, body.reference))


@router.post("/products/{sku}/exits", response_model=StockResponse, responses=_ERRORS, tags=["Movimentação"],
             summary="Saída de unidades (somente do disponível)")
def dispatch(sku: SkuPath, body: StockOperation, service: Service) -> StockResponse:
    return StockResponse.of(service.dispatch(sku, body.quantity, body.reference))


@router.get("/products/{sku}/movements", response_model=list[MovementResponse], responses=_ERRORS,
            tags=["Movimentação"], summary="Histórico de movimentações (mais recentes primeiro)")
def list_movements(sku: SkuPath, service: Service,
                   limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[MovementResponse]:
    return [MovementResponse.of(m) for m in service.list_movements(sku, limit)]


# ------------------------------------------------- reservas e bloqueios
def _register_hold_routes(kind: HoldKind, collection: str, tag: str) -> None:
    """Reservas e bloqueios têm o mesmo ciclo de vida; as rotas são geradas
    para os dois, mantendo URLs separadas e explícitas."""

    label = kind.label.lower()

    @router.post(f"/products/{{sku}}/{collection}", status_code=201, response_model=HoldResponse,
                 responses=_ERRORS, tags=[tag], summary=f"Criar {label}", name=f"create_{collection}")
    def create(sku: SkuPath, body: HoldCreate, service: Service) -> HoldResponse:
        if kind is HoldKind.RESERVATION:
            return HoldResponse.of(service.reserve(sku, body.quantity, body.reference))
        return HoldResponse.of(service.block(sku, body.quantity, body.reference))

    @router.get(f"/products/{{sku}}/{collection}", response_model=list[HoldResponse], responses=_ERRORS,
                tags=[tag], summary=f"Listar {label}s do produto", name=f"list_{collection}")
    def list_for_product(sku: SkuPath, service: Service,
                         status_filter: Annotated[HoldStatus | None, Query(alias="status")] = None
                         ) -> list[HoldResponse]:
        return [HoldResponse.of(h) for h in service.list_holds(sku, kind, status_filter)]

    @router.get(f"/{collection}/{{hold_id}}", response_model=HoldResponse, responses=_ERRORS, tags=[tag],
                summary=f"Consultar {label}", name=f"get_{collection}")
    def get_one(hold_id: str, service: Service) -> HoldResponse:
        return HoldResponse.of(service.get_hold(hold_id, kind))

    @router.post(f"/{collection}/{{hold_id}}/release", response_model=HoldResponse, responses=_ERRORS,
                 tags=[tag], summary=f"Liberar {label}", name=f"release_{collection}")
    def release(hold_id: str, service: Service) -> HoldResponse:
        return HoldResponse.of(service.release(hold_id, kind))


_register_hold_routes(HoldKind.RESERVATION, "reservations", "Reservas")
_register_hold_routes(HoldKind.BLOCK, "blocks", "Bloqueios")


@router.get("/health", tags=["Infra"], summary="Verificação de saúde")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------- erros
def _error(status_code: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body))


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(errors.InventoryError)
    async def handle_domain_error(_: Request, exc: errors.InventoryError) -> JSONResponse:
        status_code = _STATUS_BY_ERROR.get(type(exc), 500)
        if status_code >= 500:
            logger.error("Erro de negócio inesperado: %s %s", exc.code, exc.details)
        return _error(status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ]
        return _error(422, "VALIDATION_ERROR", "Requisição inválida.", {"errors": problems})

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Detalhes vão para o log, nunca para o cliente.
        logger.exception("Erro inesperado")
        return _error(500, "INTERNAL_ERROR", "Erro interno. Tente novamente mais tarde.")


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Controle de Estoque",
        version="1.0.0",
        description="Entrada, saída, reserva, bloqueio, liberação e consulta de estoque por produto.",
    )
    db = SqliteDatabase(db_path or os.environ.get("INVENTORY_DB_PATH", DEFAULT_DB_PATH))
    app.state.service = InventoryService(db)
    app.include_router(router)
    _install_error_handlers(app)
    return app

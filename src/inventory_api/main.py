import secrets
from collections.abc import Generator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from inventory_api import service
from inventory_api.config import Settings
from inventory_api.db import SCHEMA_REVISION, build_engine
from inventory_api.errors import DomainError
from inventory_api.models import Product, Reservation, StockMovement
from inventory_api.schemas import (
    AdjustmentCreate,
    ErrorResponse,
    HealthOut,
    MovementOut,
    MovementPage,
    ProductCreate,
    ProductOut,
    ProductPage,
    ReservationCreate,
    ReservationOut,
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def authenticate(request: Request, key: Annotated[str | None, Depends(api_key_header)]) -> None:
    expected = request.app.state.settings.api_key.get_secret_value()
    if key is None or not secrets.compare_digest(key.encode(), expected.encode()):
        raise DomainError(401, "unauthorized", "A valid X-API-Key header is required")


def get_session(request: Request) -> Generator[Session]:
    with request.app.state.sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
KeyDep = Annotated[UUID, Header(alias="Idempotency-Key")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10000)]
router = APIRouter(
    prefix="/v1",
    dependencies=[Depends(authenticate)],
    responses={code: {"model": ErrorResponse} for code in (401, 404, 409, 422, 503)},
)


@router.post("/products", status_code=201, tags=["Products"])
def create_product(body: ProductCreate, session: SessionDep) -> ProductOut:
    return service.create_product(session, body)


@router.get("/products", tags=["Products"])
def list_products(session: SessionDep, limit: Limit = 50, offset: Offset = 0) -> ProductPage:
    items = session.scalars(
        select(Product).order_by(Product.created_at, Product.id).limit(limit).offset(offset)
    )
    return ProductPage(
        items=[ProductOut.model_validate(item) for item in items], limit=limit, offset=offset
    )


@router.get("/products/{product_id}", tags=["Products"])
def get_product(product_id: UUID, session: SessionDep) -> ProductOut:
    return ProductOut.model_validate(service.require_product(session, product_id))


@router.post(
    "/products/{product_id}/adjustments",
    status_code=201,
    tags=["Stock"],
    responses={200: {"model": MovementOut, "description": "Existing operation replayed"}},
)
def adjust_stock(
    product_id: UUID, body: AdjustmentCreate, key: KeyDep, response: Response, session: SessionDep
) -> MovementOut:
    movement, replayed = service.adjust_stock(session, product_id, body, key)
    response.status_code = 200 if replayed else 201
    response.headers["Idempotent-Replayed"] = str(replayed).lower()
    return movement


@router.get("/products/{product_id}/movements", tags=["Stock"])
def list_movements(
    product_id: UUID, session: SessionDep, limit: Limit = 50, offset: Offset = 0
) -> MovementPage:
    service.require_product(session, product_id)
    movements = session.scalars(
        select(StockMovement)
        .where(StockMovement.product_id == product_id)
        .order_by(StockMovement.id)
        .limit(limit)
        .offset(offset)
    )
    return MovementPage(
        items=[MovementOut.model_validate(item) for item in movements], limit=limit, offset=offset
    )


@router.post(
    "/reservations",
    status_code=201,
    tags=["Reservations"],
    responses={200: {"model": ReservationOut, "description": "Existing operation replayed"}},
)
def reserve_stock(
    body: ReservationCreate, key: KeyDep, response: Response, session: SessionDep
) -> ReservationOut:
    reservation, replayed = service.reserve_stock(session, body, key)
    response.status_code = 200 if replayed else 201
    response.headers["Idempotent-Replayed"] = str(replayed).lower()
    return reservation


@router.get("/reservations/{reservation_id}", tags=["Reservations"])
def get_reservation(reservation_id: UUID, session: SessionDep) -> ReservationOut:
    reservation = session.get(Reservation, reservation_id)
    if reservation is None:
        raise DomainError(404, "reservation_not_found", "Reservation does not exist")
    return ReservationOut.model_validate(reservation)


@router.post("/reservations/{reservation_id}/fulfill", tags=["Reservations"])
def fulfill_reservation(reservation_id: UUID, session: SessionDep) -> ReservationOut:
    return service.transition_reservation(session, reservation_id, "fulfilled")


@router.post("/reservations/{reservation_id}/cancel", tags=["Reservations"])
def cancel_reservation(reservation_id: UUID, session: SessionDep) -> ReservationOut:
    return service.transition_reservation(session, reservation_id, "cancelled")


def error_response(
    status: int, code: str, message: str, details=None, headers=None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or []}},
        headers=headers,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or Settings()
        engine = build_engine(app.state.settings)
        app.state.sessions = sessionmaker(engine, expire_on_commit=False)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Inventory Reservations API",
        version="0.1.0",
        description=(
            "Synthetic, local demonstration of PostgreSQL inventory transactions. "
            "All /v1 routes require X-API-Key. Reservation and adjustment creation "
            "require a UUID Idempotency-Key; replay returns 200 instead of 201. "
            "Not a production inventory or payment system."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [
            {"location": list(item["loc"]), "type": item["type"], "message": item["msg"]}
            for item in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return error_response(exc.status_code, "http_error", str(exc.detail), headers=exc.headers)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: SQLAlchemyError):
        state = getattr(getattr(exc, "orig", None), "sqlstate", None)
        busy = state in {"55P03", "40P01", "57014"}
        return error_response(
            503,
            "database_busy" if busy else "database_unavailable",
            "Database busy; retry the same operation" if busy else "Database unavailable",
            headers={"Retry-After": "1"},
        )

    @app.get("/health/live", tags=["Health"])
    def live() -> HealthOut:
        return HealthOut(status="alive")

    @app.get("/health/ready", tags=["Health"], responses={503: {"model": ErrorResponse}})
    def ready(session: SessionDep) -> HealthOut:
        revision = session.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != SCHEMA_REVISION:
            raise DomainError(503, "schema_not_ready", "Apply the expected database migrations")
        return HealthOut(status="ready")

    app.include_router(router)
    return app


app = create_app()

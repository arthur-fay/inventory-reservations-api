"""All inventory writers lock the product first and commit each operation atomically."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inventory_api.errors import DomainError
from inventory_api.models import MAX_STOCK, Product, Reservation, StockMovement
from inventory_api.schemas import (
    AdjustmentCreate,
    MovementOut,
    ProductCreate,
    ProductOut,
    ReservationCreate,
    ReservationOut,
)


def require_product(session: Session, product_id: UUID, *, lock: bool = False) -> Product:
    query = select(Product).where(Product.id == product_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    product = session.scalar(query)
    if product is None:
        raise DomainError(404, "product_not_found", "Product does not exist")
    return product


def _constraint(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _idempotency_conflict() -> DomainError:
    return DomainError(409, "idempotency_conflict", "This key was used for a different request")


def create_product(session: Session, body: ProductCreate) -> ProductOut:
    try:
        with session.begin():
            product = Product(sku=body.sku, name=body.name, on_hand=body.initial_stock)
            session.add(product)
            session.flush()
            if body.initial_stock:
                session.add(
                    StockMovement(
                        product_id=product.id,
                        delta=body.initial_stock,
                        on_hand_after=body.initial_stock,
                        kind="opening",
                        reason="Synthetic opening balance",
                    )
                )
            return ProductOut.model_validate(product)
    except IntegrityError as exc:
        if _constraint(exc) == "uq_products_sku":
            raise DomainError(409, "duplicate_sku", "A product with this SKU exists") from exc
        raise


def adjust_stock(
    session: Session, product_id: UUID, body: AdjustmentCreate, key: UUID
) -> tuple[MovementOut, bool]:
    try:
        with session.begin():
            product = require_product(session, product_id, lock=True)
            previous = session.scalar(
                select(StockMovement).where(StockMovement.idempotency_key == key)
            )
            if previous:
                if (previous.product_id, previous.delta, previous.reason) != (
                    product_id,
                    body.delta,
                    body.reason,
                ):
                    raise _idempotency_conflict()
                return MovementOut.model_validate(previous), True
            balance = product.on_hand + body.delta
            if balance < product.reserved:
                raise DomainError(
                    409, "insufficient_stock", "Adjustment would consume reserved stock"
                )
            if balance > MAX_STOCK:
                raise DomainError(409, "stock_limit", "Adjustment exceeds the stock limit")
            product.on_hand = balance
            movement = StockMovement(
                product_id=product_id,
                delta=body.delta,
                on_hand_after=balance,
                kind="adjustment",
                reason=body.reason,
                idempotency_key=key,
            )
            session.add(movement)
            session.flush()
            return MovementOut.model_validate(movement), False
    except IntegrityError as exc:
        if _constraint(exc) == "uq_movements_idempotency_key":
            raise _idempotency_conflict() from exc
        raise


def reserve_stock(
    session: Session, body: ReservationCreate, key: UUID
) -> tuple[ReservationOut, bool]:
    try:
        with session.begin():
            product = require_product(session, body.product_id, lock=True)
            previous = session.scalar(select(Reservation).where(Reservation.idempotency_key == key))
            if previous:
                if (previous.product_id, previous.quantity) != (body.product_id, body.quantity):
                    raise _idempotency_conflict()
                return ReservationOut.model_validate(previous), True
            if product.available < body.quantity:
                raise DomainError(409, "insufficient_stock", "Not enough available stock")
            product.reserved += body.quantity
            reservation = Reservation(
                product_id=body.product_id, quantity=body.quantity, idempotency_key=key
            )
            session.add(reservation)
            session.flush()
            return ReservationOut.model_validate(reservation), False
    except IntegrityError as exc:
        # Different products do not share a row lock. The unique key decides that race;
        # session.begin() rolls back the losing stock update before this is returned.
        if _constraint(exc) == "uq_reservations_idempotency_key":
            raise _idempotency_conflict() from exc
        raise


def transition_reservation(session: Session, reservation_id: UUID, target: str) -> ReservationOut:
    with session.begin():
        product_id = session.scalar(
            select(Reservation.product_id).where(Reservation.id == reservation_id)
        )
        if product_id is None:
            raise DomainError(404, "reservation_not_found", "Reservation does not exist")
        # Lock order is always product, then reservation; no path takes these in reverse.
        product = require_product(session, product_id, lock=True)
        reservation = session.scalar(
            select(Reservation).where(Reservation.id == reservation_id).with_for_update()
        )
        assert reservation is not None
        if reservation.status == target:
            return ReservationOut.model_validate(reservation)
        if reservation.status != "active":
            raise DomainError(409, "reservation_closed", "Reservation is already closed")
        product.reserved -= reservation.quantity
        if target == "fulfilled":
            product.on_hand -= reservation.quantity
            session.add(
                StockMovement(
                    product_id=product_id,
                    delta=-reservation.quantity,
                    on_hand_after=product.on_hand,
                    kind="fulfillment",
                    reason="Reservation fulfilled",
                    reservation_id=reservation_id,
                )
            )
        reservation.status = target
        session.flush()
        return ReservationOut.model_validate(reservation)

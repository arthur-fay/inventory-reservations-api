from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MAX_STOCK = 1_000_000


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_products_sku"),
        CheckConstraint("sku ~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'", name="ck_products_sku"),
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_products_name"),
        CheckConstraint("on_hand BETWEEN 0 AND 1000000", name="ck_products_on_hand"),
        CheckConstraint("reserved >= 0 AND reserved <= on_hand", name="ck_products_reserved"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_reservations_idempotency_key"),
        CheckConstraint("quantity BETWEEN 1 AND 1000000", name="ck_reservations_quantity"),
        CheckConstraint(
            "status IN ('active', 'fulfilled', 'cancelled')", name="ck_reservations_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_movements_idempotency_key"),
        UniqueConstraint("reservation_id", name="uq_movements_reservation_id"),
        CheckConstraint("delta <> 0", name="ck_movements_nonzero_delta"),
        CheckConstraint("on_hand_after BETWEEN 0 AND 1000000", name="ck_movements_balance"),
        CheckConstraint("length(btrim(reason)) BETWEEN 3 AND 200", name="ck_movements_reason"),
        CheckConstraint(
            "(kind = 'opening' AND delta > 0 AND reservation_id IS NULL "
            "AND idempotency_key IS NULL) OR "
            "(kind = 'adjustment' AND reservation_id IS NULL AND idempotency_key IS NOT NULL) OR "
            "(kind = 'fulfillment' AND delta < 0 AND reservation_id IS NOT NULL "
            "AND idempotency_key IS NULL)",
            name="ck_movements_kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    on_hand_after: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[UUID | None] = mapped_column(Uuid)
    reservation_id: Mapped[UUID | None] = mapped_column(ForeignKey("reservations.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

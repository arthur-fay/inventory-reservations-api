"""Create the inventory, reservations and stock ledger schema.

This migration is deliberately independent of mutable ORM model definitions.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_inventory"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku", name="uq_products_sku"),
        sa.CheckConstraint("sku ~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'", name="ck_products_sku"),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_products_name"),
        sa.CheckConstraint("on_hand BETWEEN 0 AND 1000000", name="ck_products_on_hand"),
        sa.CheckConstraint("reserved >= 0 AND reserved <= on_hand", name="ck_products_reserved"),
    )
    op.create_table(
        "reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_reservations_idempotency_key"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 1000000", name="ck_reservations_quantity"),
        sa.CheckConstraint(
            "status IN ('active', 'fulfilled', 'cancelled')", name="ck_reservations_status"
        ),
    )
    op.create_index("ix_reservations_product_id", "reservations", ["product_id"])
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("on_hand_after", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=True),
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["reservation_id"], ["reservations.id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_movements_idempotency_key"),
        sa.UniqueConstraint("reservation_id", name="uq_movements_reservation_id"),
        sa.CheckConstraint("delta <> 0", name="ck_movements_nonzero_delta"),
        sa.CheckConstraint("on_hand_after BETWEEN 0 AND 1000000", name="ck_movements_balance"),
        sa.CheckConstraint("length(btrim(reason)) BETWEEN 3 AND 200", name="ck_movements_reason"),
        sa.CheckConstraint(
            "(kind = 'opening' AND delta > 0 AND reservation_id IS NULL "
            "AND idempotency_key IS NULL) OR "
            "(kind = 'adjustment' AND reservation_id IS NULL AND idempotency_key IS NOT NULL) OR "
            "(kind = 'fulfillment' AND delta < 0 AND reservation_id IS NOT NULL "
            "AND idempotency_key IS NULL)",
            name="ck_movements_kind",
        ),
    )
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_movements_product_id", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_index("ix_reservations_product_id", table_name="reservations")
    op.drop_table("reservations")
    op.drop_table("products")

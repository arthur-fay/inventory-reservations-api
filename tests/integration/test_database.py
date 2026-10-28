from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, update
from sqlalchemy.exc import IntegrityError

from inventory_api.models import Product, Reservation, StockMovement


@pytest.mark.parametrize(
    "values",
    [
        {"on_hand": -1},
        {"on_hand": 1_000_001},
        {"reserved": -1},
        {"reserved": 6},
        {"sku": "invalid sku"},
        {"name": ""},
    ],
)
def test_database_rejects_invalid_product_state(engine, product, values):
    item = product(5)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(update(Product).where(Product.id == UUID(item["id"])).values(**values))


@pytest.mark.parametrize(
    "values",
    [
        {"quantity": 0},
        {"quantity": 1_000_001},
        {"status": "unknown"},
        {"product_id": uuid4()},
    ],
)
def test_database_rejects_invalid_reservations(engine, product, reserve, values):
    reservation = reserve(product(5), 2).json()
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            update(Reservation).where(Reservation.id == UUID(reservation["id"])).values(**values)
        )


@pytest.mark.parametrize(
    "values",
    [
        {"delta": 0},
        {"on_hand_after": -1},
        {"kind": "unknown"},
        {"reason": "x"},
    ],
)
def test_database_rejects_invalid_ledger_rows(engine, product, values):
    item = product(5)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            update(StockMovement)
            .where(StockMovement.product_id == UUID(item["id"]))
            .values(**values)
        )


def test_migrations_are_reversible_and_match_models(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert not inspect(connection).has_table("products")
        command.upgrade(config, "head")
        assert {"products", "reservations", "stock_movements"} <= set(
            inspect(connection).get_table_names()
        )
        command.check(config)

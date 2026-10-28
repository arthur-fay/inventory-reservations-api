"""Integration tests require real PostgreSQL and a disposable *_test database."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from inventory_api.config import Settings
from inventory_api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = "integration-test-key-not-for-deployment"


@pytest.fixture(scope="session")
def database_url():
    value = os.environ.get("INVENTORY_TEST_DATABASE_URL")
    if not value:
        pytest.fail("Set INVENTORY_TEST_DATABASE_URL to a disposable PostgreSQL *_test database")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+psycopg" or not (parsed.database or "").endswith("_test"):
        pytest.fail("Refusing destructive tests: use postgresql+psycopg and a *_test database")
    return value


@pytest.fixture(scope="session")
def engine(database_url):
    engine = create_engine(database_url, connect_args={"connect_timeout": 5})
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database_and_check_invariants(engine):
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE stock_movements, reservations, products RESTART IDENTITY")
        )
    yield
    # Independently reconcile counters to the ledger and the active reservations.
    # No API implementation helper is reused to compute the expected balances.
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text("""
            SELECT p.id, p.on_hand, p.reserved,
              COALESCE((SELECT SUM(m.delta) FROM stock_movements m
                        WHERE m.product_id = p.id), 0) AS ledger_balance,
              COALESCE((SELECT SUM(r.quantity) FROM reservations r
                        WHERE r.product_id = p.id AND r.status = 'active'), 0) AS held
            FROM products p
        """)
            )
            .mappings()
            .all()
        )
    for row in rows:
        assert 0 <= row["reserved"] <= row["on_hand"] <= 1_000_000, row
        assert row["on_hand"] == row["ledger_balance"], row
        assert row["reserved"] == row["held"], row


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url, api_key=TEST_KEY, lock_timeout_ms=5000, _env_file=None
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers={"X-API-Key": TEST_KEY}) as client:
        yield client


@pytest.fixture
def product(client):
    def create(stock=10, **fields):
        body = {"sku": f"DEMO-{uuid4().hex[:12]}", "name": "Synthetic item", "initial_stock": stock}
        response = client.post("/v1/products", json=body | fields)
        assert response.status_code == 201, response.text
        return response.json()

    return create


@pytest.fixture
def reserve(client):
    def create(product, quantity=1, key=None):
        return client.post(
            "/v1/reservations",
            json={"product_id": product["id"], "quantity": quantity},
            headers={"Idempotency-Key": str(key or uuid4())},
        )

    return create


@pytest.fixture
def adjust(client):
    def create(product, delta, key=None, reason="Synthetic correction"):
        return client.post(
            f"/v1/products/{product['id']}/adjustments",
            json={"delta": delta, "reason": reason},
            headers={"Idempotency-Key": str(key or uuid4())},
        )

    return create

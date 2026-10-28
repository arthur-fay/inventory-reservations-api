import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from inventory_api.config import DatabaseSettings, Settings
from inventory_api.main import create_app
from inventory_api.schemas import AdjustmentCreate, ProductCreate, ReservationCreate

KEY = "unit-test-key-not-for-public-deployment"
URL = "postgresql+psycopg://unused:unused@127.0.0.1:1/unused_test"


@pytest.fixture
def client():
    # Engine connections are lazy: these tests intentionally have no database.
    settings = Settings(database_url=URL, api_key=KEY, _env_file=None)
    with TestClient(create_app(settings)) as client:
        yield client


def test_public_liveness_and_structured_http_errors(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/unknown").json()["error"]["code"] == "http_error"
    assert client.put("/health/live").status_code == 405


@pytest.mark.parametrize("key", [None, "wrong", "é" * 24])
def test_authentication_rejects_missing_wrong_and_unicode_keys(client, key):
    headers = {b"X-API-Key": key.encode("utf-8")} if key else {}
    response = client.get("/v1/products", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert KEY not in response.text


def test_validation_errors_do_not_echo_inputs(client):
    private_input = "do-not-echo-this-value"
    response = client.post(
        "/v1/products",
        headers={"X-API-Key": KEY},
        json={"sku": "DEMO", "name": "Demo", "initial_stock": True, "unexpected": private_input},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert private_input not in response.text


def test_contract_and_security_are_committed():
    contract = create_app().openapi()
    expected = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    assert json.loads(expected.read_text()) == contract
    for path, methods in contract["paths"].items():
        for operation in methods.values():
            assert bool(operation.get("security")) == path.startswith("/v1/")
    for path in ("/v1/reservations", "/v1/products/{product_id}/adjustments"):
        assert {"200", "201"} <= set(contract["paths"][path]["post"]["responses"])


def test_unavailable_database_is_a_sanitized_service_error(client):
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "unused" not in response.text


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "3", 1_000_001])
def test_reservation_quantity_is_a_bounded_integer(quantity):
    with pytest.raises(ValidationError):
        ReservationCreate(product_id=uuid4(), quantity=quantity)


@pytest.mark.parametrize("delta", [0, True, 0.5, "3", 1_000_001, -1_000_001])
def test_adjustment_is_a_nonzero_bounded_integer(delta):
    with pytest.raises(ValidationError):
        AdjustmentCreate(delta=delta, reason="Synthetic correction")


def test_sku_normalization_and_unknown_fields():
    assert ProductCreate(sku=" demo-01 ", name=" Synthetic item ").sku == "DEMO-01"
    with pytest.raises(ValidationError):
        ProductCreate(sku="DEMO", name="Item", unexpected="field")
    with pytest.raises(ValidationError):
        ProductCreate(sku="💥", name="Item")
    with pytest.raises(ValidationError):
        ProductCreate(sku=123, name="Item")


def test_settings_require_postgresql_and_hide_secrets():
    with pytest.raises(ValidationError, match="SQLite is not supported"):
        DatabaseSettings(database_url="sqlite:///demo.db", _env_file=None)
    with pytest.raises(ValidationError, match="at least 24"):
        Settings(database_url=URL, api_key="short", _env_file=None)
    with pytest.raises(ValidationError, match="exceed lock timeout"):
        Settings(
            database_url=URL,
            api_key=KEY,
            lock_timeout_ms=1000,
            statement_timeout_ms=1000,
            _env_file=None,
        )
    assert KEY not in repr(Settings(database_url=URL, api_key=KEY, _env_file=None))

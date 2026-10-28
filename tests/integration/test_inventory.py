from uuid import uuid4

import pytest
from sqlalchemy import text

from inventory_api.db import SCHEMA_REVISION
from inventory_api.models import MAX_STOCK


def test_create_read_list_and_unique_normalized_sku(client, product):
    item = product(10, sku=" demo-01 ")
    assert item["sku"] == "DEMO-01"
    assert (item["on_hand"], item["reserved"], item["available"]) == (10, 0, 10)
    assert client.get(f"/v1/products/{item['id']}").json() == item
    duplicate = client.post("/v1/products", json={"sku": "demo-01", "name": "Other item"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_sku"
    product(0)
    page = client.get("/v1/products?limit=1&offset=1").json()
    assert len(page["items"]) == 1
    assert (page["limit"], page["offset"]) == (1, 1)
    for query in ("limit=101", "limit=0", "offset=-1", "offset=10001"):
        assert client.get(f"/v1/products?{query}").status_code == 422


def test_reserve_replay_and_fulfill_exactly_once(client, product, reserve):
    item = product(10)
    key = uuid4()
    created = reserve(item, 6, key)
    assert created.status_code == 201
    assert created.headers["Idempotent-Replayed"] == "false"
    replay = reserve(item, 6, key)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == created.json()
    reservation_id = created.json()["id"]
    assert client.get(f"/v1/reservations/{reservation_id}").json() == created.json()
    state = client.get(f"/v1/products/{item['id']}").json()
    assert (state["on_hand"], state["reserved"], state["available"]) == (10, 6, 4)
    assert reserve(item, 5).status_code == 409
    fulfilled = client.post(f"/v1/reservations/{reservation_id}/fulfill")
    assert fulfilled.status_code == 200
    assert fulfilled.json()["status"] == "fulfilled"
    assert client.post(f"/v1/reservations/{reservation_id}/fulfill").json() == fulfilled.json()
    assert reserve(item, 6, key).json() == fulfilled.json(), "replay returns current resource state"
    assert client.post(f"/v1/reservations/{reservation_id}/cancel").status_code == 409
    movements = client.get(f"/v1/products/{item['id']}/movements").json()["items"]
    assert [m["delta"] for m in movements] == [10, -6]
    assert [m["on_hand_after"] for m in movements] == [10, 4]
    assert movements[-1]["reservation_id"] == reservation_id


def test_cancel_releases_stock_and_is_repeatable(client, product, reserve):
    item = product(5)
    reservation = reserve(item, 5).json()
    path = f"/v1/reservations/{reservation['id']}"
    result = client.post(path + "/cancel")
    assert result.status_code == 200
    assert result.json()["status"] == "cancelled"
    assert client.post(path + "/cancel").json() == result.json()
    assert client.post(path + "/fulfill").status_code == 409
    assert client.get(f"/v1/products/{item['id']}").json()["available"] == 5
    assert len(client.get(f"/v1/products/{item['id']}/movements").json()["items"]) == 1


def test_adjustments_respect_holds_limits_and_idempotency(client, product, reserve, adjust):
    item = product(10)
    assert reserve(item, 7).status_code == 201
    assert adjust(item, -4).status_code == 409
    key = uuid4()
    movement = adjust(item, -3, key)
    assert movement.status_code == 201
    assert movement.json()["on_hand_after"] == 7
    assert adjust(item, -3, key).json() == movement.json()
    assert adjust(item, -2, key).status_code == 409
    assert adjust(item, -3, key, "Different reason").status_code == 409
    assert adjust(item, MAX_STOCK).json()["error"]["code"] == "stock_limit"
    another = product(0)
    assert adjust(another, 2, key).status_code == 409
    assert client.get(f"/v1/products/{another['id']}").json()["on_hand"] == 0
    page = client.get(f"/v1/products/{item['id']}/movements?limit=1&offset=1").json()
    assert len(page["items"]) == 1 and page["items"][0]["delta"] == -3


def test_reservation_key_cannot_change_product_or_quantity(product, reserve):
    one, two = product(5), product(5)
    key = uuid4()
    assert reserve(one, 2, key).status_code == 201
    for item, quantity in [(one, 3), (two, 2)]:
        response = reserve(item, quantity, key)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "idempotency_conflict"


def test_failed_reservation_does_not_consume_key(product, reserve, adjust):
    item, key = product(0), uuid4()
    assert reserve(item, 2, key).status_code == 409
    assert adjust(item, 2).status_code == 201
    assert reserve(item, 2, key).status_code == 201


@pytest.mark.parametrize(
    "path",
    [
        "/v1/products/{id}",
        "/v1/products/{id}/movements",
        "/v1/reservations/{id}",
    ],
)
def test_missing_reads_have_structured_404s(client, path):
    response = client.get(path.format(id=uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["code"].endswith("_not_found")


def test_missing_writes_and_invalid_requests(client, product, reserve, adjust):
    missing = {"id": str(uuid4())}
    assert reserve(missing).status_code == 404
    assert adjust(missing, 1).status_code == 404
    for action in ("fulfill", "cancel"):
        assert client.post(f"/v1/reservations/{uuid4()}/{action}").status_code == 404
    item = product(2)
    for header in ({}, {"Idempotency-Key": "not-a-uuid"}):
        response = client.post(
            "/v1/reservations", json={"product_id": item["id"], "quantity": 1}, headers=header
        )
        assert response.status_code == 422
    assert client.get("/v1/products/not-a-uuid").status_code == 422


def test_readiness_checks_expected_migration(client, engine):
    assert client.get("/health/ready").json() == {"status": "ready"}
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'unexpected_revision'"))
    try:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "schema_not_ready"
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": SCHEMA_REVISION},
            )

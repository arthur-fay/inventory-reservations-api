import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, text, update

from inventory_api.main import create_app
from inventory_api.models import Product, Reservation, StockMovement


def race(function, count):
    barrier = Barrier(count)

    def run(index):
        barrier.wait(timeout=5)
        return function(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def test_competing_reservations_do_not_oversell(client, product, reserve):
    item = product(7)
    responses = race(lambda _: reserve(item, 1), 16)
    assert sorted(r.status_code for r in responses) == [201] * 7 + [409] * 9
    state = client.get(f"/v1/products/{item['id']}").json()
    assert (state["on_hand"], state["reserved"], state["available"]) == (7, 7, 0)


def test_parallel_retries_create_one_reservation(client, product, reserve):
    item, key = product(10), uuid4()
    responses = race(lambda _: reserve(item, 3, key), 12)
    assert sorted(r.status_code for r in responses) == [200] * 11 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert client.get(f"/v1/products/{item['id']}").json()["reserved"] == 3


def test_parallel_adjustment_retries_change_stock_once(client, product, adjust):
    item, key = product(0), uuid4()
    responses = race(lambda _: adjust(item, 3, key), 12)
    assert sorted(r.status_code for r in responses) == [200] * 11 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert client.get(f"/v1/products/{item['id']}").json()["on_hand"] == 3


def test_fulfill_cancel_race_has_one_terminal_outcome(client, product, reserve):
    item = product(5)
    reservation = reserve(item, 5).json()
    path = f"/v1/reservations/{reservation['id']}"
    responses = race(lambda i: client.post(path + ("/fulfill" if i else "/cancel")), 2)
    assert sorted(r.status_code for r in responses) == [200, 409]
    final = client.get(path).json()
    state = client.get(f"/v1/products/{item['id']}").json()
    assert final["status"] in {"fulfilled", "cancelled"}
    assert state["reserved"] == 0
    assert state["on_hand"] == (0 if final["status"] == "fulfilled" else 5)


def test_correction_cannot_race_past_a_reservation(client, product, reserve, adjust):
    item = product(5)
    responses = race(lambda i: reserve(item, 5) if i else adjust(item, -5), 2)
    assert sorted(r.status_code for r in responses) == [201, 409]
    state = client.get(f"/v1/products/{item['id']}").json()
    assert (state["on_hand"], state["reserved"]) in {(0, 0), (5, 5)}


def wait_for_database_lock(engine):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.scalar(
                text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                  WHERE application_name = 'inventory-reservations-api'
                  AND state = 'active' AND wait_event_type = 'Lock')
            """)
            )
        if waiting:
            return
        time.sleep(0.01)
    pytest.fail("The competing PostgreSQL transaction never reached its lock wait")


@pytest.mark.parametrize("kind", ["reservation", "adjustment"])
def test_cross_product_key_conflict_rolls_back_the_losing_stock_update(
    client, engine, product, reserve, adjust, kind
):
    one, two = product(5), product(5)
    key = uuid4()
    # Keep a successful operation uncommitted on product one. A request for product
    # two cannot see that key, but must wait for its UNIQUE index entry to resolve.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as connection:
            if kind == "reservation":
                connection.execute(
                    update(Product).where(Product.id == UUID(one["id"])).values(reserved=2)
                )
                connection.execute(
                    insert(Reservation).values(
                        id=uuid4(),
                        product_id=UUID(one["id"]),
                        quantity=2,
                        status="active",
                        idempotency_key=key,
                    )
                )
                future = pool.submit(reserve, two, 2, key)
            else:
                connection.execute(
                    update(Product).where(Product.id == UUID(one["id"])).values(on_hand=7)
                )
                connection.execute(
                    insert(StockMovement).values(
                        product_id=UUID(one["id"]),
                        delta=2,
                        on_hand_after=7,
                        kind="adjustment",
                        reason="Synthetic correction",
                        idempotency_key=key,
                    )
                )
                future = pool.submit(adjust, two, 2, key)
            wait_for_database_lock(engine)
        response = future.result(timeout=10)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"
    losing_state = client.get(f"/v1/products/{two['id']}").json()
    assert (losing_state["on_hand"], losing_state["reserved"]) == (5, 0)


def test_lock_timeout_is_bounded_and_retryable(client, engine, settings, product, reserve):
    item, key = product(5), uuid4()
    bounded = settings.model_copy(update={"lock_timeout_ms": 100})
    with TestClient(create_app(bounded), headers=dict(client.headers)) as busy_client:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM products WHERE id = :id FOR UPDATE"), {"id": item["id"]}
            )
            response = busy_client.post(
                "/v1/reservations",
                json={"product_id": item["id"], "quantity": 2},
                headers={"Idempotency-Key": str(key)},
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "database_busy"
            assert response.headers["Retry-After"] == "1"
    assert client.get(f"/v1/products/{item['id']}").json()["reserved"] == 0
    assert reserve(item, 2, key).status_code == 201

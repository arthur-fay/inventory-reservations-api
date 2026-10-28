"""Exercise a running local API with synthetic data; no third-party libraries required."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

DEFAULT_KEY = "local-demo-key-not-for-public-deployment"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Save the final synthetic state for restart checks"
    )
    parser.add_argument(
        "--verify", type=Path, help="Verify a previously saved state without writing"
    )
    args = parser.parse_args()
    base = os.environ.get("INVENTORY_DEMO_URL", "http://127.0.0.1:8000").rstrip("/")
    parts = urlsplit(base)
    if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("This demonstration script only targets a local HTTP API")
    key = os.environ.get("INVENTORY_API_KEY", DEFAULT_KEY)

    def request(method, path, body=None, *, operation_key=None, expected=200):
        headers = {"X-API-Key": key, "Content-Type": "application/json"}
        if operation_key:
            headers["Idempotency-Key"] = str(operation_key)
        req = Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            data = json.load(response)
            assert response.status == expected, (response.status, data)
            return data

    if args.verify:
        saved = json.loads(args.verify.read_text())
        state = request("GET", f"/v1/products/{saved['product_id']}")
        assert (state["on_hand"], state["reserved"], state["available"]) == (7, 0, 7)
        print("Restart check passed: product and stock persisted in PostgreSQL")
        return

    item = request(
        "POST",
        "/v1/products",
        {
            "sku": "DEMO-" + uuid4().hex[:10].upper(),
            "name": "Synthetic cable pack",
            "initial_stock": 10,
        },
        expected=201,
    )
    body = {"product_id": item["id"], "quantity": 6}
    operation_key = uuid4()
    reservation = request(
        "POST", "/v1/reservations", body, operation_key=operation_key, expected=201
    )
    assert (
        request("POST", "/v1/reservations", body, operation_key=operation_key)["id"]
        == reservation["id"]
    )
    rejected = request(
        "POST", "/v1/reservations", body | {"quantity": 5}, operation_key=uuid4(), expected=409
    )
    assert rejected["error"]["code"] == "insufficient_stock"
    path = f"/v1/reservations/{reservation['id']}/fulfill"
    assert request("POST", path)["status"] == "fulfilled"
    assert request("POST", path)["status"] == "fulfilled"
    request(
        "POST",
        f"/v1/products/{item['id']}/adjustments",
        {"delta": 3, "reason": "Synthetic restock"},
        operation_key=uuid4(),
        expected=201,
    )
    state = request("GET", f"/v1/products/{item['id']}")
    assert (state["on_hand"], state["reserved"], state["available"]) == (7, 0, 7)
    ledger = request("GET", f"/v1/products/{item['id']}/movements")["items"]
    assert [row["delta"] for row in ledger] == [10, -6, 3]
    result = {
        "product_id": item["id"],
        "on_hand": 7,
        "reserved": 0,
        "available": 7,
        "ledger_deltas": [10, -6, 3],
        "oversell_rejected": True,
        "retries_applied_once": True,
    }
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()

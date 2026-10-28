# API guide

Base URL after starting Compose: `http://127.0.0.1:8000`. Send `X-API-Key` on every `/v1` request. JSON request bodies reject unknown fields. Quantities are strict integers; booleans, floating-point numbers and numeric strings are rejected. SKUs are trimmed and normalized to uppercase.

## Operations

| Method and path | Purpose | Successful status |
| --- | --- | --- |
| `POST /v1/products` | Create a product and optional opening stock | 201; duplicate SKU is 409 |
| `GET /v1/products` | List products, `limit` 1–100 and `offset` 0–10000 | 200 |
| `GET /v1/products/{id}` | Read physical, reserved and available stock | 200 |
| `POST /v1/products/{id}/adjustments` | Nonzero correction/restock with a reason | 201; identical retry 200 |
| `GET /v1/products/{id}/movements` | Read the stock ledger, ascending sequence ID | 200 |
| `POST /v1/reservations` | Hold available stock for one product | 201; identical retry 200 |
| `GET /v1/reservations/{id}` | Read a reservation's current state | 200 |
| `POST /v1/reservations/{id}/fulfill` | Consume the hold and physical stock | 200, including same-action retries |
| `POST /v1/reservations/{id}/cancel` | Release an active hold | 200, including same-action retries |
| `GET /health/live` | Process liveness; no database query or key required | 200 |
| `GET /health/ready` | PostgreSQL connection and expected schema revision | 200 or 503; no key required |

Reservation and adjustment creation require a UUID `Idempotency-Key` header. A key is scoped to its operation kind, across all products. Reusing it with a changed normalized payload returns `409`; rejected attempts do not consume the key. Successful responses include `Idempotent-Replayed: false` (201) or `true` (200). A replay returns the stored resource, including any later reservation status or the adjustment's historical `on_hand_after` balance.

## Example requests

Create a synthetic product:

```bash
curl -sS http://127.0.0.1:8000/v1/products \
  -H 'X-API-Key: local-demo-key-not-for-public-deployment' \
  -H 'Content-Type: application/json' \
  -d '{"sku":"DEMO-CABLE","name":"Synthetic cable pack","initial_stock":10}'
```

Copy its returned UUID as `PRODUCT_ID`, then reserve six units:

```bash
curl -sS http://127.0.0.1:8000/v1/reservations \
  -H 'X-API-Key: local-demo-key-not-for-public-deployment' \
  -H 'Idempotency-Key: 11111111-1111-4111-8111-111111111111' \
  -H 'Content-Type: application/json' \
  -d "{\"product_id\":\"$PRODUCT_ID\",\"quantity\":6}"
```

Repeat the identical request to see `200` and the same reservation ID. Use a fresh UUID key for a new operation. The [demo script](../scripts/demo.py) performs the complete sequence without requiring UUID copying or `jq`.

## Errors

Errors use this shape. Validation errors add field locations/types/messages in `details`, without echoing the supplied inputs:

```json
{
  "error": {
    "code": "insufficient_stock",
    "message": "Not enough available stock",
    "details": []
  }
}
```

| Status | Typical codes | Client action |
| --- | --- | --- |
| 401 | `unauthorized` | Supply the configured operator key. |
| 404 | `product_not_found`, `reservation_not_found` | Check the resource UUID. |
| 409 | `insufficient_stock`, `stock_limit`, `duplicate_sku`, `idempotency_conflict`, `reservation_closed` | Resolve the state/payload conflict; do not blindly retry with a fresh key. |
| 422 | `validation_error` | Correct the fields, missing/invalid UUID key or pagination bounds. |
| 503 | `database_busy`, `database_unavailable`, `schema_not_ready` | A busy database includes `Retry-After: 1`; retry the same payload/key. Fix connectivity/schema issues before retrying other 503s. |

The [committed OpenAPI contract](openapi.json) is generated from the app. `uv run python scripts/export_openapi.py --check` fails when it differs. Run the exporter without `--check` after an intentional API change and review the diff.

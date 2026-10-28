# Inventory Reservations API

**FastAPI · PostgreSQL 17 · SQLAlchemy · Alembic · integration and concurrency tests**

A small backend for a concrete problem: two requests should not reserve the same last unit of stock. Products have physical stock, active reservations and a movement ledger. Reserving holds stock; fulfilling consumes it; cancelling releases the hold. Retried successful operations reuse their stored result instead of changing stock again.

An independent portfolio project by **Arthur Fay**, using **synthetic products and quantities only**. It contains no employer code, operational records, customer data or payment integration. This is a local demonstration, not a publicly hosted or production inventory service.

## Review the engineering

| Question | Evidence |
| --- | --- |
| What makes competing stock changes safe? | [Transactional service](src/inventory_api/service.py), [locking design](docs/DESIGN.md), [real PostgreSQL race tests](tests/integration/test_concurrency.py) |
| Is the schema reproducible? | [Reversible Alembic migration](migrations/versions/0001_inventory.py), [constraint and migration checks](tests/integration/test_database.py) |
| How do clients handle retries and errors? | [API guide](docs/API.md), [committed OpenAPI contract](docs/openapi.json), [HTTP integration tests](tests/integration/test_inventory.py) |
| Can I run it? | [Compose configuration](compose.yaml), [asserted synthetic demo](scripts/demo.py), [CI runs](https://github.com/arthur-fay/inventory-reservations-api/actions/workflows/ci.yml) |

## Run locally

Requires Docker with the Compose v2 plugin and an available local port 8000. The first build downloads container images and locked Python dependencies.

```bash
git clone https://github.com/arthur-fay/inventory-reservations-api.git
cd inventory-reservations-api
docker compose up --build --wait
```

Compose starts PostgreSQL, applies `alembic upgrade head`, then waits for the API's readiness check. Only the API is exposed, at **http://127.0.0.1:8000**; PostgreSQL is on the internal Compose network.

- Interactive API docs: **http://127.0.0.1:8000/docs**
- OpenAPI JSON: **http://127.0.0.1:8000/openapi.json**
- Liveness / database-and-schema readiness: `/health/live` / `/health/ready`

All `/v1` operations require `X-API-Key`. In the local Swagger UI, choose **Authorize** and enter:

```text
local-demo-key-not-for-public-deployment
```

That key and the Compose database passwords are published local-demo values. To choose a different API key, copy `.env.example` to `.env` and change it before starting Compose. The app runs as a non-root user; its container filesystem is read-only. Swagger UI loads upstream browser assets; the JSON contract is available independently of those assets.

## Run the synthetic scenario

```bash
docker compose exec -T api python scripts/demo.py
```

The script creates a new synthetic product with 10 units, reserves 6, repeats the same request, rejects a further reservation of 5, fulfills the first reservation twice, then adds 3 units. It asserts that the retries apply once, and that the ledger is **`[+10, -6, +3]`** with **7 on hand, 0 reserved and 7 available**. Each run uses a new SKU and operation keys, so it can be repeated.

## Run the tests

```bash
docker compose --profile test run --build --rm tests
```

This builds the development test image and starts a separate PostgreSQL database named `inventory_test`. Tests apply real migrations, truncate only that disposable test database between cases, and independently reconcile every product's counters to its ledger and active reservations. They do not substitute SQLite or skip the concurrency tests when PostgreSQL is missing.

The tests include competing reservations for the last units, simultaneous retries, fulfillment versus cancellation, a correction racing a reservation, lock timeout and retry, and a deliberately blocked cross-product unique-key conflict whose losing stock change must roll back. Coverage includes branches and has a **90% minimum** for the application package; coverage is not a claim that every possible database schedule is proven safe.

For Python development (Python 3.12 and uv 0.13.4):

```bash
uv sync --frozen
uv run pytest tests/unit
uv run ruff check .
uv run ruff format --check .
uv run python scripts/export_openapi.py --check
```

The unit/contract tests run without a database. To run the full suite outside Compose, provide a disposable PostgreSQL database whose name ends in `_test`, set `INVENTORY_TEST_DATABASE_URL` to a `postgresql+psycopg://.../inventory_test` URL, then run `uv run pytest --cov=inventory_api`. The integration fixture refuses other database names. Use a database created specifically for these tests.

CI also builds the actual runtime image, starts Compose, runs the demo through HTTP, restarts the API and verifies the saved product/stock, then exercises the container test command above. Python dependencies are locked with hashes in `uv.lock`; the Python and PostgreSQL container tags pin their release series, not immutable image digests.

## Configuration and boundaries

| Variable | Use |
| --- | --- |
| `INVENTORY_DATABASE_URL` | Required outside Compose; `postgresql+psycopg` only. Read by migrations and the app. |
| `INVENTORY_API_KEY` | Required outside Compose; at least 24 characters. Shared operator key, not end-user identity or authorization. |
| `INVENTORY_LOCK_TIMEOUT_MS` | Default 3000; bounds PostgreSQL lock waits. |
| `INVENTORY_STATEMENT_TIMEOUT_MS` | Default 10000; must exceed the lock timeout. |
| `INVENTORY_TEST_DATABASE_URL` | Full integration suite only; an explicitly disposable `*_test` database. |

Reservations are single-product and do not expire automatically. There are no accounts, roles, multi-item order atomicity, payments, shipping, TLS termination, distributed rate limiting, production observability, backups or deployment/scale claims. API keys are for the local demonstration. Counters and ledger totals are maintained transactionally by the service; row constraints alone do not enforce their cross-table reconciliation against arbitrary direct SQL. See [design and tradeoffs](docs/DESIGN.md).

`docker compose down` stops the demo while keeping its named stock-data volume. To deliberately reset the synthetic demo, `docker compose --profile test down -v` removes its volumes as well. No real data should be placed in this project.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN python -m pip install --no-cache-dir uv==0.13.4 \
    && useradd --uid 10001 --create-home app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/demo.py ./scripts/demo.py
RUN uv sync --frozen --no-dev && chown -R 10001:10001 /app

FROM base AS test
COPY tests ./tests
COPY scripts ./scripts
COPY docs/openapi.json ./docs/openapi.json
RUN uv sync --frozen && chown -R 10001:10001 /app
USER 10001
CMD ["pytest", "--cov=inventory_api", "--cov-report=term-missing"]

FROM base AS runtime
USER 10001
EXPOSE 8000
CMD ["uvicorn", "inventory_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

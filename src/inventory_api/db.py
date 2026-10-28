from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from inventory_api.config import Settings

SCHEMA_REVISION = "0001_inventory"


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args={
            "connect_timeout": 5,
            "application_name": "inventory-reservations-api",
            "options": (
                f"-c lock_timeout={settings.lock_timeout_ms} "
                f"-c statement_timeout={settings.statement_timeout_ms}"
            ),
        },
    )

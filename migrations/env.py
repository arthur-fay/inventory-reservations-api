from alembic import context
from sqlalchemy import create_engine, pool

from inventory_api.config import DatabaseSettings
from inventory_api.models import Base

config = context.config


def configure(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=DatabaseSettings().database_url.get_secret_value(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    configure(config.attributes["connection"])
else:
    engine = create_engine(
        DatabaseSettings().database_url.get_secret_value(), poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        configure(connection)
    engine.dispose()

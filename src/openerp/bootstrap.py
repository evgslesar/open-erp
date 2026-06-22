from __future__ import annotations

from sqlalchemy.engine import Engine

from openerp.config import get_settings
from openerp.core.metadata import MetadataRegistry
from openerp.core.migrations import ModuleMigrator
from openerp.core.schema import create_all
from openerp.db import create_db_engine, transaction
from openerp.modules.trade import register as register_trade


def build_registry() -> MetadataRegistry:
    registry = MetadataRegistry()
    register_trade(registry)
    return registry


def init_engine(database_url: str | None = None) -> tuple[Engine, MetadataRegistry]:
    settings = get_settings()
    registry = build_registry()
    engine = create_db_engine(database_url or settings.database_url)
    metadata = create_all(engine, registry)
    engine._openerp_metadata = metadata
    with transaction(engine) as connection:
        ModuleMigrator(registry).apply(connection)
    return engine, registry

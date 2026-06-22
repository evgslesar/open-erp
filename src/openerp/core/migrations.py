from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from openerp.core.metadata import MetadataRegistry, ModuleDef
from openerp.core.schema import utcnow

MigrationCallable = Callable[[Connection], None]


@dataclass(frozen=True)
class ModuleMigration:
    module_name: str
    migration_id: str
    apply: MigrationCallable


class ModuleMigrator:
    def __init__(self, registry: MetadataRegistry, migrations: list[ModuleMigration] | None = None):
        self.registry = registry
        self.migrations = migrations or []

    def apply(self, connection: Connection) -> None:
        for module in self.registry.modules.values():
            self._upsert_module_version(connection, module)
        for migration in self.migrations:
            if not self._is_applied(connection, migration):
                migration.apply(connection)
                self._mark_applied(connection, migration)

    def _upsert_module_version(self, connection: Connection, module: ModuleDef) -> None:
        table = connection.engine._openerp_metadata.tables["sys_module_versions"]
        row = connection.execute(
            select(table.c.module_name).where(table.c.module_name == module.name)
        ).first()
        if row is None:
            connection.execute(
                table.insert().values(
                    module_name=module.name,
                    version=module.version,
                    installed_at=utcnow(),
                )
            )
        else:
            connection.execute(
                table.update()
                .where(table.c.module_name == module.name)
                .values(version=module.version)
            )

    def _is_applied(self, connection: Connection, migration: ModuleMigration) -> bool:
        table = connection.engine._openerp_metadata.tables["sys_module_migrations"]
        row = connection.execute(
            select(table.c.id).where(
                and_(
                    table.c.module_name == migration.module_name,
                    table.c.migration_id == migration.migration_id,
                )
            )
        ).first()
        return row is not None

    def _mark_applied(self, connection: Connection, migration: ModuleMigration) -> None:
        table = connection.engine._openerp_metadata.tables["sys_module_migrations"]
        connection.execute(
            table.insert().values(
                module_name=migration.module_name,
                migration_id=migration.migration_id,
                applied_at=utcnow(),
            )
        )

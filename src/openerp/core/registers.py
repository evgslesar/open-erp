from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.decimal import decimal_to_db, to_decimal
from openerp.core.metadata import MetadataRegistry
from openerp.core.naming import (
    information_register_table,
    register_movements_table,
    register_totals_table,
)
from openerp.core.schema import utcnow


def month_start(value: date) -> date:
    return date(value.year, value.month, 1)


class RegisterService:
    def __init__(self, connection: Connection, registry: MetadataRegistry, context: RequestContext):
        self.connection = connection
        self.registry = registry
        self.context = context
        self.metadata = connection.engine._openerp_metadata

    def delete_registrator_movements(self, registrator_type: str, registrator_id: int) -> None:
        for register in self.registry.accumulation_registers():
            table = self.metadata.tables[register_movements_table(register.name)]
            self.connection.execute(
                delete(table).where(
                    and_(
                        table.c.registrator_type == registrator_type,
                        table.c.registrator_id == registrator_id,
                        table.c.organization_id == self.context.organization_id,
                    )
                )
            )

    def add_movement(
        self,
        register_name: str,
        period: date,
        registrator_type: str,
        registrator_id: int,
        line_no: int,
        dimensions: dict[str, Any],
        resources: dict[str, Decimal | str | int | float],
    ) -> None:
        register = self.registry.accumulation_register(register_name)
        table = self.metadata.tables[register_movements_table(register_name)]
        payload: dict[str, Any] = {
            "period": period,
            "organization_id": self.context.organization_id,
            "registrator_type": registrator_type,
            "registrator_id": registrator_id,
            "line_no": line_no,
            "created_at": utcnow(),
            **dimensions,
        }
        for resource in register.resources:
            payload[resource.name] = decimal_to_db(resources.get(resource.name, Decimal("0")))
        self.connection.execute(table.insert().values(**payload))

    def balance(
        self,
        register_name: str,
        on_date: date,
        dimensions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        register = self.registry.accumulation_register(register_name)
        dimensions = dimensions or [item.name for item in register.dimensions]
        filters = filters or {}
        period_start = month_start(on_date)
        totals = self.metadata.tables[register_totals_table(register_name)]
        movements = self.metadata.tables[register_movements_table(register_name)]
        grouped: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
            lambda: {resource.name: Decimal("0") for resource in register.resources}
        )

        total_conditions = [totals.c.organization_id == self.context.organization_id]
        total_conditions.append(totals.c.period_start == period_start)
        move_conditions = [movements.c.organization_id == self.context.organization_id]
        move_conditions.append(movements.c.period >= period_start)
        move_conditions.append(movements.c.period <= on_date)
        for key, value in filters.items():
            total_conditions.append(totals.c[key] == value)
            move_conditions.append(movements.c[key] == value)

        for row in self.connection.execute(select(totals).where(and_(*total_conditions))):
            mapping = row._mapping
            key = tuple(mapping[name] for name in dimensions)
            for resource in register.resources:
                grouped[key][resource.name] += to_decimal(mapping[resource.name])

        for row in self.connection.execute(select(movements).where(and_(*move_conditions))):
            mapping = row._mapping
            key = tuple(mapping[name] for name in dimensions)
            for resource in register.resources:
                grouped[key][resource.name] += to_decimal(mapping[resource.name])

        result = []
        for key, resources in grouped.items():
            row = {dimension: key[index] for index, dimension in enumerate(dimensions)}
            row.update({name: value for name, value in resources.items()})
            result.append(row)
        return result

    def turnover(
        self,
        register_name: str,
        start_date: date,
        end_date: date,
        dimensions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        register = self.registry.accumulation_register(register_name)
        table = self.metadata.tables[register_movements_table(register_name)]
        dimensions = dimensions or [item.name for item in register.dimensions]
        filters = filters or {}
        grouped: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
            lambda: {resource.name: Decimal("0") for resource in register.resources}
        )
        conditions = [
            table.c.organization_id == self.context.organization_id,
            table.c.period >= start_date,
            table.c.period <= end_date,
        ]
        for key, value in filters.items():
            conditions.append(table.c[key] == value)
        for row in self.connection.execute(select(table).where(and_(*conditions))):
            mapping = row._mapping
            key = tuple(mapping[name] for name in dimensions)
            for resource in register.resources:
                grouped[key][resource.name] += to_decimal(mapping[resource.name])
        return [
            {
                **{dimension: key[index] for index, dimension in enumerate(dimensions)},
                **resources,
            }
            for key, resources in grouped.items()
        ]

    def slice_last(
        self,
        register_name: str,
        on_date: date,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        table = self.metadata.tables[information_register_table(register_name)]
        conditions = [
            table.c.organization_id == self.context.organization_id,
            table.c.period <= on_date,
        ]
        for key, value in (filters or {}).items():
            conditions.append(table.c[key] == value)
        latest = (
            select(func.max(table.c.id).label("id"))
            .where(and_(*conditions))
            .group_by(*(table.c[key] for key in (filters or {}).keys()))
            .subquery()
        )
        query = select(table).join(latest, table.c.id == latest.c.id)
        return [dict(row._mapping) for row in self.connection.execute(query)]

    def rebuild_totals(self, register_name: str) -> None:
        register = self.registry.accumulation_register(register_name)
        movements = self.metadata.tables[register_movements_table(register_name)]
        totals = self.metadata.tables[register_totals_table(register_name)]
        self.connection.execute(delete(totals))

        grouped: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
            lambda: {resource.name: Decimal("0") for resource in register.resources}
        )
        raw_rows = [dict(row._mapping) for row in self.connection.execute(select(movements))]
        period_starts = sorted({month_start(row["period"]) for row in raw_rows})

        for period_start in period_starts:
            period_rows = [row for row in raw_rows if row["period"] < period_start]
            period_grouped: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
                lambda: {resource.name: Decimal("0") for resource in register.resources}
            )
            for mapping in period_rows:
                key = (
                    period_start,
                    mapping["organization_id"],
                    *(mapping[dimension.name] for dimension in register.dimensions),
                )
                for resource in register.resources:
                    period_grouped[key][resource.name] += to_decimal(mapping[resource.name])
            grouped.update(period_grouped)

        for key, resources in grouped.items():
            payload = {
                "period_start": key[0],
                "organization_id": key[1],
                "updated_at": utcnow(),
            }
            for index, dimension in enumerate(register.dimensions, start=2):
                payload[dimension.name] = key[index]
            for resource in register.resources:
                payload[resource.name] = decimal_to_db(resources[resource.name])
            self.connection.execute(totals.insert().values(**payload))

    def assert_no_negative_balances(self, register_name: str, on_date: date) -> None:
        register = self.registry.accumulation_register(register_name)
        if register.allow_negative:
            return
        for row in self.balance(register_name, on_date):
            for resource in register.resources:
                if row[resource.name] < 0:
                    raise ValueError(f"Negative balance in register {register_name}: {row}")

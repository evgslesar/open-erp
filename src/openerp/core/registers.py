from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
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


class NegativeStockBalanceError(ValueError):
    def __init__(self, register_name: str, row: dict[str, Any]):
        self.register_name = register_name
        self.row = row
        super().__init__(f"Negative balance in register {register_name}: {row}")


class RegisterService:
    def __init__(self, connection: Connection, registry: MetadataRegistry, context: RequestContext):
        self.connection = connection
        self.registry = registry
        self.context = context
        self.metadata = connection.engine._openerp_metadata

    def delete_registrator_movements(self, registrator_type: str, registrator_id: int) -> None:
        for register in self.registry.accumulation_registers():
            table = self.metadata.tables[register_movements_table(register.name)]
            conditions = and_(
                table.c.registrator_type == registrator_type,
                table.c.registrator_id == registrator_id,
                table.c.organization_id == self.context.organization_id,
            )
            existing_rows = [
                dict(row._mapping)
                for row in self.connection.execute(select(table).where(conditions))
            ]
            for row in existing_rows:
                dimensions = {
                    dimension.name: row[dimension.name] for dimension in register.dimensions
                }
                resources = {
                    resource.name: -to_decimal(row[resource.name])
                    for resource in register.resources
                }
                self._apply_movement_delta(register.name, row["period"], dimensions, resources)
            self.connection.execute(delete(table).where(conditions))

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
        resource_values: dict[str, Decimal] = {}
        for resource in register.resources:
            value = to_decimal(resources.get(resource.name, Decimal("0")))
            resource_values[resource.name] = value
            payload[resource.name] = decimal_to_db(value)
        self.connection.execute(table.insert().values(**payload))
        self._apply_movement_delta(register_name, period, dimensions, resource_values)

    def _apply_movement_delta(
        self,
        register_name: str,
        period: date,
        dimensions: dict[str, Any],
        resource_deltas: dict[str, Decimal],
    ) -> None:
        register = self.registry.accumulation_register(register_name)
        totals = self.metadata.tables[register_totals_table(register_name)]
        period_start = month_start(period)
        conditions = [
            totals.c.period_start == period_start,
            totals.c.organization_id == self.context.organization_id,
        ]
        for key, value in dimensions.items():
            conditions.append(totals.c[key] == value)
        existing = self.connection.execute(select(totals).where(and_(*conditions))).first()
        if existing is None:
            payload: dict[str, Any] = {
                "period_start": period_start,
                "organization_id": self.context.organization_id,
                "updated_at": utcnow(),
            }
            payload.update(dimensions)
            for resource in register.resources:
                payload[resource.name] = decimal_to_db(
                    resource_deltas.get(resource.name, Decimal("0"))
                )
            self.connection.execute(totals.insert().values(**payload))
            return
        new_values: dict[str, Any] = {"updated_at": utcnow()}
        resulting: dict[str, Decimal] = {}
        for resource in register.resources:
            delta = resource_deltas.get(resource.name, Decimal("0"))
            current = to_decimal(existing._mapping[resource.name])
            resulting[resource.name] = current + delta
            new_values[resource.name] = decimal_to_db(resulting[resource.name])
        if all(value == 0 for value in resulting.values()):
            self.connection.execute(delete(totals).where(and_(*conditions)))
            return
        self.connection.execute(totals.update().where(and_(*conditions)).values(**new_values))

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

        total_conditions = [
            totals.c.organization_id == self.context.organization_id,
            totals.c.period_start < period_start,
        ]
        move_conditions = [
            movements.c.organization_id == self.context.organization_id,
            movements.c.period >= period_start,
            movements.c.period <= on_date,
        ]
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

        return self._build_balance_rows(dimensions, grouped)

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
        register = self.registry.information_register(register_name)
        table = self.metadata.tables[information_register_table(register_name)]
        conditions = [
            table.c.organization_id == self.context.organization_id,
            table.c.period <= on_date,
        ]
        for key, value in (filters or {}).items():
            conditions.append(table.c[key] == value)
        dimension_columns = [table.c[dim.name] for dim in register.dimensions]
        latest = (
            select(func.max(table.c.id).label("id"))
            .where(and_(*conditions))
            .group_by(*dimension_columns)
            .subquery()
        )
        query = select(table).join(latest, table.c.id == latest.c.id)
        return [dict(row._mapping) for row in self.connection.execute(query)]

    def movements(
        self,
        register_name: str,
        start_date: date | None = None,
        end_date: date | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        table = self.metadata.tables[register_movements_table(register_name)]
        conditions = [table.c.organization_id == self.context.organization_id]
        if start_date is not None:
            conditions.append(table.c.period >= start_date)
        if end_date is not None:
            conditions.append(table.c.period <= end_date)
        for key, value in (filters or {}).items():
            conditions.append(table.c[key] == value)
        query = select(table).where(and_(*conditions)).order_by(table.c.period, table.c.id)
        return [dict(row._mapping) for row in self.connection.execute(query)]

    def balance_and_turnover(
        self,
        register_name: str,
        start_date: date,
        end_date: date,
        dimensions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        register = self.registry.accumulation_register(register_name)
        dimensions = dimensions or [item.name for item in register.dimensions]
        filters = filters or {}
        opening_date = start_date - timedelta(days=1) if start_date > date.min else date.min
        opening = self.balance(register_name, opening_date, dimensions=dimensions, filters=filters)
        turnover_rows = self.turnover(
            register_name, start_date, end_date, dimensions=dimensions, filters=filters
        )
        opening_map: dict[tuple[Any, ...], dict[str, Decimal]] = {
            tuple(row[dim] for dim in dimensions): {
                res.name: to_decimal(row[res.name]) for res in register.resources
            }
            for row in opening
        }
        turnover_map: dict[tuple[Any, ...], dict[str, Decimal]] = {
            tuple(row[dim] for dim in dimensions): {
                res.name: to_decimal(row[res.name]) for res in register.resources
            }
            for row in turnover_rows
        }
        keys = set(opening_map) | set(turnover_map)
        result: list[dict[str, Any]] = []
        for key in keys:
            open_resources = opening_map.get(
                key, {res.name: Decimal("0") for res in register.resources}
            )
            turn_resources = turnover_map.get(
                key, {res.name: Decimal("0") for res in register.resources}
            )
            row = {dim: key[idx] for idx, dim in enumerate(dimensions)}
            for res in register.resources:
                row[f"{res.name}_open"] = open_resources[res.name]
                row[f"{res.name}_turnover"] = turn_resources[res.name]
                row[f"{res.name}_close"] = open_resources[res.name] + turn_resources[res.name]
            result.append(row)
        return result

    def rebuild_totals(self, register_name: str) -> None:
        register = self.registry.accumulation_register(register_name)
        movements = self.metadata.tables[register_movements_table(register_name)]
        totals = self.metadata.tables[register_totals_table(register_name)]
        self.connection.execute(
            delete(totals).where(totals.c.organization_id == self.context.organization_id)
        )

        grouped: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
            lambda: {resource.name: Decimal("0") for resource in register.resources}
        )
        for row in self.connection.execute(
            select(movements).where(movements.c.organization_id == self.context.organization_id)
        ):
            mapping = dict(row._mapping)
            key = (
                month_start(mapping["period"]),
                mapping["organization_id"],
                *(mapping[dimension.name] for dimension in register.dimensions),
            )
            for resource in register.resources:
                grouped[key][resource.name] += to_decimal(mapping[resource.name])

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
                    raise NegativeStockBalanceError(register_name, dict(row))

    def verify_totals(self, register_name: str) -> list[dict[str, Any]]:
        register = self.registry.accumulation_register(register_name)
        movements = self.metadata.tables[register_movements_table(register_name)]
        totals = self.metadata.tables[register_totals_table(register_name)]

        expected: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
            lambda: {resource.name: Decimal("0") for resource in register.resources}
        )
        for row in self.connection.execute(select(movements)):
            mapping = dict(row._mapping)
            key = (
                month_start(mapping["period"]),
                mapping["organization_id"],
                *(mapping[dimension.name] for dimension in register.dimensions),
            )
            for resource in register.resources:
                expected[key][resource.name] += to_decimal(mapping[resource.name])

        actual: dict[tuple[Any, ...], dict[str, Decimal]] = {}
        for row in self.connection.execute(select(totals)):
            mapping = dict(row._mapping)
            key = (
                mapping["period_start"],
                mapping["organization_id"],
                *(mapping[dimension.name] for dimension in register.dimensions),
            )
            actual[key] = {
                resource.name: to_decimal(mapping[resource.name]) for resource in register.resources
            }

        discrepancies: list[dict[str, Any]] = []
        for key, expected_resources in expected.items():
            period_start, organization_id, *dimension_values = key
            actual_resources = actual.get(key, {})
            for resource in register.resources:
                if to_decimal(expected_resources[resource.name]) != to_decimal(
                    actual_resources.get(resource.name, Decimal("0"))
                ):
                    discrepancies.append(
                        {
                            "period_start": period_start,
                            "organization_id": organization_id,
                            "dimensions": {
                                dim: value
                                for dim, value in zip(
                                    [d.name for d in register.dimensions],
                                    dimension_values,
                                    strict=False,
                                )
                            },
                            "resource": resource.name,
                            "expected": expected_resources[resource.name],
                            "actual": actual_resources.get(resource.name, Decimal("0")),
                        }
                    )
        return discrepancies

    def _build_balance_rows(
        self,
        dimensions: list[str],
        grouped: dict[tuple[Any, ...], dict[str, Decimal]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for key, resources in grouped.items():
            row = {dimension: key[index] for index, dimension in enumerate(dimensions)}
            row.update({name: value for name, value in resources.items()})
            result.append(row)
        return result

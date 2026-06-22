from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.decimal import to_decimal
from openerp.core.metadata import MetadataRegistry
from openerp.core.naming import catalog_table
from openerp.core.registers import RegisterService


def _catalog_name_map(
    connection: Connection,
    catalog_name: str,
) -> dict[int, str]:
    table = connection.engine._openerp_metadata.tables[catalog_table(catalog_name)]
    rows = connection.execute(select(table.c.id, table.c.name))
    return {row._mapping["id"]: row._mapping["name"] for row in rows}


def stock_balance_report(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    on_date: date | None = None,
) -> list[dict]:
    service = RegisterService(connection, registry, context)
    rows = service.balance("stock", on_date or date.today())
    products = _catalog_name_map(connection, "product")
    warehouses = _catalog_name_map(connection, "warehouse")
    for row in rows:
        row["product_name"] = products.get(row.get("product_id"), row.get("product_id"))
        row["warehouse_name"] = warehouses.get(row.get("warehouse_id"), row.get("warehouse_id"))
    return rows


def sales_report(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    end_date = date_to or date.today()
    start_date = date_from or date.min
    service = RegisterService(connection, registry, context)
    movements = service.movements(
        "stock",
        start_date=start_date,
        end_date=end_date,
        filters={"registrator_type": "sale"},
    )
    grouped: dict[int, dict[str, Decimal]] = {}
    for move in movements:
        product_id = move["product_id"]
        bucket = grouped.setdefault(product_id, {"quantity": Decimal("0"), "amount_minor": 0})
        bucket["quantity"] += to_decimal(move["quantity"])
        bucket["amount_minor"] += _line_amount(connection, move)

    products = _catalog_name_map(connection, "product")
    result: list[dict[str, Any]] = []
    for product_id, totals in sorted(grouped.items()):
        result.append(
            {
                "product_id": product_id,
                "product_name": products.get(product_id, product_id),
                "quantity": -totals["quantity"],
                "amount_minor": totals["amount_minor"],
            }
        )
    return result


def _line_amount(connection: Connection, movement: dict) -> int:
    metadata = connection.engine._openerp_metadata
    lines_table = metadata.tables["doc_sale_lines"]
    row = connection.execute(
        select(lines_table.c.amount_minor).where(
            lines_table.c.document_id == movement["registrator_id"],
            lines_table.c.line_no == movement["line_no"],
        )
    ).first()
    if row is None:
        return 0
    return int(row._mapping["amount_minor"] or 0)


def settlements_report(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    on_date: date | None = None,
) -> list[dict]:
    service = RegisterService(connection, registry, context)
    rows = service.balance("settlements", on_date or date.today())
    counterparties = _catalog_name_map(connection, "counterparty")
    for row in rows:
        row["counterparty_name"] = counterparties.get(
            row.get("counterparty_id"), row.get("counterparty_id")
        )
    return rows


def cash_report(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    on_date: date | None = None,
) -> list[dict]:
    service = RegisterService(connection, registry, context)
    rows = service.balance("cash", on_date or date.today())
    return rows

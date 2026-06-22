from __future__ import annotations

from collections import defaultdict
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
    organization_id: int,
) -> dict[int, str]:
    table = connection.engine._openerp_metadata.tables[catalog_table(catalog_name)]
    rows = connection.execute(
        select(table.c.id, table.c.name).where(
            table.c.organization_id == organization_id,
            table.c.deletion_mark.is_(False),
        )
    )
    return {row._mapping["id"]: row._mapping["name"] for row in rows}


def stock_balance_report(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    on_date: date | None = None,
) -> list[dict]:
    service = RegisterService(connection, registry, context)
    rows = service.balance("stock", on_date or date.today())
    products = _catalog_name_map(connection, "product", context.organization_id)
    warehouses = _catalog_name_map(connection, "warehouse", context.organization_id)
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

    products = _catalog_name_map(connection, "product", context.organization_id)
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
    counterparties = _catalog_name_map(connection, "counterparty", context.organization_id)
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
    rows = service.balance(
        "cash",
        on_date or date.today(),
        dimensions=["money_account_id", "currency_id"],
    )
    money_accounts = _catalog_name_map(connection, "money_account", context.organization_id)
    for row in rows:
        row["money_account_name"] = money_accounts.get(
            row.get("money_account_id"), row.get("money_account_id")
        )
    return rows


def format_money_minor(amount_minor: int | Decimal, scale: int = 2) -> str:
    value = to_decimal(amount_minor) / (Decimal(10) ** scale)
    text = f"{value:,.2f}"
    return text.replace(",", "\u00a0")


def dashboard_summary(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    on_date: date | None = None,
) -> dict[str, Any]:
    on_date = on_date or date.today()
    cash_rows = cash_report(connection, registry, context, on_date)
    stock_rows = stock_balance_report(connection, registry, context, on_date)
    settlements_rows = settlements_report(connection, registry, context, on_date)
    sales_rows = sales_report(
        connection,
        registry,
        context,
        date_from=on_date.replace(day=1),
        date_to=on_date,
    )

    cash_total_minor = sum(int(to_decimal(row["amount_minor"])) for row in cash_rows)
    receivable_minor = sum(
        int(to_decimal(row["amount_minor"]))
        for row in settlements_rows
        if to_decimal(row["amount_minor"]) > 0
    )
    payable_minor = sum(
        abs(int(to_decimal(row["amount_minor"])))
        for row in settlements_rows
        if to_decimal(row["amount_minor"]) < 0
    )
    sales_month_minor = sum(int(row["amount_minor"]) for row in sales_rows)
    stock_positions = len([row for row in stock_rows if to_decimal(row["quantity"]) > 0])

    warehouse_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in stock_rows:
        quantity = to_decimal(row["quantity"])
        if quantity <= 0:
            continue
        warehouse_totals[str(row.get("warehouse_name", "?"))] += quantity

    top_stock = sorted(
        stock_rows,
        key=lambda row: to_decimal(row["quantity"]),
        reverse=True,
    )[:5]
    top_settlements = sorted(
        settlements_rows,
        key=lambda row: abs(to_decimal(row["amount_minor"])),
        reverse=True,
    )[:5]

    return {
        "on_date": on_date,
        "cash_total_minor": cash_total_minor,
        "cash_accounts": cash_rows,
        "cash_chart": {
            "labels": [row["money_account_name"] for row in cash_rows],
            "values": [float(to_decimal(row["amount_minor"]) / 100) for row in cash_rows],
        },
        "stock_positions": stock_positions,
        "stock_by_warehouse": [
            {"warehouse_name": name, "quantity": quantity}
            for name, quantity in sorted(warehouse_totals.items())
        ],
        "stock_chart": {
            "labels": list(warehouse_totals.keys()),
            "values": [float(quantity) for quantity in warehouse_totals.values()],
        },
        "receivable_minor": receivable_minor,
        "payable_minor": payable_minor,
        "sales_month_minor": sales_month_minor,
        "sales_chart": {
            "labels": [row["product_name"] for row in sales_rows[:5]],
            "values": [float(row["amount_minor"] / 100) for row in sales_rows[:5]],
        },
        "top_stock": top_stock,
        "top_settlements": top_settlements,
    }

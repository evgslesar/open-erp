from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Connection

from openerp.core.decimal import to_decimal
from openerp.core.naming import document_table


def _order_line_quantities(connection: Connection, order_id: int) -> dict[int, Decimal]:
    lines_table = connection.engine._openerp_metadata.tables["doc_order_lines"]
    rows = connection.execute(
        select(lines_table.c.product_id, lines_table.c.quantity).where(
            lines_table.c.document_id == order_id
        )
    ).fetchall()
    totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        mapping = dict(row._mapping)
        totals[int(mapping["product_id"])] += to_decimal(mapping["quantity"])
    return dict(totals)


def _shipped_quantities(connection: Connection, order_id: int) -> dict[int, Decimal]:
    sale_table = connection.engine._openerp_metadata.tables[document_table("sale")]
    lines_table = connection.engine._openerp_metadata.tables["doc_sale_lines"]
    sales = connection.execute(
        select(sale_table.c.id).where(
            sale_table.c.based_on_order_id == order_id,
            sale_table.c.status == "posted",
            sale_table.c.deletion_mark.is_(False),
        )
    ).fetchall()
    shipped: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for sale in sales:
        sale_id = int(sale._mapping["id"])
        rows = connection.execute(
            select(lines_table.c.product_id, lines_table.c.quantity).where(
                lines_table.c.document_id == sale_id
            )
        ).fetchall()
        for row in rows:
            mapping = dict(row._mapping)
            shipped[int(mapping["product_id"])] += to_decimal(mapping["quantity"])
    return dict(shipped)


def order_fulfillment_status(connection: Connection, order: dict) -> str:
    status = str(order.get("status") or "draft")
    if status == "cancelled":
        return "cancelled"
    if status != "posted":
        return "draft"
    order_id = int(order["id"])
    ordered = _order_line_quantities(connection, order_id)
    if not ordered:
        return "reserved"
    shipped = _shipped_quantities(connection, order_id)
    if not shipped:
        return "reserved"
    for product_id, qty in ordered.items():
        if shipped.get(product_id, Decimal("0")) < qty:
            return "partial"
    return "fulfilled"


def enrich_order_rows(connection: Connection, rows: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for row in rows:
        item = dict(row)
        item["fulfillment_status"] = order_fulfillment_status(connection, item)
        enriched.append(item)
    return enriched

from __future__ import annotations

from decimal import Decimal

from openerp.core.decimal import to_decimal
from openerp.core.posting import PostingContext
from openerp.core.registers import RegisterService


def post_receipt(context: PostingContext) -> None:
    registers = RegisterService(context.connection, context.registry, context.context)
    document = context.document
    for line in document["lines"]:
        registers.add_movement(
            "stock",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"],
            dimensions={
                "warehouse_id": document["warehouse_id"],
                "product_id": line["product_id"],
            },
            resources={"quantity": to_decimal(line["quantity"])},
        )


def post_sale(context: PostingContext) -> None:
    registers = RegisterService(context.connection, context.registry, context.context)
    document = context.document
    for line in document["lines"]:
        registers.add_movement(
            "stock",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"],
            dimensions={
                "warehouse_id": document["warehouse_id"],
                "product_id": line["product_id"],
            },
            resources={"quantity": -to_decimal(line["quantity"])},
        )


def line_amount_minor(quantity: Decimal, price_minor: int) -> int:
    return int(quantity * Decimal(price_minor))

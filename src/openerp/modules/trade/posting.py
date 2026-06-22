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
        registers.add_movement(
            "settlements",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"],
            dimensions={
                "counterparty_id": document["counterparty_id"],
                "currency_id": line["currency_id"],
            },
            resources={"amount_minor": -line["amount_minor"]},
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
        registers.add_movement(
            "settlements",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"],
            dimensions={
                "counterparty_id": document["counterparty_id"],
                "currency_id": line["currency_id"],
            },
            resources={"amount_minor": line["amount_minor"]},
        )


def post_transfer(context: PostingContext) -> None:
    registers = RegisterService(context.connection, context.registry, context.context)
    document = context.document
    for line in document["lines"]:
        quantity = to_decimal(line["quantity"])
        registers.add_movement(
            "stock",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"] * 2 - 1,
            dimensions={
                "warehouse_id": document["source_warehouse_id"],
                "product_id": line["product_id"],
            },
            resources={"quantity": -quantity},
        )
        registers.add_movement(
            "stock",
            period=document["date"],
            registrator_type=context.document_name,
            registrator_id=context.document_id,
            line_no=line["line_no"] * 2,
            dimensions={
                "warehouse_id": document["destination_warehouse_id"],
                "product_id": line["product_id"],
            },
            resources={"quantity": quantity},
        )


def post_inventory_adjustment(context: PostingContext) -> None:
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
            resources={"quantity": to_decimal(line["quantity_delta"])},
        )


def post_cash_payment(context: PostingContext) -> None:
    post_payment(context, "cash")


def post_bank_payment(context: PostingContext) -> None:
    post_payment(context, "bank")


def post_payment(context: PostingContext, account_type: str) -> None:
    registers = RegisterService(context.connection, context.registry, context.context)
    document = context.document
    multiplier = 1 if document["direction"] == "incoming" else -1
    amount_minor = document["amount_minor"] * multiplier
    registers.add_movement(
        "cash",
        period=document["date"],
        registrator_type=context.document_name,
        registrator_id=context.document_id,
        line_no=1,
        dimensions={
            "account_type": account_type,
            "cash_flow_category_id": document["cash_flow_category_id"],
            "currency_id": document["currency_id"],
        },
        resources={"amount_minor": amount_minor},
    )
    registers.add_movement(
        "settlements",
        period=document["date"],
        registrator_type=context.document_name,
        registrator_id=context.document_id,
        line_no=1,
        dimensions={
            "counterparty_id": document["counterparty_id"],
            "currency_id": document["currency_id"],
        },
        resources={"amount_minor": -amount_minor},
    )


def line_amount_minor(quantity: Decimal, price_minor: int) -> int:
    return int(quantity * Decimal(price_minor))

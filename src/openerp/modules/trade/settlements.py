from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from openerp.core.naming import document_table

ALLOCATABLE_DOCUMENT_TYPES = ("sale", "receipt")
PAYMENT_DOCUMENT_TYPES = ("cash_payment", "bank_payment")


def document_total(connection: Connection, doc_type: str, doc_id: int) -> int:
    lines_table = connection.engine._openerp_metadata.tables[f"doc_{doc_type}_lines"]
    total = connection.execute(
        select(func.coalesce(func.sum(lines_table.c.amount_minor), 0)).where(
            lines_table.c.document_id == doc_id
        )
    ).scalar_one()
    return int(total or 0)


def allocated_payments_sum(
    connection: Connection,
    organization_id: int,
    doc_type: str,
    doc_id: int,
    *,
    exclude_payment_id: int | None = None,
    exclude_payment_type: str | None = None,
) -> int:
    expected_direction = "incoming" if doc_type == "sale" else "outgoing"
    total = 0
    metadata = connection.engine._openerp_metadata
    for payment_type in PAYMENT_DOCUMENT_TYPES:
        table = metadata.tables[f"doc_{payment_type}"]
        rows = connection.execute(
            select(table.c.id, table.c.amount_minor, table.c.direction).where(
                table.c.organization_id == organization_id,
                table.c.status == "posted",
                table.c.deletion_mark.is_(False),
                table.c.allocation_document_type == doc_type,
                table.c.allocation_document_id == doc_id,
            )
        ).fetchall()
        for row in rows:
            mapping = dict(row._mapping)
            if payment_type == exclude_payment_type and mapping["id"] == exclude_payment_id:
                continue
            if mapping["direction"] != expected_direction:
                continue
            total += int(mapping["amount_minor"] or 0)
    return total


def unpaid_balance(
    connection: Connection,
    organization_id: int,
    doc_type: str,
    doc_id: int,
    *,
    exclude_payment_id: int | None = None,
    exclude_payment_type: str | None = None,
) -> int:
    doc_total = abs(document_total(connection, doc_type, doc_id))
    paid = allocated_payments_sum(
        connection,
        organization_id,
        doc_type,
        doc_id,
        exclude_payment_id=exclude_payment_id,
        exclude_payment_type=exclude_payment_type,
    )
    return max(doc_total - paid, 0)


def unpaid_documents(
    connection: Connection,
    organization_id: int,
    counterparty_id: int,
    doc_types: tuple[str, ...] = ALLOCATABLE_DOCUMENT_TYPES,
) -> list[dict]:
    metadata = connection.engine._openerp_metadata
    result: list[dict] = []
    for doc_type in doc_types:
        doc_table = metadata.tables[document_table(doc_type)]
        docs = connection.execute(
            select(
                doc_table.c.id,
                doc_table.c.number,
                doc_table.c.date,
            ).where(
                doc_table.c.organization_id == organization_id,
                doc_table.c.counterparty_id == counterparty_id,
                doc_table.c.status == "posted",
                doc_table.c.deletion_mark.is_(False),
            ).order_by(doc_table.c.date.desc(), doc_table.c.id.desc())
        ).fetchall()
        for doc in docs:
            mapping = dict(doc._mapping)
            doc_id = int(mapping["id"])
            balance = unpaid_balance(connection, organization_id, doc_type, doc_id)
            if balance <= 0:
                continue
            result.append(
                {
                    "document_type": doc_type,
                    "document_id": doc_id,
                    "document_number": mapping["number"],
                    "document_date": mapping["date"],
                    "document_total_minor": abs(document_total(connection, doc_type, doc_id)),
                    "paid_minor": allocated_payments_sum(
                        connection, organization_id, doc_type, doc_id
                    ),
                    "unpaid_minor": balance,
                }
            )
    result.sort(key=lambda row: (row["document_date"], row["document_type"], row["document_id"]))
    return result

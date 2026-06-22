from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from openerp.core.audit import log_operation
from openerp.core.context import RequestContext
from openerp.core.metadata import DocumentStatus, MetadataRegistry
from openerp.core.repository import Repository
from openerp.core.naming import catalog_table
from openerp.core.decimal import to_decimal
from openerp.core.security import PermissionDenied, require_permission


class ClosedPeriodError(RuntimeError):
    pass


class InsufficientStockError(RuntimeError):
    pass


class InsufficientFundsError(RuntimeError):
    pass


class InvalidPostingError(RuntimeError):
    pass


def _catalog_label(
    connection: Connection,
    context: RequestContext,
    catalog_name: str,
    item_id: int | None,
) -> str:
    if item_id is None:
        return "?"
    table = connection.engine._openerp_metadata.tables[catalog_table(catalog_name)]
    row = connection.execute(
        select(table.c.name).where(
            table.c.id == item_id,
            table.c.organization_id == context.organization_id,
        )
    ).first()
    if row is None:
        return f"#{item_id}"
    return str(row._mapping["name"])


def format_insufficient_stock_message(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    register_name: str,
    row: dict[str, Any],
    document: dict[str, Any] | None = None,
) -> str:
    from decimal import Decimal

    if register_name != "stock":
        return f"Отрицательный остаток в регистре {register_name}: {row}"

    warehouse_id = row.get("warehouse_id")
    product_id = row.get("product_id")
    warehouse = _catalog_label(connection, context, "warehouse", warehouse_id)
    product = _catalog_label(connection, context, "product", product_id)
    resulting = row.get("quantity", Decimal("0"))
    shortage = abs(resulting)
    doc_date = document.get("date") if document else None

    sale_qty = Decimal("0")
    if document and document.get("warehouse_id") == warehouse_id:
        for line in document.get("lines", []):
            if line.get("product_id") == product_id:
                sale_qty += to_decimal(line.get("quantity", 0))

    message = (
        f"Недостаточно товара «{product}» на складе «{warehouse}»"
        f"{f' на дату {doc_date}' if doc_date else ''}: не хватает {shortage} ед."
    )
    if sale_qty > 0:
        available_before = sale_qty + resulting
        message += (
            f" На эту дату на складе было {available_before} ед., "
            f"в документе указано {sale_qty} ед."
        )
    if doc_date and doc_date != date.today():
        message += (
            " Отчёт «Остатки товаров» по умолчанию показывает остатки на сегодня — "
            "укажите в отчёте ту же дату, что в документе."
        )
    else:
        message += (
            " Оформите поступление на этот склад (с датой не позже реализации) "
            "или уменьшите количество."
        )
    return message


def format_insufficient_balance_message(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    register_name: str,
    row: dict[str, Any],
    document: dict[str, Any] | None = None,
) -> str:
    if register_name == "stock":
        return format_insufficient_stock_message(
            connection,
            registry,
            context,
            register_name,
            row,
            document,
        )
    if register_name != "cash":
        return f"Отрицательный остаток в регистре {register_name}: {row}"

    money_account_id = row.get("money_account_id")
    money_account = _catalog_label(connection, context, "money_account", money_account_id)
    resulting = to_decimal(row.get("amount_minor", 0))
    shortage = abs(resulting)
    doc_date = document.get("date") if document else None
    message = (
        f"Недостаточно денежных средств на счете «{money_account}»"
        f"{f' на дату {doc_date}' if doc_date else ''}: не хватает {int(shortage)} коп."
    )
    if document and document.get("money_account_id") == money_account_id:
        amount_minor = int(document.get("amount_minor") or 0)
        available_before = amount_minor + resulting
        message += (
            f" На эту дату было доступно {int(available_before)} коп., "
            f"в документе указано {amount_minor} коп."
        )
    message += " Пополните счет документом прихода или уменьшите сумму расхода."
    return message


class PostingContext:
    def __init__(
        self,
        connection: Connection,
        registry: MetadataRegistry,
        context: RequestContext,
        document_name: str,
        document_id: int,
        document: dict[str, Any],
    ):
        self.connection = connection
        self.registry = registry
        self.context = context
        self.document_name = document_name
        self.document_id = document_id
        self.document = document


class DocumentPostingService:
    def __init__(self, connection: Connection, registry: MetadataRegistry, context: RequestContext):
        self.connection = connection
        self.registry = registry
        self.context = context
        self.repository = Repository(connection, registry, context)

    def post(self, document_name: str, document_id: int) -> None:
        require_permission(self.connection, self.context, f"document:{document_name}", "post")
        document_def = self.registry.document(document_name)
        if document_def.posting_handler is None:
            raise ValueError(f"Document has no posting handler: {document_name}")

        document = self.repository.get_document(document_name, document_id)
        self._ensure_period_open(document["date"])

        from openerp.core.registers import NegativeStockBalanceError, RegisterService

        registers = RegisterService(self.connection, self.registry, self.context)
        registers.delete_registrator_movements(document_name, document_id)

        handler = self.registry.posting_handlers[document_def.posting_handler]
        handler(
            PostingContext(
                self.connection,
                self.registry,
                self.context,
                document_name,
                document_id,
                document,
            )
        )

        for register in self.registry.accumulation_registers():
            if not register.allow_negative:
                try:
                    registers.assert_no_negative_balances(register.name, document["date"])
                except NegativeStockBalanceError as error:
                    message = format_insufficient_balance_message(
                        self.connection,
                        self.registry,
                        self.context,
                        error.register_name,
                        error.row,
                        document,
                    )
                    if error.register_name == "cash":
                        raise InsufficientFundsError(message) from error
                    raise InsufficientStockError(message) from error

        self.repository.update_document_status(
            document_name,
            document_id,
            DocumentStatus.POSTED,
        )

    def unpost(self, document_name: str, document_id: int) -> None:
        from openerp.core.repository import DocumentStateError

        require_permission(self.connection, self.context, f"document:{document_name}", "unpost")
        document = self.repository.get_document(document_name, document_id)
        if str(document["status"]) != DocumentStatus.POSTED.value:
            raise DocumentStateError(
                f"Document {document_name}/{document_id} is not posted; cannot unpost"
            )
        self._ensure_period_open(document["date"])

        from openerp.core.registers import RegisterService

        registers = RegisterService(self.connection, self.registry, self.context)
        registers.delete_registrator_movements(document_name, document_id)
        self.repository.update_document_status(document_name, document_id, DocumentStatus.CANCELLED)

    def repost(self, document_name: str, document_id: int) -> None:
        self.post(document_name, document_id)
        log_operation(
            self.connection,
            self.context,
            "repost",
            object_type=f"document:{document_name}",
            object_id=document_id,
        )

    def _ensure_period_open(self, document_date: date) -> None:
        ensure_period_open(self.connection, self.context, document_date)


def ensure_period_open(
    connection: Connection,
    context: RequestContext,
    document_date: date,
) -> None:
    table = connection.engine._openerp_metadata.tables["sys_closed_periods"]
    row = connection.execute(
        select(table.c.closed_until).where(table.c.organization_id == context.organization_id)
    ).first()
    if row is None:
        return
    closed_until = row._mapping["closed_until"]
    if document_date <= closed_until:
        raise ClosedPeriodError(f"Document date {document_date} is in closed period")


def set_closed_period(
    connection: Connection,
    context: RequestContext,
    closed_until: date,
) -> None:
    if not context.is_admin:
        try:
            require_permission(connection, context, "system:closed_period", "update")
        except PermissionDenied as error:
            raise PermissionDenied("Only privileged users can update closed period") from error

    table = connection.engine._openerp_metadata.tables["sys_closed_periods"]
    row = connection.execute(
        select(table.c.organization_id).where(table.c.organization_id == context.organization_id)
    ).first()
    if row is None:
        connection.execute(
            table.insert().values(
                organization_id=context.organization_id,
                closed_until=closed_until,
                updated_by=context.user_id,
            )
        )
    else:
        connection.execute(
            table.update()
            .where(and_(table.c.organization_id == context.organization_id))
            .values(closed_until=closed_until, updated_by=context.user_id)
        )
    log_operation(connection, context, "set_closed_period", details={"closed_until": closed_until})

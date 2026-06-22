from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from openerp.core.audit import log_operation
from openerp.core.context import RequestContext
from openerp.core.metadata import DocumentStatus, MetadataRegistry
from openerp.core.repository import Repository
from openerp.core.security import PermissionDenied, require_permission


class ClosedPeriodError(RuntimeError):
    pass


class InsufficientStockError(RuntimeError):
    pass


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

        from openerp.core.registers import RegisterService

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
                except ValueError as error:
                    raise InsufficientStockError(str(error)) from error

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

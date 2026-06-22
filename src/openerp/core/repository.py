from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, delete, desc, insert, select, update
from sqlalchemy.engine import Connection

from openerp.core.audit import log_audit, log_operation
from openerp.core.context import RequestContext
from openerp.core.metadata import DocumentStatus, MetadataRegistry
from openerp.core.naming import catalog_table, document_table, table_part_table
from openerp.core.schema import utcnow
from openerp.core.security import require_permission


class DocumentStateError(RuntimeError):
    """Raised when a document operation is blocked by the document's status."""


class Repository:
    def __init__(self, connection: Connection, registry: MetadataRegistry, context: RequestContext):
        self.connection = connection
        self.registry = registry
        self.context = context
        self.metadata = connection.engine._openerp_metadata

    def create_catalog_item(self, catalog_name: str, values: dict[str, Any]) -> int:
        require_permission(self.connection, self.context, f"catalog:{catalog_name}", "create")
        table = self.metadata.tables[catalog_table(catalog_name)]
        payload = {
            **values,
            "organization_id": self.context.organization_id,
            "created_by": self.context.user_id,
            "updated_by": self.context.user_id,
        }
        result = self.connection.execute(insert(table).values(**payload))
        item_id = int(result.inserted_primary_key[0])
        log_audit(
            self.connection,
            self.context,
            f"catalog:{catalog_name}",
            "create",
            item_id,
            after=payload,
        )
        return item_id

    def list_catalog_items(self, catalog_name: str, limit: int = 100) -> list[dict[str, Any]]:
        require_permission(self.connection, self.context, f"catalog:{catalog_name}", "read")
        table = self.metadata.tables[catalog_table(catalog_name)]
        query = (
            select(table)
            .where(
                and_(
                    table.c.organization_id == self.context.organization_id,
                    table.c.deletion_mark.is_(False),
                )
            )
            .order_by(table.c.name)
            .limit(limit)
        )
        return [dict(row._mapping) for row in self.connection.execute(query)]

    def get_catalog_item(self, catalog_name: str, item_id: int) -> dict[str, Any]:
        require_permission(self.connection, self.context, f"catalog:{catalog_name}", "read")
        table = self.metadata.tables[catalog_table(catalog_name)]
        row = self.connection.execute(
            select(table).where(
                and_(
                    table.c.id == item_id,
                    table.c.organization_id == self.context.organization_id,
                    table.c.deletion_mark.is_(False),
                )
            )
        ).one()
        return dict(row._mapping)

    def update_catalog_item(
        self,
        catalog_name: str,
        item_id: int,
        values: dict[str, Any],
    ) -> None:
        require_permission(self.connection, self.context, f"catalog:{catalog_name}", "update")
        table = self.metadata.tables[catalog_table(catalog_name)]
        before = self.get_catalog_item(catalog_name, item_id)
        payload = {
            **values,
            "updated_by": self.context.user_id,
            "updated_at": utcnow(),
            "revision": before["revision"] + 1,
        }
        self.connection.execute(
            update(table)
            .where(
                and_(
                    table.c.id == item_id,
                    table.c.organization_id == self.context.organization_id,
                )
            )
            .values(**payload)
        )
        log_audit(
            self.connection,
            self.context,
            f"catalog:{catalog_name}",
            "update",
            item_id,
            before=before,
            after={**before, **payload},
        )

    def delete_catalog_item(self, catalog_name: str, item_id: int) -> None:
        require_permission(self.connection, self.context, f"catalog:{catalog_name}", "delete")
        table = self.metadata.tables[catalog_table(catalog_name)]
        before = self.get_catalog_item(catalog_name, item_id)
        payload = {
            "deletion_mark": True,
            "updated_by": self.context.user_id,
            "updated_at": utcnow(),
            "revision": before["revision"] + 1,
        }
        self.connection.execute(
            update(table)
            .where(
                and_(
                    table.c.id == item_id,
                    table.c.organization_id == self.context.organization_id,
                )
            )
            .values(**payload)
        )
        log_audit(
            self.connection,
            self.context,
            f"catalog:{catalog_name}",
            "delete",
            item_id,
            before=before,
            after={**before, **payload},
        )

    def next_number(self, document_name: str) -> str:
        table = self.metadata.tables["sys_number_sequences"]
        row = self.connection.execute(
            select(table).where(
                and_(
                    table.c.organization_id == self.context.organization_id,
                    table.c.document_name == document_name,
                )
            )
        ).first()
        if row is None:
            self.connection.execute(
                table.insert().values(
                    organization_id=self.context.organization_id,
                    document_name=document_name,
                    last_number=1,
                )
            )
            value = 1
        else:
            value = int(row._mapping["last_number"]) + 1
            self.connection.execute(
                update(table)
                .where(
                    and_(
                        table.c.organization_id == self.context.organization_id,
                        table.c.document_name == document_name,
                    )
                )
                .values(last_number=value)
            )
        return f"{value:08d}"

    def create_document(
        self,
        document_name: str,
        values: dict[str, Any],
        table_parts: dict[str, list[dict[str, Any]]] | None = None,
    ) -> int:
        require_permission(self.connection, self.context, f"document:{document_name}", "create")
        table = self.metadata.tables[document_table(document_name)]
        payload = {
            **values,
            "organization_id": self.context.organization_id,
            "number": values.get("number") or self.next_number(document_name),
            "status": values.get("status") or DocumentStatus.DRAFT.value,
            "created_by": self.context.user_id,
            "updated_by": self.context.user_id,
        }
        payload.setdefault("date", date.today())
        from openerp.core.posting import ensure_period_open

        ensure_period_open(self.connection, self.context, payload["date"])
        result = self.connection.execute(insert(table).values(**payload))
        document_id = int(result.inserted_primary_key[0])

        for part_name, rows in (table_parts or {}).items():
            part_table = self.metadata.tables[table_part_table(document_name, part_name)]
            for line_no, row in enumerate(rows, start=1):
                self.connection.execute(
                    part_table.insert().values(document_id=document_id, line_no=line_no, **row)
                )

        log_audit(
            self.connection,
            self.context,
            f"document:{document_name}",
            "create",
            document_id,
            after=payload,
        )
        return document_id

    def get_document(self, document_name: str, document_id: int) -> dict[str, Any]:
        require_permission(self.connection, self.context, f"document:{document_name}", "read")
        table = self.metadata.tables[document_table(document_name)]
        row = self.connection.execute(
            select(table).where(
                and_(
                    table.c.id == document_id,
                    table.c.organization_id == self.context.organization_id,
                )
            )
        ).one()
        data = dict(row._mapping)
        document = self.registry.document(document_name)
        for part in document.table_parts:
            part_table = self.metadata.tables[table_part_table(document_name, part.name)]
            rows = self.connection.execute(
                select(part_table)
                .where(part_table.c.document_id == document_id)
                .order_by(part_table.c.line_no)
            )
            data[part.name] = [dict(item._mapping) for item in rows]
        return data

    def update_document_status(
        self,
        document_name: str,
        document_id: int,
        status: DocumentStatus,
    ) -> None:
        table = self.metadata.tables[document_table(document_name)]
        values: dict[str, Any] = {"status": status.value, "updated_at": utcnow()}
        if status == DocumentStatus.POSTED:
            values.update({"posted_at": utcnow(), "posted_by": self.context.user_id})
        self.connection.execute(
            update(table)
            .where(
                and_(
                    table.c.id == document_id,
                    table.c.organization_id == self.context.organization_id,
                )
            )
            .values(**values)
        )
        log_operation(
            self.connection,
            self.context,
            operation=status.value,
            object_type=f"document:{document_name}",
            object_id=document_id,
        )

    def list_documents_keyset(
        self,
        document_name: str,
        limit: int = 50,
        after_date: date | None = None,
        after_id: int | None = None,
    ) -> list[dict[str, Any]]:
        require_permission(self.connection, self.context, f"document:{document_name}", "read")
        table = self.metadata.tables[document_table(document_name)]
        conditions = [
            table.c.organization_id == self.context.organization_id,
            table.c.deletion_mark.is_(False),
        ]
        if after_date is not None and after_id is not None:
            conditions.append(
                (table.c.date < after_date)
                | ((table.c.date == after_date) & (table.c.id < after_id))
            )
        query = (
            select(table)
            .where(and_(*conditions))
            .order_by(desc(table.c.date), desc(table.c.id))
            .limit(limit)
        )
        return [dict(row._mapping) for row in self.connection.execute(query)]

    def update_document(
        self,
        document_name: str,
        document_id: int,
        values: dict[str, Any],
        table_parts: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        require_permission(self.connection, self.context, f"document:{document_name}", "update")
        document_def = self.registry.document(document_name)
        table = self.metadata.tables[document_table(document_name)]
        before = self.get_document(document_name, document_id)
        if str(before["status"]) == DocumentStatus.POSTED.value:
            raise DocumentStateError(
                f"Document {document_name}/{document_id} is posted; unpost before editing"
            )
        from openerp.core.posting import ensure_period_open

        ensure_period_open(self.connection, self.context, before["date"])
        new_date = values.get("date", before["date"])
        if new_date != before["date"]:
            ensure_period_open(self.connection, self.context, new_date)
        payload = {
            **values,
            "updated_by": self.context.user_id,
            "updated_at": utcnow(),
            "revision": before["revision"] + 1,
        }
        self.connection.execute(
            update(table)
            .where(
                and_(
                    table.c.id == document_id,
                    table.c.organization_id == self.context.organization_id,
                )
            )
            .values(**payload)
        )

        for part in document_def.table_parts:
            part_table = self.metadata.tables[table_part_table(document_name, part.name)]
            self.connection.execute(
                delete(part_table).where(part_table.c.document_id == document_id)
            )
            for line_no, row in enumerate((table_parts or {}).get(part.name, []), start=1):
                self.connection.execute(
                    part_table.insert().values(
                        document_id=document_id, line_no=line_no, **row
                    )
                )

        log_audit(
            self.connection,
            self.context,
            f"document:{document_name}",
            "update",
            document_id,
            before=before,
            after={**before, **payload},
        )

    def delete_document(self, document_name: str, document_id: int) -> None:
        require_permission(self.connection, self.context, f"document:{document_name}", "delete")
        table = self.metadata.tables[document_table(document_name)]
        before = self.get_document(document_name, document_id)
        if str(before["status"]) == DocumentStatus.POSTED.value:
            raise DocumentStateError(
                f"Document {document_name}/{document_id} is posted; unpost before deleting"
            )
        from openerp.core.posting import ensure_period_open

        ensure_period_open(self.connection, self.context, before["date"])
        payload = {
            "deletion_mark": True,
            "updated_by": self.context.user_id,
            "updated_at": utcnow(),
            "revision": before["revision"] + 1,
        }
        self.connection.execute(
            update(table)
            .where(
                and_(
                    table.c.id == document_id,
                    table.c.organization_id == self.context.organization_id,
                )
            )
            .values(**payload)
        )
        log_audit(
            self.connection,
            self.context,
            f"document:{document_name}",
            "delete",
            document_id,
            before=before,
            after={**before, **payload},
        )

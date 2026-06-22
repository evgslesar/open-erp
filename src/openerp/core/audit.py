from __future__ import annotations

import json
from typing import Any

from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.schema import utcnow


def log_audit(
    connection: Connection,
    context: RequestContext,
    object_type: str,
    operation: str,
    object_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    table = connection.metadata.tables["sys_audit_log"] if hasattr(connection, "metadata") else None
    if table is None:
        table = _table(connection, "sys_audit_log")
    connection.execute(
        table.insert().values(
            occurred_at=utcnow(),
            user_id=context.user_id,
            organization_id=context.organization_id,
            object_type=object_type,
            object_id=object_id,
            operation=operation,
            before_json=json.dumps(before, default=str) if before is not None else None,
            after_json=json.dumps(after, default=str) if after is not None else None,
        )
    )


def log_operation(
    connection: Connection,
    context: RequestContext,
    operation: str,
    object_type: str | None = None,
    object_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    table = _table(connection, "sys_operation_log")
    connection.execute(
        table.insert().values(
            occurred_at=utcnow(),
            user_id=context.user_id,
            organization_id=context.organization_id,
            operation=operation,
            object_type=object_type,
            object_id=object_id,
            details=json.dumps(details, default=str) if details is not None else None,
        )
    )


def _table(connection: Connection, name: str):
    return connection.engine._openerp_metadata.tables[name]

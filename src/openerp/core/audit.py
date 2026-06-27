from __future__ import annotations

import json
from datetime import date
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


def list_audit_log(
    connection: Connection,
    context: RequestContext,
    date_from: date | None = None,
    date_to: date | None = None,
    operation: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    from sqlalchemy import and_, desc, select

    table = _table(connection, "sys_audit_log")
    conditions = [table.c.organization_id == context.organization_id]
    if date_from is not None:
        conditions.append(table.c.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(table.c.occurred_at <= date_to)
    if operation:
        conditions.append(table.c.operation == operation)
    query = (
        select(table)
        .where(and_(*conditions))
        .order_by(desc(table.c.occurred_at), desc(table.c.id))
        .limit(limit)
    )
    return [dict(row._mapping) for row in connection.execute(query)]


def list_operation_log(
    connection: Connection,
    context: RequestContext,
    date_from: date | None = None,
    date_to: date | None = None,
    operation: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    from sqlalchemy import and_, desc, select

    table = _table(connection, "sys_operation_log")
    conditions = [table.c.organization_id == context.organization_id]
    if date_from is not None:
        conditions.append(table.c.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(table.c.occurred_at <= date_to)
    if operation:
        conditions.append(table.c.operation == operation)
    query = (
        select(table)
        .where(and_(*conditions))
        .order_by(desc(table.c.occurred_at), desc(table.c.id))
        .limit(limit)
    )
    return [dict(row._mapping) for row in connection.execute(query)]

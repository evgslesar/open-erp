from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext


class PermissionDenied(RuntimeError):
    pass


def require_permission(
    connection: Connection,
    context: RequestContext,
    object_name: str,
    operation: str,
) -> None:
    if context.is_admin:
        return

    metadata = connection.engine._openerp_metadata
    permissions = metadata.tables["sys_permissions"]
    user_roles = metadata.tables["sys_user_roles"]

    query = (
        select(permissions.c.id)
        .select_from(permissions.join(user_roles, permissions.c.role_id == user_roles.c.role_id))
        .where(
            and_(
                user_roles.c.user_id == context.user_id,
                permissions.c.object_name == object_name,
                permissions.c.operation == operation,
            )
        )
        .limit(1)
    )
    if connection.execute(query).first() is None:
        raise PermissionDenied(f"Missing permission {operation} on {object_name}")

from __future__ import annotations

import bcrypt
from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext


class PermissionDenied(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def authenticate(
    connection: Connection,
    email: str,
    password: str,
) -> dict:
    users = connection.engine._openerp_metadata.tables["sys_users"]
    row = connection.execute(
        select(users).where(users.c.email == email).limit(1)
    ).first()
    if row is None or not row._mapping["is_active"]:
        raise AuthenticationError("Invalid credentials")
    if not verify_password(password, row._mapping["password_hash"]):
        raise AuthenticationError("Invalid credentials")
    return dict(row._mapping)


def set_user_password(
    connection: Connection,
    email: str,
    password: str,
) -> None:
    users = connection.engine._openerp_metadata.tables["sys_users"]
    row = connection.execute(
        select(users.c.id).where(users.c.email == email).limit(1)
    ).first()
    if row is None:
        raise ValueError(f"User not found: {email}")
    password_hash = hash_password(password)
    connection.execute(
        users.update().where(users.c.email == email).values(password_hash=password_hash)
    )


def load_user_context(
    connection: Connection,
    user_id: int,
) -> RequestContext:
    metadata = connection.engine._openerp_metadata
    users = metadata.tables["sys_users"]
    user_roles = metadata.tables["sys_user_roles"]
    roles = metadata.tables["sys_roles"]
    row = connection.execute(
        select(users).where(users.c.id == user_id).limit(1)
    ).first()
    if row is None or not row._mapping["is_active"]:
        raise AuthenticationError("User is not available")
    admin_role_ids = {
        role._mapping["id"]
        for role in connection.execute(select(roles).where(roles.c.name == "admin")).all()
    }
    user_role_rows = connection.execute(
        select(user_roles.c.role_id).where(user_roles.c.user_id == user_id)
    ).all()
    is_admin = any(role._mapping["role_id"] in admin_role_ids for role in user_role_rows)
    organization_id = int(row._mapping["default_organization_id"] or 1)
    return RequestContext(
        user_id=int(row._mapping["id"]),
        organization_id=organization_id,
        is_admin=is_admin,
        user_name=row._mapping["name"],
        user_email=row._mapping["email"],
    )


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

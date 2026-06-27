from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.metadata import CatalogDef, DocumentDef, FieldType, MetadataRegistry
from openerp.core.naming import catalog_table, document_table


def _like_pattern(query: str) -> str:
    normalized = query.casefold()
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _column_matches(connection: Connection, column, pattern: str):
    if connection.dialect.name == "sqlite":
        return func.unicode_lower(column).like(pattern, escape="\\")
    return column.ilike(pattern, escape="\\")


def _can_read(connection: Connection, context: RequestContext, object_name: str) -> bool:
    if context.is_admin:
        return True
    metadata = connection.engine._openerp_metadata
    permissions = metadata.tables["sys_permissions"]
    user_roles = metadata.tables["sys_user_roles"]
    row = connection.execute(
        select(permissions.c.id)
        .select_from(permissions.join(user_roles, permissions.c.role_id == user_roles.c.role_id))
        .where(
            and_(
                user_roles.c.user_id == context.user_id,
                permissions.c.object_name == object_name,
                permissions.c.operation == "read",
            )
        )
        .limit(1)
    ).first()
    return row is not None


def _searchable_field_names(fields: tuple) -> list[str]:
    return [field.name for field in fields if field.type in (FieldType.STRING, FieldType.TEXT)]


def _text_conditions(connection: Connection, table, column_names: list[str], pattern: str):
    columns = [table.c[name] for name in column_names if name in table.c]
    if not columns:
        return None
    return or_(*[_column_matches(connection, column, pattern) for column in columns])


def _truncate(value: str | None, limit: int = 120) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _catalog_subtitle(row: dict, searchable_fields: list[str]) -> str:
    for name in searchable_fields:
        if name == "name":
            continue
        value = row.get(name)
        if value:
            return str(value)
    return ""


def _document_title(row: dict) -> str:
    number = row.get("number") or ""
    doc_date = row.get("date")
    if doc_date:
        return f"{number} от {doc_date}"
    return str(number)


def _search_catalog(
    connection: Connection,
    context: RequestContext,
    catalog: CatalogDef,
    pattern: str,
    row_limit: int | None = None,
) -> list[dict]:
    object_name = f"catalog:{catalog.name}"
    if not _can_read(connection, context, object_name):
        return []

    table = connection.engine._openerp_metadata.tables[catalog_table(catalog.name)]
    searchable = ["name", *_searchable_field_names(catalog.fields)]
    match = _text_conditions(connection, table, searchable, pattern)
    if match is None:
        return []

    query = (
        select(table)
        .where(
            and_(
                table.c.organization_id == context.organization_id,
                table.c.deletion_mark.is_(False),
                match,
            )
        )
        .order_by(table.c.name)
    )
    if row_limit is not None:
        query = query.limit(row_limit)
    rows = connection.execute(query).all()

    results: list[dict] = []
    extra_fields = [name for name in searchable if name != "name"]
    for row in rows:
        mapping = dict(row._mapping)
        results.append({
            "type": "catalog",
            "id": mapping["id"],
            "title": mapping["name"],
            "subtitle": _catalog_subtitle(mapping, extra_fields),
            "url": f"/catalogs/{catalog.name}/{mapping['id']}/edit",
        })
    return results


def _search_document(
    connection: Connection,
    context: RequestContext,
    document: DocumentDef,
    pattern: str,
    row_limit: int | None = None,
) -> list[dict]:
    object_name = f"document:{document.name}"
    if not _can_read(connection, context, object_name):
        return []

    table = connection.engine._openerp_metadata.tables[document_table(document.name)]
    searchable = ["number", "comment", *_searchable_field_names(document.fields)]
    match = _text_conditions(connection, table, searchable, pattern)
    if match is None:
        return []

    query = (
        select(table)
        .where(
            and_(
                table.c.organization_id == context.organization_id,
                table.c.deletion_mark.is_(False),
                match,
            )
        )
        .order_by(table.c.date.desc(), table.c.id.desc())
    )
    if row_limit is not None:
        query = query.limit(row_limit)
    rows = connection.execute(query).all()

    results: list[dict] = []
    for row in rows:
        mapping = dict(row._mapping)
        subtitle = _truncate(mapping.get("comment"))
        if not subtitle and mapping.get("status"):
            subtitle = str(mapping["status"])
        results.append({
            "type": "document",
            "id": mapping["id"],
            "title": _document_title(mapping),
            "subtitle": subtitle,
            "url": f"/documents/{document.name}/{mapping['id']}",
        })
    return results


def global_search(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    query: str,
    limit: int | None = None,
) -> dict:
    """Search catalogs and documents in the current organization."""
    q = query.strip()
    if not q:
        return {"query": q, "groups": [], "total": 0}

    pattern = _like_pattern(q)
    groups: list[dict] = []
    total = 0

    for catalog in sorted(registry.catalogs(), key=lambda item: item.label.lower()):
        if limit is not None and total >= limit:
            break
        remaining = limit - total if limit is not None else None
        results = _search_catalog(connection, context, catalog, pattern, row_limit=remaining)
        if not results:
            continue
        total += len(results)
        groups.append({
            "group_key": f"catalog:{catalog.name}",
            "group_label": catalog.label,
            "group_kind": "catalog",
            "group_url": f"/catalogs/{catalog.name}",
            "results": results,
        })

    for document in sorted(registry.documents(), key=lambda item: item.label.lower()):
        if limit is not None and total >= limit:
            break
        remaining = limit - total if limit is not None else None
        results = _search_document(connection, context, document, pattern, row_limit=remaining)
        if not results:
            continue
        total += len(results)
        groups.append({
            "group_key": f"document:{document.name}",
            "group_label": document.label,
            "group_kind": "document",
            "group_url": f"/documents/{document.name}",
            "results": results,
        })

    return {"query": q, "groups": groups, "total": total}

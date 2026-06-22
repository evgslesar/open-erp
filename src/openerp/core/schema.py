from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.engine import Engine

from openerp.core.metadata import (
    AccumulationRegisterDef,
    CatalogDef,
    DocumentDef,
    FieldDef,
    FieldType,
    InformationRegisterDef,
    MetadataRegistry,
)
from openerp.core.naming import (
    catalog_table,
    document_table,
    information_register_table,
    register_movements_table,
    register_totals_table,
    table_part_table,
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sql_type(field: FieldDef):
    match field.type:
        case FieldType.STRING:
            return String(255)
        case FieldType.TEXT:
            return Text
        case FieldType.INTEGER | FieldType.MONEY:
            return Integer
        case FieldType.DECIMAL:
            return String(64)
        case FieldType.DATE:
            return Date
        case FieldType.DATETIME:
            return DateTime
        case FieldType.BOOLEAN:
            return Boolean
    raise ValueError(f"Unsupported field type: {field.type}")


def field_column(field: FieldDef) -> Column:
    return Column(field.name, sql_type(field), nullable=not field.required, default=field.default)


def system_tables(metadata: MetaData) -> None:
    Table(
        "sys_organizations",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(255), nullable=False),
        Column("created_at", DateTime, default=utcnow, nullable=False),
    )
    Table(
        "sys_users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String(255), nullable=False, unique=True),
        Column("name", String(255), nullable=False),
        Column("is_active", Boolean, default=True, nullable=False),
        Column("created_at", DateTime, default=utcnow, nullable=False),
    )
    Table(
        "sys_roles",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(100), nullable=False, unique=True),
    )
    Table(
        "sys_permissions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("role_id", ForeignKey("sys_roles.id"), nullable=False),
        Column("object_name", String(255), nullable=False),
        Column("operation", String(64), nullable=False),
    )
    Table(
        "sys_user_roles",
        metadata,
        Column("user_id", ForeignKey("sys_users.id"), primary_key=True),
        Column("role_id", ForeignKey("sys_roles.id"), primary_key=True),
    )
    Table(
        "sys_module_versions",
        metadata,
        Column("module_name", String(120), primary_key=True),
        Column("version", String(50), nullable=False),
        Column("installed_at", DateTime, default=utcnow, nullable=False),
    )
    Table(
        "sys_module_migrations",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("module_name", String(120), nullable=False),
        Column("migration_id", String(120), nullable=False),
        Column("applied_at", DateTime, default=utcnow, nullable=False),
    )
    Table(
        "sys_audit_log",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("occurred_at", DateTime, default=utcnow, nullable=False),
        Column("user_id", Integer, nullable=True),
        Column("organization_id", Integer, nullable=True),
        Column("object_type", String(120), nullable=False),
        Column("object_id", Integer, nullable=True),
        Column("operation", String(80), nullable=False),
        Column("before_json", Text, nullable=True),
        Column("after_json", Text, nullable=True),
    )
    Table(
        "sys_operation_log",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("occurred_at", DateTime, default=utcnow, nullable=False),
        Column("user_id", Integer, nullable=True),
        Column("organization_id", Integer, nullable=True),
        Column("operation", String(80), nullable=False),
        Column("object_type", String(120), nullable=True),
        Column("object_id", Integer, nullable=True),
        Column("details", Text, nullable=True),
    )
    Table(
        "sys_closed_periods",
        metadata,
        Column("organization_id", Integer, primary_key=True),
        Column("closed_until", Date, nullable=False, default=date.min),
        Column("updated_by", Integer, nullable=True),
        Column("updated_at", DateTime, default=utcnow, nullable=False),
    )
    Table(
        "sys_number_sequences",
        metadata,
        Column("organization_id", Integer, primary_key=True),
        Column("document_name", String(120), primary_key=True),
        Column("last_number", Integer, nullable=False, default=0),
    )


def base_application_columns() -> list[Column]:
    return [
        Column("id", Integer, primary_key=True),
        Column("organization_id", Integer, nullable=False, index=True),
        Column("created_at", DateTime, default=utcnow, nullable=False),
        Column("created_by", Integer, nullable=True),
        Column("updated_at", DateTime, default=utcnow, onupdate=utcnow, nullable=False),
        Column("updated_by", Integer, nullable=True),
        Column("deletion_mark", Boolean, default=False, nullable=False),
        Column("revision", Integer, default=1, nullable=False),
    ]


def build_catalog(metadata: MetaData, catalog: CatalogDef) -> None:
    columns = base_application_columns()
    columns.extend(
        [
            Column("name", String(255), nullable=False, index=True),
            Column("parent_id", Integer, nullable=True),
        ]
        if catalog.hierarchical
        else [Column("name", String(255), nullable=False, index=True)]
    )
    columns.extend(field_column(item) for item in catalog.fields)
    table = Table(catalog_table(catalog.name), metadata, *columns)
    for item in catalog.fields:
        if item.indexed:
            Index(f"ix_{table.name}_{item.name}", table.c[item.name])


def build_document(metadata: MetaData, document: DocumentDef) -> None:
    doc_table_name = document_table(document.name)
    columns = base_application_columns()
    columns.extend(
        [
            Column("date", Date, nullable=False, index=True),
            Column("number", String(40), nullable=False),
            Column("status", String(40), nullable=False, default="draft", index=True),
            Column("posted_at", DateTime, nullable=True),
            Column("posted_by", Integer, nullable=True),
            Column("comment", Text, nullable=True),
        ]
    )
    columns.extend(field_column(item) for item in document.fields)
    table = Table(doc_table_name, metadata, *columns)
    Index(f"ix_{doc_table_name}_org_date_id", table.c.organization_id, table.c.date, table.c.id)
    Index(f"uq_{doc_table_name}_org_number", table.c.organization_id, table.c.number, unique=True)

    for table_part in document.table_parts:
        part_name = table_part_table(document.name, table_part.name)
        part_columns = [
            Column("id", Integer, primary_key=True),
            Column("document_id", ForeignKey(f"{doc_table_name}.id"), nullable=False, index=True),
            Column("line_no", Integer, nullable=False),
        ]
        part_columns.extend(field_column(item) for item in table_part.fields)
        part_table = Table(part_name, metadata, *part_columns)
        Index(
            f"uq_{part_name}_document_line",
            part_table.c.document_id,
            part_table.c.line_no,
            unique=True,
        )


def build_accumulation_register(metadata: MetaData, register: AccumulationRegisterDef) -> None:
    movement_name = register_movements_table(register.name)
    movement_columns = [
        Column("id", Integer, primary_key=True),
        Column("period", Date, nullable=False, index=True),
        Column("organization_id", Integer, nullable=False, index=True),
        Column("registrator_type", String(120), nullable=False),
        Column("registrator_id", Integer, nullable=False),
        Column("line_no", Integer, nullable=False),
        Column("created_at", DateTime, default=utcnow, nullable=False),
    ]
    movement_columns.extend(field_column(item) for item in register.dimensions)
    movement_columns.extend(field_column(item) for item in register.resources)
    movements = Table(movement_name, metadata, *movement_columns)
    Index(
        f"ix_{movement_name}_registrator",
        movements.c.registrator_type,
        movements.c.registrator_id,
    )

    totals_name = register_totals_table(register.name)
    total_columns = [
        Column("period_start", Date, nullable=False),
        Column("organization_id", Integer, nullable=False),
    ]
    total_columns.extend(field_column(item) for item in register.dimensions)
    total_columns.extend(field_column(item) for item in register.resources)
    total_columns.append(Column("updated_at", DateTime, default=utcnow, nullable=False))
    totals = Table(totals_name, metadata, *total_columns)
    pk_columns = [totals.c.period_start, totals.c.organization_id]
    pk_columns.extend(totals.c[item.name] for item in register.dimensions)
    Index(f"uq_{totals_name}_key", *pk_columns, unique=True)


def build_information_register(metadata: MetaData, register: InformationRegisterDef) -> None:
    table_name = information_register_table(register.name)
    columns = [
        Column("id", Integer, primary_key=True),
        Column("period", Date, nullable=False, index=True),
        Column("organization_id", Integer, nullable=False, index=True),
        Column("created_at", DateTime, default=utcnow, nullable=False),
    ]
    columns.extend(field_column(item) for item in register.dimensions)
    columns.extend(field_column(item) for item in register.resources)
    table = Table(table_name, metadata, *columns)
    Index(f"ix_{table_name}_org_period_id", table.c.organization_id, table.c.period, table.c.id)


def build_metadata(registry: MetadataRegistry) -> MetaData:
    metadata = MetaData()
    system_tables(metadata)
    for catalog in registry.catalogs():
        build_catalog(metadata, catalog)
    for document in registry.documents():
        build_document(metadata, document)
    for register in registry.accumulation_registers():
        build_accumulation_register(metadata, register)
    for register in registry.information_registers():
        build_information_register(metadata, register)
    return metadata


def create_all(engine: Engine, registry: MetadataRegistry) -> MetaData:
    metadata = build_metadata(registry)
    metadata.create_all(engine)
    return metadata

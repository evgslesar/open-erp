# ADR 0001: MVP Scope

## Status

Accepted

## Decision

The first release is a trade, warehouse, cash/bank, reports, print forms, import/export, and extension platform for small businesses.

The MVP includes catalogs, documents, accumulation registers, information registers, generated server-rendered UI, audit, roles, multi-organization accounting, SQLite backup, demo data, and PostgreSQL-compatible SQLAlchemy Core queries.

The MVP excludes regulated accounting, payroll, fixed assets, production, full tax reporting, visual schema configuration, and user-uploaded Python code.

## Rationale

The main project risk is trying to replace a full accounting and ERP product family at once. A narrow end-to-end product proves the document/register platform and gives early users something useful before country-specific accounting modules are built.

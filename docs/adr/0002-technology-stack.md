# ADR 0002: Technology Stack

## Status

Accepted

## Decision

Use FastAPI, Jinja2, HTMX-compatible server-rendered templates, SQLAlchemy Core in synchronous transactions, SQLite by default, and PostgreSQL as the growth database.

Use openpyxl and CSV for export/import. PDF generation is an optional backend and must not be required for the default install.

## Rationale

Accounting systems are dominated by transactions, indexes, locks, posting consistency, and reporting correctness. Synchronous SQLAlchemy Core keeps database access explicit and portable while still allowing dynamic tables generated from metadata.

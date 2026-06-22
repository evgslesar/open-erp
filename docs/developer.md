# Developer Guide

## Metadata-first extensions

Extensions are trusted Python packages. They register metadata and handlers with `MetadataRegistry`.

An extension can define:

- catalogs;
- documents and table parts;
- accumulation registers;
- information registers;
- reports;
- print forms;
- posting handlers.

User-uploaded Python code is intentionally not supported. Regular users can configure filters, report variants, columns, print templates, and dashboards without code.

## Posting rules

Document posting must be idempotent. The platform deletes existing movements for the document registrar before running the posting handler again. Posting and unposting must run inside one database transaction.

Posting handlers should only write movements through `RegisterService`, never with raw SQL. Reports should read register data through `RegisterService.balance`, `turnover`, `slice_last`, or related APIs.

## Database portability

Application code must not depend on SQLite-only or PostgreSQL-only SQL. Use SQLAlchemy Core expressions and keep tests running on SQLite. PostgreSQL is the target production database for active multi-user installations.

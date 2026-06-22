# Open ERP

Open ERP is a developer-first open-source web application for trade, warehouse, cash/bank, and management accounting for small businesses.

The MVP is intentionally scoped to:

- catalogs: organizations, counterparties, products, warehouses, currencies, units, cash-flow categories, price types;
- documents: receipts, sales, transfers, inventory adjustments, orders, cash and bank payments;
- registers: stock, settlements, cash, and dated information such as prices and currency rates;
- reports, print forms, CSV/XLSX import and export;
- roles, permissions, organizations, audit log, and operation log;
- SQLite by default with PostgreSQL support for growth;
- trusted Python plugins for extensions.

Post-MVP modules such as regulated accounting, payroll, fixed assets, production, tax reporting, and country-specific compliance are out of scope for the first release.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\openerp init-db
.\.venv\Scripts\openerp seed-demo
.\.venv\Scripts\openerp run
```

Open http://127.0.0.1:8000.

## Architecture

The system is built around metadata, documents, registers, and idempotent posting. Application objects are described in trusted Python modules. The platform uses those definitions to create tables, render basic UI, enforce permissions, and expose register APIs for reports.

The database layer is SQLAlchemy Core in synchronous transactions. SQLite is the zero-dependency default; PostgreSQL is supported by keeping application code database-neutral.

## License

AGPLv3. See `LICENSE`.

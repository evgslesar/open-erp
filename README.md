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

Open http://127.0.0.1:8000 and log in with `admin@example.local` / `admin`.

## CLI commands

| Command | Description |
|---|---|
| `init-db` | Create database schema |
| `seed-demo` | Load demo trade data |
| `run --port 8000` | Start web server |
| `backup` | Backup SQLite database |
| `set-password <email> <password>` | Change user password |
| `export-stock <file> [--fmt csv\|xlsx]` | Export stock balance report |
| `import-catalog <catalog> <file> [--fmt csv\|xlsx] [--dry-run]` | Import catalog items |
| `import-initial-stock <file> [--dry-run]` | Import initial stock via inventory adjustment |
| `catalog-template <catalog> <file>` | Download CSV import template |
| `rebuild-totals <register>` | Rebuild register totals from movements |
| `verify-totals <register>` | Check totals consistency |

## Architecture

The system is built around metadata, documents, registers, and idempotent posting. Application objects are described in trusted Python modules. The platform uses those definitions to create tables, render basic UI, enforce permissions, and expose register APIs for reports.

The database layer is SQLAlchemy Core in synchronous transactions. SQLite is the zero-dependency default; PostgreSQL is supported by keeping application code database-neutral. CI runs on both.

### Key design decisions

- **Register totals** store monthly turnover. Balance on date = sum of all prior months' totals + current month's movements. Totals are maintained incrementally on every posting.
- **Posting** is idempotent: re-posting deletes old movements then re-creates them. Unposting removes movements. Closed periods block changes.
- **Authentication** uses bcrypt password hashes with signed cookie sessions.
- **Forms** are generated from metadata. Table parts support dynamic line addition via Alpine.js.

## License

AGPLv3. See `LICENSE`.

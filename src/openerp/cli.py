from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
import uvicorn

from openerp.bootstrap import init_engine
from openerp.config import get_settings
from openerp.core.backup import backup_sqlite, verify_backup
from openerp.core.context import RequestContext
from openerp.core.import_export import (
    catalog_template_rows,
    export_rows_csv,
    export_rows_xlsx,
    import_catalog_rows,
    import_initial_stock_rows,
    read_csv_rows,
    read_xlsx_rows,
)
from openerp.core.registers import RegisterService
from openerp.core.security import set_user_password
from openerp.db import transaction
from openerp.modules.trade.demo import seed_demo as seed_trade_demo
from openerp.modules.trade.reports import stock_balance_report

app = typer.Typer(help="Open ERP command line")


@app.command("init-db")
def init_db() -> None:
    init_engine()
    typer.echo("Database initialized")


@app.command("seed-demo")
def seed_demo() -> None:
    engine, registry = init_engine()
    with transaction(engine) as connection:
        seed_trade_demo(connection, registry)
    typer.echo("Demo data seeded")
    typer.echo("Login: admin@example.local  Password: admin")


@app.command("set-password")
def set_password(email: str, password: str) -> None:
    engine, registry = init_engine()
    with transaction(engine) as connection:
        set_user_password(connection, email, password)
    typer.echo(f"Password updated for {email}")


@app.command("run")
def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    uvicorn.run("openerp.web.app:create_app", host=host, port=port, factory=True, reload=False)


@app.command("backup")
def backup() -> None:
    settings = get_settings()
    destination = backup_sqlite(settings.database_url, settings.backup_dir)
    if not verify_backup(destination):
        raise typer.Exit(1)
    typer.echo(str(destination))


@app.command("export-stock")
def export_stock(path: Path, fmt: str = "csv") -> None:
    engine, registry = init_engine()
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        rows = stock_balance_report(connection, registry, context, date.today())
    if fmt == "xlsx":
        export_rows_xlsx(rows, path)
    else:
        export_rows_csv(rows, path)
    typer.echo(str(path))


@app.command("rebuild-totals")
def rebuild_totals(register_name: str) -> None:
    engine, registry = init_engine()
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        RegisterService(connection, registry, context).rebuild_totals(register_name)
    typer.echo(f"Totals rebuilt for register '{register_name}'")


@app.command("verify-totals")
def verify_totals(register_name: str) -> None:
    engine, registry = init_engine()
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        discrepancies = RegisterService(connection, registry, context).verify_totals(register_name)
    if not discrepancies:
        typer.echo(f"Register '{register_name}' totals are consistent with movements")
        return
    for entry in discrepancies:
        typer.echo(
            f"DISCREPANCY period={entry['period_start']} "
            f"org={entry['organization_id']} "
            f"dims={entry['dimensions']} "
            f"resource={entry['resource']} "
            f"expected={entry['expected']} actual={entry['actual']}"
        )
    raise typer.Exit(1)


@app.command("import-catalog")
def import_catalog(
    catalog_name: str,
    file: Path,
    fmt: str = "csv",
    dry_run: bool = False,
) -> None:
    rows = _read_rows(file, fmt)
    engine, registry = init_engine()
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        result = import_catalog_rows(
            connection, registry, context, catalog_name, rows, dry_run=dry_run
        )
    _print_import_result(result, dry_run)


@app.command("import-initial-stock")
def import_initial_stock(
    file: Path,
    fmt: str = "csv",
    dry_run: bool = False,
) -> None:
    rows = _read_rows(file, fmt)
    engine, registry = init_engine()
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        result = import_initial_stock_rows(
            connection, registry, context, rows, dry_run=dry_run
        )
    _print_import_result(result, dry_run)


@app.command("catalog-template")
def catalog_template(catalog_name: str, file: Path, fmt: str = "csv") -> None:
    engine, registry = init_engine()
    rows = catalog_template_rows(registry, catalog_name)
    if fmt == "xlsx":
        export_rows_xlsx(rows, file)
    else:
        export_rows_csv(rows, file)
    typer.echo(f"Template exported: {file}")


def _read_rows(file: Path, fmt: str) -> list[dict]:
    if fmt == "xlsx":
        return read_xlsx_rows(file)
    return read_csv_rows(file)


def _print_import_result(result: dict, dry_run: bool) -> None:
    if dry_run:
        typer.echo(f"Dry run: {result.get('imported', 0)} rows would be imported")
    else:
        typer.echo(f"Imported: {result.get('imported', 0)} rows")
    if result.get("document_id"):
        typer.echo(f"Document: {result['document_id']}")
    for error in result.get("errors", []):
        typer.echo(f"ERROR row {error['row']}: {error['error']}")
    if result.get("errors"):
        raise typer.Exit(1)

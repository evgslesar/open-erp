from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
import uvicorn

from openerp.bootstrap import init_engine
from openerp.config import get_settings
from openerp.core.backup import backup_sqlite, verify_backup
from openerp.core.context import RequestContext
from openerp.core.import_export import export_rows_csv, export_rows_xlsx
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

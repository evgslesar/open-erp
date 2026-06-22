from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.repository import Repository


def import_catalog_csv(
    connection: Connection,
    registry,
    context: RequestContext,
    catalog_name: str,
    path: Path,
) -> list[int]:
    repository = Repository(connection, registry, context)
    imported_ids = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            imported_ids.append(repository.create_catalog_item(catalog_name, dict(row)))
    return imported_ids


def export_rows_csv(rows: Iterable[dict], path: Path) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def export_rows_xlsx(rows: Iterable[dict], path: Path) -> None:
    rows = list(rows)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Report"
    if rows:
        headers = list(rows[0].keys())
        worksheet.append(headers)
        for row in rows:
            worksheet.append([row.get(header) for header in headers])
    workbook.save(path)

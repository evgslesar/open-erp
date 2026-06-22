from __future__ import annotations

from datetime import date

from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.metadata import MetadataRegistry
from openerp.core.registers import RegisterService


def stock_balance_report(
    connection: Connection,
    registry: MetadataRegistry,
    context: RequestContext,
    on_date: date | None = None,
) -> list[dict]:
    service = RegisterService(connection, registry, context)
    return service.balance("stock", on_date or date.today())

from __future__ import annotations

from datetime import date, timedelta

from openerp.core.import_export import export_rows_csv, export_rows_xlsx
from openerp.core.posting import DocumentPostingService
from openerp.core.registers import RegisterService
from openerp.core.repository import Repository
from openerp.db import transaction
from tests.test_posting import create_document, create_master_data


def test_register_turnover_rebuild_totals_and_keyset(app_state, context, tmp_path):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        first_id = create_document(
            repository,
            "receipt",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            5,
        )
        second_id = create_document(
            repository,
            "receipt",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            7,
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", first_id)
        poster.post("receipt", second_id)

        registers = RegisterService(connection, registry, context)
        turnover = registers.turnover("stock", date.today() - timedelta(days=1), date.today())
        assert turnover[0]["quantity"] == 12

        registers.rebuild_totals("stock")
        assert registers.balance("stock", date.today())[0]["quantity"] == 12

        page = repository.list_documents_keyset("receipt", limit=1)
        assert len(page) == 1
        next_page = repository.list_documents_keyset(
            "receipt",
            limit=1,
            after_date=page[0]["date"],
            after_id=page[0]["id"],
        )
        assert len(next_page) == 1
        assert next_page[0]["id"] != page[0]["id"]

        csv_path = tmp_path / "stock.csv"
        xlsx_path = tmp_path / "stock.xlsx"
        export_rows_csv(turnover, csv_path)
        export_rows_xlsx(turnover, xlsx_path)
        assert csv_path.read_text(encoding="utf-8")
        assert xlsx_path.exists()

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


def test_balance_and_turnover_returns_open_turnover_close(app_state, context):
    engine, registry = app_state
    past = date.today().replace(day=1) - timedelta(days=15)
    today = date.today()
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)

        past_receipt = repository.create_document(
            "receipt",
            {"date": past, "counterparty_id": counterparty_id, "warehouse_id": warehouse_id},
            {"lines": [{"product_id": product_id, "quantity": "100", "price": "10.00",
                        "amount_minor": 100000, "currency_id": currency_id}]},
        )
        DocumentPostingService(connection, registry, context).post("receipt", past_receipt)

        today_sale = repository.create_document(
            "sale",
            {"date": today, "counterparty_id": counterparty_id, "warehouse_id": warehouse_id},
            {"lines": [{"product_id": product_id, "quantity": "30", "price": "15.00",
                        "amount_minor": 45000, "currency_id": currency_id}]},
        )
        DocumentPostingService(connection, registry, context).post("sale", today_sale)

        registers = RegisterService(connection, registry, context)
        result = registers.balance_and_turnover(
            "stock", today.replace(day=1), today
        )
        assert len(result) == 1
        row = result[0]
        assert row["quantity_open"] == 100
        assert row["quantity_turnover"] == -30
        assert row["quantity_close"] == 70


def test_movements_filters_by_registrator_type(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 10
        )
        sale_id = create_document(
            repository, "sale", warehouse_id, counterparty_id, product_id, currency_id, 4
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("sale", sale_id)

        registers = RegisterService(connection, registry, context)
        all_moves = registers.movements("stock")
        assert len(all_moves) == 2
        sale_only = registers.movements(
            "stock", filters={"registrator_type": "sale"}
        )
        assert len(sale_only) == 1
        assert sale_only[0]["registrator_type"] == "sale"


def test_slice_last_returns_latest_per_dimension(app_state, context):
    engine, registry = app_state
    past = date.today() - timedelta(days=30)
    recent = date.today() - timedelta(days=1)
    with transaction(engine) as connection:
        registers = RegisterService(connection, registry, context)
        metadata = connection.engine._openerp_metadata
        prices = metadata.tables["ireg_prices"]
        connection.execute(
            prices.insert().values(
                period=past,
                organization_id=1,
                product_id=1,
                price_type_id=1,
                price="90.00",
                currency_id=1,
            )
        )
        connection.execute(
            prices.insert().values(
                period=recent,
                organization_id=1,
                product_id=1,
                price_type_id=1,
                price="105.00",
                currency_id=1,
            )
        )
        connection.execute(
            prices.insert().values(
                period=recent,
                organization_id=1,
                product_id=2,
                price_type_id=1,
                price="50.00",
                currency_id=1,
            )
        )

        result = registers.slice_last("prices", date.today())
        assert len(result) == 2
        by_product = {row["product_id"]: row for row in result}
        assert by_product[1]["price"] == "105.00"
        assert by_product[2]["price"] == "50.00"

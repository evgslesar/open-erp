from __future__ import annotations

from datetime import date, timedelta

import pytest
from dateutil.relativedelta import relativedelta

from openerp.core.posting import ClosedPeriodError, DocumentPostingService, set_closed_period
from openerp.core.registers import RegisterService
from openerp.core.repository import Repository
from openerp.db import transaction


def previous_month_day() -> date:
    return date.today().replace(day=1) - relativedelta(days=15)


def create_master_data(repository: Repository):
    currency_id = repository.create_catalog_item(
        "currency",
        {"name": "Russian ruble", "code": "RUB", "scale": 2},
    )
    warehouse_id = repository.create_catalog_item("warehouse", {"name": "Main"})
    counterparty_id = repository.create_catalog_item(
        "counterparty",
        {"name": "Counterparty", "tax_id": "1"},
    )
    product_id = repository.create_catalog_item(
        "product",
        {"name": "Product", "sku": "P1", "unit": "pcs"},
    )
    return currency_id, warehouse_id, counterparty_id, product_id


def create_document(
    repository,
    document_name,
    warehouse_id,
    counterparty_id,
    product_id,
    currency_id,
    qty,
):
    return repository.create_document(
        document_name,
        {
            "date": date.today(),
            "counterparty_id": counterparty_id,
            "warehouse_id": warehouse_id,
        },
        {
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": str(qty),
                    "price": "100.00",
                    "amount_minor": int(qty) * 10000,
                    "currency_id": currency_id,
                }
            ]
        },
    )


def test_posting_is_idempotent(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository,
            "receipt",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            10,
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("receipt", receipt_id)

        registers = RegisterService(connection, registry, context)
        balance = registers.balance("stock", date.today())[0]
        assert balance["quantity"] == 10


def test_unpost_and_repost_restore_balances(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository,
            "receipt",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            10,
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.unpost("receipt", receipt_id)
        registers = RegisterService(connection, registry, context)
        assert registers.balance("stock", date.today()) == []
        poster.repost("receipt", receipt_id)
        assert registers.balance("stock", date.today())[0]["quantity"] == 10


def test_negative_stock_is_blocked(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        sale_id = create_document(
            repository,
            "sale",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            3,
        )
        poster = DocumentPostingService(connection, registry, context)
        with pytest.raises(ValueError):
            poster.post("sale", sale_id)


def test_closed_period_blocks_posting(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository,
            "receipt",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            10,
        )
        set_closed_period(connection, context, date.today() + timedelta(days=1))
        poster = DocumentPostingService(connection, registry, context)
        with pytest.raises(ClosedPeriodError):
            poster.post("receipt", receipt_id)


def test_trade_metadata_covers_mvp_scope(app_state):
    _, registry = app_state

    assert {item.name for item in registry.catalogs()} >= {
        "organization",
        "counterparty",
        "product",
        "warehouse",
        "currency",
        "unit",
        "cash_flow_category",
        "price_type",
    }
    assert {item.name for item in registry.documents()} >= {
        "receipt",
        "sale",
        "transfer",
        "inventory_adjustment",
        "order",
        "cash_payment",
        "bank_payment",
    }
    assert {item.name for item in registry.accumulation_registers()} >= {
        "stock",
        "settlements",
        "cash",
    }
    assert {item.name for item in registry.information_registers()} >= {
        "prices",
        "currency_rates",
    }


def test_receipt_and_sale_update_settlements(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository,
            "receipt",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            10,
        )
        sale_id = create_document(
            repository,
            "sale",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            4,
        )

        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("sale", sale_id)

        registers = RegisterService(connection, registry, context)
        settlements = registers.balance("settlements", date.today())[0]
        assert settlements["amount_minor"] == -60000


def test_transfer_moves_stock_between_warehouses(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        (
            currency_id,
            source_warehouse_id,
            counterparty_id,
            product_id,
        ) = create_master_data(repository)
        destination_warehouse_id = repository.create_catalog_item("warehouse", {"name": "Store"})
        receipt_id = create_document(
            repository,
            "receipt",
            source_warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            10,
        )
        transfer_id = repository.create_document(
            "transfer",
            {
                "date": date.today(),
                "source_warehouse_id": source_warehouse_id,
                "destination_warehouse_id": destination_warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "3",
                    }
                ]
            },
        )

        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("transfer", transfer_id)

        registers = RegisterService(connection, registry, context)
        balances = {
            row["warehouse_id"]: row["quantity"]
            for row in registers.balance("stock", date.today())
        }
        assert balances[source_warehouse_id] == 7
        assert balances[destination_warehouse_id] == 3


def test_inventory_adjustment_and_payments_update_registers(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        category_id = repository.create_catalog_item(
            "cash_flow_category",
            {"name": "Оплата покупателей", "kind": "operating"},
        )
        adjustment_id = repository.create_document(
            "inventory_adjustment",
            {
                "date": date.today(),
                "warehouse_id": warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity_delta": "5",
                    }
                ]
            },
        )
        payment_id = repository.create_document(
            "cash_payment",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "cash_flow_category_id": category_id,
                "direction": "incoming",
                "amount_minor": 50000,
                "currency_id": currency_id,
            },
        )

        poster = DocumentPostingService(connection, registry, context)
        poster.post("inventory_adjustment", adjustment_id)
        poster.post("cash_payment", payment_id)

        registers = RegisterService(connection, registry, context)
        stock = registers.balance("stock", date.today())[0]
        cash = registers.balance("cash", date.today())[0]
        settlements = registers.balance("settlements", date.today())[0]
        assert stock["quantity"] == 5
        assert cash["account_type"] == "cash"
        assert cash["amount_minor"] == 50000
        assert settlements["amount_minor"] == -50000


def test_cross_month_balance_uses_totals(app_state, context):
    engine, registry = app_state
    past_date = previous_month_day()
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = repository.create_document(
            "receipt",
            {
                "date": past_date,
                "counterparty_id": counterparty_id,
                "warehouse_id": warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "100",
                        "price": "10.00",
                        "amount_minor": 100000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)

        registers = RegisterService(connection, registry, context)
        balance_today = registers.balance("stock", date.today())
        balance_past = registers.balance("stock", past_date)
        assert balance_past[0]["quantity"] == 100
        assert balance_today[0]["quantity"] == 100


def test_repost_in_past_period_updates_totals(app_state, context):
    engine, registry = app_state
    past_date = previous_month_day()
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = repository.create_document(
            "receipt",
            {
                "date": past_date,
                "counterparty_id": counterparty_id,
                "warehouse_id": warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "100",
                        "price": "10.00",
                        "amount_minor": 100000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.unpost("receipt", receipt_id)

        registers = RegisterService(connection, registry, context)
        assert registers.balance("stock", date.today()) == []


def test_verify_totals_detects_and_resolves_divergence(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository,
            "receipt",
            warehouse_id,
            counterparty_id,
            product_id,
            currency_id,
            10,
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)

        registers = RegisterService(connection, registry, context)
        assert registers.verify_totals("stock") == []

        movements_table = connection.engine._openerp_metadata.tables["reg_stock_movements"]
        connection.execute(
            movements_table.update()
            .where(movements_table.c.id == 1)
            .values(quantity="999")
        )
        assert len(registers.verify_totals("stock")) == 1

        registers.rebuild_totals("stock")
        assert registers.verify_totals("stock") == []

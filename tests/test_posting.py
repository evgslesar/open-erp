from __future__ import annotations

from datetime import date, timedelta

import pytest

from openerp.core.posting import ClosedPeriodError, DocumentPostingService, set_closed_period
from openerp.core.registers import RegisterService
from openerp.core.repository import Repository
from openerp.db import transaction


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

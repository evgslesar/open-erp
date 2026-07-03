from __future__ import annotations

from datetime import date

import pytest

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.posting import DocumentPostingService, InvalidPostingError
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.demo import ensure_admin_security
from openerp.modules.trade.orders import order_fulfillment_status
from openerp.modules.trade.settlements import unpaid_balance, unpaid_documents
from tests.test_posting import create_document, create_master_data


@pytest.fixture()
def app_state(tmp_path, monkeypatch):
    db_path = tmp_path / "settlements.db"
    monkeypatch.setenv("OPENERP_DATABASE_URL", f"sqlite:///{db_path}")
    engine, registry = init_engine(f"sqlite:///{db_path}")
    with transaction(engine) as connection:
        ensure_admin_security(connection)
    return engine, registry


def test_unpaid_balance_after_partial_payment(app_state, context):
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
        assert unpaid_balance(connection, context.organization_id, "sale", sale_id) == 40000
        payment_id = repository.create_document(
            "cash_payment",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "money_account_id": repository.create_catalog_item(
                    "money_account",
                    {"name": "Cash", "type": "cash", "currency_id": currency_id},
                ),
                "cash_flow_category_id": repository.create_catalog_item(
                    "cash_flow_category", {"name": "Sales", "kind": "operating"}
                ),
                "direction": "incoming",
                "amount_minor": 15000,
                "currency_id": currency_id,
                "allocation_document_type": "sale",
                "allocation_document_id": sale_id,
            },
        )
        poster.post("cash_payment", payment_id)
        assert unpaid_balance(connection, context.organization_id, "sale", sale_id) == 25000


def test_allocation_rejects_overpayment(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
        sale_id = create_document(
            repository, "sale", warehouse_id, counterparty_id, product_id, currency_id, 1
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("sale", sale_id)
        payment_id = repository.create_document(
            "cash_payment",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "money_account_id": repository.create_catalog_item(
                    "money_account",
                    {"name": "Cash2", "type": "cash", "currency_id": currency_id},
                ),
                "cash_flow_category_id": repository.create_catalog_item(
                    "cash_flow_category", {"name": "Sales2", "kind": "operating"}
                ),
                "direction": "incoming",
                "amount_minor": 20000,
                "currency_id": currency_id,
                "allocation_document_type": "sale",
                "allocation_document_id": sale_id,
            },
        )
        with pytest.raises(InvalidPostingError):
            poster.post("cash_payment", payment_id)


def test_unpaid_documents_lists_open_sales(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
        sale_id = create_document(
            repository, "sale", warehouse_id, counterparty_id, product_id, currency_id, 2
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("sale", sale_id)
        rows = unpaid_documents(connection, context.organization_id, counterparty_id)
        sale_rows = [row for row in rows if row["document_type"] == "sale"]
        assert len(sale_rows) == 1
        assert sale_rows[0]["document_id"] == sale_id
        assert sale_rows[0]["unpaid_minor"] == 20000


def test_order_fulfillment_status_flow(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 10
        )
        order_id = create_document(
            repository, "order", warehouse_id, counterparty_id, product_id, currency_id, 8
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        order = repository.get_document("order", order_id)
        assert order_fulfillment_status(connection, order) == "draft"
        poster.post("order", order_id)
        order = repository.get_document("order", order_id)
        assert order_fulfillment_status(connection, order) == "reserved"
        sale_id = repository.create_document(
            "sale",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "warehouse_id": warehouse_id,
                "based_on_order_id": order_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "3",
                        "price": "100.00",
                        "amount_minor": 30000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        poster.post("sale", sale_id)
        order = repository.get_document("order", order_id)
        assert order_fulfillment_status(connection, order) == "partial"
        sale_full_id = repository.create_document(
            "sale",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "warehouse_id": warehouse_id,
                "based_on_order_id": order_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "5",
                        "price": "100.00",
                        "amount_minor": 50000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        poster.post("sale", sale_full_id)
        order = repository.get_document("order", order_id)
        assert order_fulfillment_status(connection, order) == "fulfilled"

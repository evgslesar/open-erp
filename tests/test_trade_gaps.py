from __future__ import annotations

from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.posting import DocumentPostingService, get_closed_period, set_closed_period
from openerp.core.registers import RegisterService
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.demo import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, ensure_admin_security
from openerp.modules.trade.reports import payment_calendar_report, turnover_report
from openerp.web.app import create_app
from tests.test_posting import create_document, create_master_data


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_gaps.db"
    monkeypatch.setenv("OPENERP_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENERP_SECRET_KEY", "test-secret")
    engine, registry = init_engine(f"sqlite:///{db_path}")
    with transaction(engine) as connection:
        ensure_admin_security(connection)
    test_client = TestClient(create_app())
    test_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
    )
    return test_client, engine, registry


def test_document_list_filters(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
        create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 7
        )
        other_cp = repository.create_catalog_item("counterparty", {"name": "Other", "tax_id": "2"})
        repository.create_document(
            "receipt",
            {
                "date": date.today(),
                "counterparty_id": other_cp,
                "warehouse_id": warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "1",
                        "price": "10.00",
                        "amount_minor": 1000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        filtered = repository.list_documents_keyset(
            "receipt",
            counterparty_id=counterparty_id,
        )
        assert len(filtered) == 2
        assert all(row["counterparty_id"] == counterparty_id for row in filtered)


def test_catalog_name_search(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        repository.create_catalog_item("product", {"name": "Alpha widget", "sku": "A1", "unit": "pcs"})
        repository.create_catalog_item("product", {"name": "Beta part", "sku": "B1", "unit": "pcs"})
        rows = repository.list_catalog_items("product", q="alpha")
        assert len(rows) == 1
        assert rows[0]["name"] == "Alpha widget"


def test_closed_period_ui_route(client):
    test_client, _, _ = client
    response = test_client.get("/settings/closed-period")
    assert response.status_code == 200
    assert "Закрытый период" in response.text


def test_closed_period_get_and_set(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        assert get_closed_period(connection, context) is None
        set_closed_period(connection, context, date.today())
        assert get_closed_period(connection, context) == date.today()


def test_order_reservation_blocks_sale(app_state, context):
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
        sale_id = create_document(
            repository, "sale", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("order", order_id)
        with pytest.raises(Exception):
            poster.post("sale", sale_id)


def test_sale_releases_order_reservation(app_state, context):
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
                        "quantity": "5",
                        "price": "100.00",
                        "amount_minor": 50000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("order", order_id)
        poster.post("sale", sale_id)
        registers = RegisterService(connection, registry, context)
        reserved = registers.balance("stock_reserved", date.today())
        total_reserved = sum(row["quantity"] for row in reserved) if reserved else 0
        assert total_reserved == 3


def test_returns_reverse_postings(app_state, context):
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
        sale_return_id = create_document(
            repository, "sale_return", warehouse_id, counterparty_id, product_id, currency_id, 2
        )
        purchase_return_id = create_document(
            repository, "purchase_return", warehouse_id, counterparty_id, product_id, currency_id, 1
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("sale", sale_id)
        poster.post("sale_return", sale_return_id)
        poster.post("purchase_return", purchase_return_id)
        registers = RegisterService(connection, registry, context)
        stock = registers.balance("stock", date.today())[0]
        assert stock["quantity"] == 7


def test_price_api(client):
    test_client, engine, registry = client
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id = repository.create_catalog_item(
            "currency", {"name": "RUB", "code": "RUB", "scale": 2}
        )
        price_type_id = repository.create_catalog_item(
            "price_type", {"name": "Retail", "currency_id": currency_id}
        )
        product_id = repository.create_catalog_item(
            "product", {"name": "Item", "sku": "I1", "unit": "pcs"}
        )
        metadata = connection.engine._openerp_metadata
        prices = metadata.tables["ireg_prices"]
        connection.execute(
            prices.insert().values(
                period=date.today(),
                organization_id=1,
                product_id=product_id,
                price_type_id=price_type_id,
                price="150.00",
                currency_id=currency_id,
            )
        )
    response = test_client.get(
        f"/api/price?product_id={product_id}&price_type_id={price_type_id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["price"] == "150.00"
    assert data["currency_id"] == currency_id


def test_turnover_report(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 10
        )
        DocumentPostingService(connection, registry, context).post("receipt", receipt_id)
        rows = turnover_report(
            connection,
            registry,
            context,
            date_from=date.today().replace(day=1),
            date_to=date.today(),
        )
        assert rows
        assert rows[0]["quantity_close"] == 10


def test_payment_calendar_report(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        sale_id = create_document(
            repository, "sale", warehouse_id, counterparty_id, product_id, currency_id, 3
        )
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 10
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        poster.post("sale", sale_id)
        rows = payment_calendar_report(
            connection,
            registry,
            context,
            date_from=date.today() - timedelta(days=1),
            date_to=date.today() + timedelta(days=1),
        )
        assert rows
        assert any(row["document_type"] == "sale" for row in rows)


def test_rest_api_catalog_and_documents(client):
    test_client, engine, registry = client
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 1
        )
    catalog_response = test_client.get("/api/v1/catalogs/product")
    assert catalog_response.status_code == 200
    assert catalog_response.json()["items"]
    docs_response = test_client.get("/api/v1/documents/receipt")
    assert docs_response.status_code == 200
    assert docs_response.json()["items"]


def test_receipt_prefill_from_purchase_order(client):
    test_client, engine, registry = client
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        order_id = repository.create_document(
            "purchase_order",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "warehouse_id": warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "12",
                        "price": "50.00",
                        "amount_minor": 60000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
    response = test_client.get(f"/documents/receipt/new?based_on=purchase_order/{order_id}")
    assert response.status_code == 200
    assert "12" in response.text

from __future__ import annotations

from datetime import date

import pytest
from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.posting import DocumentPostingService
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.demo import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, ensure_admin_security
from openerp.web.app import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "reports.db"
    monkeypatch.setenv("OPENERP_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENERP_SECRET_KEY", "test-secret")
    engine, registry = init_engine(f"sqlite:///{db_path}")
    with transaction(engine) as connection:
        ensure_admin_security(connection)
        context = RequestContext(user_id=1, organization_id=1, is_admin=True)
        repository = Repository(connection, registry, context)
        currency_id = repository.create_catalog_item(
            "currency", {"name": "RUB", "code": "RUB", "scale": 2}
        )
        warehouse_id = repository.create_catalog_item("warehouse", {"name": "Main"})
        counterparty_id = repository.create_catalog_item(
            "counterparty", {"name": "Customer", "tax_id": "1"}
        )
        product_id = repository.create_catalog_item(
            "product", {"name": "Widget", "sku": "W1", "unit": "pcs"}
        )
        category_id = repository.create_catalog_item(
            "cash_flow_category", {"name": "Operations", "kind": "operating"}
        )
        money_account_id = repository.create_catalog_item(
            "money_account",
            {"name": "Cash desk", "type": "cash", "currency_id": currency_id},
        )

        receipt_id = repository.create_document(
            "receipt",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "warehouse_id": warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "100",
                        "price": "50.00",
                        "amount_minor": 500000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        DocumentPostingService(connection, registry, context).post("receipt", receipt_id)

        sale_id = repository.create_document(
            "sale",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "warehouse_id": warehouse_id,
            },
            {
                "lines": [
                    {
                        "product_id": product_id,
                        "quantity": "20",
                        "price": "75.00",
                        "amount_minor": 150000,
                        "currency_id": currency_id,
                    }
                ]
            },
        )
        DocumentPostingService(connection, registry, context).post("sale", sale_id)

        payment_id = repository.create_document(
            "cash_payment",
            {
                "date": date.today(),
                "counterparty_id": counterparty_id,
                "money_account_id": money_account_id,
                "cash_flow_category_id": category_id,
                "direction": "incoming",
                "amount_minor": 100000,
                "currency_id": currency_id,
            },
        )
        DocumentPostingService(connection, registry, context).post("cash_payment", payment_id)

    test_client = TestClient(create_app())
    test_client.post(
        "/login", data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD}
    )
    yield test_client


def test_stock_balance_report_shows_remaining(client):
    response = client.get("/reports/stock_balance")
    assert response.status_code == 200
    assert "Widget" in response.text
    assert "80" in response.text


def test_sales_report_shows_sold_quantity_and_revenue(client):
    response = client.get("/reports/sales")
    assert response.status_code == 200
    assert "Widget" in response.text
    assert "20" in response.text
    assert "150000" in response.text


def test_settlements_report_shows_counterparty_balance(client):
    response = client.get("/reports/settlements")
    assert response.status_code == 200
    assert "Customer" in response.text


def test_cash_report_shows_account_balance(client):
    response = client.get("/reports/cash")
    assert response.status_code == 200
    assert "Cash desk" in response.text
    assert "100000" in response.text


def test_csv_export_returns_download(client):
    response = client.get("/reports/stock_balance/export?fmt=csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert "product_name" in response.text
    assert "Widget" in response.text


def test_xlsx_export_returns_download(client):
    response = client.get("/reports/stock_balance/export?fmt=xlsx")
    assert response.status_code == 200
    assert (
        "spreadsheet"
        in response.headers["content-type"]
    )
    assert response.headers["content-disposition"].endswith('.xlsx"')
    assert len(response.content) > 0


def test_report_requires_authentication(client):
    client.post("/logout")
    response = client.get("/reports/stock_balance", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_sales_report_empty_when_no_sales_in_period(client):
    far_future = date.today().replace(year=date.today().year + 1)
    response = client.get(
        f"/reports/sales?date_from={far_future.isoformat()}"
    )
    assert response.status_code == 200
    assert "Нет данных" in response.text


def test_export_with_unsupported_params_does_not_crash(client):
    """Report handlers only accept their declared params; extra query
    params like date_to on a balance report must be silently ignored."""
    today = date.today().isoformat()
    csv_resp = client.get(
        f"/reports/stock_balance/export?fmt=csv&on_date={today}&date_to={today}"
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]

    xlsx_resp = client.get(
        f"/reports/stock_balance/export?fmt=xlsx&on_date={today}&date_to={today}"
    )
    assert xlsx_resp.status_code == 200
    assert "spreadsheet" in xlsx_resp.headers["content-type"]


def test_report_view_with_unsupported_params_does_not_crash(client):
    today = date.today().isoformat()
    response = client.get(
        f"/reports/stock_balance?on_date={today}&date_to={today}&date_from={today}"
    )
    assert response.status_code == 200
    assert "Widget" in response.text

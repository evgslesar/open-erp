from __future__ import annotations

from datetime import date

import pytest
from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.demo import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, ensure_admin_security
from openerp.web.app import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "docs.db"
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
            "counterparty", {"name": "Supplier", "tax_id": "1"}
        )
        product_id = repository.create_catalog_item(
            "product", {"name": "Widget", "sku": "W1", "unit": "pcs"}
        )
        connection.commit_data = {
            "currency_id": currency_id,
            "warehouse_id": warehouse_id,
            "counterparty_id": counterparty_id,
            "product_id": product_id,
        }
    test_client = TestClient(create_app())
    test_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
    )
    yield test_client, connection.commit_data


def _receipt_form(master: dict, qty: str = "10") -> dict:
    return {
        "date": date.today().isoformat(),
        "counterparty_id": str(master["counterparty_id"]),
        "warehouse_id": str(master["warehouse_id"]),
        "comment": "test receipt",
        "lines.0.product_id": str(master["product_id"]),
        "lines.0.quantity": qty,
        "lines.0.price": "100.00",
        "lines.0.amount_minor": str(int(qty) * 10000),
        "lines.0.currency_id": str(master["currency_id"]),
    }


def _sale_form(master: dict, qty: str = "5") -> dict:
    return {
        "date": date.today().isoformat(),
        "counterparty_id": str(master["counterparty_id"]),
        "warehouse_id": str(master["warehouse_id"]),
        "comment": "test sale",
        "lines.0.product_id": str(master["product_id"]),
        "lines.0.quantity": qty,
        "lines.0.price": "150.00",
        "lines.0.amount_minor": str(int(qty) * 15000),
        "lines.0.currency_id": str(master["currency_id"]),
    }


def test_create_document_via_form(client):
    test_client, master = client
    response = test_client.post(
        "/documents/receipt/new", data=_receipt_form(master), follow_redirects=False
    )
    assert response.status_code == 303
    document_url = response.headers["location"]
    assert document_url.startswith("/documents/receipt/")

    view = test_client.get(document_url)
    assert view.status_code == 200
    assert "черновик" in view.text


def test_post_and_unpost_via_web(client):
    test_client, master = client
    create = test_client.post(
        "/documents/receipt/new", data=_receipt_form(master, "10"), follow_redirects=False
    )
    document_url = create.headers["location"]

    posted = test_client.post(document_url + "/post", follow_redirects=False)
    assert posted.status_code == 303
    view = test_client.get(document_url)
    assert "проведён" in view.text

    stock = test_client.get("/reports/stock_balance")
    assert "10" in stock.text

    unposted = test_client.post(document_url + "/unpost", follow_redirects=False)
    assert unposted.status_code == 303
    after = test_client.get(document_url)
    assert "отменён" in after.text


def test_edit_document_after_unpost(client):
    test_client, master = client
    create = test_client.post(
        "/documents/receipt/new", data=_receipt_form(master, "10"), follow_redirects=False
    )
    document_url = create.headers["location"]
    test_client.post(document_url + "/post")
    test_client.post(document_url + "/unpost")

    edit_form = _receipt_form(master, "25")
    updated = test_client.post(document_url + "/edit", data=edit_form, follow_redirects=False)
    assert updated.status_code == 303

    reposted = test_client.post(document_url + "/post", follow_redirects=False)
    assert reposted.status_code == 303
    stock = test_client.get("/reports/stock_balance")
    assert "25" in stock.text


def test_delete_draft_document(client):
    test_client, master = client
    create = test_client.post(
        "/documents/receipt/new", data=_receipt_form(master), follow_redirects=False
    )
    document_url = create.headers["location"]

    deleted = test_client.post(document_url + "/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/documents/receipt"

    journal = test_client.get("/documents/receipt")
    assert "test receipt" not in journal.text


def test_posted_document_blocks_edit_and_delete(client):
    test_client, master = client
    create = test_client.post(
        "/documents/receipt/new", data=_receipt_form(master), follow_redirects=False
    )
    document_url = create.headers["location"]
    test_client.post(document_url + "/post")

    edit = test_client.post(document_url + "/edit", data=_receipt_form(master, "99"))
    assert edit.status_code >= 400

    delete = test_client.post(document_url + "/delete")
    assert delete.status_code >= 400


def test_form_renders_with_reference_dropdowns(client):
    test_client, _ = client
    form_page = test_client.get("/documents/receipt/new")
    assert form_page.status_code == 200
    assert "<select" in form_page.text
    assert "Widget" in form_page.text
    assert "Supplier" in form_page.text


def test_anonymous_cannot_create_documents(client):
    test_client, _ = client
    test_client.post("/logout")
    response = test_client.get("/documents/receipt/new", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_posting_sale_without_stock_returns_business_error(client):
    test_client, master = client
    create = test_client.post(
        "/documents/sale/new", data=_sale_form(master), follow_redirects=False
    )
    document_url = create.headers["location"]
    response = test_client.post(document_url + "/post", follow_redirects=False)
    assert response.status_code == 409
    assert "Internal Server Error" not in response.text
    assert "Negative balance" in response.text or "Операция невозможна" in response.text

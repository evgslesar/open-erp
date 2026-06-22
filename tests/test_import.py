from __future__ import annotations

import io
from datetime import date

import pytest
from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.import_export import (
    catalog_template_rows,
    import_catalog_rows,
    import_initial_stock_rows,
    read_csv_rows,
    read_xlsx_rows,
)
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.demo import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, ensure_admin_security
from openerp.web.app import create_app


@pytest.fixture()
def engine_and_registry(tmp_path, monkeypatch):
    db_path = tmp_path / "import.db"
    monkeypatch.setenv("OPENERP_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENERP_SECRET_KEY", "test-secret")
    engine, registry = init_engine(f"sqlite:///{db_path}")
    with transaction(engine) as connection:
        ensure_admin_security(connection)
    yield engine, registry


@pytest.fixture()
def web_client(engine_and_registry):
    engine, registry = engine_and_registry
    client = TestClient(create_app())
    client.post(
        "/login", data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD}
    )
    yield client


def test_read_csv_rows_from_bytes():
    csv_bytes = b"name,sku,unit\nWidget,W1,pcs\nGadget,G2,pcs\n"
    rows = read_csv_rows(csv_bytes)
    assert len(rows) == 2
    assert rows[0]["name"] == "Widget"
    assert rows[1]["sku"] == "G2"


def test_read_csv_rows_handles_bom():
    csv_bytes = b"\xef\xbb\xbfname,code\nWidget,W1\n"
    rows = read_csv_rows(csv_bytes)
    assert rows[0]["name"] == "Widget"


def test_read_xlsx_rows_from_bytes():
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "sku"])
    sheet.append(["Widget", "W1"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    rows = read_xlsx_rows(buffer.getvalue())
    assert len(rows) == 1
    assert rows[0]["name"] == "Widget"


def test_import_catalog_rows_creates_items(engine_and_registry):
    engine, registry = engine_and_registry
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    rows = [
        {"name": "Tea", "sku": "TEA-1", "unit": "pcs"},
        {"name": "Coffee", "sku": "COF-1", "unit": "pcs"},
    ]
    with transaction(engine) as connection:
        result = import_catalog_rows(connection, registry, context, "product", rows)
    assert result["imported"] == 2
    assert len(result["errors"]) == 0

    with transaction(engine) as connection:
        items = Repository(connection, registry, context).list_catalog_items("product")
    assert len(items) == 2
    names = {item["name"] for item in items}
    assert names == {"Tea", "Coffee"}


def test_import_catalog_rows_reports_errors(engine_and_registry):
    engine, registry = engine_and_registry
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    rows = [
        {"name": "Valid", "sku": "V1", "unit": "pcs"},
        {"name": "NoUnit", "sku": "N1"},
        {"name": "", "sku": "X1", "unit": "pcs"},
    ]
    with transaction(engine) as connection:
        result = import_catalog_rows(connection, registry, context, "product", rows)
    assert result["imported"] == 1
    assert len(result["errors"]) == 2


def test_import_dry_run_does_not_commit(engine_and_registry):
    engine, registry = engine_and_registry
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    rows = [{"name": "DryRun", "sku": "DRY-1", "unit": "pcs"}]
    with transaction(engine) as connection:
        result = import_catalog_rows(
            connection, registry, context, "product", rows, dry_run=True
        )
    assert result["errors"] == []
    with transaction(engine) as connection:
        items = Repository(connection, registry, context).list_catalog_items("product")
    assert len(items) == 0


def test_catalog_template_rows_have_correct_headers(engine_and_registry):
    _, registry = engine_and_registry
    template = catalog_template_rows(registry, "product")
    assert len(template) == 1
    assert "name" in template[0]
    assert "sku" in template[0]
    assert "unit" in template[0]


def test_import_initial_stock_creates_and_posts_document(engine_and_registry):
    engine, registry = engine_and_registry
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        repository.create_catalog_item("warehouse", {"name": "Main"})
        repository.create_catalog_item(
            "product", {"name": "Widget", "sku": "W1", "unit": "pcs"}
        )

    rows = [{"product_sku": "W1", "warehouse_name": "Main", "quantity": "50"}]
    with transaction(engine) as connection:
        result = import_initial_stock_rows(connection, registry, context, rows)

    assert result["imported"] == 1
    assert result["document_id"] is not None
    assert len(result["errors"]) == 0

    from openerp.core.registers import RegisterService

    with transaction(engine) as connection:
        balance = RegisterService(connection, registry, context).balance(
            "stock", date.today()
        )
    assert len(balance) == 1
    assert int(balance[0]["quantity"]) == 50


def test_import_initial_stock_unknown_sku_reports_error(engine_and_registry):
    engine, registry = engine_and_registry
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        repository.create_catalog_item("warehouse", {"name": "Main"})

    rows = [{"product_sku": "UNKNOWN", "warehouse_name": "Main", "quantity": "10"}]
    with transaction(engine) as connection:
        result = import_initial_stock_rows(connection, registry, context, rows)

    assert result["imported"] == 0
    assert len(result["errors"]) == 1
    assert "UNKNOWN" in result["errors"][0]["error"]


def test_web_import_catalog_via_upload(web_client):
    csv_content = b"name,sku,unit\nUploadItem,U1,pcs\n"
    response = web_client.post(
        "/catalogs/product/import",
        files={"file": ("products.csv", csv_content, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "U1" in response.text or "1 строк" in response.text


def test_web_import_template_download(web_client):
    response = web_client.get("/catalogs/product/import-template")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert b"name" in response.content
    assert b"sku" in response.content


def test_web_import_requires_authentication(web_client):
    web_client.post("/logout")
    response = web_client.get("/catalogs/product/import", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")

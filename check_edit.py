from __future__ import annotations

import os
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory

from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.demo import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, ensure_admin_security
from openerp.web.app import create_app


class FormDataParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        value = dict(attrs).get("x-data", "")
        if value.startswith("goodsLinesData("):
            print("FULL x-data:")
            print(value)


def main() -> None:
    original_env = {
        key: os.environ.get(key) for key in ("OPENERP_DATABASE_URL", "OPENERP_SECRET_KEY")
    }
    try:
        with TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "check_edit.db"
            os.environ["OPENERP_DATABASE_URL"] = f"sqlite:///{db_path}"
            os.environ["OPENERP_SECRET_KEY"] = "test-secret"
            engine, registry = init_engine()
            try:
                with transaction(engine) as conn:
                    ensure_admin_security(conn)
                    ctx = RequestContext(user_id=1, organization_id=1, is_admin=True)
                    repo = Repository(conn, registry, ctx)
                    currency_id = repo.create_catalog_item(
                        "currency", {"name": "RUB", "code": "RUB", "scale": 2}
                    )
                    warehouse_id = repo.create_catalog_item("warehouse", {"name": "Main"})
                    counterparty_id = repo.create_catalog_item(
                        "counterparty", {"name": "Customer", "tax_id": "1"}
                    )
                    product_id = repo.create_catalog_item(
                        "product", {"name": "Widget", "sku": "W1", "unit": "pcs"}
                    )
                    sale_id = repo.create_document(
                        "sale",
                        {
                            "date": date.today(),
                            "counterparty_id": counterparty_id,
                            "warehouse_id": warehouse_id,
                        },
                        {
                            "lines": [{
                                "product_id": product_id,
                                "quantity": "5",
                                "price": "100.00",
                                "amount_minor": 50000,
                                "currency_id": currency_id,
                            }]
                        },
                    )
                with TestClient(create_app()) as client:
                    try:
                        client.post(
                            "/login",
                            data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
                        )
                        response = client.get(f"/documents/sale/{sale_id}/edit")
                        response.raise_for_status()
                        FormDataParser().feed(response.text)
                    finally:
                        client.app.state.engine.dispose()
            finally:
                engine.dispose()
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()

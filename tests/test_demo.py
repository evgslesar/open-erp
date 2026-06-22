from __future__ import annotations

from datetime import date

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.db import transaction
from openerp.core.posting import DocumentPostingService
from openerp.core.repository import Repository
from openerp.modules.trade.demo import DEMO_PRODUCT_NAME, seed_demo
from openerp.modules.trade.reports import cash_report


def test_seed_demo_creates_cash_balances(tmp_path):
    db_path = tmp_path / "demo.db"
    engine, registry = init_engine(f"sqlite:///{db_path}")
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)

    with transaction(engine) as connection:
        seed_demo(connection, registry)
        rows = cash_report(connection, registry, context, on_date=date.today())

    balances = {row["money_account_name"]: row["amount_minor"] for row in rows}
    assert balances["Основная касса"] == 55000
    assert balances["Расчётный счёт"] == 495000


def test_seed_demo_adds_cash_to_existing_trade_database(tmp_path):
    db_path = tmp_path / "legacy-demo.db"
    engine, registry = init_engine(f"sqlite:///{db_path}")
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)

    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        poster = DocumentPostingService(connection, registry, context)
        rub_id = repository.create_catalog_item(
            "currency",
            {"name": "Российский рубль", "code": "RUB", "scale": 2},
        )
        warehouse_id = repository.create_catalog_item("warehouse", {"name": "Основной склад"})
        counterparty_id = repository.create_catalog_item(
            "counterparty",
            {"name": "Customer", "tax_id": "1"},
        )
        product_id = repository.create_catalog_item(
            "product",
            {"name": DEMO_PRODUCT_NAME, "sku": "TEA-100", "unit": "шт"},
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
                        "quantity": "10",
                        "price": "100.00",
                        "amount_minor": 100000,
                        "currency_id": rub_id,
                    }
                ]
            },
        )
        poster.post("receipt", receipt_id)
        assert cash_report(connection, registry, context, on_date=date.today()) == []

        seed_demo(connection, registry)
        rows = cash_report(connection, registry, context, on_date=date.today())

    balances = {row["money_account_name"]: row["amount_minor"] for row in rows}
    assert balances["Основная касса"] == 55000
    assert balances["Расчётный счёт"] == 495000


def test_seed_demo_does_not_duplicate_cash_payments(tmp_path):
    db_path = tmp_path / "demo-repeat.db"
    engine, registry = init_engine(f"sqlite:///{db_path}")
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)

    with transaction(engine) as connection:
        seed_demo(connection, registry)
        seed_demo(connection, registry)
        rows = cash_report(connection, registry, context, on_date=date.today())

    assert len(rows) == 2
    balances = {row["money_account_name"]: row["amount_minor"] for row in rows}
    assert balances["Основная касса"] == 55000
    assert balances["Расчётный счёт"] == 495000

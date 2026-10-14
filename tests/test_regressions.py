from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from openerp.core.context import RequestContext
from openerp.core.import_export import import_initial_stock_rows, read_csv_rows
from openerp.core.posting import DocumentPostingService, InsufficientStockError, InvalidPostingError
from openerp.core.registers import RegisterService
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.settlements import unpaid_balance
from openerp.web.app import templates
from tests.test_posting import create_document, create_master_data


def test_header_only_update_preserves_document_lines(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        document_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
        before = repository.get_document("receipt", document_id)

        repository.update_document("receipt", document_id, {"comment": "Updated header"})

        after = repository.get_document("receipt", document_id)
        assert after["comment"] == "Updated header"
        assert after["lines"] == before["lines"]
        DocumentPostingService(connection, registry, context).post("receipt", document_id)
        assert RegisterService(connection, registry, context).balance("stock", date.today())[0][
            "quantity"
        ] == 5


def test_document_line_update_is_recorded_in_audit(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        document_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
        document = repository.get_document("receipt", document_id)
        document["lines"][0].update(quantity="7", amount_minor=70000)
        repository.update_document("receipt", document_id, {}, {"lines": document["lines"]})

        audit = connection.engine._openerp_metadata.tables["sys_audit_log"]
        rows = connection.execute(
            select(audit).where(audit.c.object_type == "document:receipt").order_by(audit.c.id)
        ).mappings().all()
        assert json.loads(rows[0]["after_json"])["lines"][0]["quantity"] == "5"
        assert json.loads(rows[-1]["before_json"])["lines"][0]["quantity"] == "5"
        assert json.loads(rows[-1]["after_json"])["lines"][0]["quantity"] == "7"


def _payment(repository, payment_type, currency_id, counterparty_id, amount, **extra):
    account_type = "cash" if payment_type == "cash_payment" else "bank"
    account_id = repository.create_catalog_item(
        "money_account",
        {
            "name": f"{payment_type}-{currency_id}-{amount}",
            "type": account_type,
            "currency_id": currency_id,
        },
    )
    categories = repository.list_catalog_items("cash_flow_category")
    category_id = categories[0]["id"] if categories else repository.create_catalog_item(
        "cash_flow_category", {"name": "Payments", "kind": "operating"}
    )
    return repository.create_document(
        payment_type,
        {
            "counterparty_id": counterparty_id,
            "money_account_id": account_id,
            "cash_flow_category_id": category_id,
            "currency_id": currency_id,
            "amount_minor": amount,
            "direction": "incoming",
            **extra,
        },
    )


@pytest.mark.parametrize("first_type", ["cash_payment", "bank_payment"])
def test_allocation_counts_other_payment_type_with_same_id(app_state, context, first_type):
    engine, registry = app_state
    second_type = "bank_payment" if first_type == "cash_payment" else "cash_payment"
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
        allocation = {"allocation_document_type": "sale", "allocation_document_id": sale_id}
        first_id = _payment(
            repository, first_type, currency_id, counterparty_id, 6000, **allocation
        )
        second_id = _payment(
            repository, second_type, currency_id, counterparty_id, 5000, **allocation
        )
        assert first_id == second_id
        poster.post(first_type, first_id)
        poster.repost(first_type, first_id)
        assert unpaid_balance(connection, context.organization_id, "sale", sale_id) == 4000
        with pytest.raises(InvalidPostingError, match="превышает"):
            poster.post(second_type, second_id)


@pytest.mark.parametrize("invalid_case", ["direction", "currency", "document_type"])
def test_allocation_rejects_incompatible_payment(app_state, context, invalid_case):
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
        funding_id = _payment(repository, "cash_payment", currency_id, counterparty_id, 20000)
        poster.post("cash_payment", funding_id)
        allocation = {"allocation_document_type": "sale", "allocation_document_id": sale_id}
        if invalid_case == "direction":
            allocation["direction"] = "outgoing"
        elif invalid_case == "currency":
            currency_id = repository.create_catalog_item(
                "currency", {"name": "USD", "code": "USD", "scale": 2}
            )
        else:
            order_id = create_document(
                repository, "order", warehouse_id, counterparty_id, product_id, currency_id, 1
            )
            poster.post("order", order_id)
            allocation.update(allocation_document_type="order", allocation_document_id=order_id)
        payment_id = _payment(
            repository, "cash_payment", currency_id, counterparty_id, 1000, **allocation
        )
        if invalid_case == "direction":
            funded_account = repository.get_document("cash_payment", funding_id)["money_account_id"]
            repository.update_document(
                "cash_payment", payment_id, {"money_account_id": funded_account}
            )
        with pytest.raises(InvalidPostingError):
            poster.post("cash_payment", payment_id)


def test_price_slice_uses_period_before_insertion_order(app_state, context):
    engine, registry = app_state
    today = date.today()
    with transaction(engine) as connection:
        prices = connection.engine._openerp_metadata.tables["ireg_prices"]
        for period, price, organization_id in [
            (today, "100", 1),
            (today, "110", 1),
            (today - timedelta(days=10), "80", 1),
            (today + timedelta(days=1), "120", 1),
            (today, "900", 2),
        ]:
            connection.execute(
                prices.insert().values(
                    period=period,
                    organization_id=organization_id,
                    product_id=1,
                    price_type_id=1,
                    price=price,
                    currency_id=1,
                )
            )
        registers = RegisterService(connection, registry, context)
        assert registers.slice_last("prices", today)[0]["price"] == "110"
        assert registers.slice_last("prices", today - timedelta(days=1))[0]["price"] == "80"


def test_totals_verification_detects_orphans_and_respects_organization(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        totals = connection.engine._openerp_metadata.tables["reg_stock_totals"]
        for organization_id in (1, 2):
            connection.execute(
                totals.insert().values(
                    period_start=date.today().replace(day=1),
                    organization_id=organization_id,
                    warehouse_id=1,
                    product_id=1,
                    quantity="25",
                )
            )
        registers = RegisterService(connection, registry, context)
        discrepancies = registers.verify_totals("stock")
        assert len(discrepancies) == 1
        assert discrepancies[0]["organization_id"] == 1
        assert discrepancies[0]["expected"] == Decimal("0")
        assert discrepancies[0]["actual"] == Decimal("25")
        registers.rebuild_totals("stock")
        assert registers.verify_totals("stock") == []
        assert connection.execute(select(totals.c.organization_id)).scalars().all() == [2]


def test_order_cannot_reserve_product_with_no_stock(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        order_id = create_document(
            repository, "order", warehouse_id, counterparty_id, product_id, currency_id, 1
        )
        with pytest.raises(InsufficientStockError):
            DocumentPostingService(connection, registry, context).post("order", order_id)


class _AttributeParser(HTMLParser):
    value = None

    def handle_starttag(self, tag, attrs):
        if "x-data" in dict(attrs):
            self.value = dict(attrs)["x-data"]


def test_template_json_preserves_quotes_and_escapes_html():
    value = {"name": "O'Reilly </script><script>alert(1)</script>&", "date": date.today()}
    rendered = templates.env.from_string("<div x-data='{{ value | tojson }}'></div>").render(
        value=value
    )
    parser = _AttributeParser()
    parser.feed(rendered)
    assert json.loads(parser.value) == {**value, "date": date.today().isoformat()}
    assert "</script>" not in rendered


def test_csv_file_import_handles_bom(tmp_path):
    path = tmp_path / "products.csv"
    path.write_text("name,sku,unit\nWidget,W1,pcs\n", encoding="utf-8-sig")
    assert read_csv_rows(path)[0]["name"] == "Widget"


@pytest.mark.parametrize("quantity", ["bad", "NaN", "Infinity", "0", 0])
def test_initial_stock_dry_run_validates_quantity(app_state, context, quantity):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        create_master_data(repository)
        result = import_initial_stock_rows(
            connection,
            registry,
            context,
            [{"product_sku": "P1", "warehouse_name": "Main", "quantity": quantity}],
            dry_run=True,
        )
        assert result["imported"] == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["row"] == 2
        assert repository.list_documents_keyset("inventory_adjustment") == []


def test_initial_stock_accepts_numeric_xlsx_quantity(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        create_master_data(repository)
        result = import_initial_stock_rows(
            connection,
            registry,
            context,
            [{"product_sku": "P1", "warehouse_name": "Main", "quantity": 2.5}],
        )
        assert result["imported"] == 1
        assert RegisterService(connection, registry, context).balance("stock", date.today())[0][
            "quantity"
        ] == Decimal("2.5")


def test_document_creation_audit_does_not_require_read_permission(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        permissions = connection.engine._openerp_metadata.tables["sys_permissions"]
        connection.execute(
            permissions.insert().values(
                role_id=1, object_name="document:receipt", operation="create"
            )
        )
        creator = Repository(connection, registry, RequestContext(user_id=1, organization_id=1))
        document_id = create_document(
            creator, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 3
        )
        assert repository.get_document("receipt", document_id)["lines"][0]["quantity"] == "3"


def test_failed_posting_leaves_no_movements_when_error_is_caught(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        sale_id = create_document(
            repository, "sale", warehouse_id, counterparty_id, product_id, currency_id, 1
        )
        with pytest.raises(InsufficientStockError):
            DocumentPostingService(connection, registry, context).post("sale", sale_id)
        registers = RegisterService(connection, registry, context)
        assert registers.movements("stock") == []
        assert registers.movements("settlements") == []
        assert registers.balance("stock", date.today()) == []
        assert repository.get_document("sale", sale_id)["status"] == "draft"


def test_failed_repost_restores_previous_movements(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
        poster = DocumentPostingService(connection, registry, context)
        poster.post("receipt", receipt_id)
        registers = RegisterService(connection, registry, context)
        before = registers.movements("stock")

        def failing_handler(posting_context):
            raise InvalidPostingError("Handler failed")

        registry.posting_handlers["trade.post_receipt"] = failing_handler
        with pytest.raises(InvalidPostingError):
            poster.repost("receipt", receipt_id)
        assert registers.movements("stock") == before
        assert registers.verify_totals("stock") == []
        assert repository.get_document("receipt", receipt_id)["status"] == "posted"


def test_successful_posting_rolls_back_with_outer_transaction(app_state, context):
    engine, registry = app_state
    with transaction(engine) as connection:
        repository = Repository(connection, registry, context)
        currency_id, warehouse_id, counterparty_id, product_id = create_master_data(repository)
        receipt_id = create_document(
            repository, "receipt", warehouse_id, counterparty_id, product_id, currency_id, 5
        )
    with pytest.raises(RuntimeError, match="Abort batch"):
        with transaction(engine) as connection:
            DocumentPostingService(connection, registry, context).post("receipt", receipt_id)
            raise RuntimeError("Abort batch")
    with transaction(engine) as connection:
        assert Repository(connection, registry, context).get_document("receipt", receipt_id)[
            "status"
        ] == "draft"
        assert RegisterService(connection, registry, context).movements("stock") == []

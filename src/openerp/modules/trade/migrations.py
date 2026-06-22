from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from openerp.core.migrations import ModuleMigration
from openerp.core.schema import utcnow


def _table_exists(connection: Connection, table_name: str) -> bool:
    return table_name in inspect(connection).get_table_names()


def _column_names(connection: Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {column["name"] for column in inspect(connection).get_columns(table_name)}


def _add_integer_column(connection: Connection, table_name: str, column_name: str) -> None:
    if column_name in _column_names(connection, table_name):
        return
    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER"))


def _ensure_default_money_account(
    connection: Connection,
    organization_id: int,
    currency_id: int,
    account_type: str,
) -> int:
    table = connection.engine._openerp_metadata.tables["cat_money_account"]
    name = "Основная касса" if account_type == "cash" else "Основной банк"
    existing = connection.execute(
        table.select().where(
            table.c.organization_id == organization_id,
            table.c.type == account_type,
            table.c.currency_id == currency_id,
            table.c.name == name,
            table.c.deletion_mark.is_(False),
        )
    ).first()
    if existing is not None:
        return int(existing._mapping["id"])

    result = connection.execute(
        table.insert().values(
            organization_id=organization_id,
            name=name,
            type=account_type,
            currency_id=currency_id,
            created_by=None,
            updated_by=None,
            created_at=utcnow(),
            updated_at=utcnow(),
            deletion_mark=False,
            revision=1,
        )
    )
    return int(result.inserted_primary_key[0])


def _default_accounts(connection: Connection) -> dict[tuple[int, int, str], int]:
    org_table = connection.engine._openerp_metadata.tables["sys_organizations"]
    currency_table = connection.engine._openerp_metadata.tables["cat_currency"]
    organizations = connection.execute(org_table.select()).fetchall()
    currencies = connection.execute(currency_table.select()).fetchall()
    accounts: dict[tuple[int, int, str], int] = {}
    for organization in organizations:
        organization_id = int(organization._mapping["id"])
        for currency in currencies:
            currency_data = currency._mapping
            if int(currency_data["organization_id"]) != organization_id:
                continue
            currency_id = int(currency_data["id"])
            for account_type in ("cash", "bank"):
                accounts[(organization_id, currency_id, account_type)] = (
                    _ensure_default_money_account(
                        connection,
                        organization_id,
                        currency_id,
                        account_type,
                    )
                )
    return accounts


def _backfill_table(
    connection: Connection,
    table_name: str,
    accounts: dict[tuple[int, int, str], int],
    account_type: str | None = None,
) -> None:
    if not _table_exists(connection, table_name):
        return
    columns = _column_names(connection, table_name)
    if "money_account_id" not in columns or "currency_id" not in columns:
        return
    rows = connection.execute(
        text(
            f"SELECT id, organization_id, currency_id"
            f"{', account_type' if 'account_type' in columns else ''} "
            f"FROM {table_name} WHERE money_account_id IS NULL"
        )
    ).fetchall()
    for row in rows:
        data: dict[str, Any] = dict(row._mapping)
        row_account_type = account_type or str(data.get("account_type") or "cash")
        money_account_id = accounts.get(
            (int(data["organization_id"]), int(data["currency_id"]), row_account_type)
        )
        if money_account_id is None:
            continue
        connection.execute(
            text(f"UPDATE {table_name} SET money_account_id = :money_account_id WHERE id = :id"),
            {"money_account_id": money_account_id, "id": data["id"]},
        )


def _backfill_cash_totals(
    connection: Connection,
    accounts: dict[tuple[int, int, str], int],
) -> None:
    table_name = "reg_cash_totals"
    if not _table_exists(connection, table_name):
        return
    columns = _column_names(connection, table_name)
    required = {
        "period_start",
        "organization_id",
        "account_type",
        "cash_flow_category_id",
        "currency_id",
        "money_account_id",
    }
    if not required.issubset(columns):
        return
    rows = connection.execute(
        text(
            "SELECT period_start, organization_id, account_type, cash_flow_category_id, "
            "currency_id FROM reg_cash_totals WHERE money_account_id IS NULL"
        )
    ).fetchall()
    for row in rows:
        data: dict[str, Any] = dict(row._mapping)
        money_account_id = accounts.get(
            (
                int(data["organization_id"]),
                int(data["currency_id"]),
                str(data["account_type"]),
            )
        )
        if money_account_id is None:
            continue
        connection.execute(
            text(
                "UPDATE reg_cash_totals SET money_account_id = :money_account_id "
                "WHERE period_start = :period_start "
                "AND organization_id = :organization_id "
                "AND account_type = :account_type "
                "AND cash_flow_category_id = :cash_flow_category_id "
                "AND currency_id = :currency_id"
            ),
            {
                "money_account_id": money_account_id,
                "period_start": data["period_start"],
                "organization_id": data["organization_id"],
                "account_type": data["account_type"],
                "cash_flow_category_id": data["cash_flow_category_id"],
                "currency_id": data["currency_id"],
            },
        )


def _recreate_cash_totals_index(connection: Connection) -> None:
    if not _table_exists(connection, "reg_cash_totals"):
        return
    connection.execute(text("DROP INDEX IF EXISTS uq_reg_cash_totals_key"))
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_reg_cash_totals_key "
            "ON reg_cash_totals ("
            "period_start, organization_id, account_type, money_account_id, "
            "cash_flow_category_id, currency_id"
            ")"
        )
    )


def add_money_accounts(connection: Connection) -> None:
    for table_name in (
        "doc_cash_payment",
        "doc_bank_payment",
        "reg_cash_movements",
        "reg_cash_totals",
    ):
        _add_integer_column(connection, table_name, "money_account_id")

    accounts = _default_accounts(connection)
    _backfill_table(connection, "doc_cash_payment", accounts, account_type="cash")
    _backfill_table(connection, "doc_bank_payment", accounts, account_type="bank")
    _backfill_table(connection, "reg_cash_movements", accounts)
    _backfill_cash_totals(connection, accounts)
    _recreate_cash_totals_index(connection)


trade_migrations = [
    ModuleMigration("trade", "20260622_add_money_accounts", add_money_accounts),
]

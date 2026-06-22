from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.posting import DocumentPostingService
from openerp.core.registers import RegisterService
from openerp.core.repository import Repository
from openerp.core.security import hash_password

DEMO_ADMIN_EMAIL = "admin@example.local"
DEMO_ADMIN_PASSWORD = "admin"
DEMO_PRODUCT_NAME = "Чай черный, 100 г"


def ensure_admin_security(connection: Connection) -> None:
    metadata = connection.engine._openerp_metadata
    organizations = metadata.tables["sys_organizations"]
    users = metadata.tables["sys_users"]
    roles = metadata.tables["sys_roles"]
    permissions = metadata.tables["sys_permissions"]
    user_roles = metadata.tables["sys_user_roles"]

    org_id = connection.execute(select(organizations.c.id).limit(1)).scalar_one_or_none()
    if org_id is None:
        org_id = connection.execute(
            organizations.insert().values(name='ООО "Демо Торг"')
        ).inserted_primary_key[0]

    user_id = connection.execute(
        select(users.c.id).where(users.c.email == DEMO_ADMIN_EMAIL).limit(1)
    ).scalar_one_or_none()
    if user_id is None:
        user_id = connection.execute(
            users.insert().values(
                email=DEMO_ADMIN_EMAIL,
                name="Администратор",
                password_hash=hash_password(DEMO_ADMIN_PASSWORD),
                default_organization_id=org_id,
            )
        ).inserted_primary_key[0]
    else:
        connection.execute(
            users.update()
            .where(users.c.email == DEMO_ADMIN_EMAIL)
            .values(
                password_hash=hash_password(DEMO_ADMIN_PASSWORD),
                default_organization_id=org_id,
            )
        )

    role_id = connection.execute(
        select(roles.c.id).where(roles.c.name == "admin")
    ).scalar_one_or_none()
    if role_id is None:
        role_id = connection.execute(roles.insert().values(name="admin")).inserted_primary_key[0]

    if connection.execute(
        select(user_roles.c.user_id).where(user_roles.c.user_id == user_id)
    ).first() is None:
        connection.execute(user_roles.insert().values(user_id=user_id, role_id=role_id))

    object_names = [
        "catalog:organization",
        "catalog:counterparty",
        "catalog:product",
        "catalog:warehouse",
        "catalog:currency",
        "catalog:unit",
        "catalog:cash_flow_category",
        "catalog:money_account",
        "catalog:price_type",
        "document:receipt",
        "document:sale",
        "document:transfer",
        "document:inventory_adjustment",
        "document:order",
        "document:cash_payment",
        "document:bank_payment",
        "system:closed_period",
    ]
    for object_name in object_names:
        for operation in ("create", "read", "update", "delete", "post", "unpost"):
            exists = connection.execute(
                select(permissions.c.id).where(
                    (permissions.c.role_id == role_id)
                    & (permissions.c.object_name == object_name)
                    & (permissions.c.operation == operation)
                )
            ).first()
            if exists is None:
                connection.execute(
                    permissions.insert().values(
                        role_id=role_id,
                        object_name=object_name,
                        operation=operation,
                    )
                )


def _find_catalog_id(repository: Repository, catalog_name: str, name: str) -> int | None:
    for item in repository.list_catalog_items(catalog_name, limit=1000):
        if item["name"] == name:
            return int(item["id"])
    return None


def _get_or_create_catalog(
    repository: Repository,
    catalog_name: str,
    name: str,
    values: dict,
) -> int:
    item_id = _find_catalog_id(repository, catalog_name, name)
    if item_id is not None:
        return item_id
    return repository.create_catalog_item(catalog_name, {"name": name, **values})


def ensure_demo_cash_data(
    connection: Connection,
    registry,
    context: RequestContext,
    repository: Repository,
    poster: DocumentPostingService,
) -> bool:
    registers = RegisterService(connection, registry, context)
    if registers.balance(
        "cash",
        date.today(),
        dimensions=["money_account_id", "currency_id"],
    ):
        return False

    rub_id = _find_catalog_id(repository, "currency", "Российский рубль")
    if rub_id is None:
        for item in repository.list_catalog_items("currency", limit=1000):
            if item.get("code") == "RUB":
                rub_id = int(item["id"])
                break
    if rub_id is None:
        rub_id = repository.create_catalog_item(
            "currency",
            {"name": "Российский рубль", "code": "RUB", "scale": 2},
        )

    supplier_id = _get_or_create_catalog(
        repository,
        "counterparty",
        'ООО "Поставщик Север"',
        {"tax_id": "7701234567"},
    )
    customer_id = _get_or_create_catalog(
        repository,
        "counterparty",
        'ИП Иванов Сергей Петрович',
        {"tax_id": "503212345678"},
    )
    customer_payment_category_id = _get_or_create_catalog(
        repository,
        "cash_flow_category",
        "Оплата покупателей",
        {"kind": "operating"},
    )
    supplier_payment_category_id = _get_or_create_catalog(
        repository,
        "cash_flow_category",
        "Оплата поставщикам",
        {"kind": "operating"},
    )
    cash_account_id = _get_or_create_catalog(
        repository,
        "money_account",
        "Основная касса",
        {"type": "cash", "currency_id": rub_id},
    )
    bank_account_id = _get_or_create_catalog(
        repository,
        "money_account",
        "Расчётный счёт",
        {"type": "bank", "currency_id": rub_id},
    )

    bank_incoming_id = repository.create_document(
        "bank_payment",
        {
            "date": date.today(),
            "counterparty_id": customer_id,
            "money_account_id": bank_account_id,
            "cash_flow_category_id": customer_payment_category_id,
            "direction": "incoming",
            "amount_minor": 495000,
            "currency_id": rub_id,
            "comment": "Оплата покупателя за реализацию",
        },
    )
    poster.post("bank_payment", bank_incoming_id)

    cash_incoming_id = repository.create_document(
        "cash_payment",
        {
            "date": date.today(),
            "counterparty_id": customer_id,
            "money_account_id": cash_account_id,
            "cash_flow_category_id": customer_payment_category_id,
            "direction": "incoming",
            "amount_minor": 80000,
            "currency_id": rub_id,
            "comment": "Розничная выручка в кассу",
        },
    )
    poster.post("cash_payment", cash_incoming_id)

    cash_outgoing_id = repository.create_document(
        "cash_payment",
        {
            "date": date.today(),
            "counterparty_id": supplier_id,
            "money_account_id": cash_account_id,
            "cash_flow_category_id": supplier_payment_category_id,
            "direction": "outgoing",
            "amount_minor": 25000,
            "currency_id": rub_id,
            "comment": "Частичная оплата поставщику из кассы",
        },
    )
    poster.post("cash_payment", cash_outgoing_id)
    return True


def _seed_demo_trade_documents(
    repository: Repository,
    poster: DocumentPostingService,
    rub_id: int,
    main_warehouse_id: int,
    shop_warehouse_id: int,
    supplier_id: int,
    customer_id: int,
    tea_id: int,
    coffee_id: int,
    sugar_id: int,
) -> None:
    receipt_id = repository.create_document(
        "receipt",
        {
            "date": date.today(),
            "counterparty_id": supplier_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Поступление товаров от российского поставщика",
        },
        {
            "lines": [
                {
                    "product_id": tea_id,
                    "quantity": "120",
                    "price": "100.00",
                    "amount_minor": 1200000,
                    "currency_id": rub_id,
                },
                {
                    "product_id": coffee_id,
                    "quantity": "80",
                    "price": "320.00",
                    "amount_minor": 2560000,
                    "currency_id": rub_id,
                },
                {
                    "product_id": sugar_id,
                    "quantity": "200",
                    "price": "75.00",
                    "amount_minor": 1500000,
                    "currency_id": rub_id,
                },
            ]
        },
    )
    poster.post("receipt", receipt_id)

    sale_id = repository.create_document(
        "sale",
        {
            "date": date.today(),
            "counterparty_id": customer_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Реализация покупателю",
        },
        {
            "lines": [
                {
                    "product_id": tea_id,
                    "quantity": "15",
                    "price": "150.00",
                    "amount_minor": 225000,
                    "currency_id": rub_id,
                },
                {
                    "product_id": coffee_id,
                    "quantity": "6",
                    "price": "450.00",
                    "amount_minor": 270000,
                    "currency_id": rub_id,
                },
            ]
        },
    )
    poster.post("sale", sale_id)

    shop_receipt_id = repository.create_document(
        "receipt",
        {
            "date": date.today(),
            "counterparty_id": supplier_id,
            "warehouse_id": shop_warehouse_id,
            "comment": "Оприходование товара в розничном магазине",
        },
        {
            "lines": [
                {
                    "product_id": tea_id,
                    "quantity": "25",
                    "price": "100.00",
                    "amount_minor": 250000,
                    "currency_id": rub_id,
                },
                {
                    "product_id": sugar_id,
                    "quantity": "40",
                    "price": "75.00",
                    "amount_minor": 300000,
                    "currency_id": rub_id,
                },
            ]
        },
    )
    poster.post("receipt", shop_receipt_id)


def seed_demo(connection: Connection, registry) -> None:
    ensure_admin_security(connection)
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    repository = Repository(connection, registry, context)
    poster = DocumentPostingService(connection, registry, context)

    if _find_catalog_id(repository, "product", DEMO_PRODUCT_NAME) is None:
        rub_id = repository.create_catalog_item(
            "currency",
            {"name": "Российский рубль", "code": "RUB", "scale": 2},
        )
        main_warehouse_id = repository.create_catalog_item(
            "warehouse",
            {"name": "Основной склад"},
        )
        shop_warehouse_id = repository.create_catalog_item(
            "warehouse",
            {"name": "Розничный магазин"},
        )
        supplier_id = repository.create_catalog_item(
            "counterparty",
            {"name": 'ООО "Поставщик Север"', "tax_id": "7701234567"},
        )
        customer_id = repository.create_catalog_item(
            "counterparty",
            {"name": 'ИП Иванов Сергей Петрович', "tax_id": "503212345678"},
        )
        tea_id = repository.create_catalog_item(
            "product",
            {"name": DEMO_PRODUCT_NAME, "sku": "TEA-100", "unit": "шт"},
        )
        coffee_id = repository.create_catalog_item(
            "product",
            {"name": "Кофе молотый, 250 г", "sku": "COF-250", "unit": "шт"},
        )
        sugar_id = repository.create_catalog_item(
            "product",
            {"name": "Сахар-песок, 1 кг", "sku": "SUG-001", "unit": "кг"},
        )
        _seed_demo_trade_documents(
            repository,
            poster,
            rub_id,
            main_warehouse_id,
            shop_warehouse_id,
            supplier_id,
            customer_id,
            tea_id,
            coffee_id,
            sugar_id,
        )

    ensure_demo_cash_data(connection, registry, context, repository, poster)

from __future__ import annotations

from datetime import date, timedelta

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


def _doc_date(days_ago: int) -> date:
    return date.today() - timedelta(days=days_ago)


def _goods_line(product_id: int, quantity: str, price: str, rub_id: int) -> dict:
    amount_minor = int(float(quantity) * float(price) * 100)
    return {
        "product_id": product_id,
        "quantity": quantity,
        "price": price,
        "amount_minor": amount_minor,
        "currency_id": rub_id,
    }


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
    supplier_opt_id = _get_or_create_catalog(
        repository,
        "counterparty",
        'АО "ТоргОпт"',
        {"tax_id": "7709876543"},
    )
    customer_id = _get_or_create_catalog(
        repository,
        "counterparty",
        'ИП Иванов Сергей Петрович',
        {"tax_id": "503212345678"},
    )
    cafe_id = _get_or_create_catalog(
        repository,
        "counterparty",
        'ООО "Кафе Уют"',
        {"tax_id": "7705551234"},
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

    payments = [
        (
            "bank_payment",
            {
                "date": _doc_date(11),
                "counterparty_id": customer_id,
                "money_account_id": bank_account_id,
                "cash_flow_category_id": customer_payment_category_id,
                "direction": "incoming",
                "amount_minor": 495000,
                "currency_id": rub_id,
                "comment": "Оплата покупателя за реализацию",
            },
        ),
        (
            "bank_payment",
            {
                "date": _doc_date(7),
                "counterparty_id": cafe_id,
                "money_account_id": bank_account_id,
                "cash_flow_category_id": customer_payment_category_id,
                "direction": "incoming",
                "amount_minor": 800000,
                "currency_id": rub_id,
                "comment": "Частичная оплата от кафе",
            },
        ),
        (
            "cash_payment",
            {
                "date": _doc_date(13),
                "counterparty_id": customer_id,
                "money_account_id": cash_account_id,
                "cash_flow_category_id": customer_payment_category_id,
                "direction": "incoming",
                "amount_minor": 200000,
                "currency_id": rub_id,
                "comment": "Розничная выручка в кассу",
            },
        ),
        (
            "cash_payment",
            {
                "date": _doc_date(6),
                "counterparty_id": customer_id,
                "money_account_id": cash_account_id,
                "cash_flow_category_id": customer_payment_category_id,
                "direction": "incoming",
                "amount_minor": 150000,
                "currency_id": rub_id,
                "comment": "Выручка магазина за выходные",
            },
        ),
        (
            "cash_payment",
            {
                "date": _doc_date(5),
                "counterparty_id": supplier_id,
                "money_account_id": cash_account_id,
                "cash_flow_category_id": supplier_payment_category_id,
                "direction": "outgoing",
                "amount_minor": 25000,
                "currency_id": rub_id,
                "comment": "Частичная оплата поставщику из кассы",
            },
        ),
        (
            "cash_payment",
            {
                "date": _doc_date(3),
                "counterparty_id": supplier_opt_id,
                "money_account_id": cash_account_id,
                "cash_flow_category_id": supplier_payment_category_id,
                "direction": "outgoing",
                "amount_minor": 50000,
                "currency_id": rub_id,
                "comment": "Оплата поставщику за воду и мёд",
            },
        ),
    ]
    for doc_type, header in payments:
        doc_id = repository.create_document(doc_type, header)
        poster.post(doc_type, doc_id)
    return True


def _seed_demo_trade_documents(
    repository: Repository,
    poster: DocumentPostingService,
    rub_id: int,
    main_warehouse_id: int,
    shop_warehouse_id: int,
    supplier_id: int,
    supplier_opt_id: int,
    customer_id: int,
    cafe_id: int,
    petrova_id: int,
    tea_id: int,
    coffee_id: int,
    sugar_id: int,
    milk_id: int,
    cookies_id: int,
    water_id: int,
    honey_id: int,
) -> None:
    main_receipt_id = repository.create_document(
        "receipt",
        {
            "date": _doc_date(25),
            "counterparty_id": supplier_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Поступление товаров от российского поставщика",
        },
        {
            "lines": [
                _goods_line(tea_id, "200", "100.00", rub_id),
                _goods_line(coffee_id, "150", "320.00", rub_id),
                _goods_line(sugar_id, "300", "75.00", rub_id),
                _goods_line(milk_id, "120", "65.00", rub_id),
                _goods_line(cookies_id, "100", "90.00", rub_id),
            ]
        },
    )
    poster.post("receipt", main_receipt_id)

    opt_receipt_id = repository.create_document(
        "receipt",
        {
            "date": _doc_date(22),
            "counterparty_id": supplier_opt_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Поступление напитков и мёда",
        },
        {
            "lines": [
                _goods_line(water_id, "250", "30.00", rub_id),
                _goods_line(honey_id, "60", "280.00", rub_id),
                _goods_line(cookies_id, "80", "88.00", rub_id),
            ]
        },
    )
    poster.post("receipt", opt_receipt_id)

    transfer_id = repository.create_document(
        "transfer",
        {
            "date": _doc_date(18),
            "source_warehouse_id": main_warehouse_id,
            "destination_warehouse_id": shop_warehouse_id,
            "comment": "Пополнение розничного магазина",
        },
        {
            "lines": [
                {"product_id": tea_id, "quantity": "40"},
                {"product_id": water_id, "quantity": "80"},
                {"product_id": cookies_id, "quantity": "30"},
            ]
        },
    )
    poster.post("transfer", transfer_id)

    shop_receipt_id = repository.create_document(
        "receipt",
        {
            "date": _doc_date(16),
            "counterparty_id": supplier_id,
            "warehouse_id": shop_warehouse_id,
            "comment": "Оприходование товара в розничном магазине",
        },
        {
            "lines": [
                _goods_line(tea_id, "30", "100.00", rub_id),
                _goods_line(sugar_id, "50", "75.00", rub_id),
                _goods_line(milk_id, "40", "65.00", rub_id),
            ]
        },
    )
    poster.post("receipt", shop_receipt_id)

    order_id = repository.create_document(
        "order",
        {
            "date": _doc_date(8),
            "counterparty_id": petrova_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Заказ покупателя на чай и мёд",
        },
        {
            "lines": [
                _goods_line(tea_id, "8", "150.00", rub_id),
                _goods_line(honey_id, "10", "350.00", rub_id),
            ]
        },
    )
    poster.post("order", order_id)

    sale_id = repository.create_document(
        "sale",
        {
            "date": _doc_date(12),
            "counterparty_id": customer_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Реализация покупателю",
        },
        {
            "lines": [
                _goods_line(tea_id, "15", "150.00", rub_id),
                _goods_line(coffee_id, "6", "450.00", rub_id),
            ]
        },
    )
    poster.post("sale", sale_id)

    cafe_sale_id = repository.create_document(
        "sale",
        {
            "date": _doc_date(9),
            "counterparty_id": cafe_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Поставка в кафе",
        },
        {
            "lines": [
                _goods_line(coffee_id, "12", "450.00", rub_id),
                _goods_line(milk_id, "30", "85.00", rub_id),
                _goods_line(cookies_id, "25", "120.00", rub_id),
            ]
        },
    )
    poster.post("sale", cafe_sale_id)

    petrova_sale_id = repository.create_document(
        "sale",
        {
            "date": _doc_date(7),
            "counterparty_id": petrova_id,
            "warehouse_id": main_warehouse_id,
            "based_on_order_id": order_id,
            "comment": "Отгрузка по заказу покупателя",
        },
        {
            "lines": [
                _goods_line(tea_id, "8", "150.00", rub_id),
                _goods_line(honey_id, "10", "350.00", rub_id),
            ]
        },
    )
    poster.post("sale", petrova_sale_id)

    retail_sale_id = repository.create_document(
        "sale",
        {
            "date": _doc_date(3),
            "counterparty_id": customer_id,
            "warehouse_id": shop_warehouse_id,
            "comment": "Розничная продажа в магазине",
        },
        {
            "lines": [
                _goods_line(sugar_id, "15", "95.00", rub_id),
                _goods_line(water_id, "50", "45.00", rub_id),
                _goods_line(tea_id, "5", "160.00", rub_id),
            ]
        },
    )
    poster.post("sale", retail_sale_id)

    recent_sale_id = repository.create_document(
        "sale",
        {
            "date": _doc_date(1),
            "counterparty_id": cafe_id,
            "warehouse_id": main_warehouse_id,
            "comment": "Дополнительная поставка напитков",
        },
        {
            "lines": [
                _goods_line(water_id, "40", "45.00", rub_id),
                _goods_line(milk_id, "20", "85.00", rub_id),
            ]
        },
    )
    poster.post("sale", recent_sale_id)


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
        supplier_opt_id = repository.create_catalog_item(
            "counterparty",
            {"name": 'АО "ТоргОпт"', "tax_id": "7709876543"},
        )
        customer_id = repository.create_catalog_item(
            "counterparty",
            {"name": 'ИП Иванов Сергей Петрович', "tax_id": "503212345678"},
        )
        cafe_id = repository.create_catalog_item(
            "counterparty",
            {"name": 'ООО "Кафе Уют"', "tax_id": "7705551234"},
        )
        petrova_id = repository.create_catalog_item(
            "counterparty",
            {"name": "ИП Петрова Мария Владимировна", "tax_id": "503298765432"},
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
        milk_id = repository.create_catalog_item(
            "product",
            {"name": "Молоко ультрапастеризованное, 1 л", "sku": "MLK-001", "unit": "шт"},
        )
        cookies_id = repository.create_catalog_item(
            "product",
            {"name": "Печенье «Юбилейное», 400 г", "sku": "BIS-400", "unit": "шт"},
        )
        water_id = repository.create_catalog_item(
            "product",
            {"name": "Вода минеральная, 0.5 л", "sku": "WTR-050", "unit": "шт"},
        )
        honey_id = repository.create_catalog_item(
            "product",
            {"name": "Мёд цветочный, 250 г", "sku": "HON-250", "unit": "шт"},
        )
        _seed_demo_trade_documents(
            repository,
            poster,
            rub_id,
            main_warehouse_id,
            shop_warehouse_id,
            supplier_id,
            supplier_opt_id,
            customer_id,
            cafe_id,
            petrova_id,
            tea_id,
            coffee_id,
            sugar_id,
            milk_id,
            cookies_id,
            water_id,
            honey_id,
        )

    ensure_demo_cash_data(connection, registry, context, repository, poster)

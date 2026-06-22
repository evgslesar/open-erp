from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.engine import Connection

from openerp.core.context import RequestContext
from openerp.core.posting import DocumentPostingService
from openerp.core.repository import Repository


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

    user_id = connection.execute(select(users.c.id).limit(1)).scalar_one_or_none()
    if user_id is None:
        user_id = connection.execute(
            users.insert().values(email="admin@example.local", name="Администратор")
        ).inserted_primary_key[0]

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
        "document:receipt",
        "document:sale",
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


def seed_demo(connection: Connection, registry) -> None:
    ensure_admin_security(connection)
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    repository = Repository(connection, registry, context)
    poster = DocumentPostingService(connection, registry, context)

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
        {"name": "Чай черный, 100 г", "sku": "TEA-100", "unit": "шт"},
    )
    coffee_id = repository.create_catalog_item(
        "product",
        {"name": "Кофе молотый, 250 г", "sku": "COF-250", "unit": "шт"},
    )
    sugar_id = repository.create_catalog_item(
        "product",
        {"name": "Сахар-песок, 1 кг", "sku": "SUG-001", "unit": "кг"},
    )

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

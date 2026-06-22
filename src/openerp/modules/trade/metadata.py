from __future__ import annotations

from openerp.core.metadata import (
    AccumulationRegisterDef,
    CatalogDef,
    DocumentDef,
    FieldDef,
    FieldType,
    InformationRegisterDef,
    ModuleDef,
    PrintFormDef,
    ReportDef,
    TablePartDef,
)

organization = CatalogDef(
    name="organization",
    label="Организации",
    fields=(FieldDef("tax_id", FieldType.STRING, "ИНН", indexed=True),),
)

counterparty = CatalogDef(
    name="counterparty",
    label="Контрагенты",
    fields=(FieldDef("tax_id", FieldType.STRING, "ИНН", indexed=True),),
)

product = CatalogDef(
    name="product",
    label="Номенклатура",
    fields=(
        FieldDef("sku", FieldType.STRING, "Артикул", indexed=True),
        FieldDef("unit", FieldType.STRING, "Единица", required=True, default="шт"),
    ),
)

warehouse = CatalogDef(name="warehouse", label="Склады", fields=())

currency = CatalogDef(
    name="currency",
    label="Валюты",
    fields=(
        FieldDef("code", FieldType.STRING, "Код", required=True, indexed=True),
        FieldDef("scale", FieldType.INTEGER, "Точность", required=True, default=2),
    ),
)

receipt = DocumentDef(
    name="receipt",
    label="Поступление товаров",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
    ),
    table_parts=(
        TablePartDef(
            name="lines",
            label="Товары",
            fields=(
                FieldDef(
                    "product_id",
                    FieldType.INTEGER,
                    "Номенклатура",
                    required=True,
                    indexed=True,
                ),
                FieldDef("quantity", FieldType.DECIMAL, "Количество", required=True),
                FieldDef("price", FieldType.DECIMAL, "Цена", required=True),
                FieldDef("amount_minor", FieldType.MONEY, "Сумма в копейках", required=True),
                FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True),
            ),
        ),
    ),
    posting_handler="trade.post_receipt",
)

sale = DocumentDef(
    name="sale",
    label="Реализация товаров",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
    ),
    table_parts=receipt.table_parts,
    posting_handler="trade.post_sale",
)

stock_register = AccumulationRegisterDef(
    name="stock",
    label="Товары на складах",
    dimensions=(
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("product_id", FieldType.INTEGER, "Номенклатура", required=True, indexed=True),
    ),
    resources=(FieldDef("quantity", FieldType.DECIMAL, "Количество", required=True),),
)

price_register = InformationRegisterDef(
    name="prices",
    label="Цены номенклатуры",
    dimensions=(FieldDef("product_id", FieldType.INTEGER, "Номенклатура", required=True),),
    resources=(
        FieldDef("price", FieldType.DECIMAL, "Цена", required=True),
        FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True),
    ),
)

trade_module = ModuleDef(
    name="trade",
    version="0.1.0",
    catalogs=(organization, counterparty, product, warehouse, currency),
    documents=(receipt, sale),
    accumulation_registers=(stock_register,),
    information_registers=(price_register,),
    reports=(ReportDef("stock_balance", "Остатки товаров", "trade.stock_balance_report"),),
    print_forms=(
        PrintFormDef("receipt_html", "Поступление HTML", "receipt", "documents/print_form.html"),
        PrintFormDef("sale_html", "Реализация HTML", "sale", "documents/print_form.html"),
    ),
)

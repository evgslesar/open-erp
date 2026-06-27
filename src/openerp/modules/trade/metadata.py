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
        FieldDef("barcode", FieldType.STRING, "Штрихкод", indexed=True),
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

unit = CatalogDef(
    name="unit",
    label="Единицы измерения",
    fields=(
        FieldDef("code", FieldType.STRING, "Код", required=True, indexed=True),
        FieldDef("precision", FieldType.INTEGER, "Точность", required=True, default=0),
    ),
)

cash_flow_category = CatalogDef(
    name="cash_flow_category",
    label="Статьи движения денежных средств",
    fields=(FieldDef("kind", FieldType.STRING, "Вид", required=True, indexed=True),),
)

money_account = CatalogDef(
    name="money_account",
    label="Денежные счета",
    fields=(
        FieldDef("type", FieldType.STRING, "Тип", required=True, default="cash", indexed=True),
        FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True, indexed=True),
    ),
)

price_type = CatalogDef(
    name="price_type",
    label="Типы цен",
    fields=(FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True, indexed=True),),
)

goods_lines = (
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
)

payment_allocation_fields = (
    FieldDef("allocation_document_type", FieldType.STRING, "Тип документа-основания", indexed=True),
    FieldDef("allocation_document_id", FieldType.INTEGER, "Документ-основание", indexed=True),
)

receipt = DocumentDef(
    name="receipt",
    label="Поступление товаров",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("price_type_id", FieldType.INTEGER, "Тип цены", indexed=True),
        FieldDef("based_on_document_id", FieldType.INTEGER, "Заказ поставщику", indexed=True),
    ),
    table_parts=goods_lines,
    posting_handler="trade.post_receipt",
)

sale = DocumentDef(
    name="sale",
    label="Реализация товаров",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("price_type_id", FieldType.INTEGER, "Тип цены", indexed=True),
        FieldDef("based_on_order_id", FieldType.INTEGER, "Заказ покупателя", indexed=True),
    ),
    table_parts=goods_lines,
    posting_handler="trade.post_sale",
)

sale_return = DocumentDef(
    name="sale_return",
    label="Возврат от покупателя",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("price_type_id", FieldType.INTEGER, "Тип цены", indexed=True),
    ),
    table_parts=goods_lines,
    posting_handler="trade.post_sale_return",
)

purchase_return = DocumentDef(
    name="purchase_return",
    label="Возврат поставщику",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("price_type_id", FieldType.INTEGER, "Тип цены", indexed=True),
    ),
    table_parts=goods_lines,
    posting_handler="trade.post_purchase_return",
)

transfer = DocumentDef(
    name="transfer",
    label="Перемещение товаров",
    fields=(
        FieldDef(
            "source_warehouse_id",
            FieldType.INTEGER,
            "Склад-отправитель",
            required=True,
            indexed=True,
        ),
        FieldDef(
            "destination_warehouse_id",
            FieldType.INTEGER,
            "Склад-получатель",
            required=True,
            indexed=True,
        ),
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
            ),
        ),
    ),
    posting_handler="trade.post_transfer",
)

inventory_adjustment = DocumentDef(
    name="inventory_adjustment",
    label="Корректировка остатков",
    fields=(FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),),
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
                FieldDef(
                    "quantity_delta",
                    FieldType.DECIMAL,
                    "Изменение количества",
                    required=True,
                ),
            ),
        ),
    ),
    posting_handler="trade.post_inventory_adjustment",
)

order = DocumentDef(
    name="order",
    label="Заказ покупателя",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("price_type_id", FieldType.INTEGER, "Тип цены", indexed=True),
    ),
    table_parts=goods_lines,
    posting_handler="trade.post_order",
)

purchase_order = DocumentDef(
    name="purchase_order",
    label="Заказ поставщику",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("price_type_id", FieldType.INTEGER, "Тип цены", indexed=True),
    ),
    table_parts=goods_lines,
)

cash_payment = DocumentDef(
    name="cash_payment",
    label="Кассовый платеж",
    fields=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef(
            "money_account_id",
            FieldType.INTEGER,
            "Денежный счет",
            required=True,
            indexed=True,
        ),
        FieldDef(
            "cash_flow_category_id",
            FieldType.INTEGER,
            "Статья ДДС",
            required=True,
            indexed=True,
        ),
        FieldDef("direction", FieldType.STRING, "Направление", required=True, default="outgoing"),
        FieldDef("amount_minor", FieldType.MONEY, "Сумма в копейках", required=True),
        FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True),
        *payment_allocation_fields,
    ),
    posting_handler="trade.post_cash_payment",
)

bank_payment = DocumentDef(
    name="bank_payment",
    label="Банковский платеж",
    fields=cash_payment.fields,
    posting_handler="trade.post_bank_payment",
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

stock_reserved_register = AccumulationRegisterDef(
    name="stock_reserved",
    label="Резерв товаров",
    dimensions=(
        FieldDef("warehouse_id", FieldType.INTEGER, "Склад", required=True, indexed=True),
        FieldDef("product_id", FieldType.INTEGER, "Номенклатура", required=True, indexed=True),
    ),
    resources=(FieldDef("quantity", FieldType.DECIMAL, "Количество", required=True),),
)

settlements_register = AccumulationRegisterDef(
    name="settlements",
    label="Расчеты с контрагентами",
    dimensions=(
        FieldDef("counterparty_id", FieldType.INTEGER, "Контрагент", required=True, indexed=True),
        FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True, indexed=True),
    ),
    resources=(FieldDef("amount_minor", FieldType.MONEY, "Сумма в копейках", required=True),),
    allow_negative=True,
)

cash_register = AccumulationRegisterDef(
    name="cash",
    label="Денежные средства",
    dimensions=(
        FieldDef("account_type", FieldType.STRING, "Тип счета", required=True, indexed=True),
        FieldDef("money_account_id", FieldType.INTEGER, "Денежный счет", required=True, indexed=True),
        FieldDef(
            "cash_flow_category_id",
            FieldType.INTEGER,
            "Статья ДДС",
            required=True,
            indexed=True,
        ),
        FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True, indexed=True),
    ),
    resources=(FieldDef("amount_minor", FieldType.MONEY, "Сумма в копейках", required=True),),
)

price_register = InformationRegisterDef(
    name="prices",
    label="Цены номенклатуры",
    dimensions=(
        FieldDef("product_id", FieldType.INTEGER, "Номенклатура", required=True),
        FieldDef("price_type_id", FieldType.INTEGER, "Тип цены", required=True),
    ),
    resources=(
        FieldDef("price", FieldType.DECIMAL, "Цена", required=True),
        FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True),
    ),
)

currency_rate_register = InformationRegisterDef(
    name="currency_rates",
    label="Курсы валют",
    dimensions=(FieldDef("currency_id", FieldType.INTEGER, "Валюта", required=True),),
    resources=(FieldDef("rate", FieldType.DECIMAL, "Курс", required=True),),
)

trade_module = ModuleDef(
    name="trade",
    version="0.2.0",
    catalogs=(
        organization,
        counterparty,
        product,
        warehouse,
        currency,
        unit,
        cash_flow_category,
        money_account,
        price_type,
    ),
    documents=(
        receipt,
        sale,
        sale_return,
        purchase_return,
        transfer,
        inventory_adjustment,
        order,
        purchase_order,
        cash_payment,
        bank_payment,
    ),
    accumulation_registers=(stock_register, stock_reserved_register, settlements_register, cash_register),
    information_registers=(price_register, currency_rate_register),
    reports=(
        ReportDef("stock_balance", "Остатки товаров", "trade.stock_balance_report"),
        ReportDef("sales", "Продажи", "trade.sales_report"),
        ReportDef("settlements", "Взаиморасчёты", "trade.settlements_report"),
        ReportDef("cash", "Денежные средства", "trade.cash_report"),
        ReportDef("turnover", "Оборотно-сальдовая ведомость", "trade.turnover_report"),
        ReportDef("payment_calendar", "Платежный календарь", "trade.payment_calendar_report"),
    ),
    print_forms=(
        PrintFormDef("receipt_html", "Поступление HTML", "receipt", "documents/print_form.html"),
        PrintFormDef("sale_html", "Реализация HTML", "sale", "documents/print_form.html"),
        PrintFormDef("invoice_html", "Счёт на оплату", "sale", "documents/print_invoice.html"),
        PrintFormDef("act_html", "Акт выполненных работ", "sale", "documents/print_act.html"),
    ),
)

from __future__ import annotations

from openerp.core.metadata import MetadataRegistry
from openerp.modules.trade.metadata import trade_module
from openerp.modules.trade.posting import (
    post_bank_payment,
    post_cash_payment,
    post_inventory_adjustment,
    post_receipt,
    post_sale,
    post_transfer,
)
from openerp.modules.trade.reports import (
    cash_report,
    sales_report,
    settlements_report,
    stock_balance_report,
)


def register(registry: MetadataRegistry) -> None:
    registry.register_module(trade_module)
    registry.register_posting_handler("trade.post_receipt", post_receipt)
    registry.register_posting_handler("trade.post_sale", post_sale)
    registry.register_posting_handler("trade.post_transfer", post_transfer)
    registry.register_posting_handler("trade.post_inventory_adjustment", post_inventory_adjustment)
    registry.register_posting_handler("trade.post_cash_payment", post_cash_payment)
    registry.register_posting_handler("trade.post_bank_payment", post_bank_payment)
    registry.register_report_handler("trade.stock_balance_report", stock_balance_report)
    registry.register_report_handler("trade.sales_report", sales_report)
    registry.register_report_handler("trade.settlements_report", settlements_report)
    registry.register_report_handler("trade.cash_report", cash_report)

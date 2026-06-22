from __future__ import annotations

from openerp.core.metadata import MetadataRegistry
from openerp.modules.trade.metadata import trade_module
from openerp.modules.trade.posting import post_receipt, post_sale
from openerp.modules.trade.reports import stock_balance_report


def register(registry: MetadataRegistry) -> None:
    registry.register_module(trade_module)
    registry.register_posting_handler("trade.post_receipt", post_receipt)
    registry.register_posting_handler("trade.post_sale", post_sale)
    registry.register_report_handler("trade.stock_balance_report", stock_balance_report)

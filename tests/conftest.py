from __future__ import annotations

import pytest

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.db import transaction
from openerp.modules.trade.demo import ensure_admin_security


@pytest.fixture()
def app_state(tmp_path):
    db_path = tmp_path / "test.db"
    engine, registry = init_engine(f"sqlite:///{db_path}")
    with transaction(engine) as connection:
        ensure_admin_security(connection)
    return engine, registry


@pytest.fixture()
def context():
    return RequestContext(user_id=1, organization_id=1, is_admin=True)

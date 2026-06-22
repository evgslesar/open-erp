import os, tempfile, re
db_path = tempfile.NamedTemporaryFile(suffix='.db', delete=False).name
os.environ['OPENERP_DATABASE_URL'] = f'sqlite:///{db_path}'
os.environ['OPENERP_SECRET_KEY'] = 'test-secret'
from datetime import date
from starlette.testclient import TestClient
from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.core.repository import Repository
from openerp.db import transaction
from openerp.modules.trade.demo import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, ensure_admin_security
from openerp.web.app import create_app

engine, registry = init_engine(f'sqlite:///{db_path}')
with transaction(engine) as conn:
    ensure_admin_security(conn)
    ctx = RequestContext(user_id=1, organization_id=1, is_admin=True)
    repo = Repository(conn, registry, ctx)
    currency_id = repo.create_catalog_item('currency', {'name': 'RUB', 'code': 'RUB', 'scale': 2})
    warehouse_id = repo.create_catalog_item('warehouse', {'name': 'Main'})
    counterparty_id = repo.create_catalog_item('counterparty', {'name': 'Customer', 'tax_id': '1'})
    product_id = repo.create_catalog_item('product', {'name': 'Widget', 'sku': 'W1', 'unit': 'pcs'})
    sale_id = repo.create_document(
        'sale',
        {'date': date.today(), 'counterparty_id': counterparty_id, 'warehouse_id': warehouse_id},
        {'lines': [{'product_id': product_id, 'quantity': '5', 'price': '100.00', 'amount_minor': 50000, 'currency_id': currency_id}]},
    )

c = TestClient(create_app())
c.post('/login', data={'email': DEMO_ADMIN_EMAIL, 'password': DEMO_ADMIN_PASSWORD})
r = c.get(f'/documents/sale/{sale_id}/edit')

idx = r.text.find('x-data="{ partName')
print('FULL x-data:')
print(r.text[idx:idx+600])

from __future__ import annotations

from datetime import date

import pytest
from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.core.context import RequestContext
from openerp.db import transaction
from openerp.modules.trade.demo import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    ensure_admin_security,
    seed_demo,
)
from openerp.modules.trade.reports import dashboard_summary, format_money_minor
from openerp.web.app import create_app


@pytest.fixture()
def dashboard_client(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard.db"
    monkeypatch.setenv("OPENERP_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENERP_SECRET_KEY", "test-secret")
    engine, registry = init_engine(f"sqlite:///{db_path}")
    with transaction(engine) as connection:
        ensure_admin_security(connection)
        seed_demo(connection, registry)
    client = TestClient(create_app())
    client.post(
        "/login", data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD}
    )
    yield client, registry, engine


def test_format_money_minor_uses_rubles():
    assert format_money_minor(55000) == "550.00"


def test_dashboard_summary_returns_balances(tmp_path):
    db_path = tmp_path / "summary.db"
    engine, registry = init_engine(f"sqlite:///{db_path}")
    context = RequestContext(user_id=1, organization_id=1, is_admin=True)
    with transaction(engine) as connection:
        ensure_admin_security(connection)
        seed_demo(connection, registry)
        summary = dashboard_summary(connection, registry, context, on_date=date.today())

    assert summary["cash_total_minor"] == 550000
    assert summary["stock_positions"] > 0
    assert len(summary["cash_accounts"]) == 2
    assert summary["cash_chart"]["labels"]


def test_home_page_shows_dashboard_cards(dashboard_client):
    client, _, _ = dashboard_client
    response = client.get("/")
    assert response.status_code == 200
    assert "Денежные средства" in response.text
    assert "Основная касса" in response.text
    assert "cashChart" in response.text
    assert '"Основная касса"' in response.text
    chart_script = response.text.split("chart.umd.min.js\"></script>", 1)[1]
    chart_script = chart_script.split("</script>", 1)[0]
    assert "&#34;" not in chart_script

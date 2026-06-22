from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.core.security import (
    AuthenticationError,
    authenticate,
    hash_password,
    load_user_context,
    set_user_password,
    verify_password,
)
from openerp.db import transaction
from openerp.modules.trade.demo import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    ensure_admin_security,
)
from openerp.web.app import create_app


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "auth.db"
    monkeypatch.setenv("OPENERP_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENERP_SECRET_KEY", "test-secret-key")
    init_engine(f"sqlite:///{db_path}")
    with transaction(init_engine(f"sqlite:///{db_path}")[0]) as connection:
        ensure_admin_security(connection)
    client = TestClient(create_app())
    yield client


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed) is True
    assert verify_password("wrong", hashed) is False
    assert verify_password("s3cret", None) is False


def test_authenticate_rejects_bad_credentials(app_client):
    engine = app_client.app.state.engine
    with transaction(engine) as connection:
        with pytest.raises(AuthenticationError):
            authenticate(connection, DEMO_ADMIN_EMAIL, "wrong")
        with pytest.raises(AuthenticationError):
            authenticate(connection, "nobody@example.local", DEMO_ADMIN_PASSWORD)


def test_load_user_context_marks_admin(app_client):
    engine = app_client.app.state.engine
    with transaction(engine) as connection:
        context = load_user_context(connection, 1)
    assert context.user_id == 1
    assert context.is_admin is True
    assert context.user_email == DEMO_ADMIN_EMAIL


def test_protected_route_redirects_anonymous_user(app_client):
    response = app_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_and_access_protected_route(app_client):
    response = app_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    home = app_client.get("/", follow_redirects=False)
    assert home.status_code == 200
    assert "Администратор" in home.text or "admin@example.local" in home.text


def test_login_with_wrong_password_returns_401(app_client):
    response = app_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": "nope"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Неверный" in response.text


def test_logout_clears_session(app_client):
    app_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
    )
    assert app_client.get("/", follow_redirects=False).status_code == 200

    logout = app_client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"

    after = app_client.get("/", follow_redirects=False)
    assert after.status_code == 303
    assert after.headers["location"].startswith("/login")


def test_set_password_changes_login(app_client):
    engine = app_client.app.state.engine
    with transaction(engine) as connection:
        set_user_password(connection, DEMO_ADMIN_EMAIL, "new-password")

    bad = app_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert bad.status_code == 401

    good = app_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": "new-password"},
        follow_redirects=False,
    )
    assert good.status_code == 303


def test_report_only_visible_after_login(app_client):
    response = app_client.get("/reports/stock_balance", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")

    app_client.post(
        "/login",
        data={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD},
    )
    seeded = app_client.get("/reports/stock_balance")
    assert seeded.status_code == 200
    rows = re.findall(r"<tr>\s*<td>\d+</td>\s*<td>\d+</td>\s*<td>\d+</td>", seeded.text)
    assert len(rows) == 0, "fixture only seeds admin user, no trade documents yet"

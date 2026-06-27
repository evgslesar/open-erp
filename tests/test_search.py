from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from openerp.bootstrap import init_engine
from openerp.db import transaction
from openerp.modules.trade.demo import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    DEMO_PRODUCT_NAME,
    ensure_admin_security,
    seed_demo,
)
from openerp.web.app import create_app


@pytest.fixture()
def search_client(tmp_path, monkeypatch):
    db_path = tmp_path / "search.db"
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
    yield client


def test_search_page(search_client: TestClient) -> None:
    response = search_client.get(f"/search?q={DEMO_PRODUCT_NAME[:5]}")
    assert response.status_code == 200
    assert DEMO_PRODUCT_NAME in response.text


def test_search_page_empty_redirects(search_client: TestClient) -> None:
    response = search_client.get("/search", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_api_search(search_client: TestClient) -> None:
    response = search_client.get(f"/api/search?q={DEMO_PRODUCT_NAME[:5]}&limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any(DEMO_PRODUCT_NAME in item["title"] for item in data["results"])


def test_api_search_is_case_insensitive_for_cyrillic(search_client: TestClient) -> None:
    response = search_client.get("/api/search?q=чай&limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any("чай" in item["title"].casefold() for item in data["results"])


def test_api_search_empty(search_client: TestClient) -> None:
    response = search_client.get("/api/search?q=")
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_api_search_result_keys_are_unique(search_client: TestClient) -> None:
    response = search_client.get("/api/search?q=1&limit=20")
    assert response.status_code == 200
    data = response.json()
    keys = [f"{item['group_key']}:{item['id']}" for item in data["results"]]
    assert len(keys) == len(set(keys))


def test_search_finds_document_comment(search_client: TestClient) -> None:
    response = search_client.get("/search?q=Реализация")
    assert response.status_code == 200
    assert "Реализация покупателю" in response.text


def test_authenticated_layout_includes_search(search_client: TestClient) -> None:
    response = search_client.get("/")
    assert response.status_code == 200
    assert 'aria-label="Поиск"' in response.text
    assert "openPanel" in response.text
    assert "/api/search" in response.text

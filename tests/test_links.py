from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from redis import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import CachedShortLink
from app.models import ShortLink


def test_users_only_see_their_own_links(
    db_client: TestClient, auth_headers: dict[str, str]
) -> None:
    payload1 = {
        "email": "user1@example.com",
        "password": "learning-password",
    }

    payload2 = {
        "email": "user2@example.com",
        "password": "learning-password",
    }

    response1 = db_client.post("/api/v1/auth/signup", json=payload1)
    response2 = db_client.post("/api/v1/auth/signup", json=payload2)

    assert response1.status_code == 201
    assert response2.status_code == 201

    login1 = db_client.post("/api/v1/auth/login", json=payload1)
    login2 = db_client.post("/api/v1/auth/login", json=payload2)

    assert login1.status_code == 200
    assert login2.status_code == 200

    token1 = login1.json()["access_token"]
    token2 = login2.json()["access_token"]
    owner_headers = {"Authorization": f"Bearer {token1}"}
    other_headers = {"Authorization": f"Bearer {token2}"}

    url = {
        "destination_url": "https://example.com/",
    }

    create_link_response = db_client.post("/api/v1/links", json=url, headers=owner_headers)

    assert create_link_response.status_code == 201

    get_link_response1 = db_client.get("/api/v1/links", headers=owner_headers)
    get_link_response2 = db_client.get("/api/v1/links", headers=other_headers)

    assert get_link_response1.status_code == 200
    assert get_link_response2.status_code == 200

    owner_links = get_link_response1.json()
    other_links = get_link_response2.json()

    assert len(owner_links) == 1
    assert owner_links[0]["id"] == create_link_response.json()["id"]
    assert owner_links[0]["destination_url"] == "https://example.com/"
    assert other_links == []

    short_code = create_link_response.json()["short_code"]
    get_short_code_response1 = db_client.get(
        f"/api/v1/links/{short_code}/analytics", headers=owner_headers
    )
    get_short_code_response2 = db_client.get(
        f"/api/v1/links/{short_code}/analytics", headers=other_headers
    )

    assert get_short_code_response1.status_code == 200
    assert get_short_code_response1.json()["click_count"] == 0
    assert get_short_code_response1.json()["short_code"] == short_code
    assert get_short_code_response2.status_code == 404
    assert get_short_code_response2.json() == {"detail": "Short link not found!"}


def test_custom_alias_conflict(
    db_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "destination_url": "https://example.com/",
        "custom_alias": "my-alias",
    }
    response = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert response.status_code == 201
    assert response.json()["short_code"] == "my-alias"

    second_response = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert second_response.status_code == 409
    assert second_response.json() == {"detail": "Custom alias already in use"}


def test_reserved_alias_rejected(
    db_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "destination_url": "https://example.com/",
        "custom_alias": "docs",
    }
    response = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Custom alias is reserved"}


def test_invalid_alias_rejected(
    db_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "destination_url": "https://example.com/",
        "custom_alias": "bad alias",
    }
    response = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "custom_alias"]


def test_past_expiration_rejected(
    db_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "destination_url": "https://example.com/",
        "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    response = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "expires_at"]


def test_redirect_records_click(
    db_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.get_cached_short_link", lambda short_code: None)
    monkeypatch.setattr(
        "app.main.cache_short_link",
        lambda short_code, short_link, ttl_seconds: None,
    )
    payload = {
        "destination_url": "https://example.com/",
    }
    sl = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert sl.status_code == 201

    short_code = sl.json()["short_code"]
    response = db_client.get(f"/{short_code}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/"

    analytics = db_client.get(f"/api/v1/links/{short_code}/analytics", headers=auth_headers)

    assert analytics.status_code == 200
    assert analytics.json()["click_count"] == 1


def test_expired_link_does_not_record_click(
    db_client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "destination_url": "https://example.com/",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }
    link = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert link.status_code == 201

    short_code = link.json()["short_code"]
    sl = db_session.scalar(select(ShortLink).where(ShortLink.short_code == short_code))

    assert sl is not None

    sl.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    db_session.commit()
    monkeypatch.setattr("app.main.get_cached_short_link", lambda short_code: None)

    response = db_client.get(f"/{short_code}", follow_redirects=False)

    assert response.status_code == 410
    assert response.json()["detail"] == "Short link has expired"

    analytics = db_client.get(f"/api/v1/links/{short_code}/analytics", headers=auth_headers)

    assert analytics.status_code == 200
    assert analytics.json()["click_count"] == 0


def test_redirect_works_when_redis_is_unavailable(
    db_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "destination_url": "https://example.com/",
    }
    link = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert link.status_code == 201

    def redis_unavailable(*_args: object) -> None:
        raise RedisError("Redis unavailable")

    monkeypatch.setattr("app.main.get_cached_short_link", redis_unavailable)
    monkeypatch.setattr("app.main.cache_short_link", redis_unavailable)

    short_code = link.json()["short_code"]

    short_link = db_client.get(f"/{short_code}", follow_redirects=False)

    assert short_link.status_code == 307
    assert short_link.headers["location"] == "https://example.com/"

    analytics = db_client.get(f"/api/v1/links/{short_code}/analytics", headers=auth_headers)

    assert analytics.status_code == 200
    assert analytics.json()["click_count"] == 1


def test_redirect_uses_cached_link(
    db_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "destination_url": "https://example.com/",
    }

    sl = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert sl.status_code == 201

    cached_short_link = CachedShortLink(
        id=sl.json()["id"],
        destination_url=sl.json()["destination_url"],
        expires_at=None,
    )

    def unexpected_cache_write(*_args: object) -> None:
        raise AssertionError("A cache hit should not write to Redis")

    monkeypatch.setattr(
        "app.main.get_cached_short_link",
        lambda short_code: cached_short_link,
    )
    monkeypatch.setattr("app.main.cache_short_link", unexpected_cache_write)

    short_code = sl.json()["short_code"]

    cache_hit_link = db_client.get(f"/{short_code}", follow_redirects=False)

    assert cache_hit_link.status_code == 307
    assert cache_hit_link.headers["location"] == "https://example.com/"

    analytics = db_client.get(f"/api/v1/links/{short_code}/analytics", headers=auth_headers)

    assert analytics.status_code == 200
    assert analytics.json()["click_count"] == 1


def test_expired_cached_link_does_not_record_click(
    db_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "destination_url": "https://example.com/",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }
    sl = db_client.post("/api/v1/links", json=payload, headers=auth_headers)

    assert sl.status_code == 201

    cached_short_link = CachedShortLink(
        id=sl.json()["id"],
        destination_url=sl.json()["destination_url"],
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    monkeypatch.setattr(
        "app.main.get_cached_short_link",
        lambda short_code: cached_short_link,
    )

    short_code = sl.json()["short_code"]

    cache_hit_link = db_client.get(f"/{short_code}", follow_redirects=False)

    assert cache_hit_link.status_code == 410
    assert cache_hit_link.json()["detail"] == "Short link has expired"

    analytics = db_client.get(f"/api/v1/links/{short_code}/analytics", headers=auth_headers)

    assert analytics.status_code == 200
    assert analytics.json()["click_count"] == 0


def test_unknown_short_code_returns_404(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr("app.main.get_cached_short_link", lambda short_code: None)

    response = db_client.get("/a-code-that-does-not-exist", follow_redirects=False)

    assert response.status_code == 404
    assert response.json()["detail"] == "Short link not found!"

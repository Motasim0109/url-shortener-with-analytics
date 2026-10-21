from fastapi.testclient import TestClient


def test_signup(db_client: TestClient) -> None:
    payload = {
        "email": "student@example.com",
        "password": "learning-password",
    }
    response = db_client.post("/api/v1/auth/signup", json=payload)

    assert response.status_code == 201

    body = response.json()
    assert body["email"] == "student@example.com"
    assert "id" in body
    assert "created_at" in body
    assert "password" not in body
    assert "hashed_password" not in body


def test_signup_reject_duplicate_email(db_client: TestClient) -> None:
    payload1 = {
        "email": "student@example.com",
        "password": "learning-password",
    }
    payload2 = {
        "email": "Student@Example.com",
        "password": "learning-password",
    }
    response = db_client.post("/api/v1/auth/signup", json=payload1)

    assert response.status_code == 201

    response = db_client.post("/api/v1/auth/signup", json=payload2)

    assert response.status_code == 409
    assert response.json() == {"detail": "Email already registered"}


def test_login_returns_access_token(db_client: TestClient) -> None:
    payload = {
        "email": "student@example.com",
        "password": "learning-password",
    }
    signup_response = db_client.post("/api/v1/auth/signup", json=payload)

    assert signup_response.status_code == 201

    login_response = db_client.post("/api/v1/auth/login", json=payload)

    assert login_response.status_code == 200
    body = login_response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert body["access_token"]

    auth_response = db_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )

    assert auth_response.status_code == 200
    assert auth_response.json()["email"] == payload["email"]


def test_login_rejects_wrong_password(db_client: TestClient) -> None:
    payload = {
        "email": "student@example.com",
        "password": "learning-password",
    }
    payload1 = {
        "email": "student@example.com",
        "password": "wrong-password",
    }

    signup_response = db_client.post("/api/v1/auth/signup", json=payload)

    assert signup_response.status_code == 201

    login_response = db_client.post("/api/v1/auth/login", json=payload1)

    assert login_response.status_code == 401
    assert login_response.json() == {"detail": "Invalid email or password"}
    assert login_response.headers["www-authenticate"] == "Bearer"


def test_login_rejects_unknown_email(db_client: TestClient) -> None:
    payload = {
        "email": "notstudent@example.com",
        "password": "wrong-password",
    }

    login_response = db_client.post("/api/v1/auth/login", json=payload)

    assert login_response.status_code == 401
    assert login_response.json() == {"detail": "Invalid email or password"}
    assert login_response.headers["www-authenticate"] == "Bearer"

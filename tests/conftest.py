import os
from collections.abc import Generator

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from alembic import command

os.environ["ENV_FILE"] = os.devnull
os.environ["DATABASE_URL"] = "postgresql+psycopg://test:test@127.0.0.1:5433/url_shortener_test"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ["JWT_SECRET_KEY"] = "test-only-secret-not-for-production"


@pytest.fixture(scope="session")
def prepare_test_database() -> None:
    from app.database import engine

    if (
        engine.url.host != "127.0.0.1"
        or engine.url.port != 5433
        or engine.url.database != "url_shortener_test"
    ):
        raise RuntimeError("Refusing to migrate a non-test database")
    config = Config("alembic.ini")
    command.upgrade(config, "head")


@pytest.fixture
def db_session(
    prepare_test_database: None,
) -> Generator[Session]:
    from app.database import engine

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            transaction.rollback()


@pytest.fixture
def db_client(db_session: Session) -> Generator[TestClient]:
    from app.database import get_db
    from app.main import app

    def override_get_db() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_headers(db_client: TestClient) -> dict[str, str]:
    payload = {
        "email": "owner@example.com",
        "password": "learning-password",
    }
    signup_response = db_client.post("/api/v1/auth/signup", json=payload)
    assert signup_response.status_code == 201

    login_response = db_client.post("/api/v1/auth/login", json=payload)
    assert login_response.status_code == 200

    access_token = login_response.json()["access_token"]

    return {"Authorization": f"Bearer {access_token}"}

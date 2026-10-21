from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import User
from app.security import hash_password


def test_database_tables_exist(prepare_test_database: None) -> None:
    table_names = inspect(engine).get_table_names()
    assert table_names.__contains__("users")
    assert table_names.__contains__("short_links")
    assert table_names.__contains__("click_events")


def test_db_session_supports_commit(db_session: Session) -> None:
    pwd = hash_password("testpassword")
    user = User(
        email="fixture@example.com",
        hashed_password=pwd,
    )
    db_session.add(user)
    db_session.commit()

    stored_id = db_session.scalar(select(User.id).where(User.email == "fixture@example.com"))
    assert stored_id is not None
    assert stored_id == user.id

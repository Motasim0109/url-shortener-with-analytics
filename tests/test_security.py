from datetime import UTC, datetime, timedelta

import jwt

from app.main import settings
from app.security import decode_access_token, hash_password, verify_password


def test_decode_valid_access_token() -> None:
    payload = {"sub": "1", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    decoded = decode_access_token(token)
    assert decoded == 1


def test_decode_token_with_wrong_signature() -> None:
    payload = {
        "sub": "1",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(
        payload, "wrong-test-secret-not-used-by-the-app", algorithm=settings.jwt_algorithm
    )
    decoded = decode_access_token(token)
    assert decoded is None


def test_verify_password() -> None:
    original_password = "testpassword"
    hashed_password = hash_password(original_password)
    assert verify_password(original_password, hashed_password)
    assert not verify_password("wrongpassword", hashed_password)

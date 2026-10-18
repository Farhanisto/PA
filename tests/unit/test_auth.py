"""Unit tests for auth core: password hashing, sessions, permissions."""

import time

import pytest

from pa.auth import AuthError
from pa.auth.passwords import (
    PasswordError,
    hash_password,
    hash_token,
    normalize_email,
    validate_password,
    verify_password,
)


def test_hash_and_verify_roundtrip():
    encoded = hash_password("Str0ngPassw0rd")
    assert encoded.startswith("v1$scrypt$")
    assert verify_password("Str0ngPassw0rd", encoded)
    assert not verify_password("wrong-password", encoded)


def test_hash_is_salted_and_unique():
    a = hash_password("Str0ngPassw0rd")
    b = hash_password("Str0ngPassw0rd")
    assert a != b  # different salt -> different hash
    assert verify_password("Str0ngPassw0rd", a)
    assert verify_password("Str0ngPassw0rd", b)


def test_hash_never_contains_plaintext():
    encoded = hash_password("Str0ngPassw0rd")
    assert "Str0ngPassw0rd" not in encoded


def test_verify_rejects_malformed():
    for bad in ("", "notahash", "v1$scrypt$x$y$z", "v1$bcrypt$1$1$1$aa$bb", None):
        assert verify_password("whatever", bad) is False


def test_verify_rejects_absurd_params():
    # Tampered record claiming a huge N must not be used (DoS guard).
    forged = "v1$scrypt$999999999$8$1$YWJjZA$YWJjZA"
    assert verify_password("x", forged) is False


def test_password_policy():
    with pytest.raises(PasswordError):
        validate_password("short1A")  # too short
    with pytest.raises(PasswordError):
        validate_password("alllowercase123")  # no uppercase
    with pytest.raises(PasswordError):
        validate_password("ALLUPPERCASE123")  # no lowercase
    with pytest.raises(PasswordError):
        validate_password("NoDigitsHere")  # no digit
    validate_password("Str0ngPassw0rd")  # ok


def test_normalize_email():
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
    for bad in ("", "nope", "@x.com", "a@", "a b@x.com", "a@x"):
        with pytest.raises(ValueError):
            normalize_email(bad)


def test_token_hashing_is_deterministic_and_opaque():
    t = "sometoken"
    assert hash_token(t) == hash_token(t)
    assert hash_token(t) != hash_token(t + "x")
    assert len(hash_token(t)) == 64  # sha256 hex


# --- service level ---


def _db():
    from pa.db.database import get_session_factory

    return get_session_factory()()


def test_signup_login_logout_flow():
    db = _db()
    user, token = __import__("pa.auth", fromlist=["create_user"]).create_user(
        db, email="alice@test.com", password="Str0ngPassw0rd"
    )
    from pa.auth import authenticate, resolve_session, revoke_session

    assert resolve_session(db, token).id == user.id
    # login again
    u2, tok2 = authenticate(db, email="alice@test.com", password="Str0ngPassw0rd")
    assert u2.id == user.id and tok2 != token
    # logout revokes only that token
    assert revoke_session(db, token)
    assert resolve_session(db, token) is None
    assert resolve_session(db, tok2) is not None


def test_duplicate_email_rejected():
    from pa.auth import create_user

    db = _db()
    create_user(db, email="dup@test.com", password="Str0ngPassw0rd")
    with pytest.raises(AuthError, match="already exists"):
        create_user(db, email="dup@test.com", password="Str0ngPassw0rd")


def test_wrong_password_and_unknown_user_same_message():
    from pa.auth import authenticate, create_user

    db = _db()
    create_user(db, email="pw@test.com", password="Str0ngPassw0rd")
    with pytest.raises(AuthError) as e1:
        authenticate(db, email="pw@test.com", password="WrongPassw0rd")
    with pytest.raises(AuthError) as e2:
        authenticate(db, email="nobody@test.com", password="WrongPassw0rd")
    # Identical message -> no user-enumeration oracle.
    assert str(e1.value) == str(e2.value) == "Invalid email or password."


def test_permissions_default_and_toggle():
    from pa.auth import (
        PERM_AUTO_SEND,
        PERM_SEND_EMAIL,
        create_user,
        get_permissions,
        permission_granted,
        set_permission,
    )

    db = _db()
    user, _ = __import__("pa.auth", fromlist=["create_user"]).create_user(
        db, email="perm@test.com", password="Str0ngPassw0rd"
    )
    # Safe defaults.
    assert permission_granted(db, user_id=user.id, key=PERM_SEND_EMAIL)
    assert not permission_granted(db, user_id=user.id, key=PERM_AUTO_SEND)
    # Deny-by-default for unknown key.
    assert not permission_granted(db, user_id=user.id, key="nope.not.a.key")
    # Toggle.
    set_permission(db, user_id=user.id, key=PERM_AUTO_SEND, granted=True)
    assert permission_granted(db, user_id=user.id, key=PERM_AUTO_SEND)


def test_expired_session_rejected():
    from pa.auth import create_user, issue_session, resolve_session
    from pa.db.models import Session as SessionModel

    db = _db()
    user, _ = create_user(db, email="exp@test.com", password="Str0ngPassw0rd")
    tok, session = issue_session(db, user_id=user.id)
    # Force expiry.
    session.expires_at = session.expires_at.replace(year=2000)
    db.commit()
    assert resolve_session(db, tok) is None


def test_inactive_user_rejected():
    from pa.auth import create_user, resolve_session

    db = _db()
    user, tok = create_user(db, email="inactive@test.com", password="Str0ngPassw0rd")
    assert resolve_session(db, tok) is not None
    user.is_active = False
    db.commit()
    assert resolve_session(db, tok) is None


def test_scrypt_password_verification_is_not_trivially_fast():
    # A too-cheap hash would make brute force trivial; assert scrypt params.
    encoded = hash_password("Str0ngPassw0rd")
    parts = encoded.split("$")
    assert int(parts[2]) >= 2**14  # N
    start = time.monotonic()
    verify_password("Str0ngPassw0rd", encoded)
    assert time.monotonic() - start < 1.0  # bounded, but non-trivial

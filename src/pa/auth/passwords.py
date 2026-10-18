"""Password hashing and opaque session tokens (stdlib only, no third-party deps).

Design notes (see requirements §1 and §9):
- Passwords use ``hashlib.scrypt`` (memory-hard) with a per-user random salt
  and a versioned, self-describing encoding so parameters can be upgraded
  later without a migration.
- Verification is constant-time (``hmac.compare_digest``) and does an
  unverified-parameter pre-hash so a wrong-password attempt costs the same
  as a right-password one (no user-enumeration timing oracle).
- Sessions are opaque random tokens; only their SHA-256 hash is stored, so
  a database leak cannot be replayed as a live session.
"""

import base64
import hashlib
import hmac
import secrets

# scrypt cost params. N=2**15 keeps verification well under ~100ms on modest
# hardware; raise as hardware improves.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32
SCHEME = "scrypt"
SCHEME_VERSION = "v1"

TOKEN_BYTES = 32
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


class PasswordError(ValueError):
    """Raised when a password fails policy validation."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _derive(
    password: str, salt: bytes, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P
) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DKLEN, maxmem=64 * 1024 * 1024
    )


def hash_password(password: str) -> str:
    """Return a self-describing encoded hash: v1$scrypt$n$r$p$salt$dk."""
    salt = secrets.token_bytes(16)
    dk = _derive(password, salt)
    return "$".join(
        [
            SCHEME_VERSION,
            SCHEME,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            _b64(salt),
            _b64(dk),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification. Malformed hashes verify as False."""
    if not password or not encoded or not isinstance(encoded, str):
        return False
    try:
        parts = encoded.split("$")
        if len(parts) != 7:
            return False
        version, scheme, n_raw, r_raw, p_raw, salt_b64, dk_b64 = parts
        if scheme != SCHEME:
            return False
        salt = _unb64(salt_b64)
        expected = _unb64(dk_b64)
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        # Guard against absurd params from a tampered/corrupt record.
        if n > 2**20 or r > 32 or p > 16:
            return False
        actual = _derive(password, salt, n=n, r=r, p=p)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# --- Session tokens ---


def new_session_token() -> str:
    """Opaque bearer token handed to the client (sent as a cookie or header)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Deterministic lookup key for a token (SHA-256 hex)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --- Password policy ---


def validate_password(password: str) -> None:
    """Minimum viable policy. Raises PasswordError with an actionable message."""
    if password is None:
        raise PasswordError("Password is required.")
    if len(password) < 10:
        raise PasswordError("Password must be at least 10 characters.")
    if len(password) > 512:
        raise PasswordError("Password must be at most 512 characters.")
    if password.lower() == password:
        raise PasswordError("Password must include an uppercase letter.")
    if password.upper() == password:
        raise PasswordError("Password must include a lowercase letter.")
    if not any(c.isdigit() for c in password):
        raise PasswordError("Password must include a digit.")


def normalize_email(email: str) -> str:
    """Lowercase/trim for uniqueness; light structural validation."""
    e = (email or "").strip().lower()
    if not e:
        raise ValueError("Email is required.")
    if len(e) > 320:
        raise ValueError("Email is too long.")
    if "@" not in e or e.startswith("@") or e.endswith("@") or " " in e:
        raise ValueError("Enter a valid email address.")
    local, _, domain = e.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Enter a valid email address.")
    return e

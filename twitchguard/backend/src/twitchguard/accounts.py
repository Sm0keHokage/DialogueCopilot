"""Local account passwords: stdlib scrypt hashing, no third-party crypto dep.

Twitch credentials are never accepted anywhere in this system (NFR-Sec-01,
AR-01); a local account's password is a completely separate secret that only
ever exists for the личный кабинет (self-service account) and is hashed here
before it ever touches the database.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

#: Password policy enforced by the pydantic body models in api/account.py.
MIN_PASSWORD_LENGTH = 8

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """scrypt(N=16384, r=8, p=1) with a random 16-byte salt.

    Format: "scrypt:<n>:<r>:<p>:<salt-hex>:<hash-hex>" — self-describing so
    the cost parameters can change later without invalidating old hashes.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison against a hash produced by hash_password()."""
    try:
        scheme, n_s, r_s, p_s, salt_hex, hash_hex = stored.split(":")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        n, r, p = int(n_s), int(r_s), int(p_s)
    except ValueError:
        return False
    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
    )
    return hmac.compare_digest(candidate, expected)


def hash_token(token: str) -> str:
    """sha256 hex digest — used to store verify tokens without keeping the
    plaintext bearer token server-side."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_verify_token() -> tuple[str, str]:
    """Return (token, hash_token(token)); only the hash is ever persisted."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


#: Burned on unknown-login attempts so response latency does not leak whether
#: a given login/email exists in the system (timing side channel mitigation).
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))

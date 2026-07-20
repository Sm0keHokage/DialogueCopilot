"""At-rest encryption for Twitch tokens and LLM API keys (FR-07, DR-09, NFR-Sec-02)."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    pass


class TokenCipher:
    """Symmetric encryption; the key comes from ENCRYPTION_KEY env (never from code)."""

    def __init__(self, key: str) -> None:
        if not key:
            raise CryptoError("ENCRYPTION_KEY is not set")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except Exception as exc:  # noqa: BLE001 - normalize any key error
            raise CryptoError("ENCRYPTION_KEY is not a valid Fernet key") from exc

    def encrypt(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, blob: bytes | memoryview) -> str:
        try:
            return self._fernet.decrypt(bytes(blob)).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError("cannot decrypt value (wrong ENCRYPTION_KEY?)") from exc

    def encrypt_str(self, value: str) -> str:
        """Encrypt to an ASCII string (for storage inside JSONB, DR-01)."""
        return self.encrypt(value).decode("ascii")

    def decrypt_str(self, value: str) -> str:
        return self.decrypt(value.encode("ascii"))

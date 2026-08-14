"""
Парольная аутентификация локальных пользователей.

Хэширование — Argon2id (рекомендация OWASP Password Storage Cheat Sheet),
параметры по умолчанию библиотеки argon2-cffi: m=64 МБ, t=3, p=4, len=32,
salt 16 байт. Хэш хранится в стандартном строковом формате PHC:
``$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>``.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_BYTES = 1024

_PASSWORD_HASHER = PasswordHasher()

_PASSWORD_TOO_SHORT = ValueError("Пароль должен содержать не менее 8 символов.")
_PASSWORD_TOO_LONG = ValueError("Пароль слишком длинный.")


def validate_new_password(password: str) -> None:
    """Check the password for minimum length and byte limit."""
    if len(password) < MIN_PASSWORD_CHARS:
        raise _PASSWORD_TOO_SHORT
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise _PASSWORD_TOO_LONG


def hash_password(password: str) -> str:
    """Hashes the password with Argon2id (PHC string with parameters and salt)."""
    validate_new_password(password)
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Verify the password against the stored hash; format mismatch — False."""
    try:
        return _PASSWORD_HASHER.verify(encoded, password)
    except InvalidHashError, VerificationError:
        return False

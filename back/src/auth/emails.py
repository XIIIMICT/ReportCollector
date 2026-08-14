"""Нормализация и валидация email (адреса регистронезависимы)."""

import email_validator


def normalize_email(email: str) -> str:
    """Приводит email к нижнему регистру и убирает пробелы по краям."""
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    """Корректность формата через email-validator (тот же движок, что у EmailStr)."""
    try:
        email_validator.validate_email(email, check_deliverability=False)
    except email_validator.EmailNotValidError:
        return False
    return True

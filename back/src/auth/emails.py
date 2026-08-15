"""Email normalization and validation (addresses are case-insensitive)."""

import email_validator


def normalize_email(email: str) -> str:
    """Lowercases the email and strips surrounding whitespace."""
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    """Format validity via email-validator (the same engine as EmailStr)."""
    try:
        email_validator.validate_email(email, check_deliverability=False)
    except email_validator.EmailNotValidError:
        return False
    return True

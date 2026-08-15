"""App settings: CORS origins normalization."""

import json

import pytest

from src.config import Settings


def test_cors_origins_normalize_trailing_slashes() -> None:
    settings = Settings(backend_cors_origins=["http://a/", "https://b.example//"])
    assert settings.backend_cors_origins == ["http://a", "https://b.example"]


def test_cors_origins_from_env_json_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BACKEND_CORS_ORIGINS",
        json.dumps(["http://localhost:5000/", "https://m2v-form-1.h2-group.ru/"]),
    )
    settings = Settings(_env_file=None)
    assert settings.backend_cors_origins == [
        "http://localhost:5000",
        "https://m2v-form-1.h2-group.ru",
    ]

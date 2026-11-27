"""Credential contract for isolated e2e fixtures and local demo Keycloak."""

from pathlib import Path

import pytest

from src.scripts.seed_database import (
    E2E_CREDENTIAL_ENV,
    RC_SEED_E2E_FIXTURES,
    SEED_E2E_USERS,
    e2e_test_password,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RESERVED_EMAILS = ("user1@test.local", "user2@test.local", "user3@test.local", "user4@test.local")


def test_seed_e2e_users_are_only_reserved_test_identities() -> None:
    assert tuple(str(user["email"]) for user in SEED_E2E_USERS) == RESERVED_EMAILS


def test_e2e_password_error_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(E2E_CREDENTIAL_ENV, raising=False)
    with pytest.raises(ValueError, match=rf"Set {E2E_CREDENTIAL_ENV} when {RC_SEED_E2E_FIXTURES}=1"):
        e2e_test_password()


def test_e2e_password_rejects_short_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(E2E_CREDENTIAL_ENV, "short")
    with pytest.raises(ValueError, match="at least 8 characters"):
        e2e_test_password()


def test_e2e_password_accepts_generated_value(monkeypatch: pytest.MonkeyPatch) -> None:
    generated = "e2e-12345678"
    monkeypatch.setenv(E2E_CREDENTIAL_ENV, generated)
    assert e2e_test_password() == generated


def test_isolated_ci_relies_only_on_generated_e2e_password() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "infra/compose.ci.yaml").read_text(encoding="utf-8")
    assert "AUTH_SEED_ADMIN_EMAIL:" not in compose
    assert "AUTH_SEED_ADMIN_PASSWORD:" not in compose
    assert "ruschem.ru" not in compose
    assert "E2E_TEST_PASSWORD:" in workflow
    assert "github.run_id" in workflow
    assert "-e E2E_TEST_PASSWORD=" in workflow


def test_front_helper_uses_only_reserved_test_identities() -> None:
    helper = (REPO_ROOT / "front/e2e/helpers/test-users.ts").read_text(encoding="utf-8")
    for email in RESERVED_EMAILS:
        assert email in helper
    assert "ruschem.ru" not in helper
    assert E2E_CREDENTIAL_ENV in helper


def test_demo_provisioning_has_no_password_fallback() -> None:
    script = (REPO_ROOT / "infra/keycloak/provision-renovation-users.ps1").read_text(encoding="utf-8")
    assert "$env:RENOVATION_SEED_PASSWORD" in script
    guard = script.index("RENOVATION_SEED_PASSWORD is required")
    first_kcadm_call = script.index("Invoke-Kc config credentials")
    assert guard < first_kcadm_call

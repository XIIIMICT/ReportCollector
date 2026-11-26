"""Privacy contract for application logs (RC-5).

Distinctive synthetic sentinels stand in for every identity claim and payload
value. None of them may reach an emitted JSON log line; correlation fields and
stable failure classifications must survive.
"""

import ast
import io
import json
import logging
from pathlib import Path
from typing import Any, Never, cast
from uuid import uuid4

import pytest

from src.app_logging import SAFE_LOG_FIELDS, JsonFormatter, log_error, log_event

SENTINEL = "5d9c41b7-privacy-sentinel"
SENTINEL_EMAIL = f"{SENTINEL}@example.ru"
PASSWORD = "StrongSentinel123"  # noqa: S105 - synthetic password, never a credential

FORBIDDEN_FIELDS = (
    "user_email",
    "user_name",
    "user_sub",
    "oidc_issuer",
    "preferred_username",
    "given_name",
    "family_name",
    "email_verified",
    "user_is_admin",
    "bootstrap_is_admin",
    "login_email",
    "extra_origin",
    "client_id",
    "error",
)
CORRELATION_FIELDS = ("request_id", "job_id", "operation", "outcome", "error_code", "error_type")


class LogCapture:
    """Capture JSON Lines emitted through the production formatter."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()
        handler = logging.StreamHandler(self.buffer)
        handler.setFormatter(JsonFormatter())
        self.logger = logging.getLogger(f"rc.privacy.{uuid4().hex}")
        self.logger.handlers.clear()
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.addHandler(handler)

    @property
    def text(self) -> str:
        return self.buffer.getvalue()

    @property
    def lines(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.text.splitlines() if line]


@pytest.fixture
def capture():
    instance = LogCapture()
    yield instance
    for handler in instance.logger.handlers[:]:
        instance.logger.removeHandler(handler)
        handler.close()


class FakeSocket:
    """Only the handshake surface _serve_room touches before rejecting."""

    def __init__(self, headers=None, query_params=None) -> None:
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.closed_with: int | None = None

    async def close(self, code: int) -> None:
        self.closed_with = code


class FakeSession:
    """Access checks are injected; the rejection path only closes the session."""

    async def close(self) -> None:
        return None


async def _deny(_session, _identity) -> Never:
    from src.collab.access import RoomAccessDeniedError

    raise RoomAccessDeniedError


def test_formatter_allowlist_contains_only_bounded_fields() -> None:
    assert not set(FORBIDDEN_FIELDS) & set(SAFE_LOG_FIELDS)
    assert set(CORRELATION_FIELDS) <= set(SAFE_LOG_FIELDS)


def test_formatter_drops_identity_and_payload_fields_even_if_passed(capture) -> None:
    log_event(
        capture.logger,
        "sentinel_identity",
        "test",
        request_id="req-1",
        job_id="job-1",
        **{name: f"{SENTINEL}-{name}" for name in FORBIDDEN_FIELDS},
    )

    assert SENTINEL not in capture.text
    first = capture.lines[0]
    assert first["event"] == "sentinel_identity"
    assert first["request_id"] == "req-1"
    assert first["job_id"] == "job-1"
    assert not set(FORBIDDEN_FIELDS) & set(first)


def test_failure_diagnostics_keep_type_and_code_without_message(capture) -> None:
    error = RuntimeError(f"{SENTINEL} file-contents key sql-parameters")

    log_error(
        capture.logger,
        "sentinel_failure",
        "test",
        job_id="job-2",
        error_code="generation_failed",
        error_type=type(error).__name__,
    )

    assert SENTINEL not in capture.text
    first = capture.lines[0]
    assert first["error_type"] == "RuntimeError"
    assert first["error_code"] == "generation_failed"
    assert first["job_id"] == "job-2"


def test_no_log_call_site_passes_identity_or_payload_fields() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    violations: list[str] = []

    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in {"log_event", "log_warning", "log_error"}:
                continue
            for keyword in node.keywords:
                if keyword.arg in FORBIDDEN_FIELDS:
                    violations.append(f"{path.relative_to(source_root)}:{node.lineno} {keyword.arg}")

    assert not violations


async def test_oidc_events_never_log_claims(db_session, monkeypatch, capture) -> None:
    from src.auth import service
    from src.auth.service import CurrentUserIdentity

    monkeypatch.setattr(service, "LOGGER", capture.logger)
    identity = CurrentUserIdentity(
        email=SENTINEL_EMAIL,
        email_verified=True,
        family_name=f"{SENTINEL}-family",
        given_name=f"{SENTINEL}-given",
        issuer=f"{SENTINEL}-issuer",
        name=f"{SENTINEL}-name",
        oidc_sub=f"{SENTINEL}-sub",
        preferred_username=f"{SENTINEL}-preferred",
    )

    user = await service.upsert_current_user(db_session, identity, create=True)
    assert user is not None
    user.is_deleted = True
    db_session.add(user)
    await db_session.commit()
    with pytest.raises(service.BlockedUserError):
        await service.upsert_current_user(db_session, identity, create=True)

    assert SENTINEL not in capture.text
    events = [line["event"] for line in capture.lines]
    assert "keycloak_identity_received" in events
    assert "user_created" in events
    assert "blocked_user_attempt" in events


async def test_local_auth_events_never_log_email(db_session, monkeypatch, capture) -> None:
    from src.auth import service
    from src.auth.models import User

    monkeypatch.setattr(service, "LOGGER", capture.logger)

    with pytest.raises(service.InvalidCredentialsError):
        await service.authenticate_local_user(db_session, SENTINEL_EMAIL, "wrong-password")

    user = User(email=SENTINEL_EMAIL, password_hash=None)
    db_session.add(user)
    await db_session.commit()

    with pytest.raises(service.InvalidCredentialsError):
        await service.authenticate_local_user(db_session, SENTINEL_EMAIL, "short")
    await service.authenticate_local_user(db_session, SENTINEL_EMAIL, PASSWORD)

    assert SENTINEL not in capture.text
    events = [line["event"] for line in capture.lines]
    assert events.count("local_login_failed") == 2
    assert "local_password_set" in events
    assert "local_login_succeeded" in events


async def test_ws_identity_rejection_drops_client_id(monkeypatch, capture) -> None:
    from src.collab import ws

    monkeypatch.setattr(ws, "logger", capture.logger)
    socket = FakeSocket(query_params={"clientId": f"{SENTINEL}-client"})

    await ws._serve_room(
        cast("Any", socket),
        cast("Any", FakeSession()),
        resolve_access=_deny,
        default_section="materialMovement",
        default_project="raw_materials",
    )

    assert socket.closed_with == 4401
    assert SENTINEL not in capture.text
    assert capture.lines[0]["event"] == "collab.ws_identity_rejected"


async def test_ws_access_rejection_drops_identity_claims(monkeypatch, capture) -> None:
    from conftest import signed_identity_headers

    from src.collab import ws

    monkeypatch.setattr(ws, "logger", capture.logger)
    headers = signed_identity_headers(
        email=SENTINEL_EMAIL,
        name=f"{SENTINEL}-name",
        subject=f"{SENTINEL}-sub",
        issuer=f"{SENTINEL}-issuer",
        preferred_username=f"{SENTINEL}-preferred",
        given_name=f"{SENTINEL}-given",
        family_name=f"{SENTINEL}-family",
        email_verified="true",
    )
    socket = FakeSocket(headers=headers, query_params={"reportMonth": "2026-01-01"})

    await ws._serve_room(
        cast("Any", socket),
        cast("Any", FakeSession()),
        resolve_access=_deny,
        default_section="materialMovement",
        default_project="raw_materials",
    )

    assert socket.closed_with == 4403
    assert SENTINEL not in capture.text
    assert capture.lines[0]["event"] == "collab.ws_access_rejected"

"""Privacy contract for application logs (RC-5).

Distinctive synthetic sentinels stand in for every identity claim and payload
value. None of them may reach an emitted JSON log line; correlation fields and
stable failure classifications must survive.
"""

import ast
import asyncio
import io
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, cast
from uuid import uuid4

import pytest
from starlette.types import Message, Scope

from src.app_logging import SAFE_LOG_FIELDS, JsonFormatter, log_error, log_event, log_failure, safe_error_type

SENTINEL = "5d9c41b7-privacy-sentinel"
SENTINEL_EMAIL = f"{SENTINEL}@example.ru"
PASSWORD = "StrongSentinel123"  # noqa: S105 - synthetic password, never a credential
CAUSE_SENTINEL = "cause-5a31-SENTINEL"
TRACEBACK_SENTINEL = "traceback-7e42-SENTINEL"
FIELD_SENTINEL = "field-9c08-SENTINEL"
ALL_SENTINELS = (SENTINEL, CAUSE_SENTINEL, TRACEBACK_SENTINEL, FIELD_SENTINEL)

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


def _raise_nested() -> None:
    """Raise a chained error whose message, cause and traceback frame carry sentinels."""
    local_secret = f"local-{FIELD_SENTINEL}"
    inner_message = f"inner-{CAUSE_SENTINEL}-{local_secret}"
    outer_message = f"outer-{TRACEBACK_SENTINEL}"
    try:
        raise ValueError(inner_message)  # noqa: TRY301 - deliberate nested cause for the sentinel test
    except ValueError as inner:
        raise RuntimeError(outer_message) from inner


def _assert_no_sentinels(text: str) -> None:
    for secret in ALL_SENTINELS:
        assert secret not in text


def test_formatter_replaces_exc_info_with_stable_classification(capture) -> None:
    try:
        _raise_nested()
    except RuntimeError as error:
        record = logging.LogRecord(
            capture.logger.name,
            logging.ERROR,
            __file__,
            42,
            "Failed to process stage.",
            (),
            (type(error), error, error.__traceback__),
        )
    record.request_id = "req-classified"
    record.job_id = "job-classified"
    record.phase = "generate"
    record.error = f"forbidden-{FIELD_SENTINEL}"

    payload = json.loads(JsonFormatter().format(record))

    text = json.dumps(payload, ensure_ascii=False)
    _assert_no_sentinels(text)
    assert "exception" not in payload
    assert "error" not in payload
    assert payload["message"] == "Failed to process stage."
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_code"] == "operation_failed"
    assert payload["request_id"] == "req-classified"
    assert payload["job_id"] == "job-classified"
    assert payload["phase"] == "generate"


def test_log_failure_is_classified_without_attaching_exception_text(capture) -> None:
    try:
        _raise_nested()
    except RuntimeError as error:
        log_failure(
            capture.logger,
            "sentinel_failure_event",
            "Static failure message.",
            error,
            request_id="req-failure",
            job_id="job-failure",
            phase="dispatch",
            error_code="dispatch_failed",
            **{name: f"{name}-{FIELD_SENTINEL}" for name in FORBIDDEN_FIELDS if name != "error"},
        )

    _assert_no_sentinels(capture.text)
    first = capture.lines[0]
    assert first["event"] == "sentinel_failure_event"
    assert first["message"] == "Static failure message."
    assert first["error_type"] == "RuntimeError"
    assert first["error_code"] == "dispatch_failed"
    assert first["request_id"] == "req-failure"
    assert first["job_id"] == "job-failure"
    assert first["phase"] == "dispatch"
    assert not set(FORBIDDEN_FIELDS) & set(first)


def test_safe_error_type_never_emits_untrusted_text() -> None:
    assert safe_error_type(RuntimeError("anything")) == "RuntimeError"
    assert safe_error_type(RuntimeError) == "RuntimeError"
    assert safe_error_type(f"custom-{SENTINEL}Error") == "Exception"
    assert safe_error_type({"error": SENTINEL}) == "Exception"


async def test_request_middleware_failure_logs_classification_not_raw_path(monkeypatch, capture) -> None:
    from src import observability

    monkeypatch.setattr(observability, "LOGGER", capture.logger)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: Message) -> None:
        return None

    async def application(_scope: Scope, _receive: object, _send: object) -> None:
        _raise_nested()

    middleware = observability.RequestMetricsMiddleware(cast("Any", application))
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/secret/{FIELD_SENTINEL}",
        "route": SimpleNamespace(path="/api/secret/{id}"),
    }
    with pytest.raises(RuntimeError):
        await middleware(scope, receive, send)

    _assert_no_sentinels(capture.text)
    failure = next(line for line in capture.lines if line["event"] == "http_request_exception")
    assert failure["error_type"] == "RuntimeError"
    assert failure["error_code"] == "request_failed"
    assert failure["phase"] == "request"
    assert failure["route"] == "/api/secret/{id}"
    assert failure["request_id"]
    assert "path" not in failure
    assert all("path" not in line for line in capture.lines)


async def test_outbox_failure_logs_classification_not_exception_text(monkeypatch, capture) -> None:
    from src.collab import outbox

    monkeypatch.setattr(outbox, "logger", capture.logger)

    async def fail_once() -> bool:
        message = f"outbox-{CAUSE_SENTINEL}"
        raise RuntimeError(message)

    class StopLoop(BaseException):
        pass

    async def stop(_delay: float) -> None:
        raise StopLoop

    monkeypatch.setattr(outbox, "dispatch_once", fail_once)
    monkeypatch.setattr(outbox.asyncio, "sleep", stop)
    with pytest.raises(StopLoop):
        await outbox.run_outbox()

    _assert_no_sentinels(capture.text)
    failure = capture.lines[0]
    assert failure["event"] == "outbox.dispatch_failed"
    assert failure["error_type"] == "RuntimeError"
    assert failure["error_code"] == "outbox_dispatch_failed"
    assert failure["phase"] == "dispatch"


async def test_ws_failures_log_classification_not_exception_text(monkeypatch, capture) -> None:
    from src.collab import ws

    monkeypatch.setattr(ws, "logger", capture.logger)

    async def broken_subscribe(_room: str, *, ready: asyncio.Event) -> AsyncIterator[dict[str, str]]:
        ready.set()
        message = f"relay-{CAUSE_SENTINEL}"
        raise RuntimeError(message)
        yield {}  # pragma: no cover - makes this an async generator

    manager = ws.RoomManager(cast("Any", SimpleNamespace(subscribe=broken_subscribe)))
    room = "raw_materials:materialMovement:2026-01:"
    manager._ready[room] = asyncio.Event()
    await manager._relay(room)

    class FailingSocket:
        def __init__(self) -> None:
            self.closed_with: int | None = None

        async def send_json(self, _message: object) -> None:
            message = f"writer-{TRACEBACK_SENTINEL}"
            raise RuntimeError(message)

        async def close(self, code: int) -> None:
            self.closed_with = code

    socket = FailingSocket()
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait((0.0, {"type": "ping"}))
    await manager._writer(room, cast("Any", socket), queue)

    _assert_no_sentinels(capture.text)
    events = {line["event"]: line for line in capture.lines}
    assert events["collab.ws_relay_failed"]["error_type"] == "RuntimeError"
    assert events["collab.ws_relay_failed"]["phase"] == "relay"
    assert events["collab.ws_writer_failed"]["error_type"] == "RuntimeError"
    assert events["collab.ws_writer_failed"]["phase"] == "writer"
    assert socket.closed_with == 1013


async def test_export_subprocess_failure_bounds_child_error_type(monkeypatch, capture) -> None:
    from src.common import export_process
    from src.common.export_jobs import ExportJob, ExportSpec

    def capture_error(_logger: logging.Logger, event: str, message: str, **fields: object) -> None:
        log_error(capture.logger, event, message, **fields)

    async def lines() -> AsyncIterator[bytes]:
        yield json.dumps({"event": "failed", "retryable": False, "error": f"child-{FIELD_SENTINEL}"}).encode()

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = lines()
            self.returncode = 1

        async def wait(self) -> int:
            return 1

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    async def spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr(export_process, "log_error", capture_error)
    monkeypatch.setattr(export_process.asyncio, "create_subprocess_exec", spawn)
    job = ExportJob(id="job-export", user_id="user-1", spec=ExportSpec(file_name="x.xlsx", total=1, cache_key="k"))

    with pytest.raises(export_process.ExportProcessError):
        await export_process.generate_in_process(job)

    _assert_no_sentinels(capture.text)
    failure = capture.lines[0]
    assert failure["event"] == "export.process_failed"
    assert failure["error_type"] == "Exception"
    assert failure["error_code"] == "export_process_failed"
    assert failure["job_id"] == "job-export"


_FORBIDDEN_LOG_METHODS = frozenset({"exception"})
_FORBIDDEN_TRACE_METHODS = frozenset({"formatException", "format_exc", "format_exception", "print_exc"})


def _log_guard_violations() -> list[str]:
    """Inspect the AST of every backend source file for log-leak regressions."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_LOG_METHODS | _FORBIDDEN_TRACE_METHODS:
                violations.append(f"{relative}:{node.lineno} forbidden attribute .{node.attr}")
            if isinstance(node, ast.Attribute) and node.attr == "exc_text":
                violations.append(f"{relative}:{node.lineno} formatter reads exc_text")
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_LOG_METHODS | _FORBIDDEN_TRACE_METHODS:
                violations.append(f"{relative}:{node.lineno} forbidden name {node.id}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and any(
                    isinstance(arg, ast.Constant) and arg.value in _FORBIDDEN_LOG_METHODS | _FORBIDDEN_TRACE_METHODS
                    for arg in node.args[1:]
                )
            ):
                violations.append(f"{relative}:{node.lineno} getattr alias to a forbidden log method")
            if isinstance(node, ast.keyword) and node.arg == "exc_info":
                violations.append(f"{relative}:{node.lineno} exc_info attached to a log call")
            # Only the exception TYPE (index 0) may be read from exc_info; the
            # value at 1, traceback at 2 and frame locals must stay untouched.
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "exc_info"
                and not (isinstance(node.slice, ast.Constant) and node.slice.value == 0)
            ):
                violations.append(f"{relative}:{node.lineno} exc_info value or traceback read")
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in {"log_event", "log_warning", "log_error", "log_failure"}:
                    message_index = 2
                elif name in {"info", "warning", "error", "debug", "critical"}:
                    message_index = 0
                elif name == "log":
                    message_index = 1
                else:
                    continue
                if len(node.args) > message_index and relative != "app_logging.py":
                    message = node.args[message_index]
                    if not (isinstance(message, ast.Constant) and isinstance(message.value, str)):
                        violations.append(f"{relative}:{node.lineno} dynamic log message")
    return violations


def test_no_exception_serialization_or_dynamic_log_messages() -> None:
    violations = _log_guard_violations()
    assert not violations

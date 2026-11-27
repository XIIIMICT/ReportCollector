"""Scheduled pg_dump sidecar with a restore drill and a narrow metrics endpoint.

Modes (first positional argument, default ``daemon``):

* ``daemon`` — daily full dump + weekly restore drill, forever.
* ``backup`` — one full dump + retention + optional off-host copy; exits with
  the failing PostgreSQL tool's return code (0 on success).
* ``drill`` — restore the newest dump into a scratch database, run a DDL/row
  assertion, drop the scratch database; exits non-zero on failure.

Only the Python stdlib and the PostgreSQL client tools from the base image are
used. State is persisted next to the dumps so metrics and "already done today"
survive a container restart.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXIT_CONFIG = 2
EXIT_FAILURE = 1

IDENTITY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DUMP_SUFFIX = ".dump"
PART_SUFFIX = ".part"
YEAR_SECONDS = 31_536_000  # sentinel "never" for age gauges

OPS = ("dump", "verify", "offhost", "prune", "drill")


class Config:
    """Environment-driven settings; invalid values fail before any work starts."""

    def __init__(self) -> None:
        self.host = env("POSTGRES_HOST", "postgres")
        self.port = env_int("POSTGRES_PORT", 5432, 1, 65535)
        self.user = env("POSTGRES_USER", "postgres")
        self.password = env("POSTGRES_PASSWORD", "")
        self.db = env("POSTGRES_DB", "")
        self.schema = env("POSTGRES_SCHEMA", "rc")
        if not IDENTITY.fullmatch(self.schema):
            raise ValueError("POSTGRES_SCHEMA must be a plain SQL identifier")
        self.backup_dir = Path(env("BACKUP_DIR", "/var/backups/reportcollector"))
        self.retention = env_int("BACKUP_RETENTION_COUNT", 7, 1, 365)
        self.backup_hour = env_int("BACKUP_HOUR_UTC", 2, 0, 23)
        self.drill_hour = env_int("BACKUP_DRILL_HOUR_UTC", 3, 0, 23)
        self.drill_weekday = env_int("BACKUP_DRILL_WEEKDAY", 6, 0, 6)
        self.offhost = env("BACKUP_OFFHOST_TARGET", "")
        if self.offhost and "{file}" not in self.offhost:
            raise ValueError("BACKUP_OFFHOST_TARGET must contain exactly one {file} token")
        self.metrics_port = env_int("BACKUP_METRICS_PORT", 9113, 1, 65535)
        if not self.db or not self.password:
            raise ValueError("POSTGRES_DB and POSTGRES_PASSWORD are required")

    def psql_env(self) -> dict[str, str]:
        child = os.environ.copy()
        child["PGPASSWORD"] = self.password
        return child

    def conn_args(self) -> list[str]:
        return ["-h", self.host, "-p", str(self.port), "-U", self.user]


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not low <= value <= high:
        raise ValueError(f"{name} must be in [{low}, {high}]")
    return value


def now_ts() -> float:
    return time.time()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class State:
    """Persistent, lock-protected status of the sidecar."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        boot = now_ts()
        self.data: dict[str, object] = {
            "last_backup_date": "",
            "last_success": boot,
            "last_drill_week": "",
            "drill_last_success": boot,
            "drill_last_failure": 0.0,
            "last_failure": {},
            "last_duration": {},
            "last_size": 0,
            "files_retained": 0,
            "backup_success": 0,
            "backup_failure": 0,
            "drill_success": 0,
            "drill_failure": 0,
        }
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, ValueError):
                # A corrupt state file must not stop backups; the metrics start
                # fresh and the next successful run rewrites it.
                pass

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".json.part")
            tmp.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)

    def bump(self, key: str) -> None:
        with self.lock:
            self.data[key] = int(self.data[key]) + 1  # type: ignore[arg-type]

    def set(self, key: str, value: object) -> None:
        with self.lock:
            self.data[key] = value

    def set_nested(self, key: str, child: str, value: float) -> None:
        with self.lock:
            nested = self.data.setdefault(key, {})
            if isinstance(nested, dict):
                nested[child] = value

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return dict(self.data)

    def render(self, offhost_configured: bool) -> bytes:
        d = self.snapshot()
        now = now_ts()
        lines: list[str] = []

        def header(name: str, help_text: str, kind: str) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")

        def sample(name: str, value: float, labels: str = "") -> None:
            lines.append(f"{name}{labels} {value:g}")

        def age(key: str) -> float:
            value = float(d.get(key, 0.0))
            return max(0.0, now - value) if value > 0 else float(YEAR_SECONDS)

        failures = d.get("last_failure", {})
        durations = d.get("last_duration", {})

        header("pg_backup_last_success_age_seconds", "Seconds since the last verified full backup (computed at scrape time).", "gauge")
        sample("pg_backup_last_success_age_seconds", age("last_success"))

        header("pg_backup_last_failure_timestamp_seconds", "Unix time of the last failure in this operation.", "gauge")
        for op in OPS:
            value = float(failures.get(op, 0.0)) if isinstance(failures, dict) else 0.0
            sample("pg_backup_last_failure_timestamp_seconds", value, f'{{operation="{op}"}}')

        header("pg_backup_runs_total", "Backup runs by outcome.", "counter")
        sample("pg_backup_runs_total", int(d.get("backup_success", 0)), '{outcome="success"}')
        sample("pg_backup_runs_total", int(d.get("backup_failure", 0)), '{outcome="failure"}')

        header("pg_backup_last_duration_seconds", "Duration of the last operation of this kind.", "gauge")
        for op in OPS:
            value = float(durations.get(op, 0.0)) if isinstance(durations, dict) else 0.0
            sample("pg_backup_last_duration_seconds", value, f'{{operation="{op}"}}')

        header("pg_backup_last_size_bytes", "Size of the newest dump in bytes.", "gauge")
        sample("pg_backup_last_size_bytes", float(d.get("last_size", 0)))
        header("pg_backup_files_retained", "Number of dump files kept locally.", "gauge")
        sample("pg_backup_files_retained", float(d.get("files_retained", 0)))
        header("pg_backup_offhost_configured", "1 when BACKUP_OFFHOST_TARGET is set (0 means requirement unmet).", "gauge")
        sample("pg_backup_offhost_configured", 1.0 if offhost_configured else 0.0)

        header("pg_backup_drill_last_success_age_seconds", "Seconds since the last successful restore drill (computed at scrape time).", "gauge")
        sample("pg_backup_drill_last_success_age_seconds", age("drill_last_success"))
        header("pg_backup_drill_last_failure_age_seconds", "Seconds since the last failed restore drill; sentinel when never failed.", "gauge")
        sample(
            "pg_backup_drill_last_failure_age_seconds",
            age("drill_last_failure") if float(d.get("drill_last_failure", 0.0)) > 0 else float(YEAR_SECONDS),
        )
        header("pg_backup_drill_runs_total", "Restore drills by outcome.", "counter")
        sample("pg_backup_drill_runs_total", int(d.get("drill_success", 0)), '{outcome="success"}')
        sample("pg_backup_drill_runs_total", int(d.get("drill_failure", 0)), '{outcome="failure"}')
        return ("\n".join(lines) + "\n").encode("utf-8")


def run(cfg: Config, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a PostgreSQL tool with the password supplied out-of-band."""
    try:
        return subprocess.run(
            args,
            env=cfg.psql_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timeout after 3600s: {args[0]}")
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", f"cannot start {args[0]}: {exc}")


def run_offhost(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the operator-supplied off-host command without a shell."""
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=3600)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", "off-host command timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", f"cannot start off-host command: {exc}")


def dump_files(cfg: Config) -> list[Path]:
    return sorted(
        (p for p in cfg.backup_dir.glob(f"*{DUMP_SUFFIX}") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )


def do_backup(cfg: Config, state: State) -> int:
    """One full logical backup, structural verification, retention, off-host copy."""
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    final = cfg.backup_dir / f"rc-full-{stamp}{DUMP_SUFFIX}"
    part = final.with_suffix(PART_SUFFIX)
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    cmd = [
        "pg_dump",
        "-Fc",
        "--no-owner",
        "--no-privileges",
        *cfg.conn_args(),
        "-d",
        cfg.db,
        "-f",
        str(part),
    ]
    result = run(cfg, cmd)
    state.set_nested("last_duration", "dump", time.monotonic() - started)
    if result.returncode != 0:
        print(f"pg_dump failed (rc={result.returncode}): {result.stderr.strip()}", file=sys.stderr)
        state.set_nested("last_failure", "dump", now_ts())
        state.bump("backup_failure")
        state.save()
        return result.returncode or EXIT_FAILURE

    # Structural check: a truncated dump cannot be listed.
    verify_started = time.monotonic()
    listed = run(cfg, ["pg_restore", "--list", str(part)])
    state.set_nested("last_duration", "verify", time.monotonic() - verify_started)
    if listed.returncode != 0:
        print(f"pg_restore --list failed (rc={listed.returncode}): {listed.stderr.strip()}", file=sys.stderr)
        part.unlink(missing_ok=True)
        state.set_nested("last_failure", "verify", now_ts())
        state.bump("backup_failure")
        state.save()
        return listed.returncode or EXIT_FAILURE
    os.replace(part, final)

    state.set("last_size", final.stat().st_size)
    state.set("last_success", now_ts())
    state.set("last_backup_date", utc_now().strftime("%Y-%m-%d"))

    files = dump_files(cfg)
    kept = files[-cfg.retention :]
    try:
        for stale in files[: len(files) - len(kept)]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        print(f"prune failed: {exc}", file=sys.stderr)
        state.set_nested("last_failure", "prune", now_ts())
        state.bump("backup_failure")
        state.save()
        return EXIT_FAILURE
    state.set("files_retained", len(kept))

    if cfg.offhost:
        tokens = shlex.split(cfg.offhost)
        command = [final.as_posix() if token == "{file}" else token for token in tokens]
        off_started = time.monotonic()
        copied = run_offhost(command)
        state.set_nested("last_duration", "offhost", time.monotonic() - off_started)
        if copied.returncode != 0:
            print(f"off-host copy failed (rc={copied.returncode}): {copied.stderr.strip()}", file=sys.stderr)
            state.set_nested("last_failure", "offhost", now_ts())
            state.bump("backup_failure")
            state.save()
            return copied.returncode or EXIT_FAILURE

    state.bump("backup_success")
    state.save()
    print(f"backup ok: {final.name} ({final.stat().st_size} bytes), retained {len(kept)}")
    return 0


def do_drill(cfg: Config, state: State) -> int:
    """Restore the newest dump into a scratch DB and assert DDL + row count."""
    dumps = dump_files(cfg)
    if not dumps:
        print("restore drill: no dump file to restore", file=sys.stderr)
        state.set("drill_last_failure", now_ts())
        state.set_nested("last_failure", "drill", now_ts())
        state.bump("drill_failure")
        state.save()
        return EXIT_FAILURE

    source = dumps[-1]
    scratch = f"rc_backup_drill_{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    started = time.monotonic()
    code = EXIT_FAILURE
    try:
        created = run(cfg, ["createdb", *cfg.conn_args(), scratch])
        if created.returncode != 0:
            print(f"createdb failed (rc={created.returncode}): {created.stderr.strip()}", file=sys.stderr)
            code = created.returncode or EXIT_FAILURE
        else:
            restored = run(
                cfg,
                ["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", *cfg.conn_args(), "-d", scratch, str(source)],
            )
            if restored.returncode != 0:
                print(f"pg_restore failed (rc={restored.returncode}): {restored.stderr.strip()}", file=sys.stderr)
                code = restored.returncode or EXIT_FAILURE
            else:
                query = (
                    f"SELECT to_regclass('\"{cfg.schema}\".alembic_version') IS NOT NULL "
                    f"AND (SELECT count(*) FROM \"{cfg.schema}\".alembic_version) > 0;"
                )
                checked = run(
                    cfg,
                    ["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", *cfg.conn_args(), "-d", scratch, "-c", query],
                )
                if checked.returncode != 0 or checked.stdout.strip().lower() != "t":
                    print(
                        f"restore assertion failed (rc={checked.returncode}, got={checked.stdout.strip()!r})",
                        file=sys.stderr,
                    )
                    code = EXIT_FAILURE
                else:
                    print(f"restore drill ok: {source.name} -> {scratch}, alembic_version has rows")
                    code = 0
    finally:
        dropped = run(cfg, ["dropdb", "--if-exists", *cfg.conn_args(), scratch])
        if dropped.returncode != 0:
            print(f"WARNING: could not drop scratch DB {scratch}: {dropped.stderr.strip()}", file=sys.stderr)
            code = code or dropped.returncode or EXIT_FAILURE

    state.set_nested("last_duration", "drill", time.monotonic() - started)
    if code == 0:
        state.set("drill_last_success", now_ts())
        state.set("last_drill_week", utc_now().strftime("%G-W%V"))
        state.bump("drill_success")
    else:
        state.set("drill_last_failure", now_ts())
        state.set_nested("last_failure", "drill", now_ts())
        state.bump("drill_failure")
    state.save()
    return code


class MetricsHandler(BaseHTTPRequestHandler):
    state: State
    offhost_configured: bool

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        if self.path.split("?", 1)[0] not in {"/metrics", "/healthz"}:
            self.send_error(404)
            return
        body = self.state.render(self.offhost_configured) if self.path.startswith("/metrics") else b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return  # Prometheus scrapes every 15 s; no per-scrape log noise.


def serve_metrics(state: State, port: int, offhost_configured: bool) -> None:
    MetricsHandler.state = state
    MetricsHandler.offhost_configured = offhost_configured
    server = ThreadingHTTPServer(("0.0.0.0", port), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, name="metrics", daemon=True)
    thread.start()


def daemon(cfg: Config, state: State) -> int:
    serve_metrics(state, cfg.metrics_port, bool(cfg.offhost))
    print(f"pgbackup daemon: daily {cfg.backup_hour:02d}:00Z, weekly drill day {cfg.drill_weekday} {cfg.drill_hour:02d}:00Z", flush=True)
    while True:
        current = utc_now()
        day = current.strftime("%Y-%m-%d")
        if state.snapshot().get("last_backup_date") != day and current.hour >= cfg.backup_hour:
            do_backup(cfg, state)
        week = current.strftime("%G-W%V")
        if (
            current.weekday() == cfg.drill_weekday
            and current.hour >= cfg.drill_hour
            and state.snapshot().get("last_drill_week") != week
        ):
            do_drill(cfg, state)
        time.sleep(60)


def main(argv: list[str]) -> int:
    try:
        cfg = Config()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    state = State(cfg.backup_dir / "state.json")
    mode = argv[1] if len(argv) > 1 else "daemon"
    if mode == "backup":
        return do_backup(cfg, state)
    if mode == "drill":
        return do_drill(cfg, state)
    if mode == "daemon":
        return daemon(cfg, state)
    print(f"unknown mode {mode!r}: use daemon, backup or drill", file=sys.stderr)
    return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main(sys.argv))

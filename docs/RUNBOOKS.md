# Runbook — PostgreSQL backup and restore (P1-8)

Scope: the local/standalone Compose stack (`infra/compose.db.yaml`) and the same
service as deployed in the central DWH stack. It covers **logical** backups of
the application database only; roles, tablespaces, server configuration and WAL
are out of scope.

## Evidence and decision

- Local and CI PostgreSQL are `postgres:18-alpine` with the named volume
  `rc-postgres-data` (`infra/compose.db.yaml:14,23-24,51-53`). Production is
  PostgreSQL **14** (`docs/WORKFLOW.md:273-277`) on an external/central server
  (`docs/ARCHITECTURE.md:72-76`).
- Before this change there was **no** backup container, cron, script or job —
  only a manual `pg_dump` instruction (`docs/DATA-JOBS.md:516`) — and no declared
  RPO/RTO (`docs/REMEDIATION-PHASE0-RESEARCH.md` §5;
  `docs/TODO.md:68,314`).
- **Decision: `pg_dump` custom-format sidecar (`pgbackup`), not WAL-G /
  pgBackRest.** WAL-based tools require server-side `archive_mode` /
  `archive_command` and a separate repository; the production server is not
  configured by this repository, and no point-in-time-recovery requirement is
  recorded anywhere in the docs. At one logical database with a daily RPO, a
  verified full dump is adequate and can be delivered entirely in-repo.
  Escalation path: if a stricter RPO or PITR is requested, add WAL archiving on
  the server and a WAL-G/pgBackRest repository — do not pretend this sidecar
  provides PITR.
- Client compatibility: the image is `postgres:18-alpine`, so `pg_dump` 18 can
  dump both the local 18 stand and the production 14 server. The reverse
  (a 14 client against an 18 server) is not supported and is why the generic
  `postgresql-client` image is not used.

## Schedule, retention and RPO

| Contract | Value | Source |
|---|---|---|
| RPO (default assumption) | **≤ 24 h** — no stricter need is documented | `docs/TODO.md:68`, this doc |
| Full backup | daily, `BACKUP_HOUR_UTC=2` (UTC) | `.env.example`, compose |
| Local retention | newest `BACKUP_RETENTION_COUNT=7` dumps in volume `rc-postgres-backups` | `infra/compose.db.yaml` |
| Restore drill | weekly, Sunday (`BACKUP_DRILL_WEEKDAY=6`) at `BACKUP_DRILL_HOUR_UTC=3` | compose |
| Off-host copy | **OPTIONAL** and off by default — see below | `BACKUP_OFFHOST_TARGET` |

Dumps are named `rc-full-<YYYYmmddTHHMMSSZ>.dump`, written as `.part` and renamed
only after `pg_restore --list` succeeds, so a truncated file is never counted or
restored. Pruning happens after the new dump is verified. A missed window (daemon
started late) is caught up on the first pass after the target hour; a run that
fails is **not** silently retried the same day — it alerts and is retried with
`just backup-now`.

`RTO` is deliberately **not** asserted here: it depends on the target host and
dump size and has not been measured.

## What the drill actually proves

`just backup-drill` restores the newest dump into a temporary database
`rc_backup_drill_<UTCstamp>` and asserts both DDL and data:

```sql
SELECT to_regclass('"<schema>".alembic_version') IS NOT NULL
   AND (SELECT count(*) FROM "<schema>".alembic_version) > 0;
```

Only if that returns `t` is the drill counted as a success; the scratch database
is dropped in a `finally` block even when the restore fails. The backup role
therefore needs `CREATEDB` (the local `postgres` superuser has it).

## Restore procedure

1. Announce a maintenance window and stop the application workers so no writes
   land during the restore. Keep the original dump and the original database
   until the restored copy has been verified.
2. List the dumps (the sidecar's own volume is the source of truth):

   ```sh
   docker compose run --rm --no-deps --entrypoint sh pgbackup \
     -c 'ls -lh /var/backups/reportcollector'
   ```

3. Rehearse first on a disposable stand with `just backup-drill`; it performs
   the exact restore + assertion + cleanup sequence below against the newest
   dump.
4. For a real recovery create a target database (example name `rc_restore`) and
   restore into it with the same tools the sidecar uses:

   ```sh
   docker compose run --rm --entrypoint sh pgbackup -c \
     'PGPASSWORD="$POSTGRES_PASSWORD" createdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" rc_restore && \
      PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --no-owner --no-privileges --exit-on-error \
        -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d rc_restore \
        /var/backups/reportcollector/<dump-file>'
   ```

5. Run the assertion from the previous section against `rc_restore` (with
   `psql -v ON_ERROR_STOP=1`) and check a few known business row counts.
6. Point the application at the restored database (`POSTGRES_HOST` /
   `POSTGRES_DB`, same `POSTGRES_SCHEMA`), start the workers and verify
   `GET /health`, `GET /ready` and one save/undo smoke test. Run `just migrate`
   only if the dump predates the current migration head; applied migrations are
   immutable (`docs/WORKFLOW.md`).

Never restore over the live database in place: restore to a new database,
verify, then switch the connection.

## Drill cadence and trigger

- Automatic: weekly as in the table above.
- Manual: `just backup-drill` after any change to the backup sidecar, the
  restore procedure, the `POSTGRES_SCHEMA` value or a PostgreSQL major upgrade,
  and before a maintenance window that depends on a rollback.

## Off-host copy (OPEN by default)

`BACKUP_OFFHOST_TARGET` is optional. It must be a whitespace-separated command
containing exactly one `{file}` token; the sidecar substitutes the absolute dump
path and runs it **without a shell** (`shlex.split` + `subprocess.run(shell=False)`):

```dotenv
BACKUP_OFFHOST_TARGET=aws s3 cp {file} s3://my-bucket/reportcollector/pg/
BACKUP_OFFHOST_TARGET=rsync -a {file} backup-host:/srv/reportcollector/pg/
```

Credentials come from the operator's environment, an IAM role or an ssh agent —
**no credential is stored in this repository** and no S3/MinIO/rsync
infrastructure is created by this change. When the variable is empty (the
default), the off-host copy is skipped entirely, `pg_backup_offhost_configured`
is `0` and **the off-host requirement is NOT satisfied**. There is no alert that
claims it is satisfied; the operator must configure and verify a destination.
The local restore drill does not exercise any off-host target.

## Monitoring contract

The sidecar exposes a narrow stdlib Prometheus endpoint on `pgbackup:9113`
(no host publication). Prometheus scrapes it as job
`reportcollector-pg-backup` (`infra/prometheus/prometheus.yml.tpl`). The
central DWH stack must add the same scrape job against its own backup service
and copy `alert-rules.yml` into its `rules/` directory, exactly as it already
does for the other ReportCollector rules (`docs/ARCHITECTURE.md`,
"Two deployment modes").

| Metric | Type / labels | Meaning |
|---|---|---|
| `pg_backup_last_success_age_seconds` | gauge | seconds since last verified dump, computed at scrape time |
| `pg_backup_last_failure_timestamp_seconds` | gauge `{operation="dump\|verify\|offhost\|prune\|drill"}` | last failure per operation |
| `pg_backup_runs_total` | counter `{outcome="success\|failure"}` | backup runs |
| `pg_backup_last_duration_seconds` | gauge `{operation=…}` | last operation duration |
| `pg_backup_last_size_bytes` | gauge | newest dump size |
| `pg_backup_files_retained` | gauge | dumps kept locally |
| `pg_backup_offhost_configured` | gauge | `1` when a target is configured, `0` otherwise |
| `pg_backup_drill_last_success_age_seconds` | gauge | seconds since last successful drill |
| `pg_backup_drill_last_failure_age_seconds` | gauge | seconds since last failed drill (1-year sentinel if never) |
| `pg_backup_drill_runs_total` | counter `{outcome="success\|failure"}` | drills |

All series additionally carry the scrape labels `job=reportcollector-pg-backup`
and `env=<LOG_ENV>`. Alerts (group `reportcollector_pg_backup` in
`infra/prometheus/alert-rules.yml`):

| Alert | Condition | Severity |
|---|---|---|
| `PgBackupExporterDown` | `up{job="reportcollector-pg-backup"} == 0` for 10m | warning |
| `PgBackupFailed` | any failure counter increase in 6h | warning |
| `PgBackupOverdue` | last success age > 90 000 s (25 h) | critical |
| `PgRestoreDrillFailed` | last drill failure age < 24 h | warning |
| `PgRestoreDrillOverdue` | last successful drill age > 691 200 s (8 d) | warning |

The provisioned dashboard **ReportCollector · Сервис**
(`infra/grafana/provisioning/dashboards-json/service.json`, panels 16–17) shows
backup age/size/retention and drill age. Edit the JSON, not the browser
(`allowUiUpdates: false`).

## Alert response

| Alert | First action | Escalation |
|---|---|---|
| `PgBackupExporterDown` | `docker compose ps pgbackup`, `just backup-logs`; check `net` and the healthcheck | recreate the sidecar; verify the scrape target in Prometheus |
| `PgBackupFailed` | read `pg_backup_last_failure_timestamp_seconds{operation=…}` and the sidecar log for the failing tool/rc | fix DB connectivity/disk/off-host command, then `just backup-now` |
| `PgBackupOverdue` | treat as RPO breach: `just backup-now`, confirm `pg_backup_files_retained` grows | notify the operator; schedule a manual drill and record the gap |
| `PgRestoreDrillFailed` | run `just backup-drill` and read the `pg_restore`/assertion error | a dump may be unrestorable: do not rely on it; verify an older dump and escalate |
| `PgRestoreDrillOverdue` | confirm the weekly window and `CREATEDB` rights, then `just backup-drill` | if the role cannot create scratch DBs, grant `CREATEDB` to the backup role |

## Exit codes

`0` — success. `1` — backup/drill failure without a tool code (no dump,
assertion failed, scratch cleanup failed). `2` — configuration error (bad
identifier, missing `{file}` token, bad integer range). Any other non-zero
value is the failing `pg_dump` / `pg_restore` / `psql` / `createdb` / `dropdb` /
off-host command's own return code. One-shot modes (`just backup-now`,
`just backup-drill`) propagate it to the caller; the daemon records the same
information in the metrics above instead of exiting.

## Still OPEN

- Off-host destination: none configured and none exercised here
  (`BACKUP_OFFHOST_TARGET` is empty by default). No target was contacted.
- RTO and any stricter RPO need an operator decision; 24 h is an assumption.
- PITR/WAL archiving is not implemented (see the decision above).
- The central DWH stack's scrape/rule wiring is an operator action, as for the
  existing alert rules; it was not exercised in this repository.
- Alert delivery to the operator mailbox was not re-verified here (last
  explicit check noted in `docs/TODO.md`).

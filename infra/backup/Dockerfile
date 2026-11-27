# PostgreSQL logical backup sidecar.
#
# The base image supplies pg_dump / pg_restore / psql / createdb / dropdb at the
# same major version as the local stand (18). A pg_dump client may target an
# OLDER server, so this image also backs up the production PostgreSQL 14
# server; the reverse is not true and is why we do not use a generic client.
# python3 is added only to run the stdlib-only scheduler and /metrics endpoint.
FROM postgres:18-alpine

RUN apk add --no-cache python3

WORKDIR /opt/pgbackup
COPY pg_backup.py /opt/pgbackup/pg_backup.py

RUN mkdir -p /var/backups/reportcollector \
    && chown postgres:postgres /var/backups/reportcollector

# The container writes dumps with the unprivileged `postgres` account.
USER postgres
EXPOSE 9113

ENTRYPOINT ["python3", "/opt/pgbackup/pg_backup.py"]

#!/bin/sh
# Render the Prometheus scrape config at container start.
# Mirrors alertmanager/entrypoint.sh: Prometheus does not expand environment
# variables in its YAML, and --setenv does not help for static_configs targets.
set -eu

: "${BACKEND_METRICS_HOST:?BACKEND_METRICS_HOST is required}"
: "${BACKEND_PORT:?BACKEND_PORT is required}"
: "${LOG_ENV:?LOG_ENV is required}"

# The template lives on a read-only mount; render into the writable container
# layer, exactly like the Alertmanager config.
sed \
  -e "s|\${BACKEND_METRICS_HOST}|${BACKEND_METRICS_HOST}|g" \
  -e "s|\${BACKEND_PORT}|${BACKEND_PORT}|g" \
  -e "s|\${LOG_ENV}|${LOG_ENV}|g" \
  /etc/prometheus/prometheus.yml.tpl > /tmp/prometheus.yml

echo "Rendered scrape target: ${BACKEND_METRICS_HOST}:${BACKEND_PORT} (env=${LOG_ENV})"
exec /bin/prometheus \
  --config.file=/tmp/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time="${PROMETHEUS_RETENTION:-15d}" \
  --web.enable-lifecycle

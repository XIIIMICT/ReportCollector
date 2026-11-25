# Prometheus scrape configuration — RENDERED AT CONTAINER START.
# Edit this template, not /tmp/prometheus.yml. The renderer is entrypoint.sh:
# Prometheus itself does not expand ${VAR} from the compose environment, so a
# hardcoded port silently breaks scraping when BACKEND_PORT changes.

global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/alert-rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  - job_name: reportcollector-back
    metrics_path: /metrics
    static_configs:
      - targets: ["${BACKEND_METRICS_HOST}:${BACKEND_PORT}"]
        labels:
          env: "${LOG_ENV}"

  # Stack self-monitoring: every service this compose stack runs must have an
  # `up` series so the alert rules can detect its failure.
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  - job_name: loki
    static_configs:
      - targets: ["loki:3100"]

  - job_name: alertmanager
    static_configs:
      - targets: ["alertmanager:9093"]

  - job_name: grafana
    static_configs:
      - targets: ["grafana:3000"]

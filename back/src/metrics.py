"""
Prometheus metrics for the application.

Logs answer "what happened"; metrics answer "how many and how often". The
percentiles (p50/p95/p99) live ONLY here: they are computed from histogram
buckets recorded at write time, and cannot be derived from log lines after the
fact. See `docs/ARCHITECTURE.md`, «Monitoring».

Design notes:

- **Two histograms, not one.** The HTTP duration label is the ROUTE TEMPLATE
  (`/api/kppk/realization/{row_id}`), never the raw path: a uuid per row would
  create one time series per row and blow up Prometheus' cardinality. The route
  template is resolved from the ASGI scope after routing, so it is available
  only once the request has been matched.
- **The default registry is process-global.** Tests import `src.main` many
  times in one process; registering the same collector twice raises. Every
  collector is therefore created once at import and reused, and `reset_metrics()`
  exists for the tests that assert on values.
- **WebSocket metrics are gauges/counters updated at the connection lifecycle**
  (`RoomManager`), not scraped from memory: a scrape reads whatever the last
  update left, which is the point of a gauge.

The `/metrics` endpoint is served WITHOUT authentication because Prometheus
scrapes over the internal network; it exposes only aggregate counts and
histogram buckets — no user data, no paths with ids (see above).
"""

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)

# Latency buckets in seconds. Spread over the range this application actually
# exhibits: sub-10 ms dictionary reads, ~50 ms grid loads, multi-second Excel
# exports. p95 on the default buckets (all clustered below 10 s but too coarse
# near 100 ms) would be meaningless for the grid.
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency split by route template.",
    labelnames=("method", "route"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being handled.",
    registry=REGISTRY,
)

HTTP_REQUEST_UPLOAD_BYTES = Histogram(
    "http_request_upload_bytes",
    "HTTP body bytes consumed by the application, including chunked uploads.",
    labelnames=("method", "route"),
    buckets=(1_024, 10_240, 100_000, 1_000_000, 10_000_000, 100_000_000, 1_000_000_000),
    registry=REGISTRY,
)

HTTP_RESPONSE_BYTES = Histogram(
    "http_response_bytes",
    "HTTP body bytes sent by the application, including streamed downloads.",
    labelnames=("method", "route", "status"),
    buckets=(1_024, 10_240, 100_000, 1_000_000, 10_000_000, 100_000_000, 1_000_000_000),
    registry=REGISTRY,
)

# Concrete application gauges — the numbers an operator asks about first.
WS_CONNECTIONS_ACTIVE = Gauge(
    "ws_connections_active",
    "Open WebSocket connections, across all collaboration rooms.",
    registry=REGISTRY,
)

WS_ROOMS_ACTIVE = Gauge(
    "ws_rooms_active",
    "Collaboration rooms that currently have at least one subscriber.",
    registry=REGISTRY,
)

WS_EVENTS_RELAYED_TOTAL = Counter(
    "ws_events_relayed_total",
    "Collaboration events relayed to subscribers.",
    labelnames=("room_section",),
    registry=REGISTRY,
)

# A slow consumer that fills its queue is DISCONNECTED on purpose (see
# `RoomManager`): the client reconnects and refetches. That is a correctness
# decision, but a rising rate means clients cannot keep up — either the network
# or the server is the bottleneck, and without this counter the phenomenon is
# invisible: nothing fails, clients just reconnect.
WS_SLOW_CONSUMERS_TOTAL = Counter(
    "ws_slow_consumers_total",
    "WebSocket connections dropped because their send queue filled up.",
    labelnames=("room_section",),
    registry=REGISTRY,
)

DB_CONNECTIONS_IN_USE = Gauge(
    "db_connections_in_use",
    "Database connections currently checked out by this process.",
    registry=REGISTRY,
)

DB_CONNECTION_CHECKOUTS_TOTAL = Counter(
    "db_connection_checkouts_total",
    "Database connection checkouts completed by this process.",
    registry=REGISTRY,
)

DB_CONNECTION_INVALIDATIONS_TOTAL = Counter(
    "db_connection_invalidations_total",
    "Database connections invalidated after a transport or server error.",
    registry=REGISTRY,
)

DB_QUERIES_TOTAL = Counter(
    "db_queries_total",
    "Database statements finished by this process, including failures.",
    registry=REGISTRY,
)

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database statement duration without SQL text labels.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

DB_CONNECTION_HOLD_DURATION = Histogram(
    "db_connection_hold_seconds",
    "Time a checked-out connection remains occupied.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

DB_QUERY_ERRORS_TOTAL = Counter(
    "db_query_errors_total",
    "Database statements that raised before completion.",
    registry=REGISTRY,
)

DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Configured database pool size for this process.",
    registry=REGISTRY,
)

DB_POOL_OVERFLOW = Gauge(
    "db_pool_overflow",
    "Current database pool overflow connections.",
    registry=REGISTRY,
)

EXPORT_JOBS_ACTIVE = Gauge(
    "export_jobs_active",
    "Export jobs currently pending or running.",
    labelnames=("kind",),
    registry=REGISTRY,
)

EXPORT_JOBS_QUEUE_DEPTH = Gauge(
    "export_jobs_queue_depth",
    "Export jobs waiting for a worker.",
    labelnames=("kind",),
    registry=REGISTRY,
)

EXPORT_JOBS_COMPLETED_TOTAL = Counter(
    "export_jobs_completed_total",
    "Export jobs completed successfully.",
    labelnames=("kind",),
    registry=REGISTRY,
)

EXPORT_JOBS_FAILED_TOTAL = Counter(
    "export_jobs_failed_total",
    "Export jobs that failed during generation.",
    labelnames=("kind",),
    registry=REGISTRY,
)

EXPORT_JOBS_REJECTED = Counter(
    "export_jobs_rejected_total",
    "Exports rejected by admission limits.",
    labelnames=("kind", "reason"),
    registry=REGISTRY,
)
EXPORT_JOB_OLDEST = Gauge(
    "export_job_oldest_seconds",
    "Age of the oldest active export, zero when idle.",
    labelnames=("kind",),
    registry=REGISTRY,
)

EXPORT_JOB_QUEUE_WAIT = Histogram(
    "export_job_queue_wait_seconds",
    "Time an export waited before its worker started it.",
    labelnames=("kind",),
    buckets=(*_LATENCY_BUCKETS, 60, 120, 300, 900),
    registry=REGISTRY,
)

EXPORT_JOB_DURATION = Histogram(
    "export_job_duration_seconds",
    "Export generation duration.",
    labelnames=("kind",),
    buckets=(*_LATENCY_BUCKETS, 60.0, 120.0, 300.0, 900.0),
    registry=REGISTRY,
)

EXPORT_JOB_ROWS = Histogram(
    "export_job_rows",
    "Rows written by a completed export.",
    labelnames=("kind",),
    buckets=(1, 10, 100, 1_000, 10_000, 100_000, 1_000_000),
    registry=REGISTRY,
)

EXPORT_JOB_BYTES = Histogram(
    "export_job_bytes",
    "Bytes written by a completed export.",
    labelnames=("kind",),
    buckets=(1_024, 10_240, 100_000, 1_000_000, 10_000_000, 100_000_000, 1_000_000_000),
    registry=REGISTRY,
)

CACHE_HITS_TOTAL = Counter(
    "cache_hits_total",
    "In-process or Valkey cache hits.",
    labelnames=("cache",),
    registry=REGISTRY,
)

CACHE_MISSES_TOTAL = Counter(
    "cache_misses_total",
    "In-process or Valkey cache misses, including unavailable caches.",
    labelnames=("cache",),
    registry=REGISTRY,
)

RUNTIME_EVENT_LOOP_LAG = Histogram(
    "runtime_event_loop_lag_seconds",
    "Event-loop scheduling delay measured by the runtime sampler.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)

RUNTIME_DISK_FREE_BYTES = Gauge(
    "runtime_disk_free_bytes",
    "Free bytes on application filesystems.",
    labelnames=("storage",),
    registry=REGISTRY,
)

RUNTIME_DISK_SIZE_BYTES = Gauge(
    "runtime_disk_size_bytes",
    "Total bytes on application filesystems.",
    labelnames=("storage",),
    registry=REGISTRY,
)

DB_POOL_ACQUIRE_DURATION = Histogram(
    "db_pool_acquire_seconds",
    "Pool acquisition time, including new connection setup; excludes pre-ping.",
    labelnames=("outcome",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
DB_POOL_CAPACITY = Gauge("db_pool_capacity", "Maximum pool plus overflow connections.", registry=REGISTRY)
DB_QUERY_FAILURES = Counter(
    "db_query_failures_total",
    "Failed statements by bounded failure category.",
    labelnames=("reason",),
    registry=REGISTRY,
)
HTTP_RESPONSE_FAILURES = Counter(
    "http_response_failures_total",
    "HTTP response interruptions or unhandled errors.",
    labelnames=("method", "route"),
    registry=REGISTRY,
)
DEPENDENCY_DURATION = Histogram(
    "dependency_check_duration_seconds",
    "Readiness dependency check duration.",
    labelnames=("dependency", "outcome"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
OPERATION_DURATION = Histogram(
    "data_operation_duration_seconds",
    "Data operation phase duration, including failed/cancelled phases.",
    labelnames=("operation", "phase", "outcome"),
    buckets=(*_LATENCY_BUCKETS, 60, 120, 300, 900),
    registry=REGISTRY,
)
IMPORT_UPLOAD_BYTES = Histogram(
    "import_upload_bytes",
    "Verified import file size, excluding multipart framing.",
    buckets=(1024, 100_000, 1_000_000, 10_000_000, 100_000_000, 300_000_000),
    registry=REGISTRY,
)
IMPORT_REJECTIONS = Counter(
    "import_rejections_total",
    "Uploads rejected by application limits after multipart parsing.",
    labelnames=("reason",),
    registry=REGISTRY,
)

VALKEY_OPERATION_DURATION = Histogram(
    "valkey_operation_duration_seconds",
    "Logical Valkey operation duration, including client retries; excludes idle subscriptions.",
    labelnames=("operation", "outcome"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
VALKEY_SUBSCRIPTION_FAILURES = Counter(
    "valkey_subscription_failures_total",
    "Pub/sub transport errors that escaped client retries.",
    registry=REGISTRY,
)
AUTH_RATE_LIMIT_EVENTS = Counter(
    "auth_rate_limit_events_total",
    "Authentication rate-limit decisions by storage and outcome.",
    labelnames=("storage", "outcome"),
    registry=REGISTRY,
)


def observe_http_request(*, method: str, route: str, status: int, duration_seconds: float) -> None:
    """Record one finished request against its ROUTE TEMPLATE."""
    HTTP_REQUESTS_TOTAL.labels(method=method, route=route, status=str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration_seconds)


def observe_http_sizes(
    *, method: str, route: str, status: int, request_bytes: int | None, response_bytes: int | None
) -> None:
    """Record sizes only when the ASGI stack exposes a reliable byte count."""
    if request_bytes is not None and request_bytes >= 0:
        HTTP_REQUEST_UPLOAD_BYTES.labels(method=method, route=route).observe(request_bytes)
    if response_bytes is not None and response_bytes >= 0:
        HTTP_RESPONSE_BYTES.labels(method=method, route=route, status=str(status)).observe(response_bytes)

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
    "Database statements completed by this process.",
    registry=REGISTRY,
)

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database statement duration without SQL text labels.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)


def observe_http_request(*, method: str, route: str, status: int, duration_seconds: float) -> None:
    """Record one finished request against its ROUTE TEMPLATE."""
    HTTP_REQUESTS_TOTAL.labels(method=method, route=route, status=str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration_seconds)

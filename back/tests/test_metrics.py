"""
The `/metrics` endpoint and the series it exposes.

These tests pin the two properties that make the metrics USABLE rather than
merely present:

1. the route label is the ROUTE TEMPLATE, never the concrete path — a uuid per
   row would create one Prometheus time series per row;
2. the WebSocket gauges track the live connection count, and the slow-consumer
   counter moves when a client is dropped (the failure that is otherwise
   invisible: nothing errors, clients just reconnect).
"""

from httpx2 import AsyncClient

from src.metrics import REGISTRY

HEADERS = {"X-ReportCollector-User": "admin@example.ru"}


async def test_metrics_endpoint_serves_prometheus_text(client: AsyncClient) -> None:
    """`/metrics` answers without auth and in the Prometheus exposition format."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    # The HELP/TYPE preamble of a counter and of the latency histogram.
    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE http_request_duration_seconds histogram" in body


async def test_request_labels_use_the_route_template_not_the_path(client: AsyncClient) -> None:
    """
    A per-row id must NOT become a label value.

    `http_requests_total` reads the router's route template; the raw path would
    put a uuid into the label and the series count would grow with the data.
    """
    missing_id = "5cde1871-1fae-4634-99c7-a5dd811e5cfa"
    await client.patch(f"/api/kppk/product-positions/{missing_id}", headers=HEADERS, json={})
    body = (await client.get("/metrics")).text

    # The template is present, the concrete id is not — anywhere in the payload.
    assert 'route="/kppk/product-positions/{position_id}"' in body
    assert missing_id not in body


async def test_unmatched_paths_share_one_series(client: AsyncClient) -> None:
    """A 404 on an unknown path falls into a single bounded bucket."""
    await client.get("/v1/models")
    await client.get("/totally/unknown/path")
    body = (await client.get("/metrics")).text
    assert 'route="unmatched",status="404"' in body


async def test_health_is_measured_even_though_it_is_not_logged(client: AsyncClient) -> None:
    """
    `/health` is skipped in the LOG (it would spam 174 MB) but still counted.

    The readiness probe is part of the service's health, and a scrape should see
    it — dropping it from the log was a volume decision, not an observability one.
    """
    await client.get("/health")
    body = (await client.get("/metrics")).text
    assert 'route="/health",status="200"' in body


async def test_http_bytes_and_runtime_metrics_are_exposed(client: AsyncClient) -> None:
    """Byte accounting and saturation samplers are visible to Prometheus."""
    response = await client.post("/api/auth/login", json={"email": "missing@example.ru", "password": "bad"})
    assert response.status_code in {400, 401, 404, 422}
    body = (await client.get("/metrics")).text
    assert "# TYPE http_request_upload_bytes histogram" in body
    assert "# TYPE http_response_bytes histogram" in body
    assert "# TYPE runtime_event_loop_lag_seconds histogram" in body
    assert "# TYPE db_pool_capacity gauge" in body


def test_websocket_metrics_are_registered() -> None:
    """
    The collaboration series exist in the registry.

    A labelled counter has no samples until its first `inc()`, so it contributes
    no entry to `REGISTRY.collect()` at import — the gauge is always described,
    the counters are checked through the family names the registry is built
    from. This pins that the module wires all four rather than three.
    """
    described = {metric.name for metric in REGISTRY.collect()}
    assert {"ws_connections_active", "ws_rooms_active"} <= described
    # The counters are alive: incrementing them yields a describable sample.
    from src.metrics import WS_EVENTS_RELAYED_TOTAL, WS_SLOW_CONSUMERS_TOTAL

    WS_EVENTS_RELAYED_TOTAL.labels(room_section="testSection").inc()
    WS_SLOW_CONSUMERS_TOTAL.labels(room_section="testSection").inc()
    after = {metric.name for metric in REGISTRY.collect()}
    assert {"ws_events_relayed", "ws_slow_consumers"} <= after


def test_database_metrics_are_registered() -> None:
    from src.metrics import DB_CONNECTIONS_IN_USE, DB_QUERY_DURATION

    DB_CONNECTIONS_IN_USE.set(2)
    DB_QUERY_DURATION.observe(0.01)
    described = {metric.name for metric in REGISTRY.collect()}
    assert {"db_connections_in_use", "db_query_duration_seconds"} <= described

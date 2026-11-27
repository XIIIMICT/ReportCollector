"""Unit tests for the rendered-Compose host-bind guard.

No Docker daemon is needed: the samples below have the exact shape of
``docker compose config --format json`` output, which is what the guard parses.
"""

from __future__ import annotations

import unittest
from typing import Any

from check_host_binds import PUBLIC_PORTS, public_port_justification, violations


def port(target: int, published: str, host_ip: str | None = None) -> dict[str, Any]:
    """One entry from a rendered ``ports:`` list."""
    entry: dict[str, Any] = {"mode": "ingress", "target": target, "published": published, "protocol": "tcp"}
    if host_ip is not None:
        entry["host_ip"] = host_ip
    return entry


def rendered(**services: list[dict[str, Any]]) -> dict[str, Any]:
    """A minimal rendered compose tree: ``{"services": {"name": {"ports": [...]}}}``."""
    return {"services": {name: {"ports": ports} for name, ports in services.items()}}


class BindGuardTests(unittest.TestCase):
    def test_positive_rendered_config(self) -> None:
        config = rendered(
            nginx=[port(80, "80"), port(443, "443")],
            back=[],  # private to the compose network
            front=[],  # private to the compose network
            postgres=[port(5432, "5432", "127.0.0.1")],
            grafana=[port(3000, "3000", "127.0.0.1")],
        )
        self.assertEqual(violations("compose.yaml", config), [])

    def test_negative_explicit_all_interfaces(self) -> None:
        config = rendered(grafana=[port(3000, "3000", "0.0.0.0")])
        self.assertEqual(
            violations("compose.yaml", config),
            ["compose.yaml: grafana publishes 3000 on 0.0.0.0"],
        )

    def test_negative_implicit_all_interfaces(self) -> None:
        # A mapping with no `host_ip` is exactly how docker compose renders an
        # unqualified `"8000:8000"` — it is published on every interface.
        config = rendered(back=[port(8000, "8000")])
        self.assertEqual(
            violations("compose.yaml", config),
            ["compose.yaml: back publishes 8000 on 0.0.0.0 (all interfaces)"],
        )

    def test_allow_list_case(self) -> None:
        config = rendered(nginx=[port(80, "80"), port(443, "443")])
        self.assertEqual(violations("compose.yaml", config), [])
        for reason in PUBLIC_PORTS.values():
            self.assertTrue(reason.strip())
        self.assertEqual(
            public_port_justification("compose.yaml", "nginx", "443"),
            PUBLIC_PORTS[("compose.yaml", "nginx", "443")],
        )
        # The allow-list is keyed by service AND published port: another port on
        # the same service, or the same port on another service, still fails.
        extra = rendered(nginx=[port(8443, "8443")])
        self.assertEqual(len(violations("compose.yaml", extra)), 1)
        other = rendered(front=[port(443, "443")])
        self.assertEqual(len(violations("compose.yaml", other)), 1)

    def test_ci_stack_loopback_is_unchanged(self) -> None:
        config = rendered(
            postgres=[port(5432, "15432", "127.0.0.1")],
            valkey=[port(6379, "16379", "127.0.0.1")],
            back=[port(8000, "18000", "127.0.0.1")],
            front=[port(5000, "15000", "127.0.0.1")],
        )
        self.assertEqual(violations("infra/compose.ci.yaml", config), [])

    def test_ipv6_loopback_and_unpublished_ports(self) -> None:
        config = rendered(
            loki=[port(3100, "3100", "::1")],
            smtp_relay=[],
        )
        self.assertEqual(violations("compose.yaml", config), [])
        wide = rendered(loki=[port(3100, "3100", "::")])
        self.assertEqual(len(violations("compose.yaml", wide)), 1)


if __name__ == "__main__":
    unittest.main()

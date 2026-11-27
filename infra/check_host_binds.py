"""CI guard: reject host-wide port publications in rendered Compose configs.

The check reads `docker compose config --format json`, not the source YAML:
`include`, profiles and ${VAR} interpolation have already been resolved, so a
binding that only becomes public after expansion cannot slip through. A raw
text grep would miss that (and would flag comments).

Only the containerized reverse proxy's HTTP/TLS ports are public; every other
published port must bind to a loopback host IP. ``PUBLIC_PORTS`` carries the
explicit justification for each of those exceptions. The isolated CI stack is
checked with the same rule: its four ports are already loopback-only.

Usage: python infra/check_host_binds.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]

LOOPBACK_HOST_IPS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})

# (stack label, service, published host port) -> why this one may be public.
# Keep application and monitoring admin ports OFF this list: expose those via
# an authenticated reverse proxy or reach them over the Compose network.
PUBLIC_PORTS: dict[tuple[str, str, str], str] = {
    ("compose.yaml", "nginx", "80"): "HTTP entry point; redirects to HTTPS on 443",
    ("compose.yaml", "nginx", "443"): "TLS/WSS termination for front and /api/ws/; only public app entry point",
}


class Stack(NamedTuple):
    """One rendered compose project and the profiles needed to see every app."""

    label: str
    file: str
    profiles: tuple[str, ...]


STACKS: tuple[Stack, ...] = (
    Stack("compose.yaml", "compose.yaml", ("prod", "dev")),
    Stack("infra/compose.ci.yaml", "infra/compose.ci.yaml", ()),
)


def render(stack: Stack) -> dict[str, Any]:
    """Return the fully rendered ``docker compose config --format json`` tree."""
    command = ["docker", "compose", "-f", stack.file]
    for profile in stack.profiles:
        command += ["--profile", profile]
    command += ["config", "--format", "json"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"cannot render {stack.file}: {message}")
    return json.loads(result.stdout)


def violations(stack: str, config: dict[str, Any]) -> list[str]:
    """Find published ports that are neither loopback nor explicitly allowed."""
    problems: list[str] = []
    for service, definition in config.get("services", {}).items():
        for port in definition.get("ports") or []:
            host_ip = str(port.get("host_ip") or "").strip()
            published = str(port.get("published") or "")
            target = port.get("target", "?")
            if host_ip in LOOPBACK_HOST_IPS:
                continue
            if (stack, service, published) in PUBLIC_PORTS:
                continue
            bound = host_ip or "0.0.0.0 (all interfaces)"
            problems.append(f"{stack}: {service} publishes {published or target} on {bound}")
    return problems


def public_port_justification(stack: str, service: str, published: str) -> str | None:
    """Return the allow-list reason, or None when the port is not allowed."""
    return PUBLIC_PORTS.get((stack, service, published))


def main() -> int:
    problems: list[str] = []
    for stack in STACKS:
        problems.extend(violations(stack.label, render(stack)))
    if problems:
        print("Host-wide port publications are not allowed:")
        for problem in problems:
            print(f"  {problem}")
        print("Bind to 127.0.0.1, keep the port private to the Compose network,")
        print("or add a justified exception to PUBLIC_PORTS in this script.")
        return 1
    allowed = ", ".join(f"{service}:{port}" for _, service, port in PUBLIC_PORTS)
    print(f"Compose host bindings OK (public by allow-list: {allowed}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

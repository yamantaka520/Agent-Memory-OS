"""Same-host node discovery and liveness probing.

Multiple OS accounts on one machine can each run their own AgentMemoryOS
instance (separate homes, separate consoles). Discovery finds them by probing
loopback ports with the unauthenticated `/healthz` endpoint — which returns
the node name and integrity status but never memory content — so a new
account can see "there are other nodes on this host" and start the explicit
`team invite` / `join` pairing flow. Works identically on macOS, Linux, and
Windows (loopback is not firewalled in practice).

Nothing here grants access: seeing a node is not joining it. Membership
always requires a pairing code issued by the other node's operator.
"""

from __future__ import annotations

import json
import socket
import urllib.request
from dataclasses import dataclass, field

from .constants import (
    DISCOVERY_HEALTH_PROBE_TIMEOUT_MULTIPLIER,
    DISCOVERY_PORT_PROBE_TIMEOUT_SECONDS,
)

DEFAULT_PORT_RANGE = range(8000, 8021)
PROBE_TIMEOUT = DISCOVERY_PORT_PROBE_TIMEOUT_SECONDS


@dataclass(slots=True)
class NodeProbe:
    url: str
    port: int
    reachable: bool = False
    is_amos: bool = False
    node_name: str = ""
    status: str = ""
    integrity: bool | None = None
    detail: str = ""
    extras: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "url": self.url, "port": self.port, "reachable": self.reachable,
            "is_amos": self.is_amos, "node_name": self.node_name,
            "status": self.status, "integrity": self.integrity,
            "detail": self.detail,
        }


def _port_open(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_node(url: str, *, timeout: float = PROBE_TIMEOUT * DISCOVERY_HEALTH_PROBE_TIMEOUT_MULTIPLIER) -> NodeProbe:
    """Probe one URL's `/healthz`. Never raises; failures land in `detail`."""
    from urllib.parse import urlparse

    parsed = urlparse(url if "://" in url else f"http://{url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    probe = NodeProbe(url=f"{parsed.scheme}://{parsed.netloc}", port=port)
    request = urllib.request.Request(f"{probe.url}/healthz", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - probing must not raise
        probe.detail = str(exc)
        # An HTTP error still proves something answered; a 503 from /healthz
        # is a degraded AMOS node, anything else is just "not ours".
        code = getattr(exc, "code", None)
        if code is not None:
            probe.reachable = True
            if code == 503:
                try:
                    payload = json.loads(exc.read().decode("utf-8"))  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    return probe
                probe.is_amos = "node" in payload
                probe.node_name = str(payload.get("node", ""))
                probe.status = str(payload.get("status", ""))
                probe.integrity = payload.get("integrity")
        return probe
    probe.reachable = True
    probe.is_amos = isinstance(payload, dict) and "node" in payload
    if probe.is_amos:
        probe.node_name = str(payload.get("node", ""))
        probe.status = str(payload.get("status", ""))
        probe.integrity = payload.get("integrity")
        if payload.get("version"):
            probe.extras["version"] = str(payload["version"])
    return probe


def scan_local_nodes(
    *,
    host: str = "127.0.0.1",
    ports: range | list[int] | None = None,
    exclude_ports: set[int] | None = None,
    timeout: float = PROBE_TIMEOUT,
) -> list[NodeProbe]:
    """Find AgentMemoryOS nodes listening on this host's loopback.

    Fast two-phase scan: a cheap TCP connect filters closed ports, then
    `/healthz` identifies which listeners are actually AMOS nodes. Ports in
    `exclude_ports` (normally this instance's own console) are skipped.
    """
    found: list[NodeProbe] = []
    for port in ports or DEFAULT_PORT_RANGE:
        if exclude_ports and port in exclude_ports:
            continue
        if not _port_open(host, port, timeout=timeout):
            continue
        probe = probe_node(f"http://{host}:{port}", timeout=timeout * DISCOVERY_HEALTH_PROBE_TIMEOUT_MULTIPLIER)
        if probe.is_amos:
            found.append(probe)
    return found

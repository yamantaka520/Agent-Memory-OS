"""Fleet console (v1.6): signed cross-node operations and status aggregation.

Any node holding a fleet admin PRIVATE key (`agent-memory fleet keygen`) can
act as the console for every node that granted the matching public key
(`agent-memory fleet grant`). There is no standing central authority: the
console COORDINATES — each managed node verifies every request's signature
against its own local grant, enforces its own capability split (manage vs
read-private), and audits what it accepted. Losing the console loses nothing
but the aggregate view.

Transport is the nodes' existing HTTP API; authentication is per-request
Ed25519 signatures (see crypto.fleet_sign_headers), so no shared secret ever
crosses the wire.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import crypto
from .constants import FLEET_HTTP_TIMEOUT_SECONDS

DEFAULT_TIMEOUT = FLEET_HTTP_TIMEOUT_SECONDS


class FleetKeyMissing(RuntimeError):
    """This node has no fleet admin key — run `agent-memory fleet keygen`."""


def _http_request(
    url: str, method: str, target: str, body: bytes, headers: dict[str, str],
    *, timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str]:
    """One raw HTTP exchange. Module-level so tests can bridge to a TestClient."""
    request = urllib.request.Request(
        url.rstrip("/") + target, data=body or None, headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)  # unreachable — status 0 marks "no HTTP answer at all"


def signed_call(
    keypair: dict[str, str], url: str, method: str, target: str,
    payload: dict | None = None, *, timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, Any]:
    """One signed fleet request; (status_code, parsed JSON or raw text)."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    headers = crypto.fleet_sign_headers(keypair, method, target, body)
    if body:
        headers["Content-Type"] = "application/json"
    status, text = _http_request(url, method, target, body, headers, timeout=timeout)
    try:
        return status, json.loads(text) if text else {}
    except ValueError:
        return status, text


def load_console_key(home: str | None = None) -> dict[str, str]:
    keypair = crypto.load_fleet_key(home)
    if not keypair:
        raise FleetKeyMissing(
            "this node has no fleet admin key — run `agent-memory fleet keygen`, "
            "then `agent-memory fleet grant <public key>` on every node to manage")
    return keypair


def _probe_peer(keypair: dict[str, str], peer: dict[str, Any]) -> dict[str, Any]:
    """Aggregate one managed node's identity, health, and store totals.

    Degrades per-call: a node that is up but hasn't granted this console
    reports reachable=True with an 'unauthorized' note instead of vanishing.
    """
    url = str(peer["url"])
    entry: dict[str, Any] = {
        "url": url,
        "name": peer.get("name") or "",
        "policy": peer.get("policy") or "",
        "last_synced_at": peer.get("last_synced_at"),
        "reachable": False,
        "authorized": False,
        "node_name": "",
        "version": "",
        "memories": None,
        "links": None,
        "owners": None,
        "detail": "",
    }
    status, node = signed_call(keypair, url, "GET", "/api/node")
    if status == 0:
        entry["detail"] = str(node)[:200]
        return entry
    entry["reachable"] = True
    if status != 200:
        detail = node.get("detail") if isinstance(node, dict) else str(node)
        entry["detail"] = f"HTTP {status}: {detail}"
        return entry
    entry["authorized"] = True
    if isinstance(node, dict):
        entry["node_name"] = str(node.get("node_name", ""))
        entry["version"] = str(node.get("version", ""))
    status, stats = signed_call(keypair, url, "GET", "/api/stats")
    if status == 200 and isinstance(stats, dict):
        entry["memories"] = stats.get("total")
        entry["links"] = stats.get("links")
    status, owners = signed_call(keypair, url, "GET", "/api/owners")
    if status == 200 and isinstance(owners, dict):
        entry["owners"] = owners.get("owners")
    return entry


def console_snapshot(client, keypair: dict[str, str]) -> tuple[dict[str, Any], list]:
    """(console section, peer list) from LOCAL state only — cheap DB reads.

    Split from the network probing so a web server can take its store lock
    just for this part and fan out to peers without blocking other requests.
    """
    local_stats = client.stats()
    try:
        from importlib.metadata import version as _version
        own_version = _version("agent-memory-os")
    except Exception:  # noqa: BLE001
        own_version = ""
    console = {
        "node_name": client.node_name,
        "key_id": keypair["key_id"],
        "version": own_version,
        "memories": local_stats.get("total"),
        "links": local_stats.get("links"),
        "owners": client.owner_counts(),
    }
    return console, client.store.list_peers()


def probe_peers(keypair: dict[str, str], peers: list) -> list[dict[str, Any]]:
    """Concurrently aggregate every peer's identity/health/totals (network only)."""
    if not peers:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(peers))) as pool:
        return list(pool.map(lambda p: _probe_peer(keypair, p), peers))


def assemble_status(console: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    versions = sorted({v for v in ([console["version"]]
                                   + [n["version"] for n in nodes]) if v})
    return {"console": console, "nodes": nodes,
            "versions": versions, "version_drift": len(versions) > 1}


def fleet_status(client, home: str | None = None) -> dict[str, Any]:
    """The whole fleet at a glance: this console node + every registered peer.

    Peers are this node's sync_peers — the same registry sync uses — probed
    concurrently with signed requests.
    """
    keypair = load_console_key(home)
    console, peers = console_snapshot(client, keypair)
    return assemble_status(console, probe_peers(keypair, peers))


TRIGGER_TARGETS = {
    "sync": ("POST", "/api/sync/run"),
    # update-run demands an explicit ?confirm= echo so a stray POST can never
    # restart a node; the console supplies it — the operator already confirmed.
    "update": ("POST", "/api/maintenance/update-run?confirm=update"),
}


def trigger_on(
    keypair: dict[str, str], peers: list, action: str, *,
    only_url: str | None = None,
) -> list[dict[str, Any]]:
    """Run one management action on the given peers (network only, no DB).

    Supported actions: 'sync' (POST /api/sync/run — the node syncs its own
    mesh) and 'update' (POST /api/maintenance/update-run — the node upgrades
    itself and restarts; each node's operator opted in by granting 'manage').
    """
    if action not in TRIGGER_TARGETS:
        raise ValueError(
            f"unknown fleet action: {action} (valid: {sorted(TRIGGER_TARGETS)})")
    method, path = TRIGGER_TARGETS[action]
    if only_url:
        wanted = only_url.strip().rstrip("/")
        peers = [p for p in peers if str(p["url"]).rstrip("/") == wanted]
        if not peers:
            raise ValueError(f"no registered peer with url {only_url}")

    def _run(peer: dict[str, Any]) -> dict[str, Any]:
        status, body = signed_call(keypair, str(peer["url"]), method, path)
        return {"url": peer["url"], "name": peer.get("name") or "",
                "status": status, "ok": status == 200,
                "response": body if isinstance(body, dict) else str(body)[:400]}

    if not peers:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(peers))) as pool:
        return list(pool.map(_run, peers))


def fleet_trigger(
    client, home: str | None, action: str, *, only_url: str | None = None,
) -> list[dict[str, Any]]:
    """CLI-shaped wrapper: resolve the key + peers, then trigger."""
    keypair = load_console_key(home)
    return trigger_on(keypair, client.store.list_peers(), action, only_url=only_url)

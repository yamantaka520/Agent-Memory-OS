"""Per-instance settings: `<home>/instance.toml`.

When several Agent Memory OS instances run on one machine — each with its own
`--home` — this file gives each a stable identity and a fixed (or auto-chosen)
Web UI port:

    # <home>/instance.toml
    [instance]
    node_name = "mizuki-laptop"   # shown to peers during memory sync
    host = "127.0.0.1"
    port = 8000                   # taken port? the launcher advances to a free one

Everything has a sensible default, so the file is optional. `node_name`
defaults to a host+home derived label so two instances on the same machine
don't collide.
"""

from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    SETTINGS_PORT_PROBE_TIMEOUT_SECONDS,
    SETTINGS_PORT_SEARCH_LIMIT,
)
from .tokens import resolve_home

SETTINGS_FILENAME = "instance.toml"
DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"


def settings_path(home: str | Path | None) -> Path:
    return resolve_home(home) / SETTINGS_FILENAME


def default_node_name(home: str | Path | None) -> str:
    """A stable machine+account+home-derived name for co-located instances.

    The OS username is part of the default because several accounts on one
    machine all use the same default home basename ("agent-memory") — without
    it every account's node advertised the same name to peers. The home
    basename still disambiguates multiple instances within one account; it is
    dropped when it's just the default, to keep names short.
    """
    import getpass

    host = socket.gethostname().split(".")[0] or "amos"
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no login database
        user = ""
    base = resolve_home(home).name.lstrip(".") or "amos"
    parts = [host]
    # Always include the username (unless it IS the host) so two accounts on
    # one machine never collide. A mere substring match is not enough — hosts
    # "workstation" with users "work"/"station" would both drop to "workstation".
    if user and user.lower() != host.lower():
        parts.append(user)
    if base != "agent-memory":
        parts.append(base)
    return "-".join(parts)


@dataclass(slots=True)
class InstanceSettings:
    node_name: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


def load_instance_settings(home: str | Path | None) -> InstanceSettings:
    path = settings_path(home)
    data: dict = {}
    if path.exists():
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid {path}: {exc}") from exc
        section = parsed.get("instance", parsed)
        if not isinstance(section, dict):
            raise ValueError(f"invalid {path}: [instance] table expected")
        data = section
    # Env overrides the file (12-factor), so a container can be configured
    # entirely through environment variables with no config file mounted.
    node_name = (
        str(os.getenv("AGENT_MEMORY_NODE_NAME") or data.get("node_name") or "").strip()
        or default_node_name(home)
    )
    host = (
        str(os.getenv("AGENT_MEMORY_WEB_HOST") or data.get("host") or DEFAULT_HOST).strip()
        or DEFAULT_HOST
    )
    port = os.getenv("AGENT_MEMORY_WEB_PORT") or data.get("port", DEFAULT_PORT)
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid port (env AGENT_MEMORY_WEB_PORT or {path}): must be an integer") from exc
    if not (0 < port < 65536):
        raise ValueError(f"invalid {path}: port must be 1-65535")
    return InstanceSettings(node_name=node_name, host=host, port=port)


def save_instance_settings(home: str | Path | None, settings: InstanceSettings) -> Path:
    path = settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Agent Memory OS instance settings\n"
        "[instance]\n"
        f'node_name = "{settings.node_name}"\n'
        f'host = "{settings.host}"\n'
        f"port = {settings.port}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def update_instance_settings(
    home: str | Path | None,
    *,
    node_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> InstanceSettings:
    current = load_instance_settings(home)
    updated = InstanceSettings(
        node_name=(node_name.strip() if node_name and node_name.strip() else current.node_name),
        host=host or current.host,
        port=port if port is not None else current.port,
    )
    if updated.port <= 0 or updated.port >= 65536:
        raise ValueError("port must be 1-65535")
    save_instance_settings(home, updated)
    return updated


def port_is_free(host: str, port: int) -> bool:
    """True unless a server is already LISTENING on host:port.

    Probes by attempting a connection rather than a bind. A bind test is wrong
    two ways: with SO_REUSEADDR, Windows reports an in-use port as free; without
    it, a just-vacated port lingering in TIME_WAIT (POSIX) reports as taken, so
    a restarted server drifts off its usual port. `connect` sees only a live
    listener — no listener (free port or TIME_WAIT) refuses the connection.
    """
    target = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(SETTINGS_PORT_PROBE_TIMEOUT_SECONDS)
        # connect_ex == 0 means the connection succeeded → something is listening.
        return sock.connect_ex((target, port)) != 0


def find_available_port(host: str, preferred: int, *, limit: int = SETTINGS_PORT_SEARCH_LIMIT) -> int:
    """Return `preferred` if free, otherwise the next free port above it.

    Lets several instances on one machine start without hand-assigning ports.
    """
    for candidate in range(preferred, min(preferred + limit, 65536)):
        if port_is_free(host, candidate):
            return candidate
    raise RuntimeError(
        f"no free port found in {preferred}-{preferred + limit - 1} on {host}"
    )

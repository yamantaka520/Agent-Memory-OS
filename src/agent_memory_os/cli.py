from __future__ import annotations

import argparse
import json
from pathlib import Path

from .client import MemoryClient
from .constants import (
    PAIRING_INVITE_TTL_SECONDS,
    PROCESS_ELAPSED_QUERY_TIMEOUT_SECONDS,
    PROCESS_LIST_TIMEOUT_SECONDS,
    PYPI_REQUEST_TIMEOUT_SECONDS,
    SCHEDULED_TASK_QUERY_TIMEOUT_SECONDS,
    STALE_PROCESS_START_SLACK_SECONDS,
    TEAM_UPDATE_REQUEST_TIMEOUT_SECONDS,
    WEB_RESTART_LIVENESS_DELAY_SECONDS,
    WEB_RESTART_POLL_ATTEMPTS,
    WEB_RESTART_POLL_INTERVAL_SECONDS,
)
from .golden_recall import evaluate_golden_queries, load_golden_query_cases
from .hermes_importer import import_hermes_memory_files
from .importers import SUPPORTED as IMPORT_SOURCES
from .importers import import_export
from .shadow_mode import summarize_shadow_log


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-memory", description="Local-first AI agent memory runtime")
    p.add_argument("--home", default=None, help="Memory home directory; defaults to AGENT_MEMORY_HOME or ~/.agent-memory")
    sub = p.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add a memory")
    add.add_argument("content")
    add.add_argument("--owner", default="default")
    add.add_argument("--scope", default="user")
    add.add_argument("--type", default="note")
    add.add_argument("--summary")
    add.add_argument("--tag", action="append", default=[])
    add.add_argument("--confidence", type=float, default=0.8)
    add.add_argument("--importance", type=float, default=0.5)

    search = sub.add_parser("search", help="Search memories")
    search.add_argument("query")
    search.add_argument("--owner")
    search.add_argument("--scope")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--json", action="store_true")

    pack = sub.add_parser("pack", help="Build a prompt-ready context pack")
    pack.add_argument("query")
    pack.add_argument("--owner")
    pack.add_argument("--scope")
    pack.add_argument("--limit", type=int, default=12)
    pack.add_argument("--max-tokens", type=int, default=1200)

    sub.add_parser("stats", help="Show database statistics")

    imp = sub.add_parser("import-hermes", help="Import Hermes MEMORY.md/USER.md into AgentMemoryOS")
    imp.add_argument("--profile", required=True, help="Hermes profile name / AgentMemoryOS owner")
    imp.add_argument("--profile-home", required=True, help="Hermes profile home containing memories/MEMORY.md and USER.md")
    imp.add_argument("--json", action="store_true", help="Emit JSON report")

    hermes = sub.add_parser(
        "hermes",
        help="Install/remove the Hermes Agent memory-provider plugin shim",
        description=(
            "Registers AgentMemoryOS with NousResearch hermes-agent (v0.18+) by "
            "writing a provider shim into $HERMES_HOME/plugins/agent-memory-os/, "
            "so it appears in `hermes memory setup` / `hermes memory status`. "
            "Idempotent — re-run after upgrades to refresh the version stamp."
        ),
    )
    hermes.add_argument("action", choices=["install", "uninstall"])
    hermes.add_argument("--hermes-home", default=None,
                        help="Hermes home directory (default: $HERMES_HOME or ~/.hermes)")
    hermes.add_argument("--json", action="store_true", help="Emit JSON report")

    imp2 = sub.add_parser("import", help="Import an export from another memory system (mem0/zep/chatgpt)")
    imp2.add_argument("--from", dest="source", required=True, choices=list(IMPORT_SOURCES),
                      help="Source system")
    imp2.add_argument("file", help="Path to the source's JSON export")
    imp2.add_argument("--owner", default=None, help="Owner for imported memories (default: source name)")
    imp2.add_argument("--visibility", default=None,
                      help="Comma-separated visibility grants (default: private). e.g. global or team:apollo")
    imp2.add_argument("--type", default="note", help="Memory type for imported records (default: note)")
    imp2.add_argument("--json", action="store_true", help="Emit JSON report")

    shadow = sub.add_parser("shadow-summary", help="Summarize shadow-mode JSONL evidence")
    shadow.add_argument("--log", required=True, help="Path to agent_memory_os_shadow.jsonl")
    shadow.add_argument("--last", type=int, default=None, help="Only summarize the last N records")
    shadow.add_argument("--json", action="store_true", help="Emit JSON evidence pack")

    golden = sub.add_parser("golden-recall", help="Run golden-query recall cases against the memory store")
    golden.add_argument("--cases", required=True, help="JSON/JSONL golden query case file")
    golden.add_argument("--limit", type=int, default=10, help="Default search limit for cases without limit")
    golden.add_argument("--recall-target", type=float, default=0.95, help="Required pass rate for GO")
    golden.add_argument("--json", action="store_true", help="Emit JSON evidence report")

    token = sub.add_parser("token", help="Manage the Web UI API token")
    token.add_argument("action", choices=["create", "show", "rotate", "disable"])
    tier_group = token.add_mutually_exclusive_group()
    tier_group.add_argument(
        "--readonly", action="store_true",
        help="Operate on the read-only token (GET-only access) instead of the full token",
    )
    tier_group.add_argument(
        "--sync", action="store_true",
        help="Operate on the sync token: authorizes ONLY federation routes "
             "(/api/sync/*, /api/node). Hand this to a peer instead of the admin token.",
    )

    doctor = sub.add_parser("doctor", help="Check optional dependencies and setup health")
    doctor.add_argument("--install", action="store_true", help="pip-install any missing optional extras")

    backup = sub.add_parser("backup", help="Back up the memory database to a file")
    backup.add_argument("dest", help="Destination .db file path")
    backup.add_argument(
        "--keep", type=int, default=0, metavar="N",
        help="After backing up, keep only the N newest backups sharing dest's "
             "name prefix in its directory (rotate older ones out). 0 = keep all.",
    )

    restore = sub.add_parser("restore", help="Restore the memory database from a backup file")
    restore.add_argument("src", help="Backup .db file to restore from")
    restore.add_argument("--force", action="store_true", help="Overwrite an existing database")

    sub.add_parser("check", help="Run database integrity and invariant checks")

    service = sub.add_parser(
        "service", help="Install the Web console as a login service (launchd/systemd/Task Scheduler)"
    )
    service.add_argument("action", choices=["install", "uninstall", "start", "stop", "restart", "status"])
    service.add_argument("--host", default=None, help="Bind host (default: instance.toml or 127.0.0.1)")
    service.add_argument("--port", type=int, default=None, help="Bind port (default: instance.toml or 8000)")
    service.add_argument("--dry-run", action="store_true", help="Print actions without executing")

    sync = sub.add_parser("sync", help="Federated sync: file bundles, peer HTTP endpoints, or the whole mesh")
    sync.add_argument("action", choices=["export", "import", "pull", "push", "auto", "genkey"])
    sync.add_argument(
        "target", nargs="?", default=None,
        help="Bundle .jsonl path (export/import) or peer base URL (pull/push); omit for auto/genkey",
    )
    sync.add_argument("--since", default=None, help="Only records updated after this ISO timestamp")
    sync.add_argument("--peer-token", default=None, help="Sync-scoped bearer token of the peer's Web API")
    sync.add_argument("--team", default=None, help="Export only one team/project's shared memory")

    peers = sub.add_parser("peers", help="Manage federated sync peers")
    peers.add_argument("action", choices=["add", "remove", "list"])
    peers.add_argument("url", nargs="?", default=None)
    peers.add_argument("--peer-token", default=None, help="Bearer token of the peer's Web API")
    peers.add_argument(
        "--policy", dest="peer_policy", default="shared",
        help="What to sync to this peer: 'shared' (no private memory, default), "
             "'full' (whole store — own trusted nodes only), or 'team:<id>'",
    )
    peers.add_argument(
        "--name", dest="peer_name", default="",
        help="Friendly name for this peer (auto-fetched from the peer if omitted)",
    )

    node = sub.add_parser("node", help="Show or set this instance's identity and Web UI port")
    node.add_argument("--set-name", default=None, help="Set node_name (shown to peers during sync)")
    node.add_argument("--set-host", default=None, help="Set the Web UI bind host")
    node.add_argument("--set-port", type=int, default=None, help="Set the Web UI port")

    team = sub.add_parser("team", help="Manage teams and their node members")
    team.add_argument("action", choices=["list", "create", "rename", "delete",
                                        "add-member", "remove-member", "invite"])
    team.add_argument("team_id", nargs="?", default=None)
    team.add_argument("agent_id", nargs="?", default=None,
                      help="add-member/remove-member: the agent; rename: the NEW team id")
    team.add_argument("--name", default="", help="Display name (create, rename)")
    team.add_argument("--yes", action="store_true",
                      help="rename: skip the confirmation prompt")
    team.add_argument("--dry-run", action="store_true",
                      help="rename: report what would move, change nothing")
    team.add_argument("--ttl", type=int, default=PAIRING_INVITE_TTL_SECONDS,
                      help=f"invite: pairing-code lifetime in seconds (default {PAIRING_INVITE_TTL_SECONDS})")

    join = sub.add_parser(
        "join",
        help="Join another node's team with a pairing code (see: team invite)",
        description=(
            "Redeem a one-time pairing code issued by another node's "
            "'agent-memory team invite <team>'. Exchanges sync-scoped tokens "
            "both ways, registers team-scoped peers, installs the mesh sync "
            "key if the inviter uses one, and runs a first sync."
        ),
    )
    join.add_argument("code", help="Pairing code (amos_join_…)")
    join.add_argument("--url", required=True, help="The inviting node's console URL, e.g. http://127.0.0.1:8001")
    join.add_argument("--agent-id", default=None,
                      help="This node's agent identity (default: AGENT_MEMORY_AGENT_ID or node name)")
    join.add_argument("--my-url", default=None,
                      help="URL the inviter should sync back to (default: this node's host:port)")
    join.add_argument("--no-sync", action="store_true", help="Skip the initial sync after joining")
    join.add_argument("--insecure", action="store_true",
                      help="Allow sending the pairing code over plain HTTP to a non-local host "
                           "(only on a trusted network; prefer https)")

    status = sub.add_parser(
        "status",
        help="Show this host's service status and every connected node's state",
    )
    status.add_argument("--json", action="store_true", help="Emit JSON report")
    status.add_argument("--no-probe", action="store_true",
                        help="Skip live /healthz probes of peers (offline mode)")

    neighbors = sub.add_parser(
        "neighbors",
        help="Discover other AgentMemoryOS nodes on this host (loopback scan)",
    )
    neighbors.add_argument("--json", action="store_true", help="Emit JSON report")
    neighbors.add_argument("--ports", default=None,
                           help="Port range to scan, e.g. 8000-8030 (default 8000-8020)")

    project = sub.add_parser("project", help="Manage projects under a team (members ⊆ team)")
    project.add_argument("action", choices=["list", "create", "delete", "add-member", "remove-member"])
    project.add_argument("project_id", nargs="?", default=None)
    project.add_argument("agent_id", nargs="?", default=None)
    project.add_argument("--team", dest="team_id", default=None, help="Team id (create / list filter)")
    project.add_argument("--name", default="", help="Display name (create)")

    maint = sub.add_parser("maintenance", help="Ops maintenance: health scan, orphan cleanup, reindex, vacuum")
    maint.add_argument("action", choices=["scan", "orphans", "reindex", "vacuum"])
    maint.add_argument("--delete", action="store_true", help="orphans: delete them (default lists)")

    update = sub.add_parser("update", help="Check for and install a newer version (host or Docker)")
    update.add_argument("--check", action="store_true", help="Only report the latest version; don't install")
    update.add_argument("--yes", action="store_true", help="Install without prompting (host/pip only)")
    update.add_argument("--no-restart", action="store_true",
                        help="After upgrading, do not restart the running web console")
    update.add_argument("--team", action="store_true",
                        help="Also trigger self-update on every registered peer that "
                             "opted in (peer console started with AGENT_MEMORY_ALLOW_TEAM_UPDATE=1)")

    path_cmd = sub.add_parser(
        "path",
        help="Check/fix whether the agent-memory scripts directory is on PATH",
        description=(
            "pip installs console scripts into a directory that is often NOT "
            "on PATH for per-user installs — 'agent-memory: command not found' "
            "right after installing. 'path show' diagnoses; 'path install' "
            "appends the export line to your shell profile "
            "(zsh/bash/fish; on Windows it prints the setx command). "
            "Works via 'python -m agent_memory_os.cli path install' when the "
            "script itself is unreachable."
        ),
    )
    path_cmd.add_argument("action", choices=["show", "install"])

    agent = sub.add_parser("agent", help="Manage agent identities")
    agent.add_argument("action", choices=["rename"])
    agent.add_argument("old_id", nargs="?", default=None)
    agent.add_argument("new_id", nargs="?", default=None)
    agent.add_argument("--yes", action="store_true",
                       help="skip the confirmation prompt")

    owner = sub.add_parser(
        "owner",
        help="Inspect and re-attribute memory ownership (list / reassign / delete)",
    )
    owner.add_argument("action", choices=["list", "reassign", "delete"])
    owner.add_argument(
        "old_owner", nargs="?", default=None,
        help="reassign: source owner; delete: owner to purge",
    )
    owner.add_argument("new_owner", nargs="?", default=None,
                       help="reassign: destination owner (may already exist — merges)")
    owner.add_argument("--yes", action="store_true",
                       help="reassign/delete: skip the confirmation prompt")
    owner.add_argument("--no-register", action="store_true",
                       help="reassign: do NOT register the destination as an agent "
                            "(leaves the moved memories under an unrecognized owner)")

    fleet = sub.add_parser(
        "fleet",
        help="Fleet admin identity: keygen on the console node, grant/revoke on each managed node",
        description=(
            "A fleet admin holds an Ed25519 private key on ONE console node; every "
            "node that should accept its signed cross-node operations registers the "
            "PUBLIC key with 'fleet grant'. Grants are local-only by design — they "
            "never propagate over sync, so a peer cannot make itself an admin."
        ),
    )
    fleet.add_argument("action",
                       choices=["keygen", "grant", "revoke", "list",
                                "status", "sync", "update"])
    fleet.add_argument("value", nargs="?", default=None,
                       help="grant: the console's PUBLIC key (b64); revoke: the key id")
    fleet.add_argument("--caps", default="manage",
                       help="grant: comma-separated capabilities — 'manage' (status, "
                            "update, sync, owner ops) and/or 'read-private' (read "
                            "memory content across nodes). Default: manage")
    fleet.add_argument("--force", action="store_true",
                       help="keygen: overwrite an existing fleet key")
    fleet.add_argument("--json", action="store_true", dest="as_json",
                       help="status: machine-readable output")
    fleet.add_argument("--node", default=None,
                       help="sync/update: target one node's URL instead of all")

    retention = sub.add_parser("retention", help="Archive expired and deeply-decayed memories")
    retention.add_argument(
        "--half-lives", type=float, default=None,
        help="Also archive unpinned memories idle for N decay half-lives (default 4; 0 = expired only)",
    )
    return p




def _parse_port_range(spec: str | None):
    if not spec:
        return None
    lo, _, hi = spec.partition("-")
    return range(int(lo), int(hi or lo) + 1)


def _cmd_neighbors(client, args) -> int:
    """Loopback discovery: list other AMOS nodes on this host (see discovery.py)."""
    from .discovery import scan_local_nodes
    from .settings import load_instance_settings

    settings = load_instance_settings(args.home)
    nodes = scan_local_nodes(
        ports=_parse_port_range(args.ports),
        exclude_ports={settings.port},
    )
    if args.json:
        print(json.dumps([n.as_dict() for n in nodes], ensure_ascii=False, indent=2))
        return 0
    if not nodes:
        print("no other AgentMemoryOS nodes found on this host (scanned loopback ports)")
        return 0
    print(f"found {len(nodes)} node(s) on this host:")
    for n in nodes:
        print(f"  {n.node_name or '(unnamed)':24} {n.url}  status={n.status}")
    print("\nTo share memory with one of them (explicit consent required):")
    print("  on that node:  agent-memory team invite <team>")
    print("  on this node:  agent-memory join <code> --url <that url>")
    return 0


def _cmd_status(client, args) -> int:
    """This host's service state + every connected node's live state."""
    import importlib.metadata as _md

    from . import service as svc
    from .discovery import probe_node
    from .pidfile import read_web_pidfile
    from .settings import load_instance_settings
    from .tokens import resolve_home

    settings = load_instance_settings(args.home)
    console_url = f"http://{settings.host}:{settings.port}"

    local: dict = {
        "node_name": client.node_name,
        "version": _md.version("agent-memory-os"),
        "home": str(resolve_home(args.home)),
        "console_url": console_url,
        "service": svc.status_info(),
        "web": None,
        "pid": None,
        "stats": client.stats(),
    }
    pid_info = read_web_pidfile(args.home)
    if pid_info:
        local["pid"] = pid_info.get("pid")
    if not args.no_probe:
        local["web"] = probe_node(console_url).as_dict()

    peers = client.store.list_peers()
    peer_rows = [dict(peer) for peer in peers]
    if not args.no_probe and peer_rows:
        # Probe peers concurrently — a serial loop blocks ~2s per unreachable
        # peer, so a few dead nodes made `status` feel hung.
        from concurrent.futures import ThreadPoolExecutor

        def _probe(row):
            probe = probe_node(str(row["url"]))
            row["online"] = probe.is_amos
            row["remote_node"] = probe.node_name
            row["remote_status"] = probe.status
            return row

        with ThreadPoolExecutor(max_workers=min(8, len(peer_rows))) as pool:
            list(pool.map(_probe, peer_rows))

    report = {"local": local, "peers": peer_rows}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    service_state = local["service"]
    running = {True: "running", False: "stopped", None: "unknown"}[service_state["running"]]
    web = local["web"] or {}
    print(f"Node {local['node_name']}  (agent-memory-os {local['version']})")
    if web.get("is_amos") and web.get("node_name") not in ("", local["node_name"]):
        console_state = (f"⚠ answered by a DIFFERENT node ({web['node_name']}) — "
                         "another instance owns this port; set a distinct port in instance.toml")
    elif web.get("is_amos"):
        console_state = "reachable"
    else:
        console_state = "not responding"
    print(f"  console   {console_url}  {console_state}")
    print(f"  service   {'installed' if service_state['installed'] else 'not installed'}"
          f" / {running}  [{service_state['platform']}]")
    if local["pid"]:
        print(f"  pid       {local['pid']}")
    stats = local["stats"] or {}
    if stats:
        core = {k: stats[k] for k in ("total", "links") if k in stats}
        if core:
            print("  store     " + "  ".join(f"{k}={v}" for k, v in core.items()))
    if not peer_rows:
        print("  peers     none registered")
        return 0
    print(f"  peers     {len(peer_rows)} registered")
    for row in peer_rows:
        if args.no_probe:
            state = "?"
        else:
            state = "online" if row.get("online") else "offline"
        name = row.get("name") or row.get("remote_node") or "(unnamed)"
        last = row.get("last_synced_at") or "never"
        print(f"    {state:7} {name:24} {row['url']}")
        print(f"            policy={row['policy']}  token={'yes' if row['has_token'] else 'no'}"
              f"  last_synced={last}  last_result={row.get('last_result') or '-'}")
    return 0


def _cmd_join(client, args) -> int:
    import os

    from .pairing import join_with_code
    from .settings import load_instance_settings
    from .sync import sync_with_peer

    settings = load_instance_settings(args.home)
    agent_id = (args.agent_id or os.getenv("AGENT_MEMORY_AGENT_ID")
                or settings.node_name)
    my_url = args.my_url or f"http://{settings.host}:{settings.port}"
    try:
        report = join_with_code(
            client, args.code, args.url,
            agent_id=agent_id, my_url=my_url,
            node_name=settings.node_name, home=args.home,
            allow_insecure=args.insecure,
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    print(f"joined team {report['team_id']} — peer "
          f"{report['peer_name'] or report['peer_url']} registered (policy=team:{report['team_id']})")
    if report["sync_key_installed"]:
        print("mesh sync key installed — sync payloads will be encrypted")
    if not args.no_sync:
        try:
            summary = sync_with_peer(
                client, report["peer_url"],
                peer_token=client.store.peer_token(report["peer_url"]),
                policy=f"team:{report['team_id']}",
            )
            print(f"initial sync: {summary}")
        except Exception as exc:  # noqa: BLE001 - join succeeded; sync is best-effort
            print(f"initial sync failed (retry with: agent-memory sync auto): {exc}")
    return 0



def _scripts_dir() -> str:
    import sysconfig

    return sysconfig.get_path("scripts") or ""


def _cmd_path(args) -> int:
    import os
    import sys

    scripts = _scripts_dir()
    on_path = scripts in os.environ.get("PATH", "").split(os.pathsep)
    print(f"scripts directory: {scripts}")
    print(f"on PATH:           {'yes' if on_path else 'NO'}")
    if args.action == "show":
        if not on_path:
            print("fix: agent-memory path install   (or: python -m agent_memory_os.cli path install)")
        return 0 if on_path else 1
    if on_path:
        print("nothing to do.")
        return 0
    if sys.platform == "win32":
        print("run this in PowerShell, then open a NEW terminal:")
        print(f'  setx PATH "$env:PATH;{scripts}"')
        return 0
    shell = os.path.basename(os.environ.get("SHELL", "sh"))
    if shell == "fish":
        rc = Path("~/.config/fish/config.fish").expanduser()
        line = f'fish_add_path "{scripts}"'
    elif shell == "zsh":
        rc = Path("~/.zshrc").expanduser()
        line = f'export PATH="$PATH:{scripts}"'
    else:  # bash and everything sh-like
        rc = Path("~/.bash_profile" if sys.platform == "darwin" else "~/.bashrc").expanduser()
        line = f'export PATH="$PATH:{scripts}"'
    marker = "# added by agent-memory path install"
    existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
    if line in existing:
        print(f"{rc} already contains the export line — open a new terminal.")
        return 0
    # Replace any previous managed line (e.g. a stale scripts dir after a venv
    # move) rather than appending a second one, so PATH never accumulates dead
    # entries. The marker comment + the line after it are our managed block.
    import re

    block = f"{marker}\n{line}"
    if marker in existing:
        existing = re.sub(rf"\n?{re.escape(marker)}\n[^\n]*\n?", "\n", existing)
    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.write_text(existing.rstrip("\n") + f"\n\n{block}\n" if existing.strip()
                  else f"{block}\n", encoding="utf-8")
    print(f"updated {rc} — open a new terminal (or: source {rc})")
    return 0


def _cmd_team_update(client, own_version: str) -> int:
    """Trigger self-update on every opted-in peer (their console must run
    with AGENT_MEMORY_ALLOW_TEAM_UPDATE=1; the sync token then authorizes
    POST /api/maintenance/update-run on that node — and nothing else new)."""
    import json as _json
    import urllib.request

    from .discovery import probe_node

    peers = client.store.list_peers()
    if not peers:
        print("no peers registered — nothing to update")
        return 0
    failures = 0
    for peer in peers:
        url = str(peer["url"])
        label = peer.get("name") or url
        probe = probe_node(url)
        if not probe.is_amos:
            print(f"  {label}: offline — skipped")
            continue
        peer_ver = probe.extras.get("version") or ""
        if peer_ver == own_version:
            print(f"  {label}: already {own_version}")
            continue
        token = client.store.peer_token(url)
        request = urllib.request.Request(
            url.rstrip("/") + "/api/maintenance/update-run?confirm=update",
            method="POST", data=b"",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=TEAM_UPDATE_REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
                payload = _json.loads(response.read().decode("utf-8"))
            print(f"  {label}: {peer_ver or '?'} → update started ({payload.get('status', 'ok')})")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            code = getattr(exc, "code", None)
            if code in (401, 403):
                print(f"  {label}: refused — that node has not opted in "
                      f"(start its console with AGENT_MEMORY_ALLOW_TEAM_UPDATE=1)")
            else:
                print(f"  {label}: failed — {exc}")
    return 1 if failures else 0


def _cmd_service(args) -> int:
    from . import service as svc
    from .settings import load_instance_settings

    settings = load_instance_settings(args.home)
    host = args.host or settings.host
    port = args.port if args.port is not None else settings.port
    if args.action == "install" and args.port is None:
        # Multi-account hosts: if another instance (another account's console)
        # already holds this port, silently baking it into the unit would make
        # the two services race at login. Pick the next free port and PERSIST
        # it to instance.toml so this account's unit and peer URLs stay stable.
        from .settings import find_available_port, update_instance_settings

        chosen = find_available_port(host, port)
        if chosen != port:
            print(f"port {port} is taken on this host — using {chosen} "
                  f"(persisted to instance.toml)")
            port = chosen
            if not args.dry_run:
                update_instance_settings(args.home, port=port)
    config = svc.make_config(args.home, host, port)
    if args.action == "install":
        try:
            actions = svc.install(config, dry_run=args.dry_run)
        except RuntimeError as exc:
            print(f"service install failed: {exc}")
            return 1
        for action in actions:
            print(("would: " if args.dry_run else "") + action)
        if not args.dry_run:
            print(f"installed — console at http://{host}:{port}/ (starts at login)")
        return 0
    if args.action == "uninstall":
        for action in svc.uninstall(dry_run=args.dry_run):
            print(("would: " if args.dry_run else "") + action)
        return 0
    result = svc.control(args.action)
    output = (result.stdout or result.stderr or "").strip()
    if output:
        print(output.splitlines()[0] if args.action == "status" else output)
    print(f"{args.action}: {'ok' if result.returncode == 0 else 'not running / not installed'}")
    return 0 if result.returncode == 0 else 1


def _cmd_token(args) -> int:
    from . import tokens

    tier = "sync" if getattr(args, "sync", False) else (
        "readonly" if getattr(args, "readonly", False) else "full")
    flag = {"full": "", "readonly": " --readonly", "sync": " --sync"}[tier]
    label = {"full": "Web UI token", "readonly": "read-only Web UI token",
             "sync": "sync token"}[tier]
    existing = tokens.load_token(args.home, tier=tier)
    if args.action == "show":
        if existing is None:
            print(f"no {label} set — run: agent-memory token create{flag}")
            return 1
        print(existing)
        return 0
    if args.action == "disable":
        if tokens.delete_token(args.home, tier=tier):
            print(f"{label} removed")
        else:
            print(f"no {label} was set")
        return 0
    if args.action == "create" and existing is not None:
        print(f"a {label} already exists — use `... token rotate{flag}` to replace it,")
        print(f"or `agent-memory token show{flag}` to display it")
        return 1
    token = tokens.create_token(args.home, tier=tier)
    print(f"{label} saved to {tokens.token_path(args.home, tier=tier)} (mode 600):")
    print()
    print(f"  {token}")
    print()
    if tier == "sync":
        print("Give this to a peer so it can join the mesh WITHOUT your admin token:")
        print("  on the peer:  agent-memory peers add <this-node-url> --peer-token <token>")
        print("It authorizes only /api/sync/* and /api/node.")
    else:
        print("agent-memory-web now requires this token on every /api/ route.")
        print("The Web UI will prompt for it on first use.")
    return 0


def _cmd_doctor(args) -> int:
    import importlib.util
    import sqlite3
    import subprocess
    import sys

    from . import tokens

    def present(module: str) -> bool:
        return importlib.util.find_spec(module) is not None

    checks = {
        "api": (["fastapi", "uvicorn"], "Web UI (agent-memory-web)"),
        "mcp": (["mcp"], "MCP server for agent integration"),
        "semantic": (["numpy", "turbovec"], "turbovec semantic vector recall"),
        "secure-sync": (["cryptography"], "encrypted federation transport (AGENT_MEMORY_SYNC_KEY)"),
    }
    fts_ok = True
    try:
        probe = sqlite3.connect(":memory:")
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        probe.close()
    except sqlite3.OperationalError:
        fts_ok = False
    print(f"[{'ok' if fts_ok else 'FAIL'}] SQLite FTS5 (required)")

    missing_extras: list[str] = []
    for extra, (modules, description) in checks.items():
        ok = all(present(module) for module in modules)
        print(f"[{'ok' if ok else 'missing'}] {extra}: {description}")
        if not ok:
            missing_extras.append(extra)

    from .agents_config import config_path, load_agents_config

    try:
        configured = load_agents_config(args.home)
        print(f"[{'ok' if configured else 'none'}] agents.toml "
              f"({len(configured)} agents declared at {config_path(args.home)})"
              if configured else
              f"[none] agents.toml (optional; declare your fleet at {config_path(args.home)})")
    except ValueError as exc:
        print(f"[FAIL] agents.toml: {exc}")

    token_set = tokens.load_token(args.home) is not None
    print(f"[{'ok' if token_set else 'none'}] Web UI token "
          f"({'set' if token_set else 'run: agent-memory token create'})")

    # Stale running processes: on-disk version is current but a live web/MCP
    # process started before it landed still serves the OLD code.
    stale = _stale_amos_processes()
    if stale:
        print(f"[warn] {len(stale)} process(es) predate the installed version and likely run old code:")
        for pid, kind, _cmd in stale:
            hint = ("agent-memory service restart / kill+relaunch" if kind == "web"
                    else "restart the host app, e.g. Claude Code")
            print(f"        [{kind}] pid {pid} → {hint}")
    else:
        print("[ok] no stale processes (running code matches the installed version)")

    if missing_extras:
        spec = f"agent-memory-os[{','.join(missing_extras)}]"
        if args.install:
            print(f"installing: {spec}")
            result = subprocess.run([sys.executable, "-m", "pip", "install", spec])
            return result.returncode
        print()
        print(f"install everything missing with: pip install '{spec}'")
        print("or re-run: agent-memory doctor --install")
        return 1
    if not fts_ok:
        return 1
    import os as _os

    scripts = _scripts_dir()
    if scripts and scripts not in _os.environ.get("PATH", "").split(_os.pathsep):
        print(f"[warn] scripts dir not on PATH ({scripts}) — 'agent-memory' may be"
              " 'command not found' in new shells. Fix: agent-memory path install")
    # Same-host awareness: other accounts' nodes are worth knowing about.
    try:
        from .discovery import scan_local_nodes
        from .settings import load_instance_settings

        others = scan_local_nodes(
            exclude_ports={load_instance_settings(args.home).port})
        if others:
            names = ", ".join(f"{n.node_name} ({n.url})" for n in others[:3])
            print(f"[info] {len(others)} other AgentMemoryOS node(s) on this host: {names}")
            print("       to share memory: agent-memory neighbors  →  team invite / join")
    except Exception:  # noqa: BLE001 - a hint must never fail doctor
        pass
    print("all good.")
    return 0


def _cmd_backup(args) -> int:
    import sqlite3
    from pathlib import Path

    from .tokens import resolve_home

    db_path = resolve_home(args.home) / "memories.db"
    if not db_path.exists():
        print(f"no database at {db_path}")
        return 1
    dest = Path(args.dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(dest)
    try:
        # sqlite3 online backup: consistent even while another process writes (WAL)
        source.backup(target)
    finally:
        target.close()
        source.close()
    print(f"backed up {db_path} -> {dest}")
    if getattr(args, "keep", 0) and args.keep > 0:
        removed = _rotate_backups(dest, keep=args.keep)
        for path in removed:
            print(f"rotated out old backup: {path}")
    return 0


#: The live database and its WAL sidecars — rotation must NEVER delete these.
_LIVE_DB_NAMES = frozenset({"memories.db", "memories.db-wal", "memories.db-shm"})


def _rotate_backups(latest, *, keep: int) -> list:
    """Keep only the `keep` newest backups in `latest`'s rotation series; delete
    the rest. Returns the paths removed.

    A file is in the series only if its name is `<prefix><marker>` where prefix
    is `latest`'s stem with its trailing datestamp removed and the marker begins
    with a separator or digit — so `mem-2026-07-12.db` and `mem-2026-07-11.db`
    rotate together while `memories.db` (marker 'ories…' starts with a letter) is
    excluded. The live database and its WAL sidecars are ALSO excluded by name as
    a hard backstop, so rotation can never delete the database it backs up.
    """
    import re
    from pathlib import Path

    latest = Path(latest)
    prefix = re.sub(r"[-_0-9]+$", "", latest.stem)  # strip trailing date-ish chars
    if not prefix:
        return []
    siblings = []
    for p in latest.parent.glob(f"{prefix}*"):
        if not p.is_file() or p.suffix != latest.suffix:
            continue
        if p.name in _LIVE_DB_NAMES:
            continue
        marker = p.name[len(prefix):]
        # Require the char after the prefix to be a separator or digit so a mere
        # name-prefix collision (memories.db vs prefix 'mem') is never matched.
        if not re.match(r"[-_.0-9]", marker):
            continue
        siblings.append(p)
    # Newest first by mtime; keep the first `keep`, remove the rest.
    siblings.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for path in siblings[keep:]:
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            pass
    return removed


def _cmd_restore(args) -> int:
    import sqlite3
    from pathlib import Path

    from .tokens import resolve_home

    src = Path(args.src).expanduser()
    if not src.exists():
        print(f"backup not found: {src}")
        return 1
    db_path = resolve_home(args.home) / "memories.db"
    if db_path.exists() and not args.force:
        print(f"database already exists at {db_path} — pass --force to overwrite")
        return 1
    db_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(src)
    target = sqlite3.connect(db_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    print(f"restored {src} -> {db_path}")
    print("disposable indexes rebuild automatically; run `agent-memory stats` to verify")
    return 0


def _report_orphans(client) -> None:
    """After a member removal, warn if any memory is now reachable by nobody."""
    n = client.orphan_count()
    if n:
        print(f"note: {n} memory(ies) are now orphaned (scoped to a group with no "
              f"members — visible only to admin). Review: agent-memory maintenance "
              f"orphans   |   clean: agent-memory maintenance orphans --delete")


def _in_docker() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text()
    except OSError:
        return False


_PYPI_LAST_ERROR: str | None = None


def _pypi_latest(pkg: str) -> str | None:
    global _PYPI_LAST_ERROR
    import urllib.request
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=PYPI_REQUEST_TIMEOUT_SECONDS) as resp:
            _PYPI_LAST_ERROR = None
            return json.load(resp)["info"]["version"]
    except Exception as exc:  # noqa: BLE001 - offline / unreachable is a normal outcome
        _PYPI_LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return None


def _running_amos_processes() -> list[tuple[int, str, str]]:
    """Find running AgentMemoryOS processes: (pid, kind, cmdline).

    kind is "web" (console, restartable by us) or "mcp" (stdio child owned by a
    host app such as Claude Code — never killed here, only reported).
    """
    import os
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 'Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }'],
                text=True, timeout=PROCESS_LIST_TIMEOUT_SECONDS)
            rows = [line.split("\t", 1) for line in out.splitlines() if "\t" in line]
        else:
            out = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True, timeout=PROCESS_LIST_TIMEOUT_SECONDS)
            rows = [line.strip().split(None, 1) for line in out.splitlines() if line.strip()]
    except Exception:  # noqa: BLE001 - process listing is best-effort
        return []
    me = os.getpid()
    procs: list[tuple[int, str, str]] = []
    for row in rows:
        if len(row) != 2:
            continue
        pid_s, cmd = row
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == me:
            continue
        kind = _classify_amos_cmdline(cmd)
        if kind:
            procs.append((pid, kind, cmd.strip()))
    return procs


def _classify_amos_cmdline(cmd: str) -> str | None:
    """"web" / "mcp" / None for a raw process command line.

    Token-exact matching: host apps (e.g. Claude Code) can carry the module
    name inside a config argument without BEING the server — substring
    matching would misreport them as restart targets.
    """
    tokens = cmd.split()
    if not tokens:
        return None

    def _basename(token: str) -> str:
        return token.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")

    interp = _basename(tokens[0]).lower()
    if interp in ("grep", "egrep", "fgrep", "rg", "less", "more", "tail", "vim", "nano"):
        return None
    is_python = "python" in interp
    basenames = [_basename(t) for t in tokens]
    module_pairs = {
        (tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)
    }
    if "agent-memory-web" in basenames or (
        is_python and ("-m", "agent_memory_os.web_app") in module_pairs
    ):
        return "web"
    if is_python and ("-m", "agent_memory_os.mcp_server") in module_pairs:
        return "mcp"
    return None


def _parse_etime(etime: str) -> int | None:
    """ps etime ([[dd-]hh:]mm:ss) -> elapsed seconds."""
    import re

    m = re.match(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$", etime.strip())
    if not m:
        return None
    d, h, mn, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mn) * 60 + s


def _proc_start_ts(pid: int) -> float | None:
    import subprocess
    import sys
    import time

    if sys.platform == "win32":
        return None  # unknown -> caller treats as not-provably-stale
    try:
        out = subprocess.check_output(["ps", "-p", str(pid), "-o", "etime="], text=True, timeout=PROCESS_ELAPSED_QUERY_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        return None
    elapsed = _parse_etime(out)
    return None if elapsed is None else time.time() - elapsed


def _install_mtime() -> float | None:
    """When the installed package files were last written (== install/upgrade time)."""
    try:
        import agent_memory_os

        return Path(agent_memory_os.__file__).stat().st_mtime
    except Exception:  # noqa: BLE001
        return None


def _stale_amos_processes() -> list[tuple[int, str, str]]:
    """Running processes that started before the current install landed on disk."""
    installed = _install_mtime()
    if installed is None:
        return []
    stale = []
    for pid, kind, cmd in _running_amos_processes():
        started = _proc_start_ts(pid)
        # 90s slack absorbs ps's minute-resolution etime.
        if started is not None and started < installed - STALE_PROCESS_START_SLACK_SECONDS:
            stale.append((pid, kind, cmd))
    return stale


def _restart_web_from_pidfile(home) -> str:
    """Restart the console recorded in <home>/web.pid.

    SECURITY: the relaunch argv comes from the pidfile that the console wrote
    about ITSELF — never from `ps` output, which any local process could spoof
    into being re-executed. Only the recorded pid is signalled, and only if it
    is actually alive. Returns a short status string for the caller to print.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    from .pidfile import read_web_pidfile
    from .tokens import resolve_home

    rec = read_web_pidfile(home)
    if not rec:
        return "no pidfile — restart the console manually"
    pid, argv, cwd = rec["pid"], rec["argv"], rec.get("cwd") or None
    if sys.platform == "win32":
        return "automatic restart unsupported on Windows — restart the console manually"
    try:
        os.kill(pid, 0)  # is the recorded process actually alive & signal-able?
    except ProcessLookupError:
        return f"recorded pid {pid} is not running — start the console manually"
    except PermissionError:
        return f"recorded pid {pid} not owned by this user — restart it manually"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return f"could not signal pid {pid} — restart the console manually"
    for _ in range(WEB_RESTART_POLL_ATTEMPTS):  # up to ~10s for the port to be released
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(WEB_RESTART_POLL_INTERVAL_SECONDS)
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    try:
        # logs/web.log: the same location the installed service logs to, and
        # inside the console log viewer's whitelist — a root-level web.log was
        # invisible to Tools -> Logs.
        log_dir = Path(resolve_home(home)) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "web.log", "ab")  # noqa: SIM115 - handed to child
        stdout, stderr = log, subprocess.STDOUT
    except OSError:
        pass
    try:
        child = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr,
                                 start_new_session=True)
    except Exception as exc:  # noqa: BLE001
        return f"relaunch failed ({exc}) — restart the console manually"
    time.sleep(WEB_RESTART_LIVENESS_DELAY_SECONDS)  # liveness: confirm the new process didn't immediately die
    if child.poll() is not None:
        return (f"relaunched process exited (code {child.returncode}) — the old "
                f"port may still be held; restart the console manually")
    return f"restarted (new pid {child.pid})"


def _web_service_installed() -> bool:
    import subprocess
    import sys

    from . import service as svc

    if sys.platform == "win32":
        # No unit file — query the scheduled task instead.
        try:
            return subprocess.run(
                ["schtasks", "/Query", "/TN", svc.SERVICE_NAME],
                capture_output=True, timeout=SCHEDULED_TASK_QUERY_TIMEOUT_SECONDS,
            ).returncode == 0
        except Exception:  # noqa: BLE001
            return False
    try:
        return svc._unit_path(sys.platform).exists()
    except Exception:  # noqa: BLE001
        return False


def _handle_running_processes(*, assume_yes: bool, no_restart: bool, home=None) -> None:
    """Post-upgrade cleanup: everything still running loads the OLD code."""
    procs = _running_amos_processes()
    web = [p for p in procs if p[1] == "web"]
    mcp = [p for p in procs if p[1] == "mcp"]
    if not web and not mcp:
        return
    print("\nRunning processes still loaded with the previous version:")
    for pid, kind, cmd in web + mcp:
        print(f"  [{kind}] pid {pid}: {cmd[:100]}")
    if web:
        if no_restart:
            print("Web console NOT restarted (--no-restart). Restart it to load the new version.")
        elif _web_service_installed():
            from . import service as svc

            result = svc.control("restart")
            print(f"web console service restart: {'ok' if result.returncode == 0 else 'failed'}")
        else:
            do_restart = assume_yes
            if not do_restart:
                try:
                    resp = input("Restart the web console now to load the new version? [Y/n] ").strip().lower()
                except EOFError:
                    resp = "n"
                do_restart = resp in ("", "y", "yes")
            if do_restart:
                print(f"web console: {_restart_web_from_pidfile(home)}")
            else:
                print("Skipped. Restart the web console manually to load the new version.")
    if mcp:
        print("MCP server(s) are owned by their host app and were not touched.")
        print("Restart the host app (e.g. Claude Code) to load the new version.")


def _warn_stale_processes() -> None:
    """`update --check` / already-latest path: disk is current, memory may not be."""
    stale = _stale_amos_processes()
    if not stale:
        return
    print("\nNote: these processes started BEFORE the installed version landed and are")
    print("likely still running older code (a pip upgrade never touches live processes):")
    for pid, kind, cmd in stale:
        print(f"  [{kind}] pid {pid}: {cmd[:100]}")
    if any(k == "web" for _, k, _ in stale):
        print("Web console: restart it (agent-memory service restart, or kill + relaunch).")
    if any(k == "mcp" for _, k, _ in stale):
        print("MCP server: restart the host app (e.g. Claude Code).")


def _cmd_update(args) -> int:
    import platform
    import subprocess
    import sys
    from importlib.metadata import PackageNotFoundError, version

    try:
        current = version("agent-memory-os")
    except PackageNotFoundError:
        current = "unknown"
    docker = _in_docker()
    latest = _pypi_latest("agent-memory-os")
    print(f"current:    {current}")
    print(f"latest:     {latest or 'unknown (could not reach PyPI)'}")
    print(f"platform:   {platform.system()} {platform.machine()}")
    print(f"deployment: {'Docker container' if docker else 'host (pip)'}")
    if not latest:
        print(f"Could not reach PyPI ({_PYPI_LAST_ERROR or 'unknown error'}).")
        if _PYPI_LAST_ERROR and "CERTIFICATE" in _PYPI_LAST_ERROR.upper():
            print("Your Python is missing CA certificates. Fix with:  pip install -U certifi")
            print("(on macOS you may also need to run the 'Install Certificates.command' for your Python).")
        return 1
    if latest == current:
        print("Already on the latest version.")
        _warn_stale_processes()
        return 0
    print(f"\nA newer version is available: {current} -> {latest}")
    if args.check:
        _warn_stale_processes()
        return 0
    if docker:
        # A container can't pip-upgrade itself in place; guide the host update.
        print("\nDocker deployment — update by pulling the new image and recreating:")
        print(f"  docker pull yamantaka520/agent-memory-os:{latest}")
        print("  docker compose up -d          # or re-run docker run with the new tag")
        print("Data in the /data volume persists; migrations self-apply on start.")
        return 0
    if not args.yes:
        try:
            resp = input(f"Upgrade agent-memory-os {current} -> {latest} via pip now? [y/N] ").strip().lower()
        except EOFError:
            resp = ""
        if resp not in ("y", "yes"):
            print("Aborted. Run with --yes to skip this prompt.")
            return 0
    cmd = [sys.executable, "-m", "pip", "install", "-U", "agent-memory-os[full]"]
    print("running:", " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc == 0:
        _handle_running_processes(
            assume_yes=args.yes, no_restart=args.no_restart, home=args.home,
        )
    return rc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "path":
        return _cmd_path(args)
    if args.command == "update":
        rc = _cmd_update(args)
        if getattr(args, "team", False):
            import importlib.metadata as _md

            client = MemoryClient(home=args.home)
            try:
                print("team update:")
                team_rc = _cmd_team_update(client, _md.version("agent-memory-os"))
            finally:
                client.close()
            return rc or team_rc
        return rc
    if args.command == "service":
        return _cmd_service(args)
    if args.command == "token":
        return _cmd_token(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "backup":
        return _cmd_backup(args)
    if args.command == "restore":
        return _cmd_restore(args)
    if args.command == "hermes":
        from .hermes_plugin import install_shim, shim_dir, uninstall_shim

        if args.action == "install":
            report = install_shim(args.hermes_home)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(f"Hermes provider shim installed: {report['installed']} "
                      f"(agent-memory-os {report['version']})")
                print("Next steps:")
                for step in report["next_steps"]:
                    print(f"  {step}")
        else:
            removed = uninstall_shim(args.hermes_home)
            target = shim_dir(args.hermes_home)
            if args.json:
                print(json.dumps({"removed": removed, "path": str(target)}))
            else:
                print(f"{'Removed' if removed else 'Nothing to remove at'} {target}")
        return 0

    if args.command == "shadow-summary":
        summary = summarize_shadow_log(args.log, last_n=args.last)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                f"records={summary['records']} activation_gate={summary['activation_gate']} "
                f"mean_top_k_hit_rate={summary['mean_top_k_hit_rate']} "
                f"p99_candidate_latency_ms={summary['p99_candidate_latency_ms']} "
                f"acl_leakage_count={summary['acl_leakage_count']} "
                f"production_injection_count={summary['production_injection_count']}"
            )
        return 0

    client = MemoryClient(home=args.home)
    try:
        if args.command == "check":
            report = client.integrity_check()
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        if args.command == "node":
            from .settings import load_instance_settings, update_instance_settings

            if args.set_name is not None or args.set_host is not None or args.set_port is not None:
                settings = update_instance_settings(
                    args.home, node_name=args.set_name, host=args.set_host, port=args.set_port
                )
            else:
                settings = load_instance_settings(args.home)
            print(json.dumps({
                "node_name": settings.node_name, "host": settings.host, "port": settings.port,
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "agent":
            if args.action == "rename":
                if not (args.old_id and args.new_id):
                    print("agent rename requires <old_id> <new_id>"); return 2
                # Preview what moves so a rename can never silently strand
                # memories under the old id. owner_counts is unfiltered.
                src = {r["owner"]: r for r in client.owner_counts()}.get(args.old_id)
                live = src["memories"] if src else 0
                arch = src["archived"] if src else 0
                print(f"rename {args.old_id} -> {args.new_id} will move "
                      f"{live} live + {arch} archived memories, plus any "
                      f"agent:{args.old_id} grants, memberships, and recall profile.")
                if not args.yes:
                    reply = input("proceed? [y/N]: ")
                    if reply.strip().lower() not in ("y", "yes"):
                        print("aborted"); return 1
                try:
                    counts = client.store.rename_agent(args.old_id, args.new_id)
                except ValueError as exc:
                    print(f"error: {exc}"); return 2
                client.cache.clear()  # ownership/ACL moved — drop stale visibility
                print(f"renamed {args.old_id} -> {args.new_id}:")
                for key, value in counts.items():
                    print(f"  {key}: {value}")
                print("note: peers converge on the next sync (ACL clock bumped)")
                if live or arch:
                    print(f"note: if a running service or MCP client still uses "
                          f"'{args.old_id}', point it at '{args.new_id}' — it won't "
                          f"see these memories until you do.")
                return 0

        if args.command == "owner":
            if args.action == "list":
                rows = client.owner_counts()
                print(json.dumps(rows, ensure_ascii=False, indent=2))
                return 0
            if args.action == "reassign":
                if not (args.old_owner and args.new_owner):
                    print("owner reassign requires <old_owner> <new_owner>"); return 2
                owners = {r["owner"]: r for r in client.owner_counts()}
                src = owners.get(args.old_owner)
                live = src["memories"] if src else 0
                arch = src["archived"] if src else 0
                if not (live or arch):
                    print(f"note: {args.old_owner} owns no memories to move "
                          f"(grants/memberships, if any, still migrate).")
                dst = owners.get(args.new_owner)
                dst_registered = bool(dst and dst["registered_agent"])
                print(f"reassign will move {live} live + {arch} archived memories "
                      f"(plus agent:{args.old_owner} grants, memberships, profile) "
                      f"from {args.old_owner} -> {args.new_owner}.")
                if not dst_registered and not args.no_register:
                    print(f"  {args.new_owner} is not a registered agent yet — it "
                          f"will be registered so the moved memories are recognized.")
                elif not dst_registered and args.no_register:
                    print(f"  WARNING: {args.new_owner} is not a registered agent and "
                          f"--no-register was given; the moved memories will be owned "
                          f"by an identity no console surface recognizes.")
                if not args.yes:
                    reply = input("proceed? [y/N]: ")
                    if reply.strip().lower() not in ("y", "yes"):
                        print("aborted"); return 1
                try:
                    counts = client.reassign_owner(
                        args.old_owner, args.new_owner,
                        register_target=not args.no_register)
                except ValueError as exc:
                    print(f"error: {exc}"); return 2
                registered = counts.pop("target_registered", 0)
                print(f"reassigned {args.old_owner} -> {args.new_owner}:")
                for key, value in counts.items():
                    print(f"  {key}: {value}")
                if registered:
                    print(f"  registered '{args.new_owner}' as an agent "
                          f"(now recognized by the console and pickers)")
                print("note: peers converge on the next sync (ACL clock bumped)")
                return 0
            if args.action == "delete":
                if not args.old_owner:
                    print("owner delete requires <owner>"); return 2
                target = args.old_owner
                if not args.yes:
                    existing = {r["owner"]: r for r in client.owner_counts()}
                    row = existing.get(target)
                    n = (row["memories"] + row["archived"]) if row else 0
                    reply = input(
                        f"permanently delete ALL {n} memories owned by "
                        f"'{target}'? this cannot be undone [y/N]: ")
                    if reply.strip().lower() not in ("y", "yes"):
                        print("aborted"); return 1
                counts = client.purge_owner(target)
                print(f"deleted owner {target}:")
                for key, value in counts.items():
                    print(f"  {key}: {value}")
                return 0

        if args.command == "fleet":
            from . import crypto

            if args.action == "keygen":
                existing = crypto.load_fleet_key(args.home)
                if existing and not args.force:
                    print(f"fleet key already exists (key id {existing.get('key_id')}); "
                          "pass --force to replace it — nodes that granted the old "
                          "key will need a re-grant")
                    return 2
                keypair = crypto.generate_fleet_keypair()
                path = crypto.save_fleet_key(args.home, keypair)
                print(f"fleet admin keypair written to {path} (mode 600)")
                print(f"  key id:     {keypair['key_id']}")
                print(f"  public key: {keypair['public_key']}")
                print("on every node this console should manage, run:")
                print(f"  agent-memory fleet grant {keypair['public_key']} --caps manage")
                print("(add --caps manage,read-private to also allow reading memory content)")
                return 0
            if args.action == "grant":
                if not args.value:
                    print("fleet grant requires the console's public key"); return 2
                caps = [c.strip() for c in args.caps.split(",") if c.strip()]
                try:
                    grant = client.store.grant_fleet_admin(args.value, caps)
                except ValueError as exc:
                    print(f"error: {exc}"); return 2
                print(f"granted fleet admin {grant['key_id']} caps={','.join(grant['caps'])}")
                if "read-private" in grant["caps"]:
                    print("note: read-private lets this key read ALL memory content on "
                          "this node, including private memories — every such read is "
                          "recorded in the org audit log")
                return 0
            if args.action == "revoke":
                if not args.value:
                    print("fleet revoke requires a key id"); return 2
                if client.store.revoke_fleet_admin(args.value):
                    print(f"revoked fleet admin {args.value} — signed requests from "
                          "this key are refused immediately")
                    return 0
                print(f"no active grant for key id {args.value}")
                return 1
            if args.action == "list":
                admins = client.store.list_fleet_admins()
                own = crypto.load_fleet_key(args.home)
                if own:
                    print(f"this node's own fleet key: {own.get('key_id')} "
                          f"(private key held here — this node can act as console)")
                print(json.dumps(admins, ensure_ascii=False, indent=2))
                return 0
            from .fleet import FleetKeyMissing, fleet_status, fleet_trigger

            if args.action == "status":
                try:
                    report = fleet_status(client, args.home)
                except FleetKeyMissing as exc:
                    print(f"error: {exc}"); return 2
                if args.as_json:
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                    return 0
                console = report["console"]
                print(f"console: {console['node_name']} (key {console['key_id']}, "
                      f"v{console['version']}) — {console['memories']} memories, "
                      f"{console['links']} links, {len(console['owners'])} owners")
                if not report["nodes"]:
                    print("no managed nodes — register peers (join/peers add), then "
                          "run `fleet grant` on each")
                    return 0
                for n in report["nodes"]:
                    if not n["reachable"]:
                        mark, extra = "✗ offline", n["detail"]
                    elif not n["authorized"]:
                        mark, extra = "! unauthorized", n["detail"]
                    else:
                        owners = len(n["owners"] or [])
                        mark = "✓ ok"
                        extra = (f"v{n['version']} — {n['memories']} memories, "
                                 f"{n['links']} links, {owners} owners")
                    name = n["name"] or n["node_name"] or n["url"]
                    print(f"  {mark:15s} {name:20s} {n['url']}  {extra}")
                if report["version_drift"]:
                    print(f"⚠ version drift across the fleet: {report['versions']} — "
                          "run `agent-memory fleet update` to converge")
                return 0
            if args.action in ("sync", "update"):
                try:
                    results = fleet_trigger(client, args.home, args.action,
                                            only_url=args.node)
                except (FleetKeyMissing, ValueError) as exc:
                    print(f"error: {exc}"); return 2
                if not results:
                    print("no managed nodes"); return 0
                failed = 0
                for r in results:
                    state = "ok" if r["ok"] else f"FAILED (HTTP {r['status']})"
                    print(f"  {r['name'] or r['url']}: {state}")
                    if not r["ok"]:
                        failed += 1
                        detail = r["response"]
                        if isinstance(detail, dict):
                            detail = detail.get("detail", detail)
                        print(f"    {detail}")
                return 1 if failed else 0

        if args.command == "join":
            return _cmd_join(client, args)

        if args.command == "status":
            return _cmd_status(client, args)

        if args.command == "neighbors":
            return _cmd_neighbors(client, args)

        if args.command == "team":
            s = client.store
            if args.action == "invite":
                from .pairing import issue_invite
                if not args.team_id:
                    print("team invite requires a team id"); return 2
                try:
                    invite = issue_invite(client, args.team_id, ttl_seconds=args.ttl)
                except ValueError as exc:
                    print(f"error: {exc}"); return 2
                print(f"pairing code (single-use, expires {invite['expires_at']}):")
                print(f"\n  {invite['code']}\n")
                print("On the joining node run:")
                print(f"  agent-memory join {invite['code']} --url http://<this-host>:<this-port>")
                return 0
            if args.action == "list":
                print(json.dumps(s.list_teams(), ensure_ascii=False, indent=2))
            elif args.action == "create":
                if not args.team_id:
                    print("team create requires a team id"); return 2
                print(json.dumps(s.create_team(args.team_id, name=args.name), ensure_ascii=False))
            elif args.action == "rename":
                if not (args.team_id and args.agent_id):
                    print("team rename requires <old_team_id> <new_team_id>"); return 2
                old_id, new_id = args.team_id, args.agent_id
                pre = client.team_rename_preview(old_id, new_id)
                if not pre["exists"]:
                    print(f"error: team not found: {old_id}"); return 2
                if pre["target_exists"]:
                    print(f"error: team id already exists: {new_id} "
                          f"(rename never merges two teams)"); return 2
                print(f"rename {old_id} -> {new_id} will move:")
                print(f"  {pre['members']} member(s)")
                print(f"  {len(pre['projects'])} project(s)"
                      + (f": {', '.join(pre['projects'])}" if pre["projects"] else "")
                      + f" ({pre['project_members']} project membership rows)")
                print(f"  {pre['explicit_grants']} memory visibility grant(s) "
                      f"team:{old_id} -> team:{new_id}"
                      + (f", plus {pre['archived_grants']} archived"
                         if pre["archived_grants"] else ""))
                if pre["bare_grants"]:
                    print(f"  {pre['bare_grants']} memory/ies using the legacy bare "
                          f"'team' grant (their source.team_id is repointed)")
                if pre["content_mentions"]:
                    print(f"  note: {pre['content_mentions']} memory/ies mention "
                          f"'{old_id}' in their text — prose is history and is left as-is")
                if pre["sync_peers"]:
                    print(f"  WARNING: a rename is local state and does not propagate as a "
                          f"deletion. These peers may keep team:{old_id} as an inert orphan: "
                          f"{', '.join(pre['sync_peers'])}")
                if args.dry_run:
                    print("dry run — nothing changed")
                    return 0
                if not args.yes:
                    reply = input("proceed? [y/N]: ")
                    if reply.strip().lower() not in ("y", "yes"):
                        print("aborted"); return 1
                try:
                    result = client.rename_team(old_id, new_id,
                                               name=args.name or None)
                except (ValueError, KeyError) as exc:
                    print(f"error: {exc}"); return 2
                print(json.dumps(result, ensure_ascii=False))
                print(json.dumps(s.get_team(new_id), ensure_ascii=False))
            elif args.action == "delete":
                print("deleted" if s.delete_team(args.team_id) else "not found")
            elif args.action in ("add-member", "remove-member"):
                if not (args.team_id and args.agent_id):
                    print(f"team {args.action} requires <team_id> <agent_id>"); return 2
                if args.action == "add-member":
                    s.add_team_member(args.team_id, args.agent_id)
                else:
                    s.remove_team_member(args.team_id, args.agent_id)
                    _report_orphans(client)
                print(json.dumps(s.get_team(args.team_id), ensure_ascii=False))
            return 0
        if args.command == "project":
            s = client.store
            if args.action == "list":
                print(json.dumps(s.list_projects(args.team_id), ensure_ascii=False, indent=2))
            elif args.action == "create":
                if not (args.project_id and args.team_id):
                    print("project create requires <project_id> --team <team_id>"); return 2
                print(json.dumps(s.create_project(args.project_id, args.team_id, name=args.name), ensure_ascii=False))
            elif args.action == "delete":
                print("deleted" if s.delete_project(args.project_id) else "not found")
            elif args.action in ("add-member", "remove-member"):
                if not (args.project_id and args.agent_id):
                    print(f"project {args.action} requires <project_id> <agent_id>"); return 2
                if args.action == "add-member":
                    s.add_project_member(args.project_id, args.agent_id)
                else:
                    s.remove_project_member(args.project_id, args.agent_id)
                    _report_orphans(client)
                print(json.dumps(s.get_project(args.project_id), ensure_ascii=False))
            return 0
        if args.command == "maintenance":
            if args.action == "scan":
                print(json.dumps(client.maintenance_scan(), ensure_ascii=False, indent=2))
            elif args.action == "orphans":
                orphans = client.find_orphan_memories()
                if args.delete:
                    print(json.dumps(client.delete_orphan_memories(), ensure_ascii=False))
                else:
                    print(f"{len(orphans)} orphan memories (scoped to an empty/deleted group):")
                    for o in orphans[:50]:
                        print(f"  {o['id']}  {o['visibility']}  {o['content']!r}")
                    if orphans:
                        print("delete them with: agent-memory maintenance orphans --delete")
            elif args.action == "reindex":
                print(json.dumps(client.rebuild_indexes(), ensure_ascii=False))
            elif args.action == "vacuum":
                print(json.dumps(client.vacuum(), ensure_ascii=False))
            return 0
        if args.command == "peers":
            if args.action == "list":
                print(json.dumps(client.store.list_peers(), ensure_ascii=False, indent=2))
                return 0
            if not args.url:
                print("peers add/remove require a URL")
                return 2
            if args.action == "add":
                name = args.peer_name.strip()
                if not name:
                    from .sync import fetch_peer_node_name
                    name = fetch_peer_node_name(args.url, token=args.peer_token)
                print(json.dumps(client.store.add_peer(
                    args.url, token=args.peer_token, policy=args.peer_policy, name=name
                )))
            else:
                removed = client.store.remove_peer(args.url)
                print("removed" if removed else "not registered")
            return 0
        if args.command == "sync":
            if args.action == "genkey":
                from . import crypto

                existing = crypto.load_sync_secret(args.home)
                if existing:
                    print("a sync key is already configured (env AGENT_MEMORY_SYNC_KEY "
                          f"or {crypto.sync_key_path(args.home)}).")
                    print("delete/replace it deliberately — changing it breaks sync with "
                          "peers still on the old key.")
                    return 1
                secret = crypto.generate_secret()
                path = crypto.save_sync_secret(args.home, secret)
                print(f"sync key saved to {path} (mode 600):")
                print()
                print(f"  {secret}")
                print()
                print("Set this SAME key on every node in the mesh — as env "
                      "AGENT_MEMORY_SYNC_KEY or in each node's sync_key file — to "
                      "encrypt bundle content on the wire (app-layer, independent of TLS).")
                return 0
            if args.action == "auto":
                from .sync import sync_all_peers

                print(json.dumps(sync_all_peers(client), ensure_ascii=False, indent=2))
                return 0
            if not args.target:
                print("sync export/import/pull/push require a target")
                return 2
            if args.action == "export":
                report = client.export_bundle(args.target, since=args.since, team=args.team)
            elif args.action == "import":
                report = client.import_bundle(args.target)
            else:
                from .sync import pull_from_peer, push_to_peer

                if args.action == "pull":
                    report = pull_from_peer(
                        client, args.target, since=args.since, peer_token=args.peer_token
                    )
                else:
                    report = push_to_peer(
                        client, args.target, since=args.since, peer_token=args.peer_token
                    )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "retention":
            if args.half_lives is None:
                result = client.run_retention()
            else:
                result = client.run_retention(decayed_half_lives=args.half_lives or None)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "golden-recall":
            cases = load_golden_query_cases(args.cases)
            report = evaluate_golden_queries(
                client,
                cases,
                default_limit=args.limit,
                recall_target=args.recall_target,
            )
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(
                    f"cases={report['cases']} passed={report['passed']} failed={report['failed']} "
                    f"golden_recall_rate={report['golden_recall_rate']} "
                    f"forbidden_hit_count={report['forbidden_hit_count']} "
                    f"activation_gate={report['activation_gate']}"
                )
            return 0
        if args.command == "add":
            rec = client.add(
                args.content, owner=args.owner, scope=args.scope, type=args.type,
                summary=args.summary, tags=args.tag, confidence=args.confidence, importance=args.importance,
            )
            print(rec.id)
            return 0
        if args.command == "search":
            results = client.search(args.query, owner=args.owner, scope=args.scope, limit=args.limit)
            if args.json:
                print(json.dumps([
                    {"id": r.record.id, "score": r.score, "content": r.record.content, "scope": r.record.scope, "type": r.record.type}
                    for r in results
                ], ensure_ascii=False, indent=2))
            else:
                for r in results:
                    print(f"{r.record.id}\t{r.score:.3f}\t{r.record.scope}/{r.record.type}\t{r.record.content}")
            return 0
        if args.command == "pack":
            print(client.context_pack(args.query, owner=args.owner, scope=args.scope, limit=args.limit, max_tokens=args.max_tokens), end="")
            return 0
        if args.command == "stats":
            print(json.dumps(client.stats(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "import-hermes":
            report = import_hermes_memory_files(client, profile=args.profile, profile_home=args.profile_home)
            if args.json:
                print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            else:
                print(
                    f"profile={report.profile} scanned={report.scanned} inserted={report.inserted} "
                    f"updated={report.updated} skipped={report.skipped}"
                )
            return 0
        if args.command == "import":
            vis = [g.strip() for g in args.visibility.split(",") if g.strip()] if args.visibility else None
            report = import_export(client, args.source, args.file, owner=args.owner,
                                   visibility=vis, memory_type=args.type)
            if args.json:
                print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"source={report.source} scanned={report.scanned} inserted={report.inserted} "
                      f"updated={report.updated} skipped={report.skipped}")
                for w in report.warnings:
                    print(f"  warning: {w}")
            return 0
    except (ValueError, KeyError) as exc:
        # Domain errors (e.g. subset violation, missing team/project) should
        # print a friendly message and a non-zero exit, not a raw traceback.
        print(f"error: {exc}")
        return 2
    finally:
        client.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

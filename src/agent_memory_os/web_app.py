"""Local-first Web UI and HTTP API for AgentMemoryOS.

This is an inspection and smoke-test console for a locally running memory
store. It exposes the same requester-aware retrieval the SDK offers: searches
without a `requester_agent_id` run in unrestricted owner/admin view, so bind
the server to localhost only unless you front it with real authentication.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from .client import MemoryClient
from .schema import (
    MemoryRecord,
    PUBLIC_MEMORY_SCOPES,
    PUBLIC_MEMORY_TYPES,
    SearchResult,
    VALID_LINK_RELATIONS,
)
from .tokens import load_token
from .web_ui import PAGE

# Backward-compatible aliases for callers that imported the Web API's public
# classification sets before they moved to the shared schema module.
VALID_SCOPES = PUBLIC_MEMORY_SCOPES
VALID_TYPES = PUBLIC_MEMORY_TYPES


class AddMemoryRequest(BaseModel):
    content: str = Field(min_length=1)
    owner: str = "default"
    scope: str = "user"
    type: str = "note"
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    visibility: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    expires_at: str | None = None
    decay_policy: str = "exponential"
    pinned: bool = False
    auto_link: bool = False

    @field_validator("scope")
    @classmethod
    def _valid_scope(cls, value: str) -> str:
        if value not in PUBLIC_MEMORY_SCOPES:
            raise ValueError(f"scope must be one of {sorted(PUBLIC_MEMORY_SCOPES)}")
        return value

    @field_validator("type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if value not in PUBLIC_MEMORY_TYPES:
            raise ValueError(f"type must be one of {sorted(PUBLIC_MEMORY_TYPES)}")
        return value

    @field_validator("expires_at")
    @classmethod
    def _valid_expires_at(cls, value: str | None) -> str | None:
        # Expiry gates compare this lexicographically against ISO-8601 UTC
        # "now"; a non-ISO value would silently make the memory permanently
        # expired (or never-expiring) with no error anywhere.
        if value is None:
            return value
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
        return value


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    summary: str | None = None
    scope: str | None = None
    type: str | None = None
    tags: list[str] | None = None
    visibility: list[str] | None = None
    source: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    expires_at: str | None = None
    pinned: bool | None = None
    decay_policy: str | None = None

    @field_validator("scope")
    @classmethod
    def _valid_scope(cls, value: str | None) -> str | None:
        if value is not None and value not in PUBLIC_MEMORY_SCOPES:
            raise ValueError(f"scope must be one of {sorted(PUBLIC_MEMORY_SCOPES)}")
        return value

    @field_validator("type")
    @classmethod
    def _valid_type(cls, value: str | None) -> str | None:
        if value is not None and value not in PUBLIC_MEMORY_TYPES:
            raise ValueError(f"type must be one of {sorted(PUBLIC_MEMORY_TYPES)}")
        return value

    @field_validator("expires_at")
    @classmethod
    def _valid_expires_at(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
        return value


class LinkRequest(BaseModel):
    src_id: str = Field(min_length=1)
    dst_id: str = Field(min_length=1)
    relation: str = "related_to"
    weight: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("relation")
    @classmethod
    def _valid_relation(cls, value: str) -> str:
        if value not in VALID_LINK_RELATIONS:
            raise ValueError(f"relation must be one of {sorted(VALID_LINK_RELATIONS)}")
        return value


class AgentRequest(BaseModel):
    id: str = Field(min_length=1)
    display_name: str = ""
    kind: str = "custom"
    # None = leave team membership alone (managed in the Teams tab); a list
    # reconciles it. So editing an agent's name never wipes its memberships.
    teams: list[str] | None = None
    notes: str = ""


class TeamRequest(BaseModel):
    id: str = Field(min_length=1)
    name: str = ""


class TeamRenameRequest(BaseModel):
    new_id: str = Field(min_length=1)
    # None keeps the current display name (or follows the id if it mirrored it);
    # a string sets it explicitly.
    name: str | None = None


class ProjectRequest(BaseModel):
    id: str = Field(min_length=1)
    team_id: str = Field(min_length=1)
    name: str = ""


class MemberRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    # `actor` is accepted for backward compatibility but IGNORED for audit
    # attribution: under the single shared token the server cannot authenticate
    # a per-user identity, so a client-supplied actor would be forgeable. Web
    # membership changes are recorded with the fixed channel actor "web".
    actor: str = "local"


class PeerRequest(BaseModel):
    url: str = Field(min_length=8)
    token: str | None = None
    policy: str = "shared"
    name: str = ""


class PairingRedeemRequest(BaseModel):
    code: str = Field(min_length=12)
    envelope: str = Field(min_length=8)


class NodeUpdateRequest(BaseModel):
    node_name: str = Field(min_length=1, max_length=80)


class AgentRenameRequest(BaseModel):
    old_id: str = Field(min_length=1)
    new_id: str = Field(min_length=1)


class OwnerReassignRequest(BaseModel):
    old_owner: str = Field(min_length=1)
    new_owner: str = Field(min_length=1)
    register_target: bool = True


class FleetTriggerRequest(BaseModel):
    action: str = Field(pattern="^(sync|update)$")
    url: str = ""


class ShareRequest(BaseModel):
    actor: str = Field(min_length=1)
    to_agent: str | None = None
    to_team: str | None = None
    to_project: str | None = None
    deidentify: bool = False


class RecallFeedbackRequest(BaseModel):
    memory_ids: list[str] = Field(min_length=1)
    helpful: bool = True
    create_colinks: bool = False
    requester_agent_id: str | None = None
    requester_team_id: str | None = None


def _record_payload(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "content": record.content,
        "owner": record.owner,
        "scope": record.scope,
        "type": record.type,
        "summary": record.summary,
        "tags": record.tags,
        "visibility": record.visibility,
        "source": record.source,
        "confidence": record.confidence,
        "importance": record.importance,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "expires_at": record.expires_at,
        "decay_policy": record.decay_policy,
        "pinned": record.pinned,
        "access_count": record.access_count,
    }


def _search_result_payload(result: SearchResult) -> dict[str, Any]:
    return _record_payload(result.record) | {
        "score": result.score,
        "reason": result.reason,
    }


def create_app(home: str | Path | None = None, *, token: str | None = None,
               readonly_token: str | None = None) -> FastAPI:
    # One shared client per app: the schema/migration cost is paid once and
    # the LRU cache actually works. SQLite access is serialized by `lock`
    # because sync endpoints run in a threadpool.
    client = MemoryClient(home=home, check_same_thread=False)
    lock = threading.Lock()
    # Close the first-run loop: with an empty agents registry the Teams tab's
    # member picker had nothing to offer. Seed this node's own default agent
    # identity (env AGENT_MEMORY_AGENT_ID, else the node name) so the very
    # first install can add itself to a team.
    try:
        if not client.store.list_agents():
            seed_id = os.getenv("AGENT_MEMORY_AGENT_ID") or client.node_name
            client.store.register_agent(seed_id, display_name=client.node_name,
                                        kind="custom")
    except Exception:  # noqa: BLE001 - seeding must never block startup
        pass
    # Resolution order: explicit --token > env > <home>/web_token created by
    # `agent-memory token create`.
    api_token = token or os.getenv("AGENT_MEMORY_WEB_TOKEN") or load_token(home)
    ro_token = (readonly_token or os.getenv("AGENT_MEMORY_WEB_READONLY_TOKEN")
                or load_token(home, readonly=True))
    # Federation-only credential: authorizes just the sync/node routes, so a
    # peer can join the mesh without being handed the full admin token.
    sync_token = os.getenv("AGENT_MEMORY_WEB_SYNC_TOKEN") or load_token(home, tier="sync")
    # Optional mesh sync key: when set, bundle bodies are encrypted on the wire
    # (app-layer, independent of TLS). Resolved once here from env or <home>.
    from . import crypto
    sync_secret = crypto.load_sync_secret(home)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        client.close()

    app = FastAPI(title="AgentMemoryOS Web UI", lifespan=lifespan)

    if api_token or ro_token or sync_token:
        # Opt-in bearer-token gate for every API route. The page shell and
        # /health stay open so the UI can load and ask for the token.
        # Token tiers, weakest last:
        #   full  — every route.
        #   read-only — SAFE methods only (GET/HEAD/OPTIONS); mutations 403.
        #   sync  — federation only: GET /api/node, GET /api/sync/export,
        #           POST /api/sync/import. Anything else is rejected, so a peer
        #           holding this token cannot read/write memory via the API.
        safe_methods = {"GET", "HEAD", "OPTIONS"}

        def _authorizes(supplied: str, secret: str) -> bool:
            return bool(secret) and hmac.compare_digest(
                supplied.encode(), f"Bearer {secret}".encode())

        def _sync_scoped(path: str, method: str) -> bool:
            if path == "/api/node" or path == "/api/sync/export":
                return method in safe_methods
            if path == "/api/sync/import":
                return method == "POST"
            if path == "/api/maintenance/update-run" and method == "POST":
                # Team-wide updates: a peer's sync token may trigger THIS
                # node's self-update only when the operator opted in.
                return os.getenv("AGENT_MEMORY_ALLOW_TEAM_UPDATE", "").lower() in ("1", "true", "yes")
            return False

        # Fleet admin signatures (v1.6): an alternative to bearer tokens for
        # cross-node operations. The console signs each request with its
        # Ed25519 key; this node verifies against the PUBLIC keys the local
        # operator granted (fleet_admins). Content-bearing routes additionally
        # require the 'read-private' capability — a manage-only admin can
        # operate the node but never read memory content.
        FLEET_FRESHNESS_S = 120
        FLEET_CONTENT_PREFIXES = (
            "/api/memories", "/api/search", "/api/recall", "/api/context-pack",
            "/api/orchestrate", "/api/archive", "/api/graph", "/api/sync/export",
        )

        def _fleet_required_cap(path: str) -> str:
            if any(path.startswith(p) for p in FLEET_CONTENT_PREFIXES):
                return "read-private"
            return "manage"

        async def _fleet_authorizes(request) -> tuple[bool, str, str]:
            """(allowed, key_id, deny_reason) for a signed fleet request."""
            from . import crypto as _crypto

            key_id = request.headers.get("x-amos-fleet-key-id", "")
            timestamp = request.headers.get("x-amos-fleet-timestamp", "")
            nonce = request.headers.get("x-amos-fleet-nonce", "")
            signature = request.headers.get("x-amos-fleet-signature", "")
            if not (key_id and timestamp and nonce and signature):
                return False, key_id, "incomplete fleet signature headers"
            with lock:
                admin = client.store.get_fleet_admin(key_id)
            if admin is None:
                return False, key_id, "unknown or revoked fleet key"
            try:
                skew = abs(time.time() - int(timestamp))
            except ValueError:
                return False, key_id, "malformed timestamp"
            if skew > FLEET_FRESHNESS_S:
                return False, key_id, "signature expired"
            target = request.url.path + (
                f"?{request.url.query}" if request.url.query else "")
            body = await request.body()
            message = _crypto.fleet_canonical_message(
                request.method, target, body, timestamp, nonce)
            if not _crypto.fleet_verify(admin["public_key"], message, signature):
                return False, key_id, "invalid signature"
            required = _fleet_required_cap(request.url.path)
            if required not in admin["caps"]:
                return False, key_id, f"capability '{required}' not granted"
            # Consume the nonce LAST — only a fully valid signature may burn
            # it, and the atomic insert makes concurrent replays lose.
            with lock:
                if not client.store.consume_fleet_nonce(nonce):
                    return False, key_id, "replayed nonce"
                # Every accepted mutation and every content read is auditable
                # on the node it happened on.
                if request.method not in safe_methods or required == "read-private":
                    client.store.audit_fleet_op(f"{request.method} {target}", key_id)
            return True, key_id, ""

        @app.middleware("http")
        async def require_token(request, call_next):
            # Pairing redemption authenticates with the one-time invite code
            # itself (the whole exchange is encrypted under it) — a joiner by
            # definition has no bearer token yet. Everything else under /api/
            # stays token-gated.
            if (request.url.path == "/api/pairing/redeem"
                    and request.method == "POST"):
                return await call_next(request)
            if request.url.path.startswith("/api/"):
                supplied = request.headers.get("authorization", "")
                full = _authorizes(supplied, api_token) if api_token else False
                ro = _authorizes(supplied, ro_token) if ro_token else False
                sync = _authorizes(supplied, sync_token) if sync_token else False
                allowed = (
                    full
                    or (ro and request.method in safe_methods)
                    or (sync and _sync_scoped(request.url.path, request.method))
                )
                fleet_reason = ""
                if not allowed and request.headers.get("x-amos-fleet-key-id"):
                    allowed, _fleet_key, fleet_reason = await _fleet_authorizes(request)
                if not allowed:
                    if fleet_reason:
                        detail, status = f"fleet signature refused: {fleet_reason}", 403
                    elif (ro or sync):
                        detail, status = (
                            "this token is not authorized for this action", 403)
                    else:
                        detail, status = "unauthorized", 401
                    return JSONResponse({"detail": detail}, status_code=status)
            return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Liveness + readiness for Docker/k8s healthchecks (unauthenticated,
        no sensitive data): confirms the DB opens and integrity holds."""
        try:
            with lock:
                ok = bool(client.integrity_check().get("ok", False))
            try:
                from importlib.metadata import version as _v
                ver = _v("agent-memory-os")
            except Exception:  # noqa: BLE001
                ver = ""
            return JSONResponse({"status": "ok" if ok else "degraded",
                                 "node": client.node_name, "integrity": ok,
                                 "version": ver},
                                status_code=200 if ok else 503)
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)

    @app.get("/metrics")
    def metrics() -> Response:
        """Prometheus text-format operational metrics (aggregate counts only —
        no memory content, ids, or secrets). Unauthenticated by convention so a
        scraper needs no token; the values are non-sensitive totals."""
        try:
            with lock:
                scan = client.maintenance_scan()
                peers = client.store.list_peers()
        except Exception as exc:  # noqa: BLE001 - a scrape endpoint must degrade, not 500
            return Response(f"# metrics unavailable: {exc}\n",
                            media_type="text/plain; version=0.0.4", status_code=503)
        teams = scan.get("teams", 0)
        projects = scan.get("projects", 0)
        # A peer whose last sync recorded an error is "unhealthy"; count them so
        # a mesh operator can alert on sync lag/failure.
        peer_errors = sum(1 for p in peers if str(p.get("last_result", "")).startswith("error"))
        lines = [
            "# HELP agentmemory_memories_total Number of memories in the store.",
            "# TYPE agentmemory_memories_total gauge",
            f"agentmemory_memories_total {scan.get('memories', 0)}",
            "# HELP agentmemory_orphan_memories Memories reachable by no one.",
            "# TYPE agentmemory_orphan_memories gauge",
            f"agentmemory_orphan_memories {scan.get('orphan_memories', 0)}",
            "# HELP agentmemory_index_drift memories minus FTS-indexed rows (0 = healthy).",
            "# TYPE agentmemory_index_drift gauge",
            f"agentmemory_index_drift {abs(scan.get('memories', 0) - scan.get('indexed', 0))}",
            "# HELP agentmemory_archived_total Cold-archived memories.",
            "# TYPE agentmemory_archived_total gauge",
            f"agentmemory_archived_total {scan.get('archived', 0)}",
            "# HELP agentmemory_teams_total Teams.",
            "# TYPE agentmemory_teams_total gauge",
            f"agentmemory_teams_total {teams}",
            "# HELP agentmemory_projects_total Projects.",
            "# TYPE agentmemory_projects_total gauge",
            f"agentmemory_projects_total {projects}",
            "# HELP agentmemory_peers_total Registered sync peers.",
            "# TYPE agentmemory_peers_total gauge",
            f"agentmemory_peers_total {len(peers)}",
            "# HELP agentmemory_peer_errors Peers whose last sync failed.",
            "# TYPE agentmemory_peer_errors gauge",
            f"agentmemory_peer_errors {peer_errors}",
            "# HELP agentmemory_integrity_ok 1 if the DB passes integrity_check.",
            "# TYPE agentmemory_integrity_ok gauge",
            f"agentmemory_integrity_ok {1 if scan.get('schema_ok', True) else 0}",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.post("/api/node")
    def node_update(request: NodeUpdateRequest) -> dict[str, Any]:
        """Rename this node (persists to instance.toml, applies immediately).

        Peers pick the new name up automatically on their next sync — display
        names refresh from /api/node each cycle. Agent IDs are identities and
        are NOT touched; use /api/agents/rename to migrate one.
        """
        from .settings import update_instance_settings

        name = request.node_name.strip()
        with lock:
            update_instance_settings(home, node_name=name)
            client.node_name = name
            client.settings.node_name = name
        return {"node_name": name}

    @app.post("/api/agents/rename")
    def agent_rename(request: AgentRenameRequest) -> dict[str, Any]:
        """Migrate an agent identity everywhere: memory ownership, agent:<id>
        ACL grants (ACL clock bumped so it propagates), team/project
        membership, registry, and recall profiles — atomically."""
        with lock:
            try:
                counts = client.store.rename_agent(request.old_id, request.new_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"old_id": request.old_id, "new_id": request.new_id, "changed": counts}

    @app.get("/api/owners")
    def owners_list() -> dict[str, Any]:
        """Memory count per owner. Surfaces identities that hold memories no
        current browsing identity can see (the 'hidden memories' case)."""
        with lock:
            return {"owners": client.owner_counts()}

    @app.post("/api/owners/reassign")
    def owners_reassign(request: OwnerReassignRequest) -> dict[str, Any]:
        """Re-attribute one owner's memories to another (merge-capable). Unlike
        agent rename, the target may already exist — used to fold memories
        written under a fallback owner into a real identity."""
        with lock:
            try:
                counts = client.reassign_owner(
                    request.old_owner, request.new_owner,
                    register_target=request.register_target)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"old_owner": request.old_owner, "new_owner": request.new_owner,
                "changed": counts}
    # NOTE: destructive owner deletion is DELETE /api/owners/{owner}/memories
    # (defined below), which already enforces a ?confirm=<owner> guard.

    @app.get("/api/node")
    def node_identity() -> dict[str, Any]:
        """This instance's sync identity — the name peers show for it."""
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("agent-memory-os")
        except PackageNotFoundError:
            ver = "unknown"
        return {"node_name": client.node_name, "version": ver}

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        with lock:
            return client.stats()

    @app.post("/api/memories")
    def add_memory(request: AddMemoryRequest) -> dict[str, Any]:
        payload = request.model_dump()
        auto_link = payload.pop("auto_link")
        content = payload.pop("content")
        with lock:
            try:
                record = client.add(content, auto_link=auto_link, **payload)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _record_payload(record)

    @app.get("/api/memories")
    def list_memories(
        owner: str | None = None,
        scope: str | None = None,
        type: str | None = None,  # noqa: A002 - query param name mirrors the schema field
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with lock:
            records = client.list_recent(
                owner=owner or None,
                scope=scope or None,
                memory_type=type or None,
                requester_agent_id=requester_agent_id or None,
                requester_team_id=requester_team_id or None,
                limit=limit,
                offset=offset,
            )
        return {"memories": [_record_payload(record) for record in records]}

    @app.get("/api/graph")
    def graph(
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = Query(default=300, ge=1, le=1000),
    ) -> dict[str, Any]:
        with lock:
            return client.graph_snapshot(
                requester_agent_id=requester_agent_id or None,
                requester_team_id=requester_team_id or None,
                limit=limit,
            )

    @app.get("/api/memories/{memory_id}")
    def get_memory(
        memory_id: str,
        requester_agent_id: str = Query(default=""),
        requester_team_id: str = Query(default=""),
    ) -> dict[str, Any]:
        with lock:
            record = client.get_visible(
                memory_id,
                requester_agent_id=requester_agent_id or None,
                requester_team_id=requester_team_id or None,
            )
        if record is None:
            raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
        return _record_payload(record)

    @app.patch("/api/memories/{memory_id}")
    def update_memory(memory_id: str, request: UpdateMemoryRequest) -> dict[str, Any]:
        fields = {name: value for name, value in request.model_dump().items() if value is not None}
        if not fields:
            raise HTTPException(status_code=400, detail="no fields to update")
        with lock:
            try:
                record = client.update(memory_id, **fields)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"memory not found: {exc.args[0]}") from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _record_payload(record)

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, Any]:
        with lock:
            return client.dashboard_stats()

    @app.post("/api/retention")
    def retention(decayed_half_lives: float | None = Query(default=None, ge=0)) -> dict[str, Any]:
        with lock:
            return client.run_retention(
                decayed_half_lives=decayed_half_lives if decayed_half_lives else None
            )

    @app.get("/api/archive")
    def archive_list(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with lock:
            return {"archived": client.list_archived(limit=limit, offset=offset)}

    @app.post("/api/archive/{memory_id}/restore")
    def archive_restore(memory_id: str) -> dict[str, Any]:
        with lock:
            try:
                record = client.restore_archived(memory_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"not in archive: {exc.args[0]}") from exc
        return _record_payload(record)

    @app.get("/api/integrity")
    def integrity() -> dict[str, Any]:
        with lock:
            return client.integrity_check()

    @app.get("/api/agents")
    def agents_list() -> dict[str, Any]:
        with lock:
            return {"agents": client.list_agents()}

    @app.post("/api/agents")
    def agents_register(request: AgentRequest) -> dict[str, Any]:
        with lock:
            try:
                return client.register_agent(
                    request.id, display_name=request.display_name, kind=request.kind,
                    teams=request.teams, notes=request.notes,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/agents/{agent_id}")
    def agents_remove(agent_id: str) -> dict[str, Any]:
        with lock:
            removed = client.remove_agent(agent_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"agent not registered: {agent_id}")
        return {"removed": agent_id}

    # ---------- teams ----------

    @app.get("/api/teams")
    def teams_list() -> dict[str, Any]:
        with lock:
            return {"teams": client.store.list_teams()}

    @app.post("/api/teams")
    def teams_create(request: TeamRequest) -> dict[str, Any]:
        with lock:
            try:
                return client.store.create_team(request.id, name=request.name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/teams/{team_id}")
    def teams_delete(team_id: str) -> dict[str, Any]:
        with lock:
            removed = client.store.delete_team(team_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"team not found: {team_id}")
        return {"removed": team_id}

    @app.get("/api/teams/{team_id}/rename-preview")
    def teams_rename_preview(team_id: str, new_id: str = Query(min_length=1)) -> dict[str, Any]:
        """What a rename would move. The console shows this before asking to
        confirm, so an operator never renames a team blind to the memory
        grants, projects, and memberships that travel with the id."""
        with lock:
            return client.store.team_rename_preview(team_id, new_id)

    @app.post("/api/teams/{team_id}/rename")
    def teams_rename(team_id: str, request: TeamRenameRequest) -> dict[str, Any]:
        with lock:
            try:
                return client.rename_team(team_id, request.new_id, name=request.name)
            except KeyError as exc:
                raise HTTPException(status_code=404,
                                    detail=f"team not found: {team_id}") from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/org/audit")
    def org_audit(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        with lock:
            return {"audit": client.store.org_audit_log(limit=limit)}

    # ---------- ops / maintenance ----------

    @app.get("/api/maintenance/scan")
    def maintenance_scan() -> dict[str, Any]:
        with lock:
            return client.maintenance_scan()

    @app.get("/api/maintenance/orphans")
    def maintenance_orphans() -> dict[str, Any]:
        with lock:
            return {"orphans": client.find_orphan_memories()}

    @app.post("/api/maintenance/orphans/delete")
    def maintenance_orphans_delete(confirm: str = Query(default="")) -> dict[str, Any]:
        # Permanent, sync-propagating deletion — gate it like purge_owner so a
        # stray click (or a cross-site form POST in token-less mode) can't wipe
        # data. Caller must pass ?confirm=orphans.
        if confirm != "orphans":
            raise HTTPException(
                status_code=400,
                detail="confirmation required: pass ?confirm=orphans to delete orphan memories",
            )
        with lock:
            return client.delete_orphan_memories()

    @app.post("/api/maintenance/reindex")
    def maintenance_reindex() -> dict[str, Any]:
        with lock:
            return client.rebuild_indexes()

    @app.post("/api/maintenance/vacuum")
    def maintenance_vacuum() -> dict[str, Any]:
        with lock:
            return client.vacuum()

    @app.get("/api/usage")
    def usage() -> dict[str, Any]:
        """Token footprint for the dashboard cards (agent / team / project / total)."""
        with lock:
            return client.usage_summary()

    @app.get("/api/maintenance/update-check")
    def update_check() -> dict[str, Any]:
        """Current vs PyPI-latest version + deployment, for the update button."""
        from importlib.metadata import PackageNotFoundError, version

        from .cli import _in_docker, _pypi_latest

        try:
            current = version("agent-memory-os")
        except PackageNotFoundError:
            current = "unknown"
        latest = _pypi_latest("agent-memory-os")
        return {
            "current": current,
            "latest": latest,
            "update_available": bool(latest and latest != current),
            "deployment": "docker" if _in_docker() else "host",
        }

    @app.post("/api/maintenance/update-run")
    def update_run(confirm: str = Query(default="")) -> dict[str, Any]:
        """Trigger a self-update: upgrade the package, then restart the console.

        Delegated to the `agent-memory update --yes` CLI as a detached process —
        it pip-upgrades and restarts THIS console via its pidfile, so the button
        can't take the web process down mid-request. Docker deployments can't
        pip-upgrade in place, so it returns guidance instead.
        """
        import subprocess
        import sys

        from .cli import _in_docker

        if confirm != "update":
            raise HTTPException(status_code=400,
                                detail="confirmation required: pass ?confirm=update")
        if _in_docker():
            return {"started": False,
                    "detail": "Docker deployment: pull the new image tag and recreate the "
                              "container (a container cannot pip-upgrade itself)."}
        from .service import running_under_systemd, systemd_self_update

        if running_under_systemd():
            # The detached updater would be reaped with the unit's cgroup the
            # moment it kills this process (pip never finishes). Upgrade in
            # an in-process thread instead, then self-exit — Restart=always
            # brings the console back on the new code.
            threading.Thread(target=systemd_self_update, daemon=True).start()
            return {"started": True,
                    "detail": "systemd-managed console: upgrading in place — the service "
                              "manager will restart it on the new version shortly. "
                              "Reload the page in ~30s."}
        try:
            subprocess.Popen(
                [sys.executable, "-m", "agent_memory_os.cli", "update", "--yes"],
                start_new_session=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not start updater: {exc}") from exc
        return {"started": True,
                "detail": "Updating in the background — the console will restart on the new "
                          "version shortly. Reload the page in ~30s."}

    @app.post("/api/teams/{team_id}/members")
    def team_add_member(team_id: str, request: MemberRequest) -> dict[str, Any]:
        with lock:
            try:
                client.store.add_team_member(team_id, request.agent_id, actor="web")
                return client.store.get_team(team_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/teams/{team_id}/members")
    def team_remove_member(team_id: str, agent_id: str = Query(min_length=1)) -> dict[str, Any]:
        with lock:
            client.store.remove_team_member(team_id, agent_id, actor="web")
            return client.store.get_team(team_id) or {"removed": agent_id}

    # ---------- projects ----------

    @app.get("/api/projects")
    def projects_list(team: str | None = None) -> dict[str, Any]:
        with lock:
            return {"projects": client.store.list_projects(team or None)}

    @app.post("/api/projects")
    def projects_create(request: ProjectRequest) -> dict[str, Any]:
        with lock:
            try:
                return client.store.create_project(request.id, request.team_id, name=request.name)
            except (ValueError, KeyError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}")
    def projects_delete(project_id: str) -> dict[str, Any]:
        with lock:
            removed = client.store.delete_project(project_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
        return {"removed": project_id}

    @app.post("/api/projects/{project_id}/members")
    def project_add_member(project_id: str, request: MemberRequest) -> dict[str, Any]:
        with lock:
            try:
                client.store.add_project_member(project_id, request.agent_id, actor="web")
                return client.store.get_project(project_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}/members")
    def project_remove_member(project_id: str, agent_id: str = Query(min_length=1)) -> dict[str, Any]:
        with lock:
            client.store.remove_project_member(project_id, agent_id, actor="web")
            return client.store.get_project(project_id) or {"removed": agent_id}

    @app.get("/api/peers")
    def peers_list() -> dict[str, Any]:
        with lock:
            return {"peers": client.store.list_peers()}

    @app.get("/api/fleet/status")
    def fleet_status_view() -> dict[str, Any]:
        """Aggregate fleet view — only meaningful on a console node (one that
        holds a fleet admin private key). Elsewhere returns configured=false
        with setup guidance instead of erroring, so the UI can explain."""
        from .fleet import (FleetKeyMissing, assemble_status, console_snapshot,
                            load_console_key, probe_peers)

        try:
            keypair = load_console_key(home)
        except FleetKeyMissing as exc:
            return {"configured": False, "hint": str(exc)}
        # Local snapshot under the store lock; the (slow) network fan-out
        # runs outside it so the console stays responsive meanwhile.
        with lock:
            console, peers = console_snapshot(client, keypair)
        nodes = probe_peers(keypair, peers)
        return {"configured": True} | assemble_status(console, nodes)

    @app.post("/api/fleet/trigger")
    def fleet_trigger_view(request: FleetTriggerRequest) -> dict[str, Any]:
        """Run 'sync' or 'update' on every managed node (or one URL)."""
        from .fleet import FleetKeyMissing, load_console_key, trigger_on

        try:
            keypair = load_console_key(home)
            with lock:
                peers = client.store.list_peers()
            results = trigger_on(keypair, peers, request.action,
                                 only_url=request.url or None)
        except (FleetKeyMissing, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"action": request.action, "results": results}

    @app.get("/api/fleet/browse")
    def fleet_browse(
        url: str = Query(min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Read memories LIVE from a managed node (console only).

        Private memories deliberately never sync to the console, so its local
        Browse cannot show them; this reads them off the owning node with a
        signed request instead. The target node enforces its own capability
        gate ('read-private') and records the read in ITS org audit — the
        node's owner always sees that the console looked.
        """
        from urllib.parse import quote, urlencode

        from .fleet import FleetKeyMissing, load_console_key, signed_call

        try:
            keypair = load_console_key(home)
        except FleetKeyMissing as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        params = {"limit": str(limit), "offset": str(offset)}
        if owner:
            params["owner"] = owner
        target = "/api/memories?" + urlencode(params, quote_via=quote)
        status, body = signed_call(keypair, url, "GET", target)
        if status == 0:
            raise HTTPException(status_code=502, detail=f"node unreachable: {body}")
        if status != 200:
            detail = body.get("detail") if isinstance(body, dict) else str(body)[:200]
            raise HTTPException(status_code=status, detail=str(detail))
        return body if isinstance(body, dict) else {"memories": []}

    @app.api_route("/api/fleet/proxy",
                   methods=["GET", "POST", "PATCH", "DELETE"])
    async def fleet_proxy(request: Request) -> Response:
        """Console-side signed proxy: operate a managed node through THIS UI.

        Powers remote management mode — when the operator switches the console
        to a fleet identity, every tab's API call is forwarded to that node,
        signed with the console's fleet key. The target node stays the
        authority: it verifies the signature, enforces its manage/read-private
        grants, and audits every accepted call. Guard rails here: the target
        must be a REGISTERED peer (no open forwarder), only /api/ paths are
        forwardable, and fleet endpoints are excluded (no proxy recursion).
        """
        from .fleet import FleetKeyMissing, load_console_key, signed_call

        url = request.query_params.get("url", "").strip().rstrip("/")
        target = request.query_params.get("path", "")
        if not url or not target:
            raise HTTPException(status_code=400, detail="url and path are required")
        if not target.startswith("/api/") or target.startswith("/api/fleet"):
            raise HTTPException(status_code=400,
                                detail="only non-fleet /api/ paths can be proxied")
        try:
            keypair = load_console_key(home)
        except FleetKeyMissing as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with lock:
            peers = {str(p["url"]).rstrip("/") for p in client.store.list_peers()}
        if url not in peers:
            raise HTTPException(status_code=400,
                                detail="target is not a registered peer of this console")
        body = await request.body()
        try:
            request_payload = None if not body else json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail="request body must be valid UTF-8 JSON",
            ) from exc
        status, payload = signed_call(
            keypair, url, request.method, target,
            payload=request_payload,
        )
        if status == 0:
            raise HTTPException(status_code=502, detail=f"node unreachable: {payload}")
        return JSONResponse(
            content=payload if isinstance(payload, (dict, list)) else {"detail": str(payload)[:400]},
            status_code=status,
        )

    LOG_TAIL_READ_BYTES = 2_000_000  # bounded read so huge logs can't OOM the console

    @app.get("/api/logs")
    def logs_view(
        file: str | None = None,
        lines: int = Query(default=100, ge=1, le=2000),
        q: str | None = None,
    ) -> dict[str, Any]:
        """Tail this node's service logs (default: last 100 lines).

        Strictly whitelisted to the home's known log locations — never an
        arbitrary file reader. `q` filters case-insensitively BEFORE tailing,
        so a search returns the last N *matching* lines. In the console's
        remote-management mode this proxies like everything else, showing the
        managed node's own logs.
        """
        from .crypto import resolve_home

        base = resolve_home(home)
        candidates: list[tuple[str, Path]] = []
        # Root-level names: webui.log (documented) and web.log — the file the
        # pidfile relaunch in `agent-memory update` wrote before it moved to
        # logs/web.log, kept viewable for stores that already have one.
        for root_name in ("webui.log", "web.log"):
            direct = base / root_name
            if direct.is_file():
                candidates.append((root_name, direct))
        logs_dir = base / "logs"
        if logs_dir.is_dir():
            for p in sorted(logs_dir.glob("*.log")):
                if p.is_file():
                    candidates.append((f"logs/{p.name}", p))
        names = [name for name, _ in candidates]
        if not candidates:
            return {"files": [], "file": None, "lines": [], "truncated": False}
        chosen_name, chosen = candidates[0]
        if file:
            match = next(((n, p) for n, p in candidates if n == file), None)
            if match is None:
                raise HTTPException(status_code=404, detail=f"unknown log file: {file}")
            chosen_name, chosen = match
        size = chosen.stat().st_size
        with open(chosen, "rb") as handle:
            if size > LOG_TAIL_READ_BYTES:
                handle.seek(size - LOG_TAIL_READ_BYTES)
            text = handle.read().decode("utf-8", errors="replace")
        rows = text.splitlines()
        truncated = size > LOG_TAIL_READ_BYTES
        if truncated and rows:
            rows = rows[1:]  # drop the partial first line of the window
        if q:
            needle = q.lower()
            rows = [row for row in rows if needle in row.lower()]
        return {"files": names, "file": chosen_name, "lines": rows[-lines:],
                "truncated": truncated, "size": size, "matched": len(rows)}

    @app.get("/api/peers/status")
    def peers_status() -> dict[str, Any]:
        """Probe every registered peer's /healthz concurrently and report
        liveness. Powers the console's connection dot (green ok / orange
        degraded / red unreachable). Separate from /api/peers so the peer list
        renders instantly while these network probes fill in."""
        from concurrent.futures import ThreadPoolExecutor
        from .discovery import probe_node

        with lock:
            peers = client.store.list_peers()
        if not peers:
            return {"statuses": []}

        def _probe(peer: dict) -> dict:
            p = probe_node(peer["url"])
            return {
                "url": peer["url"],
                "name": peer.get("name") or "",
                "node_name": p.node_name,
                "reachable": p.reachable,
                "is_amos": p.is_amos,
                "status": p.status,
                "integrity": p.integrity,
                "version": p.extras.get("version", ""),
                "detail": p.detail,
            }

        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as pool:
            statuses = list(pool.map(_probe, peers))
        return {"statuses": statuses}

    @app.post("/api/peers")
    def peers_add(request: PeerRequest) -> dict[str, Any]:
        # Auto-fill the peer's friendly name from its advertised node identity
        # (outside the lock — it's a network call) unless one was given.
        name = request.name.strip()
        if not name:
            from .sync import fetch_peer_node_name

            name = fetch_peer_node_name(request.url, token=request.token)
        with lock:
            try:
                return client.store.add_peer(
                    request.url, token=request.token, policy=request.policy, name=name
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/pairing/redeem")
    def pairing_redeem(request: PairingRedeemRequest) -> dict[str, Any]:
        """Redeem a one-time team-join invite (see pairing.py).

        Deliberately exempt from bearer-token auth: the single-use code is
        the credential, and the request/response bodies are encrypted under
        it. All failures collapse to one opaque 403 so probing cannot
        distinguish unknown, expired, and used codes.
        """
        nonlocal sync_token

        from . import pairing

        with lock:
            try:
                payload = pairing.redeem_invite(
                    client, request.envelope, request.code,
                    home=home, self_node_name=client.node_name,
                    self_agent_id=os.getenv("AGENT_MEMORY_AGENT_ID") or client.node_name,
                )
            except ValueError as exc:
                raise HTTPException(status_code=403, detail="pairing refused") from exc
        # Redemption may have MINTED this node's first sync token; the auth
        # middleware captured `sync_token` at startup, so rebind it now or the
        # joiner's very first sync gets 401 until the console restarts.
        minted = str(payload.get("sync_token") or "")
        if minted and not sync_token:
            sync_token = minted
        return {"envelope": pairing.encrypt_payload(payload, request.code)}

    @app.delete("/api/peers")
    def peers_remove(url: str = Query(min_length=1)) -> dict[str, Any]:
        with lock:
            removed = client.store.remove_peer(url)
        if not removed:
            raise HTTPException(status_code=404, detail=f"peer not registered: {url}")
        return {"removed": url}

    @app.post("/api/sync/run")
    def sync_run() -> dict[str, Any]:
        from .sync import sync_all_peers

        # Pass the lock instead of holding it: DB access is serialized, but a
        # slow/unreachable peer's HTTP round-trip never freezes other requests.
        return {"results": sync_all_peers(client, lock=lock)}

    @app.get("/api/sync/export")
    def sync_export(since: str | None = None) -> Any:
        """Stream this host's memory bundle (peer pull / browser download).

        Private (`visibility=[]`) memories are never served here: the endpoint
        cannot authenticate which peer is pulling, so it always exports at
        'shared' scope. Full replication of private memory happens only over
        the authenticated push leg (POST /api/sync/import) between own nodes.
        """
        import tempfile

        from .sync import export_bundle

        with lock:
            with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False, encoding="utf-8") as handle:
                export_bundle(client.store, handle.name, since=since or None,
                              include_private=False, node_name=client.node_name)
                handle.seek(0)
                body = Path(handle.name).read_text(encoding="utf-8")
            Path(handle.name).unlink(missing_ok=True)
        from fastapi.responses import PlainTextResponse

        # Encrypt the bundle for the wire when a mesh key is configured. The
        # AMOSENC1 envelope is self-describing, so a peer with the same key
        # auto-detects and decrypts it (see crypto.py).
        if sync_secret:
            body = crypto.encrypt_bundle(body, sync_secret)

        return PlainTextResponse(
            body,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="agent-memory-bundle.jsonl"'},
        )

    @app.post("/api/sync/import")
    async def sync_import(request: Request) -> dict[str, Any]:
        """Accept a bundle body (peer push / browser upload) and merge it.

        A pushed bundle is anonymous under the single shared token — we cannot
        attribute it to a policy-scoped peer — so it is merged UNTRUSTED and may
        NOT mutate org structure (`org_scope=None`): membership/ACL definitions
        converge only via authenticated pull from a peer of known policy. This
        closes the forge-membership / griefing vector on the push leg.
        """
        import tempfile

        from .sync import import_bundle

        body = (await request.body()).decode("utf-8")
        # Decrypt an encrypted push if we share the mesh key; reject one we
        # cannot open rather than merging ciphertext as if it were memory.
        if crypto.is_encrypted(body):
            if not sync_secret:
                raise HTTPException(
                    status_code=400,
                    detail="received an encrypted bundle but this node has no "
                           "AGENT_MEMORY_SYNC_KEY configured",
                )
            try:
                body = crypto.decrypt_bundle(body, sync_secret)
            except crypto.SyncCryptoError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        with lock:
            with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as handle:
                handle.write(body)
            try:
                stats = import_bundle(
                    client.store, handle.name, trusted=False, org_scope=None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                Path(handle.name).unlink(missing_ok=True)
        return stats

    @app.get("/api/orchestrate")
    def orchestrate(
        task: str = Query(min_length=1),
        session_id: str | None = None,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        max_tokens: int = Query(default=2000, ge=128, le=32000),
    ) -> dict[str, Any]:
        with lock:
            result = client.orchestrate_context(
                task,
                session_id=session_id or None,
                requester_agent_id=requester_agent_id or None,
                requester_team_id=requester_team_id or None,
                max_tokens=max_tokens,
            )
        return {
            "task": task,
            "text": result.text,
            "sections": result.sections,
            "used_tokens": result.used_tokens,
            "max_tokens": result.max_tokens,
            "session_id": result.session_id,
            "delivered_ids": result.delivered_ids,
            "emphasis": result.emphasis,
        }

    @app.delete("/api/owners/{owner}/memories")
    def purge_owner(owner: str, confirm: str = Query(default="")) -> dict[str, Any]:
        # Destructive and unscoped by id: the caller must retype the exact
        # owner as confirmation so a stray click can never wipe an agent.
        if confirm != owner:
            raise HTTPException(
                status_code=400,
                detail="confirmation mismatch: pass ?confirm=<owner> with the exact owner id",
            )
        with lock:
            try:
                result = client.purge_owner(owner)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"owner": owner} | result

    @app.delete("/api/memories/{memory_id}")
    def delete_memory(memory_id: str) -> dict[str, Any]:
        with lock:
            removed = client.delete(memory_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
        return {"deleted": memory_id}

    @app.post("/api/memories/{memory_id}/share")
    def share_memory(memory_id: str, request: ShareRequest) -> dict[str, Any]:
        with lock:
            try:
                return client.share_memory(
                    memory_id,
                    actor=request.actor,
                    to_agent=request.to_agent,
                    to_team=request.to_team,
                    to_project=request.to_project,
                    deidentify=request.deidentify,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"memory not found: {exc.args[0]}") from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/memories/{memory_id}/revoke")
    def revoke_share(memory_id: str, request: ShareRequest) -> dict[str, Any]:
        with lock:
            try:
                return client.revoke_share(
                    memory_id,
                    actor=request.actor,
                    to_agent=request.to_agent,
                    to_team=request.to_team,
                    to_project=request.to_project,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"memory not found: {exc.args[0]}") from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/memories/{memory_id}/audit")
    def memory_audit(memory_id: str) -> dict[str, Any]:
        with lock:
            return {"memory_id": memory_id, "audit": client.audit_log(memory_id)}

    @app.get("/api/memories/{memory_id}/links")
    def memory_links(
        memory_id: str,
        requester_agent_id: str = Query(default=""),
        requester_team_id: str = Query(default=""),
    ) -> dict[str, Any]:
        with lock:
            if client.get_visible(
                memory_id,
                requester_agent_id=requester_agent_id or None,
                requester_team_id=requester_team_id or None,
            ) is None:
                raise HTTPException(status_code=404, detail=f"memory not found: {memory_id}")
            links = client.links(memory_id)
        return {
            "memory_id": memory_id,
            "links": [
                {
                    "src_id": link.src_id,
                    "dst_id": link.dst_id,
                    "relation": link.relation,
                    "weight": link.weight,
                    "activation_count": link.activation_count,
                    "last_activated_at": link.last_activated_at,
                }
                for link in links
            ],
        }

    @app.post("/api/links")
    def add_link(request: LinkRequest) -> dict[str, Any]:
        with lock:
            try:
                link = client.link(
                    request.src_id, request.dst_id,
                    relation=request.relation, weight=request.weight,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"memory not found: {exc.args[0]}") from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "src_id": link.src_id,
            "dst_id": link.dst_id,
            "relation": link.relation,
            "weight": link.weight,
        }

    @app.post("/api/recall")
    def recall_feedback(request: RecallFeedbackRequest) -> dict[str, int]:
        with lock:
            return client.record_recall(
                request.memory_ids,
                helpful=request.helpful,
                create_colinks=request.create_colinks,
                requester_agent_id=request.requester_agent_id or None,
                requester_team_id=request.requester_team_id or None,
            )

    @app.post("/api/consolidate")
    def consolidate(owner: str | None = None, scope: str | None = None) -> dict[str, int]:
        with lock:
            return client.consolidate(owner=owner or None, scope=scope or None)

    @app.get("/api/search")
    def search(
        q: str = Query(min_length=1),
        owner: str | None = None,
        scope: str | None = None,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = Query(default=10, ge=1, le=100),
    ) -> dict[str, Any]:
        with lock:
            results = client.search(
                q,
                owner=owner or None,
                scope=scope or None,
                requester_agent_id=requester_agent_id or None,
                requester_team_id=requester_team_id or None,
                limit=limit,
            )
        return {"query": q, "results": [_search_result_payload(result) for result in results]}

    @app.get("/api/context-pack")
    def context_pack(
        q: str = Query(min_length=1),
        owner: str | None = None,
        scope: str | None = None,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        max_tokens: int = Query(default=1200, ge=32, le=32000),
        auto_reinforce: bool = False,
    ) -> dict[str, Any]:
        with lock:
            report = client.context_pack_report(
                q,
                owner=owner or None,
                scope=scope or None,
                requester_agent_id=requester_agent_id or None,
                requester_team_id=requester_team_id or None,
                max_tokens=max_tokens,
                auto_reinforce=auto_reinforce,
            )
        return {
            "query": q,
            "text": report.text,
            "used_tokens": report.used_tokens,
            "max_tokens": report.max_tokens,
            "decisions": [
                {
                    "memory_id": decision.memory_id,
                    "selected": decision.selected,
                    "effective_score": decision.effective_score,
                    "reason": decision.reason,
                }
                for decision in report.decisions
            ],
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(PAGE)

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agent-memory-web", description="Run the AgentMemoryOS Web UI")
    parser.add_argument("--host", default=None, help="Bind host (default: instance.toml or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: instance.toml or 8000)")
    parser.add_argument("--home", default=None, help="Memory home directory; defaults to AGENT_MEMORY_HOME or ~/.agent-memory")
    parser.add_argument(
        "--token",
        default=None,
        help="Require this bearer token on all /api/ routes (also via AGENT_MEMORY_WEB_TOKEN)",
    )
    parser.add_argument(
        "--strict-port", action="store_true",
        help="Fail if the chosen port is in use instead of advancing to a free one",
    )
    args = parser.parse_args(argv)

    import uvicorn

    from .settings import find_available_port, load_instance_settings, port_is_free

    # Resolution order: CLI flag > <home>/instance.toml > built-in default.
    settings = load_instance_settings(args.home)
    host = args.host or settings.host
    preferred = args.port if args.port is not None else settings.port
    if args.strict_port:
        if not port_is_free(host, preferred):
            parser.error(f"port {preferred} on {host} is in use")
        port = preferred
    else:
        # Multiple instances on one machine: skip past taken ports automatically.
        port = find_available_port(host, preferred)
        if port != preferred:
            print(f"NOTE: port {preferred} was in use — bound to {port} instead.")

    if not (args.token or os.getenv("AGENT_MEMORY_WEB_TOKEN") or load_token(args.home)):
        print("NOTE: no API token configured — the console runs in open admin mode.")
        print("      Protect it with:  agent-memory token create")

    print(f"Agent Memory OS '{settings.node_name}' → http://{host}:{port}")
    # Record our pid + relaunch command so `agent-memory update` can restart the
    # console it owns without trusting ps output. Cleared on clean exit.
    import atexit

    from .pidfile import clear_web_pidfile, write_web_pidfile

    write_web_pidfile(args.home)
    atexit.register(clear_web_pidfile, args.home)
    uvicorn.run(create_app(home=args.home, token=args.token), host=host, port=port)


if __name__ == "__main__":
    main()

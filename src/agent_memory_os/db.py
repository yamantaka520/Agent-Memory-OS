from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import DefaultDict, Sequence

from .candidates import Candidate, CandidateProvider
from .constants import (
    AUTO_LINK_LIMIT,
    AUTO_LINK_WEIGHT,
    CO_RECALL_INITIAL_WEIGHT,
    CO_RECALL_WEAKEN_STEP,
    CO_RECALL_WEIGHT_STEP,
    CONSOLIDATION_MIN_ACTIVATIONS,
    CONSOLIDATION_MIN_CLUSTER_SIZE,
    CONSOLIDATION_MIN_CLUSTER_WEIGHT,
    DASHBOARD_ACTIVITY_WINDOW_DAYS,
    DECAY_FEEDBACK_MAX_HALF_LIFE_DAYS,
    DECAY_FEEDBACK_MAX_MULTIPLIER,
    DECAY_FEEDBACK_MIN_HALF_LIFE_DAYS,
    DECAY_FEEDBACK_MIN_MULTIPLIER,
    DECAY_FEEDBACK_ROUND_DIGITS,
    DECAY_FEEDBACK_UPDATE_THRESHOLD_DAYS,
    DEFAULT_DECAY_HALF_LIFE_DAYS,
    DEFAULT_DECAY_HALF_LIFE_FALLBACK_DAYS,
    FLEET_NONCE_RETENTION_SECONDS,
    LEGACY_CONTEXT_OWNER,
    LINK_DECAY_HALF_LIFE_DAYS,
    MAX_RESONANCE_CANDIDATES,
    MAX_SEMANTIC_CANDIDATES,
    MEMBERSHIP_CACHE_TTL_SECONDS,
    NEGATIVE_FEEDBACK_CONFIDENCE_STEP,
    PAIRING_INVITE_TTL_SECONDS,
    RESONANCE_CONVERGENCE_CAP,
    RESONANCE_HOP_DECAY,
    RESONANCE_MAX_EDGES_PER_NODE,
    RETENTION_MIN_HALF_LIVES,
    SNAPSHOT_RETENTION_COUNT_PER_SESSION,
    SQLITE_BUSY_TIMEOUT_MILLISECONDS,
    SUPERSEDED_SCORE_PENALTY,
)
from .database_schema import SCHEMA
from .migrations import MIGRATIONS, Migration
from .schema import (
    MemoryLink,
    MemoryRecord,
    RecallProfile,
    SearchResult,
    utc_now,
    utc_now_micro,
)
from .scoring import effective_score, freshness_factor, reinforcement_factor


def _is_local_url(url: str) -> bool:
    """True if a peer URL points at the local host (loopback), where plain HTTP
    carries no network-exposure risk."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".localhost")


def _validate_migration_plan(migrations: Sequence[Migration]) -> None:
    """Reject an ambiguous migration plan before opening or changing a database."""
    versions = [version for version, _, _ in migrations]
    duplicates = sorted({version for version in versions if versions.count(version) > 1})
    if duplicates:
        raise RuntimeError(f"duplicate migration versions: {duplicates}")
    if versions != sorted(versions):
        raise RuntimeError("migration versions must be strictly increasing")


class MemoryStore:
    def __init__(
        self,
        path: str | Path,
        *,
        candidate_providers: Sequence[CandidateProvider] | None = None,
        resonance_hops: int = 1,
        check_same_thread: bool = True,
    ):
        if resonance_hops < 0:
            raise ValueError("resonance_hops must be >= 0")
        _validate_migration_plan(MIGRATIONS)
        self.path = Path(path)
        self.candidate_providers = list(candidate_providers or [])
        self.resonance_hops = resonance_hops
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False lets a server share one connection across a
        # threadpool; callers doing so must serialize access themselves.
        self.conn = sqlite3.connect(self.path, check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        # Multi-agent deployments share one database file; WAL lets concurrent
        # readers coexist with a writer and busy_timeout absorbs write races.
        # Both PRAGMAs degrade gracefully where unsupported (e.g. some network
        # filesystems keep journal_mode unchanged).
        self.conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MILLISECONDS}")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._run_migrations()
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ #
    # Schema migrations: forward-only, versioned, recorded per database.
    # Each migration must be idempotent (it may run once against databases
    # created before the migrations table existed).
    # ------------------------------------------------------------------ #

    def _run_migrations(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              description TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            row["version"]: row["description"]
            for row in self.conn.execute(
                "SELECT version, description FROM schema_migrations"
            )
        }
        for version, description, migrate in MIGRATIONS:
            if version in applied:
                if applied[version] != description:
                    raise RuntimeError(
                        f"migration {version} history mismatch: database recorded "
                        f"{applied[version]!r}, code expects {description!r}"
                    )
                continue
            migrate(self.conn)
            self.conn.execute(
                "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?, ?, ?)",
                (version, description, utc_now()),
            )

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    def integrity_check(self) -> dict[str, object]:
        """Verify the database and the store's own invariants.

        Returns per-check results; `ok` is the conjunction. Read-only.
        """
        checks: dict[str, object] = {}
        pragma = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
        checks["sqlite_integrity"] = pragma == "ok"
        total = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        indexed = self.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        checks["fts_in_sync"] = indexed == total
        checks["fts_rows"] = {"memories": int(total), "indexed": int(indexed)}
        orphan_edges = self.conn.execute(
            """
            SELECT COUNT(*) FROM memory_links l
            WHERE NOT EXISTS (SELECT 1 FROM memories m WHERE m.id = l.src_id)
               OR NOT EXISTS (SELECT 1 FROM memories m WHERE m.id = l.dst_id)
            """
        ).fetchone()[0]
        checks["orphan_links"] = int(orphan_edges)
        checks["links_valid"] = orphan_edges == 0
        checks["schema_version"] = self.schema_version()
        checks["ok"] = bool(
            checks["sqlite_integrity"] and checks["fts_in_sync"] and checks["links_valid"]
        )
        return checks

    def add(self, record: MemoryRecord) -> MemoryRecord:
        record.summary = record.normalized_summary()
        self.conn.execute(
            """
            INSERT INTO memories(id, owner, scope, type, content, summary, tags, visibility, source,
                                 confidence, importance, created_at, updated_at, acl_updated_at, expires_at,
                                 decay_policy, decay_half_life_days, decay_base_half_life_days,
                                 last_accessed_at, access_count, pinned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id, record.owner, record.scope, record.type, record.content, record.summary,
                record.tags_json(), record.visibility_json(), record.source_json(),
                record.confidence, record.importance, record.created_at, record.updated_at,
                record.updated_at, record.expires_at,
                record.decay_policy, record.decay_half_life_days, record.decay_half_life_days,
                record.last_accessed_at, record.access_count, int(record.pinned),
            ),
        )
        self.conn.commit()
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_visible(
        self,
        memory_id: str,
        *,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
    ) -> MemoryRecord | None:
        """get() through the same ACL/expiry gate as search.

        A requester never resolves a memory it could not have found by
        searching — invisible ids return None, indistinguishable from absent.
        """
        rows = self._visible_rows_for_ids(
            [memory_id],
            owner=None,
            scope=None,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            now=utc_now(),
        )
        return self._row_to_record(rows[0]) if rows else None

    UPDATABLE_FIELDS = {
        "content", "summary", "tags", "visibility", "source", "confidence",
        "importance", "type", "scope", "pinned", "expires_at",
        "decay_policy", "decay_half_life_days",
    }

    def update_memory(
        self,
        memory_id: str,
        *,
        requester_agent_id: str | None = None,
        **fields,
    ) -> MemoryRecord:
        """Update selected fields of a memory; validation runs through MemoryRecord.

        The updated_at bump is intentional here (unlike recall feedback):
        editing content IS new information, so the freshness clock restarts.
        """
        unknown = set(fields) - self.UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"cannot update fields: {sorted(unknown)}")
        existing = self.get(memory_id)
        if existing is None:
            raise KeyError(memory_id)
        if requester_agent_id is not None and existing.owner != requester_agent_id:
            raise KeyError(memory_id)
        for name, value in fields.items():
            setattr(existing, name, value)
        if "content" in fields and "summary" not in fields:
            existing.summary = None
        existing.summary = existing.normalized_summary()
        # Re-run the complete canonical validation and retain normalized values
        # such as UTC expiry timestamps.
        existing = replace(existing)
        existing.updated_at = utc_now_micro()
        self.conn.execute(
            """
            UPDATE memories SET content=?, summary=?, tags=?, visibility=?, source=?,
                                confidence=?, importance=?, type=?, scope=?, pinned=?,
                                expires_at=?, decay_policy=?, decay_half_life_days=?, updated_at=?
            WHERE id=?
            """,
            (
                existing.content, existing.summary, existing.tags_json(),
                existing.visibility_json(), existing.source_json(),
                existing.confidence, existing.importance, existing.type, existing.scope,
                int(existing.pinned), existing.expires_at, existing.decay_policy,
                existing.decay_half_life_days, existing.updated_at, memory_id,
            ),
        )
        if "decay_half_life_days" in fields:
            # An explicit half-life edit sets a new configured base for tuning.
            self.conn.execute(
                "UPDATE memories SET decay_base_half_life_days = ? WHERE id = ?",
                (existing.decay_half_life_days, memory_id),
            )
        if "visibility" in fields:
            # A visibility change is an ACL change — bump the independent ACL
            # clock so the share/revoke propagates over sync (same discipline as
            # _set_visibility; without this a revoke made via update() would stay
            # local). Microsecond resolution so an edit + ACL change in the same
            # second still orders after creation.
            self.conn.execute(
                "UPDATE memories SET acl_updated_at = ? WHERE id = ?",
                (utc_now_micro(), memory_id),
            )
        self.conn.commit()
        return existing

    def update_content(self, memory_id: str, content: str, *, summary: str | None = None) -> MemoryRecord:
        existing = self.get(memory_id)
        if not existing:
            raise KeyError(memory_id)
        existing.content = content
        existing.summary = summary or MemoryRecord(content=content).normalized_summary()
        existing = replace(existing)
        existing.updated_at = utc_now_micro()
        self.conn.execute(
            "UPDATE memories SET content=?, summary=?, updated_at=? WHERE id=?",
            (existing.content, existing.summary, existing.updated_at, memory_id),
        )
        self.conn.commit()
        return existing

    def delete(self, memory_id: str) -> bool:
        self.conn.execute(
            "DELETE FROM memory_links WHERE src_id = ? OR dst_id = ?", (memory_id, memory_id)
        )
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._record_tombstone(memory_id, commit=False)
        self.conn.commit()
        return cur.rowcount > 0

    def _record_tombstone(self, memory_id: str, *, commit: bool = True) -> None:
        """Mark an id as deleted so the deletion propagates over sync instead
        of the row resurrecting from a peer that still holds it."""
        self.conn.execute(
            "INSERT INTO tombstones(id, deleted_at) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET deleted_at = excluded.deleted_at",
            (memory_id, utc_now()),
        )
        if commit:
            self.conn.commit()

    def list_tombstones(self, *, since: str | None = None) -> list[tuple[str, str]]:
        if since:
            rows = self.conn.execute(
                "SELECT id, deleted_at FROM tombstones WHERE deleted_at > ?", (since,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT id, deleted_at FROM tombstones").fetchall()
        return [(row["id"], row["deleted_at"]) for row in rows]

    def tombstone_for(self, memory_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT deleted_at FROM tombstones WHERE id = ?", (memory_id,)
        ).fetchone()
        return row["deleted_at"] if row else None

    def add_link(self, link: MemoryLink) -> MemoryLink:
        """Upsert an authoritative association edge between two existing memories."""
        for endpoint in (link.src_id, link.dst_id):
            if self.get(endpoint) is None:
                raise KeyError(endpoint)
        self.conn.execute(
            """
            INSERT INTO memory_links(src_id, dst_id, relation, weight, created_at, updated_at,
                                     last_activated_at, activation_count, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(src_id, dst_id, relation) DO UPDATE SET
              weight = excluded.weight,
              updated_at = excluded.updated_at,
              source = excluded.source
            """,
            (
                link.src_id, link.dst_id, link.relation, link.weight,
                link.created_at, link.updated_at, link.last_activated_at,
                link.activation_count, link.source_json(),
            ),
        )
        self.conn.commit()
        return link

    def remove_link(self, src_id: str, dst_id: str, relation: str | None = None) -> bool:
        where = "((src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?))"
        params: list[object] = [src_id, dst_id, dst_id, src_id]
        if relation:
            where += " AND relation = ?"
            params.append(relation)
        cur = self.conn.execute(f"DELETE FROM memory_links WHERE {where}", params)
        self.conn.commit()
        return cur.rowcount > 0

    def auto_link_similar(
        self,
        record: MemoryRecord,
        *,
        limit: int = AUTO_LINK_LIMIT,
        weight: float = AUTO_LINK_WEIGHT,
    ) -> list[MemoryLink]:
        """Create weak `related_to` edges from a new memory to its FTS neighbors.

        This is the write-time association pass: a new memory immediately joins
        the graph near lexically similar memories. Edges are weak and derived —
        co-recall reinforcement decides which of them mature. Reading through
        these edges is still ACL/expiry hard-gated, so linking across owners or
        visibility levels leaks nothing.
        """
        query = " ".join(record.content.split()[:16])
        if not query.strip():
            return []
        rows = self._fts_rows(
            query,
            owner=None,
            scope=None,
            requester_agent_id=None,
            requester_team_id=None,
            limit=limit + 1,
            now=utc_now(),
        )
        created: list[MemoryLink] = []
        for row in rows:
            if row["id"] == record.id or len(created) >= limit:
                continue
            existing = self.conn.execute(
                "SELECT 1 FROM memory_links WHERE (src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?)",
                (record.id, row["id"], row["id"], record.id),
            ).fetchone()
            if existing:
                continue
            created.append(
                self.add_link(
                    MemoryLink(
                        src_id=record.id,
                        dst_id=row["id"],
                        relation="related_to",
                        weight=weight,
                        source={"auto": "fts_similarity"},
                    )
                )
            )
        return created

    def save_profile(self, profile: RecallProfile) -> RecallProfile:
        if not profile.agent_id:
            raise ValueError("profile.agent_id must be non-empty to persist")
        self.conn.execute(
            """
            INSERT INTO recall_profiles(agent_id, type_weights, scope_weights, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
              type_weights = excluded.type_weights,
              scope_weights = excluded.scope_weights,
              updated_at = excluded.updated_at
            """,
            (
                profile.agent_id,
                json.dumps(profile.type_weights, ensure_ascii=False, sort_keys=True),
                json.dumps(profile.scope_weights, ensure_ascii=False, sort_keys=True),
                utc_now(),
            ),
        )
        self.conn.commit()
        return profile

    def load_profile(self, agent_id: str) -> RecallProfile | None:
        row = self.conn.execute(
            "SELECT * FROM recall_profiles WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return RecallProfile(
            agent_id=row["agent_id"],
            type_weights=json.loads(row["type_weights"] or "{}"),
            scope_weights=json.loads(row["scope_weights"] or "{}"),
        )

    def list_recent(
        self,
        *,
        owner: str | None = None,
        scope: str | None = None,
        memory_type: str | None = None,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """List memories by recency for browsing (inventory view, no scoring)."""
        where = ["1=1"]
        params: list[object] = []
        if owner:
            where.append("owner = ?")
            params.append(owner)
        if scope:
            where.append("scope = ?")
            params.append(scope)
        if memory_type:
            where.append("type = ?")
            params.append(memory_type)
        self._append_acl_filter(
            where,
            params,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            alias="",
        )
        params.extend([max(1, limit), max(0, offset)])
        rows = self.conn.execute(
            f"""
            SELECT * FROM memories WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC, rowid DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def bedrock_records(
        self,
        *,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = 6,
    ) -> list[MemoryRecord]:
        """Authority-track constants (pinned / permanence+weight) for every pack."""
        rows = self._authority_rows(
            owner=None,
            scope=None,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            limit=limit,
            now=utc_now(),
        )
        return [self._row_to_record(row) for row in rows]

    def top_records_by_type(
        self,
        memory_type: str,
        *,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = 4,
    ) -> list[MemoryRecord]:
        """Proactive recall source: the most important live records of a type."""
        where = [
            "type = ?",
            "(expires_at IS NULL OR julianday(expires_at) > julianday(?))",
        ]
        params: list[object] = [memory_type, utc_now()]
        self._append_acl_filter(
            where,
            params,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            alias="",
        )
        params.append(max(1, limit))
        rows = self.conn.execute(
            f"""
            SELECT * FROM memories WHERE {' AND '.join(where)}
            ORDER BY pinned DESC, importance DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def delivered_ids(self, session_id: str, *, owner: str | None = None) -> set[str]:
        rows = self.conn.execute(
            "SELECT memory_id FROM session_recall_log "
            "WHERE owner IN (?, ?) AND session_id = ?",
            (owner or "", LEGACY_CONTEXT_OWNER, session_id),
        ).fetchall()
        return {row["memory_id"] for row in rows}

    def record_delivery(
        self,
        session_id: str,
        memory_ids: Sequence[str],
        *,
        owner: str | None = None,
    ) -> None:
        now = utc_now()
        self.conn.executemany(
            "INSERT OR IGNORE INTO session_recall_log"
            "(owner, session_id, memory_id, delivered_at) VALUES (?, ?, ?, ?)",
            [(owner or "", session_id, memory_id, now) for memory_id in memory_ids],
        )
        self.conn.commit()

    def rotate_snapshots(self, *, keep_per_session: int = SNAPSHOT_RETENTION_COUNT_PER_SESSION) -> int:
        """Archive all but the newest N context snapshots per session.

        Pinned snapshots are never rotated out — they follow the same rule as
        the rest of retention, where only a hard expiry retires a pinned row.
        """
        rows = self.conn.execute(
            """
            SELECT id FROM (
              SELECT id, ROW_NUMBER() OVER (
                PARTITION BY owner, json_extract(source, '$.session_id')
                ORDER BY created_at DESC, rowid DESC
              ) AS rank
              FROM memories
              WHERE type = 'snapshot' AND json_extract(source, '$.session_id') IS NOT NULL
                AND pinned = 0
            ) WHERE rank > ?
            """,
            (max(1, keep_per_session),),
        ).fetchall()
        ids = [row["id"] for row in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        return self._archive_where(f"id IN ({placeholders})", ids, reason="snapshot_rotation")

    AGENT_KINDS = {"claude-code", "codex", "openclaw", "hermes", "agy", "custom"}

    def register_agent(
        self,
        agent_id: str,
        *,
        display_name: str = "",
        kind: str = "custom",
        teams: Sequence[str] | None = None,
        notes: str = "",
    ) -> dict[str, object]:
        agent_id = agent_id.strip()
        if not agent_id:
            raise ValueError("agent id must be non-empty")
        if kind not in self.AGENT_KINDS:
            raise ValueError(f"kind must be one of {sorted(self.AGENT_KINDS)}")
        # teams=None means "leave team membership alone" (e.g. editing an
        # agent's display name); only an explicit list reconciles membership,
        # so a metadata-only update never wipes Teams-tab-managed memberships.
        team_list = (
            sorted({team.strip() for team in teams if team.strip()})
            if teams is not None else None
        )
        self.conn.execute(
            """
            INSERT INTO agents(id, display_name, kind, teams, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              display_name = excluded.display_name,
              kind = excluded.kind,
              notes = excluded.notes
            """ + ("" if team_list is None else ", teams = excluded.teams"),
            (agent_id, display_name, kind, json.dumps(team_list or []), notes, utc_now()),
        )
        # team_members is authoritative for ACL: reconcile this agent's rows to
        # the declared list (create missing teams), so declaring an agent's
        # teams == setting its team membership. The agents.teams column is kept
        # as a denormalized convenience only.
        if team_list is not None:
            self._reconcile_agent_teams(agent_id, team_list)
        self.conn.commit()
        self._invalidate_membership_caches()
        return self.get_agent(agent_id)

    def _reconcile_agent_teams(self, agent_id: str, team_list: Sequence[str]) -> None:
        now = utc_now()
        wanted = {t for t in team_list if t}
        current = {r[0] for r in self.conn.execute(
            "SELECT team_id FROM team_members WHERE agent_id = ?", (agent_id,)
        ).fetchall()}
        for team_id in wanted - current:
            self.conn.execute(
                "INSERT OR IGNORE INTO teams(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (team_id, team_id, now, now),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO team_members(team_id, agent_id) VALUES (?, ?)",
                (team_id, agent_id),
            )
            self._touch_team(team_id)  # membership change -> version bump for sync
        for team_id in current - wanted:
            self._remove_team_member_row(team_id, agent_id)

    def get_agent(self, agent_id: str) -> dict[str, object] | None:
        row = self.conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "display_name": row["display_name"], "kind": row["kind"],
            "teams": self.teams_for(row["id"]), "notes": row["notes"],
            "created_at": row["created_at"], "last_seen_at": row["last_seen_at"],
        }

    def list_agents(self) -> list[dict[str, object]]:
        rows = self.conn.execute("SELECT id FROM agents ORDER BY id").fetchall()
        agents = []
        for row in rows:
            agent = self.get_agent(row["id"])
            agent["memory_count"] = self.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE owner = ?", (row["id"],)
            ).fetchone()[0]
            agents.append(agent)
        return agents

    def remove_agent(self, agent_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        self.conn.execute("DELETE FROM team_members WHERE agent_id = ?", (agent_id,))
        self.conn.execute("DELETE FROM project_members WHERE agent_id = ?", (agent_id,))
        self.conn.commit()
        self._invalidate_membership_caches()
        return cur.rowcount > 0

    def owner_counts(self) -> list[dict[str, object]]:
        """Memory count per owner (live + archived), newest-active first.

        Powers the console's owner/identity panel and the "some memories are
        owned by an identity you are not browsing as" hint: an owner with
        memories that no current identity can see is otherwise invisible.
        """
        live = {r["owner"]: r["n"] for r in self.conn.execute(
            "SELECT owner, COUNT(*) AS n FROM memories GROUP BY owner")}
        arch = {r["owner"]: r["n"] for r in self.conn.execute(
            "SELECT owner, COUNT(*) AS n FROM memories_archive GROUP BY owner")}
        legacy_deliveries = int(self.conn.execute(
            "SELECT COUNT(*) FROM session_recall_log WHERE owner = ?",
            (LEGACY_CONTEXT_OWNER,),
        ).fetchone()[0])
        registered = {a["id"] for a in self.list_agents()}
        owners = sorted(set(live) | set(arch))
        if legacy_deliveries and LEGACY_CONTEXT_OWNER not in owners:
            owners.append(LEGACY_CONTEXT_OWNER)
        return [
            {"owner": o, "memories": live.get(o, 0), "archived": arch.get(o, 0),
             "registered_agent": o in registered,
             "context_deliveries": legacy_deliveries if o == LEGACY_CONTEXT_OWNER else 0,
             "classification_required": o == LEGACY_CONTEXT_OWNER}
            for o in owners
        ]

    def reassign_owner(self, old_owner: str, new_owner: str) -> dict[str, int]:
        """Move every reference from `old_owner` to `new_owner`, atomically.

        Unlike `rename_agent`, this MERGES: `new_owner` may already exist (its
        memories/memberships are kept and the old owner's folded in). Use it to
        re-attribute memories written under a fallback owner (e.g. 'default')
        to a real agent identity. Moves memory ownership, archived rows,
        `agent:<old>` ACL grants (bumping the ACL clock so peers converge),
        team/project memberships (dedup on conflict), recall profiles, and the
        agents-registry row if present. Requester-scoped delivery history moves
        with the owner as well. Returns per-table change counts.
        """
        old_owner = old_owner.strip()
        new_owner = new_owner.strip()
        if not old_owner or not new_owner:
            raise ValueError("reassign_owner requires non-empty ids")
        if old_owner == new_owner:
            raise ValueError("old and new owner are identical")

        counts: dict[str, int] = {}
        now = utc_now()
        with self.conn:  # one transaction — all or nothing
            counts["memories_owner"] = self.conn.execute(
                "UPDATE memories SET owner = ? WHERE owner = ?", (new_owner, old_owner)
            ).rowcount
            counts["archive_owner"] = self.conn.execute(
                "UPDATE memories_archive SET owner = ? WHERE owner = ?", (new_owner, old_owner)
            ).rowcount
            old_token = json.dumps(f"agent:{old_owner}", ensure_ascii=False)
            new_token = json.dumps(f"agent:{new_owner}", ensure_ascii=False)
            counts["visibility_grants"] = self.conn.execute(
                "UPDATE memories SET visibility = replace(visibility, ?, ?),"
                " acl_updated_at = ? WHERE visibility LIKE ?",
                (old_token, new_token, now, f'%"agent:{old_owner}"%'),
            ).rowcount
            # Agents registry: rename the row if the target is free, else drop
            # the old row (memberships below are merged onto new_owner anyway).
            if self.conn.execute("SELECT 1 FROM agents WHERE id = ?", (old_owner,)).fetchone():
                if self.conn.execute("SELECT 1 FROM agents WHERE id = ?", (new_owner,)).fetchone():
                    self.conn.execute("DELETE FROM agents WHERE id = ?", (old_owner,))
                    counts["agents_registry"] = 1
                else:
                    counts["agents_registry"] = self.conn.execute(
                        "UPDATE agents SET id = ? WHERE id = ?", (new_owner, old_owner)
                    ).rowcount
            else:
                counts["agents_registry"] = 0
            counts["team_memberships"] = self.conn.execute(
                "UPDATE OR IGNORE team_members SET agent_id = ? WHERE agent_id = ?",
                (new_owner, old_owner),
            ).rowcount
            counts["project_memberships"] = self.conn.execute(
                "UPDATE OR IGNORE project_members SET agent_id = ? WHERE agent_id = ?",
                (new_owner, old_owner),
            ).rowcount
            self.conn.execute("DELETE FROM team_members WHERE agent_id = ?", (old_owner,))
            self.conn.execute("DELETE FROM project_members WHERE agent_id = ?", (old_owner,))
            counts["recall_profiles"] = self.conn.execute(
                "UPDATE OR REPLACE recall_profiles SET agent_id = ? WHERE agent_id = ?",
                (new_owner, old_owner),
            ).rowcount
            delivery_rows = int(self.conn.execute(
                "SELECT COUNT(*) FROM session_recall_log WHERE owner = ?",
                (old_owner,),
            ).fetchone()[0])
            if delivery_rows:
                self.conn.execute(
                    "INSERT OR IGNORE INTO session_recall_log"
                    "(owner, session_id, memory_id, delivered_at) "
                    "SELECT ?, session_id, memory_id, delivered_at "
                    "FROM session_recall_log WHERE owner = ?",
                    (new_owner, old_owner),
                )
                self.conn.execute(
                    "DELETE FROM session_recall_log WHERE owner = ?",
                    (old_owner,),
                )
            counts["context_deliveries"] = delivery_rows
        self._invalidate_membership_caches()
        return counts

    def rename_agent(self, old_id: str, new_id: str) -> dict[str, int]:
        """Rename an agent identity to a NEW (non-existent) id, atomically.

        Like `reassign_owner` but with a guard: the target id must not already
        exist, so a rename can never silently merge two identities. To merge
        into an existing identity, use `reassign_owner`.
        """
        old_id = old_id.strip()
        new_id = new_id.strip()
        if not old_id or not new_id:
            raise ValueError("rename_agent requires non-empty ids")
        if old_id == new_id:
            raise ValueError("old and new agent ids are identical")
        if self.conn.execute("SELECT 1 FROM agents WHERE id = ?", (new_id,)).fetchone():
            raise ValueError(f"agent id already exists: {new_id}")
        return self.reassign_owner(old_id, new_id)

    def join_team_and_register_peer(
        self, team_id: str, agent_id: str, *,
        peer_url: str = "", peer_token: str | None = None, peer_name: str = "",
    ) -> None:
        """Add an agent to a team AND register the joining node as a peer in
        ONE transaction (used by pairing redemption).

        The individual add_* helpers each commit, so calling them in sequence
        is not atomic — a failure between them leaves a ghost team member.
        This does all writes under a single commit: either the joiner is fully
        wired up or nothing changed.
        """
        if self.get_team(team_id) is None:
            raise KeyError(f"team not found: {team_id}")
        url = ""
        if peer_url:
            url = peer_url.strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise ValueError("peer URL must start with http:// or https://")
        with self.conn:  # single atomic transaction (no inner commits below)
            now = utc_now()
            # Register the joining agent in the registry, not just touch it: a
            # remote node joining by pairing has no prior row here, and an
            # UPDATE would no-op and leave it invisible in the Agents tab
            # (member of the team, but not a known identity). INSERT it as a
            # 'custom' agent named after its node; on re-join only bump the
            # clock so an operator-set display_name/kind is never clobbered.
            self.conn.execute(
                "INSERT INTO agents(id, display_name, kind, teams, notes, created_at, last_seen_at)"
                " VALUES (?, ?, 'custom', '[]', '', ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
                (agent_id, peer_name or agent_id, now, now))
            self.conn.execute(
                "INSERT OR IGNORE INTO team_members(team_id, agent_id) VALUES (?, ?)",
                (team_id, agent_id))
            self._touch_team(team_id)
            self._org_audit("add_team_member",
                            f"{agent_id} -> team:{team_id}", "pairing-invite")
            if url:
                self.conn.execute(
                    "INSERT INTO sync_peers(url, token, added_at, policy, name)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(url) DO UPDATE SET token=excluded.token,"
                    " policy=excluded.policy, name=excluded.name",
                    (url, peer_token, now, f"team:{team_id}", peer_name or agent_id))
        self._invalidate_membership_caches()

    def update_peer_name(self, url: str, name: str) -> bool:
        """Refresh a peer's display name (from its advertised node identity)."""
        name = (name or "").strip()
        if not name:
            return False
        cursor = self.conn.execute(
            "UPDATE sync_peers SET name = ? WHERE url = ? AND name != ?",
            (name, url.strip().rstrip("/"), name),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def touch_agent(self, agent_id: str) -> None:
        """Record activity for a registered agent (no-op for unknown ids)."""
        self.conn.execute(
            "UPDATE agents SET last_seen_at = ? WHERE id = ?", (utc_now(), agent_id)
        )
        self.conn.commit()

    def teams_for(self, agent_id: str | None) -> list[str]:
        if not agent_id:
            return []
        rows = self.conn.execute(
            "SELECT team_id FROM team_members WHERE agent_id = ? ORDER BY team_id", (agent_id,)
        ).fetchall()
        return [r[0] for r in rows]

    def projects_for(self, agent_id: str | None) -> list[str]:
        if not agent_id:
            return []
        rows = self.conn.execute(
            "SELECT project_id FROM project_members WHERE agent_id = ? ORDER BY project_id",
            (agent_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def suggested_peer_policy(self, agent_id: str) -> dict[str, object]:
        """Advisory: the tightest sync policy that still covers what an agent is
        entitled to, derived from local membership. The MANUAL policy set on a
        peer remains the enforced upper bound (see sync org_scope) — this only
        ever *narrows* a suggestion, never widens access:

        - member of exactly one team and no narrower project -> 'team:<id>'
        - member of exactly one project                      -> 'project:<id>'
        - member of nothing                                  -> 'shared'
        - multiple teams/projects (not expressible as one scope) -> 'shared',
          with the full entitlement list returned so an operator can choose.

        Returns {policy, teams, projects}. Never returns 'full' (that is a
        deliberate own-replica choice, not something to infer).
        """
        teams = self.teams_for(agent_id)
        projects = self.projects_for(agent_id)
        if len(projects) == 1 and not (len(teams) > 1):
            policy = f"project:{projects[0]}"
        elif len(teams) == 1 and not projects:
            policy = f"team:{teams[0]}"
        else:
            policy = "shared"
        return {"policy": policy, "teams": teams, "projects": projects}

    # ---------- team management ----------

    def create_team(self, team_id: str, *, name: str = "") -> dict[str, object]:
        team_id = team_id.strip()
        if not team_id:
            raise ValueError("team id must be non-empty")
        now = utc_now()
        self.conn.execute(
            "INSERT INTO teams(id, name, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
            (team_id, name or team_id, now, now),
        )
        self._org_audit("create_team", team_id)
        self.conn.commit()
        return self.get_team(team_id)

    def get_team(self, team_id: str) -> dict[str, object] | None:
        row = self.conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        if row is None:
            return None
        members = [r[0] for r in self.conn.execute(
            "SELECT agent_id FROM team_members WHERE team_id = ? ORDER BY agent_id", (team_id,)
        ).fetchall()]
        projects = [r[0] for r in self.conn.execute(
            "SELECT id FROM projects WHERE team_id = ? ORDER BY id", (team_id,)
        ).fetchall()]
        return {"id": row["id"], "name": row["name"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "members": members, "projects": projects}

    def list_teams(self) -> list[dict[str, object]]:
        rows = self.conn.execute("SELECT id FROM teams ORDER BY id").fetchall()
        return [self.get_team(r[0]) for r in rows]

    def delete_team(self, team_id: str) -> bool:
        # Cascade: the team's projects, and all memberships, go with it.
        project_ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM projects WHERE team_id = ?", (team_id,)
        ).fetchall()]
        for pid in project_ids:
            self.conn.execute("DELETE FROM project_members WHERE project_id = ?", (pid,))
            self._strip_visibility_grant(f"project:{pid}")
        self.conn.execute("DELETE FROM projects WHERE team_id = ?", (team_id,))
        self.conn.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
        cur = self.conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        # Revoke the now-orphaned grant so a reused team id can't resurrect
        # read access to the old team's scoped memory. Two grant schemes carry
        # team visibility (see _append_acl_filter): the explicit `team:<id>`
        # value AND the legacy bare `team` value keyed by source.team_id. Strip
        # both, else a reused id resurrects access through the bare scheme.
        self._strip_visibility_grant(f"team:{team_id}")
        self._strip_bare_team_grant(team_id)
        # Tombstones so the deletion (and its projects') propagates over sync.
        for pid in project_ids:
            self._org_tombstone("project", pid)
        self._org_tombstone("team", team_id)
        self._org_audit("delete_team", team_id)
        self.conn.commit()
        self._invalidate_membership_caches()
        return cur.rowcount > 0

    def _strip_visibility_grant(self, grant: str) -> None:
        """Remove one `team:<id>`/`project:<id>`/`agent:<id>` grant from every
        memory's visibility. Used when a scope is deleted so a reused id cannot
        resurrect access. ACL-only change — does not touch updated_at."""
        self.conn.execute(
            """
            UPDATE memories
            SET visibility = COALESCE(
                (SELECT json_group_array(value) FROM json_each(memories.visibility)
                 WHERE value != ?), '[]')
            WHERE EXISTS (SELECT 1 FROM json_each(memories.visibility) WHERE value = ?)
            """,
            (grant, grant),
        )

    def _strip_bare_team_grant(self, team_id: str) -> None:
        """Remove the legacy bare `team` grant from memories keyed to this team
        via source.team_id. The bare scheme is invisible to
        `_strip_visibility_grant`'s single-string match, so a deleted team's id,
        if reused, would otherwise resurrect read access through it."""
        self.conn.execute(
            """
            UPDATE memories
            SET visibility = COALESCE(
                (SELECT json_group_array(value) FROM json_each(memories.visibility)
                 WHERE value != 'team'), '[]')
            WHERE json_extract(source, '$.team_id') = ?
              AND EXISTS (SELECT 1 FROM json_each(memories.visibility) WHERE value = 'team')
            """,
            (team_id,),
        )

    def team_rename_preview(self, old_id: str, new_id: str) -> dict[str, object]:
        """Report what a `rename_team` would move, without changing anything.

        A team id is not just a row key: it is the token inside every
        `team:<id>` visibility grant, the parent key of every project, and the
        `source.team_id` that the legacy bare `team` grant resolves through. An
        operator renaming a team deserves to see all of that first, so this is
        the pre-flight the CLI and console call before asking to proceed.

        `content_mentions` is informational only — prose that happens to name
        the team is history and is never rewritten.
        """
        old_id = (old_id or "").strip()
        new_id = (new_id or "").strip()
        grant_old = json.dumps(f"team:{old_id}", ensure_ascii=False)
        q = self.conn.execute
        return {
            "old_id": old_id,
            "new_id": new_id,
            "exists": q("SELECT 1 FROM teams WHERE id = ?", (old_id,)).fetchone() is not None,
            "target_exists": q("SELECT 1 FROM teams WHERE id = ?", (new_id,)).fetchone() is not None,
            "members": int(q("SELECT COUNT(*) FROM team_members WHERE team_id = ?",
                             (old_id,)).fetchone()[0]),
            "projects": [r[0] for r in q("SELECT id FROM projects WHERE team_id = ? ORDER BY id",
                                         (old_id,)).fetchall()],
            "project_members": int(q(
                "SELECT COUNT(*) FROM project_members WHERE project_id IN "
                "(SELECT id FROM projects WHERE team_id = ?)", (old_id,)).fetchone()[0]),
            "explicit_grants": int(q(
                "SELECT COUNT(*) FROM memories WHERE instr(visibility, ?) > 0",
                (grant_old,)).fetchone()[0]),
            "archived_grants": int(q(
                "SELECT COUNT(*) FROM memories_archive WHERE instr(visibility, ?) > 0",
                (grant_old,)).fetchone()[0]),
            "bare_grants": int(q(
                "SELECT COUNT(*) FROM memories WHERE json_extract(source, '$.team_id') = ? "
                "AND EXISTS (SELECT 1 FROM json_each(memories.visibility) WHERE value = 'team')",
                (old_id,)).fetchone()[0]),
            "content_mentions": int(q(
                "SELECT COUNT(*) FROM memories WHERE content LIKE ?",
                (f"%{old_id}%",)).fetchone()[0]),
            "sync_peers": [p["url"] for p in self.list_peers()],
        }

    def rename_team(self, old_id: str, new_id: str, *, name: str | None = None,
                    actor: str = "local") -> dict[str, object]:
        """Rename a team id to a NEW (non-existent) id, moving every reference.

        Atomic. Moves, in one transaction: the team row (keeping its
        `created_at`), team memberships, the `team_id` of every project under
        it, the `team:<old>` grant in live and archived memory visibility, and
        the `source.team_id` key that the legacy bare `team` grant resolves
        through. Bumps `acl_updated_at` on live memories whose grant changed —
        the ACL clock is what sync converges visibility on, so a rename that
        left it alone would lose to a peer's older copy.

        Display name: an explicit `name` wins; otherwise a name that merely
        mirrored the old id follows the rename, and a name the operator
        actually chose is preserved.

        Deliberately does NOT emit an org tombstone for the old id. A team
        tombstone means "this team is gone" to a peer, and applying one
        cascade-deletes that team's projects and strips their `project:<id>`
        grants from memories (see `_apply_org_tombstone`) — grants the incoming
        renamed records cannot restore, because project records carry no memory
        ACLs. A rename is therefore local state: peers keep the old team as an
        inert orphan (nothing references it once the grants move) and the
        result reports them so the operator can reconcile deliberately.
        """
        old_id = (old_id or "").strip()
        new_id = (new_id or "").strip()
        if not old_id or not new_id:
            raise ValueError("rename_team requires non-empty ids")
        if old_id == new_id:
            raise ValueError("old and new team ids are identical")
        row = self.conn.execute("SELECT name, created_at FROM teams WHERE id = ?",
                                (old_id,)).fetchone()
        if row is None:
            raise KeyError(f"team not found: {old_id}")
        if self.conn.execute("SELECT 1 FROM teams WHERE id = ?", (new_id,)).fetchone():
            raise ValueError(f"team id already exists: {new_id}")

        if name is not None:
            new_name = name
        elif (row["name"] or "") == old_id:
            new_name = new_id
        else:
            new_name = row["name"]

        grant_old = json.dumps(f"team:{old_id}", ensure_ascii=False)
        grant_new = json.dumps(f"team:{new_id}", ensure_ascii=False)
        counts: dict[str, object] = {"old_id": old_id, "new_id": new_id, "name": new_name}
        now = utc_now()
        with self.conn:  # one transaction — all or nothing
            self.conn.execute(
                "UPDATE teams SET id = ?, name = ?, updated_at = ? WHERE id = ?",
                (new_id, new_name, now, old_id),
            )
            counts["members"] = self.conn.execute(
                "UPDATE team_members SET team_id = ? WHERE team_id = ?", (new_id, old_id)
            ).rowcount
            counts["projects"] = self.conn.execute(
                "UPDATE projects SET team_id = ?, updated_at = ? WHERE team_id = ?",
                (new_id, now, old_id),
            ).rowcount
            counts["explicit_grants"] = self.conn.execute(
                "UPDATE memories SET visibility = replace(visibility, ?, ?), acl_updated_at = ? "
                "WHERE instr(visibility, ?) > 0",
                (grant_old, grant_new, now, grant_old),
            ).rowcount
            # Archived rows carry visibility but have no ACL clock to bump.
            counts["archived_grants"] = self.conn.execute(
                "UPDATE memories_archive SET visibility = replace(visibility, ?, ?) "
                "WHERE instr(visibility, ?) > 0",
                (grant_old, grant_new, grant_old),
            ).rowcount
            # Repoint the key the legacy bare `team` grant resolves through, so
            # those memories stay readable by the same team after the rename.
            counts["bare_grants"] = self.conn.execute(
                "UPDATE memories SET source = json_set(source, '$.team_id', ?), "
                "acl_updated_at = ? "
                "WHERE json_extract(source, '$.team_id') = ? "
                "AND EXISTS (SELECT 1 FROM json_each(memories.visibility) WHERE value = 'team')",
                (new_id, now, old_id),
            ).rowcount
            counts["agent_team_mirrors"] = int(self.conn.execute(
                "SELECT COUNT(*) FROM agents "
                "WHERE EXISTS (SELECT 1 FROM json_each(agents.teams) WHERE value = ?)",
                (old_id,),
            ).fetchone()[0])
            self._org_audit("rename_team", f"{old_id} -> {new_id}", actor)
        # The `agents.teams` mirror is rebuilt from team_members by
        # _invalidate_membership_caches below, so the rename does not patch it
        # here — one rebuild path, not two that could disagree.
        self._invalidate_membership_caches()
        peers = [p["url"] for p in self.list_peers()]
        if peers:
            counts["sync_warning"] = (
                "a rename does not propagate as a deletion; these peers may keep "
                f"team:{old_id} as an inert orphan: {', '.join(peers)}"
            )
        return counts

    def add_team_member(self, team_id: str, agent_id: str, *, actor: str = "local") -> None:
        if self.get_team(team_id) is None:
            raise KeyError(f"team not found: {team_id}")
        self.conn.execute(
            "INSERT OR IGNORE INTO team_members(team_id, agent_id) VALUES (?, ?)",
            (team_id, agent_id),
        )
        self._touch_team(team_id)
        self._org_audit("add_team_member", f"{agent_id} -> team:{team_id}", actor)
        self.conn.commit()
        self._invalidate_membership_caches()

    def remove_team_member(self, team_id: str, agent_id: str, *, actor: str = "local") -> None:
        self._remove_team_member_row(team_id, agent_id)
        self._org_audit("remove_team_member", f"{agent_id} -> team:{team_id}", actor)
        self.conn.commit()
        self._invalidate_membership_caches()

    def _remove_team_member_row(self, team_id: str, agent_id: str) -> None:
        self.conn.execute(
            "DELETE FROM team_members WHERE team_id = ? AND agent_id = ?", (team_id, agent_id)
        )
        # Leaving a team removes the agent from that team's projects too — a
        # project member must always be a team member. Only the projects the
        # agent was ACTUALLY in change; touching the rest would bump their
        # updated_at with an unchanged member set and could clobber a concurrent
        # peer's real membership edit on those projects during LWW sync.
        affected = [r[0] for r in self.conn.execute(
            "SELECT project_id FROM project_members WHERE agent_id = ? AND project_id IN "
            "(SELECT id FROM projects WHERE team_id = ?)",
            (agent_id, team_id),
        ).fetchall()]
        self.conn.execute(
            "DELETE FROM project_members WHERE agent_id = ? AND project_id IN "
            "(SELECT id FROM projects WHERE team_id = ?)",
            (agent_id, team_id),
        )
        # Version bump so the membership change converges over sync.
        self._touch_team(team_id)
        for pid in affected:
            self._touch_project(pid)

    # ---------- project management ----------

    def create_project(self, project_id: str, team_id: str, *, name: str = "") -> dict[str, object]:
        project_id = project_id.strip()
        if not project_id:
            raise ValueError("project id must be non-empty")
        if self.get_team(team_id) is None:
            raise KeyError(f"team not found: {team_id}")
        existing = self.get_project(project_id)
        if existing and existing["team_id"] != team_id:
            # Re-pointing a project at a different team would leave members who
            # aren't in the new team, breaking the subset invariant. Disallow it.
            raise ValueError(
                f"project {project_id!r} already exists under team "
                f"{existing['team_id']!r}; delete it to recreate under another team"
            )
        now = utc_now()
        self.conn.execute(
            "INSERT INTO projects(id, team_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
            (project_id, team_id, name or project_id, now, now),
        )
        self._org_audit("create_project", f"{project_id} (team:{team_id})")
        self.conn.commit()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict[str, object] | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        members = [r[0] for r in self.conn.execute(
            "SELECT agent_id FROM project_members WHERE project_id = ? ORDER BY agent_id",
            (project_id,),
        ).fetchall()]
        return {"id": row["id"], "team_id": row["team_id"], "name": row["name"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "members": members}

    def list_projects(self, team_id: str | None = None) -> list[dict[str, object]]:
        if team_id:
            rows = self.conn.execute(
                "SELECT id FROM projects WHERE team_id = ? ORDER BY id", (team_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT id FROM projects ORDER BY id").fetchall()
        return [self.get_project(r[0]) for r in rows]

    def delete_project(self, project_id: str) -> bool:
        self.conn.execute("DELETE FROM project_members WHERE project_id = ?", (project_id,))
        cur = self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        # Revoke the orphaned grant so a reused project id can't resurrect access.
        self._strip_visibility_grant(f"project:{project_id}")
        self._org_tombstone("project", project_id)
        self._org_audit("delete_project", project_id)
        self.conn.commit()
        self._invalidate_membership_caches()
        return cur.rowcount > 0

    def add_project_member(self, project_id: str, agent_id: str, *, actor: str = "local") -> None:
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        # Enforce the subset invariant: a project member must be a team member.
        is_team_member = self.conn.execute(
            "SELECT 1 FROM team_members WHERE team_id = ? AND agent_id = ?",
            (project["team_id"], agent_id),
        ).fetchone()
        if not is_team_member:
            raise ValueError(
                f"{agent_id!r} must be a member of team {project['team_id']!r} "
                f"before joining project {project_id!r}"
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO project_members(project_id, agent_id) VALUES (?, ?)",
            (project_id, agent_id),
        )
        self._touch_project(project_id)
        self._org_audit("add_project_member", f"{agent_id} -> project:{project_id}", actor)
        self.conn.commit()
        self._invalidate_membership_caches()

    def remove_project_member(self, project_id: str, agent_id: str, *, actor: str = "local") -> None:
        self.conn.execute(
            "DELETE FROM project_members WHERE project_id = ? AND agent_id = ?",
            (project_id, agent_id),
        )
        self._touch_project(project_id)
        self._org_audit("remove_project_member", f"{agent_id} -> project:{project_id}", actor)
        self.conn.commit()
        self._invalidate_membership_caches()

    def _invalidate_membership_caches(self) -> None:
        self._teams_cache = {}
        self._projects_cache = {}
        self._sync_agent_team_mirrors()

    def _sync_agent_team_mirrors(self) -> None:
        """Recompute `agents.teams` from the authoritative `team_members`.

        The column is a denormalized convenience, but a stale one is a trap:
        `register_agent` RECONCILES membership to the list it is handed and
        drops any team absent from it, so a caller that round-trips this column
        (the console's agent editor does) would move an agent back to whatever
        the mirror still said — including a team id that no longer exists,
        taking its project memberships along.

        Rebuilding every row in one statement rather than patching each
        mutation site is deliberate: membership changes are rare and the table
        is one row per agent, so correctness-by-construction is worth more here
        than a targeted update that a future code path could forget to call.
        """
        self.conn.execute(
            """
            UPDATE agents SET teams = COALESCE(
                (SELECT json_group_array(team_id) FROM
                    (SELECT team_id FROM team_members
                     WHERE agent_id = agents.id ORDER BY team_id)),
                '[]')
            """
        )
        # Every caller invalidates AFTER committing its own work, so this owns
        # the commit for the mirror it just rewrote.
        self.conn.commit()

    # ---------- org versioning, audit & tombstones (federation) ----------

    def _touch_team(self, team_id: str) -> None:
        self.conn.execute("UPDATE teams SET updated_at = ? WHERE id = ?", (utc_now(), team_id))

    def _touch_project(self, project_id: str) -> None:
        self.conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (utc_now(), project_id))

    def _org_audit(self, action: str, detail: str, actor: str = "local") -> None:
        self.conn.execute(
            "INSERT INTO org_audit(at, actor, action, detail) VALUES (?, ?, ?, ?)",
            (utc_now(), actor or "local", action, detail),
        )

    def org_audit_log(self, *, limit: int = 100) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT at, actor, action, detail FROM org_audit ORDER BY id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def _org_tombstone(self, kind: str, id_: str) -> None:
        self.conn.execute(
            "INSERT INTO org_tombstones(kind, id, deleted_at) VALUES (?, ?, ?) "
            "ON CONFLICT(kind, id) DO UPDATE SET deleted_at = excluded.deleted_at",
            (kind, id_, utc_now()),
        )

    def list_org_tombstones(self, *, since: str | None = None) -> list[tuple[str, str, str]]:
        if since:
            rows = self.conn.execute(
                "SELECT kind, id, deleted_at FROM org_tombstones WHERE deleted_at > ?", (since,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT kind, id, deleted_at FROM org_tombstones").fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    PEER_POLICIES = {"full", "shared"}  # plus dynamic "team:<id>" / "project:<id>"

    @classmethod
    def _validate_peer_policy(cls, policy: str) -> str:
        policy = (policy or "").strip()
        scoped = (
            (policy.startswith("team:") or policy.startswith("project:"))
            and len(policy.split(":", 1)[1]) > 0
        )
        if policy in cls.PEER_POLICIES or scoped:
            return policy
        raise ValueError(
            "peer policy must be 'full', 'shared', 'team:<id>', or 'project:<id>' "
            f"(got {policy!r})"
        )

    def add_peer(
        self, url: str, *, token: str | None = None, policy: str = "shared",
        name: str = "",
    ) -> dict[str, object]:
        """Register a sync peer.

        `policy` decides what leaves for this peer:
        - 'shared' (default): every visibility EXCEPT private (visibility=[]).
          Private memories never leave the machine.
        - 'full': the entire store — use only for your own trusted replica nodes.
        - 'team:<id>': just that one team/project's shared memory.

        `name` is a friendly label shown instead of the URL during sync.
        """
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError("peer URL must start with http:// or https://")
        if url.startswith("http://") and not _is_local_url(url):
            import warnings

            warnings.warn(
                f"peer {url} uses plain HTTP over the network — the bearer token "
                "crosses the wire unencrypted. Set AGENT_MEMORY_SYNC_KEY on every "
                "node to encrypt the memory content (app-layer), and prefer "
                "https:// (a TLS reverse proxy or tunnel) so the token is "
                "protected too. Also hand peers a sync-scoped token "
                "(`agent-memory token create --sync`), not the admin token.",
                stacklevel=2,
            )
        policy = self._validate_peer_policy(policy)
        name = (name or "").strip()
        self.conn.execute(
            """
            INSERT INTO sync_peers(url, token, added_at, policy, name) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              token = excluded.token, policy = excluded.policy,
              name = CASE WHEN excluded.name != '' THEN excluded.name ELSE sync_peers.name END
            """,
            (url, token, utc_now(), policy, name),
        )
        self.conn.commit()
        return {"url": url, "has_token": token is not None, "policy": policy, "name": name}

    def set_peer_name(self, url: str, name: str) -> bool:
        cur = self.conn.execute(
            "UPDATE sync_peers SET name = ? WHERE url = ?",
            ((name or "").strip(), url.strip().rstrip("/")),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_peer_policy(self, url: str, policy: str) -> bool:
        policy = self._validate_peer_policy(policy)
        cur = self.conn.execute(
            "UPDATE sync_peers SET policy = ? WHERE url = ?",
            (policy, url.strip().rstrip("/")),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def peer_policy(self, url: str) -> str:
        row = self.conn.execute(
            "SELECT policy FROM sync_peers WHERE url = ?", (url.strip().rstrip("/"),)
        ).fetchone()
        return row["policy"] if row else "shared"

    def remove_peer(self, url: str) -> bool:
        cur = self.conn.execute("DELETE FROM sync_peers WHERE url = ?", (url.strip().rstrip("/"),))
        self.conn.commit()
        return cur.rowcount > 0

    def list_peers(self) -> list[dict[str, object]]:
        rows = self.conn.execute(
            "SELECT url, token IS NOT NULL AS has_token, added_at, last_synced_at, "
            "last_result, policy, name FROM sync_peers ORDER BY added_at"
        ).fetchall()
        return [
            {
                "url": row["url"], "has_token": bool(row["has_token"]),
                "added_at": row["added_at"], "last_synced_at": row["last_synced_at"],
                "last_result": row["last_result"], "policy": row["policy"],
                "name": row["name"],
            }
            for row in rows
        ]

    def peer_token(self, url: str) -> str | None:
        row = self.conn.execute(
            "SELECT token FROM sync_peers WHERE url = ?", (url.strip().rstrip("/"),)
        ).fetchone()
        return row["token"] if row else None

    # ------------------------------------------------------------------ #
    # Pairing invites (one-time team-join codes; only the hash is stored)
    # ------------------------------------------------------------------ #

    def create_pairing_invite(
        self, team_id: str, code_hash: str, *, ttl_seconds: int = PAIRING_INVITE_TTL_SECONDS
    ) -> dict[str, object]:
        if not self.get_team(team_id):
            raise ValueError(f"unknown team: {team_id}")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)
        invite_id = "inv_" + uuid.uuid4().hex[:16]
        self.conn.execute(
            "INSERT INTO pairing_invites (id, code_hash, team_id, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (invite_id, code_hash, team_id,
             now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return {"id": invite_id, "team_id": team_id,
                "expires_at": expires.isoformat(timespec="seconds")}

    def consume_pairing_invite(self, code_hash: str, *, redeemed_by: str) -> dict[str, object] | None:
        """Atomically redeem an invite: valid, unexpired, unused → mark used.

        The conditional UPDATE is the concurrency gate — two racing redeems
        of the same code cannot both see rowcount 1. Returns the invite's
        team_id on success, None for unknown/expired/already-used codes
        (indistinguishable to the caller, on purpose).
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = self.conn.execute(
            "UPDATE pairing_invites SET used_at = ?, redeemed_by = ?"
            " WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
            (now, redeemed_by, code_hash, now),
        )
        self.conn.commit()
        if cursor.rowcount != 1:
            return None
        row = self.conn.execute(
            "SELECT id, team_id FROM pairing_invites WHERE code_hash = ?", (code_hash,)
        ).fetchone()
        return {"id": row["id"], "team_id": row["team_id"]} if row else None

    # ---------- fleet admin trust anchors (v1.6) ----------

    FLEET_CAPS = {"manage", "read-private"}

    def grant_fleet_admin(
        self, public_key: str, caps: Sequence[str], *, actor: str = "local"
    ) -> dict[str, object]:
        """Accept signed fleet operations from this Ed25519 public key.

        Local-trust-channel only by design: this method is reached from the
        CLI (or a deliberate operator surface), NEVER from sync import — a
        peer must not be able to grant itself admin access. Granting an
        existing key updates its caps and clears any revocation.
        """
        public_key = public_key.strip()
        if not public_key:
            raise ValueError("public key must be non-empty")
        cap_list = sorted({c.strip() for c in caps if c.strip()})
        if not cap_list:
            raise ValueError("at least one capability is required")
        unknown = set(cap_list) - self.FLEET_CAPS
        if unknown:
            raise ValueError(
                f"unknown capabilities: {sorted(unknown)} (valid: {sorted(self.FLEET_CAPS)})")
        from . import crypto

        key_id = crypto.fleet_key_id(public_key)
        now = utc_now()
        self.conn.execute(
            "INSERT INTO fleet_admins(key_id, public_key, caps, granted_at, granted_by)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(key_id) DO UPDATE SET caps = excluded.caps,"
            " granted_at = excluded.granted_at, granted_by = excluded.granted_by,"
            " revoked_at = NULL",
            (key_id, public_key, json.dumps(cap_list), now, actor),
        )
        self._org_audit("grant_fleet_admin", f"{key_id} caps={','.join(cap_list)}", actor)
        self.conn.commit()
        return {"key_id": key_id, "caps": cap_list, "granted_at": now}

    def revoke_fleet_admin(self, key_id: str, *, actor: str = "local") -> bool:
        """Stop accepting this key immediately (row kept for the audit trail)."""
        cursor = self.conn.execute(
            "UPDATE fleet_admins SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
            (utc_now(), key_id.strip()),
        )
        if cursor.rowcount:
            self._org_audit("revoke_fleet_admin", key_id.strip(), actor)
        self.conn.commit()
        return cursor.rowcount > 0

    def list_fleet_admins(self) -> list[dict[str, object]]:
        rows = self.conn.execute(
            "SELECT key_id, public_key, caps, granted_at, granted_by, revoked_at"
            " FROM fleet_admins ORDER BY granted_at DESC"
        ).fetchall()
        return [dict(r) | {"caps": json.loads(r["caps"])} for r in rows]

    def get_fleet_admin(self, key_id: str) -> dict[str, object] | None:
        """The ACTIVE (non-revoked) grant for a key id, or None."""
        row = self.conn.execute(
            "SELECT key_id, public_key, caps FROM fleet_admins"
            " WHERE key_id = ? AND revoked_at IS NULL",
            (key_id,),
        ).fetchone()
        if row is None:
            return None
        return {"key_id": row["key_id"], "public_key": row["public_key"],
                "caps": json.loads(row["caps"])}

    def audit_fleet_op(self, detail: str, key_id: str) -> None:
        """Record an accepted fleet-admin operation in the org audit trail —
        on the node it happened on, attributed to the signing key."""
        self._org_audit("fleet_op", detail, f"fleet:{key_id}")
        self.conn.commit()

    def consume_fleet_nonce(self, nonce: str, *, prune_older_than_s: int = FLEET_NONCE_RETENTION_SECONDS) -> bool:
        """Atomically claim a signature nonce; False if already seen (replay).

        Nonces only need to outlive the signature-freshness window; anything
        older is pruned opportunistically on each call.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=prune_older_than_s)).isoformat()
        self.conn.execute("DELETE FROM fleet_nonces WHERE seen_at < ?", (cutoff,))
        try:
            self.conn.execute(
                "INSERT INTO fleet_nonces(nonce, seen_at) VALUES (?, ?)",
                (nonce, utc_now()),
            )
        except sqlite3.IntegrityError:
            self.conn.commit()
            return False
        self.conn.commit()
        return True

    def record_peer_sync(self, url: str, result: str) -> None:
        self.conn.execute(
            "UPDATE sync_peers SET last_synced_at = ?, last_result = ? WHERE url = ?",
            (utc_now(), result[:400], url),
        )
        self.conn.commit()

    def semantic_corpus(self) -> list[tuple[int, str, str]]:
        """(rowid, memory_id, embeddable text) for every memory — the auto
        semantic indexer's rebuild source. rowid doubles as the uint64
        external id; full rebuilds make rowid reuse after deletes harmless."""
        rows = self.conn.execute(
            "SELECT rowid AS rid, id, content, summary, tags FROM memories"
        ).fetchall()
        return [
            (int(row["rid"]), row["id"], f"{row['content']}\n{row['summary']}\n{row['tags']}")
            for row in rows
        ]

    def semantic_signature(self) -> tuple:
        """Cheap change signature used to decide when the auto index rebuilds.

        Content updates use a microsecond clock. Aggregate content/summary
        lengths remain a backstop for older/imported rows; reinforcement
        writes never touch either signal and do not trigger a rebuild.
        """
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(MAX(updated_at), ''), "
            "COALESCE(SUM(LENGTH(content)), 0), COALESCE(SUM(LENGTH(COALESCE(summary, ''))), 0) "
            "FROM memories"
        ).fetchone()
        return (int(row[0]), int(row[1]), row[2], int(row[3]), int(row[4]))

    def graph_snapshot(
        self,
        *,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = 300,
    ) -> dict[str, list[dict]]:
        """Return the association graph for visualization, ACL-gated.

        Only nodes visible to the requester are returned, and an edge survives
        only when BOTH endpoints are visible — the same invariant as resonance
        traversal, so the picture never leaks a private neighbor.
        """
        edges = self.conn.execute(
            "SELECT src_id, dst_id, relation, weight FROM memory_links ORDER BY weight DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        ids = list({edge["src_id"] for edge in edges} | {edge["dst_id"] for edge in edges})
        rows = self._visible_rows_for_ids(
            ids,
            owner=None,
            scope=None,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            now=utc_now(),
        )
        visible = {row["id"]: row for row in rows}
        kept_edges = [
            edge for edge in edges
            if edge["src_id"] in visible and edge["dst_id"] in visible
        ]
        degree: DefaultDict[str, int] = defaultdict(int)
        for edge in kept_edges:
            degree[edge["src_id"]] += 1
            degree[edge["dst_id"]] += 1
        return {
            "nodes": [
                {
                    "id": row["id"],
                    "label": row["summary"],
                    "scope": row["scope"],
                    "type": row["type"],
                    "pinned": bool(row["pinned"]),
                    "degree": degree[row["id"]],
                }
                for row in visible.values()
            ],
            "edges": [
                {
                    "src": edge["src_id"],
                    "dst": edge["dst_id"],
                    "relation": edge["relation"],
                    "weight": float(edge["weight"]),
                }
                for edge in kept_edges
            ],
        }

    def recent_snapshot_records(
        self,
        session_id: str,
        *,
        owner: str | None = None,
        limit: int = 2,
    ) -> list[MemoryRecord]:
        """Newest-first context snapshots for a session."""
        where = ["type = 'snapshot'", "json_extract(source, '$.session_id') = ?"]
        params: list[object] = [session_id]
        if owner is not None:
            where.append("owner IN (?, ?)")
            params.extend((owner, LEGACY_CONTEXT_OWNER))
        params.append(max(1, limit))
        rows = self.conn.execute(
            f"""
            SELECT * FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY json_extract(source, '$.snapshot_index') DESC, created_at DESC, rowid DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def next_snapshot_index(self, session_id: str, *, owner: str) -> int:
        """Return the next active snapshot sequence for one owner/session.

        Call while holding a write transaction so concurrent offloads cannot
        claim the same index.
        """
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(CAST(json_extract(source, '$.snapshot_index') AS INTEGER)), -1)
            FROM memories
            WHERE type = 'snapshot' AND owner IN (?, ?)
              AND json_extract(source, '$.session_id') = ?
            """,
            (owner, LEGACY_CONTEXT_OWNER, session_id),
        ).fetchone()
        return int(row[0]) + 1

    def latest_snapshot_record(
        self,
        session_id: str,
        *,
        owner: str | None = None,
    ) -> MemoryRecord | None:
        """Return the most recent context snapshot for a session.

        Recency is determined by snapshot metadata and insertion order, never
        by FTS relevance — same-second snapshots must still resolve to the
        latest one deterministically.
        """
        where = ["type = 'snapshot'", "json_extract(source, '$.session_id') = ?"]
        params: list[object] = [session_id]
        if owner is not None:
            where.append("owner IN (?, ?)")
            params.extend((owner, LEGACY_CONTEXT_OWNER))
        row = self.conn.execute(
            f"""
            SELECT * FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY json_extract(source, '$.snapshot_index') DESC, created_at DESC, rowid DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return self._row_to_record(row) if row else None

    def link_exists(self, memory_id: str, other_id: str) -> bool:
        """Return whether any edge connects the two memories in either direction."""
        row = self.conn.execute(
            "SELECT 1 FROM memory_links WHERE (src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?) LIMIT 1",
            (memory_id, other_id, other_id, memory_id),
        ).fetchone()
        return row is not None

    def links_for(self, memory_id: str) -> list[MemoryLink]:
        rows = self.conn.execute(
            "SELECT * FROM memory_links WHERE src_id = ? OR dst_id = ? ORDER BY weight DESC, src_id, dst_id",
            (memory_id, memory_id),
        ).fetchall()
        return [self._row_to_link(row) for row in rows]

    def record_recall(
        self,
        memory_ids: Sequence[str],
        *,
        create_colinks: bool = False,
        helpful: bool = True,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        owner: str | None = None,
    ) -> dict[str, int]:
        """Reinforce or weaken memories recalled together.

        Co-recall is the Hebbian signal of associative memory: memories that are
        useful together should surface together next time. With `helpful=True`
        each call bumps per-memory reinforcement metadata and strengthens
        existing links between every co-recalled pair; `create_colinks=True`
        additionally creates weak `co_recalled` edges for pairs with no existing
        link. With `helpful=False` the recall misled the agent: link weights are
        reduced and memory confidence drops slightly — the self-correction path.
        Negative feedback deliberately leaves `updated_at` alone: touching it
        would reset the freshness-decay clock and boost the memory instead.

        `supersedes` edges carry truth-arbitration direction, not association
        strength, so recall feedback never adjusts them.

        When a requester is given, only memories that requester can see are
        affected — feedback from untrusted surfaces (HTTP/MCP) cannot touch
        another agent's private memories.
        """
        ids = self._recall_eligible_ids(
            memory_ids,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            owner=owner,
        )
        now = utc_now()
        reinforced_links = 0
        created_links = 0
        weakened_links = 0
        for memory_id in ids:
            if helpful:
                self.conn.execute(
                    "UPDATE memories SET access_count = access_count + 1, "
                    "helpful_count = helpful_count + 1, last_accessed_at = ? WHERE id = ?",
                    (now, memory_id),
                )
            else:
                self.conn.execute(
                    "UPDATE memories SET confidence = max(0.0, confidence - ?), "
                    "unhelpful_count = unhelpful_count + 1 WHERE id = ?",
                    (NEGATIVE_FEEDBACK_CONFIDENCE_STEP, memory_id),
                )
        pair_clause = (
            "((src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?)) AND relation != 'supersedes'"
        )
        for i, src_id in enumerate(ids):
            for dst_id in ids[i + 1:]:
                pair_params = (src_id, dst_id, dst_id, src_id)
                if helpful:
                    cur = self.conn.execute(
                        f"""
                        UPDATE memory_links
                        SET weight = min(1.0, weight + ?),
                            activation_count = activation_count + 1,
                            last_activated_at = ?,
                            updated_at = ?
                        WHERE {pair_clause}
                        """,
                        (CO_RECALL_WEIGHT_STEP, now, now, *pair_params),
                    )
                    if cur.rowcount > 0:
                        reinforced_links += cur.rowcount
                    elif create_colinks and not self._pair_has_supersedes(src_id, dst_id):
                        # A rowcount of 0 can also mean the only edge is a
                        # supersedes (excluded above): never lay a co_recalled
                        # edge over a contradiction, which would re-associate a
                        # superseded memory and double-count it in resonance.
                        self.add_link(
                            MemoryLink(
                                src_id=src_id,
                                dst_id=dst_id,
                                relation="co_recalled",
                                weight=CO_RECALL_INITIAL_WEIGHT,
                                last_activated_at=now,
                                activation_count=1,
                            )
                        )
                        created_links += 1
                else:
                    cur = self.conn.execute(
                        f"""
                        UPDATE memory_links
                        SET weight = max(0.0, weight - ?), updated_at = ?
                        WHERE {pair_clause}
                        """,
                        (CO_RECALL_WEAKEN_STEP, now, *pair_params),
                    )
                    weakened_links += cur.rowcount
        self.conn.commit()
        return {
            "reinforced_memories": len(ids) if helpful else 0,
            "weakened_memories": 0 if helpful else len(ids),
            "reinforced_links": reinforced_links,
            "created_links": created_links,
            "weakened_links": weakened_links,
        }

    def _pair_has_supersedes(self, a: str, b: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM memory_links WHERE relation = 'supersedes' AND "
            "((src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?)) LIMIT 1",
            (a, b, b, a),
        ).fetchone()
        return row is not None

    def _recall_eligible_ids(
        self,
        memory_ids: Sequence[str],
        *,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        owner: str | None,
    ) -> list[str]:
        ordered = [memory_id for memory_id in dict.fromkeys(memory_ids) if memory_id]
        if not ordered:
            return []
        placeholders = ",".join("?" for _ in ordered)
        where = [f"id IN ({placeholders})"]
        params: list[object] = [*ordered]
        if owner is not None:
            where.append("owner = ?")
            params.append(owner)
        self._append_acl_filter(
            where,
            params,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            alias="",
        )
        visible = {
            row["id"]
            for row in self.conn.execute(
                f"SELECT id FROM memories WHERE {' AND '.join(where)}", params
            ).fetchall()
        }
        return [memory_id for memory_id in ordered if memory_id in visible]

    _MEMORY_COLUMNS = (
        "id, owner, scope, type, content, summary, tags, visibility, source, "
        "confidence, importance, created_at, updated_at, expires_at, "
        "decay_policy, decay_half_life_days, last_accessed_at, access_count, pinned, "
        "helpful_count, unhelpful_count"
    )

    def tune_decay_from_feedback(self) -> int:
        """Recompute decay half-lives from recall-feedback telemetry.

        Idempotent: half_life = type_base * sqrt((1+helpful)/(1+unhelpful)),
        clamped to [0.5x, 4x] of the base and [7, 730] days absolute. Memories
        proven helpful forget slower; memories that misled forget faster.
        """
        rows = self.conn.execute(
            "SELECT id, type, decay_half_life_days, decay_base_half_life_days, "
            "helpful_count, unhelpful_count "
            "FROM memories WHERE decay_policy != 'none' AND (helpful_count > 0 OR unhelpful_count > 0)"
        ).fetchall()
        tuned = 0
        for row in rows:
            # Scale the memory's CONFIGURED base (default-for-type unless the
            # user set an explicit half-life), never the bare type default —
            # otherwise a custom half-life is clobbered on every retention pass.
            base = row["decay_base_half_life_days"]
            if base is None:
                base = DEFAULT_DECAY_HALF_LIFE_DAYS.get(row["type"], DEFAULT_DECAY_HALF_LIFE_FALLBACK_DAYS)
            multiplier = math.sqrt((1 + row["helpful_count"]) / (1 + row["unhelpful_count"]))
            multiplier = min(
                DECAY_FEEDBACK_MAX_MULTIPLIER,
                max(DECAY_FEEDBACK_MIN_MULTIPLIER, multiplier),
            )
            new_half_life = round(
                min(
                    DECAY_FEEDBACK_MAX_HALF_LIFE_DAYS,
                    max(DECAY_FEEDBACK_MIN_HALF_LIFE_DAYS, base * multiplier),
                ),
                DECAY_FEEDBACK_ROUND_DIGITS,
            )
            if (
                abs(new_half_life - float(row["decay_half_life_days"]))
                >= DECAY_FEEDBACK_UPDATE_THRESHOLD_DAYS
            ):
                self.conn.execute(
                    "UPDATE memories SET decay_half_life_days = ? WHERE id = ?",
                    (new_half_life, row["id"]),
                )
                tuned += 1
        self.conn.commit()
        return tuned

    def share_memory(
        self,
        memory_id: str,
        *,
        actor: str,
        to_agent: str | None = None,
        to_team: str | None = None,
        to_project: str | None = None,
        deidentify: bool = False,
    ) -> dict[str, object]:
        """Owner-controlled memory sharing with an audit trail.

        Only the memory's owner may grant access — that IS the negotiation
        boundary. A plain share appends a visibility grant; a de-identified
        share creates a copy with the owner's name scrubbed and provenance
        kept only in the audit log (visible via the original memory).
        """
        record = self.get(memory_id)
        if record is None:
            raise KeyError(memory_id)
        if record.owner != actor:
            raise PermissionError(f"only owner {record.owner!r} may share this memory")
        grant = self._share_grant(to_agent, to_team, to_project)

        if deidentify:
            scrubbed = record.content.replace(record.owner, "a teammate")
            copy = MemoryRecord(
                content=scrubbed,
                owner=to_team or to_project or "shared",
                scope="project" if to_project else "team" if to_team else record.scope,
                # Tags are dropped: they can carry owner-identifying labels that
                # would defeat de-identification for the recipient.
                tags=[],
                visibility=[grant],
                type=record.type,
                source={"shared": "deidentified"},
                confidence=record.confidence,
                importance=record.importance,
            )
            self.add(copy)
            # Provenance stays on the ORIGINAL memory's audit (owner-visible).
            # The copy's audit — which the recipient CAN read — must not name
            # the owner, or de-identification is defeated.
            self._audit(memory_id, actor, "share_deidentified", f"{grant} as {copy.id}")
            self._audit(copy.id, copy.owner, "created_from_share", "deidentified copy")
            return {"shared_as": copy.id, "grant": grant, "deidentified": True}

        if grant not in record.visibility:
            visibility = list(record.visibility) + [grant]
            self._set_visibility(memory_id, visibility)
        self._audit(memory_id, actor, "share", grant)
        return {"shared_as": memory_id, "grant": grant, "deidentified": False}

    def revoke_share(
        self,
        memory_id: str,
        *,
        actor: str,
        to_agent: str | None = None,
        to_team: str | None = None,
        to_project: str | None = None,
    ) -> dict[str, object]:
        record = self.get(memory_id)
        if record is None:
            raise KeyError(memory_id)
        if record.owner != actor:
            raise PermissionError(f"only owner {record.owner!r} may revoke access")
        grant = self._share_grant(to_agent, to_team, to_project)
        visibility = [entry for entry in record.visibility if entry != grant]
        if len(visibility) != len(record.visibility):
            self._set_visibility(memory_id, visibility)
        self._audit(memory_id, actor, "revoke", grant)
        return {"memory_id": memory_id, "revoked": grant}

    @staticmethod
    def _share_grant(to_agent: str | None, to_team: str | None, to_project: str | None) -> str:
        chosen = [("agent", to_agent), ("team", to_team), ("project", to_project)]
        chosen = [(kind, val) for kind, val in chosen if val]
        if len(chosen) != 1:
            raise ValueError("specify exactly one of to_agent / to_team / to_project")
        kind, val = chosen[0]
        return f"{kind}:{val}"

    def _set_visibility(self, memory_id: str, visibility: list[str]) -> None:
        """Change only the ACL of a memory, leaving updated_at untouched.

        Sharing/revoking is not a content edit, so it must not restart the
        freshness or decay-archival clock (same discipline as record_recall).
        It DOES bump acl_updated_at — the independent ACL clock sync uses to
        propagate the grant change to peers (so a revoke actually retracts
        already-synced access instead of staying local).
        """
        self.conn.execute(
            "UPDATE memories SET visibility = ?, acl_updated_at = ? WHERE id = ?",
            (json.dumps(visibility, ensure_ascii=False), utc_now_micro(), memory_id),
        )
        self.conn.commit()

    def audit_log(self, memory_id: str) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT actor, action, detail, at FROM memory_audit WHERE memory_id = ? ORDER BY id",
            (memory_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _audit(self, memory_id: str, actor: str, action: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO memory_audit(memory_id, actor, action, detail, at) VALUES (?, ?, ?, ?, ?)",
            (memory_id, actor, action, detail, utc_now()),
        )
        self.conn.commit()

    def run_retention(
        self, *, decayed_half_lives: float | None = RETENTION_MIN_HALF_LIVES
    ) -> dict[str, int]:
        """Move expired (and optionally deeply-decayed) memories to cold archive.

        Archived memories leave active recall entirely but stay restorable.
        Pinned and authority-track records are never archived by decay; only a
        hard expiry can retire them.
        """
        now = utc_now()
        tuned = self.tune_decay_from_feedback()
        expired = self._archive_where(
            "expires_at IS NOT NULL AND julianday(expires_at) <= julianday(?)",
            [now],
            reason="expired",
        )
        rotated = self.rotate_snapshots()
        decayed = 0
        if decayed_half_lives and decayed_half_lives > 0:
            decayed = self._archive_where(
                """
                pinned = 0 AND decay_policy != 'none'
                AND NOT (COALESCE(json_extract(source, '$.permanence'), 0) = 1
                         AND COALESCE(json_extract(source, '$.weight'), 0) >= 10)
                AND (julianday(?) - julianday(COALESCE(last_accessed_at, updated_at)))
                    > (? * decay_half_life_days)
                """,
                [now, float(decayed_half_lives)],
                reason="decayed",
            )
        return {
            "archived_expired": expired,
            "archived_decayed": decayed,
            "archived_snapshots": rotated,
            "tuned_half_lives": tuned,
        }

    def _archive_where(self, where_sql: str, params: list[object], *, reason: str) -> int:
        rows = self.conn.execute(
            f"SELECT id FROM memories WHERE {where_sql}", params
        ).fetchall()
        ids = [row["id"] for row in rows]
        if not ids:
            return 0
        now = utc_now()
        placeholders = ",".join("?" for _ in ids)
        self.conn.execute(
            f"""
            INSERT OR REPLACE INTO memories_archive
              ({self._MEMORY_COLUMNS}, archived_at, archive_reason)
            SELECT {self._MEMORY_COLUMNS}, ?, ? FROM memories WHERE id IN ({placeholders})
            """,
            [now, reason, *ids],
        )
        # Preserve the edges so restore is lossless: copy every link touching
        # an archived memory into the link archive, then delete from live.
        self.conn.execute(
            f"""
            INSERT OR REPLACE INTO memory_links_archive
              (src_id, dst_id, relation, weight, created_at, updated_at,
               last_activated_at, activation_count, source, archived_at)
            SELECT src_id, dst_id, relation, weight, created_at, updated_at,
                   last_activated_at, activation_count, source, ?
            FROM memory_links
            WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})
            """,
            [now, *ids, *ids],
        )
        self.conn.execute(
            f"DELETE FROM memory_links WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})",
            [*ids, *ids],
        )
        self.conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
        self.conn.commit()
        return len(ids)

    def list_archived(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, object]]:
        rows = self.conn.execute(
            "SELECT * FROM memories_archive ORDER BY archived_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (max(1, limit), max(0, offset)),
        ).fetchall()
        return [
            {
                "id": row["id"], "owner": row["owner"], "scope": row["scope"],
                "type": row["type"], "content": row["content"], "summary": row["summary"],
                "archived_at": row["archived_at"], "archive_reason": row["archive_reason"],
            }
            for row in rows
        ]

    def restore_archived(self, memory_id: str) -> MemoryRecord:
        """Bring an archived memory back into active recall.

        The original expiry is cleared — restoring an expired memory that
        stays expired would be a no-op — and updated_at restarts the decay
        clock, because a human decision to restore IS a relevance signal.
        """
        row = self.conn.execute(
            "SELECT * FROM memories_archive WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        now = utc_now()
        self.conn.execute(
            f"""
            INSERT INTO memories({self._MEMORY_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["owner"], row["scope"], row["type"], row["content"],
                row["summary"], row["tags"], row["visibility"], row["source"],
                row["confidence"], row["importance"], row["created_at"], now,
                None, row["decay_policy"], row["decay_half_life_days"],
                row["last_accessed_at"], row["access_count"], row["pinned"],
                row["helpful_count"], row["unhelpful_count"],
            ),
        )
        # The archive table predates the ACL clock and doesn't carry
        # acl_updated_at, so seed it to created_at (a stable floor) rather than
        # leaving it NULL: NULL would COALESCE to updated_at=now and could clobber
        # a peer's more-recent revoke on the next sync. created_at defers to any
        # peer ACL decision made after creation, which is the safe direction.
        self.conn.execute(
            "UPDATE memories SET acl_updated_at = ? WHERE id = ?",
            (row["created_at"], memory_id),
        )
        self.conn.execute("DELETE FROM memories_archive WHERE id = ?", (memory_id,))
        # Re-attach archived edges whose OTHER endpoint is live again. Edges to
        # a still-archived memory stay parked until that one is restored too.
        self.conn.execute(
            """
            INSERT OR IGNORE INTO memory_links
              (src_id, dst_id, relation, weight, created_at, updated_at,
               last_activated_at, activation_count, source)
            SELECT la.src_id, la.dst_id, la.relation, la.weight, la.created_at,
                   la.updated_at, la.last_activated_at, la.activation_count, la.source
            FROM memory_links_archive la
            WHERE (la.src_id = ? OR la.dst_id = ?)
              AND EXISTS (SELECT 1 FROM memories WHERE id = la.src_id)
              AND EXISTS (SELECT 1 FROM memories WHERE id = la.dst_id)
            """,
            (memory_id, memory_id),
        )
        self.conn.execute(
            """
            DELETE FROM memory_links_archive
            WHERE (src_id = ? OR dst_id = ?)
              AND EXISTS (SELECT 1 FROM memories WHERE id = memory_links_archive.src_id)
              AND EXISTS (SELECT 1 FROM memories WHERE id = memory_links_archive.dst_id)
            """,
            (memory_id, memory_id),
        )
        self.conn.commit()
        return self.get(memory_id)

    def purge_owner(self, owner: str) -> dict[str, int]:
        """Delete every memory owned by `owner`, plus all links touching them.

        This is the right-to-forget / agent-retirement operation. It is
        deliberately owner-exact (no wildcard) and returns counts so callers
        can surface what was destroyed.
        """
        if not owner or not owner.strip():
            raise ValueError("owner must be non-empty")
        owner = owner.strip()
        # Right-to-forget must also reach the cold archive and the id-keyed
        # side tables, otherwise purged content survives in memories_archive
        # (restorable via restore_archived) and in the recall/audit logs.
        owned_ids = [
            row[0]
            for row in self.conn.execute(
                "SELECT id FROM memories WHERE owner = ? "
                "UNION SELECT id FROM memories_archive WHERE owner = ?",
                (owner, owner),
            ).fetchall()
        ]
        links_removed = self.conn.execute(
            """
            DELETE FROM memory_links
            WHERE src_id IN (SELECT id FROM memories WHERE owner = ?)
               OR dst_id IN (SELECT id FROM memories WHERE owner = ?)
            """,
            (owner, owner),
        ).rowcount
        memories_removed = self.conn.execute(
            "DELETE FROM memories WHERE owner = ?", (owner,)
        ).rowcount
        archived_removed = self.conn.execute(
            "DELETE FROM memories_archive WHERE owner = ?", (owner,)
        ).rowcount
        delivery_rows_removed = self.conn.execute(
            "DELETE FROM session_recall_log WHERE owner = ?", (owner,)
        ).rowcount
        if owned_ids:
            placeholders = ", ".join("?" for _ in owned_ids)
            self.conn.execute(
                f"DELETE FROM session_recall_log WHERE memory_id IN ({placeholders})",
                owned_ids,
            )
            self.conn.execute(
                f"DELETE FROM memory_audit WHERE memory_id IN ({placeholders})",
                owned_ids,
            )
            self.conn.execute(
                f"DELETE FROM memory_links_archive WHERE src_id IN ({placeholders}) "
                f"OR dst_id IN ({placeholders})",
                owned_ids + owned_ids,
            )
        self.conn.execute("DELETE FROM recall_profiles WHERE agent_id = ?", (owner,))
        for mem_id in owned_ids:
            self._record_tombstone(mem_id, commit=False)
        self.conn.commit()
        return {
            "memories_deleted": int(memories_removed),
            "links_deleted": int(links_removed),
            "archived_deleted": int(archived_removed),
            "delivery_rows_deleted": int(delivery_rows_removed),
        }

    def rebuild_indexes(self) -> dict[str, int]:
        """Rebuild disposable retrieval indexes from authoritative memories."""
        self.conn.executescript(
            """
            DROP TRIGGER IF EXISTS memories_ai;
            DROP TRIGGER IF EXISTS memories_ad;
            DROP TRIGGER IF EXISTS memories_au;
            DROP TABLE IF EXISTS memories_fts;
            """
        )
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            """
            INSERT INTO memories_fts(id, owner, scope, type, content, summary, tags)
            SELECT id, owner, scope, type, content, summary, tags FROM memories
            """
        )
        self.conn.commit()
        indexed = self.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        total = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return {"memories_indexed": int(indexed), "memories_total": int(total)}

    # ---------- ops / maintenance ----------

    def find_orphan_memories(self, *, limit: int = 1000) -> list[dict[str, object]]:
        """Memories that are shared but reachable by no one — dead data.

        A memory is an orphan only when EVERY path to it is gone:
        - its owner is not a currently-registered agent (the ACL always grants
          the owner access, so a live owner alone keeps a memory reachable), AND
        - it has no `global` grant and no `agent:<id>` grant to a live agent, AND
        - every `team:<id>`/`project:<id>` grant points at a scope that no longer
          EXISTS (was deleted).

        Existence — not membership count — is the gate: an empty-but-existing
        team can gain a member again, so its memories are recoverable and must
        NOT be classed as orphans (else `delete_orphan_memories` would destroy
        live, re-shareable data).
        """
        live_agents = {r[0] for r in self.conn.execute("SELECT id FROM agents")}
        existing_teams = {r[0] for r in self.conn.execute("SELECT id FROM teams")}
        existing_projects = {r[0] for r in self.conn.execute("SELECT id FROM projects")}
        orphans: list[dict[str, object]] = []
        rows = self.conn.execute(
            "SELECT id, content, visibility, owner FROM memories WHERE json_array_length(visibility) > 0"
        ).fetchall()
        for row in rows:
            if row["owner"] in live_agents:
                continue  # owner can always read it — never an orphan
            grants = json.loads(row["visibility"] or "[]")
            has_scoped = False
            reachable = False
            for g in grants:
                if g == "global":
                    reachable = True
                    break
                if g.startswith("agent:"):
                    if g[len("agent:"):] in live_agents:
                        reachable = True
                        break
                elif g.startswith("team:"):
                    has_scoped = True
                    if g[len("team:"):] in existing_teams:
                        reachable = True
                        break
                elif g.startswith("project:"):
                    has_scoped = True
                    if g[len("project:"):] in existing_projects:
                        reachable = True
                        break
            if has_scoped and not reachable:
                orphans.append({"id": row["id"], "owner": row["owner"],
                                "content": (row["content"] or "")[:100], "visibility": grants})
                if len(orphans) >= limit:
                    break
        return orphans

    def orphan_count(self) -> int:
        return len(self.find_orphan_memories(limit=10 ** 9))

    def delete_orphan_memories(self) -> dict[str, int]:
        """Delete every orphan memory (tombstoned, so the deletion syncs)."""
        ids = [o["id"] for o in self.find_orphan_memories(limit=10 ** 9)]
        for mem_id in ids:
            self.delete(mem_id)
        return {"orphans_deleted": len(ids)}

    def vacuum(self) -> dict[str, object]:
        """Reclaim space and refresh planner stats (ops maintenance)."""
        before = self.path.stat().st_size if self.path.exists() else 0
        self.conn.execute("PRAGMA optimize")
        self.conn.execute("ANALYZE")
        # VACUUM cannot run inside a transaction.
        self.conn.isolation_level = None
        try:
            self.conn.execute("VACUUM")
        finally:
            self.conn.isolation_level = ""
        after = self.path.stat().st_size if self.path.exists() else 0
        return {"bytes_before": int(before), "bytes_after": int(after),
                "bytes_reclaimed": int(max(0, before - after))}

    def usage_summary(self, *, top: int = 20) -> dict[str, object]:
        """Approximate token footprint of stored memory, grouped for the
        dashboard's four cards: total, per agent (owner), per team, per project.

        Tokens are the dependency-free approx_tokens() estimate of each memory's
        content. A memory counts toward a team/project if its visibility grants
        that scope (either `team:<id>`/`project:<id>` or the bare `team` scheme
        keyed by source.team_id). One memory can count toward several scopes.
        """
        from collections import defaultdict

        from .context_pack import approx_tokens

        rows = self.conn.execute(
            "SELECT owner, content, visibility, source FROM memories"
        ).fetchall()
        total_tokens = 0
        total_mem = 0
        by_agent: dict[str, list[int]] = defaultdict(lambda: [0, 0])   # tokens, memories
        by_team: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        by_project: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in rows:
            tok = approx_tokens(row["content"] or "")
            total_tokens += tok
            total_mem += 1
            if row["owner"]:
                by_agent[row["owner"]][0] += tok
                by_agent[row["owner"]][1] += 1
            try:
                grants = json.loads(row["visibility"] or "[]")
            except (ValueError, TypeError):
                grants = []
            teams, projects = set(), set()
            for g in grants:
                if isinstance(g, str) and g.startswith("team:"):
                    teams.add(g[len("team:"):])
                elif isinstance(g, str) and g.startswith("project:"):
                    projects.add(g[len("project:"):])
            if "team" in grants:  # bare scheme keyed by source.team_id
                try:
                    tid = (json.loads(row["source"]) or {}).get("team_id")
                    if tid:
                        teams.add(str(tid))
                except (ValueError, TypeError):
                    pass
            for t in teams:
                by_team[t][0] += tok; by_team[t][1] += 1
            for p in projects:
                by_project[p][0] += tok; by_project[p][1] += 1

        def _rank(d):
            items = [{"id": k, "tokens": v[0], "memories": v[1]} for k, v in d.items()]
            items.sort(key=lambda x: x["tokens"], reverse=True)
            return items[:top]

        return {
            "total": {"tokens": total_tokens, "memories": total_mem,
                      "agents": len(by_agent), "teams": len(by_team),
                      "projects": len(by_project)},
            "by_agent": _rank(by_agent),
            "by_team": _rank(by_team),
            "by_project": _rank(by_project),
        }

    def maintenance_scan(self) -> dict[str, object]:
        """A read-only health snapshot for the ops maintenance view."""
        integrity = self.integrity_check() if hasattr(self, "integrity_check") else {"ok": True}
        return {
            "orphan_memories": self.orphan_count(),
            "memories": self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
            "indexed": self.conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0],
            "archived": self.conn.execute("SELECT COUNT(*) FROM memories_archive").fetchone()[0],
            "teams": self.conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
            "projects": self.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "schema_ok": bool(integrity.get("ok", True)),
        }

    def search(
        self,
        query: str,
        *,
        owner: str | None = None,
        scope: str | None = None,
        requester_agent_id: str | None = None,
        requester_team_id: str | None = None,
        limit: int = 10,
        profile: RecallProfile | None = None,
    ) -> list[SearchResult]:
        """Search memories via dual-track retrieval plus resonance expansion.

        Track A is query-bound FTS5 relevance. Track B is query-independent
        authority recall for bedrock memories. Track C expands direct hits
        through authoritative `memory_links` edges (associative recall). All
        tracks pass the same expiry and requester ACL hard gates before
        scoring/fusion; an optional RecallProfile then applies per-agent soft
        re-weighting to ranking only.
        """
        now = utc_now()
        now_dt = datetime.now(timezone.utc)
        rows = self._fts_rows(
            query,
            owner=owner,
            scope=scope,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            limit=limit,
            now=now,
        )
        results: dict[str, SearchResult] = {}
        raw_text_scores: dict[str, float] = {}
        for row in rows:
            # bm25() returns more-negative values for stronger matches; map to
            # (0.5, 1.0) so relevance rises with match strength instead of
            # inverting it.
            rank = min(float(row["rank"]), 0.0)
            text_score = (1.0 - rank) / (2.0 - rank)
            raw_text_scores[row["id"]] = text_score
            result = self._score_row(row, text_score=text_score, now_dt=now_dt, reason_prefix="fts")
            results[result.record.id] = result

        # Bedrock recall is query-independent: cap its share of the result
        # window so always-on constants cannot crowd out genuinely relevant
        # lexical/semantic/resonance hits (validation finding, v0.9.x).
        authority_rows = self._authority_rows(
            owner=owner,
            scope=scope,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            limit=max(1, limit // 4),
            now=now,
        )
        for row in authority_rows:
            source = json.loads(row["source"] or "{}")
            authority_weight = min(max(float(source.get("weight", 10.0)), 0.0), 10.0) / 10.0
            # Fuse the RAW lexical relevance (not the already-metadata-weighted
            # FTS score) with authority weight, so _score_row applies
            # importance/confidence/freshness exactly once.
            text_component = raw_text_scores.get(row["id"], 0.0)
            fused_score = (text_component * 0.3) + (authority_weight * 0.7)
            result = self._score_row(
                row,
                text_score=fused_score,
                now_dt=now_dt,
                reason_prefix="authority_track",
            )
            previous = results.get(result.record.id)
            if previous is None or result.score > previous.score:
                results[result.record.id] = result

        semantic_rows = self._semantic_candidate_rows(
            query,
            owner=owner,
            scope=scope,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            limit=limit,
            now=now,
        )
        for row, candidate in semantic_rows:
            result = self._score_row(
                row,
                text_score=max(0.0, float(candidate.score)),
                now_dt=now_dt,
                reason_prefix=self._semantic_reason_prefix(candidate),
            )
            previous = results.get(result.record.id)
            if previous is None or result.score > previous.score:
                results[result.record.id] = result

        if self.resonance_hops > 0 and results:
            resonance_results = self._resonance_results(
                seed_scores={memory_id: result.score for memory_id, result in results.items()},
                owner=owner,
                scope=scope,
                requester_agent_id=requester_agent_id,
                requester_team_id=requester_team_id,
                now=now,
                now_dt=now_dt,
            )
            for result in resonance_results:
                previous = results.get(result.record.id)
                if previous is None or result.score > previous.score:
                    results[result.record.id] = result

        self._apply_supersedes_demotion(results)

        final_results = sorted(results.values(), key=lambda result: result.score, reverse=True)
        if not final_results:
            final_results = self._fallback_candidates(
                owner=owner,
                scope=scope,
                requester_agent_id=requester_agent_id,
                requester_team_id=requester_team_id,
                limit=limit,
            )
        if profile is not None:
            for result in final_results:
                weight = profile.weight_for(result.record)
                result.score *= weight
                result.reason = f"{result.reason}+profile:{weight:.2f}"
            final_results.sort(key=lambda result: result.score, reverse=True)
        return final_results[:limit]

    def consolidate(self, *, owner: str | None = None, scope: str | None = None) -> dict[str, int]:
        """Write-side hygiene pass: merge exact duplicates, synthesize concepts.

        Both steps only operate within groups sharing identical owner, scope,
        and visibility, so consolidation can never move content across ACL
        boundaries or blend private and public memories into one record.
        """
        duplicates_merged = self._merge_exact_duplicates(owner=owner, scope=scope)
        concepts_created = self._synthesize_corecall_clusters(owner=owner, scope=scope)
        return {"duplicates_merged": duplicates_merged, "concepts_created": concepts_created}

    def _merge_exact_duplicates(self, *, owner: str | None, scope: str | None) -> int:
        where = ["1=1"]
        params: list[object] = []
        if owner:
            where.append("owner = ?")
            params.append(owner)
        if scope:
            where.append("scope = ?")
            params.append(scope)
        rows = self.conn.execute(
            f"SELECT * FROM memories WHERE {' AND '.join(where)}", params
        ).fetchall()
        groups: DefaultDict[tuple, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            key = (row["owner"], row["scope"], row["visibility"], _content_fingerprint(row["content"]))
            groups[key].append(row)

        merged = 0
        for group in groups.values():
            if len(group) < 2:
                continue
            # Pinned and authority-track records must never lose to a casual
            # duplicate, regardless of confidence.
            group.sort(key=_merge_priority, reverse=True)
            canonical = group[0]
            for duplicate in group[1:]:
                for link in self.links_for(duplicate["id"]):
                    other_end = link.dst_id if link.src_id == duplicate["id"] else link.src_id
                    if other_end == canonical["id"]:
                        continue
                    if self.link_exists(canonical["id"], other_end):
                        continue
                    src, dst = (
                        (canonical["id"], other_end)
                        if link.src_id == duplicate["id"]
                        else (other_end, canonical["id"])
                    )
                    self.add_link(
                        MemoryLink(
                            src_id=src, dst_id=dst, relation=link.relation,
                            weight=link.weight, last_activated_at=link.last_activated_at,
                            activation_count=link.activation_count, source=link.source,
                        )
                    )
                self.conn.execute(
                    "UPDATE memories SET access_count = access_count + ? WHERE id = ?",
                    (int(duplicate["access_count"] or 0), canonical["id"]),
                )
                self.delete(duplicate["id"])
                merged += 1
        self.conn.commit()
        return merged

    def _synthesize_corecall_clusters(self, *, owner: str | None, scope: str | None) -> int:
        """Turn strongly co-recalled clusters into concept nodes.

        Concept nodes are the cheap recall handle: everyday retrieval hits the
        synthesized summary, and `derived_from` edges lead back to the original
        episodes when detail is needed.
        """
        edges = self.conn.execute(
            """
            SELECT l.src_id, l.dst_id FROM memory_links l
            JOIN memories s ON s.id = l.src_id
            JOIN memories d ON d.id = l.dst_id
            WHERE l.relation = 'co_recalled' AND l.weight >= ? AND l.activation_count >= ?
              AND s.owner = d.owner AND s.scope = d.scope AND s.visibility = d.visibility
            """,
            (CONSOLIDATION_MIN_CLUSTER_WEIGHT, CONSOLIDATION_MIN_ACTIVATIONS),
        ).fetchall()

        parent: dict[str, str] = {}

        def find(node: str) -> str:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for edge in edges:
            root_a, root_b = find(edge["src_id"]), find(edge["dst_id"])
            if root_a != root_b:
                parent[root_b] = root_a

        clusters: DefaultDict[str, list[str]] = defaultdict(list)
        for node in parent:
            clusters[find(node)].append(node)

        created = 0
        for members in clusters.values():
            if len(members) < CONSOLIDATION_MIN_CLUSTER_SIZE:
                continue
            placeholders = ",".join("?" for _ in members)
            rows = self.conn.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders}) ORDER BY updated_at",
                members,
            ).fetchall()
            if len(rows) < CONSOLIDATION_MIN_CLUSTER_SIZE:
                continue
            first = rows[0]
            if owner and first["owner"] != owner:
                continue
            if scope and first["scope"] != scope:
                continue
            already = self.conn.execute(
                f"SELECT 1 FROM memory_links WHERE relation = 'derived_from' AND dst_id IN ({placeholders}) LIMIT 1",
                members,
            ).fetchone()
            if already:
                continue
            summaries = "; ".join(row["summary"] for row in rows)
            concept = MemoryRecord(
                content=f"Consolidated insight from {len(rows)} related memories: {summaries}",
                owner=first["owner"],
                scope=first["scope"],
                type="note",
                visibility=json.loads(first["visibility"] or "[]"),
                importance=max(float(row["importance"]) for row in rows),
                confidence=sum(float(row["confidence"]) for row in rows) / len(rows),
                source={"auto": "consolidation", "consolidated_from": [row["id"] for row in rows]},
            )
            self.add(concept)
            for row in rows:
                self.add_link(
                    MemoryLink(
                        src_id=concept.id, dst_id=row["id"], relation="derived_from",
                        weight=0.9, source={"auto": "consolidation"},
                    )
                )
            created += 1
        self.conn.commit()
        return created

    def _apply_supersedes_demotion(self, results: dict[str, SearchResult]) -> None:
        """Demote memories whose superseding record is also in the result set.

        `supersedes` is the one directional relation: when both ends survive the
        hard gates, the superseded end is stale by definition and must not
        outrank its replacement. Demotion only fires when the requester can see
        the superseding memory, so edge direction never leaks hidden records.
        """
        if len(results) < 2:
            return
        ids = list(results)
        placeholders = ",".join("?" for _ in ids)
        edges = self.conn.execute(
            f"""
            SELECT src_id, dst_id FROM memory_links
            WHERE relation = 'supersedes'
              AND src_id IN ({placeholders}) AND dst_id IN ({placeholders})
            """,
            [*ids, *ids],
        ).fetchall()
        for edge in edges:
            superseded = results.get(edge["dst_id"])
            if superseded is None or edge["src_id"] not in results:
                continue
            superseded.score *= SUPERSEDED_SCORE_PENALTY
            superseded.reason = f"{superseded.reason}+superseded_by:{edge['src_id']}"

    def _resonance_results(
        self,
        *,
        seed_scores: dict[str, float],
        owner: str | None,
        scope: str | None,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        now: str,
        now_dt: datetime,
    ) -> list[SearchResult]:
        """Expand seed hits through memory_links with ACL-safe traversal.

        Requester-invisible or expired nodes are dropped before they enter the
        frontier, so they are both unreturnable and untraversable: a private
        memory can never bridge two public memories for an unauthorized
        requester, and edge existence never leaks through scores.

        Edges themselves decay: an association that has not been co-activated
        recently contributes less activation than a well-worn one, and each
        frontier node only expands its strongest RESONANCE_MAX_EDGES_PER_NODE
        edges so hub memories cannot flood the cluster.
        """
        visited: set[str] = set(seed_scores)
        frontier: dict[str, float] = dict(seed_scores)
        collected: list[tuple[sqlite3.Row, float, int, str]] = []

        for hop in range(1, self.resonance_hops + 1):
            if not frontier or len(collected) >= MAX_RESONANCE_CANDIDATES:
                break
            frontier_ids = list(frontier)
            placeholders = ",".join("?" for _ in frontier_ids)
            edges = self.conn.execute(
                f"""
                SELECT src_id AS from_id, dst_id AS neighbor_id, weight, relation,
                       last_activated_at, updated_at
                FROM memory_links WHERE src_id IN ({placeholders})
                UNION ALL
                SELECT dst_id AS from_id, src_id AS neighbor_id, weight, relation,
                       last_activated_at, updated_at
                FROM memory_links WHERE dst_id IN ({placeholders})
                """,
                [*frontier_ids, *frontier_ids],
            ).fetchall()

            edges_by_node: DefaultDict[str, list[tuple[float, sqlite3.Row]]] = defaultdict(list)
            for edge in edges:
                if edge["neighbor_id"] in visited:
                    continue
                edge_weight = min(max(float(edge["weight"]), 0.0), 1.0)
                link_age_days = self._age_days(
                    edge["last_activated_at"] or edge["updated_at"], now_dt
                )
                link_freshness = 0.5 ** (link_age_days / LINK_DECAY_HALF_LIFE_DAYS)
                edges_by_node[edge["from_id"]].append((edge_weight * link_freshness, edge))

            contributions: DefaultDict[str, list[tuple[float, str, str]]] = defaultdict(list)
            for from_id, node_edges in edges_by_node.items():
                node_edges.sort(key=lambda item: item[0], reverse=True)
                for effective_weight, edge in node_edges[:RESONANCE_MAX_EDGES_PER_NODE]:
                    activation = frontier[from_id] * effective_weight * RESONANCE_HOP_DECAY
                    contributions[edge["neighbor_id"]].append((activation, from_id, edge["relation"]))

            # Converging evidence: a memory activated from several independent
            # sources resonates more strongly than any single path — sum the
            # contributions, capped so one hub can't be amplified without bound.
            activations: dict[str, tuple[float, str, str, int]] = {}
            for neighbor_id, sources in contributions.items():
                best_activation, best_from, best_relation = max(sources, key=lambda item: item[0])
                total = min(
                    sum(activation for activation, _, _ in sources),
                    best_activation * RESONANCE_CONVERGENCE_CAP,
                )
                activations[neighbor_id] = (total, best_from, best_relation, len(sources))
            if not activations:
                break

            ids = list(activations)
            rows = self._visible_rows_for_ids(
                ids,
                owner=owner,
                scope=scope,
                requester_agent_id=requester_agent_id,
                requester_team_id=requester_team_id,
                now=now,
            )

            visited.update(ids)
            frontier = {}
            for row in rows:
                activation, from_id, relation, source_count = activations[row["id"]]
                frontier[row["id"]] = activation
                path = f"via:{from_id}:{relation}"
                if source_count > 1:
                    path = f"{path}:converge{source_count}"
                collected.append((row, activation, hop, path))
                if len(collected) >= MAX_RESONANCE_CANDIDATES:
                    break

        return [
            self._score_row(
                row,
                text_score=activation,
                now_dt=now_dt,
                reason_prefix=f"resonance:hop{hop}:{path}",
            )
            for row, activation, hop, path in collected
        ]

    def _fts_rows(
        self,
        query: str,
        *,
        owner: str | None,
        scope: str | None,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        limit: int,
        now: str,
    ) -> list[sqlite3.Row]:
        fts_query = self._fts_query(query)
        where = [
            "memories_fts MATCH ?",
            "(m.expires_at IS NULL OR julianday(m.expires_at) > julianday(?))",
        ]
        params: list[object] = [fts_query, now]
        if owner:
            where.append("m.owner = ?")
            params.append(owner)
        if scope:
            where.append("m.scope = ?")
            params.append(scope)
        self._append_acl_filter(
            where,
            params,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            alias="m.",
        )
        params.append(max(limit * 5, limit))
        return self.conn.execute(
            f"""
            SELECT m.*, bm25(memories_fts) AS rank
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.id
            WHERE {' AND '.join(where)}
            ORDER BY rank, m.importance DESC, m.confidence DESC, m.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    def _authority_rows(
        self,
        *,
        owner: str | None,
        scope: str | None,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        limit: int,
        now: str,
    ) -> list[sqlite3.Row]:
        where = [
            "(expires_at IS NULL OR julianday(expires_at) > julianday(?))",
            "(json_extract(source, '$.permanence') = 1 AND json_extract(source, '$.weight') >= 10)",
        ]
        params: list[object] = [now]
        if owner:
            where.append("owner = ?")
            params.append(owner)
        if scope:
            where.append("scope = ?")
            params.append(scope)
        self._append_acl_filter(
            where,
            params,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            alias="",
        )
        params.append(max(limit, 1))
        return self.conn.execute(
            f"""
            SELECT * FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY pinned DESC, json_extract(source, '$.weight') DESC,
                     importance DESC, confidence DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    def _semantic_candidate_rows(
        self,
        query: str,
        *,
        owner: str | None,
        scope: str | None,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        limit: int,
        now: str,
    ) -> list[tuple[sqlite3.Row, Candidate]]:
        """Rejoin untrusted semantic candidates through SQLite and hard gates."""
        if not self.candidate_providers:
            return []

        candidates_by_id: dict[str, Candidate] = {}
        candidate_cap = max(1, min(MAX_SEMANTIC_CANDIDATES, max(limit * 10, limit)))
        # Over-fetch from providers: the ACL/expiry hard gates run AFTER the
        # provider returns, so asking for exactly `limit` can leave the whole
        # semantic track empty for requesters who can't see the global top hits.
        provider_fetch_limit = max(limit, min(candidate_cap, limit * 5))
        for provider in self.candidate_providers:
            provider_name = self._safe_provider_name(provider)
            provider_candidates: dict[str, Candidate] = {}
            raw_seen = 0
            try:
                candidates = provider.candidates(
                    query,
                    owner=owner,
                    scope=scope,
                    requester_agent_id=requester_agent_id,
                    requester_team_id=requester_team_id,
                    limit=provider_fetch_limit,
                )
                for raw_candidate in candidates:
                    raw_seen += 1
                    if raw_seen > candidate_cap:
                        break
                    candidate = self._coerce_semantic_candidate(raw_candidate, provider_name=provider_name)
                    if candidate is None:
                        continue
                    previous = provider_candidates.get(candidate.memory_id)
                    if previous is None or candidate.score > previous.score:
                        provider_candidates[candidate.memory_id] = candidate
                    if len(provider_candidates) >= candidate_cap:
                        break
            except Exception:
                # Candidate providers are optional sidecars. Backend failure must
                # discard provider-local partial output and degrade to
                # authoritative SQLite/FTS/fallback retrieval.
                continue
            for memory_id, candidate in provider_candidates.items():
                previous = candidates_by_id.get(memory_id)
                if previous is None or candidate.score > previous.score:
                    candidates_by_id[memory_id] = candidate
                if len(candidates_by_id) >= candidate_cap:
                    break
            if len(candidates_by_id) >= candidate_cap:
                break

        if not candidates_by_id:
            return []

        rows = self._visible_rows_for_ids(
            list(candidates_by_id),
            owner=owner,
            scope=scope,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            now=now,
        )
        return [(row, candidates_by_id[row["id"]]) for row in rows]

    def _visible_rows_for_ids(
        self,
        ids: list[str],
        *,
        owner: str | None,
        scope: str | None,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        now: str,
    ) -> list[sqlite3.Row]:
        """Rejoin untrusted candidate ids through the ACL/expiry hard gates.

        This is the single security gate for every id-producing retrieval
        track (semantic sidecars, resonance expansion): keep it in one place so
        a future ACL change cannot diverge between tracks.
        """
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        where = [
            f"id IN ({placeholders})",
            "(expires_at IS NULL OR julianday(expires_at) > julianday(?))",
        ]
        params: list[object] = [*ids, now]
        if owner:
            where.append("owner = ?")
            params.append(owner)
        if scope:
            where.append("scope = ?")
            params.append(scope)
        self._append_acl_filter(
            where,
            params,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            alias="",
        )
        return self.conn.execute(
            f"SELECT * FROM memories WHERE {' AND '.join(where)}",
            params,
        ).fetchall()

    @classmethod
    def _coerce_semantic_candidate(cls, raw_candidate: object, *, provider_name: str) -> Candidate | None:
        memory_id = getattr(raw_candidate, "memory_id", None)
        if not isinstance(memory_id, str):
            return None
        memory_id = memory_id.strip()
        if not memory_id:
            return None

        raw_score = getattr(raw_candidate, "score", None)
        if raw_score is None:
            return None
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(score):
            return None
        score = min(max(score, 0.0), 1.0)

        raw_rank = getattr(raw_candidate, "rank", None)
        rank = raw_rank if isinstance(raw_rank, int) else None
        reason = cls._safe_semantic_label(getattr(raw_candidate, "reason", ""))
        return Candidate(
            memory_id=memory_id,
            provider=provider_name,
            score=score,
            rank=rank,
            reason=reason,
        )

    @staticmethod
    def _safe_provider_name(provider: CandidateProvider) -> str:
        try:
            raw_name = getattr(provider, "name")
        except Exception:
            raw_name = provider.__class__.__name__
        return MemoryStore._safe_semantic_label(raw_name)

    @staticmethod
    def _safe_semantic_label(value: object, *, max_length: int = 80) -> str:
        try:
            text = "" if value is None else str(value)
        except Exception:
            text = "unknown"
        cleaned = "".join(char if char.isalnum() or char in {"_", "-", ":", "."} else "_" for char in text)
        return cleaned[:max_length] or "unknown"

    @staticmethod
    def _semantic_reason_prefix(candidate: Candidate) -> str:
        reason = f"semantic:{candidate.provider}"
        if candidate.reason:
            reason = f"{reason}:{candidate.reason}"
        return reason

    def _append_acl_filter(
        self,
        where: list[str],
        params: list[object],
        *,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        alias: str,
    ) -> None:
        if not requester_agent_id:
            return
        acl_clauses = [
            f"{alias}owner = ?",
            f"EXISTS (SELECT 1 FROM json_each({alias}visibility) WHERE value = 'global')",
            f"EXISTS (SELECT 1 FROM json_each({alias}visibility) WHERE value = ?)",
        ]
        params.extend([requester_agent_id, f"agent:{requester_agent_id}"])
        # Registered team memberships resolve automatically: an agent in the
        # registry sees its teams' memories without callers wiring
        # requester_team_id through every call site.
        team_ids = list(dict.fromkeys(
            ([requester_team_id] if requester_team_id else [])
            + self._cached_teams_for(requester_agent_id)
        ))
        for team_id in team_ids:
            acl_clauses.extend(
                [
                    f"EXISTS (SELECT 1 FROM json_each({alias}visibility) WHERE value = 'team' AND json_extract({alias}source, '$.team_id') = ?)",
                    f"EXISTS (SELECT 1 FROM json_each({alias}visibility) WHERE value = ?)",
                ]
            )
            params.extend([team_id, f"team:{team_id}"])
        # Project memberships resolve the same way: project:<id> memory is
        # visible only to that project's members (a subset of the team).
        for project_id in self._cached_projects_for(requester_agent_id):
            acl_clauses.append(
                f"EXISTS (SELECT 1 FROM json_each({alias}visibility) WHERE value = ?)"
            )
            params.append(f"project:{project_id}")
        where.append("(" + " OR ".join(acl_clauses) + ")")

    # Team memberships are cached for one search's worth of ACL clauses. A
    # membership change made through THIS store invalidates immediately; a
    # change from another process/connection (WAL multi-writer) is picked up
    # within this TTL instead of persisting until restart.
    _TEAMS_CACHE_TTL_SECONDS = MEMBERSHIP_CACHE_TTL_SECONDS

    def _cached_teams_for(self, agent_id: str) -> list[str]:
        cache = getattr(self, "_teams_cache", None)
        now = time.monotonic()
        if cache is None or now - getattr(self, "_teams_cache_at", 0.0) > self._TEAMS_CACHE_TTL_SECONDS:
            cache = self._teams_cache = {}
            self._teams_cache_at = now
        if agent_id not in cache:
            cache[agent_id] = self.teams_for(agent_id)
        return cache[agent_id]

    def _cached_projects_for(self, agent_id: str) -> list[str]:
        cache = getattr(self, "_projects_cache", None)
        now = time.monotonic()
        if cache is None or now - getattr(self, "_projects_cache_at", 0.0) > self._TEAMS_CACHE_TTL_SECONDS:
            cache = self._projects_cache = {}
            self._projects_cache_at = now
        if agent_id not in cache:
            cache[agent_id] = self.projects_for(agent_id)
        return cache[agent_id]

    def _score_row(
        self,
        row: sqlite3.Row,
        *,
        text_score: float,
        now_dt: datetime,
        reason_prefix: str,
    ) -> SearchResult:
        age_days = self._age_days(row["updated_at"], now_dt)
        freshness = freshness_factor(
            row["decay_policy"],
            age_days=age_days,
            half_life_days=float(row["decay_half_life_days"]),
            pinned=bool(row["pinned"]),
        )
        reinforcement = reinforcement_factor(int(row["access_count"] or 0))
        score = effective_score(
            text_score=text_score,
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            freshness=freshness,
            reinforcement=reinforcement,
        )
        reason = f"{reason_prefix}+metadata+freshness:{freshness:.3f}+reinforcement:{reinforcement:.3f}"
        return SearchResult(record=self._row_to_record(row), score=score, reason=reason)

    def _fallback_candidates(
        self,
        *,
        owner: str | None,
        scope: str | None,
        requester_agent_id: str | None,
        requester_team_id: str | None,
        limit: int,
    ) -> list[SearchResult]:
        where = ["(expires_at IS NULL OR julianday(expires_at) > julianday(?))"]
        params: list[object] = [utc_now()]
        if owner:
            where.append("owner = ?")
            params.append(owner)
        if scope:
            where.append("scope = ?")
            params.append(scope)
        self._append_acl_filter(
            where,
            params,
            requester_agent_id=requester_agent_id,
            requester_team_id=requester_team_id,
            alias="",
        )
        params.append(max(limit, 1))
        rows = self.conn.execute(
            f"""
            SELECT * FROM memories
            WHERE {' AND '.join(where)}
            ORDER BY pinned DESC, importance DESC, confidence DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        results: list[SearchResult] = []
        now_dt = datetime.now(timezone.utc)
        for row in rows:
            age_days = self._age_days(row["updated_at"], now_dt)
            freshness = freshness_factor(
                row["decay_policy"],
                age_days=age_days,
                half_life_days=float(row["decay_half_life_days"]),
                pinned=bool(row["pinned"]),
            )
            reinforcement = reinforcement_factor(int(row["access_count"] or 0))
            score = effective_score(
                text_score=0.05,
                importance=float(row["importance"]),
                confidence=float(row["confidence"]),
                freshness=freshness,
                reinforcement=reinforcement,
            )
            reason = f"fallback:pinned_recent+freshness:{freshness:.3f}+reinforcement:{reinforcement:.3f}"
            results.append(SearchResult(record=self._row_to_record(row), score=score, reason=reason))
        results.sort(key=lambda result: result.score, reverse=True)
        return results

    def stats(self) -> dict[str, int]:
        total = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        by_scope = dict(self.conn.execute("SELECT scope, COUNT(*) FROM memories GROUP BY scope").fetchall())
        by_type = dict(self.conn.execute("SELECT type, COUNT(*) FROM memories GROUP BY type").fetchall())
        links = self.conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]
        return {"total": total, "by_scope": by_scope, "by_type": by_type, "links": links}

    def dashboard_stats(self, *, activity_days: int = DASHBOARD_ACTIVITY_WINDOW_DAYS) -> dict[str, object]:
        """Aggregate figures for the console dashboard."""
        now = utc_now()
        base = self.stats()
        pinned = self.conn.execute("SELECT COUNT(*) FROM memories WHERE pinned = 1").fetchone()[0]
        expired = self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE expires_at IS NOT NULL "
            "AND julianday(expires_at) <= julianday(?)",
            (now,),
        ).fetchone()[0]
        by_owner = dict(
            self.conn.execute(
                "SELECT owner, COUNT(*) FROM memories GROUP BY owner ORDER BY COUNT(*) DESC LIMIT 6"
            ).fetchall()
        )
        by_relation = dict(
            self.conn.execute("SELECT relation, COUNT(*) FROM memory_links GROUP BY relation").fetchall()
        )
        top_recalled = [
            {"id": row["id"], "summary": row["summary"], "access_count": int(row["access_count"])}
            for row in self.conn.execute(
                "SELECT id, summary, access_count FROM memories WHERE access_count > 0 "
                "ORDER BY access_count DESC, updated_at DESC LIMIT 5"
            ).fetchall()
        ]
        today = datetime.now(timezone.utc).date()
        days = [(today - timedelta(days=offset)).isoformat() for offset in range(activity_days - 1, -1, -1)]
        counted = dict(
            self.conn.execute(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) FROM memories "
                "WHERE substr(created_at, 1, 10) >= ? GROUP BY day",
                (days[0],),
            ).fetchall()
        )
        linked = self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id IN "
            "(SELECT src_id FROM memory_links UNION SELECT dst_id FROM memory_links)"
        ).fetchone()[0]
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=int(LINK_DECAY_HALF_LIFE_DAYS))).isoformat(timespec="seconds")
        stale_links = self.conn.execute(
            "SELECT COUNT(*) FROM memory_links WHERE COALESCE(last_activated_at, updated_at) < ?",
            (stale_cutoff,),
        ).fetchone()[0]
        top_hubs = [
            {"id": row["id"], "summary": row["summary"], "degree": int(row["degree"])}
            for row in self.conn.execute(
                """
                SELECT m.id, m.summary, COUNT(*) AS degree FROM (
                    SELECT src_id AS memory_id FROM memory_links
                    UNION ALL SELECT dst_id FROM memory_links
                ) endpoints JOIN memories m ON m.id = endpoints.memory_id
                GROUP BY m.id ORDER BY degree DESC LIMIT 3
                """
            ).fetchall()
        ]
        graph_health = {
            "linked_memories": int(linked),
            "orphan_memories": int(base["total"]) - int(linked),
            "avg_degree": round(2 * int(base["links"]) / max(1, int(base["total"])), 2),
            "stale_links": int(stale_links),
            "top_hubs": top_hubs,
        }
        archived = self.conn.execute("SELECT COUNT(*) FROM memories_archive").fetchone()[0]
        return base | {
            "archived": int(archived),
            "graph_health": graph_health,
            "pinned": int(pinned),
            "expired": int(expired),
            "by_owner": by_owner,
            "by_relation": by_relation,
            "top_recalled": top_recalled,
            "activity": [{"day": day, "count": int(counted.get(day, 0))} for day in days],
        }

    def _row_to_link(self, row: sqlite3.Row) -> MemoryLink:
        return MemoryLink(
            src_id=row["src_id"], dst_id=row["dst_id"], relation=row["relation"],
            weight=float(row["weight"]), created_at=row["created_at"], updated_at=row["updated_at"],
            last_activated_at=row["last_activated_at"],
            activation_count=int(row["activation_count"] or 0),
            source=json.loads(row["source"] or "{}"),
        )

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"], owner=row["owner"], scope=row["scope"], type=row["type"],
            content=row["content"], summary=row["summary"], tags=json.loads(row["tags"] or "[]"),
            visibility=json.loads(row["visibility"] or "[]"), source=json.loads(row["source"] or "{}"),
            confidence=float(row["confidence"]), importance=float(row["importance"]),
            created_at=row["created_at"], updated_at=row["updated_at"], expires_at=row["expires_at"],
            decay_policy=row["decay_policy"], decay_half_life_days=float(row["decay_half_life_days"]),
            last_accessed_at=row["last_accessed_at"], access_count=int(row["access_count"] or 0),
            pinned=bool(row["pinned"]),
            helpful_count=int(_row_get(row, "helpful_count", 0) or 0),
            unhelpful_count=int(_row_get(row, "unhelpful_count", 0) or 0),
            _validate=False,
        )

    @staticmethod
    def _age_days(value: str, now_dt: datetime) -> float:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (now_dt - parsed).total_seconds() / 86_400)

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = [t.replace('"', ' ').strip() for t in query.split() if t.strip()]
        return " OR ".join(f'"{t}"' for t in terms) if terms else '""'


def _row_get(row: sqlite3.Row, key: str, default):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _content_fingerprint(text: str) -> str:
    """Hash the FULL normalized content for exact-duplicate detection.

    Unlike context_pack's claim fingerprint (which truncates for cheap
    comparison), consolidation deletes records, so two memories that share a
    long prefix but diverge later must never collide.
    """
    normalized = re.sub(r"\W+", " ", text.casefold()).strip()
    normalized = " ".join(normalized.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _merge_priority(row: sqlite3.Row) -> tuple:
    source = json.loads(row["source"] or "{}")
    try:
        weight = float(source.get("weight", 0) or 0)
    except (TypeError, ValueError):
        weight = 0.0
    authority = 1 if (source.get("permanence") in (True, 1) and weight >= 10) else 0
    return (int(row["pinned"] or 0), authority, float(row["confidence"]), row["updated_at"], row["id"])

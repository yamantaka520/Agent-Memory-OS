from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import uuid

from .constants import (
    DEFAULT_DECAY_HALF_LIFE_DAYS,
    DEFAULT_DECAY_HALF_LIFE_FALLBACK_DAYS,
)
from .scoring import VALID_DECAY_POLICIES


PUBLIC_MEMORY_SCOPES = frozenset({"user", "agent", "project", "team", "global"})
VALID_MEMORY_SCOPES = PUBLIC_MEMORY_SCOPES | {"profile"}
PUBLIC_MEMORY_TYPES = frozenset(
    {"preference", "fact", "procedure", "environment", "decision", "warning", "note"}
)
VALID_MEMORY_TYPES = PUBLIC_MEMORY_TYPES | {"snapshot"}


def normalize_iso_timestamp(value: str | None, *, field_name: str) -> str | None:
    """Validate an ISO-8601 timestamp and canonicalize it to UTC.

    Naive values retain the historical API interpretation of UTC. Explicit
    offsets are converted as instants, so storage and lexical presentation no
    longer depend on the caller's offset spelling.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_now_micro() -> str:
    """Microsecond-resolution UTC timestamp for clocks that must order events
    within the same second (e.g. the ACL clock: a create then a revoke in one
    second must still sort revoke-after-create). Sorts correctly against
    second-resolution stamps: '…00+00:00' < '…00.5+00:00' because '+' < '.'."""
    return datetime.now(timezone.utc).isoformat()


def new_memory_id() -> str:
    return "mem_" + uuid.uuid4().hex


@dataclass(slots=True)
class MemoryRecord:
    content: str
    owner: str = "default"
    scope: str = "user"
    type: str = "note"
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    visibility: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    importance: float = 0.5
    id: str = field(default_factory=new_memory_id)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    expires_at: str | None = None
    decay_policy: str = "exponential"
    decay_half_life_days: float | None = None
    last_accessed_at: str | None = None
    access_count: int = 0
    pinned: bool = False
    helpful_count: int = 0
    unhelpful_count: int = 0
    _validate: InitVar[bool] = True

    def __post_init__(self, _validate: bool) -> None:
        # Persisted rows predate the canonical write contract and may contain
        # application-defined scope/type values. Hydration preserves those
        # rows; every ordinary constructor and dataclasses.replace() call keeps
        # the strict default and therefore validates new or updated state.
        if _validate:
            if not isinstance(self.content, str) or not self.content.strip():
                raise ValueError("content must be a non-empty string")
            if not isinstance(self.owner, str) or not self.owner.strip():
                raise ValueError("owner must be a non-empty string")
            if self.scope not in VALID_MEMORY_SCOPES:
                raise ValueError(f"scope must be one of {sorted(VALID_MEMORY_SCOPES)}")
            if self.type not in VALID_MEMORY_TYPES:
                raise ValueError(f"type must be one of {sorted(VALID_MEMORY_TYPES)}")
            if self.summary is not None and not isinstance(self.summary, str):
                raise ValueError("summary must be a string or None")
            if not isinstance(self.tags, list) or not all(
                isinstance(tag, str) and tag.strip() for tag in self.tags
            ):
                raise ValueError("tags must be a list of non-empty strings")
            if not isinstance(self.visibility, list) or not all(
                isinstance(grant, str) and grant.strip() for grant in self.visibility
            ):
                raise ValueError("visibility must be a list of non-empty strings")
            if not isinstance(self.source, dict):
                raise ValueError("source must be a mapping")
            for field_name in ("confidence", "importance"):
                value = getattr(self, field_name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0.0 <= value <= 1.0
                ):
                    raise ValueError(f"{field_name} must be between 0.0 and 1.0")
            self.expires_at = normalize_iso_timestamp(
                self.expires_at,
                field_name="expires_at",
            )
        if self.decay_policy not in VALID_DECAY_POLICIES:
            raise ValueError(f"decay_policy must be one of {sorted(VALID_DECAY_POLICIES)}")
        if self.decay_half_life_days is None:
            self.decay_half_life_days = DEFAULT_DECAY_HALF_LIFE_DAYS.get(self.type, DEFAULT_DECAY_HALF_LIFE_FALLBACK_DAYS)
        if self.decay_policy != "none" and self.decay_half_life_days <= 0:
            raise ValueError("decay_half_life_days must be positive")
        if self.access_count < 0:
            raise ValueError("access_count must be non-negative")

    def normalized_summary(self) -> str:
        if self.summary:
            return self.summary
        text = " ".join(self.content.split())
        return text[:96] + ("…" if len(text) > 96 else "")

    def tags_json(self) -> str:
        return json.dumps(self.tags, ensure_ascii=False)

    def visibility_json(self) -> str:
        return json.dumps(self.visibility, ensure_ascii=False)

    def source_json(self) -> str:
        return json.dumps(self.source, ensure_ascii=False, sort_keys=True)


@dataclass(slots=True)
class ContextSnapshot:
    session_id: str
    snapshot_data: dict[str, Any]
    owner: str = "default"
    trigger: str = "manual"
    snapshot_index: int = 0

    def to_record(self) -> MemoryRecord:
        return MemoryRecord(
            content=json.dumps(self.snapshot_data, ensure_ascii=False),
            owner=self.owner,
            type="snapshot",
            summary=f"Snapshot for session {self.session_id} (index {self.snapshot_index})",
            source={
                "session_id": self.session_id,
                "trigger": self.trigger,
                "snapshot_index": self.snapshot_index,
            }
        )


@dataclass(slots=True)
class SearchResult:
    record: MemoryRecord
    score: float
    reason: str = "fts"


VALID_LINK_RELATIONS = {
    "related_to",
    "supersedes",
    "caused_by",
    "derived_from",
    "co_recalled",
}


@dataclass(slots=True)
class MemoryLink:
    """Authoritative association edge between two memories.

    Links live in the SQLite source-of-truth layer next to `memories`; they
    survive disposable index rebuilds. Traversal is undirected for resonance
    recall, and every traversed node must still pass ACL/expiry hard gates.
    """

    src_id: str
    dst_id: str
    relation: str = "related_to"
    weight: float = 0.5
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    last_activated_at: str | None = None
    activation_count: int = 0
    source: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.src_id or not self.dst_id:
            raise ValueError("link endpoints must be non-empty memory ids")
        if self.src_id == self.dst_id:
            raise ValueError("link endpoints must differ")
        if self.relation not in VALID_LINK_RELATIONS:
            raise ValueError(f"relation must be one of {sorted(VALID_LINK_RELATIONS)}")
        self.weight = min(max(float(self.weight), 0.0), 1.0)
        if self.activation_count < 0:
            raise ValueError("activation_count must be non-negative")

    def source_json(self) -> str:
        return json.dumps(self.source, ensure_ascii=False, sort_keys=True)


@dataclass(slots=True)
class RecallProfile:
    """Per-agent soft recall bias reflecting the agent's persona.

    Different agents need different memory: an engineering agent leans on
    `procedure`/`decision`, a companion agent leans on `preference`/`note`.
    Profiles only re-weight ranking; they never bypass ACL or expiry hard gates
    and never grant visibility.
    """

    agent_id: str = ""
    type_weights: dict[str, float] = field(default_factory=dict)
    scope_weights: dict[str, float] = field(default_factory=dict)

    def weight_for(self, record: MemoryRecord) -> float:
        weight = self.type_weights.get(record.type, 1.0) * self.scope_weights.get(record.scope, 1.0)
        return min(max(float(weight), 0.25), 2.0)

    def signature(self) -> tuple:
        return (
            self.agent_id,
            tuple(sorted(self.type_weights.items())),
            tuple(sorted(self.scope_weights.items())),
        )

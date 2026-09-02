from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BundleContract:
    """The import shape AgentMemoryOS promises for one bundle version."""

    version: int
    schema_resource: str
    import_record_kinds: frozenset[str]
    allow_unknown_record_kinds: bool

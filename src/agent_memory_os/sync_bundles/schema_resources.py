from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from types import MappingProxyType

from .registry import SUPPORTED_BUNDLE_VERSIONS, contract_for

_SCHEMA_IDS = {
    version: f"urn:agent-memory-os:sync-bundle:v{version:03d}"
    for version in SUPPORTED_BUNDLE_VERSIONS
}
SCHEMA_IDS: Mapping[int, str] = MappingProxyType(_SCHEMA_IDS)


def load_schema(version: object) -> dict[str, object]:
    """Load one packaged schema without assuming a filesystem-backed install."""

    contract = contract_for(version)
    resource = files("agent_memory_os.sync_bundles").joinpath(
        *contract.schema_resource.split("/")
    )
    schema = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(schema, dict) or schema.get("$id") != SCHEMA_IDS[contract.version]:
        raise ValueError(f"bundle v{contract.version} schema identity is invalid")
    return schema


def load_schema_resources() -> Mapping[str, dict[str, object]]:
    """Return every registered schema keyed by its stable offline identifier."""

    resources = {
        SCHEMA_IDS[version]: load_schema(version)
        for version in sorted(SUPPORTED_BUNDLE_VERSIONS)
    }
    return MappingProxyType(resources)

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from .contract import BundleContract


def load_contract_manifest() -> dict[str, Any]:
    """Load the packaged normative sync-bundle contract."""

    resource = files("agent_memory_os.sync_bundles").joinpath("contract.json")
    manifest = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("sync-bundle contract must be a JSON object")
    return manifest


def _load_contracts() -> tuple[Mapping[int, BundleContract], int, frozenset[int]]:
    manifest = load_contract_manifest()
    current = manifest.get("current_writer_version")
    supported = manifest.get("supported_versions")
    versions = manifest.get("versions")

    if not isinstance(current, int) or isinstance(current, bool):
        raise TypeError("sync-bundle current_writer_version must be an integer")
    if not isinstance(supported, list) or any(
        not isinstance(version, int) or isinstance(version, bool)
        for version in supported
    ):
        raise TypeError("sync-bundle supported_versions must be an integer array")
    if len(supported) != len(set(supported)):
        raise ValueError("sync-bundle supported_versions must be unique")
    if not isinstance(versions, list):
        raise TypeError("sync-bundle versions must be an array")

    contracts: dict[int, BundleContract] = {}
    for value in versions:
        if not isinstance(value, dict):
            raise TypeError("each sync-bundle version contract must be an object")
        version = value.get("version")
        schema_resource = value.get("schema_resource")
        record_kinds = value.get("import_record_kinds")
        allow_unknown = value.get("allow_unknown_record_kinds")
        if not isinstance(version, int) or isinstance(version, bool):
            raise TypeError("sync-bundle contract version must be an integer")
        if not isinstance(schema_resource, str):
            raise TypeError(f"bundle v{version} schema_resource must be a string")
        if not schema_resource:
            raise ValueError(
                f"bundle v{version} schema_resource must be a non-empty string"
            )
        if not isinstance(record_kinds, list) or any(
            not isinstance(kind, str) for kind in record_kinds
        ):
            raise TypeError(
                f"bundle v{version} import_record_kinds must be a string array"
            )
        if any(not kind for kind in record_kinds):
            raise ValueError(
                f"bundle v{version} import_record_kinds must be non-empty strings"
            )
        if len(record_kinds) != len(set(record_kinds)):
            raise ValueError(f"bundle v{version} import_record_kinds must be unique")
        if not isinstance(allow_unknown, bool):
            raise TypeError(
                f"bundle v{version} allow_unknown_record_kinds must be boolean"
            )
        if version in contracts:
            raise ValueError(f"duplicate sync-bundle version contract: {version}")
        contracts[version] = BundleContract(
            version=version,
            schema_resource=schema_resource,
            import_record_kinds=frozenset(record_kinds),
            allow_unknown_record_kinds=allow_unknown,
        )

    supported_versions = frozenset(supported)
    if frozenset(contracts) != supported_versions:
        raise ValueError(
            "sync-bundle supported_versions must match the version contracts"
        )
    if current not in supported_versions:
        raise ValueError("sync-bundle current_writer_version must be supported")
    return MappingProxyType(contracts), current, supported_versions


CONTRACTS, CURRENT_BUNDLE_VERSION, SUPPORTED_BUNDLE_VERSIONS = _load_contracts()


def contract_for(version: object) -> BundleContract:
    """Resolve a supported import promise without tightening header tolerance."""

    for contract in CONTRACTS.values():
        if version == contract.version:
            return contract
    raise ValueError(f"unsupported bundle version: {version}")

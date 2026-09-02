from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from agent_memory_os import MemoryClient
from agent_memory_os.sync_bundles import (
    CONTRACTS,
    CURRENT_BUNDLE_VERSION,
    SCHEMA_IDS,
    SUPPORTED_BUNDLE_VERSIONS,
    contract_for,
    load_contract_manifest,
    load_schema_resources,
)
from agent_memory_os.sync_bundles.codec import (
    decode_header,
    decode_record,
    encode_header,
    encode_record,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "sync_bundles"
_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$id",
        "$ref",
        "$schema",
        "additionalProperties",
        "anyOf",
        "const",
        "contentMediaType",
        "contentSchema",
        "description",
        "enum",
        "exclusiveMinimum",
        "items",
        "maximum",
        "minLength",
        "minimum",
        "not",
        "oneOf",
        "properties",
        "required",
        "title",
        "type",
    }
)


def _fixture_lines(filename: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (_FIXTURES / filename).read_text(encoding="utf-8").splitlines()
    ]


def _fixture_cases() -> tuple[tuple[int, str], ...]:
    cases = []
    for path in sorted(_FIXTURES.glob("*.jsonl")):
        header = _fixture_lines(path.name)[0]
        assert header.get("kind") == "bundle"
        version = header.get("version")
        assert isinstance(version, int) and not isinstance(version, bool)
        cases.append((version, path.name))
    return tuple(cases)


_FIXTURE_CASES = _fixture_cases()


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


def _fixture_with_version(
    tmp_path: Path,
    filename: str,
    version: int,
) -> Path:
    lines = _fixture_lines(filename)
    lines[0]["version"] = version
    target = tmp_path / f"v{version}-{filename}"
    target.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )
    return target


def _schema_children(schema: Mapping[str, object]) -> Iterator[tuple[str, object]]:
    for container_name in ("$defs", "properties"):
        container = schema.get(container_name)
        if container is None:
            continue
        assert isinstance(container, dict), f"{container_name} must be an object"
        for name, child in container.items():
            yield f"/{container_name}/{name}", child

    for collection_name in ("anyOf", "oneOf"):
        collection = schema.get(collection_name)
        if collection is None:
            continue
        assert isinstance(collection, list), f"{collection_name} must be an array"
        for index, child in enumerate(collection):
            yield f"/{collection_name}/{index}", child

    negated = schema.get("not")
    if negated is not None:
        yield "/not", negated
    additional = schema.get("additionalProperties")
    if isinstance(additional, (dict, bool)):
        yield "/additionalProperties", additional
    for schema_name in ("contentSchema", "items"):
        child = schema.get(schema_name)
        if child is not None:
            yield f"/{schema_name}", child


def _assert_supported_schema_vocabulary(
    schema: object,
    *,
    path: str = "$",
) -> None:
    if isinstance(schema, bool):
        return
    assert isinstance(schema, dict), f"{path} must be a schema object or boolean"
    unsupported = set(schema) - _SUPPORTED_SCHEMA_KEYWORDS
    assert not unsupported, f"{path} uses unsupported schema keywords: {unsupported}"
    for suffix, child in _schema_children(schema):
        _assert_supported_schema_vocabulary(child, path=f"{path}{suffix}")


def _json_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(left_value, right_value)
            for left_value, right_value in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return type(left) is type(right) and left == right


def _matches_type(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value).is_integer()
        )
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise AssertionError(f"unsupported JSON Schema type: {expected}")


class _SchemaConformance:
    def __init__(self, resources: Mapping[str, dict[str, object]]) -> None:
        self._resources = dict(resources)
        for resource_id, schema in self._resources.items():
            assert schema.get("$id") == resource_id
            _assert_supported_schema_vocabulary(schema)
        visited: set[tuple[str, int]] = set()
        for resource_id, schema in self._resources.items():
            self._assert_resolvable_references(schema, resource_id, visited)

    def is_valid(self, value: object, schema_id: str) -> bool:
        return self._matches(value, self._resources[schema_id], schema_id)

    def _resolve_reference(
        self,
        reference: str,
        base_id: str,
    ) -> tuple[object, str]:
        resource_id, separator, fragment = reference.partition("#")
        target_id = resource_id or base_id
        assert target_id in self._resources, f"unresolved schema resource: {target_id}"
        target: object = self._resources[target_id]
        if separator and fragment:
            assert fragment.startswith("/"), f"unsupported JSON Pointer: #{fragment}"
            for raw_token in fragment[1:].split("/"):
                token = raw_token.replace("~1", "/").replace("~0", "~")
                if isinstance(target, dict):
                    assert token in target, f"unresolved JSON Pointer token: {token}"
                    target = target[token]
                elif isinstance(target, list):
                    target = target[int(token)]
                else:
                    raise TypeError(f"JSON Pointer traverses a scalar at: {token}")
        assert isinstance(target, (dict, bool))
        return target, target_id

    def _assert_resolvable_references(
        self,
        schema: object,
        base_id: str,
        visited: set[tuple[str, int]],
    ) -> None:
        if isinstance(schema, bool):
            return
        assert isinstance(schema, dict)
        declared_id = schema.get("$id")
        if isinstance(declared_id, str):
            base_id = declared_id
        marker = (base_id, id(schema))
        if marker in visited:
            return
        visited.add(marker)

        reference = schema.get("$ref")
        if reference is not None:
            assert isinstance(reference, str)
            target, target_id = self._resolve_reference(reference, base_id)
            self._assert_resolvable_references(target, target_id, visited)
        for _, child in _schema_children(schema):
            self._assert_resolvable_references(child, base_id, visited)

    def _matches(self, value: object, schema: object, base_id: str) -> bool:
        if isinstance(schema, bool):
            return schema
        assert isinstance(schema, dict)
        declared_id = schema.get("$id")
        if isinstance(declared_id, str):
            base_id = declared_id

        reference = schema.get("$ref")
        if reference is not None:
            assert isinstance(reference, str)
            target, target_id = self._resolve_reference(reference, base_id)
            if not self._matches(value, target, target_id):
                return False

        expected_type = schema.get("type")
        if expected_type is not None:
            expected_types = (
                [expected_type] if isinstance(expected_type, str) else expected_type
            )
            assert isinstance(expected_types, list)
            assert all(isinstance(item, str) for item in expected_types)
            if not any(_matches_type(value, item) for item in expected_types):
                return False

        if "const" in schema and not _json_equal(value, schema["const"]):
            return False
        enum = schema.get("enum")
        if enum is not None:
            assert isinstance(enum, list)
            if not any(_json_equal(value, candidate) for candidate in enum):
                return False

        any_of = schema.get("anyOf")
        if any_of is not None:
            assert isinstance(any_of, list)
            if not any(self._matches(value, child, base_id) for child in any_of):
                return False
        one_of = schema.get("oneOf")
        if one_of is not None:
            assert isinstance(one_of, list)
            if sum(self._matches(value, child, base_id) for child in one_of) != 1:
                return False
        negated = schema.get("not")
        if negated is not None and self._matches(value, negated, base_id):
            return False

        minimum_length = schema.get("minLength")
        if minimum_length is not None:
            assert isinstance(minimum_length, int)
            if isinstance(value, str) and len(value) < minimum_length:
                return False

        for keyword, comparison in (
            ("minimum", lambda actual, limit: actual >= limit),
            ("maximum", lambda actual, limit: actual <= limit),
            ("exclusiveMinimum", lambda actual, limit: actual > limit),
        ):
            limit = schema.get(keyword)
            if limit is not None:
                assert isinstance(limit, (int, float)) and not isinstance(limit, bool)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and not comparison(value, limit)
                ):
                    return False

        content_schema = schema.get("contentSchema")
        if content_schema is not None:
            assert schema.get("contentMediaType") == "application/json"
            assert isinstance(content_schema, (dict, bool))
            if isinstance(value, str):
                try:
                    decoded_content = json.loads(
                        value,
                        parse_constant=_reject_json_constant,
                    )
                except ValueError:
                    return False
                if not self._matches(decoded_content, content_schema, base_id):
                    return False

        items = schema.get("items")
        if items is not None:
            assert isinstance(items, (dict, bool))
            if isinstance(value, list) and any(
                not self._matches(item, items, base_id) for item in value
            ):
                return False

        if isinstance(value, dict):
            required = schema.get("required", [])
            assert isinstance(required, list)
            if any(name not in value for name in required):
                return False
            properties = schema.get("properties", {})
            assert isinstance(properties, dict)
            for name, child in properties.items():
                if name in value and not self._matches(value[name], child, base_id):
                    return False
            additional = schema.get("additionalProperties", True)
            extra_names = value.keys() - properties.keys()
            if additional is False and extra_names:
                return False
            if isinstance(additional, dict) and any(
                not self._matches(value[name], additional, base_id)
                for name in extra_names
            ):
                return False

        return True


def _schema_conformance() -> _SchemaConformance:
    return _SchemaConformance(load_schema_resources())


def test_contract_manifest_drives_registry() -> None:
    manifest = load_contract_manifest()

    assert manifest["current_writer_version"] == CURRENT_BUNDLE_VERSION
    assert frozenset(manifest["supported_versions"]) == SUPPORTED_BUNDLE_VERSIONS
    manifest_versions = manifest["versions"]
    assert isinstance(manifest_versions, list)
    manifest_by_version = {
        value["version"]: value
        for value in manifest_versions
        if isinstance(value, dict)
    }
    assert set(manifest_by_version) == SUPPORTED_BUNDLE_VERSIONS
    assert {1, 2, 3} <= SUPPORTED_BUNDLE_VERSIONS

    v1 = contract_for(1)
    v2 = contract_for(2)
    v3 = contract_for(3)

    assert v1.import_record_kinds == {"memory", "link", "profile"}
    assert v2.import_record_kinds == v1.import_record_kinds | {"tombstone"}
    assert v3.import_record_kinds == v2.import_record_kinds | {
        "team",
        "project",
        "org_tombstone",
    }
    for version, contract in CONTRACTS.items():
        manifest_contract = manifest_by_version[version]
        assert manifest_contract["schema_resource"] == contract.schema_resource
        assert (
            frozenset(manifest_contract["import_record_kinds"])
            == contract.import_record_kinds
        )
        assert (
            manifest_contract["allow_unknown_record_kinds"]
            is contract.allow_unknown_record_kinds
        )


def test_registry_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        CONTRACTS[4] = contract_for(3)  # type: ignore[index]


def test_codec_selects_contract_without_filtering_shared_merge_inputs() -> None:
    contract, header = decode_header({"kind": "bundle", "version": 1})
    later_record = {
        "kind": "team",
        "id": "t",
        "name": "Team",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "members": [],
    }

    assert contract is contract_for(1)
    assert header == {"kind": "bundle", "version": 1}
    assert decode_record(contract, later_record) == later_record


def test_codec_preserves_legacy_timestamp_text() -> None:
    contract = contract_for(2)
    record = {
        "kind": "profile",
        "agent_id": "legacy",
        "type_weights": "{}",
        "scope_weights": "{}",
        "updated_at": "2026-01-01T01:00:00+01:00",
    }

    assert decode_record(contract, record) == record


@pytest.mark.parametrize("half_life", [-30.0, 0.0, 365.0])
def test_none_decay_codec_uses_provisional_zero_sentinel(
    half_life: float,
) -> None:
    record = dict(_fixture_lines("v001-core.jsonl")[1])
    record["decay_policy"] = "none"
    record["decay_half_life_days"] = half_life

    decoded = decode_record(contract_for(1), record)
    encoded = encode_record(contract_for(CURRENT_BUNDLE_VERSION), record)

    assert decoded["decay_half_life_days"] == 0.0
    assert encoded["decay_half_life_days"] == 0.0
    assert record["decay_half_life_days"] == half_life


@pytest.mark.parametrize(
    "kind",
    sorted(contract_for(CURRENT_BUNDLE_VERSION).import_record_kinds),
)
def test_current_codec_preserves_record_payloads(kind: str) -> None:
    record = {
        "kind": kind,
        "legacy_timestamp": "2026-01-01T01:00:00+01:00",
        "unicode": "記憶",
    }

    encoded = encode_record(contract_for(CURRENT_BUNDLE_VERSION), record)

    assert encoded == record
    assert encoded is not record


def test_current_header_encoder_uses_manifest_writer_version() -> None:
    assert encode_header(contract_for(CURRENT_BUNDLE_VERSION), node_name="node") == {
        "kind": "bundle",
        "version": CURRENT_BUNDLE_VERSION,
        "node_name": "node",
    }


def test_every_manifest_schema_resource_ships() -> None:
    schema_root = files("agent_memory_os.sync_bundles").joinpath("schemas")
    versions = {entry.name for entry in schema_root.iterdir() if entry.is_dir()}
    resources = load_schema_resources()
    expected_directories = {
        Path(contract.schema_resource).parent.name
        for contract in CONTRACTS.values()
    }

    assert versions == expected_directories
    assert set(resources) == set(SCHEMA_IDS.values())
    conformance = _schema_conformance()
    for version in sorted(SUPPORTED_BUNDLE_VERSIONS):
        contract = contract_for(version)
        schema_resource = files("agent_memory_os.sync_bundles").joinpath(
            *contract.schema_resource.split("/")
        )
        assert schema_resource.is_file()
        schema = resources[SCHEMA_IDS[version]]
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == SCHEMA_IDS[version]
        assert conformance.is_valid(
            {"kind": "bundle", "version": version},
            SCHEMA_IDS[version],
        )


def test_conformance_rejects_invalid_property_types() -> None:
    tombstone = dict(_fixture_lines("v002-tombstone.jsonl")[1])
    tombstone["id"] = 123

    assert not _schema_conformance().is_valid(tombstone, SCHEMA_IDS[2])


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("tags", "{}"),
        ("visibility", "not-json"),
        ("source", "[]"),
        ("confidence", 2.0),
        ("decay_policy", "not-a-policy"),
        ("decay_half_life_days", 0),
        ("access_count", -1),
        ("pinned", 2),
    ],
)
def test_memory_schema_rejects_values_that_cannot_remain_operable(
    field: str,
    invalid_value: object,
) -> None:
    memory = dict(_fixture_lines("v001-core.jsonl")[1])
    memory[field] = invalid_value

    assert not _schema_conformance().is_valid(memory, SCHEMA_IDS[1])


@pytest.mark.parametrize("half_life", [-30.0, 0.0, 365.0])
def test_memory_schema_accepts_half_life_sentinel_inputs_when_decay_is_none(
    half_life: float,
) -> None:
    memory = dict(_fixture_lines("v001-core.jsonl")[1])
    memory["decay_policy"] = "none"
    memory["decay_half_life_days"] = half_life

    assert _schema_conformance().is_valid(memory, SCHEMA_IDS[1])


@pytest.mark.parametrize(
    ("filename", "line_index", "field", "invalid_value", "version"),
    [
        ("v001-core.jsonl", 3, "relation", "not-a-relation", 1),
        ("v001-core.jsonl", 3, "source", "[]", 1),
        ("v001-core.jsonl", 4, "type_weights", "[]", 1),
        ("v001-core.jsonl", 4, "scope_weights", '{"user":"heavy"}', 1),
        ("v003-org.jsonl", 1, "members", "agent-a", 3),
        ("v003-org.jsonl", 2, "members", [1], 3),
    ],
)
def test_schemas_reject_other_persistence_unsafe_record_values(
    filename: str,
    line_index: int,
    field: str,
    invalid_value: object,
    version: int,
) -> None:
    record = dict(_fixture_lines(filename)[line_index])
    record[field] = invalid_value

    assert not _schema_conformance().is_valid(record, SCHEMA_IDS[version])


@pytest.mark.parametrize(
    ("contract_version", "filename", "line_index", "field", "invalid_value"),
    [
        (1, "v001-core.jsonl", 1, "visibility", "not-json"),
        (1, "v001-core.jsonl", 3, "relation", "not-a-relation"),
        (1, "v001-core.jsonl", 4, "type_weights", "[]"),
        (1, "v003-org.jsonl", 1, "members", "agent-a"),
    ],
)
def test_codec_rejects_persistence_unsafe_recognized_records(
    contract_version: int,
    filename: str,
    line_index: int,
    field: str,
    invalid_value: object,
) -> None:
    record = dict(_fixture_lines(filename)[line_index])
    record[field] = invalid_value

    with pytest.raises(ValueError, match=field):
        decode_record(contract_for(contract_version), record)


def test_conformance_resolves_every_declared_reference() -> None:
    resources: dict[str, Any] = deepcopy(dict(load_schema_resources()))
    resources[SCHEMA_IDS[1]]["$defs"]["memory"]["properties"]["acl_updated_at"][
        "$ref"
    ] = (
        "urn:agent-memory-os:sync-bundle:missing#/$defs/memory"
    )

    with pytest.raises(AssertionError, match="unresolved schema resource"):
        _SchemaConformance(resources)


def test_conformance_fails_loudly_on_new_schema_vocabulary() -> None:
    resources: dict[str, Any] = deepcopy(dict(load_schema_resources()))
    resources[SCHEMA_IDS[1]]["$defs"]["memory"]["properties"]["content"][
        "pattern"
    ] = ".+"

    with pytest.raises(AssertionError, match="unsupported schema keywords"):
        _SchemaConformance(resources)


@pytest.mark.parametrize(
    ("version", "filename"),
    _FIXTURE_CASES,
)
def test_declared_schemas_accept_version_fixtures(
    version: int,
    filename: str,
) -> None:
    lines = _fixture_lines(filename)
    conformance = _schema_conformance()

    assert version in SUPPORTED_BUNDLE_VERSIONS
    assert lines[0] == {"kind": "bundle", "version": version}
    assert all(
        conformance.is_valid(line, SCHEMA_IDS[version])
        for line in lines
    )


def test_every_supported_version_has_conformance_fixture() -> None:
    assert {version for version, _ in _FIXTURE_CASES} == SUPPORTED_BUNDLE_VERSIONS


def test_every_promised_record_kind_has_a_schema_witness() -> None:
    records = {
        record["kind"]: record
        for _, filename in _FIXTURE_CASES
        for record in _fixture_lines(filename)[1:]
    }
    conformance = _schema_conformance()
    for version, contract in CONTRACTS.items():
        assert contract.import_record_kinds <= records.keys()
        assert all(
            conformance.is_valid(records[kind], SCHEMA_IDS[version])
            for kind in contract.import_record_kinds
        )


@pytest.mark.parametrize("version", sorted(SUPPORTED_BUNDLE_VERSIONS))
def test_schema_applies_manifest_unknown_record_policy(version: int) -> None:
    unknown_record = {"kind": "from_the_future", "value": 1}

    assert (
        _schema_conformance().is_valid(unknown_record, SCHEMA_IDS[version])
        is contract_for(version).allow_unknown_record_kinds
    )


def test_earlier_profiles_do_not_promise_later_known_record_kinds() -> None:
    conformance = _schema_conformance()

    assert not conformance.is_valid(
        {"kind": "tombstone", "id": "m", "deleted_at": "legacy"},
        SCHEMA_IDS[1],
    )
    assert not conformance.is_valid(
        {
            "kind": "team",
            "id": "t",
            "name": "Team",
            "updated_at": "legacy",
            "members": [],
        },
        SCHEMA_IDS[2],
    )


@pytest.mark.parametrize(
    ("version", "kind"),
    [
        (version, kind)
        for version, contract in CONTRACTS.items()
        for kind in sorted(contract.import_record_kinds)
    ],
)
def test_import_profiles_reject_malformed_promised_records(
    version: int,
    kind: str,
) -> None:
    malformed = {"kind": kind}

    assert not _schema_conformance().is_valid(malformed, SCHEMA_IDS[version])


@pytest.mark.parametrize(
    "version",
    [
        version
        for version, contract in CONTRACTS.items()
        if contract_for(1).import_record_kinds <= contract.import_record_kinds
    ],
)
def test_v1_core_shapes_import_under_every_promising_version(
    tmp_path: Path,
    version: int,
) -> None:
    client = MemoryClient(home=tmp_path / f"core-v{version}")
    bundle = _fixture_with_version(tmp_path, "v001-core.jsonl", version)

    stats = client.import_bundle(bundle)
    stored = client.store.conn.execute(
        "SELECT updated_at FROM recall_profiles WHERE agent_id = ?",
        ("legacy-v1",),
    ).fetchone()

    assert stats["memories_added"] == 2
    assert stats["links_added"] == 1
    assert stats["profiles_upserted"] == 1
    assert stored is not None
    assert stored[0] == "2026-01-01T01:00:00+01:00"
    assert client.store.conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0] == 1
    client.close()


@pytest.mark.parametrize(
    "version",
    [
        version
        for version, contract in CONTRACTS.items()
        if "tombstone" in contract.import_record_kinds
    ],
)
def test_tombstone_imports_under_every_promising_version(
    tmp_path: Path,
    version: int,
) -> None:
    client = MemoryClient(home=tmp_path / f"tombstone-v{version}")
    bundle = _fixture_with_version(tmp_path, "v002-tombstone.jsonl", version)

    client.import_bundle(bundle)

    assert client.store.tombstone_for("mem_deleted_v2") == "2026-01-01T01:00:00+01:00"
    client.close()


@pytest.mark.parametrize(
    "version",
    [
        version
        for version, contract in CONTRACTS.items()
        if {"team", "project", "org_tombstone"} <= contract.import_record_kinds
    ],
)
def test_organization_shapes_import_with_timestamp_text_preserved(
    tmp_path: Path,
    version: int,
) -> None:
    client = MemoryClient(home=tmp_path / f"v{version}")
    client.store.register_agent("agent-a")

    bundle = _fixture_with_version(tmp_path, "v003-org.jsonl", version)
    stats = client.import_bundle(bundle)
    stored = client.store.conn.execute(
        "SELECT updated_at FROM teams WHERE id = ?",
        ("legacy-v3",),
    ).fetchone()
    team = client.store.get_team("legacy-v3")
    project = client.store.get_project("legacy-project")
    tombstone = client.store.conn.execute(
        "SELECT deleted_at FROM org_tombstones WHERE kind = ? AND id = ?",
        ("project", "retired-project"),
    ).fetchone()

    assert stats["teams_upserted"] == 1
    assert stats["projects_upserted"] == 1
    assert stored is not None
    assert stored[0] == "2026-01-01T01:00:00+01:00"
    assert team is not None
    assert team["members"] == ["agent-a"]
    assert project is not None
    assert project["members"] == ["agent-a"]
    assert tombstone is not None
    assert tombstone[0] == "2026-01-01T01:00:00+01:00"
    client.close()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("visibility", "not-json"),
        ("decay_policy", "not-a-policy"),
    ],
)
def test_invalid_memory_record_rolls_back_before_poisoning_store(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    client = MemoryClient(home=tmp_path / field)
    local = client.add("local survivor", visibility=["global"])
    records = [dict(_fixture_lines("v001-core.jsonl")[index]) for index in (1, 2)]
    records[0]["id"] = f"valid-before-{field}"
    records[1]["id"] = f"invalid-{field}"
    records[1][field] = invalid_value
    bundle = tmp_path / f"invalid-{field}.jsonl"
    bundle.write_text(
        "\n".join(
            json.dumps(value)
            for value in [
                {"kind": "bundle", "version": 1},
                *records,
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=field):
        client.import_bundle(bundle)

    assert client.get(records[0]["id"]) is None
    assert client.get(records[1]["id"]) is None
    assert client.get(local.id) is not None
    client.export_bundle(
        tmp_path / f"safe-after-{field}.jsonl",
        include_private=False,
    )
    client.close()


@pytest.mark.parametrize("configured_half_life", [0.0, 365.0])
def test_none_decay_bundle_round_trips_through_provisional_zero_sentinel(
    tmp_path: Path,
    configured_half_life: float,
) -> None:
    source = MemoryClient(home=tmp_path / f"source-{configured_half_life}")
    record = source.add(
        "non-decaying",
        visibility=["global"],
        decay_policy="none",
        decay_half_life_days=configured_half_life,
    )
    bundle = tmp_path / f"none-{configured_half_life}.jsonl"

    source.export_bundle(bundle)

    exported = [
        json.loads(line)
        for line in bundle.read_text(encoding="utf-8").splitlines()
    ]
    exported_memory = next(line for line in exported if line.get("kind") == "memory")
    target = MemoryClient(home=tmp_path / f"target-{configured_half_life}")
    target.import_bundle(bundle)

    assert source.get(record.id).decay_half_life_days == configured_half_life
    assert exported_memory["decay_half_life_days"] == 0.0
    assert target.get(record.id).decay_half_life_days == 0.0
    source.close()
    target.close()


def test_export_uses_registry_current_version(tmp_path: Path) -> None:
    client = MemoryClient(home=tmp_path / "export")
    target = tmp_path / "bundle.jsonl"

    client.export_bundle(target)
    header = json.loads(target.read_text(encoding="utf-8").splitlines()[0])

    assert header["version"] == CURRENT_BUNDLE_VERSION
    client.close()

# AgentMemoryOS sync bundles

## Authority

The normative sync-bundle registry is
[`contract.json`](../src/agent_memory_os/sync_bundles/contract.json#L1-L41).
It declares the current writer version, supported import versions, schema
resource for each version, promised record kinds, and unknown-record policy.
The referenced JSON Schema resources define the promised line shapes, including
the decoded shapes of JSON text fields. The codec enforces the
persistence-critical parts of those shapes before merge.

This document explains that contract and its implementation context. If a
registry statement here differs from `contract.json`, the JSON contract
governs.

## Scope

A sync bundle is UTF-8 newline-delimited JSON. The first line is one bundle
header and each following line is one bundle record. The bundle version is
independent of the database schema version and the AgentMemoryOS package
version.

Each registered version is a first-class import promise. The declared record
kinds describe the shapes AgentMemoryOS promises to import for that version;
they are not an exhaustive rejection policy for every input the existing
merger may tolerate. The contract records that all supported versions retain
unknown-record tolerance
([versions 1–3](../src/agent_memory_os/sync_bundles/contract.json#L5-L39)).
Unknown kinds remain ignored. Recognized records with malformed JSON-backed
fields, invalid runtime metadata, or malformed authoritative member lists are
rejected before any bundle mutation commits; accepting them would persist
unreadable rows or turn malformed membership into deletion.

AgentMemoryOS imports every registered supported version and emits only the
current writer version
([registry versions](../src/agent_memory_os/sync_bundles/contract.json#L2-L3)).
The JSON registry replaces the former internal
`agent_memory_os.sync.BUNDLE_VERSION` constant as the writer-version authority
([current writer](../src/agent_memory_os/sync_bundles/contract.json#L2)).

The JSON Schema resources describe individual lines in each promised import
profile. The Python codec owns version selection and the encode/decode seam.
It validates persistence-critical record shapes and applies only the
version-owned normalization stated below before merge. This contract
foundation performs no timestamp normalization or conversion. See the
[`decode_record`](../src/agent_memory_os/sync_bundles/codec.py) and
[`encode_record`](../src/agent_memory_os/sync_bundles/codec.py)
implementations.

## Version history

### Version 1

Version 1 was introduced by `512e2197`. Its promised record kinds are
`memory`, `link`, and `profile`
([version 1 contract](../src/agent_memory_os/sync_bundles/contract.json#L5-L14),
[version 1 schema](../src/agent_memory_os/sync_bundles/schemas/v001/bundle.schema.json)).

**Provisional half-life sentinel:** When `decay_policy` is `none`, bundle
codecs normalize `decay_half_life_days` to `0.0`. This represents disabled
decay, not a final duration encoding. Revisit this rule during the planned
date/time/timedelta/datetime canonicalization work, including the
representation of "not applicable," `decay_base_half_life_days`, and
transitions back to an active decay policy.

### Version 2

Version 2 was introduced by `06cb42f7`. It retains the version 1 shapes and
adds `tombstone`
([version 2 contract](../src/agent_memory_os/sync_bundles/contract.json#L15-L25),
[version 2 schema](../src/agent_memory_os/sync_bundles/schemas/v002/bundle.schema.json)).

### Version 3

Version 3 was introduced by `7ebc3daf`. It retains the version 2 shapes and
adds `team`, `project`, and `org_tombstone`
([version 3 contract](../src/agent_memory_os/sync_bundles/contract.json#L26-L39),
[version 3 schema](../src/agent_memory_os/sync_bundles/schemas/v003/bundle.schema.json)).

The current version 3 producer includes `acl_updated_at` on memory records.
Older bundles may omit it; the existing merge path continues to fall back to
`updated_at` when it is absent.

## Boundary

The normative registry defines versions 1 through 3 only
([supported versions](../src/agent_memory_os/sync_bundles/contract.json#L2-L3)).
It does not define a later bundle version, a bundle converter, canonical
timestamp requirements, or ordering repairs. The persistence-safety checks do
not reinterpret legacy timestamp text. Any incompatible wire requirement is a
separate contract decision and must not redefine an existing version.

from __future__ import annotations

import json
import math
from typing import Any, overload

from ..schema import VALID_LINK_RELATIONS
from ..scoring import VALID_DECAY_POLICIES
from .contract import BundleContract
from .registry import contract_for


def _record_error(
    contract: BundleContract,
    kind: str,
    field: str,
    detail: str,
) -> ValueError:
    return ValueError(
        f"bundle v{contract.version} {kind}.{field} {detail}"
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


@overload
def _json_text(
    contract: BundleContract,
    kind: str,
    value: dict[str, Any],
    field: str,
    expected_type: type[list],
) -> list[Any]: ...


@overload
def _json_text(
    contract: BundleContract,
    kind: str,
    value: dict[str, Any],
    field: str,
    expected_type: type[dict],
) -> dict[str, Any]: ...


def _json_text(
    contract: BundleContract,
    kind: str,
    value: dict[str, Any],
    field: str,
    expected_type: type[list | dict],
) -> list[Any] | dict[str, Any]:
    raw = value.get(field)
    if not isinstance(raw, str):
        raise _record_error(contract, kind, field, "must be JSON text")
    try:
        decoded = json.loads(raw, parse_constant=_reject_json_constant)
    except (TypeError, ValueError) as exc:
        raise _record_error(contract, kind, field, "must contain valid JSON") from exc
    if not isinstance(decoded, expected_type):
        expected_name = "array" if expected_type is list else "object"
        raise _record_error(
            contract,
            kind,
            field,
            f"must contain a JSON {expected_name}",
        )
    return decoded


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _non_negative_integer(
    contract: BundleContract,
    kind: str,
    value: dict[str, Any],
    field: str,
) -> None:
    candidate = _number(value.get(field))
    if candidate is None or candidate < 0 or not candidate.is_integer():
        raise _record_error(
            contract,
            kind,
            field,
            "must be a non-negative integer",
        )


def _validate_memory(
    contract: BundleContract,
    value: dict[str, Any],
) -> None:
    kind = "memory"
    for field in ("tags", "visibility"):
        items = _json_text(contract, kind, value, field, list)
        if any(not isinstance(item, str) or not item for item in items):
            raise _record_error(
                contract,
                kind,
                field,
                "must contain an array of non-empty strings",
            )
    _json_text(contract, kind, value, "source", dict)

    for field in ("confidence", "importance"):
        number = _number(value.get(field))
        if number is None or not 0.0 <= number <= 1.0:
            raise _record_error(
                contract,
                kind,
                field,
                "must be a finite number between 0 and 1",
            )

    policy = value.get("decay_policy")
    if policy not in VALID_DECAY_POLICIES:
        raise _record_error(
            contract,
            kind,
            "decay_policy",
            f"must be one of {sorted(VALID_DECAY_POLICIES)}",
        )
    half_life = _number(value.get("decay_half_life_days"))
    if half_life is None:
        raise _record_error(
            contract,
            kind,
            "decay_half_life_days",
            "must be a finite number",
        )
    if policy == "none":
        value["decay_half_life_days"] = 0.0
    elif half_life <= 0:
        raise _record_error(
            contract,
            kind,
            "decay_half_life_days",
            "must be positive when decay is active",
        )
    for field in (
        "access_count",
        "helpful_count",
        "unhelpful_count",
    ):
        _non_negative_integer(contract, kind, value, field)
    pinned = _number(value.get("pinned"))
    if pinned not in (0.0, 1.0):
        raise _record_error(contract, kind, "pinned", "must be 0 or 1")


def _validate_link(
    contract: BundleContract,
    value: dict[str, Any],
) -> None:
    kind = "link"
    src_id, dst_id = value.get("src_id"), value.get("dst_id")
    if not isinstance(src_id, str) or not src_id:
        raise _record_error(contract, kind, "src_id", "must be a non-empty string")
    if not isinstance(dst_id, str) or not dst_id:
        raise _record_error(contract, kind, "dst_id", "must be a non-empty string")
    if src_id == dst_id:
        raise _record_error(contract, kind, "dst_id", "must differ from src_id")
    if value.get("relation") not in VALID_LINK_RELATIONS:
        raise _record_error(
            contract,
            kind,
            "relation",
            f"must be one of {sorted(VALID_LINK_RELATIONS)}",
        )
    weight = _number(value.get("weight"))
    if weight is None or not 0.0 <= weight <= 1.0:
        raise _record_error(
            contract,
            kind,
            "weight",
            "must be a finite number between 0 and 1",
        )
    _non_negative_integer(contract, kind, value, "activation_count")
    _json_text(contract, kind, value, "source", dict)


def _validate_profile(
    contract: BundleContract,
    value: dict[str, Any],
) -> None:
    kind = "profile"
    for field in ("type_weights", "scope_weights"):
        weights = _json_text(contract, kind, value, field, dict)
        if any(_number(weight) is None for weight in weights.values()):
            raise _record_error(
                contract,
                kind,
                field,
                "must contain an object with finite numeric values",
            )


def _validate_members(
    contract: BundleContract,
    kind: str,
    value: dict[str, Any],
) -> None:
    members = value.get("members")
    if not isinstance(members, list) or any(
        not isinstance(member, str) or not member
        for member in members
    ):
        raise _record_error(
            contract,
            kind,
            "members",
            "must be an array of non-empty strings",
        )


def _validate_record(
    contract: BundleContract,
    value: dict[str, Any],
) -> None:
    kind = value.get("kind")
    if kind == "memory":
        _validate_memory(contract, value)
    elif kind == "link":
        _validate_link(contract, value)
    elif kind == "profile":
        _validate_profile(contract, value)
    elif kind in {"team", "project"}:
        _validate_members(contract, kind, value)


def decode_header(value: object) -> tuple[BundleContract, dict[str, Any]]:
    """Resolve the header contract without adding new header policy."""

    if not isinstance(value, dict) or value.get("kind") != "bundle":
        raise ValueError("not a compatible agent-memory-os bundle")
    try:
        contract = contract_for(value.get("version"))
    except ValueError:
        raise ValueError("not a compatible agent-memory-os bundle") from None
    return contract, dict(value)


def encode_header(
    contract: BundleContract,
    *,
    node_name: str = "",
) -> dict[str, Any]:
    """Build a header for the selected writer contract."""

    header: dict[str, Any] = {"kind": "bundle", "version": contract.version}
    if node_name:
        header["node_name"] = node_name
    return header


def decode_record(
    contract: BundleContract,
    value: object,
) -> dict[str, Any]:
    """Validate persistence-critical shapes and apply version-owned normalization.

    ``contract.import_record_kinds`` describes the shapes whose import is
    promised for this version. It is not an exhaustive runtime allowlist, so
    unknown record kinds retain the existing ignore-on-import behavior.
    """

    if not isinstance(value, dict):
        raise TypeError(f"bundle v{contract.version} record must be a JSON object")
    decoded = dict(value)
    _validate_record(contract, decoded)
    return decoded


def encode_record(
    contract: BundleContract,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Apply version-owned normalization to a current-writer record."""

    if not isinstance(value, dict):
        raise TypeError(f"bundle v{contract.version} record must be a JSON object")
    encoded = dict(value)
    if encoded.get("kind") == "memory" and encoded.get("decay_policy") == "none":
        encoded["decay_half_life_days"] = 0.0
    return encoded

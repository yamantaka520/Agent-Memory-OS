"""Shadow-mode recall comparison utilities for AgentMemoryOS.

The monitor records legacy-vs-candidate recall comparisons as JSONL so the
v0.3 -> v0.4 migration can collect KPI evidence while legacy memory remains the
primary response source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Any

from .constants import (
    SHADOW_P99_LATENCY_PAUSE_MILLISECONDS,
    SHADOW_P99_LATENCY_TARGET_MILLISECONDS,
)


@dataclass(frozen=True)
class ShadowModePolicy:
    """Acceptance thresholds from ``Shadow_Mode_Timeline.md``."""

    phase: str = "Phase 1: Silent Mirroring"
    recall_target: float = 0.95
    p99_latency_target_ms: float = SHADOW_P99_LATENCY_TARGET_MILLISECONDS
    p99_latency_pause_ms: float = SHADOW_P99_LATENCY_PAUSE_MILLISECONDS


class ShadowRecallMonitor:
    """Append-only recorder for legacy/candidate recall comparisons."""

    def __init__(self, *, log_path: str | Path, policy: ShadowModePolicy | None = None) -> None:
        self.log_path = Path(log_path)
        self.policy = policy or ShadowModePolicy()

    def compare_recall(
        self,
        *,
        query: str,
        legacy_results: Iterable[str],
        candidate_results: Iterable[str],
        legacy_latency_ms: float,
        candidate_latency_ms: float,
        acl_leakage: bool = False,
    ) -> dict[str, Any]:
        """Compare top-k result overlap and persist one shadow-mode record."""

        legacy = list(legacy_results)
        candidate = list(candidate_results)
        top_k_hit_rate = _top_k_hit_rate(legacy, candidate)
        latency_delta_ms = round(float(candidate_latency_ms) - float(legacy_latency_ms), 3)
        go_no_go = self._go_no_go(top_k_hit_rate, float(candidate_latency_ms), acl_leakage)

        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": self.policy.phase,
            "query": query,
            "legacy_count": len(legacy),
            "candidate_count": len(candidate),
            "top_k_hit_rate": top_k_hit_rate,
            "legacy_latency_ms": float(legacy_latency_ms),
            "candidate_latency_ms": float(candidate_latency_ms),
            "latency_delta_ms": latency_delta_ms,
            "acl_zero_leakage": not acl_leakage,
            "go_no_go": go_no_go,
        }
        self._append(record)
        return record

    def summarize(self, *, last_n: int | None = None) -> dict[str, Any]:
        """Summarize KPI status from the JSONL log."""

        return summarize_shadow_log(self.log_path, policy=self.policy, last_n=last_n)

    def _go_no_go(self, hit_rate: float, candidate_latency_ms: float, acl_leakage: bool) -> str:
        if acl_leakage:
            return "NO_GO_ACL_LEAKAGE"
        if candidate_latency_ms > self.policy.p99_latency_pause_ms:
            return "NO_GO_LATENCY_PAUSE"
        if hit_rate < self.policy.recall_target:
            return "WATCH_RECALL_BELOW_TARGET"
        return "GO"

    def _append(self, record: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_records(self) -> Iterable[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        records = []
        with self.log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


def summarize_shadow_log(
    log_path: str | Path,
    *,
    policy: ShadowModePolicy | None = None,
    last_n: int | None = None,
) -> dict[str, Any]:
    """Build an audit-ready evidence pack from shadow JSONL records.

    The summary is intentionally derived only from persisted records so reviewers
    can audit shadow evidence without executing candidate retrieval again.
    """

    policy = policy or ShadowModePolicy()
    path = Path(log_path)
    all_records = _read_jsonl_records(path)
    total_records = len(all_records)
    records = all_records[-last_n:] if last_n and last_n > 0 else all_records
    if not records:
        return {
            "log_path": str(path),
            "records": 0,
            "total_records": total_records,
            "window_records": 0,
            "phase": policy.phase,
            "activation_gate": "NO_GO_NO_EVIDENCE",
            "mean_top_k_hit_rate": 0.0,
            "p50_candidate_latency_ms": 0.0,
            "p95_candidate_latency_ms": 0.0,
            "p99_candidate_latency_ms": 0.0,
            "no_go_count": 0,
            "acl_leakage_count": 0,
            "production_injection_count": 0,
            "profile_distribution": {},
            "query_distribution": {},
            "go_no_go_distribution": {},
            "import_totals": {"scanned": 0, "inserted": 0, "updated": 0, "skipped": 0},
            "latest": None,
        }

    latencies = sorted(float(record.get("candidate_latency_ms", 0.0)) for record in records)
    hit_rates = [float(record.get("top_k_hit_rate", 0.0)) for record in records]
    go_no_go_values = [str(record.get("go_no_go", "UNKNOWN")) for record in records]
    no_go_count = sum(1 for value in go_no_go_values if value.startswith("NO_GO"))
    acl_leakage_count = sum(1 for record in records if record.get("acl_zero_leakage") is False)
    production_injection_count = sum(1 for record in records if record.get("production_injection") is True)
    profile_distribution = Counter(str(record.get("profile", "unknown")) for record in records)
    query_distribution = Counter(str(record.get("query", "")) for record in records)
    import_totals = _sum_import_reports(records)

    p99_latency = _nearest_rank_percentile(latencies, 0.99)
    mean_hit_rate = round(mean(hit_rates), 3)
    activation_gate = _activation_gate(
        mean_hit_rate=mean_hit_rate,
        p99_latency_ms=p99_latency,
        no_go_count=no_go_count,
        acl_leakage_count=acl_leakage_count,
        production_injection_count=production_injection_count,
        policy=policy,
    )

    return {
        "log_path": str(path),
        "records": len(records),
        "total_records": total_records,
        "window_records": len(records),
        "phase": policy.phase,
        "activation_gate": activation_gate,
        "mean_top_k_hit_rate": mean_hit_rate,
        "min_top_k_hit_rate": round(min(hit_rates), 3),
        "p50_candidate_latency_ms": _round_number(median(latencies)),
        "p95_candidate_latency_ms": _nearest_rank_percentile(latencies, 0.95),
        "p99_candidate_latency_ms": p99_latency,
        "max_candidate_latency_ms": _round_number(max(latencies)),
        "no_go_count": no_go_count,
        "acl_leakage_count": acl_leakage_count,
        "production_injection_count": production_injection_count,
        "profile_distribution": dict(sorted(profile_distribution.items())),
        "query_distribution": dict(query_distribution.most_common()),
        "go_no_go_distribution": dict(Counter(go_no_go_values).most_common()),
        "import_totals": import_totals,
        "latest": records[-1],
    }


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _sum_import_reports(records: list[dict[str, Any]]) -> dict[str, int]:
    totals = defaultdict(int)
    for record in records:
        report = record.get("import_report")
        if not isinstance(report, dict):
            continue
        for key in ("scanned", "inserted", "updated", "skipped"):
            totals[key] += int(report.get(key, 0) or 0)
    return {key: int(totals[key]) for key in ("scanned", "inserted", "updated", "skipped")}


def _activation_gate(
    *,
    mean_hit_rate: float,
    p99_latency_ms: float,
    no_go_count: int,
    acl_leakage_count: int,
    production_injection_count: int,
    policy: ShadowModePolicy,
) -> str:
    if production_injection_count:
        return "NO_GO_PRODUCTION_INJECTION_DETECTED"
    if acl_leakage_count:
        return "NO_GO_ACL_LEAKAGE"
    if no_go_count:
        return "NO_GO_HAS_BLOCKING_RECORDS"
    if p99_latency_ms > policy.p99_latency_pause_ms:
        return "NO_GO_LATENCY_PAUSE"
    if p99_latency_ms > policy.p99_latency_target_ms:
        return "WATCH_LATENCY_ABOVE_TARGET"
    if mean_hit_rate < policy.recall_target:
        return "WATCH_RECALL_BELOW_TARGET"
    return "GO"


def _top_k_hit_rate(legacy_results: list[str], candidate_results: list[str]) -> float:
    if not legacy_results:
        return 1.0 if not candidate_results else 0.0
    legacy_norm = {_normalize_result(result) for result in legacy_results}
    candidate_norm = {_normalize_result(result) for result in candidate_results}
    return round(len(legacy_norm & candidate_norm) / len(legacy_norm), 3)


def _normalize_result(result: str) -> str:
    return " ".join(result.casefold().split())


def _nearest_rank_percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(
        0,
        min(len(sorted_values) - 1, int(percentile * len(sorted_values) + 0.999999) - 1),
    )
    return _round_number(sorted_values[index])


def _nearest_rank_p99(sorted_values: list[float]) -> float:
    # Backward-compatible helper used by older tests/callers.
    return _nearest_rank_percentile(sorted_values, 0.99)


def _round_number(value: float) -> float | int:
    value = round(float(value), 3)
    return int(value) if value.is_integer() else value

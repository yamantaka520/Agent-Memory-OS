"""Prototype graph-neural memory resonance primitives.

This module implements an embedded ERA (Entity-Relation-Attribute)
triplet index for AgentMemoryOS v0.4 experiments.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
import re
import time
from typing import DefaultDict, Iterable

from .constants import (
    ERA_PROTOTYPE_DECAY_LAMBDA_PER_SECOND,
    ERA_PROTOTYPE_DEFAULT_HOPS,
    ERA_PROTOTYPE_INVALID_TIMESTAMP_FALLBACK_SECONDS,
    ERA_PROTOTYPE_LINK_MAX_TERM_DEGREE,
    ERA_PROTOTYPE_LINK_MAX_WEIGHT,
    ERA_PROTOTYPE_LINK_MIN_SHARED_TERMS,
    ERA_PROTOTYPE_LINK_SHARED_TERMS_FOR_MAX_WEIGHT,
    ERA_PROTOTYPE_MIN_WEIGHT,
)

_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_.-]*\b")
_VERSION_RE = re.compile(r"\bv\d+(?:\.\d+)+\b", re.IGNORECASE)
_USES_RE = re.compile(
    r"\b(?P<subject>[A-Z][A-Za-z0-9_.-]*)\s+uses\s+"
    r"(?P<object>[A-Z][A-Za-z0-9_.-]*)\b",
    re.IGNORECASE,
)
_EVOLVES_RE = re.compile(
    r"\b(?P<subject>[A-Z][A-Za-z0-9_.-]*)\s+evolves\s+from\s+"
    r"(?P<source>v\d+(?:\.\d+)+)\s+to\s+(?P<target>v\d+(?:\.\d+)+)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "and",
    "for",
    "from",
    "mode",
    "the",
    "to",
    "topic",
    "uses",
    "with",
}

@dataclass(frozen=True)
class MemoryChunk:
    """A memory unit that can be projected into the resonance graph.

    `timestamp` accepts either an ISO string (used verbatim in the timestamp
    triplet) or a Unix float for ResonanceWeight decay experiments.
    """

    id: str
    text: str
    timestamp: str | float = ""


class ResonanceWeight:
    """Temporal decay for resonance strength (prototype).

    Formula: strength = max(min_weight, base * exp(-lambda * delta_t)).
    """

    @staticmethod
    def calculate(base_strength: float, timestamp: float, current_time: float | None = None) -> float:
        if current_time is None:
            current_time = time.time()
        try:
            stamp = float(timestamp)
        except (TypeError, ValueError):
            stamp = ERA_PROTOTYPE_INVALID_TIMESTAMP_FALLBACK_SECONDS
        delta_t = max(0.0, current_time - stamp)
        # Decay constant: reduced from 0.00000133 to 0.0000008 to mitigate recall drop
        decay_lambda = ERA_PROTOTYPE_DECAY_LAMBDA_PER_SECOND
        decay_factor = math.exp(-decay_lambda * delta_t)
        # Weight floor prevents total resonance collapse for old chunks
        min_weight = ERA_PROTOTYPE_MIN_WEIGHT
        return max(min_weight, base_strength * decay_factor)


class ERATripletIndex:
    """Embedded ERA triplet index with two-hop resonance expansion.

    The prototype stores memory chunks, extracts simple ERA triplets, and links
    chunks through shared entities/concepts. It is the v0.4 bootstrap before a
    production graph backend is introduced.
    """

    def __init__(self) -> None:
        self._chunks: dict[str, MemoryChunk] = {}
        self._triplets_by_chunk: DefaultDict[str, set[tuple[str, str, str]]] = defaultdict(set)
        self._terms_by_chunk: DefaultDict[str, set[str]] = defaultdict(set)
        self._chunks_by_term: DefaultDict[str, set[str]] = defaultdict(set)

    def add_chunk(self, chunk: MemoryChunk) -> None:
        """Add or replace a chunk and index its ERA terms."""
        if not chunk.id:
            raise ValueError("MemoryChunk.id must be non-empty")

        self._remove_chunk_terms(chunk.id)
        self._chunks[chunk.id] = chunk

        triplets = self._extract_triplets(chunk)
        terms = self._extract_terms(chunk.text)
        terms.update(_normalize(value) for triplet in triplets for value in triplet)
        terms.discard("")

        self._triplets_by_chunk[chunk.id] = triplets
        self._terms_by_chunk[chunk.id] = terms
        for term in terms:
            self._chunks_by_term[term].add(chunk.id)

    def triplets_for_chunk(self, chunk_id: str) -> set[tuple[str, str, str]]:
        """Return extracted ERA triplets for a chunk."""
        return set(self._triplets_by_chunk.get(chunk_id, set()))

    def resonance_cluster(self, seed_chunk_ids: Iterable[str], *, hops: int = ERA_PROTOTYPE_DEFAULT_HOPS) -> list[str]:
        """Expand seed chunks through shared ERA terms and rank the cluster.

        Ranking is deterministic: seeds first, then closer graph distance, then
        stronger term overlap with the seed set, then chunk id. ResonanceWeight
        temporal decay stays an opt-in experiment; the cluster contract must
        not depend on wall-clock time.
        """
        seeds = [chunk_id for chunk_id in seed_chunk_ids if chunk_id in self._chunks]
        if hops < 0:
            raise ValueError("hops must be >= 0")
        if not seeds:
            return []

        seed_terms = set().union(*(self._terms_by_chunk[seed] for seed in seeds))
        distances: dict[str, int] = {seed: 0 for seed in seeds}
        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)

        while queue:
            chunk_id, distance = queue.popleft()
            if distance >= hops:
                continue
            for neighbor in self._neighbors(chunk_id):
                if neighbor in distances:
                    continue
                distances[neighbor] = distance + 1
                queue.append((neighbor, distance + 1))

        return sorted(
            distances,
            key=lambda chunk_id: (
                distances[chunk_id],
                -len(self._terms_by_chunk[chunk_id] & seed_terms),
                chunk_id,
            ),
        )

    def derive_links(
        self,
        *,
        min_shared_terms: int = ERA_PROTOTYPE_LINK_MIN_SHARED_TERMS,
        max_term_degree: int = ERA_PROTOTYPE_LINK_MAX_TERM_DEGREE,
    ) -> list[tuple[str, str, float]]:
        """Derive weak association edges from shared ERA terms between chunks.

        This is the bridge from the disposable ERA index into the authoritative
        `memory_links` layer: pairs sharing at least `min_shared_terms`
        non-hub terms become (src_id, dst_id, weight) tuples suitable for
        `MemoryClient.import_links`. Terms appearing in more than
        `max_term_degree` chunks are treated as hubs and skipped so common
        vocabulary does not link everything to everything.
        """
        shared_counts: DefaultDict[tuple[str, str], int] = defaultdict(int)
        for chunk_ids in self._chunks_by_term.values():
            if len(chunk_ids) < 2 or len(chunk_ids) > max_term_degree:
                continue
            ordered = sorted(chunk_ids)
            for i, src_id in enumerate(ordered):
                for dst_id in ordered[i + 1:]:
                    shared_counts[(src_id, dst_id)] += 1
        return [
            (src_id, dst_id, min(ERA_PROTOTYPE_LINK_MAX_WEIGHT, shared / ERA_PROTOTYPE_LINK_SHARED_TERMS_FOR_MAX_WEIGHT))
            for (src_id, dst_id), shared in sorted(shared_counts.items())
            if shared >= min_shared_terms
        ]

    def _neighbors(self, chunk_id: str) -> set[str]:
        neighbors: set[str] = set()
        for term in self._terms_by_chunk.get(chunk_id, set()):
            neighbors.update(self._chunks_by_term.get(term, set()))
        neighbors.discard(chunk_id)
        return neighbors

    def _remove_chunk_terms(self, chunk_id: str) -> None:
        for term in self._terms_by_chunk.get(chunk_id, set()):
            chunk_ids = self._chunks_by_term.get(term)
            if not chunk_ids:
                continue
            chunk_ids.discard(chunk_id)
            if not chunk_ids:
                self._chunks_by_term.pop(term, None)
        self._terms_by_chunk.pop(chunk_id, None)
        self._triplets_by_chunk.pop(chunk_id, None)

    def _extract_triplets(self, chunk: MemoryChunk) -> set[tuple[str, str, str]]:
        triplets: set[tuple[str, str, str]] = set()
        for match in _USES_RE.finditer(chunk.text):
            triplets.add((match.group("subject"), "uses", match.group("object")))
        primary_entity = self._primary_entity(chunk.text)
        for match in _EVOLVES_RE.finditer(chunk.text):
            subject = match.group("subject")
            if _normalize(subject) in _STOPWORDS and primary_entity:
                subject = primary_entity
            triplets.add((subject, "evolves_from", match.group("source")))
            triplets.add((subject, "evolves_to", match.group("target")))
        if chunk.timestamp:
            subject = self._primary_entity(chunk.text)
            if subject:
                triplets.add((subject, "timestamp", str(chunk.timestamp)))
        return triplets

    def _primary_entity(self, text: str) -> str:
        for token in _TOKEN_RE.findall(text):
            if token[:1].isupper() and _normalize(token) not in _STOPWORDS:
                return token
        return ""

    def _extract_terms(self, text: str) -> set[str]:
        terms = {_normalize(token) for token in _TOKEN_RE.findall(text)}
        terms.update(_normalize(version) for version in _VERSION_RE.findall(text))
        return {term for term in terms if term and term not in _STOPWORDS}

def _normalize(value: str) -> str:
    return value.strip().lower()

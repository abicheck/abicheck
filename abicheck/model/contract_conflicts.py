# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADR-049/067 Workstream E slice S3: multi-source contract conflicts.

``contract_evidence_collect.py`` records what each evidence *provider* saw
(:class:`~abicheck.contract_evidence.EvidenceSearchRecord`); the evaluator
in ``contract_evaluation.py`` decides one finding's relevance from that
evidence. Neither of them answers a different, narrower question this
module exists for: two evidence sources can disagree about the very same
entity -- the public-header surface says a symbol is not part of the
contract while the export table says it plainly is; a ``--post-manifest``
overlay narrows this run's committed exports below what a stored baseline
declared public; a package's own declared metadata (a Debian ``.symbols``
file, an SONAME) names something the binary actually shipped inside that
same package disagrees with.

Per ADR-067 ("record before disposing") and ``vision.md``'s own conflict
principle, a disagreement between two evidence sources is *itself* a fact
worth keeping, with both sides' claims attached -- never silently resolved
to whichever source a particular code path happened to consult first, and
never dropped once one side is judged "more authoritative" for an unrelated
purpose (e.g. contract-mode selection). :class:`ContractSourceConflict` is
that fact: two or more :class:`ConflictSourceClaim` entries, naming which
source made which claim about which entity, plus a stable reason code.

This module holds only the *shape* (mirrors ``contract_evidence.py``'s own
split: shape here, producers in a sibling ``*_detect``/collection module).
Every dataclass is frozen and normalizes its collection fields into an
immutable form in ``__post_init__``, the same convention
``contract_evidence.py`` and ``compatibility_evaluation_config.py`` use, so a
caller's later mutation of a list/set it passed in cannot silently change an
already-constructed conflict record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

#: Case 1 (reverse of ``PUBLIC_NOT_EXPORTED``): the binary's export table
#: names a symbol the public-header evidence has no declaration for.
CONFLICT_EXPORTED_BUT_UNDECLARED = "exported_but_undeclared"
#: Case 2: a ``--post-manifest``/``--public-symbol(s)`` overlay narrows the
#: effective public surface below what a baseline (an earlier run, or this
#: same comparison's "old" side) declared public -- independent of any
#: header/code change.
CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE = "manifest_narrowed_since_baseline"
#: Case 3: package-level metadata (an SONAME, a version, a declared symbol
#: list) disagrees with the binary actually contained in the same package.
CONFLICT_PACKAGE_BINARY_MISMATCH = "package_claim_vs_binary"

#: Every conflict kind this module knows, for validation and iteration.
ALL_CONFLICT_KINDS: frozenset[str] = frozenset(
    {
        CONFLICT_EXPORTED_BUT_UNDECLARED,
        CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE,
        CONFLICT_PACKAGE_BINARY_MISMATCH,
    }
)


def _coerce_detail(value: object) -> dict[str, str]:
    """``data.get("detail")`` from a decoded JSON object, or ``{}``.

    ``value`` is typed ``object`` because it comes straight out of a
    ``Mapping[str, object]`` (a decoded JSON dict, or a hand-built one in a
    test) -- narrowed here, once, rather than at every ``from_dict`` call
    site.
    """
    if not isinstance(value, Mapping):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _frozen_str_mapping(m: Mapping[str, str] | None) -> MappingProxyType[str, str]:
    if not m:
        return MappingProxyType({})
    out: dict[str, str] = {}
    for k, v in m.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise TypeError(
                f"detail mapping keys and values must both be str, got {k!r}: {v!r}"
            )
        out[k] = v
    return MappingProxyType(out)


@dataclass(frozen=True)
class ConflictSourceClaim:
    """One evidence source's own claim about a :class:`ContractSourceConflict`'s
    subject.

    ``source_kind`` names *which* source made the claim (e.g.
    ``"public_header"``, ``"export_table"``, ``"post_manifest"``,
    ``"baseline_contract"``, ``"package_metadata"``, ``"binary"``) --
    deliberately a plain string rather than an enum shared with
    ``contract_evidence.py``'s provider vocabulary, since a conflict can cite
    a source (``"baseline_contract"``) that is not itself one of ADR-049's
    evidence providers. ``claim`` is the short, human-readable assertion
    ("exports symbol 'foo'"); ``detail`` carries any structured values (an
    observed SONAME, a version string) a report renderer wants without
    parsing ``claim``'s prose.
    """

    source_kind: str
    claim: str
    detail: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, str) or not self.source_kind:
            raise ValueError(
                f"ConflictSourceClaim.source_kind must be a non-empty str, "
                f"not {self.source_kind!r}."
            )
        if not isinstance(self.claim, str) or not self.claim:
            raise ValueError(
                f"ConflictSourceClaim.claim must be a non-empty str, not "
                f"{self.claim!r}."
            )
        object.__setattr__(self, "detail", _frozen_str_mapping(self.detail))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind,
            "claim": self.claim,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ConflictSourceClaim:
        return cls(
            source_kind=str(data["source_kind"]),
            claim=str(data["claim"]),
            detail=_coerce_detail(data.get("detail")),
        )


@dataclass(frozen=True)
class ContractSourceConflict:
    """A recorded disagreement between two (or more) evidence sources about
    one entity -- never resolved to one side, per ADR-067.

    ``conflict_kind`` is one of :data:`ALL_CONFLICT_KINDS`. ``entity`` is the
    subject the sources disagree about (a symbol name, a library/SONAME, a
    version string). ``sources`` holds every claim, in the order collected;
    at least two are required -- a "conflict" naming only one source's claim
    is not one. ``side`` is which comparison side the conflict was observed
    on (``"old"``/``"new"``), or ``None`` when the conflict is not
    side-scoped (e.g. a package-vs-binary check run once per package, not as
    part of an old/new comparison).
    """

    conflict_kind: str
    entity: str
    sources: tuple[ConflictSourceClaim, ...]
    reason_code: str
    side: str | None = None
    detail: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.conflict_kind not in ALL_CONFLICT_KINDS:
            raise ValueError(
                f"ContractSourceConflict.conflict_kind must be one of "
                f"{sorted(ALL_CONFLICT_KINDS)}, not {self.conflict_kind!r}."
            )
        if not isinstance(self.entity, str) or not self.entity:
            raise ValueError(
                f"ContractSourceConflict.entity must be a non-empty str, "
                f"not {self.entity!r}."
            )
        if isinstance(self.sources, (str, bytes)) or not isinstance(
            self.sources, Sequence
        ):
            raise TypeError(
                "ContractSourceConflict.sources must be a sequence of "
                f"ConflictSourceClaim, not {self.sources!r}."
            )
        sources = tuple(self.sources)
        if len(sources) < 2:
            raise ValueError(
                "ContractSourceConflict.sources must name at least two "
                "sources -- a conflict between fewer than two claims is not "
                f"a conflict at all (got {len(sources)} for entity "
                f"{self.entity!r})."
            )
        for s in sources:
            if not isinstance(s, ConflictSourceClaim):
                raise TypeError(
                    "ContractSourceConflict.sources elements must each be a "
                    f"ConflictSourceClaim, not {s!r}."
                )
        object.__setattr__(self, "sources", sources)
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise ValueError(
                f"ContractSourceConflict.reason_code must be a non-empty "
                f"str, not {self.reason_code!r}."
            )
        if self.side is not None and self.side not in ("old", "new"):
            raise ValueError(
                f"ContractSourceConflict.side must be 'old', 'new', or "
                f"None, not {self.side!r}."
            )
        object.__setattr__(self, "detail", _frozen_str_mapping(self.detail))

    def to_dict(self) -> dict[str, object]:
        return {
            "conflict_kind": self.conflict_kind,
            "entity": self.entity,
            "sources": [s.to_dict() for s in self.sources],
            "reason_code": self.reason_code,
            "side": self.side,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ContractSourceConflict:
        raw_sources = data.get("sources", ())
        if not isinstance(raw_sources, Sequence) or isinstance(
            raw_sources, (str, bytes)
        ):
            raw_sources = ()
        return cls(
            conflict_kind=str(data["conflict_kind"]),
            entity=str(data["entity"]),
            sources=tuple(
                ConflictSourceClaim.from_dict(s)
                for s in raw_sources
                if isinstance(s, Mapping)
            ),
            reason_code=str(data["reason_code"]),
            side=(str(data["side"]) if data.get("side") is not None else None),
            detail=_coerce_detail(data.get("detail")),
        )


def conflicts_to_dicts(
    conflicts: Sequence[ContractSourceConflict],
) -> list[dict[str, object]]:
    """Serialize a sequence of conflicts, in stable (given) order."""
    return [c.to_dict() for c in conflicts]


__all__ = [
    "ALL_CONFLICT_KINDS",
    "CONFLICT_EXPORTED_BUT_UNDECLARED",
    "CONFLICT_MANIFEST_NARROWED_SINCE_BASELINE",
    "CONFLICT_PACKAGE_BINARY_MISMATCH",
    "ConflictSourceClaim",
    "ContractSourceConflict",
    "conflicts_to_dicts",
]

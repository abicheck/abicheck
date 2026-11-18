# Copyright 2026 Nikolay Petrov
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

"""Per-kind observability and declared kind relations (ADR-093).

A leaf of the leaf, like :mod:`.dimensions`: zero internal imports, so
``registry.py`` can carry these values and validate them without a cycle.

**Observability** (ADR-093 D1-D3). Evidence sources are atoms, an evidence
configuration is a *set* of them, and a kind's ``observable_at`` is the
antichain of minimal configurations in each of which the fact is physically
present and must be reported. ``{B,H}`` (stripped binary + public headers) is
the primary configuration. :data:`UNCLASSIFIED` is an explicit sentinel, never
an empty set; its count is a metric that may only decrease.

**Relations** (ADR-093 D4). :class:`KindRelations` declares how one kind
relates to others. Targets are ``ChangeKind`` *values* (strings), because this
module cannot import the enum. :func:`validate_catalog` checks every
cross-entry constraint once, when the registry is assembled.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final


class EvidenceSource(StrEnum):
    """One evidence atom (ADR-093 D1)."""

    B = "B"  # binary: symbols, versions, sections, notes
    D = "D"  # DWARF (PDB on Windows)
    H = "H"  # public headers, without build flags
    BD = "BD"  # build data: compile commands, flags
    S = "S"  # sources / translation unit
    G = "G"  # multi-TU source graph


EvidenceConfig = frozenset[EvidenceSource]


def config(*sources: str | EvidenceSource) -> EvidenceConfig:
    """``config("B", "H")`` -> ``frozenset({B, H})``."""
    return frozenset(EvidenceSource(s) for s in sources)


#: The named levels of ADR-063's amendment, as configurations, not ranks.
L0: Final = config("B")
L1: Final = config("B", "D")
L2S: Final = config("B", "H")
L3: Final = config("B", "H", "BD")
L4: Final = config("B", "H", "BD", "S")
L5: Final = config("B", "H", "BD", "S", "G")


class Platform(StrEnum):
    ELF = "elf"
    PE = "pe"
    MACHO = "macho"


class Language(StrEnum):
    C = "c"
    CXX = "cxx"


class Arch(StrEnum):
    """Processor architecture a condition can restrict to (ADR-093 amendment).

    Only the architectures a kind's emission depends on are listed; add a
    value when a kind is first conditioned on one.
    """

    X86_64 = "x86_64"
    AARCH64 = "aarch64"


@dataclass(frozen=True, slots=True)
class ObservabilityCondition:
    """Restricts an :class:`Observability` to some platforms, languages and/or arches.

    ``None`` on an axis means "every value of that axis". A condition restricts
    at least one axis; ``arch`` (ADR-093 amendment) lets a kind whose emission
    depends on the processor (struct-return and struct-layout rules) be declared
    for that architecture only.
    """

    platform: frozenset[Platform] | None = None
    language: frozenset[Language] | None = None
    arch: frozenset[Arch] | None = None

    def __post_init__(self) -> None:
        if self.platform is None and self.language is None and self.arch is None:
            raise ValueError("an ObservabilityCondition must restrict an axis")
        for axis in (self.platform, self.language, self.arch):
            if axis is not None and not axis:
                raise ValueError("an ObservabilityCondition axis may not be empty")

    def overlaps(self, other: ObservabilityCondition | None) -> bool:
        """Whether some (platform, language, arch) point satisfies both conditions."""
        if other is None:
            return True
        return (
            _axis_overlaps(self.platform, other.platform)
            and _axis_overlaps(self.language, other.language)
            and _axis_overlaps(self.arch, other.arch)
        )

    def matches(
        self,
        platform: Platform | None,
        language: Language | None,
        arch: Arch | None = None,
    ) -> bool:
        """Whether a run on *platform*/*language*/*arch* falls under this condition.

        An unknown run axis (``None``) matches, so a condition never hides a
        kind from a run that did not state its platform, language or arch.
        """
        return (
            _axis_matches(self.platform, platform)
            and _axis_matches(self.language, language)
            and _axis_matches(self.arch, arch)
        )


#: The ELF x86-64 restriction: the LP64 psABI facts (ADR-093 amendment, D7).
ELF_X86_64 = ObservabilityCondition(
    platform=frozenset({Platform.ELF}), arch=frozenset({Arch.X86_64})
)


def _axis_overlaps(a: frozenset[Any] | None, b: frozenset[Any] | None) -> bool:
    return a is None or b is None or bool(a & b)


def _axis_matches(axis: frozenset[Any] | None, value: Any) -> bool:
    return axis is None or value is None or value in axis


@dataclass(frozen=True, slots=True)
class Observability:
    """Minimal configurations that observe a kind (ADR-093 D2).

    ``alternatives`` is non-empty, every alternative is non-empty, and no
    alternative is a superset of another (an antichain); construction refuses
    anything else. ``rationale`` is required whenever ``{B}`` alone is not an
    alternative: it names what carries the fact.
    """

    alternatives: frozenset[EvidenceConfig]
    rationale: str = ""
    condition: ObservabilityCondition | None = None

    def __post_init__(self) -> None:
        if not self.alternatives:
            raise ValueError("observable_at alternatives may not be empty")
        for alt in self.alternatives:
            if not alt:
                raise ValueError("an evidence configuration may not be empty")
            for other in self.alternatives:
                if alt is not other and alt < other:
                    raise ValueError(
                        f"observable_at is not an antichain: {_spell(alt)} "
                        f"is contained in {_spell(other)}"
                    )
        if L0 not in self.alternatives and not self.rationale.strip():
            raise ValueError(
                "observable_at above {B} needs a rationale naming what carries the fact"
            )

    def observes(self, cfg: Iterable[str | EvidenceSource]) -> bool:
        """True iff *cfg* contains some alternative (monotone by construction)."""
        have = frozenset(EvidenceSource(s) for s in cfg)
        return any(alt <= have for alt in self.alternatives)


def observable(
    *alternatives: Iterable[str | EvidenceSource],
    rationale: str = "",
    condition: ObservabilityCondition | None = None,
) -> Observability:
    """``observable(("B","D"), ("B","H"), rationale=...)`` — the catalog spelling."""
    return Observability(
        frozenset(config(*alt) for alt in alternatives), rationale, condition
    )


class _Unclassified:
    """Sentinel: the kind's observability has not been declared yet."""

    _instance: _Unclassified | None = None

    def __new__(cls) -> _Unclassified:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNCLASSIFIED"

    def __reduce__(self) -> tuple[Callable[[], _Unclassified], tuple[()]]:
        return (_Unclassified, ())


UNCLASSIFIED: Final = _Unclassified()

ObservableAt = tuple[Observability, ...] | _Unclassified


class SubsumptionKey(StrEnum):
    """Fixed matching keys for ``subsumed_by`` (never a free-form predicate)."""

    ENTITY = "entity"
    ENTITY_FIELD = "entity_field"


@dataclass(frozen=True, slots=True)
class Subsumption:
    kind: str
    key: SubsumptionKey


@dataclass(frozen=True, slots=True)
class KindRelations:
    """How a kind relates to others (ADR-093 D4). Targets are kind values."""

    variant_of: str | None = None
    same_event_as: str | None = None
    subsumed_by: tuple[Subsumption, ...] = field(default=())
    implies: tuple[str, ...] = field(default=())

    def __bool__(self) -> bool:
        return bool(
            self.variant_of or self.same_event_as or self.subsumed_by or self.implies
        )

    def targets(self) -> list[str]:
        out = [t for t in (self.variant_of, self.same_event_as) if t]
        out += [s.kind for s in self.subsumed_by]
        out += list(self.implies)
        return out


NO_RELATIONS: Final = KindRelations()


def _spell(cfg: EvidenceConfig) -> str:
    order = list(EvidenceSource)
    return "{" + ",".join(s.value for s in sorted(cfg, key=order.index)) + "}"


def validate_observable_at(kind: str, value: Any) -> None:
    """Per-entry check: the value is UNCLASSIFIED or a non-empty, unambiguous tuple."""
    if value is UNCLASSIFIED:
        return
    if not isinstance(value, tuple) or not value:
        raise ValueError(
            f"{kind!r}: observable_at must be UNCLASSIFIED or a non-empty tuple"
        )
    for i, a in enumerate(value):
        if not isinstance(a, Observability):
            raise ValueError(f"{kind!r}: observable_at holds a non-Observability")
        for b in value[i + 1 :]:
            if (
                a.condition is None
                or b.condition is None
                or a.condition.overlaps(b.condition)
            ):
                raise ValueError(
                    f"{kind!r}: observable_at entries need pairwise disjoint conditions"
                )


def validate_catalog(entries: Mapping[str, Any]) -> None:
    """Cross-entry checks of ADR-093 D4, run once on the assembled registry.

    *entries* maps kind value -> ``ChangeKindMeta`` (typed ``Any`` because
    this leaf cannot import the registry).
    """
    for kind, e in entries.items():
        validate_observable_at(kind, e.observable_at)
        rel: KindRelations = e.relations
        for target in rel.targets():
            if target not in entries:
                raise ValueError(f"{kind!r}: relation target {target!r} is not a kind")
            if target == kind:
                raise ValueError(f"{kind!r}: a kind may not relate to itself")
        for name in ("variant_of", "same_event_as"):
            target = getattr(rel, name)
            if target is None:
                continue
            t = entries[target]
            if getattr(t.relations, name) is not None:
                raise ValueError(
                    f"{kind!r}: {name} target {target!r} must be canonical, "
                    f"but itself declares {name}"
                )
            if t.contract_subject != e.contract_subject:
                raise ValueError(
                    f"{kind!r}: {name} target {target!r} declares a different "
                    "contract_subject"
                )
            if name == "same_event_as" and t.default_verdict != e.default_verdict:
                raise ValueError(
                    f"{kind!r}: same_event_as target {target!r} has a different "
                    "default_verdict"
                )
    _check_implies_acyclic(entries)


def _check_implies_acyclic(entries: Mapping[str, Any]) -> None:
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(entries, white)

    def visit(k: str, path: list[str]) -> None:
        color[k] = grey
        for nxt in entries[k].relations.implies:
            if color[nxt] == grey:
                cycle = path[path.index(nxt) :] + [nxt]
                raise ValueError("implies cycle: " + " -> ".join(cycle))
            if color[nxt] == white:
                visit(nxt, [*path, nxt])
        color[k] = black

    for k in entries:
        if color[k] == white:
            visit(k, [k])

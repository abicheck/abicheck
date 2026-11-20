"""Structured findings and per-entity claims: what a report says about *which* entity.

A kind-set judgement (``Expectation.problems``) checks that the report holds
the right kinds and a verdict inside its bounds. It cannot tell "``f`` was
removed" from "``f`` and ``g`` were removed": both carry ``FUNC_REMOVED``.
A ``Finding`` keeps the entity each kind is attributed to, taken from the
checker's typed result (never from report prose), and an ``EntityClaim`` says
what the report must or must not claim about one entity.

This module is a leaf: ``checkers/`` builds ``Finding`` values from the
checker's result, ``model/expect`` judges them.
"""

from __future__ import annotations

from dataclasses import dataclass

# Verdict names in increasing severity (see abicheck.checker_types.Verdict);
# duplicated from ``model.expect.VERDICT_RANK`` so this module stays a leaf.
_RANK: dict[str, int] = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 1,
    "COMPATIBLE_WITH_RISK": 2,
    "API_BREAK": 3,
    "BREAKING": 4,
}


@dataclass(frozen=True)
class Finding:
    """One finding as the checker's typed result states it.

    ``symbol`` is the checker's subject: the exported (for C++ the mangled)
    name of a function or variable, or a type name. ``qualified_name`` is the
    declared C++ name when the checker resolved one. ``impact`` is the verdict
    category the finding itself contributes (after per-finding policy
    modulation), or None when the checker did not state one.
    """

    kind: str
    symbol: str
    qualified_name: str | None = None
    impact: str | None = None

    def names(self) -> frozenset[str]:
        """Every spelling the finding is attributed under."""
        out = {self.symbol}
        if self.qualified_name:
            out.add(self.qualified_name)
            out.add(self.qualified_name.split("(", 1)[0])
        return frozenset(n for n in out if n)

    def describe(self) -> str:
        q = f" ({self.qualified_name})" if self.qualified_name else ""
        imp = f" [{self.impact}]" if self.impact else ""
        return f"{self.kind} {self.symbol}{q}{imp}"


@dataclass(frozen=True)
class EntityRef:
    """One entity a claim is about.

    ``name`` is its source-level name (``get_count``, ``S``). ``symbol`` is
    its exact exported symbol when one identifies it (a C++ overload is told
    apart from its siblings only by its mangled name): when set, a finding
    matches only through that symbol, never through the shared source name.
    """

    name: str
    symbol: str | None = None

    def matches(self, finding: Finding) -> bool:
        if self.symbol is not None:
            return finding.symbol == self.symbol
        return self.name in finding.names()

    @property
    def label(self) -> str:
        return self.symbol or self.name


@dataclass(frozen=True)
class EntityClaim:
    """What the report must (``required``) or must not claim about one entity.

    - ``required``: some finding on the entity has a kind in ``kinds``.
    - forbidden: no finding on the entity whose impact exceeds ``impact_max``
      (or, when ``kinds`` is non-empty, whose kind is in ``kinds``). A finding
      at or below ``impact_max`` is permitted diagnostics: the report may say
      an internal type changed without claiming a public break.
    """

    entity: EntityRef
    required: bool
    kinds: frozenset[str] = frozenset()
    impact_max: str = "COMPATIBLE_WITH_RISK"
    why: str = ""
    # Evidence atoms the claim needs to be ground truth (``"H"``: only a
    # public header shows that a record is not part of the contract). A
    # configuration without them leaves the claim *undecided*, never passed.
    needs: frozenset[str] = frozenset()

    def violation(
        self, findings: tuple[Finding, ...], impact_of: dict[str, str]
    ) -> str | None:
        """The problem text when *findings* break this claim, else None.

        *impact_of* is each kind's default verdict, used when a finding
        states no impact of its own.
        """
        on = [f for f in findings if self.entity.matches(f)]
        if self.required:
            if any(f.kind in self.kinds for f in on):
                return None
            return (
                f"missing claim on {self.entity.label}: none of "
                f"{sorted(self.kinds)} attributed to it"
            )
        for f in on:
            if self.kinds:
                bad = f.kind in self.kinds
            else:
                impact = f.impact or impact_of.get(f.kind, "BREAKING")
                bad = _RANK[impact] > _RANK[self.impact_max]
            if bad:
                return f"false claim {f.kind} on {self.entity.label}"
        return None


def kinds_of(findings: tuple[Finding, ...]) -> frozenset[str]:
    return frozenset(f.kind for f in findings)

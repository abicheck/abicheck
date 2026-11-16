"""Minimum-level cells: where an edit is first observable, from the kinds' ``observable_at``.

For an operator, the *minimal evidence sets* are the minimal atom sets among
the ``observable_at`` alternatives of the kinds the operator expects (ELF
platform only: the corpus's one platform). Two cell kinds follow, plan B2:

- *at minimum*: a configuration whose evidence is exactly one minimal set. The
  edit must be caught there.
- *below minimum*: a supported configuration that does not contain a minimal
  set. Nothing is required there; the cell proves abicheck does not claim the
  edit as a break it cannot see.

Only the evidence configurations the corpus builds are supported
(``SUPPORTED``). A minimal set outside them is reported, never silently
dropped. A kind with no declared ``observable_at`` (UNCLASSIFIED) is listed
in :func:`unclassified_kinds`: it gets no floor cell.
"""

from __future__ import annotations

from dataclasses import dataclass

from abiforge.checkers.abicheck import observable_at
from abiforge.model.config import Config
from abiforge.model.expect import _LANGUAGE_NAME, ADVISORY_KINDS
from abiforge.model.opkit import EditOp

#: Evidence atom sets the corpus can build, with the configuration that builds them.
#: ``{B,H}`` is the stripped headers configuration (the primary cell, plan §2).
SUPPORTED: dict[frozenset[str], dict[str, object]] = {
    frozenset({"B"}): {"evidence": "binary", "stripped": False},
    frozenset({"B", "D"}): {"evidence": "dwarf", "stripped": False},
    frozenset({"B", "H"}): {"evidence": "headers", "stripped": True},
    frozenset({"B", "D", "H"}): {"evidence": "headers", "stripped": False},
}


def expected_kinds(op: EditOp) -> frozenset[str]:
    """The edit's own expected kinds: its own, its ``any_of`` groups, its rules'.

    Advisory kinds (``ADVISORY_KINDS``) follow the verdict and are left out.
    """
    kinds = set(op.expected)
    for group in op.any_of:
        kinds |= set(group)
    for rule in op.rules:
        kinds |= set(rule.changes.get("expected", frozenset()))
        for group in rule.changes.get("any_of", ()):
            kinds |= set(group)
    return frozenset(kinds) - ADVISORY_KINDS


def unclassified_kinds(op: EditOp) -> frozenset[str]:
    """The operator's expected kinds with no declared ``observable_at``."""
    return frozenset(k for k in expected_kinds(op) if observable_at(k) is None)


def minimal_evidence_sets(op: EditOp, base: Config) -> frozenset[frozenset[str]]:
    """The minimal atom sets over the classified expected kinds on *base*'s ELF
    platform, an antichain. A condition scoped to another language is skipped,
    as ``kind_observable`` skips it for the same configuration."""
    language = _LANGUAGE_NAME[base.lang]
    alts: set[frozenset[str]] = set()
    for kind in expected_kinds(op):
        spec = observable_at(kind)
        if spec is None:
            continue
        for cond in spec:
            if cond.platform is not None and "elf" not in cond.platform:
                continue
            if cond.language is not None and language not in cond.language:
                continue
            alts |= set(cond.alternatives)
    return frozenset(a for a in alts if not any(b < a for b in alts))


@dataclass(frozen=True)
class Cell:
    """One minimum-level cell: the evidence set it stands for and its configuration."""

    atoms: frozenset[str]
    config: Config
    role: str  # "at-minimum" or "below-minimum"


def supported_config(base: Config, atoms: frozenset[str]) -> Config:
    """*base* with the evidence and stripping of the supported set *atoms*."""
    return base.with_(**SUPPORTED[atoms])  # type: ignore[arg-type]


def min_level_cells(
    op: EditOp, base: Config
) -> tuple[tuple[Cell, ...], tuple[frozenset[str], ...]]:
    """The cells of *op* at *base*, and the minimal sets with no supported configuration.

    At-minimum cells: one per minimal set the corpus builds. Below-minimum
    cells: every other supported configuration that contains no minimal set.
    """
    if unclassified_kinds(op):
        # A reverse check needs a declared floor: with an UNCLASSIFIED expected
        # kind the floor is unknown, so the operator gets no cells (reported).
        return (), ()
    minimal = minimal_evidence_sets(op, base)
    cells: list[Cell] = []
    unsupported: list[frozenset[str]] = []
    for m in sorted(minimal, key=lambda s: (len(s), sorted(s))):
        if m not in SUPPORTED:
            unsupported.append(m)
            continue
        cells.append(Cell(m, supported_config(base, m), "at-minimum"))
    for atoms in SUPPORTED:
        if any(m <= atoms for m in minimal):
            continue
        cells.append(Cell(atoms, supported_config(base, atoms), "below-minimum"))
    return tuple(cells), tuple(unsupported)

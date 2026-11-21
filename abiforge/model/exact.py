"""Exact finding assertions: which rows a report must hold, row by row, in a declared scope.

A kind-set judgement (``Expectation.any_of``) passes a report that holds *a*
``TYPE_FIELD_OFFSET_CHANGED``; an ``EntityClaim`` passes one that attributes
it to the right record. Neither notices that the offset belongs to the wrong
member, carries the wrong old/new value, is missing for a second member, or is
reported twice. A ``FindingAssertion`` closes that gap:

- **Rows** (``ExactRow``): kind, entity (the record), member (``field_name``)
  and exact old/new values. A layout value carries its unit explicitly
  (``Bits``/``Bytes``), so a row written in bytes and a checker value in bits
  compare after conversion, never by spelling.
- **Multiset**: the expected rows and the in-scope findings are compared as
  multisets, so a duplicated row, a dropped row and a reordered report are
  told apart (reordering passes; the other two fail).
- **Scope** (``FindingScope``): only findings whose kind *and* entity are
  declared in scope are compared. A finding outside it (the exported variable
  holding the record, an advisory) never fails the assertion; one inside it
  that no row expects is an unexpected row. An assertion with no rows is a
  required absence: an unchanged control.
- **Unknown**: an assertion whose evidence the configuration lacks
  (``needs``) is *inconclusive*, never passed. A row whose value the checker
  states in a form the assertion cannot read is a problem, never a match.

Values come from the checker's structured result (``Finding.field_name``,
``old_value``, ``new_value``), never from report prose. This module is a leaf.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from abiforge.model.finding import Finding

__all__ = [
    "Bits",
    "Bytes",
    "ExactRow",
    "FindingAssertion",
    "FindingScope",
    "Outcome",
    "Width",
    "canonical_value",
]

#: How the checker states each layout kind's values. Sizes, alignments and
#: field offsets are bit counts (``"72"``); a bit-field's width is
#: ``"bits=N"`` (``bits=None``: not a bit-field). Any other kind's values are
#: declared types or names, compared as text.
_BIT_KINDS = frozenset(
    {"TYPE_SIZE_CHANGED", "TYPE_ALIGNMENT_CHANGED", "TYPE_FIELD_OFFSET_CHANGED"}
)
_WIDTH_KINDS = frozenset({"FIELD_BITFIELD_CHANGED"})
_WIDTH_RE = re.compile(r"bits=(None|\d+)")


@dataclass(frozen=True)
class Bits:
    """A layout quantity in bits (an offset, a size, an alignment)."""

    n: int

    @property
    def canonical(self) -> str:
        return f"{self.n}bit"


def Bytes(n: int) -> Bits:  # noqa: N802 - a unit constructor, read like Bits
    """A layout quantity given in bytes, held in bits."""
    return Bits(8 * n)


@dataclass(frozen=True)
class Width:
    """A bit-field's declared width; ``None`` = the member is no bit-field."""

    bits: int | None

    @property
    def canonical(self) -> str:
        return f"width={self.bits}"


Value = Bits | Width | str | None


def _canon(v: Value) -> str | None:
    if v is None or isinstance(v, str):
        return v
    return v.canonical


def canonical_value(kind: str, raw: object) -> str | None:
    """The checker's *raw* value of a *kind* finding in the assertion's units.

    Raises ``ValueError`` when the value is not in the form *kind* states:
    an unreadable value is never taken as a match.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if kind in _BIT_KINDS:
        if not text.isdigit():
            raise ValueError(f"{kind} value {text!r} is not a bit count")
        return Bits(int(text)).canonical
    if kind in _WIDTH_KINDS:
        m = _WIDTH_RE.fullmatch(text)
        if m is None:
            raise ValueError(f"{kind} value {text!r} is not bits=N")
        return Width(None if m.group(1) == "None" else int(m.group(1))).canonical
    return text


@dataclass(frozen=True)
class ExactRow:
    """One finding the report must hold exactly once (per occurrence in the rows)."""

    kind: str
    entity: str
    member: str | None = None
    old: Value = None
    new: Value = None

    def key(self) -> tuple[str, str, str | None, str | None, str | None]:
        return (self.kind, self.entity, self.member, _canon(self.old), _canon(self.new))


def _describe(key: tuple[str, str, str | None, str | None, str | None]) -> str:
    kind, entity, member, old, new = key
    who = f"{entity}::{member}" if member else entity
    return f"{kind} {who} {old}->{new}"


@dataclass(frozen=True)
class FindingScope:
    """Which findings an assertion compares: kind in ``kinds`` and entity in ``entities``.

    ``entities`` are record names as the checker's ``symbol`` names them
    (``Finding.names()``: the bare or qualified spelling).
    """

    kinds: frozenset[str]
    entities: frozenset[str]

    def covers(self, f: Finding) -> bool:
        return f.kind in self.kinds and bool(self.entities & f.names())

    def entity_of(self, f: Finding) -> str:
        hit = sorted(self.entities & f.names())
        return hit[0] if hit else f.symbol


Status = Literal["pass", "fail", "inconclusive"]


@dataclass(frozen=True)
class Outcome:
    """One assertion's judgement: ``pass``, ``fail`` (with problems) or ``inconclusive``."""

    status: Status
    problems: tuple[str, ...] = ()
    why: str = ""


@dataclass(frozen=True)
class FindingAssertion:
    """The exact multiset of rows the report must hold inside ``scope``.

    Every row's kind must be in ``scope.kinds`` and its entity in
    ``scope.entities`` (checked at construction). ``needs``: evidence atoms
    without which the rows are not ground truth; ``unknown``: why part of the
    scope is undecided at this configuration (set by the observability floor),
    which makes a clean comparison *inconclusive*, not a pass.
    """

    scope: FindingScope
    rows: tuple[ExactRow, ...] = ()
    needs: frozenset[str] = frozenset()
    why: str = ""
    # Evidence atoms a kind's rows need beyond the kind's own observability
    # (``TYPE_ALIGNMENT_CHANGED``: DWARF from gcc/clang records no alignment of
    # a record without ``alignas``, so only a header dump establishes it).
    kind_needs: Mapping[str, frozenset[str]] = field(
        default_factory=dict, compare=False
    )
    unknown: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        for r in self.rows:
            if r.kind not in self.scope.kinds or r.entity not in self.scope.entities:
                raise ValueError(f"row {_describe(r.key())} is outside its scope")

    def without_kinds(self, kinds: frozenset[str], why: str) -> FindingAssertion:
        """This assertion with *kinds* taken out of scope (and their rows).

        Dropping an expected row makes the assertion unknown (*why*): the row
        is an obligation nothing checked. Dropping a kind no row expects only
        narrows the scope."""
        if not (kinds & self.scope.kinds):
            return self
        scope = FindingScope(self.scope.kinds - kinds, self.scope.entities)
        rows = tuple(r for r in self.rows if r.kind not in kinds)
        dropped = len(rows) != len(self.rows)
        unknown = "; ".join(filter(None, (self.unknown, why if dropped else "")))
        return FindingAssertion(
            scope, rows, self.needs, self.why, self.kind_needs, unknown
        )

    def judge(self, findings: tuple[Finding, ...]) -> Outcome:
        expected = Counter(r.key() for r in self.rows)
        got: Counter[tuple[str, str, str | None, str | None, str | None]] = Counter()
        problems: list[str] = []
        for f in findings:
            if not self.scope.covers(f):
                continue
            try:
                key = (
                    f.kind,
                    self.scope.entity_of(f),
                    f.field_name,
                    canonical_value(f.kind, f.old_value),
                    canonical_value(f.kind, f.new_value),
                )
            except ValueError as exc:
                problems.append(f"exact unreadable {f.describe()}: {exc}")
                continue
            got[key] += 1
        for key, n in sorted((expected - got).items()):
            problems.append(f"exact missing {_describe(key)} (x{n})")
        for key, n in sorted((got - expected).items()):
            problems.append(f"exact unexpected {_describe(key)} (x{n})")
        if problems:
            return Outcome("fail", tuple(problems))
        if self.unknown:
            return Outcome("inconclusive", (), self.unknown)
        return Outcome("pass")

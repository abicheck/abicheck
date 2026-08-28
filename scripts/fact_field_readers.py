#!/usr/bin/env python3
"""Real, repo-wide scan for unmigrated readers of a `Fact[T]`-bridged legacy
field (ADR-063 Phase 0, docs/contribute/plans/one-semantic-pipeline.md).

A leaf module imported by ``check_ai_readiness.py``, mirroring
``engine_cli_boundary.py``'s own extraction (``check_ai_readiness.py`` is
already past the 2000-line hard cap and only stays green through
``LARGE_FILE_ALLOWLIST``, which is not a license to keep growing it).

**Why this exists.** `RecordType.bases`/`virtual_bases`/`vtable` and
`Param.is_va_list` each carry a `Fact[...]` sibling recording whether the
value is a real determination, a known-imprecise heuristic, or genuinely
uncollected/unsupported/failed -- but the legacy field itself stays a
plain, fully-populated value (`[]`/`False` when unavailable) for backward
compatibility. A reader that accesses the legacy field directly, without
ever consulting `.status`, cannot tell "confirmed empty" apart from "no
evidence" -- the exact ambiguity `Fact[T]` exists to make representable.

The plan doc's own Design section tried enumerating every such reader by
hand and repeatedly missed one: a fifth, then a tenth call site kept
turning up in later review rounds, always outside the `diff_*.py` glob a
first-draft check would have scoped to. Auditing this codebase directly
while building this check (rather than trusting that hand list) found
three more the plan's own table doesn't name yet
(`buildsource/header_graph.py`'s inheritance-edge emitter,
`buildsource/source_extractors/base.py`'s L4/L5 entity-identity fingerprint,
and `idioms.py`'s factory/non-virtual-destructor idiom detectors) --
confirming the plan's own conclusion: a hand-maintained allowlist is the
wrong invariant. This check is the real one: a plain, repo-wide AST scan
for a direct attribute read of one of the four names below, with every
*currently known* reader recorded in `KNOWN_UNMIGRATED_READERS` (an
allowlist-and-shrink baseline, exactly `IMPORT_CYCLE_ALLOWLIST`'s own
convention -- it may only shrink, and a genuinely new entry needs the same
review bar AGENTS.md sets for that allowlist) -- so the *next* hand-missed
call site fails this gate instead of silently joining the ambiguity.

**No type inference.** "On a value whose declared type resolves to
`RecordType`/`Param`" (the Design section's own phrasing) would need a real
type checker; this script runs before `pip install` and must stay pure
stdlib. Instead it matches the four attribute *names* anywhere in
`abicheck/` — verified empirically (by running exactly this scan against
the whole package before writing the baseline below) to have zero
cross-class collisions today: every single hit is genuinely a `RecordType`/
`Param` access. A future addition to this codebase reusing one of these
names on an unrelated class would need re-auditing, the same caveat this
codebase already accepts elsewhere for a structural, non-type-checking scan
(e.g. `backend_capabilities.py`'s own AST-based evidence reader).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"

#: The four `Fact[T]`-bridged legacy field names this phase converted
#: (ADR-063 Phase 0's Scope section: `RecordType.bases`/`virtual_bases`/
#: `vtable`, `Param.is_va_list`). `RecordType.vptr_offset_bits` is
#: deliberately not included -- it is `int | None`, already meaningfully
#: `None` before this phase, so "read the raw field" was never itself
#: ambiguous the way these four (whose legacy value collapses "no
#: evidence" onto an otherwise-ordinary confirmed value) are.
FACT_BRIDGED_ATTRS: frozenset[str] = frozenset(
    {"bases", "virtual_bases", "vtable", "is_va_list"}
)

#: Permanently exempt, never scanned: the field's own dataclass definition
#: and `__post_init__` omission-bridge implementation (`model/`), and the
#: header/DWARF producers plus the DWARF-backfill/hybrid-merge modules that
#: *compute or combine* the raw legacy value itself. None of these make a
#: compatibility decision from the field -- they are where the value (and,
#: since ADR-063 Phase 0's second slice, its `Fact[...]` status) comes
#: from, the role `RecordType(bases=...)`'s own keyword construction plays
#: everywhere else (which this scan can't see at all, since it looks only
#: at attribute *reads* on an existing instance, not constructor keywords).
#: Unlike `KNOWN_UNMIGRATED_READERS` below, this set does not shrink --
#: there is no "migrated" state for a bridge or a producer to reach.
EXEMPT_MODULES: frozenset[str] = frozenset(
    {
        "abicheck/model/entities.py",
        "abicheck/model/declarations.py",
        "abicheck/dwarf_snapshot.py",
        "abicheck/dumper_layout_backfill.py",
    }
)

#: Every currently-known unmigrated semantic reader, keyed
#: `"<rel>::<attr>::<occurrence>"` -- `occurrence` is the attribute's
#: 1-based rank among reads of that same attribute in that same file, in
#: top-to-bottom (line) order, mirroring `ENGINE_CLI_BOUNDARY_ALLOWLIST`'s
#: own keying rationale: stable across an unrelated edit elsewhere in the
#: file, unlike a line-number key. Closing one of these (migrating the
#: reader to check `.status` before trusting the legacy value) removes its
#: entry here; a brand-new, unlisted hit fails the gate.
#:
#: The nine modules the plan doc's own Design section names
#: (`docs/contribute/plans/one-semantic-pipeline.md`, "nine distinct
#: modules, ten call sites" table) plus the primary detectors and other
#: readers that section names separately (`diff_layout.py`/`diff_types.py`/
#: `diff_vtable_layout.py`/`diff_param_qualifiers.py`/`diff_cxx_rules.py`),
#: plus three more this check's own construction found and the plan doc
#: does not yet name (`buildsource/header_graph.py`,
#: `buildsource/source_extractors/base.py`, `idioms.py`).
KNOWN_UNMIGRATED_READERS: frozenset[str] = frozenset(
    {
        "abicheck/buildsource/header_graph.py::bases::1",
        "abicheck/buildsource/source_extractors/base.py::bases::1",
        "abicheck/buildsource/source_extractors/base.py::vtable::1",
        "abicheck/contract_evidence_collect.py::bases::1",
        "abicheck/contract_evidence_collect.py::virtual_bases::1",
        "abicheck/diff_cxx_rules.py::bases::1",
        "abicheck/diff_cxx_rules.py::bases::2",
        "abicheck/diff_cxx_rules.py::virtual_bases::1",
        "abicheck/diff_cxx_rules.py::virtual_bases::2",
        "abicheck/diff_cxx_rules.py::vtable::1",
        "abicheck/diff_cxx_rules.py::vtable::2",
        "abicheck/diff_layout.py::vtable::1",
        "abicheck/diff_layout.py::vtable::2",
        "abicheck/diff_param_qualifiers.py::is_va_list::1",
        "abicheck/diff_param_qualifiers.py::is_va_list::2",
        "abicheck/diff_param_qualifiers.py::is_va_list::3",
        "abicheck/diff_param_qualifiers.py::is_va_list::4",
        "abicheck/diff_stdlib_impl.py::bases::1",
        "abicheck/diff_stdlib_impl.py::virtual_bases::1",
        "abicheck/diff_time64.py::bases::1",
        "abicheck/diff_time64.py::virtual_bases::1",
        "abicheck/diff_types.py::bases::1",
        "abicheck/diff_types.py::bases::2",
        "abicheck/diff_types.py::bases::3",
        "abicheck/diff_types.py::bases::4",
        "abicheck/diff_types.py::bases::5",
        "abicheck/diff_types.py::bases::6",
        "abicheck/diff_types.py::bases::7",
        "abicheck/diff_types.py::bases::8",
        "abicheck/diff_types.py::bases::9",
        "abicheck/diff_types.py::bases::10",
        "abicheck/diff_types.py::virtual_bases::1",
        "abicheck/diff_types.py::virtual_bases::2",
        "abicheck/diff_types.py::virtual_bases::3",
        "abicheck/diff_types.py::virtual_bases::4",
        "abicheck/diff_types.py::virtual_bases::5",
        "abicheck/diff_types.py::virtual_bases::6",
        "abicheck/diff_types.py::virtual_bases::7",
        "abicheck/diff_types.py::virtual_bases::8",
        "abicheck/diff_types.py::virtual_bases::9",
        "abicheck/diff_types.py::virtual_bases::10",
        "abicheck/diff_types.py::virtual_bases::11",
        "abicheck/diff_types.py::vtable::1",
        "abicheck/diff_types.py::vtable::2",
        "abicheck/diff_types.py::vtable::3",
        "abicheck/diff_types.py::vtable::4",
        "abicheck/diff_types.py::vtable::5",
        "abicheck/diff_types.py::vtable::6",
        "abicheck/diff_types.py::vtable::7",
        "abicheck/diff_types.py::vtable::8",
        "abicheck/diff_types.py::vtable::9",
        "abicheck/diff_types.py::vtable::10",
        "abicheck/diff_types.py::vtable::11",
        "abicheck/diff_types.py::vtable::12",
        "abicheck/diff_types.py::vtable::13",
        "abicheck/diff_types.py::vtable::14",
        "abicheck/diff_types.py::vtable::15",
        "abicheck/diff_vtable_layout.py::bases::1",
        "abicheck/diff_vtable_layout.py::bases::2",
        "abicheck/diff_vtable_layout.py::bases::3",
        "abicheck/diff_vtable_layout.py::bases::4",
        "abicheck/diff_vtable_layout.py::virtual_bases::1",
        "abicheck/diff_vtable_layout.py::virtual_bases::2",
        "abicheck/diff_vtable_layout.py::virtual_bases::3",
        "abicheck/diff_vtable_layout.py::virtual_bases::4",
        "abicheck/diff_vtable_layout.py::virtual_bases::5",
        "abicheck/diff_vtable_layout.py::virtual_bases::6",
        "abicheck/diff_vtable_layout.py::virtual_bases::7",
        "abicheck/diff_vtable_layout.py::virtual_bases::8",
        "abicheck/diff_vtable_layout.py::virtual_bases::9",
        "abicheck/diff_vtable_layout.py::virtual_bases::10",
        "abicheck/diff_vtable_layout.py::virtual_bases::11",
        "abicheck/diff_vtable_layout.py::vtable::1",
        "abicheck/dumper_scoping.py::bases::1",
        "abicheck/dumper_scoping.py::virtual_bases::1",
        "abicheck/export_surface.py::bases::1",
        "abicheck/export_surface.py::virtual_bases::1",
        "abicheck/idioms.py::bases::1",
        "abicheck/idioms.py::vtable::1",
        "abicheck/idioms.py::vtable::2",
        "abicheck/idioms.py::vtable::3",
        "abicheck/internal_leak.py::bases::1",
        "abicheck/internal_leak.py::virtual_bases::1",
        "abicheck/surface.py::bases::1",
        "abicheck/surface.py::bases::2",
        "abicheck/surface.py::virtual_bases::1",
        "abicheck/surface.py::virtual_bases::2",
        "abicheck/surface_graph.py::bases::1",
        "abicheck/surface_graph.py::virtual_bases::1",
        "abicheck/type_reachability.py::bases::1",
        "abicheck/type_reachability.py::virtual_bases::1",
    }
)


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def unmigrated_fact_reader_sites(
    tree: ast.Module, rel: str
) -> list[tuple[str, int, str]]:
    """Return one ``(allowlist_key, lineno, attr)`` per attribute read of a
    `Fact`-bridged field found in *tree* (already parsed from *rel*).

    Only `ast.Load` context counts -- a `Store`/`Del` (an assignment like
    `storage/fact_codec.py`'s legacy-schema backfill `record.vtable = []`)
    is writing the field, not reading it as if it were unambiguous, and is
    not the failure mode this check exists to catch.
    """
    matches: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in FACT_BRIDGED_ATTRS
            and isinstance(node.ctx, ast.Load)
        ):
            matches.append((node.lineno, node.attr))
    matches.sort(key=lambda m: m[0])
    occurrence: dict[str, int] = {}
    sites: list[tuple[str, int, str]] = []
    for lineno, attr in matches:
        occurrence[attr] = occurrence.get(attr, 0) + 1
        sites.append((f"{rel}::{attr}::{occurrence[attr]}", lineno, attr))
    return sites


def check_fact_field_readers(f: Findings) -> None:
    """ERROR if a module outside `EXEMPT_MODULES` reads a `Fact`-bridged
    legacy field (`RecordType.bases`/`virtual_bases`/`vtable`,
    `Param.is_va_list`) directly, without the read being a previously
    reviewed, `KNOWN_UNMIGRATED_READERS`-baselined site.

    Real, repo-wide AST scan, not a `diff_*.py` glob -- see this module's
    own docstring for why a glob (or any other hand-maintained scope) is
    exactly what let a real reader keep going unnoticed across several
    review rounds. New violations are rejected outright; every
    *currently* known one is recorded in `KNOWN_UNMIGRATED_READERS`
    (docs/contribute/plans/one-semantic-pipeline.md, Phase 0's Design
    section) rather than silently passing.
    """
    for path in sorted(PKG.rglob("*.py")):
        rel = _rel(path)
        if rel in EXEMPT_MODULES:
            continue
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        for key, lineno, attr in unmigrated_fact_reader_sites(tree, rel):
            if key in KNOWN_UNMIGRATED_READERS:
                continue
            f.err(
                "fact-field-readers",
                f"{rel}:{lineno}: reads `{attr}` directly without checking "
                "its Fact[...] sibling's .status -- this collapses "
                "'confirmed empty/false' and 'no evidence' onto the same "
                "value; either migrate this reader to check .status first, "
                "or add its stable key to KNOWN_UNMIGRATED_READERS in "
                "scripts/fact_field_readers.py if it's a genuinely new, "
                "reviewed baseline entry (see "
                "docs/contribute/plans/one-semantic-pipeline.md Phase 0)",
            )

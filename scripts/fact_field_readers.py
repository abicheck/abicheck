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

**Exemption is function-scoped, not module-scoped (Codex review, fresh
evidence).** A first draft of this check exempted whole modules
(`dwarf_snapshot.py`, `dumper_layout_backfill.py`) on the theory that they
are pure producer/merge code. That is false for *two of their own
functions*: `dwarf_snapshot._DwarfSnapshotBuilder._filter_types_by_
reachability` reads `bases`/`virtual_bases` to decide which types survive
into the exported snapshot (a real compatibility-relevant decision, not a
value computation), and `dumper_layout_backfill._fields_corroborate` reads
`bases`/`virtual_bases`/`vtable` to decide whether two records structurally
match (also a decision, not a merge). A whole-module exemption hid both
from this scan entirely, and would silently hide the next such function
added to either file. `EXEMPT_FUNCTIONS` is keyed by qualified function
name (`Class.method` for a method, tracked through nested `def`s) so only
the specific bridge/producer functions are exempt; the two decision
functions above are real `KNOWN_UNMIGRATED_READERS` entries like any other.

**Baseline keys include the enclosing function, not just a per-file
occurrence ordinal (Codex review, fresh evidence).** A first draft keyed
`"<rel>::<attr>::<occurrence>"` -- purely a top-to-bottom rank among reads
of that attribute in that file. If an existing read is migrated or deleted
and an unrelated new read of the same attribute is later added to the same
file, the new read can silently inherit the vacated occurrence number and
pass as an already-reviewed site. Scoping the occurrence counter to
`(qualname, attr)` instead of just `attr` makes that collision need the
new read to land in the exact same function as the migrated one AND at the
same rank within it -- a real coincidence to construct, not a routine
edit's side effect the way a bare per-file ordinal was.

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

#: Permanently exempt, keyed `"<rel>::<qualname>"` (qualname is the
#: function's own name, dotted through an enclosing class -- `Class.method`
#: for a method, tracked through nested `def`s the same way Python's own
#: `__qualname__` is): the field's own dataclass `__post_init__`
#: omission-bridge implementation (`model/`), and the specific DWARF-
#: producer/DWARF-backfill functions that *compute or combine* the raw
#: legacy value itself. Function-scoped, not module-scoped -- see this
#: module's own docstring for the real bug a whole-module exemption caused
#: (two genuine decision functions living in these same two files were
#: hidden from the scan entirely). None of these make a compatibility
#: decision from the field -- they are where the value (and, since
#: ADR-063 Phase 0's second slice, its `Fact[...]` status) comes from, the
#: role `RecordType(bases=...)`'s own keyword construction plays at every
#: producer (which this attribute-*read* scan can't see at all, since it
#: only looks at reads on an existing instance, not constructor keywords).
#: Unlike `KNOWN_UNMIGRATED_READERS` below, this set does not shrink --
#: there is no "migrated" state for a bridge or a producer to reach.
EXEMPT_FUNCTIONS: frozenset[str] = frozenset(
    {
        "abicheck/model/entities.py::RecordType.__post_init__",
        "abicheck/model/declarations.py::Param.__post_init__",
        "abicheck/dwarf_snapshot.py::_DwarfSnapshotBuilder._finalize_vptr_offsets",
        "abicheck/dumper_layout_backfill.py::_backfilled_record",
    }
)

#: Every currently-known unmigrated semantic reader, keyed
#: `"<rel>::<qualname>::<attr>::<occurrence>"` -- `qualname` is the
#: enclosing function (`<module>` for module-level code, `Class.method`
#: for a method), and `occurrence` is the attribute's 1-based rank among
#: reads of that same attribute *within that same function*, in
#: top-to-bottom (line) order. Scoping the occurrence counter to the
#: function (not just the file) is what keeps a migrated-and-replaced read
#: from silently inheriting an unrelated new read's key -- see this
#: module's own docstring. Closing one of these (migrating the reader to
#: check `.status` before trusting the legacy value) removes its entry
#: here; a brand-new, unlisted hit fails the gate.
#:
#: The nine modules the plan doc's own Design section names
#: (`docs/contribute/plans/one-semantic-pipeline.md`, "nine distinct
#: modules, ten call sites" table) plus the primary detectors and other
#: readers that section names separately (`diff_layout.py`/`diff_types.py`/
#: `diff_vtable_layout.py`/`diff_param_qualifiers.py`/`diff_cxx_rules.py`),
#: plus five more this check's own construction found and the plan doc
#: does not yet name (`buildsource/header_graph.py`,
#: `buildsource/source_extractors/base.py`, `idioms.py`, and two decision
#: functions living inside otherwise-exempt producer modules --
#: `dwarf_snapshot._DwarfSnapshotBuilder._filter_types_by_reachability`
#: and `dumper_layout_backfill._fields_corroborate`, both found only once
#: whole-module exemption was narrowed to function scope, per this
#: module's own docstring).
KNOWN_UNMIGRATED_READERS: frozenset[str] = frozenset(
    {
        "abicheck/buildsource/header_graph.py::_flat_structural_type_edges::bases::1",
        "abicheck/buildsource/source_extractors/base.py::entity_from_record::bases::1",
        "abicheck/buildsource/source_extractors/base.py::entity_from_record::vtable::1",
        "abicheck/contract_evidence_collect.py::build_type_graph::bases::1",
        "abicheck/contract_evidence_collect.py::build_type_graph::virtual_bases::1",
        "abicheck/diff_cxx_rules.py::_transitive_bases::bases::1",
        "abicheck/diff_cxx_rules.py::_transitive_bases::bases::2",
        "abicheck/diff_cxx_rules.py::_transitive_bases::virtual_bases::1",
        "abicheck/diff_cxx_rules.py::_transitive_bases::virtual_bases::2",
        "abicheck/diff_cxx_rules.py::virtual_method_addition::vtable::1",
        "abicheck/diff_cxx_rules.py::virtual_method_addition::vtable::2",
        "abicheck/diff_layout.py::_check_vptr_introduced::vtable::1",
        "abicheck/diff_layout.py::_check_vptr_introduced::vtable::2",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::1",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::2",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::3",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::4",
        "abicheck/diff_stdlib_impl.py::_public_by_value_type_closure::bases::1",
        "abicheck/diff_stdlib_impl.py::_public_by_value_type_closure::virtual_bases::1",
        "abicheck/diff_time64.py::_fold_record_tokens::bases::1",
        "abicheck/diff_time64.py::_fold_record_tokens::virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::2",
        "abicheck/diff_types.py::_diff_type_bases::bases::3",
        "abicheck/diff_types.py::_diff_type_bases::bases::4",
        "abicheck/diff_types.py::_diff_type_bases::bases::5",
        "abicheck/diff_types.py::_diff_type_bases::bases::6",
        "abicheck/diff_types.py::_diff_type_bases::bases::7",
        "abicheck/diff_types.py::_diff_type_bases::bases::8",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::2",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::3",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::4",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::5",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::6",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::10",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::2",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::3",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::4",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::5",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::6",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::7",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::8",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::9",
        "abicheck/diff_types.py::_new_field_change_kind::virtual_bases::1",
        "abicheck/diff_types.py::_new_field_change_kind::vtable::1",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::virtual_bases::1",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::virtual_bases::2",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::vtable::1",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::vtable::2",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::bases::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::bases::2",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::virtual_bases::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::virtual_bases::2",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::vtable::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::vtable::2",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::bases::1",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::bases::2",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::1",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::2",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::3",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::4",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::5",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::6",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::7",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::8",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::9",
        "abicheck/diff_vtable_layout.py::_is_polymorphic::bases::1",
        "abicheck/diff_vtable_layout.py::_is_polymorphic::virtual_bases::1",
        "abicheck/diff_vtable_layout.py::_is_polymorphic::vtable::1",
        "abicheck/diff_vtable_layout.py::_secondary_groups::bases::1",
        "abicheck/diff_vtable_layout.py::_secondary_groups::virtual_bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::bases::2",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::virtual_bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::virtual_bases::2",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::vtable::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::vtable::2",
        "abicheck/dumper_scoping.py::_kept_signature_haystack::bases::1",
        "abicheck/dumper_scoping.py::_kept_signature_haystack::virtual_bases::1",
        "abicheck/dwarf_snapshot.py::_DwarfSnapshotBuilder._filter_types_by_reachability::bases::1",
        "abicheck/dwarf_snapshot.py::_DwarfSnapshotBuilder._filter_types_by_reachability::virtual_bases::1",
        "abicheck/export_surface.py::_unresolved_type_edges::bases::1",
        "abicheck/export_surface.py::_unresolved_type_edges::virtual_bases::1",
        "abicheck/idioms.py::_collect_base_targets::bases::1",
        "abicheck/idioms.py::_detect_non_virtual_dtor::vtable::1",
        "abicheck/idioms.py::_has_virtual_destructor::vtable::1",
        "abicheck/idioms.py::_recognise_factory::vtable::1",
        "abicheck/internal_leak.py::_enqueue_record_children::bases::1",
        "abicheck/internal_leak.py::_enqueue_record_children::virtual_bases::1",
        "abicheck/surface.py::_walk_exact_type_closure::bases::1",
        "abicheck/surface.py::_walk_exact_type_closure::virtual_bases::1",
        "abicheck/surface.py::_walk_type_closure::bases::1",
        "abicheck/surface.py::_walk_type_closure::virtual_bases::1",
        "abicheck/surface_graph.py::_build_type_refs::bases::1",
        "abicheck/surface_graph.py::_build_type_refs::virtual_bases::1",
        "abicheck/type_reachability.py::_walk_reached_records::bases::1",
        "abicheck/type_reachability.py::_walk_reached_records::virtual_bases::1",
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


def _enclosing_qualnames(tree: ast.Module) -> dict[int, str]:
    """Map every line number in *tree* to its innermost enclosing
    function's qualified name (``Class.method`` for a method, tracked
    through nested ``def``s the way `__qualname__` is), or ``"<module>"``
    for a line outside any function.

    A plain line-range lookup rather than tracking a live scope stack
    during the attribute walk: `ast.walk` doesn't expose parent/ancestor
    context, and re-deriving it with a hand-rolled visitor for every call
    site would duplicate this same walk. One pass building this map,
    consulted by line number, is simpler and gives the identical answer.
    """
    ranges: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                end = getattr(child, "end_lineno", child.lineno)
                ranges.append((child.lineno, end, qualname))
                visit(child, qualname + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    # Innermost enclosing range wins: sort by ascending span size so a
    # later, narrower match overwrites the wider one already recorded.
    ranges.sort(key=lambda r: r[1] - r[0], reverse=True)
    by_line: dict[int, str] = {}
    for start, end, qualname in ranges:
        for lineno in range(start, end + 1):
            by_line[lineno] = qualname
    return by_line


def unmigrated_fact_reader_sites(
    tree: ast.Module, rel: str
) -> list[tuple[str, int, str, str]]:
    """Return one ``(allowlist_key, lineno, attr, qualname)`` per attribute
    read of a `Fact`-bridged field found in *tree* (already parsed from
    *rel*).

    Only `ast.Load` context counts -- a `Store`/`Del` (an assignment like
    `storage/fact_codec.py`'s legacy-schema backfill `record.vtable = []`)
    is writing the field, not reading it as if it were unambiguous, and is
    not the failure mode this check exists to catch.
    """
    qualnames = _enclosing_qualnames(tree)
    matches: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in FACT_BRIDGED_ATTRS
            and isinstance(node.ctx, ast.Load)
        ):
            matches.append(
                (node.lineno, node.attr, qualnames.get(node.lineno, "<module>"))
            )
    matches.sort(key=lambda m: m[0])
    occurrence: dict[tuple[str, str], int] = {}
    sites: list[tuple[str, int, str, str]] = []
    for lineno, attr, qualname in matches:
        occ_key = (qualname, attr)
        occurrence[occ_key] = occurrence.get(occ_key, 0) + 1
        key = f"{rel}::{qualname}::{attr}::{occurrence[occ_key]}"
        sites.append((key, lineno, attr, qualname))
    return sites


def check_fact_field_readers(f: Findings) -> None:
    """ERROR if a function outside `EXEMPT_FUNCTIONS` reads a `Fact`-bridged
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
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        for key, lineno, attr, qualname in unmigrated_fact_reader_sites(tree, rel):
            if f"{rel}::{qualname}" in EXEMPT_FUNCTIONS:
                continue
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

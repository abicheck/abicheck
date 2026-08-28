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
for a direct attribute read of one of the five names below, with every
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

**Baseline keys include the enclosing function AND the read's own source
text, not a bare occurrence ordinal (Codex review, two rounds, fresh
evidence both times).** A first draft keyed `"<rel>::<attr>::<occurrence>"`
-- purely a top-to-bottom rank among reads of that attribute in that file.
Scoping the occurrence counter to `(qualname, attr)` (the enclosing
function, not just the file) closed the file-wide version of the
collision, but not a narrower one a second round found with fresh
evidence: `diff_param_qualifiers.py`'s `if not p_old.is_va_list and
p_new.is_va_list:` has two DIFFERENT reads (`p_old.is_va_list`,
`p_new.is_va_list`) on one line, in the same function -- a purely
positional rank can't distinguish them, or protect against a future,
unrelated third read inheriting one's rank once it's migrated away. Keys
now also include the read's own exact source text
(`ast.get_source_segment`), so a collision needs the new read to be
*textually identical* to the one it would replace, not merely occupy the
same rank in the same function.

**No type inference.** "On a value whose declared type resolves to
`RecordType`/`Param`" (the Design section's own phrasing) would need a real
type checker; this script runs before `pip install` and must stay pure
stdlib. Instead it matches the five attribute *names* anywhere in
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

#: The five `Fact[T]`-bridged legacy field names this phase converted
#: (ADR-063 Phase 0's Scope section: `RecordType.bases`/`virtual_bases`/
#: `vtable`/`vptr_offset_bits`, `Param.is_va_list`). `vptr_offset_bits` was
#: first left out of this set on the theory that it was "already
#: meaningfully `None`... never itself ambiguous" -- wrong (Codex review,
#: fresh evidence): `model/entities.py` gives it the identical
#: `_OMITTED_VPTR_OFFSET_BITS` sentinel-based omission bridge the other
#: four fields use (`RecordType()` backfills `Fact.not_collected()`;
#: `RecordType(vptr_offset_bits=None)` backfills `Fact.present(None)` --
#: two different Facts for the identical `None` legacy value), so a direct
#: read has exactly the same unavailable-vs-confirmed ambiguity.
FACT_BRIDGED_ATTRS: frozenset[str] = frozenset(
    {"bases", "virtual_bases", "vtable", "is_va_list", "vptr_offset_bits"}
)

#: The two dataclasses `FACT_BRIDGED_ATTRS` fields live on
#: (`model/entities.py`'s `RecordType`, `model/declarations.py`'s
#: `Param`). Used only by the positional-class-pattern branch below --
#: every other branch matches purely on attribute *name*, with no need to
#: know which class it belongs to.
FACT_BRIDGED_CLASS_NAMES: frozenset[str] = frozenset({"RecordType", "Param"})

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
#: `"<rel>::<qualname>::<attr>::<outer-expr>::<expr-text>::<occurrence>"` --
#: `qualname` is the enclosing function (`<module>` for module-level code,
#: `Class.method` for a method); `outer-expr` is the read's *outermost
#: containing expression* (`_outermost_containing_expr`, e.g. the whole
#: `old_decision(rec.bases)` call, or the whole `not p_old.is_va_list and
#: p_new.is_va_list` boolean test -- never a compound statement's body);
#: `expr-text` is the read's own bare exact source text
#: (`ast.get_source_segment`, e.g. `"p_old.is_va_list"` or `'getattr(t,
#: "vtable", None)'`); `occurrence` is a 1-based rank among reads sharing
#: all four of those, in top-to-bottom (line, column) order -- almost
#: always `1`. Keying on real expression text at two nesting levels (not
#: just a positional ordinal) is what keeps a migrated-and-replaced read
#: from silently inheriting an unrelated new read's key, at either
#: granularity -- see this module's own docstring for the three
#: successive collision classes this key shape had to close. Closing one
#: of these (migrating the reader to check `.status` before trusting the
#: legacy value) removes its entry here; a brand-new, unlisted hit fails
#: the gate.
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
        "abicheck/buildsource/header_graph.py::_flat_structural_type_edges::bases::rt.bases::rt.bases::1",
        "abicheck/buildsource/source_extractors/base.py::entity_from_record::bases::f\"{rec.kind}|size={rec.size_bits}|align={rec.alignment_bits}\"\n        f\"|bases={','.join(rec.bases)}|vt={','.join(rec.vtable)}|{field_repr}\"::rec.bases::1",
        "abicheck/buildsource/source_extractors/base.py::entity_from_record::vtable::f\"{rec.kind}|size={rec.size_bits}|align={rec.alignment_bits}\"\n        f\"|bases={','.join(rec.bases)}|vt={','.join(rec.vtable)}|{field_repr}\"::rec.vtable::1",
        "abicheck/contract_evidence_collect.py::build_type_graph::bases::list(rec.bases) + list(rec.virtual_bases)::rec.bases::1",
        "abicheck/contract_evidence_collect.py::build_type_graph::virtual_bases::list(rec.bases) + list(rec.virtual_bases)::rec.virtual_bases::1",
        'abicheck/diff_cpp_patterns.py::_is_empty_record::vtable::getattr(t, "vtable", None) or []::getattr(t, "vtable", None)::1',
        "abicheck/diff_cxx_rules.py::_transitive_bases::bases::[*start.bases, *start.virtual_bases]::start.bases::1",
        "abicheck/diff_cxx_rules.py::_transitive_bases::bases::stack.extend((*rec.bases, *rec.virtual_bases))::rec.bases::1",
        "abicheck/diff_cxx_rules.py::_transitive_bases::virtual_bases::[*start.bases, *start.virtual_bases]::start.virtual_bases::1",
        "abicheck/diff_cxx_rules.py::_transitive_bases::virtual_bases::stack.extend((*rec.bases, *rec.virtual_bases))::rec.virtual_bases::1",
        "abicheck/diff_cxx_rules.py::virtual_method_addition::vtable::t_old.vtable != t_new.vtable::t_new.vtable::1",
        "abicheck/diff_cxx_rules.py::virtual_method_addition::vtable::t_old.vtable != t_new.vtable::t_old.vtable::1",
        'abicheck/diff_layout.py::_check_vptr_introduced::vptr_offset_bits::f"vptr@{new_rec.vptr_offset_bits}"::new_rec.vptr_offset_bits::1',
        "abicheck/diff_layout.py::_check_vptr_introduced::vptr_offset_bits::not old_rec.vtable\n        and old_rec.vptr_offset_bits is None\n        and new_rec.vtable\n        and new_rec.vptr_offset_bits is not None::new_rec.vptr_offset_bits::1",
        "abicheck/diff_layout.py::_check_vptr_introduced::vptr_offset_bits::not old_rec.vtable\n        and old_rec.vptr_offset_bits is None\n        and new_rec.vtable\n        and new_rec.vptr_offset_bits is not None::old_rec.vptr_offset_bits::1",
        "abicheck/diff_layout.py::_check_vptr_introduced::vtable::not old_rec.vtable\n        and old_rec.vptr_offset_bits is None\n        and new_rec.vtable\n        and new_rec.vptr_offset_bits is not None::new_rec.vtable::1",
        "abicheck/diff_layout.py::_check_vptr_introduced::vtable::not old_rec.vtable\n        and old_rec.vptr_offset_bits is None\n        and new_rec.vtable\n        and new_rec.vptr_offset_bits is not None::old_rec.vtable::1",
        "abicheck/diff_layout.py::_has_layout_descriptor::vptr_offset_bits::rec.data_size_bits is not None\n        or (vtable_facts_reliable and rec.vptr_offset_bits is not None)\n        or bool(rec.base_offsets)::rec.vptr_offset_bits::1",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::not p_old.is_va_list and p_new.is_va_list::p_new.is_va_list::1",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::not p_old.is_va_list and p_new.is_va_list::p_old.is_va_list::1",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::p_old.is_va_list and not p_new.is_va_list::p_new.is_va_list::1",
        "abicheck/diff_param_qualifiers.py::param_va_list_changes::is_va_list::p_old.is_va_list and not p_new.is_va_list::p_old.is_va_list::1",
        "abicheck/diff_stdlib_impl.py::_public_by_value_type_closure::bases::(*record.bases, *record.virtual_bases)::record.bases::1",
        "abicheck/diff_stdlib_impl.py::_public_by_value_type_closure::virtual_bases::(*record.bases, *record.virtual_bases)::record.virtual_bases::1",
        "abicheck/diff_time64.py::_fold_record_tokens::bases::list(rec.bases) + list(rec.virtual_bases)::rec.bases::1",
        "abicheck/diff_time64.py::_fold_record_tokens::virtual_bases::list(rec.bases) + list(rec.virtual_bases)::rec.virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::old_bases_set == new_bases_set and t_old.bases != t_new.bases::t_new.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::old_bases_set == new_bases_set and t_old.bases != t_new.bases::t_old.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::set(t_new.bases)::t_new.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::set(t_old.bases)::t_old.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::str(t_new.bases)::t_new.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::str(t_new.bases)::t_new.bases::2",
        "abicheck/diff_types.py::_diff_type_bases::bases::str(t_old.bases)::t_old.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::str(t_old.bases)::t_old.bases::2",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::set(t_new.virtual_bases)::t_new.virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::set(t_old.virtual_bases)::t_old.virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::str(sorted(t_new.virtual_bases))::t_new.virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::str(sorted(t_old.virtual_bases))::t_old.virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::str(t_new.virtual_bases)::t_new.virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::str(t_old.virtual_bases)::t_old.virtual_bases::1",
        'abicheck/diff_types.py::_diff_type_vtable::vtable::", ".join(t_new.vtable)::t_new.vtable::1',
        'abicheck/diff_types.py::_diff_type_vtable::vtable::", ".join(t_old.vtable)::t_old.vtable::1',
        'abicheck/diff_types.py::_diff_type_vtable::vtable::f"vtable reordered: {name}"\n        if Counter(t_old.vtable) == Counter(t_new.vtable)\n        else f"vtable changed: {name}"::t_new.vtable::1',
        'abicheck/diff_types.py::_diff_type_vtable::vtable::f"vtable reordered: {name}"\n        if Counter(t_old.vtable) == Counter(t_new.vtable)\n        else f"vtable changed: {name}"::t_old.vtable::1',
        "abicheck/diff_types.py::_diff_type_vtable::vtable::len(t_old.vtable) == len(t_new.vtable) and all(\n        vtable_slot_is_override_reuse(\n            old_entry, new_entry, old_funcs, new_funcs, old_types, new_types\n        )\n        for old_entry, new_entry in zip(t_old.vtable, t_new.vtable)\n    )::t_new.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::len(t_old.vtable) == len(t_new.vtable) and all(\n        vtable_slot_is_override_reuse(\n            old_entry, new_entry, old_funcs, new_funcs, old_types, new_types\n        )\n        for old_entry, new_entry in zip(t_old.vtable, t_new.vtable)\n    )::t_old.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::t_old.vtable == t_new.vtable::t_new.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::t_old.vtable == t_new.vtable::t_old.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::zip(t_old.vtable, t_new.vtable)::t_new.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::zip(t_old.vtable, t_new.vtable)::t_old.vtable::1",
        "abicheck/diff_types.py::_new_field_change_kind::virtual_bases::bool(t_new.vtable or t_new.virtual_bases)::t_new.virtual_bases::1",
        "abicheck/diff_types.py::_new_field_change_kind::vtable::bool(t_new.vtable or t_new.virtual_bases)::t_new.vtable::1",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::virtual_bases::list(t_old.virtual_bases) != list(t_new.virtual_bases)::t_new.virtual_bases::1",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::virtual_bases::list(t_old.virtual_bases) != list(t_new.virtual_bases)::t_old.virtual_bases::1",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::vtable::t_old.vtable and t_new.vtable::t_new.vtable::1",
        "abicheck/diff_types.py::_vtable_transition_is_evidenced::vtable::t_old.vtable and t_new.vtable::t_old.vtable::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::bases::list(t_old.bases) != list(t_new.bases)::t_new.bases::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::bases::list(t_old.bases) != list(t_new.bases)::t_old.bases::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::virtual_bases::list(t_old.virtual_bases) != list(t_new.virtual_bases)::t_new.virtual_bases::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::virtual_bases::list(t_old.virtual_bases) != list(t_new.virtual_bases)::t_old.virtual_bases::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::vtable::t_old.vtable and t_new.vtable::t_new.vtable::1",
        "abicheck/diff_types.py::_vtable_transition_rests_on_unresolved_evidence::vtable::t_old.vtable and t_new.vtable::t_old.vtable::1",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::bases::o.bases == n.bases and o.virtual_bases == n.virtual_bases::n.bases::1",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::bases::o.bases == n.bases and o.virtual_bases == n.virtual_bases::o.bases::1",
        'abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::", ".join(n.virtual_bases)::n.virtual_bases::1',
        'abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::", ".join(o.virtual_bases)::o.virtual_bases::1',
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::len(o.virtual_bases) > 1\n            and set(o.virtual_bases) == set(n.virtual_bases)\n            and o.virtual_bases != n.virtual_bases::n.virtual_bases::1",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::len(o.virtual_bases) > 1\n            and set(o.virtual_bases) == set(n.virtual_bases)\n            and o.virtual_bases != n.virtual_bases::n.virtual_bases::2",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::len(o.virtual_bases) > 1\n            and set(o.virtual_bases) == set(n.virtual_bases)\n            and o.virtual_bases != n.virtual_bases::o.virtual_bases::1",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::len(o.virtual_bases) > 1\n            and set(o.virtual_bases) == set(n.virtual_bases)\n            and o.virtual_bases != n.virtual_bases::o.virtual_bases::2",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::len(o.virtual_bases) > 1\n            and set(o.virtual_bases) == set(n.virtual_bases)\n            and o.virtual_bases != n.virtual_bases::o.virtual_bases::3",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::o.bases == n.bases and o.virtual_bases == n.virtual_bases::n.virtual_bases::1",
        "abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::o.bases == n.bases and o.virtual_bases == n.virtual_bases::o.virtual_bases::1",
        "abicheck/diff_vtable_layout.py::_is_polymorphic::bases::rec.bases::rec.bases::1",
        "abicheck/diff_vtable_layout.py::_is_polymorphic::virtual_bases::rec.vtable or rec.virtual_bases::rec.virtual_bases::1",
        "abicheck/diff_vtable_layout.py::_is_polymorphic::vtable::rec.vtable or rec.virtual_bases::rec.vtable::1",
        "abicheck/diff_vtable_layout.py::_secondary_groups::bases::rec.bases::rec.bases::1",
        "abicheck/diff_vtable_layout.py::_secondary_groups::virtual_bases::rec.virtual_bases::rec.virtual_bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::bases::dwarf.bases + dwarf.virtual_bases::dwarf.bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::bases::header.bases + header.virtual_bases::header.bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::virtual_bases::dwarf.bases + dwarf.virtual_bases::dwarf.virtual_bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::virtual_bases::header.bases + header.virtual_bases::header.virtual_bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::vtable::header.has_anonymous_aggregate_fields and not dwarf.vtable::dwarf.vtable::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::vtable::not dwarf.vtable and (\n            not header.fields or header.has_anonymous_aggregate_fields\n        )::dwarf.vtable::1",
        "abicheck/dumper_scoping.py::_kept_signature_haystack::bases::texts.extend(rec.bases)::rec.bases::1",
        "abicheck/dumper_scoping.py::_kept_signature_haystack::virtual_bases::texts.extend(rec.virtual_bases)::rec.virtual_bases::1",
        "abicheck/dwarf_snapshot.py::_DwarfSnapshotBuilder._filter_types_by_reachability::bases::rec.bases + rec.virtual_bases::rec.bases::1",
        "abicheck/dwarf_snapshot.py::_DwarfSnapshotBuilder._filter_types_by_reachability::virtual_bases::rec.bases + rec.virtual_bases::rec.virtual_bases::1",
        "abicheck/export_surface.py::_unresolved_type_edges::bases::(*rec.bases, *rec.virtual_bases)::rec.bases::1",
        "abicheck/export_surface.py::_unresolved_type_edges::virtual_bases::(*rec.bases, *rec.virtual_bases)::rec.virtual_bases::1",
        "abicheck/idioms.py::_collect_base_targets::bases::rec.bases::rec.bases::1",
        "abicheck/idioms.py::_detect_non_virtual_dtor::vtable::not rec.vtable::rec.vtable::1",
        "abicheck/idioms.py::_has_virtual_destructor::vtable::rec.vtable::rec.vtable::1",
        "abicheck/idioms.py::_recognise_factory::vtable::rec is not None and rec.vtable::rec.vtable::1",
        "abicheck/internal_leak.py::_enqueue_record_children::bases::rec.bases::rec.bases::1",
        "abicheck/internal_leak.py::_enqueue_record_children::virtual_bases::rec.virtual_bases::rec.virtual_bases::1",
        "abicheck/surface.py::_walk_exact_type_closure::bases::(*rec_node.bases, *rec_node.virtual_bases)::rec_node.bases::1",
        "abicheck/surface.py::_walk_exact_type_closure::virtual_bases::(*rec_node.bases, *rec_node.virtual_bases)::rec_node.virtual_bases::1",
        "abicheck/surface.py::_walk_type_closure::bases::(*rec_node.bases, *rec_node.virtual_bases)::rec_node.bases::1",
        "abicheck/surface.py::_walk_type_closure::virtual_bases::(*rec_node.bases, *rec_node.virtual_bases)::rec_node.virtual_bases::1",
        "abicheck/surface_graph.py::_build_type_refs::bases::rec.bases::rec.bases::1",
        "abicheck/surface_graph.py::_build_type_refs::virtual_bases::rec.virtual_bases::rec.virtual_bases::1",
        "abicheck/type_reachability.py::_walk_reached_records::bases::[f.type for f in rec.fields] + [\n                *rec.bases,\n                *rec.virtual_bases,\n            ]::rec.bases::1",
        "abicheck/type_reachability.py::_walk_reached_records::virtual_bases::[f.type for f in rec.fields] + [\n                *rec.bases,\n                *rec.virtual_bases,\n            ]::rec.virtual_bases::1",
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


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    """Map ``id(child)`` to its immediate AST parent, for every node in
    *tree*. `ast.walk`/`ast.iter_child_nodes` expose no ancestor link on
    their own, so this is one pass building the reverse edge, consulted by
    :func:`_outermost_containing_expr`.
    """
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _outermost_containing_expr(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST:
    """Walk *node*'s ancestors (via *parents*, from :func:`_parent_map`) up
    through every enclosing `ast.expr`, stopping at the outermost one --
    the whole `old_decision(rec.bases)` call, or the whole `not p_old.
    is_va_list and p_new.is_va_list` boolean test, but never further: the
    next ancestor up is always a *statement* (`Expr`/`If`/`Return`/...),
    whose own body/orelse this must not pull in.

    Deliberately narrower than the *enclosing statement* an earlier
    revision of this function used -- see :func:`unmigrated_fact_reader_
    sites`'s own docstring for why that was wrong (a compound statement's
    body dwarfs and destabilizes the key for no benefit the expression
    boundary doesn't already give).
    """
    current = node
    while id(current) in parents and isinstance(parents[id(current)], ast.expr):
        current = parents[id(current)]
    return current


def _imported_class_aliases(tree: ast.Module) -> dict[str, str]:
    """Map every local name *tree* binds to one of `FACT_BRIDGED_CLASS_NAMES`
    -- either via an `import ... as` (`from abicheck.model import
    RecordType as RT` maps `"RT" -> "RecordType"`), a simple whole-tree
    name assignment (`RT = RecordType` maps the same way; an annotated
    assignment `RT: type = RecordType` maps identically; a further
    `RT2 = RT` chains to `"RecordType"` too, resolved to a fixed point the
    same way `fact_detector_misuse._fact_aliases` chains local aliases),
    or a *qualified* class reference (`import abicheck.model as model; RT
    = model.RecordType` -- resolved immediately by attribute name alone,
    matching the identical name-only stance the `import ... as` branch
    above already takes for *its* qualifying source module) -- back to
    its real name. All are real, found by Codex review with fresh
    evidence: a positional class pattern on such an alias, `case
    RT(_, _, _, _, _, []):`, is invisible to a bare-name check against the
    literal `RecordType`/`Param` spellings, whichever way the alias was
    established -- the annotated-assignment and qualified-reference
    shapes are the same gap the plain-`ast.Assign` fix already closed,
    just reached through a differently-shaped RHS (or a differently-typed
    AST node) that a check keyed on the original shape alone never
    visits. An import/assignment with no local rename needs no entry --
    the bare name already matches directly. Whole-tree, not
    function-scoped: every mechanism here is almost always module level,
    and scanning the whole tree is the same over-approximating-is-safe
    stance this module already takes elsewhere.
    """
    aliases: dict[str, str] = {}
    assign_candidates: list[tuple[str, str]] = []

    def _register_assign(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
        elif (
            isinstance(value, ast.Attribute) and value.attr in FACT_BRIDGED_CLASS_NAMES
        ):
            # `RT = model.RecordType` -- resolves immediately, the same
            # way an `import ... as` alias does, since the qualifying
            # module name is never checked either way.
            aliases[target] = value.attr

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in FACT_BRIDGED_CLASS_NAMES and local != alias.name:
                    aliases[local] = alias.name
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            _register_assign(node.targets[0].id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _register_assign(node.target.id, node.value)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in aliases:
                continue
            if ref in FACT_BRIDGED_CLASS_NAMES:
                aliases[local] = ref
                changed = True
            elif ref in aliases:
                aliases[local] = aliases[ref]
                changed = True
    return aliases


def _builtins_getattr_aliases(
    tree: ast.Module,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(getattr_names, builtins_module_names)``: every local name
    *tree* binds to the real `getattr` builtin (always includes the bare
    `"getattr"` itself, plus any `from builtins import getattr as X`, plus
    a plain assignment chain such as `read_attr = getattr` --
    `read_attr2 = read_attr` chains too, resolved to a fixed point --
    plus a *qualified* assignment such as `read_attr = builtins.getattr`,
    plus an *annotated* assignment of either shape, e.g. `read_attr:
    Callable[..., object] = getattr`), and every local name bound to the
    `builtins` module itself (`import builtins`, `import builtins as b`)
    -- used to recognize `builtins.getattr(...)`/`b.getattr(...)`
    alongside a bare call. All are real (Codex review, fresh evidence,
    three rounds: `import builtins; builtins.getattr(rec, "bases")`,
    `from builtins import getattr as read_attr`, `read_attr = getattr;
    read_attr(rec, "bases")`, combining the qualified-call recognition
    with the plain-assignment chaining as `read_attr = builtins.getattr;
    read_attr(rec, "bases")`, and -- the annotated-assignment spelling of
    either -- `read_attr: Callable[..., object] = getattr` -- are all
    invisible to a scan that only matches the literal bare callee
    `getattr`). Whole-tree, matching `_imported_class_aliases`'s own scope
    for the identical reason -- and the plain-assignment chaining mirrors
    that function's own fixed-point resolution of `RT = RecordType`/
    `RT2 = RT` exactly, just for the builtin callable instead of a class
    name; the annotated-assignment branch mirrors that same function's own
    `ast.AnnAssign` handling.
    """
    getattr_names = {"getattr"}
    builtins_names: set[str] = set()
    assign_candidates: list[tuple[str, str]] = []
    # `local = <module-name>.getattr` -- resolved once, after the walk
    # below has finished collecting every `import builtins` occurrence,
    # since (unlike the plain-name candidates) this needs the *complete*
    # `builtins_names` set to know whether `<module-name>` really is one.
    qualified_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
        elif (
            isinstance(value, ast.Attribute)
            and value.attr == "getattr"
            and isinstance(value.value, ast.Name)
        ):
            qualified_candidates.append((target, value.value.id))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    builtins_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == "getattr":
                    getattr_names.add(alias.asname or alias.name)
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            _add_candidate(node.targets[0].id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    for local, base in qualified_candidates:
        if base in builtins_names:
            getattr_names.add(local)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in getattr_names:
                continue
            if ref in getattr_names:
                getattr_names.add(local)
                changed = True
    return frozenset(getattr_names), frozenset(builtins_names)


def unmigrated_fact_reader_sites(
    tree: ast.Module, rel: str, source: str = ""
) -> list[tuple[str, int, str, str]]:
    """Return one ``(allowlist_key, lineno, attr, qualname)`` per attribute
    read of a `Fact`-bridged field found in *tree* (already parsed from
    *rel*, whose raw text is *source*).

    Only `ast.Load` context counts -- a `Store`/`Del` (an assignment like
    `storage/fact_codec.py`'s legacy-schema backfill `record.vtable = []`)
    is writing the field, not reading it as if it were unambiguous, and is
    not the failure mode this check exists to catch.

    A `getattr(obj, "vtable", ...)` call with the attribute name as a
    literal string constant is a dynamic equivalent of `obj.vtable` and is
    detected too (Codex review, fresh evidence: `diff_cpp_patterns.
    _is_empty_record` reads `vtable` exactly this way, invisible to a scan
    that only matches `ast.Attribute` nodes). A non-literal second
    argument (`getattr(obj, name, ...)`) can't be resolved statically and
    is out of scope, the same "no type inference" limit this module's own
    docstring already states for the attribute case.

    A structural-pattern-matching read (`case RecordType(bases=[]):`) is
    detected too (Codex review, fresh evidence): Python represents a class
    pattern's keyword attributes as `ast.MatchClass.kwd_attrs` (a
    `list[str]`, paired positionally with `kwd_patterns`) rather than as
    an `ast.Attribute` or a `getattr()` call, so it is invisible to both
    branches above -- `case RecordType(bases=[]):` reads `bases` exactly
    as much as `rec.bases` does, and would have collapsed unavailable and
    confirmed-empty the same way. Each matched keyword's own pattern node
    supplies the location; the whole class pattern's source text
    (`RecordType(bases=[])`, not just the one keyword) is the key's
    `expr-text`, since a `MatchClass` node has no location of its own for
    a single keyword. Verified empirically to have zero existing hits in
    `abicheck/` today -- no match/case statement currently patterns on any
    of these five fields.

    A *positional* class pattern (`case RecordType(_, _, _, _, _, []):`)
    is also flagged, though it can't be resolved to a specific field name
    (Codex review, fresh evidence): a positional pattern's Nth element
    binds to `cls.__match_args__[N]`, generated by `@dataclass` in
    declaration order -- deriving that order correctly and keeping it in
    sync with `model/entities.py`/`declarations.py` would need real
    introspection this pure-AST, pre-`pip install` script can't do (the
    same "no type inference" limit already stated below). Rather than
    silently missing this shape the way the keyword-only fix above still
    would have, ANY non-empty positional pattern on a `MatchClass` whose
    `cls` resolves (by bare name, following an import alias -- see below)
    to `RecordType`/`Param` (`FACT_BRIDGED_CLASS_NAMES`) is reported with a
    synthetic `<positional>` attr, on the conservative-by-design principle
    this whole module already applies: a false positive here (a positional
    pattern that happens to touch none of the five bridged fields) costs a
    reviewed baseline entry; a false negative would be silent.

    **An import alias is resolved before that name check (Codex review,
    fresh evidence).** `from abicheck.model import RecordType as RT` then
    `case RT(_, _, _, _, _, []):` names the identical class, but a bare
    `node.cls.id in FACT_BRIDGED_CLASS_NAMES` check rejects `"RT"` outright
    -- invisible to the positional-pattern fix above despite being exactly
    the shape it exists to catch. `_imported_class_aliases()` maps every
    such local alias back to its real name (whole-tree, since an import is
    visible for its entire enclosing scope regardless of where a later
    pattern uses it), and the positional-pattern check resolves through it
    before testing membership.

    **The key also fingerprints the *containing expression*, not only the
    read's own bare expression (Codex review, third round, fresh
    evidence).** Two DIFFERENT call sites sharing the same bare attribute
    spelling -- `old_decision(rec.bases)` and, elsewhere in the same
    function, `keep(rec.bases)` -- previously produced keys differing only
    by occurrence ordinal (`...::rec.bases::1`, `...::rec.bases::2`).
    Migrating `old_decision` away and adding an unrelated third read
    (`unrelated_new_decision(rec.bases)`) anywhere in the same function
    re-numbers the survivors from scratch in encounter order -- `keep`
    silently drops to rank 1 (colliding with `old_decision`'s vacated key,
    harmless since `keep` was already reviewed) but the *new* read then
    lands on rank 2, silently inheriting `keep`'s own baseline entry. Purely
    positional disambiguation among textually-identical bare reads can
    never close this: two different call sites are not the same site no
    matter what they're numbered. Fixed by including each read's
    *outermost containing expression* (climbing every enclosing `ast.expr`
    via a one-pass parent map, stopping at the first statement boundary --
    :func:`_outermost_containing_expr`) in the key alongside the read's own
    bare expression text: `old_decision(rec.bases)` and `keep(rec.bases)`
    now differ at that component regardless of ordinal, closing the common
    case without an ordinal at all. **Deliberately the containing
    *expression*, not the containing *statement*** -- a first version of
    this fix climbed to the nearest `ast.stmt` instead, which for a
    compound statement (`if <test>: <body>`) pulls in the entire body,
    not just the condition: `diff_param_qualifiers.py`'s `if not p_old.
    is_va_list and p_new.is_va_list: changes.append(make_change(...))`
    produced an unwieldy, body-dependent key that would silently break the
    moment anything *inside that body* changed, regardless of whether the
    read itself did. Stopping at the outermost *expression* instead gives
    `not p_old.is_va_list and p_new.is_va_list` -- the whole boolean test,
    still shared by both reads, but none of the body -- so the bare
    expression text (kept, not replaced) is still what tells `p_old.
    is_va_list` and `p_new.is_va_list` apart from each other, exactly as
    the previous round already fixed. A genuinely duplicated expression
    (the identical containing expression appearing twice, with identical
    bare-read text, in one function) still falls back to the ordinal, an
    accepted, narrow residual matching this module's own "false positive
    over false negative" stance throughout.

    **A local alias of the `getattr` builtin itself is resolved too (Codex
    review, fresh evidence).** `read_attr = getattr` then `read_attr(rec,
    "bases")` is the identical dynamic read as `getattr(rec, "bases")`, but
    `_builtins_getattr_aliases()` originally only ever collected the bare
    name and a `from builtins import getattr as X` import -- a plain
    assignment chain was invisible. Fixed by extending that function with
    the same fixed-point assignment-chaining `_imported_class_aliases`
    already does for a class alias (`RT = RecordType`, `RT2 = RT`), just
    for the builtin callable instead of a class name -- see that function's
    own docstring.

    **An augmented assignment is treated as an implicit read of its target
    (Codex review, fresh evidence).** `rec.bases += inherited` updates a
    bridged field, but Python represents the *target* Attribute node with
    `ast.Store` context even though the operation reads the field's
    existing value first, to combine it with the right-hand side, before
    writing the result back -- an ordinary `Store`/`Del` (a plain
    `record.vtable = []` overwrite, this function's own opening paragraph)
    genuinely never reads, but an `AugAssign` target always does. The
    Load-only restriction above therefore missed this shape entirely: the
    target Attribute node is still visited independently by `ast.walk`
    (it's a child of the `AugAssign`), but its `Store` context skips the
    ordinary attribute branch too, so nothing caught it. Fixed with a
    dedicated `ast.AugAssign` branch matching the target's own attribute
    name, keyed on the target Attribute node itself (not the whole
    `AugAssign` statement) so its site/text line up with an ordinary
    attribute read at the same position.
    """
    qualnames = _enclosing_qualnames(tree)
    parents = _parent_map(tree)
    class_aliases = _imported_class_aliases(tree)
    getattr_names, builtins_names = _builtins_getattr_aliases(tree)

    def _expr_text(node: ast.AST) -> str:
        outer = _outermost_containing_expr(node, parents)
        text = ast.get_source_segment(source, outer) if source else None
        return text or "<unavailable>"

    matches: list[tuple[int, int, str, str, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.MatchClass):
            # `case RecordType(bases=[]):` -- structural pattern matching
            # reads a keyword attribute (`kwd_attrs`, a list[str], paired
            # positionally with `kwd_patterns`) without ever producing an
            # `ast.Attribute` or a `getattr()` call (Codex review, fresh
            # evidence): invisible to both branches below.
            # A MatchClass pattern node is not itself an `ast.expr` (it's a
            # `pattern`), so `_expr_text` on it degenerates to the identical
            # `class_text` -- there's no larger *expression* to climb into
            # here, and the whole class pattern is already the right
            # granularity for both key components.
            class_text = (
                ast.get_source_segment(source, node) if source else None
            ) or "<unavailable>"
            qualname = qualnames.get(node.lineno, "<module>")
            for kwd_attr, kwd_pattern in zip(node.kwd_attrs, node.kwd_patterns):
                if kwd_attr not in FACT_BRIDGED_ATTRS:
                    continue
                matches.append(
                    (
                        kwd_pattern.lineno,
                        kwd_pattern.col_offset,
                        kwd_attr,
                        qualname,
                        class_text,
                        class_text,
                    )
                )
            cls_name: str | None = None
            if isinstance(node.cls, ast.Name):
                cls_name = node.cls.id
            elif isinstance(node.cls, ast.Attribute):
                cls_name = node.cls.attr
            resolved_cls_name = (
                class_aliases.get(cls_name, cls_name) if cls_name else None
            )
            if node.patterns and resolved_cls_name in FACT_BRIDGED_CLASS_NAMES:
                # A positional pattern can't be resolved to a specific
                # field name without real `__match_args__` introspection
                # (see this function's own docstring) -- report it
                # unconditionally rather than silently missing it.
                matches.append(
                    (
                        node.lineno,
                        node.col_offset,
                        "<positional>",
                        qualname,
                        class_text,
                        class_text,
                    )
                )
            continue
        record_node: ast.expr
        if (
            isinstance(node, ast.Attribute)
            and node.attr in FACT_BRIDGED_ATTRS
            and isinstance(node.ctx, ast.Load)
        ):
            attr = node.attr
            record_node = node
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Attribute)
            and node.target.attr in FACT_BRIDGED_ATTRS
        ):
            # `rec.bases += inherited` -- Python marks the target `ast.
            # Store`, even though the operation reads the field's existing
            # value before combining it with the right-hand side (Codex
            # review, fresh evidence). The Load-only restriction above
            # therefore misses it entirely: the target Attribute node is
            # still visited independently by `ast.walk` (it's a child of
            # this AugAssign), but its `ctx` is `Store`, so the branch
            # above skips it too -- this is the only place this implicit
            # read is caught, keyed on the target attribute itself (not
            # the whole AugAssign statement) so its site/text line up with
            # an ordinary attribute read.
            attr = node.target.attr
            record_node = node.target
        elif (
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id in getattr_names)
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getattr"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in builtins_names
                )
            )
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            attr = node.args[1].value
            record_node = node
        else:
            continue
        text = (
            ast.get_source_segment(source, record_node) if source else None
        ) or "<unavailable>"
        qualname = qualnames.get(record_node.lineno, "<module>")
        matches.append(
            (
                record_node.lineno,
                record_node.col_offset,
                attr,
                qualname,
                _expr_text(record_node),
                text,
            )
        )
    matches.sort(key=lambda m: (m[0], m[1]))
    occurrence: dict[tuple[str, str, str, str], int] = {}
    sites: list[tuple[str, int, str, str]] = []
    for lineno, _col, attr, qualname, outer_text, text in matches:
        occ_key = (qualname, attr, outer_text, text)
        occurrence[occ_key] = occurrence.get(occ_key, 0) + 1
        key = f"{rel}::{qualname}::{attr}::{outer_text}::{text}::{occurrence[occ_key]}"
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
        source = _read(path)
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        for key, lineno, attr, qualname in unmigrated_fact_reader_sites(
            tree, rel, source
        ):
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

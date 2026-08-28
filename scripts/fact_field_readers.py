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
        'abicheck/diff_layout.py::_check_vptr_introduced::vptr_offset_bits::[\n            make_change(\n                ChangeKind.VPTR_INTRODUCED,\n                symbol=name,\n                name=name,\n                old_value="non-polymorphic",\n                new_value=f"vptr@{new_rec.vptr_offset_bits}",\n            )\n        ]::new_rec.vptr_offset_bits::1',
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
        "abicheck/diff_types.py::_diff_type_bases::bases::changes.append(\n            make_change(\n                ChangeKind.BASE_CLASS_POSITION_CHANGED,\n                symbol=name,\n                name=name,\n                old_value=str(t_old.bases),\n                new_value=str(t_new.bases),\n            )\n        )::t_new.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::changes.append(\n            make_change(\n                ChangeKind.BASE_CLASS_POSITION_CHANGED,\n                symbol=name,\n                name=name,\n                old_value=str(t_old.bases),\n                new_value=str(t_new.bases),\n            )\n        )::t_old.bases::1",
        'abicheck/diff_types.py::_diff_type_bases::bases::changes.append(\n            make_change(\n                ChangeKind.TYPE_BASE_CHANGED,\n                symbol=name,\n                description=f"Base classes changed: {name}",\n                old_value=str(t_old.bases),\n                new_value=str(t_new.bases),\n            )\n        )::t_new.bases::1',
        'abicheck/diff_types.py::_diff_type_bases::bases::changes.append(\n            make_change(\n                ChangeKind.TYPE_BASE_CHANGED,\n                symbol=name,\n                description=f"Base classes changed: {name}",\n                old_value=str(t_old.bases),\n                new_value=str(t_new.bases),\n            )\n        )::t_old.bases::1',
        "abicheck/diff_types.py::_diff_type_bases::bases::old_bases_set == new_bases_set and t_old.bases != t_new.bases::t_new.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::old_bases_set == new_bases_set and t_old.bases != t_new.bases::t_old.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::set(t_new.bases)::t_new.bases::1",
        "abicheck/diff_types.py::_diff_type_bases::bases::set(t_old.bases)::t_old.bases::1",
        'abicheck/diff_types.py::_diff_type_bases::virtual_bases::changes.append(\n                make_change(\n                    ChangeKind.TYPE_BASE_CHANGED,\n                    symbol=name,\n                    description=f"Virtual base classes changed: {name}",\n                    old_value=str(t_old.virtual_bases),\n                    new_value=str(t_new.virtual_bases),\n                )\n            )::t_new.virtual_bases::1',
        'abicheck/diff_types.py::_diff_type_bases::virtual_bases::changes.append(\n                make_change(\n                    ChangeKind.TYPE_BASE_CHANGED,\n                    symbol=name,\n                    description=f"Virtual base classes changed: {name}",\n                    old_value=str(t_old.virtual_bases),\n                    new_value=str(t_new.virtual_bases),\n                )\n            )::t_old.virtual_bases::1',
        'abicheck/diff_types.py::_diff_type_bases::virtual_bases::changes.append(\n            make_change(\n                ChangeKind.BASE_CLASS_VIRTUAL_CHANGED,\n                symbol=name,\n                name=name,\n                detail="; ".join(desc_parts),\n                old_value=str(sorted(t_old.virtual_bases)),\n                new_value=str(sorted(t_new.virtual_bases)),\n            )\n        )::t_new.virtual_bases::1',
        'abicheck/diff_types.py::_diff_type_bases::virtual_bases::changes.append(\n            make_change(\n                ChangeKind.BASE_CLASS_VIRTUAL_CHANGED,\n                symbol=name,\n                name=name,\n                detail="; ".join(desc_parts),\n                old_value=str(sorted(t_old.virtual_bases)),\n                new_value=str(sorted(t_new.virtual_bases)),\n            )\n        )::t_old.virtual_bases::1',
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::set(t_new.virtual_bases)::t_new.virtual_bases::1",
        "abicheck/diff_types.py::_diff_type_bases::virtual_bases::set(t_old.virtual_bases)::t_old.virtual_bases::1",
        'abicheck/diff_types.py::_diff_type_vtable::vtable::f"vtable reordered: {name}"\n        if Counter(t_old.vtable) == Counter(t_new.vtable)\n        else f"vtable changed: {name}"::t_new.vtable::1',
        'abicheck/diff_types.py::_diff_type_vtable::vtable::f"vtable reordered: {name}"\n        if Counter(t_old.vtable) == Counter(t_new.vtable)\n        else f"vtable changed: {name}"::t_old.vtable::1',
        "abicheck/diff_types.py::_diff_type_vtable::vtable::len(t_old.vtable) == len(t_new.vtable) and all(\n        vtable_slot_is_override_reuse(\n            old_entry, new_entry, old_funcs, new_funcs, old_types, new_types\n        )\n        for old_entry, new_entry in zip(t_old.vtable, t_new.vtable)\n    )::t_new.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::len(t_old.vtable) == len(t_new.vtable) and all(\n        vtable_slot_is_override_reuse(\n            old_entry, new_entry, old_funcs, new_funcs, old_types, new_types\n        )\n        for old_entry, new_entry in zip(t_old.vtable, t_new.vtable)\n    )::t_new.vtable::2",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::len(t_old.vtable) == len(t_new.vtable) and all(\n        vtable_slot_is_override_reuse(\n            old_entry, new_entry, old_funcs, new_funcs, old_types, new_types\n        )\n        for old_entry, new_entry in zip(t_old.vtable, t_new.vtable)\n    )::t_old.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::len(t_old.vtable) == len(t_new.vtable) and all(\n        vtable_slot_is_override_reuse(\n            old_entry, new_entry, old_funcs, new_funcs, old_types, new_types\n        )\n        for old_entry, new_entry in zip(t_old.vtable, t_new.vtable)\n    )::t_old.vtable::2",
        'abicheck/diff_types.py::_diff_type_vtable::vtable::make_change(\n        ChangeKind.TYPE_VTABLE_CHANGED,\n        symbol=name,\n        description=description,\n        old_value=", ".join(t_old.vtable),\n        new_value=", ".join(t_new.vtable),\n    )::t_new.vtable::1',
        'abicheck/diff_types.py::_diff_type_vtable::vtable::make_change(\n        ChangeKind.TYPE_VTABLE_CHANGED,\n        symbol=name,\n        description=description,\n        old_value=", ".join(t_old.vtable),\n        new_value=", ".join(t_new.vtable),\n    )::t_old.vtable::1',
        "abicheck/diff_types.py::_diff_type_vtable::vtable::t_old.vtable == t_new.vtable::t_new.vtable::1",
        "abicheck/diff_types.py::_diff_type_vtable::vtable::t_old.vtable == t_new.vtable::t_old.vtable::1",
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
        'abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::changes.append(\n                make_change(\n                    ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED,\n                    symbol=name,\n                    name=name,\n                    old=", ".join(o.virtual_bases),\n                    new=", ".join(n.virtual_bases),\n                )\n            )::n.virtual_bases::1',
        'abicheck/diff_vtable_layout.py::_diff_vtable_layout::virtual_bases::changes.append(\n                make_change(\n                    ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED,\n                    symbol=name,\n                    name=name,\n                    old=", ".join(o.virtual_bases),\n                    new=", ".join(n.virtual_bases),\n                )\n            )::o.virtual_bases::1',
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
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::bases::{\n        _topmost_scope_suffix(b) for b in header.bases + header.virtual_bases\n    }::header.bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::bases::{_topmost_scope_suffix(b) for b in dwarf.bases + dwarf.virtual_bases}::dwarf.bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::virtual_bases::{\n        _topmost_scope_suffix(b) for b in header.bases + header.virtual_bases\n    }::header.virtual_bases::1",
        "abicheck/dumper_layout_backfill.py::_fields_corroborate::virtual_bases::{_topmost_scope_suffix(b) for b in dwarf.bases + dwarf.virtual_bases}::dwarf.virtual_bases::1",
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

    **A parameter default value/annotation is textually part of the
    function's own signature, but evaluates at *def-time*, in whatever
    scope directly, syntactically contains the `def` statement -- not the
    function's own body scope this map would otherwise attribute its
    whole line range to (Codex review, fresh evidence).** `def f(getattr,
    x=getattr(rec, "bases")): ...` -- the default `x=getattr(rec,
    "bases")` evaluates *before* `f`'s own parameters exist, so this call
    genuinely reads the real builtin, but the function's own `[child.
    lineno, end]` range covers its own signature line too, so `_shadowed()`
    saw `f`'s own (not-yet-bound) parameter `getattr` and wrongly excluded
    a real read. A decorator does *not* need this treatment: it sits on a
    line strictly *before* `child.lineno`, already outside the function's
    own range by construction. Fixed by registering each default/
    annotation's own `[lineno, end_lineno]` range under the *current*
    (enclosing, pre-function) qualname -- narrower than the function's own
    range in the ordinary case, so the existing smallest-range-wins
    tie-break below lets it correctly override the function's own broader
    range for just those lines. A default/annotation sharing a line with
    genuine function-*body* code (a one-liner `def f(x=getattr(rec,
    "bases")): return x`) is a real, accepted residual this line-based
    model can't distinguish further -- the same granularity limit this
    function's own docstring already accepts throughout.
    """
    ranges: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str, qualname: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_qualname = f"{prefix}{child.name}"
                end = getattr(child, "end_lineno", child.lineno)
                ranges.append((child.lineno, end, child_qualname))
                all_args = (
                    *child.args.posonlyargs,
                    *child.args.args,
                    *child.args.kwonlyargs,
                    *((child.args.vararg,) if child.args.vararg else ()),
                    *((child.args.kwarg,) if child.args.kwarg else ()),
                )
                def_time_subtrees = [
                    *child.args.defaults,
                    *(d for d in child.args.kw_defaults if d is not None),
                    *(a.annotation for a in all_args if a.annotation is not None),
                ]
                returns = getattr(child, "returns", None)
                if returns is not None:
                    def_time_subtrees.append(returns)
                for subtree in def_time_subtrees:
                    sub_end = getattr(subtree, "end_lineno", subtree.lineno)
                    ranges.append((subtree.lineno, sub_end, qualname))
                visit(child, child_qualname + ".", child_qualname)
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", qualname)
            else:
                visit(child, prefix, qualname)

    visit(tree, "", "<module>")
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


#: Non-`ast.expr` AST node kinds this module still climbs *through* when
#: finding a read's outermost containing expression -- each always sits
#: directly between an expression and its own real expression parent, so
#: stopping at one of these (as the original `isinstance(..., ast.expr)`
#: check alone did) understates the containing expression instead of
#: reaching it (Codex review, fresh evidence): a `keyword`-argument value
#: (`old(value=rec.bases)`) and a comprehension's own `for`/`if` clause
#: (`[x for x in rec.bases]`'s `iter`, or an `if` filter) are both
#: genuinely part of a real enclosing expression (a `Call`, a
#: `ListComp`/`SetComp`/`DictComp`/`GeneratorExp`) one hop further up --
#: `old(value=rec.bases)`/`keep(value=rec.bases)` collapsed to the
#: identical `outer_text = "rec.bases"` (differing only by occurrence
#: rank) before this fix, the exact same-key collision this whole
#: mechanism exists to prevent.
_TRANSPARENT_EXPR_WRAPPER_TYPES = (ast.keyword, ast.comprehension)


def _outermost_containing_expr(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST:
    """Walk *node*'s ancestors (via *parents*, from :func:`_parent_map`) up
    through every enclosing `ast.expr` -- and every transparent wrapper in
    `_TRANSPARENT_EXPR_WRAPPER_TYPES` above, climbed straight through
    rather than counted as the boundary -- stopping at the outermost real
    expression: the whole `old_decision(rec.bases)` call, or the whole
    `not p_old.is_va_list and p_new.is_va_list` boolean test, but never
    further: the next ancestor up is always a *statement*
    (`Expr`/`If`/`Return`/...), whose own body/orelse this must not pull
    in.

    Deliberately narrower than the *enclosing statement* an earlier
    revision of this function used -- see :func:`unmigrated_fact_reader_
    sites`'s own docstring for why that was wrong (a compound statement's
    body dwarfs and destabilizes the key for no benefit the expression
    boundary doesn't already give).
    """
    current = node
    while id(current) in parents:
        parent = parents[id(current)]
        if isinstance(parent, ast.expr) or isinstance(
            parent, _TRANSPARENT_EXPR_WRAPPER_TYPES
        ):
            current = parent
        else:
            break
    return current


def _locally_bound_names(tree: ast.Module) -> dict[str, set[str]]:
    """Map each function's qualname (the identical key `_enclosing_
    qualnames` uses) to every *parameter* name it declares -- not an
    ordinary assignment target, and not a name bound inside a *nested*
    function's own body.

    **Used to exclude a shadowed name from builtin recognition (Codex
    review, fresh evidence).** `def f(getattr, rec): return getattr(rec,
    "bases")` shadows the real `getattr` builtin with an ordinary,
    unrelated local parameter of the identical name -- but the
    builtin-recognition branch in `unmigrated_fact_reader_sites()` had no
    notion of local shadowing at all, unconditionally treating the bare
    name `getattr` as the real builtin regardless of what the enclosing
    function actually bound it to. Reported call sites in an unrelated,
    valid change would then either falsely fail the ERROR-level gate or
    force a misleading baseline entry for a read that was never really a
    builtin call.

    **Deliberately parameters only, not an ordinary assignment target
    (Codex review, fresh evidence: a first revision of this helper also
    covered `ast.Assign`/`ast.AnnAssign` targets, and immediately broke six
    existing tests).** `read_attr = getattr; read_attr(rec, "bases")` is
    exactly `_builtins_getattr_aliases()`'s own alias-resolution mechanism
    -- `read_attr` IS a genuine local assignment target, but treating that
    as "shadowing" is backwards: it's *how* an alias becomes trustworthy,
    not a reason to distrust it. Telling a real shadow (`getattr =
    some_unrelated_value`) apart from a real alias assignment (`read_attr =
    getattr`) needs per-assignment tracing of what each target's own value
    resolves to (exactly what `_builtins_getattr_aliases()`'s internal
    `assign_candidates` already does, but scoped per-function and exposed,
    neither of which it currently is) -- a real, if narrow, follow-up this
    revision does not attempt. A parameter can never be an alias source in
    that same sense (nothing in a function signature assigns FROM
    `getattr`), so restricting to parameters closes the reported false
    positive with no risk of this same conflict.

    Deliberately narrower than the exhaustive binding-form coverage
    `fact_detector_misuse.py`'s own `locally_bound` machinery has grown
    into over many review rounds (comprehension/lambda/match/walrus
    scoping, closures into a *nested* function, global/nonlocal routing) --
    only for the immediate enclosing function of the call site being
    checked, since no evidence has reported any of those more exotic
    shapes shadowing `getattr`/`builtins` specifically (or the identical
    risk for `attrgetter`/`operator`, which shares this same unhandled gap
    -- deliberately not extended there either without reported evidence).
    Extend this the same incremental way if one is ever found, matching
    this module's own established practice of only building the
    generality an actual review finding demonstrates.

    **Known gap, confirmed with a concrete repro rather than left purely
    theoretical (Codex review, fresh evidence): a shadowing parameter that
    is later *rebound* to a genuine alias source is still treated as
    shadowed for the call.** `def f(getattr, rec): getattr =
    builtins.getattr; return getattr(rec, "bases")` -- a real, unremarkable
    read of a bridged field through a locally-rebound name -- currently
    reports no site at all (the sibling `attrgetter`/`operator` shape has
    the identical gap: `def f(operator, rec): import operator; return
    operator.attrgetter("bases")(rec)`). This is exactly the follow-up the
    paragraph above already named as not attempted, now with a real
    example rather than a hypothetical one: correctly distinguishing it
    from a genuine shadow (`getattr = some_unrelated_value`) needs
    *order-aware* per-function tracing -- which assignment to the name is
    the one actually in effect at the call's own position, not merely
    whether *some* recognized-alias assignment exists anywhere in the
    scope. The latter, simpler check is unsound in the other direction:
    `def f(getattr, rec): result = getattr(rec, "bases"); getattr =
    builtins.getattr` calls `getattr` *before* the rebind, while still
    holding the arbitrary parameter value, so an order-blind "was this
    name ever reassigned to a recognized alias" check would wrongly
    exclude a real shadow the same way `_builtins_getattr_aliases()`'s own
    docstring already warns a naive treatment could. Building genuine
    per-position dataflow into this module -- rather than its current
    presence/absence-only model -- is a materially larger change than the
    guard conditions this module has added incrementally so far (it took
    `fact_detector_misuse.py`'s own alias-resolution machinery upwards of
    twenty review rounds to reach exactly this kind of order-sensitivity
    for its own, structurally similar problem), so it is recorded here as
    an accepted, deliberately unfixed gap rather than attempted under
    review pressure. This is a false *negative* (a real dynamic read
    silently passes the gate), the direction this module's own established
    "a false positive is far cheaper than the false negative it closes"
    trade-off argues hardest against accepting -- but an incorrect,
    order-blind attempt at closing it risks trading this false negative
    for a new false positive on a genuine shadow, which is not obviously
    an improvement. Revisit with real per-position tracing if this shape
    is found in practice, not with a heuristic that cannot tell the two
    cases apart.

    **A `def`/`class` statement's own *name* is a locally-bound name too,
    not just a parameter (Codex review, fresh evidence).** `def
    getattr(obj, name): return None` followed by `getattr(rec, "bases")`
    -- an ordinary, unrelated function definition that happens to share
    the builtin-looking name `getattr` -- was still unconditionally
    treated as the real builtin, since only parameters were ever recorded
    as locally bound; a `def`/`class` statement's own binding target
    (Python's ordinary `STORE_NAME`/`STORE_FAST` rule for a def/class
    statement, the identical rule `fact_detector_misuse.py`'s own
    `_def_containing_qualnames` already models) was invisible here. Fixed
    by also recording each `def`/`class`'s own `name` against whichever
    scope directly, syntactically contains it.

    **That "directly, syntactically contains it" scope is NOT simply the
    nearest enclosing *function*, unlike the closure-parent concept
    `_lexical_function_parents` tracks (Codex review, fresh evidence, a
    real regression in the first version of this same fix).** A first
    version tracked a `nearest_func` parameter, skipping class layers the
    same way `_lexical_function_parents` deliberately does for closure
    purposes -- but a method's own *name* does not bind into its
    enclosing function/module namespace at all; it becomes a class
    attribute (`C.getattr`), invisible to an ordinary bare-name lookup
    anywhere outside the class body. Recording it against the skip-class
    `nearest_func` anyway meant `class C: def getattr(self, name): ...`
    made an *unrelated* function elsewhere in the same module -- one with
    no textual relationship to `C` at all -- read as if it had a local
    `getattr` binding, silently excluding its own, genuine
    `getattr(rec, "bases")` call. Fixed by tracking a separate `binding_
    scope: str | None` -- the scope a *bare name binds into*, as opposed
    to `nearest_func`'s "scope a closure looks up through" -- `None`
    while directly inside a class body (nothing recorded there at all,
    matching how `_shadowed()` never queries a class-body scope either,
    since none of this module's qualname machinery models one), and the
    function's own qualname once recursed into a function body (an
    ordinary nested function's own name genuinely does bind into its
    immediately enclosing function, unlike a method's into its class).

    **An import statement binds its target name too, exactly like a
    parameter or a `def`/`class` statement's own name (Codex review,
    fresh evidence).** `from helper import getattr` then `getattr(rec,
    "bases")` -- an ordinary import of an unrelated module's own
    `getattr` symbol, reusing the builtin-looking bare name -- was
    unconditionally treated as the real builtin, since neither `ast.
    Import` nor `ast.ImportFrom` was ever visited here at all. Fixed by
    recording each imported name (`alias.asname` if given, else the
    plain name -- an unaliased dotted `import a.b.c` binds only the
    top-level package `a`, Python's own import-binding rule, the
    identical split `fact_detector_misuse.py`'s own import branch
    already applies) against whichever scope directly contains the
    import statement.

    **Carved out: an import this module already recognizes as a genuine
    alias *source* for a builtin/`operator` symbol must NOT be treated as
    a shadow of itself.** `from builtins import getattr` (bare, no
    `as`), `from builtins import object`/`type`, `from operator import
    attrgetter`, and a bare `import builtins`/`import operator` are all
    already resolved elsewhere in this module (`_builtins_getattr_
    aliases()`, `_unbound_getattribute_receiver_aliases()`,
    `_operator_attrgetter_aliases()`) as evidence that the bound name
    genuinely *is* the real builtin/operator symbol -- recording that
    same binding here too would make `_shadowed()` see it as a local
    shadow and wrongly exclude the very call it was imported to enable
    (e.g. `from operator import attrgetter; attrgetter("bases")(rec)`
    would stop being recognized at all, a real regression, not merely an
    incomplete fix). Every *other* import -- including an aliased
    `from builtins import getattr as g` recognized under the alias `g`,
    which is excluded the identical way -- still binds and shadows
    normally.
    """
    bound: dict[str, set[str]] = {}

    def visit(node: ast.AST, prefix: str, binding_scope: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    if isinstance(child, ast.Import):
                        bound_name = alias.asname or alias.name.split(".", 1)[0]
                        recognized = alias.name in ("builtins", "operator")
                    else:
                        bound_name = alias.asname or alias.name
                        recognized = (
                            child.module == "builtins"
                            and alias.name in ("getattr", "object", "type")
                        ) or (child.module == "operator" and alias.name == "attrgetter")
                    if recognized or binding_scope is None:
                        continue
                    bound.setdefault(binding_scope, set()).add(bound_name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_qualname = f"{prefix}{child.name}"
                if binding_scope is not None:
                    bound.setdefault(binding_scope, set()).add(child.name)
                all_args = (
                    *child.args.posonlyargs,
                    *child.args.args,
                    *child.args.kwonlyargs,
                    *((child.args.vararg,) if child.args.vararg else ()),
                    *((child.args.kwarg,) if child.args.kwarg else ()),
                )
                for arg in all_args:
                    bound.setdefault(child_qualname, set()).add(arg.arg)
                visit(child, child_qualname + ".", child_qualname)
            elif isinstance(child, ast.ClassDef):
                if binding_scope is not None:
                    bound.setdefault(binding_scope, set()).add(child.name)
                visit(child, f"{prefix}{child.name}.", None)
            else:
                visit(child, prefix, binding_scope)

    visit(tree, "", "<module>")
    return bound


def _lexical_function_parents(tree: ast.Module) -> dict[str, str]:
    """Map each function's qualname (the identical key `_enclosing_
    qualnames`/`_locally_bound_names` use) to its nearest *enclosing
    function's* qualname -- skipping any intervening class scope -- or
    `"<module>"` if it has none.

    Used to widen `_shadowed()`'s shadowing check to a call's *entire*
    lexical scope chain, not just its own innermost function (Codex
    review, fresh evidence): `def outer(getattr): def inner(rec): return
    getattr(rec, "bases")` -- `getattr` is an arbitrary callable captured
    from `outer`'s own parameter via Python's ordinary closure rule, but
    `inner` binds no parameter of that name itself, so a check restricted
    to `inner`'s own `locally_bound` entry never saw it, falsely treating
    the closed-over parameter as the real `getattr` builtin.

    A standalone copy of `fact_detector_misuse.py`'s identical-purpose
    helper (see `FACT_FIELD_NAMES`'s own docstring for why these two leaf
    modules stay decoupled), simplified to this module's own coarser,
    dot-joined qualname scheme (no `#lineno` disambiguator -- an
    `@overload` stub colliding with its real implementation is an
    existing, accepted characteristic of this module's qualnames already,
    not a new risk this helper introduces).
    """
    parents: dict[str, str] = {}

    def visit(node: ast.AST, prefix: str, nearest_func: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                parents[qualname] = nearest_func
                visit(child, qualname + ".", qualname)
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", nearest_func)
            else:
                visit(child, prefix, nearest_func)

    visit(tree, "", "<module>")
    return parents


def _imported_class_aliases(tree: ast.Module) -> dict[str, str]:
    """Map every local name *tree* binds to one of `FACT_BRIDGED_CLASS_NAMES`
    -- either via an `import ... as` (`from abicheck.model import
    RecordType as RT` maps `"RT" -> "RecordType"`), a simple whole-tree
    name assignment (`RT = RecordType` maps the same way; a *chained*
    assignment, `RT = Alias = RecordType`, maps every plain-name target
    identically, since they all receive the same RHS; an annotated
    assignment `RT: type = RecordType` maps identically too; a further
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
    established -- the chained-assignment, annotated-assignment, and
    qualified-reference shapes are the same gap the plain-`ast.Assign` fix
    already closed, just reached through a differently-shaped assignment
    (or a differently-typed AST node) that a check keyed on the original
    shape alone never visits. An import/assignment with no local rename
    needs no entry -- the bare name already matches directly. Whole-tree,
    not function-scoped: every mechanism here is almost always module
    level, and scanning the whole tree is the same
    over-approximating-is-safe stance this module already takes
    elsewhere.
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
        elif isinstance(node, ast.Assign):
            # Every plain-`Name` target, not only a lone one -- a chained
            # assignment (`RT = Alias = RecordType`) gives every target
            # the identical RHS (Codex review, fresh evidence: the
            # single-target restriction wrongly excluded this ordinary,
            # unrelated shape too).
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _register_assign(target.id, node.value)
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
    `read_attr2 = read_attr` chains too, resolved to a fixed point, and a
    *chained* assignment (`read1 = read2 = getattr`) marks every plain-
    name target the same way -- plus a *qualified* assignment such as
    `read_attr = builtins.getattr`, plus an *annotated* assignment of
    either shape, e.g. `read_attr: Callable[..., object] = getattr`), and
    every local name bound to the `builtins` module itself (`import
    builtins`, `import builtins as b`, or a plain assignment alias of an
    already-known one, `b = builtins` -- resolved to a fixed point the
    same way a `getattr` alias chain already is) -- used to recognize
    `builtins.getattr(...)`/`b.getattr(...)` alongside a bare call. All
    are real (Codex review, fresh evidence, five rounds: `import
    builtins; builtins.getattr(rec, "bases")`, `from builtins import
    getattr as read_attr`, `read_attr = getattr; read_attr(rec,
    "bases")`, combining the qualified-call recognition with the
    plain-assignment chaining as `read_attr = builtins.getattr;
    read_attr(rec, "bases")`, the annotated-assignment spelling of either
    -- `read_attr: Callable[..., object] = getattr` -- a chained
    assignment, `read1 = read2 = getattr` -- and a plain assignment alias
    of the `builtins` module itself, `b = builtins` -- are all invisible
    to a scan that only matches the literal bare callee `getattr`).
    Whole-tree, matching `_imported_class_aliases`'s own scope for the
    identical reason -- and the plain-assignment chaining mirrors that
    function's own fixed-point resolution of `RT = RecordType`/`RT2 = RT`
    exactly, just for the builtin callable (and, now, the `builtins`
    module name itself) instead of a class name; the annotated-assignment
    and chained-assignment branches mirror that same function's own
    `ast.AnnAssign`/multi-target `ast.Assign` handling.
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
        elif isinstance(node, ast.Assign):
            # Every plain-`Name` target, not only a lone one -- a chained
            # assignment (`read1 = read2 = getattr`) gives every target
            # the identical RHS (Codex review, fresh evidence: the
            # single-target restriction wrongly excluded this ordinary,
            # unrelated shape too, the identical gap fixed in
            # `_imported_class_aliases`'s own `ast.Assign` branch).
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    # `b = builtins` -- a plain assignment alias of the `builtins` module
    # itself, resolved to a fixed point the same way a `getattr` alias
    # chain already is, reusing the identical `assign_candidates` list
    # (Codex review, fresh evidence: `import builtins; b = builtins`
    # then `b.getattr(rec, "bases")` was invisible, since `builtins_names`
    # was only ever populated from a real `import` statement). Resolved
    # *before* `qualified_candidates` below, so `b.getattr(...)` is
    # recognized through the now-expanded `builtins_names` too.
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in builtins_names:
                continue
            if ref in builtins_names:
                builtins_names.add(local)
                changed = True
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


def _unbound_getattribute_receiver_aliases(tree: ast.Module) -> frozenset[str]:
    """Return every local name *tree* binds to the real `object` or `type`
    builtin -- always includes the bare `"object"`/`"type"` themselves,
    plus any `from builtins import object as O`/`from builtins import type
    as T`, plus a plain assignment chain (`O = object; O2 = O` chains too,
    resolved to a fixed point, mirroring `_builtins_getattr_aliases()`'s
    own `getattr`-alias chaining exactly).

    Used by the unbound `__getattribute__` recognition branch (Codex
    review, fresh evidence): `from builtins import object as O;
    O.__getattribute__(rec, "bases")` is the identical dynamic read as the
    unaliased `object.__getattribute__(rec, "bases")` spelling, but the
    receiver-name check there originally matched only the two literal
    strings `"object"`/`"type"`, missing this ordinary import-alias form
    entirely.
    """
    names: set[str] = {"object", "type"}
    assign_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name in ("object", "type"):
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in names:
                continue
            if ref in names:
                names.add(local)
                changed = True
    return frozenset(names)


def _operator_attrgetter_aliases(
    tree: ast.Module,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return `(attrgetter_names, operator_module_names)`: every local name
    *tree* binds to the real `operator.attrgetter` callable (the bare
    `"attrgetter"` itself only once a real `from operator import
    attrgetter` is found -- see this docstring's own "Seeded only from a
    verified import" paragraph below for why -- plus any `... as X`
    alias), and every local name bound to the `operator` module itself
    (the bare `"operator"` only once a real `import operator` is found,
    plus any `import operator as X`).

    An ordinary `import operator as op` or `from operator import attrgetter
    as ag` reads the identical legacy field as the unaliased spellings
    (Codex review, fresh evidence): `op.attrgetter("bases")(rec)`/
    `ag("bases")(rec)` are real, unremarkable Python, and the caller's own
    exact-name matching (`"operator"`/`"attrgetter"` only) missed both --
    the identical gap `_builtins_getattr_aliases()` above closes for
    `getattr`/`builtins`, applied to this pair instead.

    **A plain-assignment alias of either name is resolved too (Codex
    review, fresh evidence, second round on this same helper).** `import
    operator as op; op2 = op; op2.attrgetter("bases")(rec)` and `from
    operator import attrgetter as ag; ag2 = ag; ag2("bases")(rec)` are the
    identical dynamic reads as the unaliased/singly-aliased spellings --
    this function's own first revision claimed a `Call`-typed value (the
    *result* of `attrgetter(...)`) has no simple assignment shape to chain
    through, which is true but irrelevant: `op`/`ag`/`attrgetter` are
    themselves ordinary references (a module object, a builtin callable)
    *before* being called, and a plain `ast.Name`-valued assignment of
    either chains exactly the way `_builtins_getattr_aliases()`'s own
    `getattr`/`builtins` resolution already does. Fixed by reusing the
    identical fixed-point assignment-chaining pattern -- every plain-`Name`
    target of an `ast.Assign` (including every target of a chained
    assignment, `op2 = op3 = op`) or `ast.AnnAssign` is collected as a
    candidate, then repeatedly folded into either name set until a pass
    adds nothing new. The `_is_attrgetter_constructor_call()`'s own
    docstring wording about `attrgetter` getting "no local-alias
    resolution" refers to a *different* thing -- a value ASSIGNED FROM a
    *constructed getter* (`getter = attrgetter(...); getter(rec)`, still
    correctly out of scope, see that function's own docstring) -- not the
    module/callable references resolved here.

    **Seeded only from a verified import, not unconditionally the way
    `_builtins_getattr_aliases()` seeds bare `"getattr"` (Codex review,
    fresh evidence).** `getattr` is a real Python builtin, always in
    scope with no import required, so seeding it unconditionally is
    correct -- but `attrgetter`/`operator` are not builtins; they mean
    nothing until a real `import operator`/`from operator import
    attrgetter` actually happens. Unconditionally seeding the bare
    spellings anyway meant a module-level `def attrgetter(name): ...` (an
    ordinary, unrelated local function reusing the name, no `operator`
    import anywhere in the file) followed by `attrgetter("bases")(rec)`,
    or `operator = SomeUnrelatedHelper()` followed by `operator.
    attrgetter("bases")(rec)`, both read as the real standard-library
    callable -- and neither is a *parameter* shadow, the only shadow
    shape `_shadowed()` ever checks, so nothing excluded either. Fixed by
    starting both sets empty and relying entirely on the import-detection
    walk below (which already adds the exact bare spelling whenever a
    real, unaliased `import operator`/`from operator import attrgetter`
    is found, via its own `alias.asname or alias.name` fallback) --
    exactly the same "no import, no identity" contract `_builtins_
    getattr_aliases()` already applies to the bare `"builtins"` module
    name.
    """
    attrgetter_names: set[str] = set()
    operator_names: set[str] = set()
    assign_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "operator":
            for alias in node.names:
                if alias.name == "attrgetter":
                    attrgetter_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "operator":
                    operator_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in attrgetter_names:
                continue
            if ref in attrgetter_names:
                attrgetter_names.add(local)
                changed = True
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in operator_names:
                continue
            if ref in operator_names:
                operator_names.add(local)
                changed = True
    return frozenset(attrgetter_names), frozenset(operator_names)


def _is_attrgetter_constructor_call(
    node: ast.expr, attrgetter_names: frozenset[str], operator_names: frozenset[str]
) -> bool:
    """True for a `Call` node constructing an `operator.attrgetter(...)`
    getter -- the qualified spelling through any resolved alias of the
    `operator` module (`operator_names`), or the bare spelling through any
    resolved alias of `attrgetter` itself (`attrgetter_names`, always
    covering `from operator import attrgetter` and its own `as` alias, see
    `_operator_attrgetter_aliases`) -- with at least one positional
    argument (the attribute name(s) to read). The caller is responsible for
    checking that the requested arguments are literal, single-name strings
    matching a recognized field -- this helper only recognizes the
    *constructor*, not the field(s) it will read. Matched wherever the
    constructor call itself occurs -- immediately invoked
    (`attrgetter("bases")(rec)`), assigned to an intermediate variable
    before being called (`getter = attrgetter("bases"); getter(rec)`), or
    handed to another function as a callback (`sorted(records,
    key=attrgetter("bases"))`) -- since the field will be read on whatever
    the constructed getter is eventually called with, regardless of how
    that call happens (Codex review, fresh evidence: matching only an
    immediate outer call missed the equally common callback spelling
    entirely; see `unmigrated_fact_reader_sites()`'s own attrgetter branch
    for the full reasoning). This is a genuine improvement over needing
    dedicated alias tracking for the constructed *getter object* itself
    (no `x = attrgetter(...)` equivalent of `_builtins_getattr_aliases()`'s
    own alias-chain tracking is needed here, since the constructor call is
    matched directly rather than needing to be traced through to wherever
    it's eventually called).
    """
    return (
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "attrgetter"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in operator_names
            )
            or (isinstance(node.func, ast.Name) and node.func.id in attrgetter_names)
        )
        and len(node.args) >= 1
    )


def _attrgetter_matched_name(node: ast.Call) -> str:
    """Return the bare local name that made `_is_attrgetter_constructor_
    call()` return `True` for *node* -- the qualifying module name for the
    `operator.attrgetter(...)` spelling, or the callable name itself for
    the bare `attrgetter(...)` spelling. Used to check that name against
    `_locally_bound_names()` for shadowing (Codex review, fresh evidence:
    an unrelated local parameter named `operator`/`attrgetter` shadows the
    real module/callable exactly the way one named `getattr` can, but this
    call site never consulted the shadowing check at all). The caller is
    responsible for confirming `_is_attrgetter_constructor_call()` already
    returned `True` for *node* -- this only re-derives which of its two
    recognized shapes actually matched, narrowly typed so the caller
    doesn't need its own unchecked `ast.Attribute`/`ast.Name` assumption.
    """
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return node.func.value.id
    assert isinstance(node.func, ast.Name)
    return node.func.id


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

    **Two more standard dynamic-attribute-reading forms are detected too
    (Codex review, fresh evidence).** `operator.attrgetter("bases")(rec)`
    (or the bare `attrgetter(...)` spelling reached via `from operator
    import attrgetter`, or an `import operator as op`/`from operator import
    attrgetter as ag` alias of either -- resolved via `_operator_attrgetter_
    aliases()`, the identical import-alias mechanism `_builtins_getattr_
    aliases()` already provides for `getattr`/`builtins`) and `rec.
    __getattribute__("bases")`/`object.__getattribute__(rec, "bases")` both
    read `rec.bases` exactly as much as the attribute/`getattr()` forms
    above do -- `getattr()` is itself defined in terms of `__getattribute__`,
    and `attrgetter` is the standard-library callable-returning equivalent.
    `attrgetter` additionally accepts *any number* of positional field
    names and reads every one of it -- `attrgetter("size_bits", "bases")
    (rec)` reads `bases` too, not only the first argument (Codex review,
    fresh evidence) -- so every literal, string-constant argument matching
    a bridged name is inspected and reported independently, handled as its
    own top-level case rather than folded into the single-attribute chain
    below. Both dynamic forms stay scoped to the same "no type inference"
    limit as the rest of this scan: only a literal-string field name is
    recognized per argument (`attrgetter("a.b")`, which chains a *second*
    attribute access, is out of scope, same as a non-literal `getattr()`
    default). Matched at the point of *construction*, not only an
    immediate call -- see `_is_attrgetter_constructor_call()`'s own
    docstring for why this also catches a getter assigned to an
    intermediate variable or handed to another function as a callback,
    without needing dedicated alias tracking the way `getattr` itself
    does.

    **A local binding that shadows `getattr`/`builtins` is excluded from
    the bare-name builtin match (Codex review, fresh evidence).** `def
    f(getattr, rec): return getattr(rec, "bases")` -- an ordinary,
    unrelated function parameter reusing the name `getattr` -- was
    unconditionally treated as the real `getattr` builtin regardless of
    what the enclosing function actually bound it to, blocking a valid,
    unrelated change with a misleading error. Fixed with a new
    `_locally_bound_names()` (this function's own parameters plus any
    ordinary same-function assignment target, scoped narrower than
    `fact_detector_misuse.py`'s own exhaustive shadowing machinery --
    see that helper's own docstring for exactly what's covered and why)
    consulted at each `getattr`/`builtins` match site: a name shadowed in
    the call's own enclosing function is excluded from the match.
    """
    qualnames = _enclosing_qualnames(tree)
    parents = _parent_map(tree)
    class_aliases = _imported_class_aliases(tree)
    getattr_names, builtins_names = _builtins_getattr_aliases(tree)
    object_type_names = _unbound_getattribute_receiver_aliases(tree)
    attrgetter_names, operator_names = _operator_attrgetter_aliases(tree)
    locally_bound = _locally_bound_names(tree)
    lexical_parents = _lexical_function_parents(tree)

    def _shadowed(call_node: ast.Call, name: str) -> bool:
        qualname = qualnames.get(call_node.lineno, "<module>")
        # Walk the call's entire lexical scope chain, not just its own
        # innermost function (Codex review, fresh evidence): a nested
        # function that binds no parameter of its own can still have the
        # name shadowed via an ordinary Python closure over an *enclosing*
        # function's own parameter -- see `_lexical_function_parents`'s
        # own docstring for the exact repro.
        while True:
            if name in locally_bound.get(qualname, ()):
                return True
            if qualname == "<module>":
                return False
            qualname = lexical_parents.get(qualname, "<module>")

    def _is_mapping_receiver(value: ast.expr) -> bool:
        """True if *value* is `vars(rec)` (an ordinary bare-name call,
        gated on `_shadowed()` the same way `getattr`/`attrgetter` are)
        or `rec.__dict__` (an attribute access, nothing for a local
        binding to shadow) -- shared by both the subscript
        (`vars(rec)["bases"]`) and `.get()` (`vars(rec).get("bases")`,
        Codex review, fresh evidence) mapping-read forms, so the two
        can't independently drift on what counts as "an instance's own
        mapping"."""
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "vars"
            and len(value.args) == 1
            and not _shadowed(value, "vars")
        ):
            return True
        return isinstance(value, ast.Attribute) and value.attr == "__dict__"

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
        if (
            isinstance(node, ast.Call)
            and _is_attrgetter_constructor_call(node, attrgetter_names, operator_names)
            and not _shadowed(node, _attrgetter_matched_name(node))
        ):
            # `operator.attrgetter("bases")` (or a resolved alias of either
            # name) *constructs* a getter that will read `bases` off
            # whatever it's later called with -- reported at the point of
            # construction, not only when it's called *immediately*
            # (`operator.attrgetter("bases")(rec)`). Matching only the
            # doubly-called shape missed the equally common callback
            # spelling entirely (Codex review, fresh evidence):
            # `sorted(records, key=operator.attrgetter("bases"))` and
            # `map(attrgetter("bases"), records)` both construct the
            # identical getter, just hand it to another function instead
            # of calling it themselves -- the read still happens, on
            # whatever `sorted`/`map` eventually calls it with. Matching
            # the constructor call directly, regardless of how its result
            # is used, closes this the same conservative-by-design way
            # every other branch here does: a false positive here (a
            # constructed-but-never-called getter) costs a reviewed
            # baseline entry; a false negative would be silent. Handled as
            # its own top-level case (like `MatchClass` above), not folded
            # into the single-attribute chain below, since `attrgetter`
            # accepts *any number* of positional field names and reads
            # every one of them (Codex review, fresh evidence:
            # `attrgetter("size_bits", "bases")(rec)` reads `bases` too,
            # not only the first argument) -- each literal, string-constant
            # argument matching a bridged name is its own real read,
            # reported independently. A non-literal or dotted-name argument
            # (`attrgetter("a.b")`, chaining a *second* attribute access)
            # stays out of scope, the same "no type inference" limit the
            # plain `getattr` case already accepts for a non-literal
            # default.
            # Fingerprinted the same way every other reader form is
            # (Codex review, fresh evidence): `outer_text` climbs to the
            # read's own *outermost containing expression* via
            # `_expr_text()`, distinct from `text`, the constructor call's
            # own bare source -- an earlier revision used the call's own
            # bare text for both slots, so `old_decision(attrgetter(
            # "bases")(rec))` and `keep(attrgetter("bases")(rec))` produced
            # the identical key. Migrating the first reader while adding an
            # unrelated new one at the same rank would then have silently
            # reused the vacated key, the exact collision
            # `_outermost_containing_expr()` exists to close for every
            # other form. `_outermost_containing_expr()` still climbs
            # through an immediate outer call (`ast.Call` is itself an
            # `ast.expr`), so the doubly-called shape's `outer_text` is
            # unaffected by matching the inner constructor call instead of
            # the outer one.
            text = (
                ast.get_source_segment(source, node) if source else None
            ) or "<unavailable>"
            outer_text = _expr_text(node)
            qualname = qualnames.get(node.lineno, "<module>")
            for call_arg in node.args:
                if (
                    isinstance(call_arg, ast.Constant)
                    and isinstance(call_arg.value, str)
                    and call_arg.value in FACT_BRIDGED_ATTRS
                ):
                    matches.append(
                        (
                            node.lineno,
                            node.col_offset,
                            call_arg.value,
                            qualname,
                            outer_text,
                            text,
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
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id in getattr_names
                    and not _shadowed(node, node.func.id)
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getattr"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in builtins_names
                    and not _shadowed(node, node.func.value.id)
                )
            )
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattribute__"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            # `rec.__getattribute__("bases")` -- the bound-method spelling
            # of the same dynamic read `getattr(rec, "bases")` performs,
            # and the one every object's own `getattr()` implementation is
            # defined in terms of (Codex review, fresh evidence).
            attr = node.args[0].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattribute__"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in object_type_names
            and not _shadowed(node, node.func.value.id)
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            # `object.__getattribute__(rec, "bases")` -- the unbound-method
            # spelling used to bypass an instance's own overridden
            # `__getattribute__`, reading `rec.bases` exactly the same way.
            # `object_type_names` also covers an import alias of either
            # builtin (`from builtins import object as O; O.
            # __getattribute__(rec, "bases")` -- Codex review, fresh
            # evidence), not just the two literal spellings. `object`/
            # `type`/an alias of either are ordinary names here, so a
            # parameter shadowing one (`def f(object, rec): return object.
            # __getattribute__(rec, "bases")`) must not match -- the same
            # exclusion the getattr/attrgetter branches above already
            # apply (Codex review, fresh evidence).
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and node.slice.value in FACT_BRIDGED_ATTRS
            and _is_mapping_receiver(node.value)
        ):
            # `vars(rec)["bases"]` / `rec.__dict__["bases"]` -- both read
            # the normalized legacy value the same way `rec.bases` does,
            # through the instance's own `__dict__` mapping rather than
            # attribute-lookup machinery (Codex review, fresh evidence).
            # Only a literal string key is in scope -- a computed key
            # (`vars(rec)[name]`) can't be resolved statically, the
            # identical "no type inference" limit every other dynamic form
            # here already accepts.
            attr = node.slice.value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_mapping_receiver(node.func.value)
            and len(node.args) >= 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            # `vars(rec).get("bases")` / `rec.__dict__.get("bases")` --
            # the `dict.get()` spelling of the identical mapping read the
            # subscript branch above already catches, with the same
            # optional-default shape `getattr()`'s own second argument
            # already has (Codex review, fresh evidence: reads the exact
            # same normalized legacy value, invisible to the subscript
            # branch since neither is an `ast.Subscript`). An optional
            # second argument (the default) is accepted but not
            # inspected, matching how `getattr()`'s own third argument
            # is treated elsewhere in this module.
            attr = node.args[0].value
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
    legacy field (`RecordType.bases`/`virtual_bases`/`vtable`/
    `vptr_offset_bits`, `Param.is_va_list`) directly, without the read
    being a previously reviewed, `KNOWN_UNMIGRATED_READERS`-baselined site.

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

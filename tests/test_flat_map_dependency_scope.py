"""Dependency scoping for constants/typedefs (``extract/flat_map_dependency_scope``).

Bug class: *a declaration kind that scoping forwards verbatim through its
closing ``dataclasses.replace``* -- PR #1001 closed it for ``semantic_ir``;
the flat ``constants``/``typedefs`` maps had the same hole, so a constant from
an ``--exclude-header``-ed header gated a comparison. The invariants below are
stated against an oracle computed independently of the implementation (a
per-key re-derivation from the generated headers), over generated inputs.
"""

from __future__ import annotations

import copy
import pickle

from hypothesis import given, settings, strategies as st

from abicheck.extract.flat_map_dependency_scope import scope_flat_maps
from abicheck.model import AbiSnapshot
from abicheck.model.declaration_headers import (
    HeaderAttributedMap,
    attributed,
    declaring_header,
    has_attribution,
)
from abicheck.model.fact import Fact
from abicheck.model.identity import (
    ScopePath,
    entity_id_for_constant,
    entity_id_for_typedef,
)
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import (
    CanonicalEntity,
    SemanticIR,
    semantic_ir_conflict_key,
)

DEP_HEADERS = ("/usr/include/dep.h", "/proj/excluded.h")
OWN_HEADERS = ("/proj/api.h", "/proj/other.h")
HEADERS = (*DEP_HEADERS, *OWN_HEADERS, "")  # "" = unknown origin


def _is_dep(header: str | None) -> bool:
    return header in DEP_HEADERS


names = st.text(alphabet="abcxyz_", min_size=1, max_size=4)
entries = st.dictionaries(names, st.sampled_from(HEADERS), max_size=8)


def _snapshot(constants: dict[str, str], typedefs: dict[str, str]) -> AbiSnapshot:
    c = attributed((k, "1", h) for k, h in constants.items())
    t = attributed((k, "int", h) for k, h in typedefs.items())
    return AbiSnapshot(
        library="x",
        version="1",
        constants=c,
        typedefs=t,
        typedefs_qualified=attributed(
            (f"ns::{k}", "int", h) for k, h in typedefs.items()
        ),
        constant_entity_ids={k: entity_id_for_constant(ScopePath(()), k) for k in c},
        typedef_entity_ids={
            f"ns::{k}": entity_id_for_typedef(ScopePath(()), k) for k in t
        },
    )


def _ir(snap: AbiSnapshot) -> tuple[SemanticIR, dict[str, str]]:
    occ = {
        OccurrenceId(eid): CanonicalEntity(canonical_spelling=Fact.present("v"))
        for eid in [
            *snap.constant_entity_ids.values(),
            *snap.typedef_entity_ids.values(),
        ]
    }
    conflicts = {semantic_ir_conflict_key(o, "canonical_spelling"): "x" for o in occ}
    return SemanticIR(occurrences=occ), conflicts


@settings(max_examples=300, deadline=None)
@given(constants=entries, typedefs=entries, referenced=st.lists(names, max_size=4))
def test_scoping_matches_independent_oracle(constants, typedefs, referenced):
    snap = _snapshot(constants, typedefs)
    ir, conflicts = _ir(snap)
    haystack = " ".join(f"{r} *" for r in referenced)
    out = scope_flat_maps(snap, _is_dep, haystack, ir, conflicts)

    # Oracle: a constant survives iff its header is not a dependency one; a
    # typedef additionally survives when the kept signatures name it.
    want_c = {k for k, h in constants.items() if h not in DEP_HEADERS}
    want_t = {k for k, h in typedefs.items() if h not in DEP_HEADERS or k in referenced}
    assert set(out.constants) == want_c
    assert set(out.typedefs) == want_t
    assert set(out.typedefs_qualified) == {f"ns::{k}" for k in want_t}
    # Sidecars follow their maps exactly.
    assert set(out.constant_entity_ids) == want_c
    assert set(out.typedef_entity_ids) == {f"ns::{k}" for k in want_t}
    # The IR never holds a flat entity the flat maps dropped, and keeps every one they kept.
    kept_ids = {*out.constant_entity_ids.values(), *out.typedef_entity_ids.values()}
    assert out.semantic_ir is not None
    assert {o.entity_id for o in out.semantic_ir.occurrences} == kept_ids
    assert len(out.semantic_ir_conflicts) == len(out.semantic_ir.occurrences)
    # Attribution survives scoping, so a second pass is idempotent.
    assert has_attribution(out.constants)
    again = scope_flat_maps(
        _snapshot_from(out),
        _is_dep,
        haystack,
        out.semantic_ir,
        out.semantic_ir_conflicts,
    )
    assert again.constants == out.constants and again.typedefs == out.typedefs


def _snapshot_from(out) -> AbiSnapshot:
    return AbiSnapshot(
        library="x",
        version="1",
        constants=out.constants,
        typedefs=out.typedefs,
        typedefs_qualified=out.typedefs_qualified,
        constant_entity_ids=out.constant_entity_ids,
        typedef_entity_ids=out.typedef_entity_ids,
    )


@given(constants=entries, typedefs=entries)
def test_unattributed_maps_pass_through_as_the_same_objects(constants, typedefs):
    """A loaded snapshot has plain dicts: scoping must leave them exactly alone."""
    snap = AbiSnapshot(
        library="x",
        version="1",
        constants={k: "1" for k in constants},
        typedefs={k: "int" for k in typedefs},
    )
    out = scope_flat_maps(snap, lambda _h: True, "", None, {})
    assert out.constants is snap.constants
    assert out.typedefs is snap.typedefs
    assert out.typedefs_qualified is snap.typedefs_qualified


@given(entries)
def test_attributed_map_is_a_dict_and_survives_copy_and_pickle(headers):
    m = attributed((k, "v", h) for k, h in headers.items())
    assert m == {k: "v" for k in headers}
    for clone in (copy.copy(m), copy.deepcopy(m), pickle.loads(pickle.dumps(m))):
        assert isinstance(clone, HeaderAttributedMap)
        assert clone == m
        assert all(declaring_header(clone, k) == (h or "") for k, h in headers.items())


def test_later_entry_wins_value_and_header_together():
    m = attributed([("a", "1", "/x.h"), ("a", "2", "/y.h")])
    assert m == {"a": "2"} and declaring_header(m, "a") == "/y.h"


def test_tu_merge_keeps_attribution():
    from abicheck.tu_merge import _merge_scalar_group

    merged = _merge_scalar_group(
        [
            ("tu1", attributed([("A", "1", "/usr/include/dep.h")])),
            ("tu2", attributed([("A", "1", "/proj/api.h"), ("B", "2", "/proj/api.h")])),
            ("tu3", {"C": "3"}),
        ],
        kind="constant",
    )
    assert merged == {"A": "1", "B": "2", "C": "3"}
    assert declaring_header(merged, "A") == "/usr/include/dep.h"
    assert declaring_header(merged, "B") == "/proj/api.h"
    assert declaring_header(merged, "C") == ""


def test_dropped_entries_without_an_ir_or_matching_occurrence_leave_the_ir_alone():
    snap = _snapshot({"DEP": "/usr/include/dep.h"}, {})
    out = scope_flat_maps(snap, _is_dep, "", None, {})
    assert out.constants == {} and out.semantic_ir is None

    unrelated = SemanticIR(occurrences={})
    out = scope_flat_maps(snap, _is_dep, "", unrelated, {"k": "v"})
    assert out.constants == {}
    assert out.semantic_ir is unrelated and out.semantic_ir_conflicts == {"k": "v"}

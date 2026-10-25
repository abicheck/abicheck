"""``pair_parameter_replacements`` with header evidence on both sides: a
removal and an addition are one ``FUNC_PARAMS_CHANGED`` exactly when they are
the same header declaration (declared name, written parameter and return
spellings, declaring header) and some parameter spelled the same resolves to
a different type. A changed written signature (case189) or a changed mangling
the header cannot explain (a renumbered lambda closure) stays a removal.

Oracle: each scenario states by construction whether the written declaration
is kept and whether a resolved type changed; the expected outcome is read off
those two facts, never computed by the rule's own helpers. The mangled names
are written out from the Itanium grammar:

* ``_ZN3lib5scaleE3vecILi8EEf``  -> ``lib::scale(vec<8>, float)``
* ``_ZN3lib5scaleE3vecILi16EEf`` -> ``lib::scale(vec<16>, float)``
* ``_ZN3lib5scaleEi``            -> ``lib::scale(int)``
* ``_ZN3lib5scaleE3vecILi4EE`` / ``..ILi2EE`` -> ``lib::scale(vec<4>)`` / ``(vec<2>)``
* ``_ZN3lib3api5scaleE3vecILi8EEf`` / ``..ILi16EEf`` -> ``lib::api::scale(...)``
"""

from __future__ import annotations

import itertools
import shutil

import pytest

from abicheck.compare.parameter_replacement import pair_parameter_replacements
from abicheck.demangle import demangle
from abicheck.diff_helpers import make_change
from abicheck.model import AbiSnapshot, Function, Param, RecordType
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.fact import Fact

OLD = "_ZN3lib5scaleE3vecILi8EEf"
NEW = "_ZN3lib5scaleE3vecILi16EEf"
SIBLING = "_ZN3lib5scaleEi"

pytestmark = pytest.mark.skipif(
    demangle(OLD) is None and shutil.which("c++filt") is None,
    reason="demangled parameter text needs cxxfilt or c++filt",
)


def _param(spelling: str, identities: tuple[str, ...] | None = ()) -> Param:
    fact = None if identities is None else Fact.present(identities)
    return Param(name="p", type=spelling, type_identities_fact=fact)


def _fn(
    mangled: str,
    params: list[Param],
    *,
    header: str = "/src/v/include/api.h",
    return_type: str = "void",
) -> Function:
    return Function(
        name="scale",
        mangled=mangled,
        return_type=return_type,
        params=params,
        source_location=f"{header}:3",
    )


def _record(qualified: str, size_bits: int | None) -> RecordType:
    return RecordType(
        name=qualified.rpartition("::")[2],
        qualified_name=qualified,
        kind="struct",
        size_bits=size_bits,
        alignment_bits=size_bits,
    )


def _snap(
    functions: list[Function],
    typedefs: dict[str, str] | None = None,
    types: list[RecordType] | None = None,
) -> AbiSnapshot:
    typedefs = typedefs or {}
    return AbiSnapshot(
        library="lib",
        version="1.0",
        functions=functions,
        types=types or [],
        typedefs={k.rpartition("::")[2]: v for k, v in typedefs.items()},
        typedefs_qualified=typedefs,
        from_headers=True,
    )


def _removed(mangled: str) -> Change:
    return make_change(
        ChangeKind.FUNC_REMOVED, symbol=mangled, name="scale", description="removed"
    )


def _added(mangled: str) -> Change:
    return make_change(
        ChangeKind.FUNC_ADDED, symbol=mangled, new="scale", description="added"
    )


def _kinds(changes: list[Change]) -> list[tuple[ChangeKind, str]]:
    return [(c.kind, c.symbol) for c in changes]


UNPAIRED = [(ChangeKind.FUNC_REMOVED, OLD), (ChangeKind.FUNC_ADDED, NEW)]
PAIRED = [(ChangeKind.FUNC_PARAMS_CHANGED, OLD)]


def test_typedef_retarget_with_resolved_record_is_one_params_change() -> None:
    # using V = vec<8>;  ->  using V = vec<16>;   V scale(V, float);
    old = _snap(
        [_fn(OLD, [_param("V", ("vec<8>",)), _param("float")], return_type="V")],
        {"lib::V": "vec<8>"},
    )
    new = _snap(
        [_fn(NEW, [_param("V", ("vec<16>",)), _param("float")], return_type="V")],
        {"lib::V": "vec<16>"},
    )
    (change,) = pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new)
    assert change.kind is ChangeKind.FUNC_PARAMS_CHANGED
    assert change.symbol == OLD
    assert change.old_value == "(vec<8>, float)"
    assert change.new_value == "(vec<16>, float)"
    assert NEW in change.description


def test_typedef_retarget_to_a_builtin_is_read_from_the_typedef_maps() -> None:
    # No record/enum on either side (identities are confirmed-empty): the
    # typedef's own changed target is the evidence.
    old = _snap([_fn(OLD, [_param("const V&"), _param("float")])], {"lib::V": "int"})
    new = _snap([_fn(NEW, [_param("const V&"), _param("float")])], {"lib::V": "long"})
    assert (
        _kinds(pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new))
        == PAIRED
    )


def test_typedef_in_an_enclosing_namespace_is_found() -> None:
    old_m, new_m = "_ZN3lib3api5scaleE3vecILi8EEf", "_ZN3lib3api5scaleE3vecILi16EEf"
    old = _snap([_fn(old_m, [_param("V"), _param("float")])], {"lib::V": "int"})
    new = _snap([_fn(new_m, [_param("V"), _param("float")])], {"lib::V": "long"})
    out = pair_parameter_replacements([_removed(old_m), _added(new_m)], old, new)
    assert _kinds(out) == [(ChangeKind.FUNC_PARAMS_CHANGED, old_m)]


def test_innermost_typedef_shadows_the_outer_one() -> None:
    # lib::api::V (unchanged) is the V the declaration names; the outer lib::V
    # retargeting does not explain the mangling.
    old_m, new_m = "_ZN3lib3api5scaleE3vecILi8EEf", "_ZN3lib3api5scaleE3vecILi16EEf"
    old = _snap(
        [_fn(old_m, [_param("V"), _param("float")])],
        {"lib::api::V": "short", "lib::V": "int"},
    )
    new = _snap(
        [_fn(new_m, [_param("V"), _param("float")])],
        {"lib::api::V": "short", "lib::V": "long"},
    )
    removed, added = _removed(old_m), _added(new_m)
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


def test_default_template_argument_chosen_per_target_is_read_from_the_record() -> None:
    # using V = xvec::simd::vec<float>: the spelling, the typedef target and the
    # resolved identity are all ``basic_vec<float>`` on both sides, but the
    # default ABI argument follows -mavx2/-mavx512f, so the record it names
    # grows from 256 to 512 bits and the mangling moves with it.
    ids = ("xvec::simd::basic_vec<float>",)
    old = _snap(
        [_fn(OLD, [_param("V", ids), _param("float")], return_type="V")],
        {"lib::V": "basic_vec<float>"},
        [_record("xvec::simd::basic_vec<float>", 256)],
    )
    new = _snap(
        [_fn(NEW, [_param("V", ids), _param("float")], return_type="V")],
        {"lib::V": "basic_vec<float>"},
        [_record("xvec::simd::basic_vec<float>", 512)],
    )
    assert (
        _kinds(pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new))
        == PAIRED
    )


@pytest.mark.parametrize(
    ("old_records", "new_records"),
    [
        # Same layout: nothing the header shows changed.
        ([("lib::Rec", 64)], [("lib::Rec", 64)]),
        # Layout unknown on one side: no claim.
        ([("lib::Rec", None)], [("lib::Rec", 128)]),
        # Two records carry the name on one side: which one is meant is unknown.
        ([("lib::Rec", 64), ("lib::Rec", 64)], [("lib::Rec", 128)]),
        # The record is missing on one side.
        ([("lib::Rec", 64)], []),
    ],
)
def test_record_layout_evidence_needs_one_known_differing_record(
    old_records: list[tuple[str, int | None]],
    new_records: list[tuple[str, int | None]],
) -> None:
    old = _snap(
        [_fn(OLD, [_param("Rec", ("lib::Rec",)), _param("float")])],
        types=[_record(n, b) for n, b in old_records],
    )
    new = _snap(
        [_fn(NEW, [_param("Rec", ("lib::Rec",)), _param("float")])],
        types=[_record(n, b) for n, b in new_records],
    )
    assert (
        _kinds(pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new))
        == UNPAIRED
    )


def test_direct_written_retype_stays_a_removal() -> None:
    # void scale(Old, float) -> void scale(New, float): the header no longer
    # declares the old signature at all.
    old = _snap([_fn(OLD, [_param("Old", ("lib::Old",)), _param("float")])])
    new = _snap([_fn(NEW, [_param("New", ("lib::New",)), _param("float")])])
    assert (
        _kinds(pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new))
        == UNPAIRED
    )


def test_case189_internal_retype_stays_a_removal() -> None:
    # demo::configure(void*, Meta) -> (detail::Options*, Meta).
    old_m, new_m = (
        "_ZN4demo9configureEPvNS_4MetaE",
        "_ZN4demo9configureEPNS_6detail7OptionsENS_4MetaE",
    )
    old = _snap([_fn(old_m, [_param("void*"), _param("Meta", ("demo::Meta",))])])
    new = _snap(
        [
            _fn(
                new_m,
                [
                    _param("Options*", ("demo::detail::Options",)),
                    _param("Meta", ("demo::Meta",)),
                ],
            )
        ]
    )
    removed, added = _removed(old_m), _added(new_m)
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


def test_closure_renumbering_the_header_cannot_explain_stays_a_removal() -> None:
    # Same written spelling and same resolved identity on both sides; only the
    # closure ordinal inside the Itanium encoding moved (Ul..E_ -> Ul..E0_).
    old_m = "_Z8run_task10raii_guardIZN8tbb_like8arena_fnIiE4makeEvEUlvE_E"
    new_m = "_Z8run_task10raii_guardIZN8tbb_like8arena_fnIiE4makeEvEUlvE0_E"
    spelling = "raii_guard<(lambda:lib.h#1)>"
    old = _snap(
        [_fn(old_m, [_param(spelling, (spelling,))])], types=[_record(spelling, 8)]
    )
    new = _snap(
        [_fn(new_m, [_param(spelling, (spelling,))])], types=[_record(spelling, 8)]
    )
    removed, added = _removed(old_m), _added(new_m)
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]


def test_uncollected_identities_and_no_typedef_give_no_evidence() -> None:
    old = _snap([_fn(OLD, [_param("V", None), _param("float", None)])])
    new = _snap([_fn(NEW, [_param("V", None), _param("float", None)])])
    assert (
        _kinds(pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new))
        == UNPAIRED
    )


def test_declaration_moved_to_another_header_is_not_paired() -> None:
    old = _snap([_fn(OLD, [_param("V")])], {"lib::V": "int"})
    new = _snap(
        [_fn(NEW, [_param("V")], header="/src/v/include/other.h")], {"lib::V": "long"}
    )
    assert (
        _kinds(pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new))
        == UNPAIRED
    )


def test_retargeted_overload_beside_an_unchanged_overload_is_paired() -> None:
    # scale(V, float) and scale(int) both declared; only V retargeted. The
    # written declaration tells the overloads apart, so the unchanged scale(int)
    # does not make the pair ambiguous.
    sibling = _fn(SIBLING, [_param("int")])
    old = _snap([_fn(OLD, [_param("V"), _param("float")]), sibling], {"lib::V": "int"})
    new = _snap([_fn(NEW, [_param("V"), _param("float")]), sibling], {"lib::V": "long"})
    assert (
        _kinds(pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new))
        == PAIRED
    )


def test_two_retargeted_overloads_each_pair_with_their_own_replacement() -> None:
    a_old, a_new = OLD, NEW
    b_old, b_new = "_ZN3lib5scaleE3vecILi4EE", "_ZN3lib5scaleE3vecILi2EE"
    old = _snap(
        [_fn(a_old, [_param("V"), _param("float")]), _fn(b_old, [_param("W")])],
        {"lib::V": "int", "lib::W": "short"},
    )
    new = _snap(
        [_fn(a_new, [_param("V"), _param("float")]), _fn(b_new, [_param("W")])],
        {"lib::V": "long", "lib::W": "char"},
    )
    changes = [_removed(a_old), _removed(b_old), _added(b_new), _added(a_new)]
    out = pair_parameter_replacements(changes, old, new)
    assert _kinds(out) == [
        (ChangeKind.FUNC_PARAMS_CHANGED, a_old),
        (ChangeKind.FUNC_PARAMS_CHANGED, b_old),
    ]
    assert {c.symbol: c.new_value for c in out} == {
        a_old: "(vec<16>, float)",
        b_old: "(vec<2>)",
    }


def test_two_new_declarations_with_one_written_signature_are_not_paired() -> None:
    # The written key is not unique on the new side: no claim.
    other = "_ZN3lib5scaleE3vecILi32EEf"
    old = _snap([_fn(OLD, [_param("V"), _param("float")])], {"lib::V": "int"})
    new = _snap(
        [
            _fn(NEW, [_param("V"), _param("float")]),
            _fn(other, [_param("V"), _param("float")]),
        ],
        {"lib::V": "long"},
    )
    changes = [_removed(OLD), _added(NEW), _added(other)]
    assert pair_parameter_replacements(changes, old, new) == changes


# Exhaustive small domain. Each axis is one fact about the scenario, chosen
# here; the expected outcome is the rule stated over those facts.
_WRITTEN = ("kept", "retyped")
_RESOLUTION = ("unchanged", "record_identity", "record_layout", "typedef_target")
_HEADER = ("same", "moved")
_SIBLING = (False, True)


def _scenario(
    written: str, resolution: str, header: str, sibling: bool
) -> tuple[AbiSnapshot, AbiSnapshot]:
    new_spelling = "V" if written == "kept" else "U"
    old_ids = ("vec<8>",)
    new_ids = ("vec<16>",) if resolution == "record_identity" else old_ids
    old_td = {"lib::V": "vec<8>", "lib::U": "vec<8>"}
    new_td = dict(old_td)
    old_types = [_record("vec<8>", 256)]
    new_types = [_record("vec<8>", 512 if resolution == "record_layout" else 256)]
    if resolution == "typedef_target":
        old_ids = new_ids = ()
        new_td = {"lib::V": "long", "lib::U": "long"}
        old_td = {"lib::V": "int", "lib::U": "int"}
    new_header = "/src/v/include/api.h" if header == "same" else "/src/v/include/b.h"
    old_fns = [_fn(OLD, [_param("V", old_ids), _param("float")])]
    new_fns = [
        _fn(NEW, [_param(new_spelling, new_ids), _param("float")], header=new_header)
    ]
    if sibling:
        old_fns.append(_fn(SIBLING, [_param("int")]))
        new_fns.append(_fn(SIBLING, [_param("int")]))
    return _snap(old_fns, old_td, old_types), _snap(new_fns, new_td, new_types)


@pytest.mark.parametrize(
    ("written", "resolution", "header", "sibling"),
    list(itertools.product(_WRITTEN, _RESOLUTION, _HEADER, _SIBLING)),
)
def test_pairing_rule_over_the_whole_scenario_domain(
    written: str, resolution: str, header: str, sibling: bool
) -> None:
    old, new = _scenario(written, resolution, header, sibling)
    out = pair_parameter_replacements([_removed(OLD), _added(NEW)], old, new)
    same_declaration = written == "kept" and header == "same"
    expected = PAIRED if same_declaration and resolution != "unchanged" else UNPAIRED
    assert _kinds(out) == expected


@pytest.mark.parametrize("header_side", ["old", "new"])
def test_asymmetric_header_evidence_is_never_paired(header_side: str) -> None:
    # One side carries written spellings, the other does not: neither rule's
    # evidence is complete, so the removal and addition stand.
    old = _snap([_fn(OLD, [_param("V"), _param("float")])], {"lib::V": "int"})
    new = _snap([_fn(NEW, [_param("V"), _param("float")])], {"lib::V": "long"})
    (new if header_side == "old" else old).from_headers = False
    removed, added = _removed(OLD), _added(NEW)
    assert pair_parameter_replacements([removed, added], old, new) == [removed, added]

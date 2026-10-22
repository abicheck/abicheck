# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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
"""ADR-085: template-parameter defaults as a recorded fact (D1), the header-level
default-change kind (D2), and the assurance gap a non-recording side leaves (D3).

The unit tests build snapshots directly and need no toolchain. The end-to-end
clang check is in ``test_default_template_arg_l2_matrix.py``.
"""

from __future__ import annotations

import pytest

from abicheck.compare.template_param_defaults import (
    _pattern_records,
    detect_template_param_default_changed,
)
from abicheck.model import AbiSnapshot, Fact, RecordType
from abicheck.model.availability import FactStatus
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.analysis_assurance._template_defaults import template_default_gap
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict


def _box(
    defaults: Fact[tuple[str | None, ...]] | None, *, name: str = "Box"
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        qualified_name=f"mylib::{name}",
        is_template_pattern=True,
        template_param_defaults_fact=defaults,
    )


def _snap(*records: RecordType, producer: str | None = "clang") -> AbiSnapshot:
    return AbiSnapshot(
        library="libtpl.so.1",
        version="1.0",
        types=list(records),
        ast_producer=producer,
    )


_PRESENT_T = Fact.present((None, "T"))
_PRESENT_INT = Fact.present((None, "int"))


# D1 -- the fact survives a round trip, and an unrecorded side stays UNKNOWN.


def test_recorded_defaults_round_trip_with_none_per_parameter() -> None:
    snap = _snap(_box(_PRESENT_T))
    back = snapshot_from_dict(snapshot_to_dict(snap))
    (rec,) = back.declarations.types
    assert rec.template_param_defaults_fact is not None
    assert rec.template_param_defaults_fact.status is FactStatus.PRESENT
    assert rec.template_param_defaults_fact.value == (None, "T")


def test_unrecorded_pattern_round_trips_as_not_present() -> None:
    snap = _snap(_box(None), producer="castxml")
    back = snapshot_from_dict(snapshot_to_dict(snap))
    (rec,) = back.declarations.types
    fact = rec.template_param_defaults_fact
    assert fact is None or fact.status is not FactStatus.PRESENT


def test_unrecorded_side_never_reads_as_recorded_empty_defaults() -> None:
    recorded = _pattern_records(_snap(_box(_PRESENT_T)))["mylib::Box"]
    unrecorded = _pattern_records(_snap(_box(None), producer="castxml"))["mylib::Box"]
    assert recorded.template_param_defaults_fact is not None
    assert recorded.template_param_defaults_fact.value == (None, "T")
    assert unrecorded.template_param_defaults_fact is None


def test_ambiguous_pattern_name_is_dropped_not_trusted() -> None:
    snap = _snap(_box(_PRESENT_T), _box(_PRESENT_INT))
    assert _pattern_records(snap) == {}


# D2 -- the header-level default change is API_BREAK-kind; the detector pairs
# only two PRESENT sides of equal arity and a real spelling change.


def test_default_spelling_change_emits_kind() -> None:
    findings, suppressed = detect_template_param_default_changed(
        _snap(_box(_PRESENT_T)), _snap(_box(_PRESENT_INT))
    )
    assert suppressed == []
    (change,) = findings
    assert change.kind is ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED
    assert change.symbol == "mylib::Box"
    assert change.old_value == "(None, 'T')"
    assert change.new_value == "(None, 'int')"


def test_equal_defaults_emit_nothing() -> None:
    findings, suppressed = detect_template_param_default_changed(
        _snap(_box(_PRESENT_T)), _snap(_box(_PRESENT_T))
    )
    assert findings == []
    assert suppressed == []


@pytest.mark.parametrize("side", ["old", "new"])
def test_unknown_side_never_pairs_with_a_recorded_side(side: str) -> None:
    recorded = _snap(_box(_PRESENT_T))
    unknown = _snap(_box(None), producer="castxml")
    old, new = (unknown, recorded) if side == "old" else (recorded, unknown)
    findings, suppressed = detect_template_param_default_changed(old, new)
    assert findings == []
    assert suppressed == []


def test_arity_change_is_not_a_default_change() -> None:
    findings, _ = detect_template_param_default_changed(
        _snap(_box(Fact.present((None, "T")))),
        _snap(_box(Fact.present((None, "T", "int")))),
    )
    assert findings == []


def test_exported_instantiation_suppresses_the_header_kind() -> None:
    # Shape A: DEFAULT_TEMPLATE_ARG_CHANGED already covers mylib::Box.
    findings, suppressed = detect_template_param_default_changed(
        _snap(_box(_PRESENT_T)),
        _snap(_box(_PRESENT_INT)),
        instantiation_stems={"mylib::Box"},
    )
    assert findings == []
    (change,) = suppressed
    assert change.kind is ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED


# D3 -- a side that did not record the defaults is an assurance gap, never a
# clean comparison.


def test_all_recorded_has_no_gap() -> None:
    assert template_default_gap(_snap(_box(_PRESENT_T)), _snap(_box(_PRESENT_INT))) == (
        False,
        [],
    )


def test_castxml_unrecorded_side_names_the_backend_in_the_gap() -> None:
    gap, notes = template_default_gap(
        _snap(_box(_PRESENT_T)), _snap(_box(None), producer="castxml")
    )
    assert gap is True
    (note,) = notes
    assert "template-parameter defaults (castxml cannot record them)" in note
    assert "new snapshot" in note


def test_pre_v62_clang_side_is_a_gap_not_a_clean_pass() -> None:
    gap, notes = template_default_gap(
        _snap(_box(None), producer="clang"), _snap(_box(_PRESENT_INT))
    )
    assert gap is True
    (note,) = notes
    assert "old snapshot" in note
    assert "predates schema v62" in note


def test_v62_record_without_the_key_reads_not_collected() -> None:
    # ADR-085 D1: at v62 and above an absent key means NOT_COLLECTED (the
    # producer did not record it), never "no defaults".
    snap = _snap(_box(None), producer="castxml")
    doc = snapshot_to_dict(snap)
    assert doc["schema_version"] >= 62
    (rec,) = snapshot_from_dict(doc).declarations.types
    assert rec.template_param_defaults_fact is not None
    assert rec.template_param_defaults_fact.status is FactStatus.NOT_COLLECTED


def test_renamed_parameters_produce_the_same_positional_defaults() -> None:
    # ADR-085 D1: a pure rename of the template's own parameters is the same
    # declaration, so its recorded defaults must compare equal.
    from abicheck.extract.headers.clang.template_param_indexes import _positional_row

    original = _positional_row((None, "T"), ("T", "U"))
    renamed = _positional_row((None, "A"), ("A", "B"))
    assert original == renamed == [None, "<param 0>"]


def test_nested_declaration_under_a_pattern_is_not_counted_as_a_gap() -> None:
    from abicheck.policy.analysis_assurance._template_defaults import (
        _unrecorded_pattern_count,
    )

    pattern = RecordType(
        name="Box",
        kind="struct",
        qualified_name="mylib::Box",
        is_template_pattern=True,
        template_param_defaults_fact=_PRESENT_T,
    )
    nested = RecordType(
        name="Impl",
        kind="struct",
        qualified_name="mylib::Box::Impl",
        is_template_pattern=True,
    )
    assert _unrecorded_pattern_count(_snap(pattern, nested)) == 0


# -- Coverage of the helpers the public workflow reaches only through
#    subprocess runs (ADR-085 D1/D2/C; the patch-coverage gate counts them).


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ([], False),
        (["Box<char>"], True),
        (["Box <char>"], True),
        (["FlatBox<char>"], False),
        (["ns2::Box<int>"], False),
        (["Box"], False),
        (["Box;"], False),
        (["const Box<char>&", "other"], True),
    ],
)
def test_spells_template_matches_only_the_leaf_on_an_identifier_boundary(
    body: list[str], expected: bool
) -> None:
    from abicheck.policy.template_default_changes import _spells_template

    assert _spells_template(body, "mylib::Box") is expected


def test_all_type_params_is_true_only_for_a_pure_type_parameter_list() -> None:
    from abicheck.extract.headers.clang.template_param_indexes import _all_type_params

    type_only = {"inner": [{"kind": "TemplateTypeParmDecl"}]}
    mixed = {
        "inner": [
            {"kind": "TemplateTypeParmDecl"},
            {"kind": "NonTypeTemplateParmDecl"},
        ]
    }
    assert _all_type_params(type_only) is True
    assert _all_type_params(mixed) is False
    assert _all_type_params({"inner": []}) is False
    assert _all_type_params({}) is False


def test_positional_row_leaves_unrelated_spellings_and_none_untouched() -> None:
    from abicheck.extract.headers.clang.template_param_indexes import _positional_row

    assert _positional_row((None, "int"), ("T", "U")) == [None, "int"]
    assert _positional_row((None, "std::vector<T>"), ("T", "U")) == [
        None,
        "std::vector<<param 0>>",
    ]
    assert _positional_row((None, "T"), None) == [None, "T"]
    # A dependent member of a parameter is still that parameter (T::value_type),
    # so a pure rename of the parameter is not a default change.
    assert _positional_row((None, "typename T::value_type"), ("T", "U")) == [
        None,
        "typename <param 0>::value_type",
    ]
    assert _positional_row(
        (None, "typename T::value_type"), ("T", "U")
    ) == _positional_row((None, "typename A::value_type"), ("A", "B"))


def test_type_defaults_index_keeps_only_all_type_templates() -> None:
    from abicheck.extract.headers.clang.template_param_indexes import (
        build_template_param_indexes,
    )

    root = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Box",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {
                        "kind": "TemplateTypeParmDecl",
                        "name": "U",
                        "defaultArg": {"type": {"qualType": "T"}},
                    },
                    {"kind": "CXXRecordDecl", "name": "Box"},
                ],
            },
            {
                "kind": "ClassTemplateDecl",
                "name": "Mixed",
                "inner": [
                    {"kind": "NonTypeTemplateParmDecl", "name": "N"},
                    {"kind": "CXXRecordDecl", "name": "Mixed"},
                ],
            },
        ],
    }
    indexes = build_template_param_indexes(root)
    assert dict(indexes.type_defaults) == {"Box": (None, "<param 0>")}


def test_codec_validators_reject_malformed_payloads_and_pass_none_through() -> None:
    from abicheck.model.fact import Fact
    from abicheck.storage.fact_payload_validation import (
        validated_identities,
        validated_template_param_defaults,
    )

    assert validated_identities(None) is None
    assert validated_template_param_defaults(None) is None
    assert (
        validated_identities(Fact.present(["ns::A", ""])).status
        is FactStatus.NOT_COLLECTED
    )
    assert (
        validated_identities(Fact.present("ns::A")).status is FactStatus.NOT_COLLECTED
    )
    assert validated_identities(Fact.present(["ns::A"])).value == ("ns::A",)
    bad = validated_template_param_defaults(Fact.present("T"))
    assert bad.status is FactStatus.NOT_COLLECTED
    bad_item = validated_template_param_defaults(Fact.present([None, 3]))
    assert bad_item.status is FactStatus.NOT_COLLECTED
    good = validated_template_param_defaults(Fact.present([None, "T"]))
    assert good.value == (None, "T")


def test_a_partial_or_none_defaults_fact_on_one_side_is_never_a_change() -> None:
    partial = RecordType(
        name="Box",
        kind="struct",
        qualified_name="mylib::Box",
        is_template_pattern=True,
        template_param_defaults_fact=Fact.not_collected(),
    )
    findings, suppressed = detect_template_param_default_changed(
        _snap(_box(_PRESENT_T)), _snap(partial)
    )
    assert findings == []
    assert suppressed == []


def test_a_non_pattern_record_is_ignored_by_the_pattern_index() -> None:
    plain = RecordType(name="Point", kind="struct", is_template_pattern=False)
    assert _pattern_records(_snap(plain, _box(_PRESENT_T))).keys() == {"mylib::Box"}


def test_pipeline_records_a_shape_c_relation_as_deduplicated() -> None:
    from types import SimpleNamespace

    from abicheck.model.change import Change
    from abicheck.model.change_catalog.kinds import ChangeKind
    from abicheck.model.declarations import Function, Param
    from abicheck.policy.disposition_types import Disposition
    from abicheck.policy.template_default_changes import (
        detect_template_default_changes,
    )

    def _take(return_type: str = "void") -> Function:
        return Function(
            name="take",
            mangled="_Z4takeRK3BoxIcEE",
            return_type=return_type,
            params=[Param(name="b", type="const Box<char>&")],
        )

    old = AbiSnapshot(
        library="libtpl.so.1",
        version="1.0",
        types=[_box(_PRESENT_T)],
        functions=[_take()],
    )
    new = AbiSnapshot(
        library="libtpl.so.1",
        version="1.0",
        types=[_box(_PRESENT_INT)],
        functions=[],
    )
    removed = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_Z4takeRK3BoxIcEE",
        description="take removed",
    )

    class _Ledger:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Disposition]] = []

        def record(self, change, disposition, *, application_point):
            self.calls.append((change.kind.value, disposition))

    ledger = _Ledger()
    ctx = SimpleNamespace(new=new, disposition_ledger=ledger)
    findings = detect_template_default_changes(old, ctx, [removed])
    assert findings == []
    assert ledger.calls == [
        ("template_param_default_changed", Disposition.DEDUPLICATED)
    ]


def test_a_present_defaults_fact_with_no_value_is_not_a_change() -> None:
    null_value = RecordType(
        name="Box",
        kind="struct",
        qualified_name="mylib::Box",
        is_template_pattern=True,
        template_param_defaults_fact=Fact.present(None),
    )
    findings, suppressed = detect_template_param_default_changed(
        _snap(null_value), _snap(_box(_PRESENT_INT))
    )
    assert findings == []
    assert suppressed == []


def test_pipeline_with_no_reported_functions_names_no_template() -> None:
    from abicheck.policy.template_default_changes import (
        _templates_named_by_reported_functions,
    )

    snap = _snap(_box(_PRESENT_T))
    assert _templates_named_by_reported_functions(snap, snap, []) == set()


def test_pipeline_without_a_ledger_still_suppresses_the_finding() -> None:
    from types import SimpleNamespace

    from abicheck.model.change import Change
    from abicheck.model.change_catalog.kinds import ChangeKind
    from abicheck.policy.template_default_changes import (
        detect_template_default_changes,
    )

    old = _snap(_box(_PRESENT_T))
    new = _snap(_box(_PRESENT_INT))
    removed = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_Z4unrelatedv",
        description="removed",
    )
    ctx = SimpleNamespace(new=new, disposition_ledger=None)
    # No function reports Box, so the header finding is kept, not suppressed.
    findings = detect_template_default_changes(old, ctx, [removed])
    assert [f.kind for f in findings] == [ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED]


def test_all_type_params_skips_a_non_dict_child_and_keeps_scanning() -> None:
    from abicheck.extract.headers.clang.template_param_indexes import _all_type_params

    # A malformed JSON child is skipped, not taken as the end of the list.
    assert (
        _all_type_params({"inner": ["junk", {"kind": "TemplateTypeParmDecl"}]}) is True
    )


def test_a_repeated_removed_instance_is_reported_once() -> None:
    from abicheck.compare.template_param_defaults import (
        detect_default_template_arg_changed,
    )
    from abicheck.model.declarations import Function

    def _instance(mangled: str) -> Function:
        return Function(name="compute", mangled=mangled, return_type="void", params=[])

    old_fn = "_ZN2ns10descriptorIfNS_18minkowski_distanceIfEEE7computeEv"
    new_fn = "_ZN2ns10descriptorIfNS_18euclidean_distanceIfEEE7computeEv"
    old = AbiSnapshot(
        library="lib", version="1.0", functions=[_instance(old_fn), _instance(old_fn)]
    )
    new = AbiSnapshot(library="lib", version="1.0", functions=[_instance(new_fn)])
    assert len(detect_default_template_arg_changed(old, new)) == 1

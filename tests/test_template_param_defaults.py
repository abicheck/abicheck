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
    _recorded_template_defaults,
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
    recorded = _recorded_template_defaults(_snap(_box(_PRESENT_T)))
    unrecorded = _recorded_template_defaults(_snap(_box(None), producer="castxml"))
    assert recorded == {"mylib::Box": (None, "T")}
    assert unrecorded == {"mylib::Box": None}


def test_ambiguous_pattern_name_is_dropped_not_trusted() -> None:
    snap = _snap(_box(_PRESENT_T), _box(_PRESENT_INT))
    assert _recorded_template_defaults(snap) == {}


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

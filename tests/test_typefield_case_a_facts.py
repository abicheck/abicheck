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

"""``TypeField``'s own case-(a) facts — ADR-063 Phase 5's first **case-(a)**
batch (schema v39): ``is_const``, ``is_volatile``, ``is_mutable``,
``default``, ``deprecated``.

Case (a) is the shape Phase 0's own ``vtable``/``vptr_offset_bits``/
``is_va_list`` conversions established and every batch since avoided: the
field's own resting value (``False``) is a legitimate value, so it carries
no availability signal at all — ``AbiSnapshot.header_cv_facts_reliable``
does. Two things therefore need testing that no case-(b) batch needed:

1. an *omitted* field must reach ``NOT_COLLECTED``, not ``present(False)``
   (the private omission sentinel), and
2. a legacy document whose reliability flag says its blanket ``False``s are
   pre-fix castxml placeholders must be *downgraded* on load rather than
   read as confirmed facts.

The second is the class this batch's real risk lives in, so it is tested
against :func:`apply_case_a_fact_backfill`'s own contract for every owner
that mechanism can navigate (:class:`TestCaseAFactBackfillContract`), not
only through the one field pair that motivated it.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import (
    AbiSnapshot,
    Fact,
    FactStatus,
    Function,
    Param,
    RecordType,
    TypeField,
)
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.fact_backfill import _HEADER_AST_BACKENDS
from abicheck.storage.fact_codec import (
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS,
    CaseAFactRule,
    apply_case_a_fact_backfill,
)

#: (field name, a real sample value, the field's own normalized default).
#: The last two are `str | None`-typed rather than bool, and guarded by a
#: different flag each, but share the identical case-(a) contract: their own
#: resting value (None = "no initializer" / "not deprecated") is a real
#: value, so only the snapshot-level flag can answer "did anyone look?".
_CASE_A_FIELDS: tuple[tuple[str, object, object], ...] = (
    ("is_const", True, False),
    ("is_volatile", True, False),
    ("is_mutable", True, False),
    ("default", "30", None),
    ("deprecated", "use bar()", None),
)

_CV_FIELDS: tuple[str, ...] = ("is_const", "is_volatile", "is_mutable")


def _make_snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = {
        "library": "libfoo.so",
        "version": "v1",
        "functions": [],
        "variables": [],
        "types": [],
        "enums": [],
        "typedefs": [],
    }
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))


def _minimal_dict(**overrides: object) -> dict:
    base: dict = {
        "library": "libtest.so",
        "version": "v1",
        "functions": [],
        "variables": [],
        "types": [],
        "enums": [],
        "typedefs": [],
    }
    base.update(overrides)
    return base


def _record_with_field(**field_kwargs: object) -> RecordType:
    return RecordType(
        name="Widget",
        kind="class",
        fields=[TypeField(name="m", type="int", **field_kwargs)],  # type: ignore[arg-type]
    )


class TestTypeFieldCvFactRoundTrip:
    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    @pytest.mark.parametrize("value", [True, False])
    def test_explicit_value_round_trips_present(
        self, field_name: str, value: bool
    ) -> None:
        rec = _record_with_field(**{field_name: value})
        f = _round_trip(_make_snap(types=[rec])).types[0].fields[0]
        assert getattr(f, field_name) is value
        fact = getattr(f, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value is value

    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    def test_omitted_field_round_trips_not_collected(self, field_name: str) -> None:
        # The distinction case (a) exists for: an untouched field is False
        # *and* NOT_COLLECTED, never present(False).
        f = _round_trip(_make_snap(types=[_record_with_field()])).types[0].fields[0]
        assert getattr(f, field_name) is False
        assert getattr(f, f"{field_name}_fact").status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    def test_explicit_unsupported_fact_survives_round_trip(
        self, field_name: str
    ) -> None:
        rec = _record_with_field(**{f"{field_name}_fact": Fact.unsupported("PDB")})
        f = _round_trip(_make_snap(types=[rec])).types[0].fields[0]
        fact = getattr(f, f"{field_name}_fact")
        assert fact.status is FactStatus.UNSUPPORTED
        assert fact.diagnostics == ("PDB",)
        assert getattr(f, field_name) is False

    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    def test_snapshot_to_dict_encodes_status_as_plain_string(
        self, field_name: str
    ) -> None:
        rec = _record_with_field(**{f"{field_name}_fact": Fact.present(True)})
        d = snapshot_to_dict(_make_snap(types=[rec]))
        encoded = d["types"][0]["fields"][0][f"{field_name}_fact"]["status"]
        assert encoded == "present"

    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    def test_legacy_reliable_snapshot_with_real_value_backfills_present(
        self, field_name: str
    ) -> None:
        d = _minimal_dict(
            schema_version=_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS - 1,
            header_cv_facts_reliable=True,
            types=[
                {
                    "name": "Foo",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", field_name: True}],
                }
            ],
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert getattr(f, field_name) is True
        assert getattr(f, f"{field_name}_fact").status is FactStatus.PRESENT

    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    def test_legacy_unreliable_snapshot_downgrades_to_not_collected(
        self, field_name: str
    ) -> None:
        # The real regression this batch exists to make unrepresentable: a
        # pre-fix castxml snapshot's blanket False is a placeholder, and
        # header_cv_facts_reliable=False is the only signal saying so.
        d = _minimal_dict(
            schema_version=_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS - 1,
            header_cv_facts_reliable=False,
            types=[
                {
                    "name": "Foo",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", field_name: False}],
                }
            ],
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert getattr(f, field_name) is False
        assert getattr(f, f"{field_name}_fact").status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    def test_legacy_document_omitting_the_legacy_key_is_not_collected(
        self, field_name: str
    ) -> None:
        # decode_fact_with_legacy_presence's own reason for existing: the
        # caller's `f.get("is_const", False)` default hands the dataclass a
        # real False, which the generic bridge would otherwise confirm.
        d = _minimal_dict(
            schema_version=_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS - 1,
            header_cv_facts_reliable=True,
            types=[
                {
                    "name": "Foo",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int"}],
                }
            ],
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert getattr(f, f"{field_name}_fact").status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name", _CV_FIELDS)
    def test_current_schema_missing_fact_key_is_not_collected_not_present(
        self, field_name: str
    ) -> None:
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            types=[
                {
                    "name": "Foo",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", field_name: True}],
                }
            ],
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert getattr(f, f"{field_name}_fact").status is FactStatus.NOT_COLLECTED

    def test_schema_version_is_39_or_higher(self) -> None:
        assert SCHEMA_VERSION >= _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS == 39


class TestCaseAFactBackfillContract:
    """:func:`apply_case_a_fact_backfill`'s invariants, stated for the whole
    rule class rather than for the one field pair that motivated it.

    Phase 0 open-coded the same correction three times; this batch replaced
    those loops with one navigator plus a rule tuple, so the contract worth
    pinning is the navigator's own — "a rule downgrades exactly the
    unreliable, pre-threshold, key-absent case, on whichever owner it names,
    and never touches anything else". Exercised across every owner the
    navigator supports and both sides of each of its three gates, not
    against a single hand-picked document.
    """

    @staticmethod
    def _doc() -> tuple[dict, RecordType, Function]:
        rec = RecordType(
            name="Foo",
            kind="class",
            fields=[TypeField(name="m", type="int", is_const=True)],
        )
        func = Function(
            name="f",
            mangled="_Z1f",
            return_type="void",
            params=[Param(name="p", type="int", is_va_list=True)],
        )
        raw = {
            "types": [{"name": "Foo", "kind": "class", "fields": [{"name": "m"}]}],
            "functions": [{"name": "f", "params": [{"name": "p"}]}],
        }
        return raw, rec, func

    @pytest.mark.parametrize(
        "owner,field,default",
        [
            ("TypeField", "is_const", False),
            ("Param", "is_va_list", False),
        ],
    )
    def test_unreliable_pre_threshold_document_is_downgraded(
        self, owner: str, field: str, default: bool
    ) -> None:
        raw, rec, func = self._doc()
        apply_case_a_fact_backfill(
            raw,
            evidenced=_HEADER_AST_BACKENDS,
            schema_version=10,
            rules=(CaseAFactRule(owner, field, 38, False, default),),
            types=[rec],
            functions=[func],
        )
        obj = rec.fields[0] if owner == "TypeField" else func.params[0]
        assert getattr(obj, field) is default
        assert getattr(obj, f"{field}_fact").status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize(
        "schema_version,reliable",
        [(38, False), (99, False), (10, True), (38, True)],
    )
    def test_no_downgrade_when_either_gate_says_trustworthy(
        self, schema_version: int, reliable: bool
    ) -> None:
        raw, rec, func = self._doc()
        apply_case_a_fact_backfill(
            raw,
            evidenced=_HEADER_AST_BACKENDS,
            schema_version=schema_version,
            rules=(
                CaseAFactRule("TypeField", "is_const", 38, reliable, False),
                CaseAFactRule("Param", "is_va_list", 38, reliable, False),
            ),
            types=[rec],
            functions=[func],
        )
        assert rec.fields[0].is_const is True
        assert rec.fields[0].is_const_fact.status is FactStatus.PRESENT
        assert func.params[0].is_va_list is True
        assert func.params[0].is_va_list_fact.status is FactStatus.PRESENT

    def test_document_carrying_the_fact_key_is_never_overwritten(self) -> None:
        raw, rec, func = self._doc()
        raw["types"][0]["fields"][0]["is_const_fact"] = {"status": "partial"}
        apply_case_a_fact_backfill(
            raw,
            evidenced=_HEADER_AST_BACKENDS,
            schema_version=10,
            rules=(CaseAFactRule("TypeField", "is_const", 38, False, False),),
            types=[rec],
        )
        assert rec.fields[0].is_const is True
        assert rec.fields[0].is_const_fact.status is FactStatus.PRESENT

    def test_unknown_owner_is_a_hard_error_not_a_silent_skip(self) -> None:
        raw, rec, _func = self._doc()
        with pytest.raises(ValueError, match="no raw-document navigation"):
            apply_case_a_fact_backfill(
                raw,
                evidenced=_HEADER_AST_BACKENDS,
                schema_version=10,
                rules=(CaseAFactRule("Nonexistent", "x", 38, False, None),),
                types=[rec],
            )

    def test_a_shorter_decoded_list_than_the_document_does_not_raise(self) -> None:
        # zip(..., strict=False) is deliberate: a truncated/hand-authored
        # document must degrade to "correct fewer objects", never explode.
        raw, rec, _func = self._doc()
        raw["types"].append({"name": "Bar", "kind": "class", "fields": []})
        apply_case_a_fact_backfill(
            raw,
            evidenced=_HEADER_AST_BACKENDS,
            schema_version=10,
            rules=(CaseAFactRule("TypeField", "is_const", 38, False, False),),
            types=[rec],
        )
        assert rec.fields[0].is_const_fact.status is FactStatus.NOT_COLLECTED


class TestTypeFieldValueFactRoundTrip:
    """The same case-(a) contract across all five fields this batch converts
    — including ``default``/``deprecated``, whose ``str | None`` type would
    otherwise look like the case-(b) shape earlier batches converted.
    """

    @pytest.mark.parametrize("field_name,value,_default", _CASE_A_FIELDS)
    def test_explicit_value_round_trips_present(
        self, field_name: str, value: object, _default: object
    ) -> None:
        rec = _record_with_field(**{field_name: value})
        f = _round_trip(_make_snap(types=[rec])).types[0].fields[0]
        assert getattr(f, field_name) == value
        fact = getattr(f, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value,default", _CASE_A_FIELDS)
    def test_omitted_field_is_not_collected_at_its_normalized_default(
        self, field_name: str, _value: object, default: object
    ) -> None:
        f = _round_trip(_make_snap(types=[_record_with_field()])).types[0].fields[0]
        assert getattr(f, field_name) == default
        assert getattr(f, f"{field_name}_fact").status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,_value,default", _CASE_A_FIELDS)
    def test_explicitly_supplied_resting_value_is_present_not_not_collected(
        self, field_name: str, _value: object, default: object
    ) -> None:
        # The whole point of the private omission sentinel: a producer that
        # looked and found nothing states it, and that is a *fact*, not a gap.
        rec = _record_with_field(**{field_name: default})
        f = _round_trip(_make_snap(types=[rec])).types[0].fields[0]
        assert getattr(f, field_name) == default
        assert getattr(f, f"{field_name}_fact").status is FactStatus.PRESENT

    @pytest.mark.parametrize(
        "field_name,flag",
        [
            ("default", "clang_field_initializer_facts_reliable"),
            ("deprecated", "clang_deprecation_facts_reliable"),
        ],
    )
    def test_legacy_unreliable_snapshot_downgrades_to_not_collected(
        self, field_name: str, flag: str
    ) -> None:
        d = _minimal_dict(
            schema_version=_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS - 1,
            types=[
                {
                    "name": "Foo",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", field_name: None}],
                }
            ],
            **{flag: False},
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert getattr(f, field_name) is None
        assert getattr(f, f"{field_name}_fact").status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize(
        "field_name,flag",
        [
            ("default", "clang_field_initializer_facts_reliable"),
            ("deprecated", "clang_deprecation_facts_reliable"),
        ],
    )
    def test_each_field_answers_to_its_own_flag_only(
        self, field_name: str, flag: str
    ) -> None:
        # Both fields land in the same schema bump but are guarded
        # separately — flipping one flag must not downgrade the other's fact.
        other = "deprecated" if field_name == "default" else "default"
        d = _minimal_dict(
            schema_version=_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_CV_FACTS - 1,
            # Recorded header provenance, so the *flag* branch is what this
            # test isolates: an inferred `from_headers` downgrades every
            # header-AST-only fact on its own (see
            # test_last_case_a_facts.py's own provenance class), which would
            # mask the per-flag independence being checked here.
            from_headers=True,
            ast_producer="clang",
            types=[
                {
                    "name": "Foo",
                    "kind": "class",
                    "fields": [
                        {"name": "m", "type": "int", field_name: None, other: None}
                    ],
                }
            ],
            **{flag: False},
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert getattr(f, f"{field_name}_fact").status is FactStatus.NOT_COLLECTED
        assert getattr(f, f"{other}_fact").status is FactStatus.PRESENT

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

"""``Param.is_restrict`` and ``Variable.access`` — ADR-063 Phase 5's last two
case-(a) conversions (schema v41), the entries that empty
``KNOWN_UNCONVERTED_ELIGIBLE_FACTS``.

``Variable.access`` is the one registered fact whose value type is an enum,
so it needs the same non-JSON-native reconstruction ``elf_binding_fact``
does — a decoded bare ``"private"`` string reaching the legacy ``access``
field would break every reader that treats it as an ``AccessLevel``. That
reconstruction, and the "the allowlist is empty and must stay empty"
invariant, are what this file pins beyond the shared round-trip contract.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Fact,
    FactStatus,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.model.fact_registry import FACT_REGISTRY, KNOWN_UNCONVERTED_ELIGIBLE_FACTS
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.fact_codec import _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS

_LEGACY = _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS - 1


def _snap(**kwargs: object) -> AbiSnapshot:
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


def _fn(params: list[Param]) -> Function:
    return Function(name="f", mangled="_Z1fPi", return_type="void", params=params)


class TestParamIsRestrictFact:
    @pytest.mark.parametrize("value", [True, False])
    def test_explicit_value_round_trips_present(self, value: bool) -> None:
        snap = _snap(functions=[_fn([Param(name="p", type="int*", is_restrict=value)])])
        p = _round_trip(snap).functions[0].params[0]
        assert p.is_restrict is value
        assert p.is_restrict_fact.status is FactStatus.PRESENT

    def test_omitted_is_not_collected(self) -> None:
        p = _round_trip(_snap(functions=[_fn([Param(name="p", type="int*")])]))
        param = p.functions[0].params[0]
        assert param.is_restrict is False
        assert param.is_restrict_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_unreliable_snapshot_downgrades_a_blanket_false(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_restrict_facts_reliable=False,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fPi",
                    "return_type": "void",
                    "params": [{"name": "p", "type": "int*", "is_restrict": False}],
                }
            ],
        )
        param = snapshot_from_dict(d).functions[0].params[0]
        assert param.is_restrict is False
        assert param.is_restrict_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_reliable_snapshot_keeps_a_real_true(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_restrict_facts_reliable=True,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fPi",
                    "return_type": "void",
                    "params": [{"name": "p", "type": "int*", "is_restrict": True}],
                }
            ],
        )
        param = snapshot_from_dict(d).functions[0].params[0]
        assert param.is_restrict is True
        assert param.is_restrict_fact.status is FactStatus.PRESENT

    def test_is_va_list_still_round_trips_after_sharing_the_owner_tuple(self) -> None:
        # This batch moved Param's encode/decode wiring from one hardcoded
        # line to a per-owner tuple; Phase 0's own sibling must be unharmed.
        snap = _snap(
            functions=[_fn([Param(name="p", type="va_list", is_va_list=True)])]
        )
        param = _round_trip(snap).functions[0].params[0]
        assert param.is_va_list is True
        assert param.is_va_list_fact.status is FactStatus.PRESENT


class TestVariableAccessFact:
    @pytest.mark.parametrize("level", list(AccessLevel))
    def test_explicit_level_round_trips_as_a_real_enum_member(
        self, level: AccessLevel
    ) -> None:
        v = Variable(name="g", mangled="g", type="int", access=level)
        got = _round_trip(_snap(variables=[v])).variables[0]
        assert got.access is level
        assert got.access_fact.status is FactStatus.PRESENT
        # The elf_binding_fact-shaped trap: a bare decoded string would
        # compare equal to the member (AccessLevel is a str-Enum) while
        # failing every `.value`/identity-based reader.
        assert isinstance(got.access_fact.value, AccessLevel)
        assert got.access_fact.value.value == level.value

    def test_omitted_is_not_collected_at_the_public_default(self) -> None:
        got = _round_trip(
            _snap(variables=[Variable(name="g", mangled="g", type="int")])
        )
        assert got.variables[0].access is AccessLevel.PUBLIC
        assert got.variables[0].access_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_public_is_a_confirmed_fact(self) -> None:
        v = Variable(name="g", mangled="g", type="int", access=AccessLevel.PUBLIC)
        got = _round_trip(_snap(variables=[v])).variables[0]
        assert got.access_fact.status is FactStatus.PRESENT

    def test_legacy_unreliable_snapshot_downgrades_a_blanket_public(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            castxml_var_access_facts_reliable=False,
            variables=[
                {"name": "g", "mangled": "g", "type": "int", "access": "public"}
            ],
        )
        got = snapshot_from_dict(d).variables[0]
        assert got.access is AccessLevel.PUBLIC
        assert got.access_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_reliable_snapshot_keeps_a_real_private(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            castxml_var_access_facts_reliable=True,
            variables=[
                {"name": "g", "mangled": "g", "type": "int", "access": "private"}
            ],
        )
        got = snapshot_from_dict(d).variables[0]
        assert got.access is AccessLevel.PRIVATE
        assert got.access_fact.status is FactStatus.PRESENT
        assert isinstance(got.access_fact.value, AccessLevel)

    def test_explicit_unsupported_survives_round_trip(self) -> None:
        v = Variable(
            name="g", mangled="g", type="int", access_fact=Fact.unsupported("DWARF")
        )
        got = _round_trip(_snap(variables=[v])).variables[0]
        assert got.access_fact.status is FactStatus.UNSUPPORTED
        assert got.access is AccessLevel.PUBLIC


class TestPhase5ConversionIsComplete:
    """ADR-063 Phase 5's own closing condition, as an executable check."""

    def test_the_unconverted_allowlist_is_empty(self) -> None:
        assert KNOWN_UNCONVERTED_ELIGIBLE_FACTS == frozenset()

    def test_every_reference_flag_covered_field_is_registered(self) -> None:
        from abicheck.model.fact_registry import REFERENCE_FLAG_COVERAGE

        registered = {(e.owner, e.field) for e in FACT_REGISTRY.entries.values()}
        for flag, pairs in REFERENCE_FLAG_COVERAGE.items():
            for pair in pairs:
                assert pair in registered, f"{flag} gates unregistered {pair}"

    def test_schema_version_is_41_or_higher(self) -> None:
        assert SCHEMA_VERSION >= _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS == 41


#: Below *every* per-field threshold this phase introduced (v39-v41), so
#: one document exercises all of them: at or above a field's own threshold a
#: missing `<field>_fact` key means "malformed", not "legacy", and decodes
#: to NOT_COLLECTED for a different reason than the one under test here.
_PRE_CASE_A = 30


class TestNonHeaderLegacySnapshotsClaimNothing:
    """A producer that never parsed a header cannot have observed a
    header-AST-only fact — no reliability flag says so (Codex review, PR
    #993).

    Every ``*_facts_reliable`` flag resolves ``True`` for a snapshot with
    ``from_headers=False``, because the producer each flag describes never
    ran — "trusted by irrelevance". That is the right answer to "is this
    value a wrong placeholder" and the wrong answer to "did anyone observe
    it", so a legacy DWARF/PDB/symbols-only document's resting values were
    bridged to ``PRESENT`` while the *fresh* equivalent of the same
    snapshot reports ``NOT_COLLECTED``. The invariant is stated across
    every header-AST-only fact rather than the three that motivated it,
    and the equivalence to a fresh snapshot is the oracle — not a
    hard-coded expected status.
    """

    #: (collection, raw legacy dict at its resting value, field).
    _RESTING: tuple[tuple[str, dict, str], ...] = (
        (
            "functions",
            {
                "name": "f",
                "mangled": "_Z1fv",
                "return_type": "void",
                "deprecated": None,
            },
            "deprecated",
        ),
        (
            "variables",
            {"name": "g", "mangled": "g", "type": "int", "deprecated": None},
            "deprecated",
        ),
        (
            "variables",
            {"name": "g", "mangled": "g", "type": "int", "access": "public"},
            "access",
        ),
        ("enums", {"name": "E", "deprecated": None}, "deprecated"),
        (
            "types",
            {"name": "W", "kind": "class", "deprecated": None},
            "deprecated",
        ),
    )

    @pytest.mark.parametrize("collection,raw,field", _RESTING)
    def test_a_resting_value_is_not_collected_not_present(
        self, collection: str, raw: dict, field: str
    ) -> None:
        d = _minimal_dict(
            schema_version=_PRE_CASE_A, from_headers=False, **{collection: [raw]}
        )
        obj = getattr(snapshot_from_dict(d), collection)[0]
        assert getattr(obj, f"{field}_fact").status is FactStatus.NOT_COLLECTED

    def test_param_is_restrict_too(self) -> None:
        d = _minimal_dict(
            schema_version=_PRE_CASE_A,
            from_headers=False,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fPi",
                    "return_type": "void",
                    "params": [{"name": "p", "type": "int*", "is_restrict": False}],
                }
            ],
        )
        param = snapshot_from_dict(d).functions[0].params[0]
        assert param.is_restrict_fact.status is FactStatus.NOT_COLLECTED

    def test_a_tri_state_false_is_kept_as_evidence_not_downgraded(self) -> None:
        # `EnumType.is_scoped` is `bool | None`: `None` is its resting
        # "nobody looked" value and `False` is a real answer ("a plain C
        # enum"). So an explicit `False` in a non-header legacy document is
        # kept and stays PRESENT — the same "narrow the claim, never the
        # value" rule as the non-resting case below. A blanket pre-v19
        # clang `False` is a different question, answered by the reliability
        # flag's own branch (see TestEnumIsScopedFact above), not here.
        d = _minimal_dict(
            schema_version=_PRE_CASE_A,
            from_headers=False,
            enums=[{"name": "E", "is_scoped": False}],
        )
        got = snapshot_from_dict(d).enums[0]
        assert got.is_scoped is False
        assert got.is_scoped_fact.status is FactStatus.PRESENT

    def test_a_real_non_resting_value_is_kept_and_stays_present(self) -> None:
        # The downgrade narrows the *claim*, never the value: a non-header
        # document carrying a real value got it from somewhere, and
        # discarding it would lose data.
        d = _minimal_dict(
            schema_version=_PRE_CASE_A,
            from_headers=False,
            variables=[
                {"name": "g", "mangled": "g", "type": "int", "access": "private"}
            ],
        )
        got = snapshot_from_dict(d).variables[0]
        assert got.access is AccessLevel.PRIVATE
        assert got.access_fact.status is FactStatus.PRESENT

    @pytest.mark.parametrize(
        "label,extra,expected",
        [
            # A PE/PDB document evidences no CV producer: PDB is not one
            # (pdb_model states UNSUPPORTED outright), and the `dwarf` in
            # is_const's registry entry is a producer this document has no
            # trace of. Codex review, third round — the "could produce it in
            # principle" reading kept this one PRESENT.
            ("pe-pdb", {"platform": "pe"}, FactStatus.NOT_COLLECTED),
            # A DWARF block is real evidence its producer ran.
            (
                "elf-dwarf",
                {"platform": "elf", "dwarf": {"has_dwarf": True}},
                FactStatus.PRESENT,
            ),
            (
                "elf-dwarf-advanced",
                {"platform": "elf", "dwarf_advanced": {"has_dwarf": True}},
                FactStatus.PRESENT,
            ),
            # ELF alone, no debug block: nothing that produces CV facts ran.
            ("elf-symbols-only", {"platform": "elf"}, FactStatus.NOT_COLLECTED),
        ],
    )
    def test_cv_facts_follow_the_documents_own_evidenced_producers(
        self, label: str, extra: dict, expected: FactStatus
    ) -> None:
        d = _minimal_dict(
            schema_version=_PRE_CASE_A,
            from_headers=False,
            types=[
                {
                    "name": "W",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", "is_const": False}],
                }
            ],
            **extra,
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert f.is_const_fact.status is expected

    def test_a_placeholder_dwarf_block_is_not_dwarf_evidence(self) -> None:
        """A symbols-only ELF dump still persists DWARF blocks.

        Codex review, PR #995: ``_build_symbol_only_snapshot`` writes a
        fully-populated ``DwarfMetadata()``/``AdvancedDwarfMetadata()`` with
        ``has_dwarf: false`` for a binary that has no debug info at all, and
        their serialized form is a **non-empty mapping** -- so "the block is
        truthy" reads as DWARF evidence when there is none. Built here by
        serializing a real snapshot rather than hand-writing the dict,
        because the placeholder's real shape is exactly what a hand-written
        fixture would get wrong.
        """
        from abicheck.model.dwarf_facts import (
            AdvancedDwarfMetadata,
            DwarfMetadata,
        )

        snap = AbiSnapshot(
            library="l.so",
            version="1",
            from_headers=False,
            platform="elf",
            dwarf=DwarfMetadata(),
            dwarf_advanced=AdvancedDwarfMetadata(),
            types=[
                RecordType(
                    name="W",
                    kind="class",
                    fields=[TypeField(name="m", type="int", is_const=False)],
                )
            ],
        )
        d = snapshot_to_dict(snap)
        d["schema_version"] = _PRE_CASE_A
        for raw_field in d["types"][0]["fields"]:  # a pre-conversion document
            for key in [k for k in raw_field if k.endswith("_fact")]:
                del raw_field[key]

        assert d["dwarf"], "the placeholder block must stay non-empty"
        assert d["dwarf"]["has_dwarf"] is False
        got = snapshot_from_dict(json.loads(json.dumps(d)))
        assert got.types[0].fields[0].is_const_fact.status is FactStatus.NOT_COLLECTED

        # ... and the same document with real debug info keeps its fact.
        d["dwarf"]["has_dwarf"] = True
        got = snapshot_from_dict(json.loads(json.dumps(d)))
        assert got.types[0].fields[0].is_const_fact.status is FactStatus.PRESENT

    def test_a_dwarf_producible_fact_is_left_alone(self) -> None:
        # TypeField.is_const names dwarf among its producers, and this
        # document evidences dwarf, so its value is real evidence — the
        # producer gate reads the registry, not a hand list.
        d = _minimal_dict(
            schema_version=_PRE_CASE_A,
            from_headers=False,
            dwarf={"has_dwarf": True},
            types=[
                {
                    "name": "W",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", "is_const": True}],
                }
            ],
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert f.is_const is True
        assert f.is_const_fact.status is FactStatus.PRESENT

    @pytest.mark.parametrize("collection,raw,field", _RESTING)
    def test_inferred_header_provenance_is_unknown_not_confirmed(
        self, collection: str, raw: dict, field: str
    ) -> None:
        # Codex review, second round: a document predating the
        # `from_headers` key has it INFERRED from "does this snapshot carry
        # declarations at all", which a legacy DWARF-only dump satisfies
        # exactly as a header dump does. An inferred True is unknown
        # provenance, and must not read as evidence a header backend ran.
        d = _minimal_dict(schema_version=_PRE_CASE_A, **{collection: [raw]})
        snap = snapshot_from_dict(d)
        assert snap.from_headers is True
        assert snap.from_headers_inferred is True
        obj = getattr(snap, collection)[0]
        assert getattr(obj, f"{field}_fact").status is FactStatus.NOT_COLLECTED

    def test_a_header_snapshot_is_unaffected(self) -> None:
        # Recorded (not inferred) header provenance: the flag branch alone
        # decides, and a reliable castxml document keeps its fact.
        d = _minimal_dict(
            schema_version=_PRE_CASE_A,
            from_headers=True,
            ast_producer="castxml",
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fv",
                    "return_type": "void",
                    "deprecated": None,
                }
            ],
        )
        got = snapshot_from_dict(d).functions[0]
        assert got.deprecated_fact.status is FactStatus.PRESENT

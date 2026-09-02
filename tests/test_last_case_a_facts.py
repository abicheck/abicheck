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
            # A populated debug block on PE is a *PDB*, not DWARF:
            # parse_pdb_debug_info stores PDB data in DwarfMetadata with
            # has_dwarf=True, and _dump_pe never calls parse_dwarf. Codex
            # review, sixth round.
            (
                "pe-pdb-with-debug",
                {"platform": "pe", "dwarf": {"has_dwarf": True}},
                FactStatus.NOT_COLLECTED,
            ),
            # A DWARF block is real evidence its producer ran.
            (
                "elf-dwarf",
                {"platform": "elf", "dwarf": {"has_dwarf": True}},
                FactStatus.PRESENT,
            ),
            (
                "macho-dwarf",
                {"platform": "macho", "dwarf_advanced": {"has_dwarf": True}},
                FactStatus.PRESENT,
            ),
            # No platform recorded at all: a document predating the field
            # is an ELF one, so its debug block still evidences dwarf.
            (
                "no-platform-dwarf",
                {"dwarf": {"has_dwarf": True}},
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


class TestEvidencedProducerInvariantAcrossEveryCaseAFact:
    """The general contract, over the whole small domain.

    Every test above pins one field against one document shape. This one
    states the invariant the fix actually restores -- *after a legacy load,
    a case-(a) fact is PRESENT only if the document evidences at least one
    of that fact's registered producers* -- and checks it by exhaustively
    enumerating (every case-(a) field) x (every document shape that varies
    an evidence axis).

    The oracle is written out here from what each producer's writer does,
    read off the loaded snapshot's own public attributes. It deliberately
    does not call ``evidenced_producers`` or consult ``FACT_REGISTRY`` the
    way the implementation does, so this cannot agree with a wrong
    implementation the way #699's window-size test agreed with its own
    wrong formula (AGENTS.md, "A bug fix's regression test targets the bug
    class").
    """

    #: (owner, field, collection, raw entry at the field's *resting* value,
    #: raw entry at a *non-resting* value). The second is the control: the
    #: downgrade narrows the claim, never the value, so a real answer must
    #: survive on the very same evidence-free document.
    _FIELDS: tuple[tuple[str, str, str, dict, dict], ...] = (
        ("TypeField", "is_const", "types", {"is_const": False}, {"is_const": True}),
        (
            "TypeField",
            "is_volatile",
            "types",
            {"is_volatile": False},
            {"is_volatile": True},
        ),
        (
            "TypeField",
            "is_mutable",
            "types",
            {"is_mutable": False},
            {"is_mutable": True},
        ),
        ("TypeField", "default", "types", {"default": None}, {"default": "0"}),
        (
            "TypeField",
            "deprecated",
            "types",
            {"deprecated": None},
            {"deprecated": "gone"},
        ),
        (
            "Function",
            "deprecated",
            "functions",
            {"deprecated": None},
            {"deprecated": "gone"},
        ),
        (
            "Variable",
            "deprecated",
            "variables",
            {"deprecated": None},
            {"deprecated": "gone"},
        ),
        (
            "Variable",
            "access",
            "variables",
            {"access": "public"},
            {"access": "private"},
        ),
        (
            "RecordType",
            "deprecated",
            "types",
            {"deprecated": None},
            {"deprecated": "gone"},
        ),
        (
            "EnumType",
            "deprecated",
            "enums",
            {"deprecated": None},
            {"deprecated": "gone"},
        ),
        # `is_scoped` is `bool | None`: `None` is omission, `False` a real
        # answer ("a plain C enum") — so `False` is its non-resting value.
        ("EnumType", "is_scoped", "enums", {"is_scoped": None}, {"is_scoped": False}),
        (
            "Param",
            "is_restrict",
            "functions",
            {"is_restrict": False},
            {"is_restrict": True},
        ),
    )

    #: Document shapes, one per point in the evidence domain.
    _SHAPES: tuple[tuple[str, dict], ...] = (
        ("nothing", {"from_headers": False}),
        ("elf", {"from_headers": False, "platform": "elf"}),
        (
            "elf-placeholder-debug",
            {"from_headers": False, "platform": "elf", "dwarf": {"has_dwarf": False}},
        ),
        (
            "elf-dwarf",
            {"from_headers": False, "platform": "elf", "dwarf": {"has_dwarf": True}},
        ),
        (
            "elf-dwarf-advanced",
            {
                "from_headers": False,
                "platform": "elf",
                "dwarf_advanced": {"has_dwarf": True},
            },
        ),
        (
            "macho-dwarf",
            {"from_headers": False, "platform": "macho", "dwarf": {"has_dwarf": True}},
        ),
        ("pe", {"from_headers": False, "platform": "pe"}),
        (
            "pe-pdb",
            {"from_headers": False, "platform": "pe", "dwarf": {"has_dwarf": True}},
        ),
        ("header-castxml", {"from_headers": True, "ast_producer": "castxml"}),
        ("header-clang", {"from_headers": True, "ast_producer": "clang"}),
        ("header-hybrid", {"from_headers": True, "ast_producer": "hybrid"}),
        ("header-inferred", {}),  # from_headers guessed, not recorded
    )

    @staticmethod
    def _expected_evidenced(snap: AbiSnapshot) -> set[str]:
        """Which producers this snapshot shows ran — the independent oracle.

        Written from what each writer does, not from the implementation:
        a header-AST backend only under *recorded* provenance; the debug
        block's producer is DWARF everywhere except PE, whose blocks
        ``pdb_metadata.parse_pdb_debug_info`` fills from a PDB; and the
        container format from the platform itself.
        """
        evidenced: set[str] = set()
        if snap.from_headers and not snap.from_headers_inferred:
            if snap.ast_producer in {"castxml", "clang"}:
                evidenced.add(snap.ast_producer)
            else:
                evidenced |= {"castxml", "clang"}
        has_debug = any(
            block is not None and block.has_dwarf
            for block in (snap.dwarf, snap.dwarf_advanced)
        )
        if has_debug:
            evidenced.add("pdb" if snap.platform == "pe" else "dwarf")
        if snap.platform in {"elf", "pe", "macho"}:
            evidenced.add(snap.platform)
        return evidenced

    @classmethod
    def _document(cls, owner: str, collection: str, entry: dict, shape: dict) -> dict:
        """One raw legacy document holding *owner* at *field*'s resting value.

        Dispatches on the owner, not on which keys *entry* happens to
        carry: ``TypeField.deprecated`` and ``RecordType.deprecated`` are
        the same key at two different nesting depths.
        """
        if owner == "TypeField":
            raw: dict = {
                "name": "W",
                "kind": "class",
                "fields": [{"name": "m", "type": "int", **entry}],
            }
        elif owner == "RecordType":
            raw = {"name": "W", "kind": "class", **entry}
        elif owner == "EnumType":
            raw = {"name": "E", **entry}
        elif owner == "Param":
            raw = {
                "name": "f",
                "mangled": "_Z1fPi",
                "return_type": "void",
                "params": [{"name": "p", "type": "int*", **entry}],
            }
        elif owner == "Function":
            raw = {"name": "f", "mangled": "_Z1fv", "return_type": "void", **entry}
        else:  # Variable
            raw = {"name": "g", "mangled": "g", "type": "int", **entry}
        return _minimal_dict(schema_version=_PRE_CASE_A, **{collection: [raw]}, **shape)

    @staticmethod
    def _loaded(snap: AbiSnapshot, owner: str, collection: str) -> object:
        obj = getattr(snap, collection)[0]
        if owner == "TypeField":
            return obj.fields[0]
        if owner == "Param":
            return obj.params[0]
        return obj

    @pytest.mark.parametrize(
        "owner,field,collection,entry",
        [(o, f, c, e) for o, f, c, e, _n in _FIELDS],
        ids=[f"{o}.{f}" for o, f, _c, _e, _n in _FIELDS],
    )
    @pytest.mark.parametrize("label,shape", _SHAPES, ids=[s[0] for s in _SHAPES])
    def test_present_implies_an_evidenced_producer(
        self,
        label: str,
        shape: dict,
        owner: str,
        field: str,
        collection: str,
        entry: dict,
    ) -> None:
        d = self._document(owner, collection, entry, shape)
        snap = snapshot_from_dict(json.loads(json.dumps(d)))
        obj = self._loaded(snap, owner, collection)
        status = getattr(obj, f"{field}_fact").status
        if status is not FactStatus.PRESENT:
            return
        entry_def = FACT_REGISTRY.get(f"{owner}.{field}")
        assert entry_def is not None, f"{owner}.{field} is not registered"
        producers = set(entry_def.producing_backends)
        evidenced = self._expected_evidenced(snap)
        assert producers & evidenced, (
            f"{owner}.{field} is PRESENT on a {label!r} document, but none of "
            f"its producers {sorted(producers)} is evidenced by it "
            f"({sorted(evidenced)})"
        )

    def test_the_enumeration_is_not_vacuous(self) -> None:
        """The control for the whole enumeration.

        ``test_present_implies_an_evidenced_producer`` returns early on a
        non-PRESENT status, so an implementation that downgraded *every*
        fact would satisfy it trivially. These two assertions are the
        opposite pressure, stated once per case-(a) field on the most
        evidence-free document in the domain: its resting value must be
        downgraded there, and a real, non-resting value must not be — the
        downgrade narrows the claim, never the value.
        """
        nothing = dict(self._SHAPES[0][1])
        assert self._SHAPES[0][0] == "nothing"
        for owner, field, collection, resting, non_resting in self._FIELDS:
            for entry, expected in (
                (resting, FactStatus.NOT_COLLECTED),
                (non_resting, FactStatus.PRESENT),
            ):
                d = self._document(owner, collection, entry, nothing)
                snap = snapshot_from_dict(json.loads(json.dumps(d)))
                obj = self._loaded(snap, owner, collection)
                got = getattr(obj, f"{field}_fact").status
                assert got is expected, (
                    f"{owner}.{field} at {entry} on an evidence-free document: "
                    f"expected {expected}, got {got}"
                )

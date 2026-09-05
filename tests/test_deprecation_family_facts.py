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

"""The `deprecated` family + ``EnumType.is_scoped`` — ADR-063 Phase 5's
second case-(a) batch (schema v40).

One flag (``AbiSnapshot.clang_deprecation_facts_reliable``) guards all six
fields, on five different owners, which is exactly why the contract is
parametrized over the owners here rather than written out per dataclass:
the risk this batch carries is that one owner's wiring lands and another's
silently doesn't — the same shape ``fact_registry_completeness``'s own
owner-scoped encode/decode gate exists to catch statically.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Fact,
    FactStatus,
    Function,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.fact_codec import (
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
    _MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS,
)

_LEGACY = _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS - 1


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


def _build(owner: str, **kwargs: object) -> object:
    if owner == "Function":
        return Function(name="f", mangled="_Z1fv", return_type="void", **kwargs)  # type: ignore[arg-type]
    if owner == "Variable":
        return Variable(name="g", mangled="g", type="int", **kwargs)  # type: ignore[arg-type]
    if owner == "RecordType":
        return RecordType(name="W", kind="class", **kwargs)  # type: ignore[arg-type]
    if owner == "EnumType":
        return EnumType(name="E", **kwargs)  # type: ignore[arg-type]
    if owner == "TypeField":
        return TypeField(name="m", type="int", **kwargs)  # type: ignore[arg-type]
    raise AssertionError(owner)


#: (owner, the AbiSnapshot collection it lives in, the raw dict a legacy
#: document carries for it).
_OWNERS: tuple[tuple[str, str, dict], ...] = (
    ("Function", "functions", {"name": "f", "mangled": "_Z1fv", "return_type": "void"}),
    ("Variable", "variables", {"name": "g", "mangled": "g", "type": "int"}),
    ("RecordType", "types", {"name": "W", "kind": "class"}),
    ("EnumType", "enums", {"name": "E"}),
)


def _only(snap: AbiSnapshot, collection: str) -> object:
    return getattr(snap, collection)[0]


class TestDeprecatedFamilyRoundTrip:
    @pytest.mark.parametrize("owner,collection,_raw", _OWNERS)
    def test_explicit_message_round_trips_present(
        self, owner: str, collection: str, _raw: dict
    ) -> None:
        obj = _build(owner, deprecated="use bar()")
        got = _only(_round_trip(_make_snap(**{collection: [obj]})), collection)
        assert got.deprecated == "use bar()"  # type: ignore[attr-defined]
        assert got.deprecated_fact.status is FactStatus.PRESENT  # type: ignore[attr-defined]

    @pytest.mark.parametrize("owner,collection,_raw", _OWNERS)
    def test_omitted_is_not_collected_not_a_confirmed_none(
        self, owner: str, collection: str, _raw: dict
    ) -> None:
        got = _only(
            _round_trip(_make_snap(**{collection: [_build(owner)]})), collection
        )
        assert got.deprecated is None  # type: ignore[attr-defined]
        assert got.deprecated_fact.status is FactStatus.NOT_COLLECTED  # type: ignore[attr-defined]

    @pytest.mark.parametrize("owner,collection,_raw", _OWNERS)
    def test_explicit_none_is_a_confirmed_not_deprecated(
        self, owner: str, collection: str, _raw: dict
    ) -> None:
        # A producer that looked and found no [[deprecated]] states a fact.
        obj = _build(owner, deprecated=None)
        got = _only(_round_trip(_make_snap(**{collection: [obj]})), collection)
        assert got.deprecated is None  # type: ignore[attr-defined]
        assert got.deprecated_fact.status is FactStatus.PRESENT  # type: ignore[attr-defined]

    @pytest.mark.parametrize("owner,collection,_raw", _OWNERS)
    def test_explicit_unsupported_survives_round_trip(
        self, owner: str, collection: str, _raw: dict
    ) -> None:
        obj = _build(owner, deprecated_fact=Fact.unsupported("DWARF-only"))
        got = _only(_round_trip(_make_snap(**{collection: [obj]})), collection)
        assert got.deprecated_fact.status is FactStatus.UNSUPPORTED  # type: ignore[attr-defined]
        assert got.deprecated_fact.diagnostics == ("DWARF-only",)  # type: ignore[attr-defined]

    @pytest.mark.parametrize("owner,collection,raw", _OWNERS)
    def test_legacy_unreliable_snapshot_downgrades_on_every_owner(
        self, owner: str, collection: str, raw: dict
    ) -> None:
        # The batch's real risk: one owner wired, another silently not.
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_deprecation_facts_reliable=False,
            **{collection: [dict(raw, deprecated=None)]},
        )
        got = _only(snapshot_from_dict(d), collection)
        assert got.deprecated_fact.status is FactStatus.NOT_COLLECTED  # type: ignore[attr-defined]

    @pytest.mark.parametrize("owner,collection,raw", _OWNERS)
    def test_legacy_reliable_snapshot_keeps_its_value_on_every_owner(
        self, owner: str, collection: str, raw: dict
    ) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_deprecation_facts_reliable=True,
            **{collection: [dict(raw, deprecated="gone in 2.0")]},
        )
        got = _only(snapshot_from_dict(d), collection)
        assert got.deprecated == "gone in 2.0"  # type: ignore[attr-defined]
        assert got.deprecated_fact.status is FactStatus.PRESENT  # type: ignore[attr-defined]

    @pytest.mark.parametrize("owner,collection,raw", _OWNERS)
    def test_current_schema_missing_fact_key_is_not_collected(
        self, owner: str, collection: str, raw: dict
    ) -> None:
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            **{collection: [dict(raw, deprecated="msg")]},
        )
        got = _only(snapshot_from_dict(d), collection)
        assert got.deprecated_fact.status is FactStatus.NOT_COLLECTED  # type: ignore[attr-defined]

    def test_typefield_deprecated_answers_to_the_same_flag(self) -> None:
        # TypeField.deprecated converted one batch earlier (v39) but shares
        # this flag; a legacy document must downgrade it identically.
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_deprecation_facts_reliable=False,
            types=[
                {
                    "name": "W",
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", "deprecated": None}],
                }
            ],
        )
        f = snapshot_from_dict(d).types[0].fields[0]
        assert f.deprecated_fact.status is FactStatus.NOT_COLLECTED


#: A well-formed ``qualified_name_fact``/``enum``-sibling encoding -- needed
#: whenever a fixture below sets a real ``qualified_name`` differing from its
#: bare ``name`` at ``_LEGACY`` (which already sits above both
#: ``RecordType``/``EnumType``'s own case-(b) qualified-name schema
#: thresholds): without it, the missing sibling key reads as a
#: malformed/hand-authored document (`decode_fact`'s own documented
#: contract) and the explicit ``Fact.not_collected()`` that produces
#: overwrites the legacy ``qualified_name`` value back to ``None`` --
#: unrelated to, and a confound for, the mechanism these tests target.
def _present(value: object) -> dict[str, object]:
    return {"status": "present", "value": value}


class TestLegacyHybridProvenanceBackfill:
    """T9 / ADR-063 Phase 6 item 4 ("legacy-hybrid backfill blocker").

    ``clang_deprecation_facts_reliable`` reads True unconditionally for a
    hybrid producer (an ordinary, fresh hybrid dump states this fact
    explicitly per declaration, so the flag has no reason to distrust it),
    so on a document that predates this fact family's own schema version the
    whole-snapshot ``reliable``/``evidenced`` checks both pass regardless —
    only a real per-declaration ``fact_provenance`` lookup can tell "neither
    backend's merge ever recorded looking here" apart from "confirmed,
    genuinely resting-default". See ``storage/fact_backfill.py``'s own
    module docstring for the full account.

    Every fixture spells its ambiguous fact as an EXPLICIT resting value
    (``"deprecated": None``), never an omitted key: an omitted legacy key is
    a *different*, already-covered shape (`decode_fact_with_legacy_presence`
    resolves it to ``Fact.not_collected()`` outright, before this module's
    correction pass ever runs) -- the shape this module's own docstring
    describes is the legacy JSON format's inability to omit a value at all.
    """

    def test_confirmed_by_qualified_provenance_key_stays_present(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="hybrid",
            from_headers=True,
            types=[
                {
                    "name": "Foo",
                    "qualified_name": "ns::Foo",
                    "qualified_name_fact": _present("ns::Foo"),
                    "kind": "class",
                    "deprecated": None,
                }
            ],
            fact_provenance={"type:ns::Foo:deprecated": "castxml"},
        )
        rec = snapshot_from_dict(d).types[0]
        assert rec.deprecated_fact.status is FactStatus.PRESENT

    def test_no_provenance_entry_downgrades_a_resting_default(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="hybrid",
            from_headers=True,
            types=[
                {
                    "name": "Foo",
                    "qualified_name": "ns::Foo",
                    "qualified_name_fact": _present("ns::Foo"),
                    "kind": "class",
                    "deprecated": None,
                }
            ],
            fact_provenance={},
        )
        rec = snapshot_from_dict(d).types[0]
        assert rec.deprecated is None
        assert rec.deprecated_fact.status is FactStatus.NOT_COLLECTED

    def test_no_provenance_entry_preserves_a_real_non_default_value(self) -> None:
        # "Downgrade the claim, never the value" -- a document carrying a
        # non-resting value for this field got it from somewhere this
        # correction doesn't model, and discarding it would lose real data.
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="hybrid",
            from_headers=True,
            types=[
                {
                    "name": "Foo",
                    "qualified_name": "ns::Foo",
                    "qualified_name_fact": _present("ns::Foo"),
                    "kind": "class",
                    "deprecated": "use Bar",
                }
            ],
            fact_provenance={},
        )
        rec = snapshot_from_dict(d).types[0]
        assert rec.deprecated == "use Bar"
        assert rec.deprecated_fact.status is FactStatus.PRESENT

    def test_bare_key_fallback_when_unambiguous(self) -> None:
        # A hybrid baseline persisted before the provenance-key
        # qualification fix has real provenance recorded under the former
        # bare key alone -- must still resolve, not read as unconfirmed.
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="hybrid",
            from_headers=True,
            types=[
                {
                    "name": "Foo",
                    "qualified_name": "ns::Foo",
                    "qualified_name_fact": _present("ns::Foo"),
                    "kind": "class",
                    "deprecated": None,
                }
            ],
            fact_provenance={"type:Foo:deprecated": "castxml"},
        )
        rec = snapshot_from_dict(d).types[0]
        assert rec.deprecated_fact.status is FactStatus.PRESENT

    def test_bare_key_fallback_declined_when_ambiguous(self) -> None:
        # Two distinct qualified types share the bare name "Foo" -- the
        # bare-keyed provenance entry cannot be safely attributed to either
        # one, so neither may fall back to it.
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="hybrid",
            from_headers=True,
            types=[
                {
                    "name": "Foo",
                    "qualified_name": "ns::Foo",
                    "qualified_name_fact": _present("ns::Foo"),
                    "kind": "class",
                    "deprecated": None,
                },
                {
                    "name": "Foo",
                    "qualified_name": "other::Foo",
                    "qualified_name_fact": _present("other::Foo"),
                    "kind": "class",
                    "deprecated": None,
                },
            ],
            fact_provenance={"type:Foo:deprecated": "castxml"},
        )
        recs = snapshot_from_dict(d).types
        assert {r.deprecated_fact.status for r in recs} == {FactStatus.NOT_COLLECTED}

    def test_typefield_deprecated_uses_owning_type_provenance_key(self) -> None:
        # TypeField.deprecated converted one batch earlier (v39) than the
        # other six fields in this table (v40) -- needs its OWN, one-lower
        # legacy threshold to actually reach the backfill rule at all
        # (schema_version == 39 is already >= TypeField's own min_schema_
        # version, so at 39 this rule's own decode-time
        # `decode_fact_with_legacy_presence` resolves it directly, never
        # reaching this correction pass -- see
        # `test_typefield_deprecated_answers_to_the_same_flag` above for
        # that already-covered, ordinary v39 shape).
        d = _minimal_dict(
            schema_version=_MIN_SCHEMA_VERSION_FOR_TYPEFIELD_VALUE_FACTS - 1,
            ast_producer="hybrid",
            from_headers=True,
            types=[
                {
                    "name": "Foo",
                    "qualified_name": "ns::Foo",
                    "qualified_name_fact": _present("ns::Foo"),
                    "kind": "class",
                    "fields": [{"name": "m", "type": "int", "deprecated": None}],
                }
            ],
            fact_provenance={"type:ns::Foo:field:m:deprecated": "clang"},
        )
        field = snapshot_from_dict(d).types[0].fields[0]
        assert field.deprecated_fact.status is FactStatus.PRESENT

    def test_function_and_variable_use_mangled_name_key(self) -> None:
        # Mangled names are already unique -- no bare/qualified split, and
        # no provenance entry at all means neither backend ever looked.
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="hybrid",
            from_headers=True,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fv",
                    "return_type": "void",
                    "deprecated": None,
                }
            ],
            variables=[
                {"name": "g", "mangled": "g", "type": "int", "deprecated": None}
            ],
            fact_provenance={},
        )
        snap = snapshot_from_dict(d)
        assert snap.functions[0].deprecated_fact.status is FactStatus.NOT_COLLECTED
        assert snap.variables[0].deprecated_fact.status is FactStatus.NOT_COLLECTED

    def test_enum_is_scoped_uses_the_enum_provenance_key(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="hybrid",
            from_headers=True,
            enums=[
                {
                    "name": "Color",
                    "qualified_name": "ns::Color",
                    "qualified_name_fact": _present("ns::Color"),
                    "is_scoped": False,
                }
            ],
            fact_provenance={"enum:ns::Color:is_scoped": "clang"},
        )
        e = snapshot_from_dict(d).enums[0]
        assert e.is_scoped_fact.status is FactStatus.PRESENT

    def test_non_hybrid_producer_is_unaffected(self) -> None:
        # The provenance check only ever applies to ast_producer == "hybrid"
        # -- a plain clang/castxml legacy document keeps its pre-existing
        # (unrelated) reliability-flag-only behavior exactly.
        d = _minimal_dict(
            schema_version=_LEGACY,
            ast_producer="castxml",
            from_headers=True,
            types=[
                {
                    "name": "Foo",
                    "qualified_name": "ns::Foo",
                    "qualified_name_fact": _present("ns::Foo"),
                    "kind": "class",
                    "deprecated": None,
                }
            ],
            fact_provenance={},
        )
        rec = snapshot_from_dict(d).types[0]
        assert rec.deprecated_fact.status is FactStatus.PRESENT


class TestEnumIsScopedFact:
    def test_explicit_value_round_trips_present(self) -> None:
        e = _round_trip(_make_snap(enums=[EnumType(name="E", is_scoped=True)])).enums[0]
        assert e.is_scoped is True
        assert e.is_scoped_fact.status is FactStatus.PRESENT

    def test_omitted_is_not_collected(self) -> None:
        e = _round_trip(_make_snap(enums=[EnumType(name="E")])).enums[0]
        assert e.is_scoped is None
        assert e.is_scoped_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_unreliable_snapshot_downgrades_a_blanket_false(self) -> None:
        # The concrete pre-v19 clang bug: every enum read `is_scoped=False`.
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_deprecation_facts_reliable=False,
            enums=[{"name": "E", "is_scoped": False}],
        )
        e = snapshot_from_dict(d).enums[0]
        assert e.is_scoped is None
        assert e.is_scoped_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_reliable_snapshot_keeps_a_real_false(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_deprecation_facts_reliable=True,
            enums=[{"name": "E", "is_scoped": False}],
        )
        e = snapshot_from_dict(d).enums[0]
        assert e.is_scoped is False
        assert e.is_scoped_fact.status is FactStatus.PRESENT


class TestFactSiblingsSurviveMerge:
    """The mutation traps this batch's conversions turn into real bugs.

    Each of these call sites used a bare ``dataclasses.replace()`` to write
    ``deprecated``; once the field is ``Fact[...]``-bridged, that hands
    ``__post_init__`` the stale sibling alongside the new value — and the
    bridge resolves in the *sibling's* favour, reverting the write.
    """

    def test_hybrid_variable_merge_keeps_the_backfilled_value(self) -> None:
        from abicheck.dumper_hybrid import _merge_variable

        base = Variable(name="g", mangled="g", type="int", deprecated=None)
        clang = Variable(name="g", mangled="g", type="int", deprecated="use h()")
        merged = _merge_variable(base, clang, {})
        assert merged.deprecated == "use h()"
        assert merged.deprecated_fact.value == "use h()"

    def test_hybrid_enum_merge_keeps_both_backfilled_values(self) -> None:
        # Codex review, PR #993: _merge_enum_type applies its updates with
        # `replace(e, **updates)`, so no field name appears in the source and
        # the first sweep of this bug class missed it. Both attrs it merges
        # (`is_scoped`, `deprecated`) are Fact[...]-bridged since this batch.
        from abicheck.dumper_hybrid import _merge_enum_type

        merged = _merge_enum_type(
            EnumType(name="E"),
            EnumType(name="E", deprecated="use F", is_scoped=True),
            {},
        )
        assert merged.deprecated == "use F"
        assert merged.deprecated_fact.value == "use F"
        assert merged.is_scoped is True
        assert merged.is_scoped_fact.value is True

    def test_blank_provenance_blanks_the_fact_sibling_too(self) -> None:
        from abicheck.tu_merge_provenance import _blank_provenance

        f = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            deprecated="old",
            source_header="a.h",
        )
        blanked = _blank_provenance(f)
        assert blanked.deprecated is None
        assert blanked.deprecated_fact.status is FactStatus.NOT_COLLECTED
        # Two declarations differing only in blanked provenance must compare
        # equal — the whole reason _blank_provenance exists.
        other = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            deprecated="different",
            source_header="b.h",
        )
        assert blanked == _blank_provenance(other)

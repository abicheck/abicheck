"""Unit tests for AbiSnapshot JSON round-trip — elf_only_mode and constants.

Covers serialisation fields added in PR #63:
  - elf_only_mode
  - constants
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    ExtractionContract,
    Function,
)
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
    snapshot_to_json,
)


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


def _make_snap(**kwargs: object) -> AbiSnapshot:
    defaults = {
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


# ── EnumType.qualified_name (Codex review, PR #608 follow-up) ──────────────


class TestEnumQualifiedNameRoundTrip:
    def test_survives_roundtrip(self) -> None:
        """EnumType.qualified_name is serialized by snapshot_to_dict but was
        never read back by _enum_type_from_dict, so any dump/load comparison
        lost the enum's namespace identity and fell back to bare
        EnumType.name -- silently reopening the same cross-match/masked-
        removal risk TypeMap was built to fix, for every enum that went
        through a save/load cycle."""
        e = EnumType(
            name="Status",
            qualified_name="ns::Status",
            members=[EnumMember(name="OK", value=0)],
        )
        snap = _make_snap(enums=[e])
        d = snapshot_to_dict(snap)
        assert d["enums"][0]["qualified_name"] == "ns::Status"
        reloaded = snapshot_from_dict(d)
        assert reloaded.enums[0].qualified_name == "ns::Status"

    def test_defaults_to_none_when_absent(self) -> None:
        """A pre-existing snapshot dict predating this field must still load."""
        d = _minimal_dict(enums=[{"name": "Status", "members": []}])
        reloaded = snapshot_from_dict(d)
        assert reloaded.enums[0].qualified_name is None


# ── elf_only_mode ─────────────────────────────────────────────────────────


class TestElfOnlyModeRoundTrip:
    """elf_only_mode must survive JSON serialisation and deserialisation."""

    def test_true_survives_roundtrip(self) -> None:
        snap = _make_snap(elf_only_mode=True)
        j = json.loads(snapshot_to_json(snap))
        assert j.get("elf_only_mode") is True
        assert snapshot_from_dict(j).elf_only_mode is True

    def test_false_survives_roundtrip(self) -> None:
        snap = _make_snap(elf_only_mode=False)
        j = json.loads(snapshot_to_json(snap))
        restored = snapshot_from_dict(j)
        assert restored.elf_only_mode is False

    def test_defaults_to_false_when_absent(self) -> None:
        """Old snapshots without elf_only_mode key must deserialise to False."""
        d = _minimal_dict()
        assert "elf_only_mode" not in d
        assert snapshot_from_dict(d).elf_only_mode is False

    def test_truthy_int_coerces_to_bool_true(self) -> None:
        """Truthy non-bool values must coerce to bool True."""
        assert snapshot_from_dict(_minimal_dict(elf_only_mode=1)).elf_only_mode is True


# ── ast_producer ────────────────────────────────────────────────────────────


class TestAstProducerRoundTrip:
    """ast_producer must survive JSON serialisation and deserialisation.

    Regression guard (Codex review, PR #582): snapshot_to_dict() wrote the
    field via the generic dataclasses.asdict() pass, but snapshot_from_dict()
    never read it back into the reconstructed AbiSnapshot — every persisted
    castxml snapshot silently lost its producer tag on the normal
    dump-to-JSON-then-compare-files workflow, permanently disabling every
    detector gated on _both_castxml_backed (field defaults, abstract records,
    scoped enums, the override specifier, and all four deprecated kinds).
    """

    def test_castxml_survives_roundtrip(self) -> None:
        snap = _make_snap(from_headers=True, ast_producer="castxml")
        j = json.loads(snapshot_to_json(snap))
        assert j.get("ast_producer") == "castxml"
        assert snapshot_from_dict(j).ast_producer == "castxml"

    def test_clang_survives_roundtrip(self) -> None:
        snap = _make_snap(from_headers=True, ast_producer="clang")
        j = json.loads(snapshot_to_json(snap))
        restored = snapshot_from_dict(j)
        assert restored.ast_producer == "clang"

    def test_defaults_to_none_when_absent(self) -> None:
        """Old snapshots without the key must deserialise to None (unknown
        producer) — not silently assumed to be castxml."""
        d = _minimal_dict()
        assert "ast_producer" not in d
        assert snapshot_from_dict(d).ast_producer is None


class TestAstToolchainSupportedRoundTrip:
    """AbiSnapshot.ast_toolchain_supported / ast_toolchain_unsupported_reasons
    (schema v13, castxml_policy) must survive JSON serialisation and
    deserialisation, and default conservatively (unknown, not "supported")
    on a snapshot predating this field."""

    def test_supported_true_survives_roundtrip(self) -> None:
        snap = _make_snap(
            from_headers=True,
            ast_producer="castxml",
            ast_toolchain_supported=True,
            ast_toolchain_unsupported_reasons=[],
        )
        j = json.loads(snapshot_to_json(snap))
        assert j.get("ast_toolchain_supported") is True
        restored = snapshot_from_dict(j)
        assert restored.ast_toolchain_supported is True
        assert restored.ast_toolchain_unsupported_reasons == []

    def test_supported_false_with_reasons_survives_roundtrip(self) -> None:
        snap = _make_snap(
            from_headers=True,
            ast_producer="castxml",
            ast_toolchain_supported=False,
            ast_toolchain_unsupported_reasons=["castxml_version_below_minimum"],
        )
        j = json.loads(snapshot_to_json(snap))
        assert j.get("ast_toolchain_supported") is False
        restored = snapshot_from_dict(j)
        assert restored.ast_toolchain_supported is False
        assert restored.ast_toolchain_unsupported_reasons == [
            "castxml_version_below_minimum"
        ]

    def test_defaults_to_none_and_empty_when_absent(self) -> None:
        """A pre-v13 snapshot predating this field must deserialise to
        None/[] — "gate outcome unknown", never silently "supported"."""
        d = _minimal_dict()
        assert "ast_toolchain_supported" not in d
        restored = snapshot_from_dict(d)
        assert restored.ast_toolchain_supported is None
        assert restored.ast_toolchain_unsupported_reasons == []


class TestHeaderCvFactsReliableRoundTrip:
    """AbiSnapshot.header_cv_facts_reliable must be derived from
    schema_version, but SCOPED to the CastXML header path specifically —
    not a blanket schema_version cutoff (Codex review, PR #582, second
    round: the original blanket-by-schema_version derivation incorrectly
    also marked legacy DWARF-only and legacy clang-backend snapshots
    unreliable, even though neither was ever affected by the CastXML
    parser bug this flag exists to guard against).

    A pre-v9 *CastXML header-parsed* snapshot's
    TypeField.is_const/is_volatile/is_mutable are permanently False and its
    type spelling never carried a cv qualifier — real (not absent) data
    indistinguishable from a genuine "not const" fact by value alone. But a
    DWARF-only snapshot (``from_headers=False``) derives these independently
    from DW_TAG_const_type/DW_TAG_volatile_type (dwarf_snapshot.py), and the
    clang L2 header backend (``ast_producer="clang"``) derives them via its
    own regex-based qualifier scan (dumper_clang.py) — neither code path was
    ever touched by the CastXML bug, so both stay reliable regardless of
    schema_version.
    """

    def test_fresh_in_memory_snapshot_defaults_reliable(self) -> None:
        snap = _make_snap()
        assert snap.header_cv_facts_reliable is True

    def test_fresh_dump_serializes_current_schema_version(self) -> None:
        from abicheck.serialization import SCHEMA_VERSION

        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert j["schema_version"] == SCHEMA_VERSION == 20

    def test_legacy_castxml_header_snapshot_loads_as_unreliable(self) -> None:
        d = _minimal_dict(schema_version=8, from_headers=True, ast_producer="castxml")
        restored = snapshot_from_dict(d)
        assert restored.header_cv_facts_reliable is False

    def test_current_castxml_header_snapshot_loads_as_reliable(self) -> None:
        d = _minimal_dict(schema_version=9, from_headers=True, ast_producer="castxml")
        restored = snapshot_from_dict(d)
        assert restored.header_cv_facts_reliable is True

    def test_legacy_header_snapshot_predating_ast_producer_is_conservatively_unreliable(
        self,
    ) -> None:
        """A header-parsed snapshot with no ast_producer key at all predates
        that field's introduction and cannot be told apart from a pre-fix
        castxml dump — conservatively treated as unreliable, mirroring
        ast_producer's own None-handling elsewhere."""
        d = _minimal_dict(schema_version=8, from_headers=True)
        assert "ast_producer" not in d
        assert snapshot_from_dict(d).header_cv_facts_reliable is False

    def test_legacy_dwarf_only_snapshot_stays_reliable(self) -> None:
        """A DWARF-only snapshot's is_const/is_volatile were never derived
        by CastXML at all — schema_version is irrelevant to their
        reliability."""
        d = _minimal_dict(schema_version=8, from_headers=False)
        assert snapshot_from_dict(d).header_cv_facts_reliable is True

    def test_legacy_clang_backend_snapshot_stays_reliable(self) -> None:
        """The clang L2 header backend has always derived these facts via
        its own code path — never affected by the CastXML parser bug."""
        d = _minimal_dict(schema_version=8, from_headers=True, ast_producer="clang")
        assert snapshot_from_dict(d).header_cv_facts_reliable is True

    def test_missing_schema_version_key_on_castxml_header_snapshot_is_legacy(
        self,
    ) -> None:
        """No schema_version key at all predates even the original
        schema-versioning PR (#89) — necessarily older than the CV-fact fix
        too, for a CastXML-parsed header snapshot."""
        d = _minimal_dict(from_headers=True, ast_producer="castxml")
        assert "schema_version" not in d
        assert snapshot_from_dict(d).header_cv_facts_reliable is False

    def test_round_trip_preserves_reliable_true(self) -> None:
        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert snapshot_from_dict(j).header_cv_facts_reliable is True

    def test_reserialized_legacy_snapshot_stays_unreliable(self) -> None:
        """Regression guard (Codex review, PR #582): a load -> save -> load
        round-trip always re-stamps schema_version to the CURRENT
        SCHEMA_VERSION (it reflects the writing tool's format capability,
        not the snapshot's true field-fact origin). Re-deriving
        header_cv_facts_reliable purely from schema_version on a
        reserialized legacy snapshot would silently flip an
        already-known-unreliable snapshot's stale, real-but-wrong cv facts
        back to "reliable" the next time it's loaded, reintroducing the
        exact false-positive class this flag exists to prevent. An explicit
        header_cv_facts_reliable key in the dict must be trusted over
        re-deriving from schema_version."""
        legacy = snapshot_from_dict(
            _minimal_dict(schema_version=8, from_headers=True, ast_producer="castxml")
        )
        assert legacy.header_cv_facts_reliable is False

        reserialized = snapshot_to_dict(legacy)
        assert reserialized["schema_version"] == 20
        assert reserialized["header_cv_facts_reliable"] is False

        reloaded = snapshot_from_dict(reserialized)
        assert reloaded.header_cv_facts_reliable is False


class TestClangDeprecationFactsReliableRoundTrip:
    """AbiSnapshot.clang_deprecation_facts_reliable (schema v19, G31 Phase C,
    Codex review, fresh evidence) — same derivation shape as
    TestHeaderCvFactsReliableRoundTrip above, but the affected path is the
    OPPOSITE producer: a pre-v19 CLANG-producer snapshot's deprecated/
    is_scoped facts are real but WRONG (unconditional None/False), while a
    castxml-producer snapshot's own extraction of these facts predates this
    field entirely (G28 Phase 1) and was always reliable, regardless of
    schema_version.
    """

    def test_fresh_in_memory_snapshot_defaults_reliable(self) -> None:
        snap = _make_snap()
        assert snap.clang_deprecation_facts_reliable is True

    def test_legacy_clang_header_snapshot_loads_as_unreliable(self) -> None:
        d = _minimal_dict(schema_version=18, from_headers=True, ast_producer="clang")
        restored = snapshot_from_dict(d)
        assert restored.clang_deprecation_facts_reliable is False

    def test_current_clang_header_snapshot_loads_as_reliable(self) -> None:
        d = _minimal_dict(schema_version=19, from_headers=True, ast_producer="clang")
        restored = snapshot_from_dict(d)
        assert restored.clang_deprecation_facts_reliable is True

    def test_legacy_header_snapshot_predating_ast_producer_stays_reliable(
        self,
    ) -> None:
        """Unlike header_cv_facts_reliable (which is read regardless of
        ast_producer), this flag is only ever CONSULTED by fact_producer()
        when ast_producer == "clang" specifically -- an absent/unknown
        producer already fails that check on its own, so this flag's own
        value is moot for it, and the honest default (reliable) is correct
        rather than a defensive False that can never actually matter."""
        d = _minimal_dict(schema_version=18, from_headers=True)
        assert "ast_producer" not in d
        assert snapshot_from_dict(d).clang_deprecation_facts_reliable is True

    def test_legacy_dwarf_only_snapshot_stays_reliable(self) -> None:
        """A DWARF-only snapshot never derives deprecated/is_scoped from
        either header backend at all — schema_version is irrelevant."""
        d = _minimal_dict(schema_version=18, from_headers=False)
        assert snapshot_from_dict(d).clang_deprecation_facts_reliable is True

    def test_legacy_castxml_header_snapshot_stays_reliable(self) -> None:
        """CastXML's own deprecated/is_scoped extraction predates this flag
        entirely (G28 Phase 1) and was never affected by the clang-side
        gap this flag guards against."""
        d = _minimal_dict(schema_version=18, from_headers=True, ast_producer="castxml")
        assert snapshot_from_dict(d).clang_deprecation_facts_reliable is True

    def test_legacy_hybrid_snapshot_stays_reliable(self) -> None:
        """A legacy hybrid snapshot's own fact_provenance was always
        recorded "castxml" for deprecated/is_scoped under the OLD merge
        code (its backfill policy only ever saw a None clang value pre-fix,
        so it never recorded "clang" provenance for these two facts) --
        no equivalent false-reliability risk exists for this producer."""
        d = _minimal_dict(schema_version=18, from_headers=True, ast_producer="hybrid")
        assert snapshot_from_dict(d).clang_deprecation_facts_reliable is True

    def test_missing_schema_version_key_on_clang_header_snapshot_is_legacy(
        self,
    ) -> None:
        d = _minimal_dict(from_headers=True, ast_producer="clang")
        assert "schema_version" not in d
        assert snapshot_from_dict(d).clang_deprecation_facts_reliable is False

    def test_round_trip_preserves_reliable_true(self) -> None:
        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert snapshot_from_dict(j).clang_deprecation_facts_reliable is True

    def test_reserialized_legacy_snapshot_stays_unreliable(self) -> None:
        """Same round-trip-stability regression guard as
        TestHeaderCvFactsReliableRoundTrip.test_reserialized_legacy_snapshot_stays_unreliable:
        an explicit clang_deprecation_facts_reliable key in the dict must be
        trusted over re-deriving from the always-current schema_version."""
        legacy = snapshot_from_dict(
            _minimal_dict(schema_version=18, from_headers=True, ast_producer="clang")
        )
        assert legacy.clang_deprecation_facts_reliable is False

        reserialized = snapshot_to_dict(legacy)
        assert reserialized["schema_version"] == 20
        assert reserialized["clang_deprecation_facts_reliable"] is False

        reloaded = snapshot_from_dict(reserialized)
        assert reloaded.clang_deprecation_facts_reliable is False

    def test_gates_fact_producer_for_deprecated_and_is_scoped_only(self) -> None:
        """Direct fact_provenance.fact_producer() coverage: the gate must
        apply to exactly the two affected fact suffixes, not blanket-
        invalidate every fact on a legacy clang snapshot (e.g.
        param_defaults, which has its own, unrelated same-producer gate)."""
        from abicheck.fact_provenance import (
            enum_fact_key,
            fact_producer,
            func_fact_key,
        )

        legacy_clang = snapshot_from_dict(
            _minimal_dict(schema_version=18, from_headers=True, ast_producer="clang")
        )
        assert (
            fact_producer(legacy_clang, func_fact_key("_Z3foov", "deprecated")) is None
        )
        assert fact_producer(legacy_clang, enum_fact_key("Color", "is_scoped")) is None
        # Unaffected fact on the same legacy snapshot: still trusted.
        assert (
            fact_producer(legacy_clang, func_fact_key("_Z3foov", "param_defaults"))
            == "clang"
        )


class TestClangFieldInitializerFactsReliableRoundTrip:
    """AbiSnapshot.clang_field_initializer_facts_reliable (schema v20, G31
    Phase C continuation) — same derivation shape as
    TestClangDeprecationFactsReliableRoundTrip above, one schema version and
    one fact (TypeField.default) later. Tracked as its own flag rather than
    folded into clang_deprecation_facts_reliable: a v19 snapshot has
    reliable deprecated/is_scoped but unreliable field defaults, which one
    shared flag could not express.
    """

    def test_fresh_in_memory_snapshot_defaults_reliable(self) -> None:
        snap = _make_snap()
        assert snap.clang_field_initializer_facts_reliable is True

    def test_legacy_clang_header_snapshot_loads_as_unreliable(self) -> None:
        d = _minimal_dict(schema_version=19, from_headers=True, ast_producer="clang")
        restored = snapshot_from_dict(d)
        assert restored.clang_field_initializer_facts_reliable is False

    def test_current_clang_header_snapshot_loads_as_reliable(self) -> None:
        d = _minimal_dict(schema_version=20, from_headers=True, ast_producer="clang")
        restored = snapshot_from_dict(d)
        assert restored.clang_field_initializer_facts_reliable is True

    def test_legacy_header_snapshot_predating_ast_producer_stays_reliable(
        self,
    ) -> None:
        """Same reasoning as clang_deprecation_facts_reliable's identical
        test: an absent/unknown ast_producer already fails fact_producer()'s
        check on its own, so this flag's own value is moot for it."""
        d = _minimal_dict(schema_version=19, from_headers=True)
        assert "ast_producer" not in d
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is True

    def test_legacy_dwarf_only_snapshot_stays_reliable(self) -> None:
        d = _minimal_dict(schema_version=19, from_headers=False)
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is True

    def test_legacy_castxml_header_snapshot_stays_reliable(self) -> None:
        d = _minimal_dict(schema_version=19, from_headers=True, ast_producer="castxml")
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is True

    def test_legacy_hybrid_snapshot_stays_reliable(self) -> None:
        """A legacy hybrid snapshot's field ``default`` provenance was
        always recorded "castxml" for a matched pair under the OLD (pre-G31
        Phase C) merge code, since clang could never populate it then --
        no equivalent false-reliability risk exists for this producer."""
        d = _minimal_dict(schema_version=19, from_headers=True, ast_producer="hybrid")
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is True

    def test_missing_schema_version_key_on_clang_header_snapshot_is_legacy(
        self,
    ) -> None:
        d = _minimal_dict(from_headers=True, ast_producer="clang")
        assert "schema_version" not in d
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is False

    def test_round_trip_preserves_reliable_true(self) -> None:
        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert snapshot_from_dict(j).clang_field_initializer_facts_reliable is True

    def test_reserialized_legacy_snapshot_stays_unreliable(self) -> None:
        legacy = snapshot_from_dict(
            _minimal_dict(schema_version=19, from_headers=True, ast_producer="clang")
        )
        assert legacy.clang_field_initializer_facts_reliable is False

        reserialized = snapshot_to_dict(legacy)
        assert reserialized["schema_version"] == 20
        assert reserialized["clang_field_initializer_facts_reliable"] is False

        reloaded = snapshot_from_dict(reserialized)
        assert reloaded.clang_field_initializer_facts_reliable is False

    def test_gates_fact_producer_for_field_default_only(self) -> None:
        """Direct fact_provenance.fact_producer() coverage: the gate must
        apply to exactly the field ``:default`` suffix, not blanket-
        invalidate every fact on a legacy clang snapshot (e.g.
        deprecated/is_scoped, which are reliable as of v19, one version
        earlier than this flag)."""
        from abicheck.fact_provenance import (
            fact_producer,
            field_fact_key,
            func_fact_key,
        )

        legacy_clang = snapshot_from_dict(
            _minimal_dict(schema_version=19, from_headers=True, ast_producer="clang")
        )
        assert (
            fact_producer(legacy_clang, field_fact_key("Cfg", "timeout", "default"))
            is None
        )
        # Unaffected facts on the same legacy snapshot: still trusted.
        assert (
            fact_producer(legacy_clang, func_fact_key("_Z3foov", "deprecated"))
            == "clang"
        )
        assert (
            fact_producer(legacy_clang, func_fact_key("_Z3foov", "param_defaults"))
            == "clang"
        )


class TestDependencyScopeRoundtrip:
    """Schema v18 — AbiSnapshot.dependency_scope round-trips like every
    other purely-additive optional field; a pre-v18 snapshot with no key at
    all, or an explicit null, loads it as None; but a present, invalid
    value is rejected outright rather than silently downgraded to None."""

    def test_round_trip_preserves_filtered(self) -> None:
        snap = _make_snap(from_headers=True, dependency_scope="filtered")
        j = json.loads(snapshot_to_json(snap))
        assert j["dependency_scope"] == "filtered"
        assert snapshot_from_dict(j).dependency_scope == "filtered"

    def test_round_trip_preserves_full(self) -> None:
        snap = _make_snap(from_headers=True, dependency_scope="full")
        j = json.loads(snapshot_to_json(snap))
        restored = snapshot_from_dict(j)
        assert restored.dependency_scope == "full"

    def test_missing_key_loads_as_none(self) -> None:
        d = _minimal_dict(schema_version=17, from_headers=True)
        assert "dependency_scope" not in d
        assert snapshot_from_dict(d).dependency_scope is None

    def test_unrecognized_string_value_rejected(self) -> None:
        # Codex review, second round: a present, non-null value that isn't
        # "filtered"/"full" is not a value this codebase's own producers
        # ever write -- silently downgrading it to None would let a
        # corrupt/hand-edited snapshot (e.g. a "filterd" typo) exploit the
        # comparability gate's deliberate "a None side is never checked"
        # leniency and bypass a real filtered-vs-full mismatch. Must fail
        # loading instead of silently becoming "not recorded".
        d = _minimal_dict(
            schema_version=18, from_headers=True, dependency_scope="bogus"
        )
        with pytest.raises(SnapshotError, match="bogus"):
            snapshot_from_dict(d)

    def test_non_string_value_rejected(self) -> None:
        d = _minimal_dict(schema_version=18, from_headers=True, dependency_scope=123)
        with pytest.raises(SnapshotError, match="123"):
            snapshot_from_dict(d)

    def test_explicit_null_loads_as_none(self) -> None:
        d = _minimal_dict(schema_version=18, from_headers=True, dependency_scope=None)
        assert snapshot_from_dict(d).dependency_scope is None


# ── constants ─────────────────────────────────────────────────────────────


class TestConstantsRoundTrip:
    """constants dict must survive JSON serialisation and deserialisation."""

    def test_dict_survives_roundtrip(self) -> None:
        snap = _make_snap(constants={"MAX_SIZE": "256", "VERSION": "3"})
        j = json.loads(snapshot_to_json(snap))
        restored = snapshot_from_dict(j)
        assert restored.constants == {"MAX_SIZE": "256", "VERSION": "3"}

    def test_defaults_to_empty_dict_when_absent(self) -> None:
        """Old snapshots without constants must deserialise to an empty dict."""
        assert snapshot_from_dict(_minimal_dict()).constants == {}


# ── Function.deleted_from_dwarf ───────────────────────────────────────────


class TestDeletedFromDwarfRoundTrip:
    """Function.deleted_from_dwarf provenance must survive JSON round-trip.

    snapshot_to_dict writes it (via asdict), but snapshot_from_dict rebuilds
    Function manually — if it drops the key, a DWARF-deleted unexported member
    loads as deleted_from_dwarf=False, re-entering the public surface and
    producing FUNC_REMOVED false positives against a stripped build.
    """

    def _func(self, **kw: object) -> Function:
        return Function(
            name="atomic_backoff",
            mangled="_ZN3tbb14atomic_backoffC4ERKS_",
            return_type="void",
            **kw,  # type: ignore[arg-type]
        )

    def test_true_survives_roundtrip(self) -> None:
        snap = _make_snap(
            functions=[self._func(is_deleted=True, deleted_from_dwarf=True)]
        )
        j = json.loads(snapshot_to_json(snap))
        assert j["functions"][0]["deleted_from_dwarf"] is True
        restored = snapshot_from_dict(j)
        assert restored.functions[0].deleted_from_dwarf is True
        assert restored.functions[0].is_deleted is True

    def test_false_survives_roundtrip(self) -> None:
        snap = _make_snap(
            functions=[self._func(is_deleted=True, deleted_from_dwarf=False)]
        )
        restored = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
        assert restored.functions[0].deleted_from_dwarf is False

    def test_defaults_to_false_when_absent(self) -> None:
        """Legacy snapshots without the key deserialise to False."""
        d = _minimal_dict(
            functions=[{"name": "f", "mangled": "f", "return_type": "void"}]
        )
        assert "deleted_from_dwarf" not in d["functions"][0]
        assert snapshot_from_dict(d).functions[0].deleted_from_dwarf is False


# ── inferred from_headers provenance ──────────────────────────────────────


class TestInferredFromHeadersProvenance:
    """Inferred legacy provenance must not become explicit across a re-save.

    A legacy snapshot (no ``from_headers`` key) infers ``from_headers=True`` but
    marks ``from_headers_inferred=True``. Re-serializing must NOT emit
    ``from_headers: true`` as explicit provenance, or reloading the migrated
    baseline would re-enable source-level param-rename detection on DWARF-only
    surfaces.
    """

    def _legacy_dict(self) -> dict:
        return _minimal_dict(
            functions=[{"name": "f", "mangled": "_Z1fi", "return_type": "void"}],
        )

    def test_inferred_provenance_not_persisted_as_explicit(self) -> None:
        loaded = snapshot_from_dict(self._legacy_dict())
        assert loaded.from_headers is True
        assert loaded.from_headers_inferred is True
        # The re-emitted dict must not carry an explicit from_headers key.
        reemitted = json.loads(snapshot_to_json(loaded))
        assert "from_headers" not in reemitted
        # Reloading the migrated baseline stays inferred, not explicit.
        reloaded = snapshot_from_dict(reemitted)
        assert reloaded.from_headers is True
        assert reloaded.from_headers_inferred is True

    def test_explicit_provenance_is_persisted(self) -> None:
        snap = _make_snap(from_headers=True)
        assert snap.from_headers_inferred is False
        reemitted = json.loads(snapshot_to_json(snap))
        assert reemitted.get("from_headers") is True
        assert snapshot_from_dict(reemitted).from_headers_inferred is False


# ── file-based round-trip ─────────────────────────────────────────────────


class TestFileRoundTrip:
    """save_snapshot / load_snapshot must preserve new fields."""

    def test_elf_only_mode_and_constants_survive_file_io(self, tmp_path: Path) -> None:
        snap = _make_snap(elf_only_mode=True, constants={"FOO": "bar"})
        p = tmp_path / "snap.json"
        save_snapshot(snap, p)
        restored = load_snapshot(p)
        assert restored.elf_only_mode is True
        assert restored.constants == {"FOO": "bar"}


# ── ExtractionContract round-trip (ADR-050 D1, schema v12) ─────────────────


class TestExtractionContractRoundTrip:
    def test_populated_contract_survives_json_round_trip(self) -> None:
        contract = ExtractionContract(
            profile_fingerprint="sha256:abc",
            scope_fingerprint="sha256:def",
            profile_fields={"target_triple": "x86_64-linux-gnu"},
            scope_fields={"headers": "foo.h"},
        )
        snap = _make_snap(contract=contract)
        reloaded = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
        assert reloaded.contract == contract

    def test_missing_contract_key_loads_as_none(self) -> None:
        d = _minimal_dict()
        assert "contract" not in d
        assert snapshot_from_dict(d).contract is None

    def test_null_contract_loads_as_none(self) -> None:
        d = _minimal_dict(contract=None)
        assert snapshot_from_dict(d).contract is None

    def test_malformed_contract_value_loads_as_none(self) -> None:
        d = _minimal_dict(contract="not-a-dict")
        assert snapshot_from_dict(d).contract is None

    def test_contract_with_malformed_nested_fields_defaults_gracefully(self) -> None:
        d = _minimal_dict(
            contract={
                "profile_fingerprint": 123,  # not a str
                "scope_fingerprint": None,
                "profile_fields": "not-a-dict",
                "scope_fields": {"headers": "foo.h"},
            }
        )
        restored = snapshot_from_dict(d).contract
        assert restored is not None
        assert restored.profile_fingerprint is None
        assert restored.scope_fingerprint is None
        assert restored.profile_fields == {}
        assert restored.scope_fields == {"headers": "foo.h"}

    def test_contract_survives_file_io(self, tmp_path: Path) -> None:
        contract = ExtractionContract(
            profile_fingerprint="sha256:abc", scope_fingerprint="sha256:def"
        )
        snap = _make_snap(contract=contract)
        p = tmp_path / "snap.json"
        save_snapshot(snap, p)
        restored = load_snapshot(p)
        assert restored.contract == contract

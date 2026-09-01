"""Unit tests for AbiSnapshot JSON round-trip — elf_only_mode and constants.

Covers serialisation fields added in PR #63: elf_only_mode, constants.
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
    Fact,
    FactStatus,
    Function,
    Param,
    RecordType,
)
from abicheck.serialization import (
    SCHEMA_VERSION,
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


def _va_list_func(is_va_list: bool) -> dict:
    """One raw function dict with a single ``va_list`` param — shared by the is_va_list backfill tests, which only vary the flag/producer/reliability."""
    return {
        "name": "f",
        "mangled": "_Z1fz",
        "return_type": "void",
        "params": [{"name": "a", "type": "va_list", "is_va_list": is_va_list}],
    }


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


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))


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
        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert j["schema_version"] == SCHEMA_VERSION

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
        assert reserialized["schema_version"] == SCHEMA_VERSION
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
        assert reserialized["schema_version"] == SCHEMA_VERSION
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

    def test_legacy_header_snapshot_predating_ast_producer_loads_as_unreliable(
        self,
    ) -> None:
        """Codex review, fresh evidence, third round: unlike
        clang_deprecation_facts_reliable (whose one consumer requires an
        EXACT ``ast_producer == "clang"`` match, so an absent/unknown
        producer already fails that check on its own regardless of this
        flag's value), this flag's own consumer
        (``default_value_representation_unreliable``) treats a producer of
        ``None`` as clang-family RISK, not exclusion -- deliberately, since
        `ast_producer` has always had exactly three real values
        (`"clang"`/`"castxml"`/`"hybrid"`) and a pre-v10 snapshot missing
        the key entirely could be a legacy direct-clang dump with the old
        unstable ``"expr:"`` fingerprint. This flag's OWN derivation must
        agree with that consumer's promise: only a POSITIVELY known
        `"castxml"` producer is safe to trust, never an absent one. Before
        this fix, `ast_producer_value not in ("clang", "hybrid")` treated a
        `None` producer as "definitely not clang/hybrid" -- the opposite of
        the correct, conservative answer -- silently letting a legacy
        direct-clang snapshot's stale fingerprint compare against a fresh
        one and report a false PARAM_DEFAULT_VALUE_CHANGED/
        FIELD_DEFAULT_INITIALIZER_CHANGED for an unchanged default."""
        d = _minimal_dict(schema_version=19, from_headers=True)
        assert "ast_producer" not in d
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is False

    def test_legacy_dwarf_only_snapshot_stays_reliable(self) -> None:
        d = _minimal_dict(schema_version=19, from_headers=False)
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is True

    def test_legacy_castxml_header_snapshot_stays_reliable(self) -> None:
        d = _minimal_dict(schema_version=19, from_headers=True, ast_producer="castxml")
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is True

    def test_legacy_hybrid_snapshot_loads_as_unreliable(self) -> None:
        """Codex review, fresh evidence, second round: unlike
        clang_deprecation_facts_reliable, this flag DOES need to cover
        "hybrid" too. A MATCHED field's ``default`` provenance was always
        recorded "castxml" under the OLD (pre-G31 Phase C) merge code, but a
        pre-v20 hybrid merge's clang-only-APPENDED record types never had
        ``default`` provenance stamped at ALL (only ``deprecated`` was) --
        so an absent entry for one of those fields is real-but-WRONG legacy
        data, not genuinely unrecorded provenance. See
        same_producer_backed_fact_qualified's own tests below for the
        end-to-end consequence (a matched field's recorded entry stays
        trusted regardless of this flag; only an absent entry is blocked)."""
        d = _minimal_dict(schema_version=19, from_headers=True, ast_producer="hybrid")
        assert snapshot_from_dict(d).clang_field_initializer_facts_reliable is False

    def test_current_hybrid_header_snapshot_loads_as_reliable(self) -> None:
        d = _minimal_dict(schema_version=20, from_headers=True, ast_producer="hybrid")
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
        assert reserialized["schema_version"] == SCHEMA_VERSION
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

    def test_same_producer_gate_declines_against_unreliable_legacy_side(
        self,
    ) -> None:
        """Codex review, PR #687: same_producer_backed_fact_qualified's
        permissive "producer unknown -> allow" fallback must NOT treat a
        POSITIVELY known-unreliable value (this flag False) the same as
        genuinely-unrecorded provenance -- otherwise a fresh clang snapshot's
        real field initializer compared against this legacy snapshot would
        read as removed, purely from the schema upgrade."""
        from abicheck.fact_provenance import (
            field_fact_key,
            same_producer_backed_fact_qualified,
        )

        legacy_clang = snapshot_from_dict(
            _minimal_dict(schema_version=19, from_headers=True, ast_producer="clang")
        )
        fresh_clang = _make_snap(from_headers=True, ast_producer="clang")
        key = field_fact_key("Cfg", "timeout", "default")

        assert (
            same_producer_backed_fact_qualified(
                fresh_clang,
                legacy_clang,
                key,
                key,
                key,
                old_bare_unambiguous=True,
                new_bare_unambiguous=True,
            )
            is False
        )
        # Symmetric direction, and both-reliable stays permissive/comparable.
        assert (
            same_producer_backed_fact_qualified(
                legacy_clang,
                fresh_clang,
                key,
                key,
                key,
                old_bare_unambiguous=True,
                new_bare_unambiguous=True,
            )
            is False
        )
        assert (
            same_producer_backed_fact_qualified(
                fresh_clang,
                fresh_clang,
                key,
                key,
                key,
                old_bare_unambiguous=True,
                new_bare_unambiguous=True,
            )
            is True
        )

    def test_same_producer_gate_declines_against_unreliable_legacy_hybrid_absence(
        self,
    ) -> None:
        """Codex review, PR #687, second round, fresh evidence: a legacy
        (pre-v20) hybrid snapshot's clang-only-appended field never had
        ``default`` provenance stamped at all, so an ABSENT entry there must
        be declined the same way a legacy pure-clang snapshot's explicit
        unreliable value is -- not treated as harmless "genuinely unknown"."""
        from abicheck.fact_provenance import (
            field_fact_key,
            same_producer_backed_fact_qualified,
        )

        legacy_hybrid = snapshot_from_dict(
            _minimal_dict(schema_version=19, from_headers=True, ast_producer="hybrid")
        )
        fresh_hybrid = _make_snap(
            from_headers=True,
            ast_producer="hybrid",
            fact_provenance={"type:Cfg:field:timeout:default": "clang"},
        )
        key = field_fact_key("Cfg", "timeout", "default")

        # legacy_hybrid.fact_provenance has no entry for this key at all --
        # exactly the clang-only-appended, pre-fix shape.
        assert key not in legacy_hybrid.fact_provenance
        assert (
            same_producer_backed_fact_qualified(
                fresh_hybrid,
                legacy_hybrid,
                key,
                key,
                key,
                old_bare_unambiguous=True,
                new_bare_unambiguous=True,
            )
            is False
        )

    def test_same_producer_gate_trusts_recorded_entry_on_legacy_hybrid(self) -> None:
        """The flip side of the case above: a MATCHED field's ``default``
        provenance on the SAME legacy hybrid snapshot was always
        unconditionally stamped "castxml" by ``backfill_fact`` regardless of
        schema version -- a PRESENT entry stays trusted and comparable even
        though the snapshot's own reliability flag is False, since that flag
        only governs what an ABSENCE means, not a recorded value."""
        from abicheck.fact_provenance import (
            field_fact_key,
            same_producer_backed_fact_qualified,
        )

        key = field_fact_key("Cfg", "timeout", "default")
        legacy_hybrid = snapshot_from_dict(
            _minimal_dict(
                schema_version=19,
                from_headers=True,
                ast_producer="hybrid",
                fact_provenance={key: "castxml"},
            )
        )
        assert legacy_hybrid.clang_field_initializer_facts_reliable is False
        other_castxml_matched = _make_snap(
            from_headers=True,
            ast_producer="hybrid",
            fact_provenance={key: "castxml"},
        )

        assert (
            same_producer_backed_fact_qualified(
                legacy_hybrid,
                other_castxml_matched,
                key,
                key,
                key,
                old_bare_unambiguous=True,
                new_bare_unambiguous=True,
            )
            is True
        )


class TestClangVtableFactsReliableRoundTrip:
    """AbiSnapshot.clang_vtable_facts_reliable (schema v21, G31 Phase C
    continuation, Codex review, fresh evidence) — same derivation shape as
    TestClangDeprecationFactsReliableRoundTrip above: only the direct-clang
    ("clang") producer path is affected, not "hybrid" (the vtable
    reconstruction lives entirely in dumper_clang_vtable.py, never invoked by
    the hybrid merge path, so a legacy hybrid snapshot's vtable facts always
    came from castxml with no equivalent false-reliability risk).
    """

    def test_fresh_in_memory_snapshot_defaults_reliable(self) -> None:
        snap = _make_snap()
        assert snap.clang_vtable_facts_reliable is True

    def test_legacy_clang_header_snapshot_loads_as_unreliable(self) -> None:
        d = _minimal_dict(schema_version=20, from_headers=True, ast_producer="clang")
        restored = snapshot_from_dict(d)
        assert restored.clang_vtable_facts_reliable is False

    def test_current_clang_header_snapshot_loads_as_reliable(self) -> None:
        d = _minimal_dict(schema_version=21, from_headers=True, ast_producer="clang")
        restored = snapshot_from_dict(d)
        assert restored.clang_vtable_facts_reliable is True

    def test_legacy_header_snapshot_predating_ast_producer_stays_reliable(
        self,
    ) -> None:
        """Same reasoning as clang_deprecation_facts_reliable: this flag's
        one consumer requires an EXACT ``ast_producer == "clang"`` match, so
        an absent/unknown producer already excludes the affected path on its
        own, and the honest default (reliable) is correct rather than a
        defensive False that can never actually matter."""
        d = _minimal_dict(schema_version=20, from_headers=True)
        assert "ast_producer" not in d
        assert snapshot_from_dict(d).clang_vtable_facts_reliable is True

    def test_legacy_dwarf_only_snapshot_stays_reliable(self) -> None:
        """A DWARF-only snapshot's vtable/vptr facts come from
        dwarf_snapshot.py, a wholly separate code path this flag does not
        describe -- schema_version is irrelevant."""
        d = _minimal_dict(schema_version=20, from_headers=False)
        assert snapshot_from_dict(d).clang_vtable_facts_reliable is True

    def test_legacy_castxml_header_snapshot_stays_reliable(self) -> None:
        d = _minimal_dict(schema_version=20, from_headers=True, ast_producer="castxml")
        assert snapshot_from_dict(d).clang_vtable_facts_reliable is True

    def test_legacy_hybrid_snapshot_stays_reliable(self) -> None:
        d = _minimal_dict(schema_version=20, from_headers=True, ast_producer="hybrid")
        assert snapshot_from_dict(d).clang_vtable_facts_reliable is True

    def test_missing_schema_version_key_on_clang_header_snapshot_is_legacy(
        self,
    ) -> None:
        d = _minimal_dict(from_headers=True, ast_producer="clang")
        assert "schema_version" not in d
        assert snapshot_from_dict(d).clang_vtable_facts_reliable is False

    def test_round_trip_preserves_reliable_true(self) -> None:
        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert snapshot_from_dict(j).clang_vtable_facts_reliable is True

    def test_reserialized_legacy_snapshot_stays_unreliable(self) -> None:
        legacy = snapshot_from_dict(
            _minimal_dict(schema_version=20, from_headers=True, ast_producer="clang")
        )
        assert legacy.clang_vtable_facts_reliable is False

        reserialized = snapshot_to_dict(legacy)
        assert reserialized["schema_version"] == SCHEMA_VERSION
        assert reserialized["clang_vtable_facts_reliable"] is False

        reloaded = snapshot_from_dict(reserialized)
        assert reloaded.clang_vtable_facts_reliable is False


class TestClangRestrictFactsReliableRoundTrip:
    """AbiSnapshot.clang_restrict_facts_reliable (schema v22, G31 Phase C,
    ``dumper_clang._clang_param_is_restrict``).

    Same real-but-WRONG shape as the three flags above: ``Param.is_restrict``
    is a plain bool with no "not collected" state, so a pre-v22
    clang-producer parameter's blanket ``False`` cannot be told from a
    genuinely unqualified parameter by value alone. Covers ``"hybrid"`` as
    well as ``"clang"`` (a hybrid merge appends clang-only functions with
    clang's own parameters verbatim), and — like
    ``clang_field_initializer_facts_reliable`` — treats an ABSENT
    ``ast_producer`` as unknown rather than as castxml.
    """

    def test_legacy_clang_header_snapshot_loads_as_unreliable(self) -> None:
        d = _minimal_dict(schema_version=21, from_headers=True, ast_producer="clang")
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is False

    def test_legacy_hybrid_header_snapshot_loads_as_unreliable(self) -> None:
        """Unlike clang_vtable_facts_reliable, hybrid IS affected: a merge
        keeps castxml's params for a matched function, but appends a
        clang-ONLY function's parameters verbatim — blanket-False on a
        pre-v22 snapshot."""
        d = _minimal_dict(schema_version=21, from_headers=True, ast_producer="hybrid")
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is False

    def test_current_clang_header_snapshot_loads_as_reliable(self) -> None:
        d = _minimal_dict(schema_version=22, from_headers=True, ast_producer="clang")
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is True

    def test_current_hybrid_header_snapshot_loads_as_reliable(self) -> None:
        """The other half of the hybrid case: a v22 hybrid snapshot's
        clang-only appended functions carry real restrict facts, so it must
        load as reliable. Without this, a regression that left hybrid
        permanently unreliable would still pass the legacy test above
        (CodeRabbit review)."""
        d = _minimal_dict(schema_version=22, from_headers=True, ast_producer="hybrid")
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is True

    def test_legacy_castxml_header_snapshot_stays_reliable(self) -> None:
        """castxml's own ``_resolve_cv_restrict`` extraction predates this
        field entirely, so its values were never blanket-False."""
        d = _minimal_dict(schema_version=21, from_headers=True, ast_producer="castxml")
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is True

    def test_legacy_header_snapshot_predating_ast_producer_is_unreliable(
        self,
    ) -> None:
        """A snapshot persisted before ``ast_producer`` was tracked has an
        UNKNOWN producer, not a castxml one — so it must not be silently
        trusted (the same lesson ``clang_field_initializer_facts_reliable``
        records, spelled as ``== "castxml"`` rather than
        ``not in ("clang", "hybrid")``)."""
        d = _minimal_dict(schema_version=21, from_headers=True)
        assert "ast_producer" not in d
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is False

    def test_legacy_dwarf_only_snapshot_stays_reliable(self) -> None:
        """No header AST produced it, so the flag does not describe it. The
        detector's own header-tier gate is what keeps such a side out."""
        d = _minimal_dict(schema_version=21, from_headers=False)
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is True

    def test_missing_schema_version_key_on_clang_header_snapshot_is_legacy(
        self,
    ) -> None:
        d = _minimal_dict(from_headers=True, ast_producer="clang")
        assert "schema_version" not in d
        assert snapshot_from_dict(d).clang_restrict_facts_reliable is False

    def test_round_trip_preserves_reliable_true(self) -> None:
        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert snapshot_from_dict(j).clang_restrict_facts_reliable is True

    def test_reserialized_legacy_snapshot_stays_unreliable(self) -> None:
        legacy = snapshot_from_dict(
            _minimal_dict(schema_version=21, from_headers=True, ast_producer="clang")
        )
        assert legacy.clang_restrict_facts_reliable is False

        reserialized = snapshot_to_dict(legacy)
        assert reserialized["schema_version"] == SCHEMA_VERSION
        assert reserialized["clang_restrict_facts_reliable"] is False

        reloaded = snapshot_from_dict(reserialized)
        assert reloaded.clang_restrict_facts_reliable is False


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

    def test_malformed_scalar_fingerprints_default_gracefully(self) -> None:
        # Unlike profile_fields/scope_fields below, scalars degrade quietly.
        contract = {"profile_fingerprint": 123, "scope_fingerprint": None}
        restored = snapshot_from_dict(_minimal_dict(contract=contract)).contract
        assert restored is not None
        assert restored.profile_fingerprint is None
        assert restored.scope_fingerprint is None

    def test_malformed_profile_fields_rejects_the_load(self) -> None:
        # Present-but-wrong-shaped is corrupt, not "no evidence" (Codex).
        d = _minimal_dict(contract={"profile_fields": "not-a-dict"})
        with pytest.raises(TypeError):
            snapshot_from_dict(d)

    def test_contract_survives_file_io(self, tmp_path: Path) -> None:
        contract = ExtractionContract(
            profile_fingerprint="sha256:abc", scope_fingerprint="sha256:def"
        )
        snap = _make_snap(contract=contract)
        p = tmp_path / "snap.json"
        save_snapshot(snap, p)
        restored = load_snapshot(p)
        assert restored.contract == contract


# ── ADR-063 Phase 0: Fact[T] round-trip and legacy-schema backfill ─────────


class TestFactFieldRoundTrip:
    """A freshly-built snapshot's Fact[...] fields survive a real snapshot_to_dict()/json.dumps()/snapshot_from_dict() round-trip, and a pre-v26 (schema_version < 26) snapshot with no *_fact keys backfills correctly from the existing reliability flags — never Fact.present([])/Fact.present(False) for the unreliable/unsupported case, the exact confusion (a placeholder read as a confirmed fact) this phase exists to make unrepresentable."""

    def test_fresh_snapshot_round_trips_present_fact_and_is_json_serializable(self) -> None:
        rec = RecordType(
            name="Widget", kind="struct", vtable=["_ZN6WidgetD1Ev"], bases=["Base"]
        )
        param = Param(name="args", type="va_list", is_va_list=True)
        func = Function(name="f", mangled="_Z1fz", return_type="void", params=[param])
        # _round_trip's json.dumps() must not raise on a raw FactStatus enum.
        restored = _round_trip(_make_snap(types=[rec], functions=[func]))
        r = restored.types[0]
        assert r.vtable_fact.status is FactStatus.PRESENT
        assert r.vtable_fact.value == ["_ZN6WidgetD1Ev"]
        assert r.bases_fact.status is FactStatus.PRESENT
        assert r.bases_fact.value == ["Base"]

        p = restored.functions[0].params[0]
        assert p.is_va_list_fact.status is FactStatus.PRESENT
        assert p.is_va_list_fact.value is True

    def test_fresh_snapshot_confirmed_empty_survives_as_present_not_not_collected(self) -> None:
        rec = RecordType(name="Plain", kind="struct", vtable=[])
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert r.vtable_fact.status is FactStatus.PRESENT
        assert r.vtable_fact.value == []

    def test_explicit_not_collected_fact_survives_round_trip(self) -> None:
        rec = RecordType(
            name="Gapped", kind="struct", vtable_fact=Fact.not_collected("depth capped")
        )
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert r.vtable_fact.status is FactStatus.NOT_COLLECTED
        assert r.vtable_fact.diagnostics == ("depth capped",)
        assert r.vtable == []

    def test_legacy_snapshot_with_reliable_flag_backfills_present(self) -> None:
        d = _minimal_dict(
            schema_version=20,
            ast_producer="clang",
            from_headers=True,
            clang_vtable_facts_reliable=True,
            types=[{"name": "Foo", "kind": "struct", "vtable": ["_ZN3FooD1Ev"]}],
        )
        r = snapshot_from_dict(d).types[0]
        assert r.vtable_fact.status is FactStatus.PRESENT
        assert r.vtable_fact.value == ["_ZN3FooD1Ev"]

    def test_legacy_snapshot_with_unreliable_flag_backfills_not_collected(self) -> None:
        # Core backfill rule: an unreliable legacy vtable must NOT become
        # Fact.present([]) — that misreads "untrusted" as "confirmed empty".
        d = _minimal_dict(
            schema_version=20,
            ast_producer="clang",
            from_headers=True,
            clang_vtable_facts_reliable=False,
            types=[{"name": "Foo", "kind": "struct", "vtable": []}],
        )
        r = snapshot_from_dict(d).types[0]
        assert r.vtable_fact.status is FactStatus.NOT_COLLECTED
        assert r.vtable == []

    def test_legacy_snapshot_unreliable_va_list_backfills_not_collected(self) -> None:
        d = _minimal_dict(
            schema_version=20,
            ast_producer="clang",
            from_headers=True,
            clang_va_list_facts_reliable=False,
            functions=[_va_list_func(False)],
        )
        p = snapshot_from_dict(d).functions[0].params[0]
        assert p.is_va_list_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_castxml_snapshot_va_list_backfills_not_collected(self) -> None:
        # CastXML never determines va_list-ness (always a blanket False
        # placeholder); the clang-specific reliability flag reads True for
        # it anyway ("False is never wrong" != "this was collected").
        d = _minimal_dict(
            schema_version=20,
            ast_producer="castxml",
            from_headers=True,
            functions=[_va_list_func(False)],
        )
        p = snapshot_from_dict(d).functions[0].params[0]
        assert p.is_va_list_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_snapshot_bases_always_backfills_present_unconditionally(self) -> None:
        # bases/virtual_bases have no reliability flag (AGENTS.md's
        # type_base_changed entry) — always backfills to Fact.present(raw).
        d = _minimal_dict(schema_version=20, types=[{"name": "Foo", "kind": "struct", "bases": ["Base"]}])
        r = snapshot_from_dict(d).types[0]
        assert r.bases_fact.status is FactStatus.PRESENT
        assert r.bases_fact.value == ["Base"]

    def test_current_schema_missing_fact_key_is_not_collected_not_present(self) -> None:
        # A truncated/hand-authored v26+ document omitting a *_fact key must not read as confirmed just because the legacy field defaults to one — v26+ already commits to serializing the sibling, so a missing key means the fact was never populated (Codex review).
        d = _minimal_dict(
            schema_version=26,
            types=[{"name": "Foo", "kind": "struct", "bases": ["Base"]}],
            functions=[_va_list_func(True)],
        )
        snap = snapshot_from_dict(d)
        assert snap.types[0].bases_fact.status is FactStatus.NOT_COLLECTED
        assert snap.types[0].bases == []
        assert snap.functions[0].params[0].is_va_list_fact.status is FactStatus.NOT_COLLECTED

    def test_snapshot_to_dict_encodes_status_as_plain_string(self) -> None:
        rec = RecordType(name="Foo", kind="struct", vtable_fact=Fact.present(["m"]))
        assert snapshot_to_dict(_make_snap(types=[rec]))["types"][0]["vtable_fact"]["status"] == "present"

    def test_schema_version_is_26_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 26

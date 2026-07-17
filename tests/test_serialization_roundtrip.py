"""Unit tests for AbiSnapshot JSON round-trip — elf_only_mode and constants.

Covers serialisation fields added in PR #63:
  - elf_only_mode
  - constants
"""
from __future__ import annotations

import json

from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
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


class TestHeaderCvFactsReliableRoundTrip:
    """AbiSnapshot.header_cv_facts_reliable must be derived from
    schema_version on load, not merely round-tripped as an opaque key
    (Codex review, PR #582).

    A pre-v9 persisted snapshot's TypeField.is_const/is_volatile/is_mutable
    are permanently False and its type spelling never carried a cv
    qualifier — real (not absent) data indistinguishable from a genuine
    "not const" fact by value alone. Only the schema_version boundary can
    tell a legacy snapshot's data apart from a snapshot written by the
    fixed parser.
    """

    def test_fresh_in_memory_snapshot_defaults_reliable(self) -> None:
        snap = _make_snap()
        assert snap.header_cv_facts_reliable is True

    def test_fresh_dump_serializes_current_schema_version(self) -> None:
        from abicheck.serialization import SCHEMA_VERSION

        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert j["schema_version"] == SCHEMA_VERSION == 9

    def test_legacy_schema_version_8_loads_as_unreliable(self) -> None:
        d = _minimal_dict(schema_version=8)
        restored = snapshot_from_dict(d)
        assert restored.header_cv_facts_reliable is False

    def test_current_schema_version_9_loads_as_reliable(self) -> None:
        d = _minimal_dict(schema_version=9)
        restored = snapshot_from_dict(d)
        assert restored.header_cv_facts_reliable is True

    def test_missing_schema_version_key_treated_as_legacy(self) -> None:
        """A snapshot with no schema_version key at all predates even the
        original schema-versioning PR (#89) — necessarily older than the
        CV-fact fix too."""
        d = _minimal_dict()
        assert "schema_version" not in d
        assert snapshot_from_dict(d).header_cv_facts_reliable is False

    def test_round_trip_preserves_reliable_true(self) -> None:
        snap = _make_snap()
        j = json.loads(snapshot_to_json(snap))
        assert snapshot_from_dict(j).header_cv_facts_reliable is True


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
        snap = _make_snap(functions=[self._func(is_deleted=True, deleted_from_dwarf=True)])
        j = json.loads(snapshot_to_json(snap))
        assert j["functions"][0]["deleted_from_dwarf"] is True
        restored = snapshot_from_dict(j)
        assert restored.functions[0].deleted_from_dwarf is True
        assert restored.functions[0].is_deleted is True

    def test_false_survives_roundtrip(self) -> None:
        snap = _make_snap(functions=[self._func(is_deleted=True, deleted_from_dwarf=False)])
        restored = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
        assert restored.functions[0].deleted_from_dwarf is False

    def test_defaults_to_false_when_absent(self) -> None:
        """Legacy snapshots without the key deserialise to False."""
        d = _minimal_dict(functions=[{"name": "f", "mangled": "f", "return_type": "void"}])
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

    def test_elf_only_mode_and_constants_survive_file_io(self, tmp_path: object) -> None:
        snap = _make_snap(elf_only_mode=True, constants={"FOO": "bar"})
        p = tmp_path / "snap.json"  # type: ignore[operator]
        save_snapshot(snap, p)
        restored = load_snapshot(p)
        assert restored.elf_only_mode is True
        assert restored.constants == {"FOO": "bar"}

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

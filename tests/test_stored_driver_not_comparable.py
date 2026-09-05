# Copyright (c) 2026 abicheck contributors
# SPDX-License-Identifier: Apache-2.0
"""ADR-050 D2 per member on the stored-baseline drivers, and D7 for a
zero-overlap stored pair (Codex review, thirtieth round).

Both stored drivers diff every matched member through one
``compare_snapshots()`` chokepoint. A ``ProfileMismatchError``/
``ScopeMismatchError`` from it used to escape to the dispatcher's global
refusal, discarding every sibling's completed comparison and the scope
record -- unlike the native fan-out, which records that one library
``not_comparable`` and continues. The invariants:

* a not-comparable member is `failed` on the record with a ``not
  comparable`` reason, the siblings' results survive, and the run still
  exits 16 (ranked above ``ERROR``) with the operational axis saying why;
* a zero-overlap pair with a scope record renders its ``comparison_scope``
  (proven removals/additions under asserted inventories included) and
  exits 1 through D7 instead of a usage error that discards the record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_release_scope_completeness import _elf_snap

from abicheck.bundle_facts import capture_bundle_facts
from abicheck.model import AbiSnapshot
from abicheck.model.scope_acquisition import AcquisitionState
from abicheck.serialization import save_bundle_facts, save_snapshot


def _scoped(name: str, scope: str) -> AbiSnapshot:
    snap = _elf_snap(name)
    snap.from_headers = True
    snap.dependency_scope = scope
    return snap


def _invoke(*args: str) -> tuple[int, str]:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


class TestNotComparableMemberIsRecordedNotRaised:
    """OLD: libok.so (comparable) + libmix.so (scope `filtered`); NEW:
    libok.so + libmix.so (scope `full`). The mismatch hits the second
    member after the first completed."""

    def _write_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        old = tmp_path / "old.bundlefacts.json"
        new = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(
            capture_bundle_facts(
                {
                    "libok.so": _elf_snap("libok.so"),
                    "libmix.so": _scoped("libmix.so", "filtered"),
                }
            ),
            old,
        )
        save_bundle_facts(
            capture_bundle_facts(
                {
                    "libok.so": _elf_snap("libok.so"),
                    "libmix.so": _scoped("libmix.so", "full"),
                }
            ),
            new,
        )
        return old, new

    def test_stored_pair_keeps_the_sibling(self, tmp_path: Path) -> None:
        from abicheck.workflows.bundle_stored_pair_compare import (
            compare_stored_bundle_facts_pair,
        )

        old, new = self._write_pair(tmp_path)
        result = compare_stored_bundle_facts_pair(old, new)
        assert [d.library for d in result.per_library] == ["libok.so"]
        kind, message = result.not_comparable_members["libmix.so"]
        assert kind == "scope_mismatch" and message
        record = result.scope_record
        assert record is not None
        by_key = {m.member: m for m in record.members}
        assert by_key["libok.so"].state is AcquisitionState.AVAILABLE
        assert by_key["libmix.so"].state is AcquisitionState.FAILED
        assert by_key["libmix.so"].reason.startswith("not comparable: ")
        assert record.is_incomplete and not record.no_comparison_completed

    def test_stored_live_keeps_the_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.package as package
        from abicheck.bundle_side_input import compare_release_against_bundle_facts

        monkeypatch.setattr(
            package,
            "discover_shared_libraries",
            lambda d, include_private=False: sorted(Path(d).glob("*.json")),
        )
        old, _ = self._write_pair(tmp_path)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        save_snapshot(_elf_snap("libok.so"), new_dir / "libok.so.json")
        save_snapshot(_scoped("libmix.so", "full"), new_dir / "libmix.so.json")
        result = compare_release_against_bundle_facts(old, new_dir)
        assert [d.library for d in result.per_library] == ["libok.so"]
        assert set(result.not_comparable_members) == {"libmix.so"}
        record = result.scope_record
        assert record is not None
        by_key = {m.member: m for m in record.members}
        assert by_key["libok.so"].state is AcquisitionState.AVAILABLE
        assert by_key["libmix.so"].state is AcquisitionState.FAILED

    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_cli_exits_16_with_the_sibling_rendered(
        self, tmp_path: Path, policy: str
    ) -> None:
        """Exit 16 outranks the completeness policy and `ERROR`, as in the
        fan-out; the document still carries the sibling and the record."""
        old, new = self._write_pair(tmp_path)
        code, out = _invoke(
            "compare",
            str(old),
            str(new),
            "--format",
            "json",
            "--on-incomplete-scope",
            policy,
        )
        assert code == 16, out
        doc = json.loads(out[out.index("{") :])
        assert doc["verdict"] == "not_comparable"
        assert doc["run_outcome"]["operational"] == "not_comparable"
        assert doc["run_outcome"]["compatibility"] is None
        assert doc["run_outcome"]["scope"] == "incomplete"
        assert list(doc["libraries"]) == ["libok.so"]
        assert doc["not_comparable_members"]["libmix.so"]["kind"] == "scope_mismatch"
        members = {m["member"]: m for m in doc["comparison_scope"]["members"]}
        assert members["libmix.so"]["state"] == "failed"
        assert any("not comparable" in e for e in doc["analysis_errors"])


class TestZeroOverlapPairRendersItsScope:
    """Two asserted-complete captures with no key in common (a wholesale
    rename): every OLD member is a proven removal, every NEW member a
    proven addition, nothing was compared -- D7 exits 1 and the scope
    section is rendered, not discarded as a usage error."""

    def test_json(self, tmp_path: Path) -> None:
        old = tmp_path / "old.bundlefacts.json"
        new = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(
            capture_bundle_facts(
                {"libold.so": _elf_snap("libold.so")}, inventory_complete=True
            ),
            old,
        )
        save_bundle_facts(
            capture_bundle_facts(
                {"libnew.so": _elf_snap("libnew.so")}, inventory_complete=True
            ),
            new,
        )
        code, out = _invoke("compare", str(old), str(new), "--format", "json")
        assert code == 1, out
        doc = json.loads(out[out.index("{") :])
        scope = doc["comparison_scope"]
        assert scope["no_comparison_completed"] is True
        assert scope["proven_removed"] == ["libold.so"]
        assert scope["proven_added"] == ["libnew.so"]
        assert doc["run_outcome"]["operational"] == "no_comparison_completed"
        assert doc["libraries"] == {}

    def test_markdown_warns_and_exits_1(self, tmp_path: Path) -> None:
        old = tmp_path / "old.bundlefacts.json"
        new = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(
            capture_bundle_facts({"libold.so": _elf_snap("libold.so")}), old
        )
        save_bundle_facts(
            capture_bundle_facts({"libnew.so": _elf_snap("libnew.so")}), new
        )
        code, out = _invoke("compare", str(old), str(new), "--format", "markdown")
        assert code == 1, out
        assert "no comparison completed" in out

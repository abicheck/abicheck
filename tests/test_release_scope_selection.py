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

"""ADR-065 S2, third file: what a *selection* may and may not conclude.

The release manifest is enforced over the retained bundle members only
(D2), `--dso-only` over a stored side records an unclassifiable member as
`failed` rather than narrowing the scope (D1/D2), and a `failed` member is
never a proven removal/addition however complete the other side's
inventory is. Split out of ``tests/test_release_scope_bundle.py`` once that
file crossed the architecture gate's 1200-line test-file cap; shares that
file's helpers rather than copying them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_release_scope_bundle import _lib, _removal_findings
from test_release_scope_completeness import (
    _facts_file,
    _invoke_json,
    _write,
    _write_stored_package,
)

from abicheck.bundle_manifest import InstantiationManifest
from abicheck.model import AbiSnapshot
from abicheck.model.scope_acquisition import (
    UNCHECKED_STATES,
    AcquisitionState,
    InventoryCompleteness,
    ScopeAcquisitionRecord,
    SideInventory,
)
from abicheck.workflows.release_scope import (
    build_release_scope_record,
    build_stored_baseline_scope_record,
    bundle_analysis_members,
)

# ---------------------------------------------------------------------------
# The release manifest is enforced over the retained members only (D2)
# ---------------------------------------------------------------------------


def _manifest(*entries: tuple[str, str | None]) -> InstantiationManifest:
    """``(symbol, pinned_provider_or_None)`` entries."""
    from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry

    return InstantiationManifest(
        entries=tuple(
            ManifestEntry(symbol=sym, library=lib, optional_provider=lib is None)
            for sym, lib in entries
        )
    )


def _write_stored_package_with_manifest(
    root: Path, libraries: dict[str, AbiSnapshot], manifest: InstantiationManifest
) -> None:
    from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
    from abicheck.bundle_facts_store import write_bundle_facts_package
    from abicheck.project_snapshot_store import (
        DirectoryObjectStore,
        write_project_manifest,
    )

    facts = capture_bundle_facts(libraries, variant_fingerprint="gcc13")
    facts = BundleFacts(
        variant_fingerprint=facts.variant_fingerprint,
        per_library_snapshots=facts.per_library_snapshots,
        library_filenames={name: name for name in facts.per_library_snapshots},
        manifest=manifest,
    )
    store = DirectoryObjectStore(root)
    write_project_manifest(root, write_bundle_facts_package(facts, store=store))


def _manifest_findings(doc: dict[str, object]) -> list[str]:
    findings = doc.get("bundle_findings") or []
    assert isinstance(findings, list)
    return [
        str(f["symbol"])
        for f in findings
        if isinstance(f, dict)
        and str(f.get("kind", "")).startswith("bundle_manifest_instantiation_")
    ]


class TestManifestScopedToRetainedMembers:
    """`scope_manifest_to_members`: once any expected member is absent from
    the bundle graph, only a promise pinned to a retained provider stays
    decidable; every other promise is withheld and named (Codex review,
    eighth round)."""

    @staticmethod
    def _record(
        other: AcquisitionState, *, new_proven: bool = False
    ) -> ScopeAcquisitionRecord:
        from abicheck.model.scope_acquisition import MemberAcquisition

        return ScopeAcquisitionRecord(
            (
                MemberAcquisition("libkept.so", AcquisitionState.AVAILABLE, True, True),
                MemberAcquisition(
                    "libother.so",
                    other,
                    other is not AcquisitionState.EXPECTED_NOT_PRODUCED,
                    other is not AcquisitionState.NOT_SUPPLIED,
                    "why",
                    display_name="libother.so.2",
                ),
            ),
            SideInventory(InventoryCompleteness.UNPROVEN, "t"),
            SideInventory(
                InventoryCompleteness.PROVEN
                if new_proven
                else InventoryCompleteness.UNPROVEN,
                "t",
            ),
            "all_expected",
        )

    @pytest.mark.parametrize("new_proven", [False, True])
    @pytest.mark.parametrize("other", list(AcquisitionState))
    def test_withheld_iff_a_member_is_absent_from_the_graph(
        self, other: AcquisitionState, new_proven: bool
    ) -> None:
        from abicheck.workflows.release_scope import scope_manifest_to_members

        record = self._record(other, new_proven=new_proven)
        manifest = _manifest(
            ("any_fn", None),
            ("kept_fn", "libkept.so"),
            ("kept_versioned_fn", "libkept.so.3"),
            ("other_fn", "libother.so.2"),
        )
        scoped, note = scope_manifest_to_members(manifest, record)
        graph_complete = bundle_analysis_members(record) == {
            "libkept.so",
            "libother.so",
        }
        if graph_complete:
            assert scoped is manifest and note is None
            return
        assert scoped is not None
        assert [e.symbol for e in scoped.entries] == ["kept_fn", "kept_versioned_fn"]
        assert note is not None
        assert "any_fn" in note and "other_fn" in note and "libother.so.2" in note
        # Nothing pinned to a retained member: the manifest is withheld whole.
        scoped_none, note_none = scope_manifest_to_members(
            _manifest(("any_fn", None), ("other_fn", "libother.so")), record
        )
        assert scoped_none is None and note_none is not None

    def test_no_manifest_or_no_record_is_the_identity(self) -> None:
        from abicheck.workflows.release_scope import scope_manifest_to_members

        manifest = _manifest(("any_fn", None))
        assert scope_manifest_to_members(
            None, self._record(AcquisitionState.FAILED)
        ) == (None, None)
        assert scope_manifest_to_members(manifest, None) == (manifest, None)

    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_narrowed_live_new_does_not_fabricate_manifest_drift(
        self, tmp_path: Path, policy: str
    ) -> None:
        """A stored OLD package with an embedded manifest against NEW named
        as one current artifact (D9 narrowing): the promise the unselected
        member provides is withheld, not `BUNDLE_MANIFEST_INSTANTIATION_REMOVED`."""
        old = tmp_path / "old_pkg"
        _write_stored_package_with_manifest(
            old,
            {
                "libcore.so": _lib("libcore.so", exports=("core_fn",)),
                "libalgo.so": _lib("libalgo.so", exports=("algo_fn",)),
            },
            _manifest(("core_fn", None)),
        )
        new = tmp_path / "new"
        _write(new, "libalgo.so.json", _lib("libalgo.so", exports=("algo_fn",)))
        code, doc = _invoke_json(
            "compare",
            str(old),
            str(new / "libalgo.so.json"),
            "-j",
            "1",
            "--on-incomplete-scope",
            policy,
        )
        assert _manifest_findings(doc) == []
        assert doc["verdict"] != "BREAKING"
        errors = doc.get("bundle_analysis_errors") or []
        assert any("core_fn" in e and "withheld" in e for e in errors), errors
        # D9 narrowing is a deliberate selection: complete under either policy.
        assert doc["comparison_scope"]["completeness"] == "complete"
        assert doc["comparison_scope"]["out_of_scope"] == ["libcore.so-libcore.so"]
        assert code == 0

    def test_proven_new_inventory_still_enforces_the_manifest(
        self, tmp_path: Path
    ) -> None:
        """Control: with NEW a proven-complete stored package that dropped
        the provider, the removal is proven and the promise is enforced."""
        old = tmp_path / "old_pkg"
        libs = {
            "libcore.so": _lib("libcore.so", exports=("core_fn",)),
            "libalgo.so": _lib("libalgo.so", exports=("algo_fn",)),
        }
        _write_stored_package_with_manifest(old, libs, _manifest(("core_fn", None)))
        new = tmp_path / "new_pkg"
        _write_stored_package(new, {"libalgo.so": libs["libalgo.so"]})
        code, doc = _invoke_json("compare", str(old), str(new), "-j", "1")
        assert "core_fn" in _manifest_findings(doc)
        assert doc["comparison_scope"]["completeness"] == "complete"
        assert not doc.get("bundle_analysis_errors")

    def test_stored_pair_driver_withholds_too(self, tmp_path: Path) -> None:
        """The stored/stored driver: OLD's captured manifest promises a
        symbol only the member degraded on NEW provides."""
        from abicheck.workflows.bundle_stored_pair_compare import (
            compare_stored_bundle_facts_pair,
        )

        libs = {
            "libcore.so": _lib("libcore.so", exports=("core_fn",)),
            "libalgo.so": _lib("libalgo.so", exports=("algo_fn",)),
        }
        old = _facts_file(
            tmp_path,
            "old.bundlefacts.json",
            libs,
            manifest=_manifest(("core_fn", None)),
        )
        new = _facts_file(
            tmp_path,
            "new.bundlefacts.json",
            {**libs, "libcore.so": _lib("libcore.so")},
            degraded={"libcore.so": "dump failed"},
        )
        result = compare_stored_bundle_facts_pair(old, new)
        kinds = {f.kind.value for f in result.bundle_findings}
        assert not any(k.startswith("bundle_manifest_instantiation_") for k in kinds)
        assert any("core_fn" in e and "withheld" in e for e in result.analysis_errors)


# ---------------------------------------------------------------------------
# --dso-only over a stored side: unclassifiable is failed, never narrowed (D1/D2)
# ---------------------------------------------------------------------------


class TestDsoOnlyUnclassifiedIsFailed:
    """`--dso-only` excludes a stored member whose kind or ELF metadata it
    cannot read. That exclusion is an acquisition failure: recorded
    `failed`, and the side's inventory proof withheld, so the other side's
    copy is never a proven removal/addition (Codex review, ninth round)."""

    @staticmethod
    def _libs() -> dict[str, AbiSnapshot]:
        from dataclasses import replace

        return {
            "libdso.so": _lib("libdso.so", exports=("dso_fn",)),
            # Declared ELF (no platform stated) but carrying no ELF section.
            "libnoelf.so": AbiSnapshot(library="libnoelf.so", version="1"),
            # Confirmed not a DSO: silently outside the selection.
            "libwin.dll": replace(
                AbiSnapshot(library="libwin.dll", version="1"), platform="pe"
            ),
        }

    def test_classification_names_the_unreadable_member(self, tmp_path: Path) -> None:
        from abicheck.workflows.release_package import (
            classify_dso_only_package_map,
            dso_only_package_map,
            resolve_release_package_map,
        )

        pkg = tmp_path / "pkg"
        _write_stored_package(pkg, self._libs())
        resolved = resolve_release_package_map(
            pkg, variant_id=None, dest_root=tmp_path / "resolved"
        )
        assert len(resolved) == 3
        cls = classify_dso_only_package_map(resolved)
        assert set(cls.members) == {"libdso.so"}
        assert set(cls.unclassified) == {"libnoelf.so"}
        assert "ELF metadata" in cls.unclassified["libnoelf.so"]
        assert dso_only_package_map(resolved) == cls.members

    @pytest.mark.parametrize("side", ["old", "new"])
    def test_record_marks_it_failed_and_withholds_the_proof(self, side: str) -> None:
        from abicheck.workflows.release_scope import release_inventory_evidence

        unclassified = {"libx.so": "--dso-only could not read it"}
        evidence = release_inventory_evidence(
            old_stored=True,
            new_stored=True,
            old_unclassified=unclassified if side == "old" else None,
            new_unclassified=unclassified if side == "new" else None,
        )
        lacking = evidence.old if side == "old" else evidence.new
        proven = evidence.new if side == "old" else evidence.old
        assert lacking.completeness is InventoryCompleteness.UNPROVEN
        assert "libx.so" in lacking.provenance
        assert proven.completeness is InventoryCompleteness.PROVEN
        # libx.so classified fine on the other side, libok.so on both.
        maps = {"libok.so": Path("libok.so"), "libx.so": Path("libx.so")}
        record = build_release_scope_record(
            {"libok.so": maps["libok.so"]} if side == "old" else maps,
            maps if side == "old" else {"libok.so": maps["libok.so"]},
            ["libok.so"],
            [{"library": "libok.so", "verdict": "NO_CHANGE"}],
            evidence,
            old_failed=unclassified if side == "old" else None,
            new_failed=unclassified if side == "new" else None,
        )
        by_key = {m.member: m for m in record.members}
        assert by_key["libx.so"].state is AcquisitionState.FAILED
        assert (by_key["libx.so"].old_present, by_key["libx.so"].new_present) == (
            True,
            True,
        )
        assert side.upper() in by_key["libx.so"].reason
        assert record.proven_removed_members == ()
        assert record.proven_added_members == ()
        assert [m.member for m in record.unchecked_members] == ["libx.so"]
        assert by_key["libok.so"].state is AcquisitionState.AVAILABLE

    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_stored_pair_dso_only_never_fabricates_a_removal(
        self, tmp_path: Path, policy: str
    ) -> None:
        libs = self._libs()
        old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
        _write_stored_package(
            old, {**libs, "libnoelf.so": _lib("libnoelf.so", exports=("x",))}
        )
        _write_stored_package(new, libs)
        code, doc = _invoke_json(
            "compare",
            str(old),
            str(new),
            "-j",
            "1",
            "--dso-only",
            "--fail-on-removed-library",
            "--on-incomplete-scope",
            policy,
        )
        scope = doc["comparison_scope"]
        assert scope["new_inventory"]["completeness"] == "unproven"
        assert "libnoelf.so" in scope["new_inventory"]["provenance"]
        assert scope["old_inventory"]["completeness"] == "proven"
        assert scope["proven_removed"] == []
        assert scope["counts"]["failed"] == 1
        assert [n.split("-")[0] for n in scope["unchecked"]] == ["libnoelf.so"]
        assert scope["completeness"] == "incomplete"
        assert _removal_findings(doc) == []
        by_name = {lib["library"].split("-")[0]: lib for lib in doc["libraries"]}
        assert by_name["libnoelf.so"]["verdict"] == "failed"
        assert by_name["libdso.so"]["verdict"] == "NO_CHANGE"
        assert "libwin.dll" not in by_name
        assert code == (1 if policy == "block" else 0)

    def test_fully_classified_stored_pair_stays_proven(self, tmp_path: Path) -> None:
        """Control: every declared member classifies, so the proof stands
        and a DSO the proven NEW side dropped is a real removal."""
        old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
        libs = {k: v for k, v in self._libs().items() if k != "libnoelf.so"}
        _write_stored_package(
            old, {**libs, "libgone.so": _lib("libgone.so", exports=("g",))}
        )
        _write_stored_package(new, libs)
        code, doc = _invoke_json(
            "compare",
            str(old),
            str(new),
            "-j",
            "1",
            "--dso-only",
            "--fail-on-removed-library",
        )
        scope = doc["comparison_scope"]
        assert scope["new_inventory"]["completeness"] == "proven"
        assert [n.split("-")[0] for n in scope["proven_removed"]] == ["libgone.so"]
        assert scope["completeness"] == "complete"
        assert code == 8


class TestFailedMemberIsNeverAProvenRemoval:
    """`proven_removed_members`/`proven_added_members` require the
    `not_supplied` state: a `failed` member is present on its side but its
    acquisition never established what the artifact was, so however
    complete the other side's inventory is, it is unchecked, never a
    removal/addition (Codex review, tenth round)."""

    @pytest.mark.parametrize("state", list(AcquisitionState))
    @pytest.mark.parametrize("side", ["old", "new"])
    def test_only_not_supplied_qualifies(
        self, state: AcquisitionState, side: str
    ) -> None:
        from abicheck.model.scope_acquisition import MemberAcquisition

        member = MemberAcquisition(
            "libx.so", state, old_present=side == "old", new_present=side == "new"
        )
        record = ScopeAcquisitionRecord(
            (member,),
            SideInventory(InventoryCompleteness.PROVEN, "t"),
            SideInventory(InventoryCompleteness.PROVEN, "t"),
            "all_expected",
        )
        proven = (
            record.proven_removed_members
            if side == "old"
            else record.proven_added_members
        )
        assert bool(proven) is (state is AcquisitionState.NOT_SUPPLIED)
        if state in UNCHECKED_STATES and state is not AcquisitionState.NOT_SUPPLIED:
            assert record.unchecked_members == (member,)
            assert record.is_incomplete

    def test_dso_only_unclassified_old_member_absent_from_proven_new(
        self, tmp_path: Path
    ) -> None:
        """OLD cannot classify libnoelf.so; the proven-complete NEW package
        does not ship it at all: `failed`, unchecked, never exit 8."""
        libs = {"libdso.so": _lib("libdso.so", exports=("dso_fn",))}
        old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
        _write_stored_package(
            old,
            {**libs, "libnoelf.so": AbiSnapshot(library="libnoelf.so", version="1")},
        )
        _write_stored_package(new, libs)
        code, doc = _invoke_json(
            "compare",
            str(old),
            str(new),
            "-j",
            "1",
            "--dso-only",
            "--fail-on-removed-library",
        )
        scope = doc["comparison_scope"]
        assert scope["new_inventory"]["completeness"] == "proven"
        assert scope["old_inventory"]["completeness"] == "unproven"
        assert scope["proven_removed"] == []
        assert [n.split("-")[0] for n in scope["unchecked"]] == ["libnoelf.so"]
        assert scope["counts"]["failed"] == 1
        assert _removal_findings(doc) == []
        assert code == 0


class TestExplicitManifestNeverFallsBackToTheStoredOne:
    """An explicit `--manifest` replaces a stored side's captured manifest
    entirely. Once scoping withholds every explicit promise, the comparison
    must enforce nothing -- not the stored manifest `compare_bundle_from_
    facts`'s own fallback would otherwise pick up (CodeRabbit review)."""

    @staticmethod
    def _explicit(tmp_path: Path) -> Path:
        # Optional-provider promise: withheld whole once a member is absent.
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"provides": [{"symbol": "core_fn"}]}))
        return path

    @staticmethod
    def _libs() -> dict[str, AbiSnapshot]:
        return {
            "libcore.so": _lib("libcore.so", exports=("core_fn",)),
            "libalgo.so": _lib("libalgo.so", exports=("algo_fn",)),
        }

    # The stored manifest pins a promise NEW's retained member no longer
    # provides -- enforced, it would fire; replaced by the explicit one, it
    # must not.
    _STORED = (("gone_fn", "libalgo.so"),)

    def test_stored_pair_driver(self, tmp_path: Path) -> None:
        from abicheck.workflows.bundle_stored_pair_compare import (
            compare_stored_bundle_facts_pair,
        )

        libs = self._libs()
        old = _facts_file(
            tmp_path, "old.bundlefacts.json", libs, manifest=_manifest(*self._STORED)
        )
        new = _facts_file(
            tmp_path,
            "new.bundlefacts.json",
            {**libs, "libcore.so": _lib("libcore.so")},
            degraded={"libcore.so": "dump failed"},
        )
        result = compare_stored_bundle_facts_pair(
            old, new, manifest_path=self._explicit(tmp_path)
        )
        kinds = {f.kind.value for f in result.bundle_findings}
        assert not any(k.startswith("bundle_manifest_instantiation_") for k in kinds)
        assert any("core_fn" in e and "withheld" in e for e in result.analysis_errors)

    def test_stored_live_driver(self, tmp_path: Path) -> None:
        from abicheck.bundle_side_input import compare_release_against_bundle_facts

        libs = self._libs()
        old = _facts_file(
            tmp_path, "old.bundlefacts.json", libs, manifest=_manifest(*self._STORED)
        )
        new = tmp_path / "new"
        _write(new, "libalgo.so.json", _lib("libalgo.so", exports=("algo_fn",)))
        result = compare_release_against_bundle_facts(
            old, new / "libalgo.so.json", manifest_path=self._explicit(tmp_path)
        )
        kinds = {f.kind.value for f in result.bundle_findings}
        assert not any(k.startswith("bundle_manifest_instantiation_") for k in kinds)
        assert any("core_fn" in e and "withheld" in e for e in result.analysis_errors)


class TestDegradedMarkerIsValidatedBeforeAnyWrite:
    def test_a_rejected_import_leaves_no_orphaned_object(self) -> None:
        """`ObjectStore` has no rollback: the marker gate must run before
        the per-library snapshot imports, or a rejected document leaves
        unreferenced objects behind (CodeRabbit review)."""
        from abicheck.bundle_facts import capture_bundle_facts
        from abicheck.bundle_facts_serialization import bundle_facts_to_dict
        from abicheck.serialization import SCHEMA_VERSION
        from abicheck.storage.import_bundle_facts import import_bundle_facts
        from abicheck.storage.package import InMemoryObjectStore

        doc = dict(
            bundle_facts_to_dict(
                capture_bundle_facts(
                    {"liba.so": AbiSnapshot(library="liba.so", version="")},
                    degraded_members={"liba.so": "ELF-only: boom"},
                )
            )
        )
        doc["schema_version"] = 2
        store = InMemoryObjectStore()
        with pytest.raises(ValueError, match="degraded_members"):
            import_bundle_facts(
                doc, store=store, max_known_schema_version=SCHEMA_VERSION
            )
        assert store._objects == {}


class TestDegradedSingleArtifactPackageRoutesToTheFanOut:
    """A one-member `ProjectSnapshot` package whose sole member was captured
    degraded is not a scalar "file" operand: only the scope-aware fan-out
    reads the marker and records the member `failed`, where the
    single-artifact reader would compare the ELF-only stand-in as complete
    evidence and manufacture removals (Codex review, twelfth round)."""

    @staticmethod
    def _packages(tmp_path: Path) -> tuple[Path, Path]:
        healthy = _lib("libfoo.so", exports=("foo", "bar"))
        old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
        _write_stored_package(old, {"libfoo.so": healthy})
        _write_stored_package(
            new,
            {"libfoo.so": _lib("libfoo.so")},
            degraded={"libfoo.so": "dump failed: boom"},
        )
        return old, new

    def test_classification(self, tmp_path: Path) -> None:
        from abicheck.cli_resolve import classify_compare_operand
        from abicheck.workflows.release_package import is_multi_artifact_package

        old, new = self._packages(tmp_path)
        assert is_multi_artifact_package(old) is False
        assert is_multi_artifact_package(new) is True
        assert classify_compare_operand(old) == "file"
        assert classify_compare_operand(new) == "directory"

    @pytest.mark.parametrize("degraded_side", ["old", "new"])
    @pytest.mark.parametrize("policy", ["warn", "block"])
    def test_compare_records_the_member_failed(
        self, tmp_path: Path, degraded_side: str, policy: str
    ) -> None:
        old, new = self._packages(tmp_path)
        if degraded_side == "old":
            old, new = new, old
        code, doc = _invoke_json(
            "compare", str(old), str(new), "-j", "1", "--on-incomplete-scope", policy
        )
        assert "func_removed" not in json.dumps(doc)
        assert doc["verdict"] != "BREAKING"
        scope = doc["comparison_scope"]
        assert scope["counts"]["failed"] == 1
        assert scope["no_comparison_completed"] is True
        assert doc["run_outcome"]["operational"] == "no_comparison_completed"
        assert doc["run_outcome"]["compatibility"] is None
        assert code == 1


class TestDamagedMarkerSectionFailsClosed:
    """A single-artifact package whose composition section is present but
    unreadable is not "no marker" (Codex review, thirteenth round): it
    routes to the fan-out, and the fan-out refuses to compare it."""

    @staticmethod
    def _corrupt_composition(pkg: Path) -> None:
        from abicheck.project_snapshot_store import (
            DirectoryObjectStore,
            read_manifest_summary,
            read_variant_ref,
        )
        from abicheck.storage.dto import BUNDLE_COMPOSITION_SECTION_KIND

        (variant_id,) = read_manifest_summary(pkg).variant_ids
        ref = read_variant_ref(pkg, variant_id).sections[
            BUNDLE_COMPOSITION_SECTION_KIND
        ]
        DirectoryObjectStore(pkg)._json_path(ref.digest).write_bytes(b"garbage")

    def test_routes_to_the_fan_out_and_is_refused(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.cli_resolve import classify_compare_operand
        from abicheck.workflows.release_package import is_multi_artifact_package

        healthy = {"libfoo.so": _lib("libfoo.so", exports=("foo", "bar"))}
        old, new = tmp_path / "old_pkg", tmp_path / "new_pkg"
        _write_stored_package(old, healthy)
        _write_stored_package(new, healthy)
        self._corrupt_composition(new)
        assert is_multi_artifact_package(new) is True
        assert classify_compare_operand(new) == "directory"
        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "-j", "1", "--format", "json"]
        )
        # Refused as a usage error by whichever reader meets the damage
        # first (materialization or the marker read), never compared.
        assert result.exit_code == 64, result.output
        assert "refusing" in result.output
        assert "BREAKING" not in result.output


class TestDirectBundleApiHonorsDegradation:
    """`bundle_snapshot_from_facts` (and so `compare_bundle_from_facts` and
    `compare_bundle_sides`) refuses facts carrying a degraded marker: the
    stand-in is not evidence, and a direct caller must resolve the scope
    first as the drivers do (Codex review, thirteenth round)."""

    @staticmethod
    def _facts():
        from abicheck.bundle_facts import capture_bundle_facts

        return capture_bundle_facts(
            {"libcore.so": _lib("libcore.so"), "libalgo.so": _lib("libalgo.so")},
            degraded_members={"libcore.so": "dump failed"},
        )

    def test_compare_bundle_from_facts_refuses(self) -> None:
        from abicheck.bundle_facts import (
            bundle_snapshot_from_facts,
            capture_bundle_facts,
            compare_bundle_from_facts,
        )

        new_snapshot = bundle_snapshot_from_facts(
            capture_bundle_facts({"libalgo.so": _lib("libalgo.so")})
        )
        with pytest.raises(ValueError, match="libcore.so.*ADR-065 D8"):
            compare_bundle_from_facts(self._facts(), new_snapshot, [])

    def test_compare_bundle_sides_refuses(self, tmp_path: Path) -> None:
        from abicheck.bundle_side_input import (
            StoredBundleFactsInput,
            compare_bundle_sides,
        )
        from abicheck.serialization import save_bundle_facts

        path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(self._facts(), path)
        with pytest.raises(ValueError, match="degraded"):
            compare_bundle_sides(
                StoredBundleFactsInput(path), StoredBundleFactsInput(path), []
            )

    def test_a_resolved_scope_passes(self) -> None:
        from abicheck.bundle_facts import bundle_snapshot_from_facts
        from abicheck.workflows.release_scope import restrict_bundle_facts

        facts = self._facts()
        record = build_stored_baseline_scope_record(
            facts.per_library_snapshots,
            {"libalgo.so": Path("libalgo.so"), "libcore.so": Path("libcore.so")},
            compared=["libalgo.so"],
            degraded={"libcore.so": "dump failed"},
            old_provenance="t",
            new_provenance="t",
        )
        snapshot = bundle_snapshot_from_facts(restrict_bundle_facts(facts, record))
        assert set(snapshot.libraries) == {"libalgo.so"}

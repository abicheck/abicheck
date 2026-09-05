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

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

"""G38 Phase 14: the three diff-derived bundle detectors
(``bundle_intra_dep_signature_changed``, ``bundle_intra_type_changed``,
``bundle_provider_changed``) must not be starved by public-surface
scoping.

Split out of ``tests/test_bundle.py`` -- that module is pinned at an exact
``architecture/debt.yaml`` no-growth line-count baseline, so a genuinely
new test class needs a new file. These reproduce the three acceptance
scenarios from the G38 plan doc's Phase 14 entry directly: a
``DiffResult`` whose relevant ``Change`` lives *only* in
``out_of_surface_changes`` (never in ``changes``) -- exactly what a real
``--scope-public-headers`` run (on by default) produces for a headerless,
non-public-surface finding, per ``post_processing.FilterNonPublicSurface``.
Before this phase, all three detectors read ``diff.changes`` alone, so
each of these findings would have been silently invisible to the bundle
report even though the underlying break is real for the bundle's own
internal linkage contract.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.bundle import BundleSnapshot, _compute_resolution_graph, compare_bundle
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    return ElfMetadata(
        soname=soname or "",
        needed=needed or [],
        symbols=[ElfSymbol(name=n, visibility="default") for n in exports or []],
        imports=[ElfImport(name=n) for n in imports or []],
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph)


def _diff_out_of_surface(library: str, *changes: Change) -> DiffResult:
    """A ``DiffResult`` whose *changes* were demoted by public-surface
    scoping -- ``changes`` is empty, ``out_of_surface_changes`` carries
    them, exactly what a real headerless/non-public finding looks like
    after ``FilterNonPublicSurface`` runs (on by default)."""
    return DiffResult(
        old_version="old",
        new_version="new",
        library=library,
        changes=[],
        out_of_surface_changes=list(changes),
        verdict=Verdict.NO_CHANGE,
    )


class TestIntraDepSignatureChangedReachesOutOfSurfaceFindings:
    """Acceptance scenario 1: an internal, headerless C export consumed by
    a sibling changes signature -- no public header names it, so a real
    scan demotes the per-library finding to out-of-surface. The bundle
    report must still emit a consumer-attributed
    BUNDLE_INTRA_DEP_SIGNATURE_CHANGED breaking finding."""

    def test_promotes_out_of_surface_signature_change_when_consumer_resolves_it(
        self,
    ) -> None:
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_fn"]),
                "libalgo.so": _meta(
                    soname="libalgo.so.1", needed=["libcore.so.1"], imports=["core_fn"]
                ),
            }
        )
        diff_libcore = _diff_out_of_surface(
            "libcore.so",
            Change(
                kind=ChangeKind.FUNC_PARAMS_CHANGED,
                symbol="core_fn",
                description="int(int) -> long(long)",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libalgo.so"
        assert findings[0].provider_library == "libcore.so"

    def test_no_finding_when_no_sibling_resolves_it(self) -> None:
        # Same out-of-surface break, but no sibling imports the symbol at
        # all -- the detector's own existing reachability rule (unchanged
        # by this phase) must still suppress the bundle finding.
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["core_fn"]),
                "libother.so": _meta(soname="libother.so.1"),
            }
        )
        diff_libcore = _diff_out_of_surface(
            "libcore.so",
            Change(
                kind=ChangeKind.FUNC_PARAMS_CHANGED,
                symbol="core_fn",
                description="int(int) -> long(long)",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        assert not any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_CHANGED
            for f in result.bundle_findings
        )


class TestProviderChangedReachesOutOfSurfaceFindings:
    """Acceptance scenario 2: an internal, headerless C export moves from
    libcore to libmath between releases, with no sibling DSO importing it
    at all -- confirming the fix does not regress the existing
    external-consumer protection by requiring a sibling import this
    detector never required before."""

    def test_promotes_out_of_surface_provider_move_with_no_bundle_consumer(
        self,
    ) -> None:
        old = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["moved_fn"]),
                "libmath.so": _meta(soname="libmath.so.1"),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                "libmath.so": _meta(soname="libmath.so.1", exports=["moved_fn"]),
            }
        )
        diff_libcore = _diff_out_of_surface(
            "libcore.so",
            Change(
                kind=ChangeKind.FUNC_REMOVED, symbol="moved_fn", description="removed"
            ),
        )
        diff_libmath = _diff_out_of_surface(
            "libmath.so",
            Change(kind=ChangeKind.FUNC_ADDED, symbol="moved_fn", description="added"),
        )
        result = compare_bundle(old, new, [diff_libcore, diff_libmath])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_PROVIDER_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].old_value == "libcore.so"
        assert findings[0].new_value == "libmath.so"


class TestIntraTypeChangedReachesOutOfSurfaceFindings:
    """Acceptance scenario 3: an internal, headerless type changes layout
    in libcore, and a sibling libmath publicly re-exports the type by
    embedding its name in one of libmath's own exported (mangled) symbols
    -- with no DT_NEEDED import-resolution edge from libmath to libcore at
    all (the type reaches libmath only via a shared header, not via a
    call to a provider symbol). Confirms the unscoping fix does not
    regress detect_intra_type_changed's own name-embedding reachability
    rule by wrongly requiring an import edge this detector never
    required."""

    def test_promotes_out_of_surface_type_change_reachable_via_public_export(
        self,
    ) -> None:
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                # No `needed`/DT_NEEDED edge to libcore.so at all -- the
                # type reaches libmath only through a shared header.
                "libmath.so": _meta(
                    soname="libmath.so.1", exports=["_Z11algoUsingT4WidgetE"]
                ),
            }
        )
        diff_libcore = _diff_out_of_surface(
            "libcore.so",
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="Widget",
                description="8 -> 16 bytes",
            ),
        )
        result = compare_bundle(new, new, [diff_libcore])
        findings = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_INTRA_TYPE_CHANGED
        ]
        assert len(findings) == 1
        assert findings[0].consumer_library == "libmath.so"
        assert findings[0].provider_library == "libcore.so"
        # Reaches libmath's exported (public) surface -- full-confidence
        # break, not demoted to risk.
        assert findings[0].effective_verdict is None

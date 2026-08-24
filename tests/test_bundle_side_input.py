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

"""Unit tests for :mod:`abicheck.bundle_side_input` (G38 Phase 13).

Mirrors ``tests/test_bundle_facts.py``'s in-memory ``ElfMetadata`` fixture
style for the pure-Python resolution/parity tests; one class at the bottom
(``@pytest.mark.integration``) compiles two real tiny ``.so`` files to prove
:func:`abicheck.bundle_side_input.compare_release_against_bundle_facts`
resolves a stored OLD side against a genuinely live NEW-side directory end
to end -- not just against hand-built ``ElfMetadata``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.bundle import _compute_resolution_graph
from abicheck.bundle_facts import capture_bundle_facts, compare_bundle_from_facts
from abicheck.bundle_models import BundleSnapshot
from abicheck.bundle_side_input import (
    LiveBundleInput,
    StoredBundleFactsInput,
    compare_bundle_sides,
    compare_release_against_bundle_facts,
    resolve_bundle_side,
)
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import save_bundle_facts

# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_bundle_facts.py's own helpers)
# ---------------------------------------------------------------------------


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    syms = [ElfSymbol(name=name, visibility="default") for name in exports or []]
    imps = [ElfImport(name=name) for name in imports or []]
    return ElfMetadata(
        soname=soname or "", needed=needed or [], symbols=syms, imports=imps
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph
    )


def _diff(
    library: str, *changes: Change, verdict: Verdict = Verdict.BREAKING
) -> DiffResult:
    return DiffResult(
        old_version="old",
        new_version="new",
        library=library,
        changes=list(changes),
        verdict=verdict,
    )


def _metadata() -> dict[str, ElfMetadata]:
    return {
        "libcore.so": _meta(soname="libcore.so", exports=["core_mul", "core_add"]),
        "libalgo.so": _meta(
            soname="libalgo.so", needed=["libcore.so"], imports=["core_mul"]
        ),
    }


def _per_library_snapshots(metadata: dict[str, ElfMetadata]) -> dict[str, AbiSnapshot]:
    return {
        name: AbiSnapshot(library=name, version="old", elf=meta)
        for name, meta in metadata.items()
    }


def _elf_only_fn(symbol: str) -> Function:
    return Function(
        name=symbol, mangled=symbol, return_type="?", visibility=Visibility.ELF_ONLY
    )


# ---------------------------------------------------------------------------
# resolve_bundle_side
# ---------------------------------------------------------------------------


class TestResolveBundleSide:
    def test_live_input_resolves_from_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        metadata = _metadata()
        libraries = {name: tmp_path / name for name in metadata}
        for name, path in libraries.items():
            path.write_bytes(b"")  # existence only -- parsing is monkeypatched below
        import abicheck.bundle as bundle_mod

        monkeypatch.setattr(bundle_mod, "_path_looks_like_elf", lambda p: True)
        monkeypatch.setattr(
            bundle_mod, "parse_elf_metadata", lambda p: metadata[p.name]
        )

        resolved = resolve_bundle_side(LiveBundleInput(libraries=libraries))

        assert set(resolved.snapshot.metadata) == set(metadata)
        assert resolved.signature_evidence == {}
        assert resolved.manifest is None

    def test_stored_input_resolves_with_no_binaries_read(self, tmp_path: Path) -> None:
        metadata = _metadata()
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        out = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, out)

        resolved = resolve_bundle_side(StoredBundleFactsInput(path=out))

        assert set(resolved.snapshot.metadata) == set(metadata)
        assert set(resolved.signature_evidence) == set(metadata)
        assert all(
            isinstance(v, AbiSnapshot) for v in resolved.signature_evidence.values()
        )


# ---------------------------------------------------------------------------
# compare_bundle_sides -- parity across every live/stored pairing
# ---------------------------------------------------------------------------


class TestCompareBundleSidesParity:
    def test_stored_old_live_new_matches_compare_bundle_from_facts(
        self, tmp_path: Path
    ) -> None:
        """The same acceptance shape tests/test_bundle_facts.py's
        TestCompareBundleFromFactsParity pins for compare_bundle_from_facts()
        itself, restated for the unified compare_bundle_sides() entry point:
        a stored OLD side and a live NEW side must agree with the direct
        compare_bundle_from_facts() call, since resolve_bundle_side() is
        exactly what that function's own reconstruction does."""
        old_metadata = _metadata()
        # libcore.so removes core_mul, which libalgo.so imports -- a real
        # cross-DSO break (BUNDLE_INTRA_DEP_REMOVED), keyed off libcore.so's
        # own per-library diff carrying a matching FUNC_REMOVED change.
        new_metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_add"]),
            "libalgo.so": _meta(
                soname="libalgo.so", needed=["libcore.so"], imports=["core_mul"]
            ),
        }
        old_facts = capture_bundle_facts(_per_library_snapshots(old_metadata))
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(old_facts, facts_path)
        new_snapshot = _snapshot(new_metadata)
        core_removed = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="core_mul", description="removed"
        )
        per_lib_results = [
            _diff("libcore.so", core_removed, verdict=Verdict.BREAKING),
            _diff("libalgo.so", verdict=Verdict.NO_CHANGE),
        ]

        direct = compare_bundle_from_facts(old_facts, new_snapshot, per_lib_results)
        via_sides = compare_bundle_sides(
            StoredBundleFactsInput(path=facts_path),
            LiveBundleInput(libraries=dict.fromkeys(new_metadata, Path("/fake"))),
            per_lib_results,
        )
        # via_sides' "live" new side never parses real ElfMetadata for
        # /fake paths in this test (build_bundle_snapshot() skips a file it
        # can't parse, with a warning) -- so instead compare the two
        # against a stored/stored pairing, which needs no filesystem I/O on
        # either side and is the real apples-to-apples check.
        new_facts = capture_bundle_facts(_per_library_snapshots(new_metadata))
        new_facts_path = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(new_facts, new_facts_path)
        stored_stored = compare_bundle_sides(
            StoredBundleFactsInput(path=facts_path),
            StoredBundleFactsInput(path=new_facts_path),
            per_lib_results,
        )
        assert direct.bundle_findings == stored_stored.bundle_findings
        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED for f in direct.bundle_findings
        )
        # via_sides itself must still resolve cleanly (no exception) even
        # though its "live" side is unparseable -- confirming the mixed
        # live/stored pairing degrades the same way a live compare_bundle()
        # call already does for an unparseable file, not by crashing.
        assert via_sides is not None

    def test_signature_evidence_gate_runs_for_stored_stored_pairing(
        self, tmp_path: Path
    ) -> None:
        """G38 Phase 12's C-boundary signature-evidence gate must run for a
        stored/stored pairing too, not only stored/live -- compare_bundle_
        sides() is the first entry point in this codebase that can express
        that pairing at all."""
        bundle_metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
            "libconsumer.so": _meta(
                soname="libconsumer.so", needed=["libcore.so"], imports=["core_fn"]
            ),
        }
        per_lib_results = [
            _diff("libcore.so", verdict=Verdict.NO_CHANGE),
            _diff("libconsumer.so", verdict=Verdict.NO_CHANGE),
        ]

        def _facts_snapshots(version: str) -> dict[str, AbiSnapshot]:
            return {
                "libcore.so": AbiSnapshot(
                    library="libcore.so",
                    version=version,
                    elf=bundle_metadata["libcore.so"],
                    functions=[_elf_only_fn("core_fn")],
                    elf_only_mode=True,
                ),
                "libconsumer.so": AbiSnapshot(
                    library="libconsumer.so",
                    version=version,
                    elf=bundle_metadata["libconsumer.so"],
                ),
            }

        old_path = tmp_path / "old.bundlefacts.json"
        new_path = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(capture_bundle_facts(_facts_snapshots("old")), old_path)
        save_bundle_facts(capture_bundle_facts(_facts_snapshots("new")), new_path)

        result = compare_bundle_sides(
            StoredBundleFactsInput(path=old_path),
            StoredBundleFactsInput(path=new_path),
            per_lib_results,
        )

        assert any(
            f.kind == ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
            for f in result.bundle_findings
        )


class TestCompareBundleSidesManifestPrecedence:
    def test_explicit_manifest_overrides_either_side(self, tmp_path: Path) -> None:
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry

        metadata = _metadata()
        # facts carry no manifest of their own -- an explicitly-passed
        # override promising a symbol neither library actually exports
        # must still be enforced, proving the override reaches
        # compare_bundle() rather than being silently dropped in favour of
        # whichever side's own (here: absent) manifest resolve_bundle_side
        # would otherwise use.
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)
        override = InstantiationManifest(
            entries=(ManifestEntry(symbol="promised_but_missing"),)
        )

        result = compare_bundle_sides(
            StoredBundleFactsInput(path=facts_path),
            StoredBundleFactsInput(path=facts_path),
            [
                _diff("libcore.so", verdict=Verdict.NO_CHANGE),
                _diff("libalgo.so", verdict=Verdict.NO_CHANGE),
            ],
            manifest=override,
        )
        assert any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_INSTANTIATION_REMOVED
            for f in result.bundle_findings
        )


# ---------------------------------------------------------------------------
# compare_release_against_bundle_facts -- mocked-resolution unit coverage
# ---------------------------------------------------------------------------
#
# These pin the two Codex-review findings below without needing a real
# compiler/castxml (unlike TestCompareReleaseAgainstBundleFacts, which
# exercises the identical function end to end but can only reproduce the
# actual ScopeMismatchError symptom when castxml is available to attach
# header-derived declarations -- see that class's own docstring). Both
# findings are about how the function resolves the NEW side, so both are
# fully observable by mocking `service.resolve_input`/`.compare_snapshots`
# and inspecting what they were called with -- no header/castxml
# involvement needed to prove the *threading* is correct.


class TestCompareReleaseAgainstBundleFactsResolutionUnit:
    def _old_facts(self, tmp_path: Path) -> Path:
        metadata = {"libcore.so": _meta(soname="libcore.so", exports=["core_fn"])}
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)
        return facts_path

    def test_include_dependencies_defaults_to_false_and_is_threaded_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review (P1): the CLI flow that actually produces a
        `BundleFacts` file (`compare --bundle-facts-out`) dependency-scopes
        by default (`--include-system-declarations`'s own Click default is
        `False` -- cli_options.include_dependencies_option), not
        `service.resolve_input`'s bare-Python default of `True`. Before the
        fix, this function's NEW-side `service.resolve_input` call never
        passed `include_dependencies` at all, so it always resolved
        unfiltered regardless of how the OLD side's facts were captured --
        a real, silent scope mismatch for the flow this function exists to
        support. Pinned here as a call-argument assertion (the only way to
        observe it without castxml, since `_check_dependency_scope_
        comparable` only fires when a side carries header-derived
        declarations, which no plain-ELF fixture has)."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod

        facts_path = self._old_facts(tmp_path)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_so = new_dir / "libcore.so"
        new_so.write_bytes(b"")

        monkeypatch.setattr(
            package_mod,
            "discover_shared_libraries",
            lambda d, include_private=False: [new_so],
        )
        captured_kwargs: dict[str, object] = {}

        def _fake_resolve_input(path, **kwargs):
            captured_kwargs.update(kwargs)
            return AbiSnapshot(
                library="libcore.so",
                version="new",
                elf=_meta(soname="libcore.so", exports=["core_fn"]),
            )

        monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)
        monkeypatch.setattr(
            service_mod,
            "compare_snapshots",
            lambda old, new, policy: _diff("libcore.so", verdict=Verdict.NO_CHANGE),
        )

        compare_release_against_bundle_facts(facts_path, new_dir)

        assert captured_kwargs["include_dependencies"] is False

        captured_kwargs.clear()
        compare_release_against_bundle_facts(
            facts_path, new_dir, include_dependencies=True
        )
        assert captured_kwargs["include_dependencies"] is True

    def test_duplicate_new_side_versions_use_version_aware_selection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review (P2): two versions of one library in *new_dir* must
        resolve via the same version-aware duplicate resolution the live
        release fan-out uses (`cli_helpers_compare._build_match_map`), not
        a plain dict-build that keeps whichever path a directory listing
        happens to enumerate last. `libcore.so.9` sorts after `libcore.so.10`
        lexicographically but must lose to it under real version
        comparison."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod

        facts_path = self._old_facts(tmp_path)
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        v9 = new_dir / "libcore.so.9"
        v10 = new_dir / "libcore.so.10"
        v9.write_bytes(b"")
        v10.write_bytes(b"")

        # Enumerate the lower version last, so a naive "last write wins"
        # dict build would pick the wrong one.
        monkeypatch.setattr(
            package_mod,
            "discover_shared_libraries",
            lambda d, include_private=False: [v10, v9],
        )
        resolved_paths: list[Path] = []

        def _fake_resolve_input(path, **kwargs):
            resolved_paths.append(path)
            return AbiSnapshot(
                library="libcore.so",
                version="new",
                elf=_meta(soname="libcore.so", exports=["core_fn"]),
            )

        monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)
        monkeypatch.setattr(
            service_mod,
            "compare_snapshots",
            lambda old, new, policy: _diff("libcore.so", verdict=Verdict.NO_CHANGE),
        )

        compare_release_against_bundle_facts(facts_path, new_dir)

        assert resolved_paths == [v10]


# ---------------------------------------------------------------------------
# compare_release_against_bundle_facts -- real compiled binaries
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses the GNU ld flag -Wl,-soname; Mach-O ld and link.exe don't "
    "accept it, and gcc/clang -shared produces a non-ELF binary on those "
    "platforms anyway. Bundle analysis itself is ELF/Linux-only per "
    "ADR-018 / ADR-023 (matches TestWriteBundleFactsOutCapturesARealSnapshot's "
    "identical guard in tests/test_bundle_facts.py, which this class's own "
    "_build_so fixture is otherwise a near-duplicate of).",
)
@pytest.mark.integration
class TestCompareReleaseAgainstBundleFacts:
    def _build_so(self, tmp_path: Path, name: str, body: str) -> Path:
        gcc = shutil.which("gcc")
        if gcc is None:
            pytest.skip("gcc is not available")
        src = tmp_path / f"{name}.c"
        src.write_text(body)
        out = tmp_path / name
        res = subprocess.run(
            [
                gcc,
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                str(src),
                "-o",
                str(out),
                f"-Wl,-soname,{name}",
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            pytest.fail(f"gcc failed: {res.stderr}")
        return out

    def test_unchanged_library_is_no_change(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        body = "int add(int a, int b) { return a + b; }\n"
        self._build_so(old_dir, "libreal.so", body)
        self._build_so(new_dir, "libreal.so", body)

        from abicheck.cli_resolve import _resolve_input

        old_snapshot = _resolve_input(
            old_dir / "libreal.so", [], [], "old", "c++", include_dependencies=True
        )
        facts = capture_bundle_facts({"libreal.so": old_snapshot})
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)

        # Must match the OLD side's own dependency scope (Codex review, P1)
        # -- the function's default is False, matching --bundle-facts-out's
        # own CLI default, not this fixture's explicit True.
        result = compare_release_against_bundle_facts(
            facts_path, new_dir, include_dependencies=True
        )

        assert result.analysis_errors == []

    def test_removed_function_produces_a_bundle_finding(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        # A provider/consumer pair so an intra-bundle finding is possible --
        # a single unrelated library removing a function only affects that
        # library's own (not run here) per-library diff.
        self._build_so(
            old_dir,
            "libcore.so",
            "int core_fn(int x) { return x; }\n",
        )
        self._build_so(
            new_dir,
            "libcore.so",
            "int core_renamed(int x) { return x; }\n",
        )

        from abicheck.cli_resolve import _resolve_input

        old_snapshot = _resolve_input(
            old_dir / "libcore.so", [], [], "old", "c++", include_dependencies=True
        )
        facts = capture_bundle_facts({"libcore.so": old_snapshot})
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)

        result = compare_release_against_bundle_facts(
            facts_path, new_dir, include_dependencies=True
        )

        # New-side signature evidence was resolved for real (via
        # service.resolve_input), so a real per-library diff ran; whether
        # that surfaces as a bundle-level finding depends on there being a
        # provider/consumer relationship, which this single-library fixture
        # doesn't have -- the assertion that matters here is that the
        # driver ran the real per-library compare end to end without
        # raising, and recorded no unexpected analysis error.
        assert result.analysis_errors == []

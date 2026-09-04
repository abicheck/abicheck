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


# compare_stored_bundle_facts_pair (the stored/stored driver, CLI cleanup
# phase two, PR I) moved to abicheck/workflows/bundle_stored_pair_compare.py
# (Codex review, PR #1060) -- its own tests now live in
# tests/test_bundle_stored_pair_compare.py.


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
            lambda old, new, suppress=None, *, policy, policy_file=None: _diff(
                "libcore.so", verdict=Verdict.NO_CHANGE
            ),
        )

        compare_release_against_bundle_facts(facts_path, new_dir)

        assert captured_kwargs["include_dependencies"] is False

        captured_kwargs.clear()
        compare_release_against_bundle_facts(
            facts_path, new_dir, include_dependencies=True
        )
        assert captured_kwargs["include_dependencies"] is True

    def test_scope_mismatch_error_propagates_uncaught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``compare_snapshots()`` raises ``ScopeMismatchError`` when a
        matched pair's ``dependency_scope`` disagrees (both sides
        header-derived) -- this function must let it propagate uncaught,
        not swallow or translate it, since ``workflows/AGENTS.md``'s own
        rule is that error types are the contract and the *CLI* layer is
        the one place that translates them (Codex review, PR #1060, round
        12 -- found on this function's stored/stored sibling, but this
        function shares the identical ``service.compare_snapshots()``
        chokepoint and so the identical failure mode)."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod
        from abicheck.errors import ScopeMismatchError

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
        monkeypatch.setattr(
            service_mod,
            "resolve_input",
            lambda path, **kwargs: AbiSnapshot(
                library="libcore.so",
                version="new",
                elf=_meta(soname="libcore.so", exports=["core_fn"]),
                from_headers=True,
                dependency_scope="full",
            ),
        )

        # service.compare_snapshots() is deliberately left real here -- it's
        # the function under test for this scenario, not a collaborator to
        # stub out.

        # The OLD side's own facts were captured from a plain-ELF fixture
        # (no from_headers/dependency_scope at all) -- reload and mutate it
        # so both sides carry an explicit, disagreeing dependency_scope.
        from abicheck.serialization import load_bundle_facts, save_bundle_facts

        facts = load_bundle_facts(facts_path)
        facts.per_library_snapshots["libcore.so"].from_headers = True
        facts.per_library_snapshots["libcore.so"].dependency_scope = "filtered"
        save_bundle_facts(facts, facts_path)

        with pytest.raises(ScopeMismatchError):
            compare_release_against_bundle_facts(facts_path, new_dir)

    def test_policy_file_is_forwarded_to_per_library_compare(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Before this fix, this driver forwarded only *policy* (a bare
        base-policy name) to ``service.compare_snapshots`` for each matched
        library's per-library diff -- a caller's ``policy_file``-shaped
        reclassify/override rules never reached that call regardless of
        whether the caller's own policy document declared any (Codex
        review; the highest-leverage gap in this driver)."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod
        from abicheck.checker_policy import ChangeKind, Verdict as VerdictEnum
        from abicheck.policy_file import PolicyFile

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

        def _fake_resolve_input(path, **kwargs):
            return AbiSnapshot(
                library="libcore.so",
                version="new",
                elf=_meta(soname="libcore.so", exports=["core_fn"]),
            )

        monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)
        captured: dict[str, object] = {}

        def _fake_compare_snapshots(
            old, new, suppress=None, *, policy, policy_file=None
        ):
            captured["policy_file"] = policy_file
            return _diff("libcore.so", verdict=Verdict.NO_CHANGE)

        monkeypatch.setattr(service_mod, "compare_snapshots", _fake_compare_snapshots)

        # Omitted: unchanged behavior, None reaches the per-library call.
        compare_release_against_bundle_facts(facts_path, new_dir)
        assert captured["policy_file"] is None

        # Given: forwarded verbatim to every matched library's own compare.
        pf = PolicyFile(overrides={ChangeKind.FUNC_VISIBILITY_CHANGED: VerdictEnum.BREAKING})
        compare_release_against_bundle_facts(facts_path, new_dir, policy_file=pf)
        assert captured["policy_file"] is pf

    def test_suppress_is_forwarded_to_per_library_compare(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Before this fix, this driver had no way to honor a caller's
        suppression list at all -- ``service.compare_snapshots`` was called
        with no *suppression* argument, so a matched library was always
        scored with every known/intentional change still live, unlike every
        other comparison entry point in this codebase (the same class of
        gap the ``policy_file`` fix above closed for reclassify/override
        rules)."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod
        from abicheck.workflows.suppression import SuppressionList

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
        monkeypatch.setattr(
            service_mod,
            "resolve_input",
            lambda path, **kwargs: AbiSnapshot(
                library="libcore.so",
                version="new",
                elf=_meta(soname="libcore.so", exports=["core_fn"]),
            ),
        )
        captured: dict[str, object] = {}

        def _fake_compare_snapshots(old, new, suppress=None, *, policy, policy_file=None):
            captured["suppress"] = suppress
            return _diff("libcore.so", verdict=Verdict.NO_CHANGE)

        monkeypatch.setattr(service_mod, "compare_snapshots", _fake_compare_snapshots)

        # Omitted: unchanged behavior, None reaches the per-library call.
        compare_release_against_bundle_facts(facts_path, new_dir)
        assert captured["suppress"] is None

        # Given: forwarded verbatim to every matched library's own compare.
        suppression = SuppressionList([])
        compare_release_against_bundle_facts(facts_path, new_dir, suppress=suppression)
        assert captured["suppress"] is suppression

    def test_policy_file_also_reaches_bundle_level_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review (P2, follow-up on the same PR): the fix above threaded
        ``policy_file`` into each per-library ``service.compare_snapshots``
        call, but the final ``compare_bundle_from_facts`` call this driver
        makes for its own bundle-level (``BUNDLE_*``-kind) findings still
        received only the bare ``policy`` name -- ``BundleDiffResult.
        bundle_verdict`` never saw the policy file at all. Pinned end to end
        via the real returned ``BundleDiffResult``, not a mock."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod
        from abicheck.policy_file import PolicyFile

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
        monkeypatch.setattr(
            service_mod,
            "resolve_input",
            lambda path, **kwargs: AbiSnapshot(
                library="libcore.so",
                version="new",
                elf=_meta(soname="libcore.so", exports=["core_fn"]),
            ),
        )
        monkeypatch.setattr(
            service_mod,
            "compare_snapshots",
            lambda old, new, suppress=None, *, policy, policy_file=None: _diff(
                "libcore.so", verdict=Verdict.NO_CHANGE
            ),
        )

        pf = PolicyFile()
        result = compare_release_against_bundle_facts(facts_path, new_dir, policy_file=pf)
        assert result.policy_file is pf

    def test_policy_file_override_genuinely_demotes_a_real_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bug-class regression (plan Phase 9, incident #883): the two
        tests above only prove *forwarding* -- both mock
        ``service.compare_snapshots`` itself, the very function whose real
        behavior a ``policy_file`` override is supposed to change -- so
        neither would catch a regression where the override reaches the
        call but is silently ignored by it, or is threaded to the wrong
        keyword and has no effect. This lets the real, production
        ``compare_snapshots`` run end to end (only ``service.resolve_input``
        is mocked, since the NEW side has no real compiled binary here) and
        asserts the actual, returned verdict is genuinely demoted -- closing
        #883's own registered gap of "checked independently, not all
        derived from one production helper" for at least this one detector
        family."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod
        from abicheck.policy_file import PolicyFile

        old_fn = Function(
            name="core_fn",
            mangled="core_fn",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
        old_snapshot = AbiSnapshot(
            library="libcore.so",
            version="old",
            elf=_meta(soname="libcore.so", exports=["core_fn"]),
            functions=[old_fn],
        )
        facts = capture_bundle_facts({"libcore.so": old_snapshot})
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)

        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_so = new_dir / "libcore.so"
        new_so.write_bytes(b"")

        monkeypatch.setattr(
            package_mod,
            "discover_shared_libraries",
            lambda d, include_private=False: [new_so],
        )

        new_fn = Function(
            name="core_fn",
            mangled="core_fn",
            return_type="int",
            visibility=Visibility.HIDDEN,
        )

        def _fake_resolve_input(path, **kwargs):
            return AbiSnapshot(
                library="libcore.so",
                version="new",
                elf=_meta(soname="libcore.so", exports=[]),
                functions=[new_fn],
            )

        monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)

        # Baseline: no override -- the real compare_snapshots reports this
        # kind's built-in default verdict, BREAKING.
        baseline = compare_release_against_bundle_facts(facts_path, new_dir)
        baseline_diffs = [d for d in baseline.per_library if d.library == "libcore.so"]
        assert len(baseline_diffs) == 1
        assert any(
            c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED
            for c in baseline_diffs[0].changes
        )
        assert baseline_diffs[0].verdict == Verdict.BREAKING
        assert baseline.verdict == Verdict.BREAKING

        # Given: a real PolicyFile override demoting that one kind must
        # actually change the per-library verdict AND the aggregate
        # BundleDiffResult.verdict this driver returns -- not merely be
        # accepted and discarded.
        pf = PolicyFile(
            overrides={ChangeKind.FUNC_VISIBILITY_CHANGED: Verdict.COMPATIBLE}
        )
        demoted = compare_release_against_bundle_facts(
            facts_path, new_dir, policy_file=pf
        )
        demoted_diffs = [d for d in demoted.per_library if d.library == "libcore.so"]
        assert len(demoted_diffs) == 1
        assert any(
            c.kind == ChangeKind.FUNC_VISIBILITY_CHANGED
            for c in demoted_diffs[0].changes
        )
        assert demoted_diffs[0].verdict == Verdict.COMPATIBLE
        assert demoted.verdict == Verdict.COMPATIBLE

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
            lambda old, new, suppress=None, *, policy, policy_file=None: _diff(
                "libcore.so", verdict=Verdict.NO_CHANGE
            ),
        )

        compare_release_against_bundle_facts(facts_path, new_dir)

        assert resolved_paths == [v10]

    def test_header_backend_and_compile_are_forwarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Prior to this fix, ``header_backend``/``compile`` were silently
        dropped -- the NEW side always resolved under
        ``header_backend="auto"`` with no ``CompileContext``, so a
        header-scoped comparison on a host with no working castxml (a
        clang/icpx-only host) died rather than using the caller's own
        resolved compiler binding/frontend."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod
        from abicheck.compile_context import CompileContext

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
            lambda old, new, suppress=None, *, policy, policy_file=None: _diff(
                "libcore.so", verdict=Verdict.NO_CHANGE
            ),
        )

        ctx = CompileContext(
            gcc_path="icpx",
            gcc_option_tokens=("-fsycl", "-DONEDAL_DATA_PARALLEL", "-std=c++17"),
            frontend="clang",
        )
        compare_release_against_bundle_facts(
            facts_path, new_dir, header_backend="clang", compile=ctx
        )

        assert captured_kwargs["header_backend"] == "clang"
        assert captured_kwargs["compile"] is ctx

    def test_per_library_overrides_win_over_the_uniform_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``headers``/``includes``/``compile`` apply uniformly to every
        matched library by default -- correct only when every library in the
        bundle shares one header tree/compile configuration, which does not
        hold for a mixed-toolchain release (e.g. a plain-C++ library
        alongside a ``-fsycl``/``icpx`` one). A per-library override for one
        library must not leak onto a library with no entry in that map, which
        must keep falling back to the uniform default."""
        import abicheck.package as package_mod
        import abicheck.service as service_mod
        from abicheck.compile_context import CompileContext

        metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
            "libdpc.so": _meta(soname="libdpc.so", exports=["dpc_fn"]),
        }
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)

        new_dir = tmp_path / "new"
        new_dir.mkdir()
        core_so = new_dir / "libcore.so"
        dpc_so = new_dir / "libdpc.so"
        core_so.write_bytes(b"")
        dpc_so.write_bytes(b"")

        monkeypatch.setattr(
            package_mod,
            "discover_shared_libraries",
            lambda d, include_private=False: [core_so, dpc_so],
        )
        captured_kwargs: dict[Path, dict[str, object]] = {}

        def _fake_resolve_input(path, **kwargs):
            captured_kwargs[path] = kwargs
            return AbiSnapshot(
                library=path.name,
                version="new",
                elf=_meta(soname=path.name, exports=["fn"]),
            )

        monkeypatch.setattr(service_mod, "resolve_input", _fake_resolve_input)
        monkeypatch.setattr(
            service_mod,
            "compare_snapshots",
            lambda old, new, suppress=None, *, policy, policy_file=None: _diff(
                new.library, verdict=Verdict.NO_CHANGE
            ),
        )

        uniform_headers = [Path("/include/common")]
        uniform_includes = [Path("/include/common/sys")]
        dpc_headers = [Path("/include/dpc")]
        dpc_includes = [Path("/include/dpc/sys")]
        dpc_ctx = CompileContext(gcc_path="icpx", frontend="clang")

        compare_release_against_bundle_facts(
            facts_path,
            new_dir,
            headers=uniform_headers,
            includes=uniform_includes,
            per_library_headers={"libdpc.so": dpc_headers},
            per_library_includes={"libdpc.so": dpc_includes},
            per_library_compile={"libdpc.so": dpc_ctx},
        )

        assert captured_kwargs[core_so]["headers"] == uniform_headers
        assert captured_kwargs[core_so]["includes"] == uniform_includes
        assert captured_kwargs[core_so]["compile"] is None
        assert captured_kwargs[dpc_so]["headers"] == dpc_headers
        assert captured_kwargs[dpc_so]["includes"] == dpc_includes
        assert captured_kwargs[dpc_so]["compile"] is dpc_ctx


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

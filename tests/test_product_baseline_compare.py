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

"""Unit tests for :func:`abicheck.product_baseline.compare_product_directories`
and its supporting helpers (``_roots_for_library``, ``_discover_library_map``).

Split out of ``test_product_baseline.py`` (AI-readiness file-size cap) --
that file keeps the pack/unpack archive-format tests
(:class:`TestPackProductBaseline`, :class:`TestUnpackProductBaseline`, ...);
this one covers the whole-product comparison entry point. Same fixtures,
different concern -- see that file's own module docstring for the split
rationale."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError


class TestCompareProductDirectoriesCanonicalFallbackAndPolicy:
    def _capture_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        from abicheck import bundle as bundle_mod, service_compare_pipeline as scp

        run_compare_calls: list[dict[str, object]] = []
        compare_bundle_calls: list[dict[str, object]] = []

        class _FakeDiffResult:
            def __init__(self, library: str) -> None:
                self.library = library

        class _FakeResult:
            def __init__(self, library: str) -> None:
                self.diff = _FakeDiffResult(library)

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            run_compare_calls.append({"old": old_lib, "new": new_lib, **kwargs})
            return _FakeResult(str(new_lib))

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        class _FakeBundleDiffResult(dict):  # type: ignore[type-arg]
            """Behaves like a dict for existing kwargs-based assertions,
            while also carrying a real, mutable ``bundle_findings`` list --
            compare_product_directories() appends standalone-removal
            findings to the real BundleDiffResult it gets back."""

            def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
                super().__init__(**kwargs)
                self.bundle_findings: list[object] = []

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            compare_bundle_calls.append(kwargs)
            return _FakeBundleDiffResult(**kwargs)

        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)
        return run_compare_calls, compare_bundle_calls

    def test_pairs_unmatched_libraries_across_a_soname_major_bump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A release that only bumps a SONAME major (libfoo.so.1 ->
        # libfoo.so.2, no unversioned alias carried across) previously
        # never reached run_compare() at all -- the exact-path
        # intersection was empty -- silently losing every symbol/type
        # change between the two versions (Codex review, fresh evidence).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so.1").write_bytes(b"OLD")
        (new_dir / "lib" / "libfoo.so.2").write_bytes(b"NEW")

        run_compare_calls, _ = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir)

        assert len(run_compare_calls) == 1
        assert run_compare_calls[0]["old"] == old_dir / "lib" / "libfoo.so.1"
        assert run_compare_calls[0]["new"] == new_dir / "lib" / "libfoo.so.2"

    def test_canonical_fallback_diff_result_carries_the_new_side_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # run_compare()'s real DiffResult.library is always stamped from
        # the *old* side's bare filename (checker.py: library=old.library,
        # itself so_path.name) -- for a canonical-fallback pair (SONAME
        # bump) that differs from the new side's own filename.
        # compare_bundle() indexes per_library_results by
        # Path(result.library).name and excludes that same key from its
        # own sibling/consumer scan over new.metadata, which is keyed by
        # each library's *new*-side bare filename -- left as old.library,
        # the provider's own new binary would never be excluded from that
        # scan and could be reported as a cross-DSO consumer of its own
        # change (Codex review, fresh evidence). This test uses a fake
        # run_compare() that reproduces the real function's old-side
        # stamping (unlike this class's shared _capture_calls() helper,
        # whose fake already (and unrealistically) uses the new side) so
        # the fix is actually exercised rather than masked.
        from abicheck import bundle as bundle_mod, service_compare_pipeline as scp
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so.1").write_bytes(b"OLD")
        (new_dir / "lib" / "libfoo.so.2").write_bytes(b"NEW")

        class _FakeDiffResult:
            def __init__(self, library: str) -> None:
                self.library = library

        class _FakeResult:
            def __init__(self, library: str) -> None:
                self.diff = _FakeDiffResult(library)

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            # Mirrors the real run_compare(): DiffResult.library is the
            # *old* side's bare filename, never the new side's.
            return _FakeResult(Path(old_lib).name)

        captured: dict[str, list[_FakeDiffResult]] = {}

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            captured["per_library_results"] = per_library_results
            return type("R", (), {"bundle_findings": []})()

        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)

        compare_product_directories(old_dir, new_dir)

        per_library_results = captured["per_library_results"]
        assert len(per_library_results) == 1
        # Must match the new side's own bare filename -- the identity
        # new_bundle_map/new_snapshot (and therefore new.metadata inside
        # compare_bundle()) actually key this library under -- not the old
        # side's, which compare_bundle()'s sibling-exclusion logic would
        # never find in new.metadata.
        assert per_library_results[0].library == "libfoo.so.2"

    def test_pairs_unmatched_dylibs_across_a_macho_version_bump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The Mach-O counterpart of the SONAME-major-bump case above:
        # libfoo.1.dylib -> libfoo.2.dylib is the standard ld64
        # compatibility-version-in-filename convention, distinct from ELF's
        # version-after-extension form -- _canonical_library_key() only
        # stripped ELF `.so` versions, so this pair was never even
        # considered for the canonical fallback (Codex review, fresh
        # evidence).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.1.dylib").write_bytes(b"OLD")
        (new_dir / "lib" / "libfoo.2.dylib").write_bytes(b"NEW")

        run_compare_calls, _ = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir)

        assert len(run_compare_calls) == 1
        assert run_compare_calls[0]["old"] == old_dir / "lib" / "libfoo.1.dylib"
        assert run_compare_calls[0]["new"] == new_dir / "lib" / "libfoo.2.dylib"

    def test_does_not_guess_when_the_canonical_group_is_ambiguous(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two unmatched candidates sharing one canonical name on the same
        # side must not be silently paired with a guess -- ambiguity is
        # left unpaired, not resolved arbitrarily.
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so.1").write_bytes(b"OLD1")
        (old_dir / "lib" / "libfoo.so.3").write_bytes(b"OLD3")
        (new_dir / "lib" / "libfoo.so.2").write_bytes(b"NEW")

        run_compare_calls, _ = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir)

        assert run_compare_calls == []

    def test_canonical_fallback_is_scoped_per_directory_not_globally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two independent libraries sharing a basename in different
        # directories (an ordinary plugin-host layout) that each bump
        # their own SONAME major must each pair with their own directory's
        # counterpart -- a bare, global canonical key would put both
        # old-side and both new-side candidates in one shared bucket,
        # read that bucket as ambiguous (len != 1) on both sides, and
        # silently leave both pairs unmatched (Codex review, fresh
        # evidence).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "plugins" / "a").mkdir(parents=True)
        (old_dir / "plugins" / "b").mkdir(parents=True)
        (new_dir / "plugins" / "a").mkdir(parents=True)
        (new_dir / "plugins" / "b").mkdir(parents=True)
        (old_dir / "plugins" / "a" / "libfoo.so.1").write_bytes(b"OLDA")
        (old_dir / "plugins" / "b" / "libfoo.so.1").write_bytes(b"OLDB")
        (new_dir / "plugins" / "a" / "libfoo.so.2").write_bytes(b"NEWA")
        (new_dir / "plugins" / "b" / "libfoo.so.2").write_bytes(b"NEWB")

        run_compare_calls, _ = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir)

        assert len(run_compare_calls) == 2
        pairs = {(call["old"], call["new"]) for call in run_compare_calls}
        assert pairs == {
            (
                old_dir / "plugins" / "a" / "libfoo.so.1",
                new_dir / "plugins" / "a" / "libfoo.so.2",
            ),
            (
                old_dir / "plugins" / "b" / "libfoo.so.1",
                new_dir / "plugins" / "b" / "libfoo.so.2",
            ),
        }

    def test_canonical_fallback_still_pairs_across_a_directory_move(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A single library that moves directories between releases
        # (lib/provider.so -> lib64/provider.so), version bump included,
        # has no shared directory for a directory-scoped-only pass to key
        # on at all -- restricting the canonical fallback to that pass
        # alone regressed this case entirely, silently reporting an
        # unchanged library as a removal plus addition (Codex review,
        # fresh evidence: found by re-reviewing the directory-scoping fix
        # itself).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib64").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so.1").write_bytes(b"OLD")
        (new_dir / "lib64" / "libfoo.so.2").write_bytes(b"NEW")

        run_compare_calls, _ = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir)

        assert len(run_compare_calls) == 1
        assert run_compare_calls[0]["old"] == old_dir / "lib" / "libfoo.so.1"
        assert run_compare_calls[0]["new"] == new_dir / "lib64" / "libfoo.so.2"

    def test_exact_path_match_is_not_reconsidered_by_the_canonical_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so.1").write_bytes(b"OLD")
        (new_dir / "lib" / "libfoo.so.1").write_bytes(b"NEW")

        run_compare_calls, _ = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir)

        assert len(run_compare_calls) == 1
        assert run_compare_calls[0]["old"] == old_dir / "lib" / "libfoo.so.1"
        assert run_compare_calls[0]["new"] == new_dir / "lib" / "libfoo.so.1"

    def test_threads_the_selected_policy_into_compare_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # compare_bundle()'s own bundle-level verdict was previously always
        # scored under the hardcoded strict_abi default, regardless of what
        # policy the caller selected for its per-library comparisons
        # (Codex review, fresh evidence).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)

        _, compare_bundle_calls = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir, policy="plugin_abi")

        assert len(compare_bundle_calls) == 1
        assert compare_bundle_calls[0]["policy"] == "plugin_abi"

    def test_preserves_ambiguity_when_an_exact_match_consumes_one_member(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # old={.1,.2}, new={.2,.3}: .2/.2 exact-matches, but that must not
        # make the *remaining* .1/.3 candidates look artificially
        # unambiguous -- they're an unrelated removal+addition, not one
        # evolving library (Codex review, fresh evidence).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so.1").write_bytes(b"OLD1")
        (old_dir / "lib" / "libfoo.so.2").write_bytes(b"OLD2")
        (new_dir / "lib" / "libfoo.so.2").write_bytes(b"NEW2")
        (new_dir / "lib" / "libfoo.so.3").write_bytes(b"NEW3")

        run_compare_calls, _ = self._capture_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir)

        # Only the genuine exact match (.2/.2) is compared -- .1 and .3
        # are left unpaired, not guessed together.
        assert len(run_compare_calls) == 1
        assert run_compare_calls[0]["old"] == old_dir / "lib" / "libfoo.so.2"
        assert run_compare_calls[0]["new"] == new_dir / "lib" / "libfoo.so.2"

    def test_bundle_snapshot_is_keyed_by_bare_filename_not_relative_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A library that moves directories between releases
        # (lib/provider.so -> lib64/provider.so) must not read as an
        # unrelated removal+addition at the bundle-snapshot level, and
        # bundle-level cross-referencing must key the same way
        # compare_bundle()'s own diff_by_library does (bare filename) --
        # otherwise intra-dep signature/type correlation silently breaks
        # for any library not sitting at the discovery root (Codex
        # review, fresh evidence).
        from abicheck import bundle as bundle_mod
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib64").mkdir(parents=True)
        (old_dir / "lib" / "provider.so").write_bytes(b"OLD")
        (new_dir / "lib64" / "provider.so").write_bytes(b"NEW")

        build_calls: list[dict[str, object]] = []

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            build_calls.append(dict(libraries))
            return {"libraries": dict(libraries)}

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            class _Result(dict):  # type: ignore[type-arg]
                def __init__(self):  # type: ignore[no-untyped-def]
                    super().__init__()
                    self.bundle_findings: list[object] = []

            return _Result()

        from abicheck import service_compare_pipeline as scp

        class _FakeDiffResult:
            def __init__(self, library: str) -> None:
                self.library = library

        class _FakeResult:
            def __init__(self, library: str) -> None:
                self.diff = _FakeDiffResult(library)

        monkeypatch.setattr(
            scp, "run_compare", lambda old, new, **kw: _FakeResult(str(new))
        )
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)

        compare_product_directories(old_dir, new_dir)

        assert len(build_calls) == 2
        old_call, new_call = build_calls
        assert set(old_call) == {"provider.so"}
        assert set(new_call) == {"provider.so"}


class TestCompareProductDirectoriesRejectsNonexistentDirectories:
    def test_rejects_a_nonexistent_old_directory(self, tmp_path: Path) -> None:
        # Silently returning an empty BundleDiffResult (NO_CHANGE) for a
        # misspelled/missing directory is a false-green whole-product
        # compatibility gate (Codex review, fresh evidence).
        from abicheck.product_baseline import compare_product_directories

        new_dir = tmp_path / "new"
        new_dir.mkdir()
        with pytest.raises(SnapshotError, match="not a directory"):
            compare_product_directories(tmp_path / "does-not-exist", new_dir)

    def test_rejects_a_nonexistent_new_directory(self, tmp_path: Path) -> None:
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        old_dir.mkdir()
        with pytest.raises(SnapshotError, match="not a directory"):
            compare_product_directories(old_dir, tmp_path / "does-not-exist")

    def test_rejects_a_regular_file_as_a_directory_operand(
        self, tmp_path: Path
    ) -> None:
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        old_dir.mkdir()
        not_a_dir = tmp_path / "new.txt"
        not_a_dir.write_text("oops")
        with pytest.raises(SnapshotError, match="not a directory"):
            compare_product_directories(old_dir, not_a_dir)


class TestCompareProductDirectoriesRejectsSymlinkLoops:
    def test_a_product_containing_only_a_loop_is_rejected_not_compared(
        self, tmp_path: Path
    ) -> None:
        # A library-shaped self-referential symlink loop is not a real
        # library -- discovering it anyway previously made a product
        # containing nothing but a loop compare against an empty product
        # as a spurious bundle_library_removed finding, instead of the
        # invalid product being rejected with SnapshotError (Codex
        # review, fresh evidence).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        new_dir.mkdir()
        (old_dir / "lib" / "loop.so").symlink_to("loop.so")

        with pytest.raises(SnapshotError, match="symlink loop"):
            compare_product_directories(old_dir, new_dir)


class TestCompareProductDirectoriesHeaderRootsContainment:
    def test_absolute_header_root_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A caller-supplied absolute or escaping header root is a caller/
        # config error and must be rejected outright -- silently dropping
        # it and still running the per-library compare without that
        # side's header evidence risks a false-green result for an API/
        # header-only break the header evidence would have caught (Codex
        # review, fresh evidence: this test's own name predates the actual
        # rejection -- it previously only asserted the silent-drop
        # behavior it claims to reject).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so").write_bytes(b"OLD")
        (new_dir / "lib" / "libfoo.so").write_bytes(b"NEW")

        outside = tmp_path / "outside-etc"
        outside.mkdir()

        from abicheck import bundle as bundle_mod, service_compare_pipeline as scp

        calls: list[dict[str, object]] = []

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            raise SnapshotError("run_compare must never be reached")

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )

        with pytest.raises(SnapshotError, match="absolute or escapes"):
            compare_product_directories(old_dir, new_dir, header_roots=[str(outside)])

        # Rejected before the per-library compare loop ever runs.
        assert calls == []

    def test_escaping_relative_header_root_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so").write_bytes(b"OLD")
        (new_dir / "lib" / "libfoo.so").write_bytes(b"NEW")
        # A real directory one level above old_dir/new_dir -- if
        # containment were not enforced, "../escaped" would resolve here.
        (tmp_path / "escaped").mkdir()

        from abicheck import bundle as bundle_mod, service_compare_pipeline as scp

        calls: list[dict[str, object]] = []

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            raise SnapshotError("run_compare must never be reached")

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )

        with pytest.raises(SnapshotError, match="absolute or escapes"):
            compare_product_directories(old_dir, new_dir, header_roots=["../escaped"])

        assert calls == []

    def test_missing_header_root_is_silently_skipped_not_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A well-formed, contained root that simply doesn't exist for a
        # given library is tolerated -- that library legitimately ships
        # no public headers here -- distinct from the structurally
        # invalid (absolute/escaping) case rejected above.
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (old_dir / "lib" / "libfoo.so").write_bytes(b"OLD")
        (new_dir / "lib" / "libfoo.so").write_bytes(b"NEW")

        from abicheck import bundle as bundle_mod, service_compare_pipeline as scp

        calls: list[dict[str, object]] = []

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            raise SnapshotError("stop before a real compare runs")

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )

        with pytest.raises(SnapshotError, match="stop before"):
            compare_product_directories(
                old_dir, new_dir, header_roots=["include/nonexistent"]
            )

        assert len(calls) == 1
        assert calls[0]["old_headers"] == []
        assert calls[0]["new_headers"] == []

    def test_invalid_header_root_is_rejected_with_no_matched_pairs(
        self, tmp_path: Path
    ) -> None:
        # _resolved_headers' own containment check only runs for a root a
        # *matched* library pair actually asks for -- two empty
        # directories (or two directories with no library in common) never
        # reach that loop at all, so an absolute/escaping header root was
        # previously silently accepted and the comparison returned
        # NO_CHANGE instead of the documented SnapshotError (Codex review,
        # fresh evidence). No mocking needed: this must be rejected before
        # any discovery/compare machinery runs.
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        with pytest.raises(SnapshotError, match="absolute or escapes"):
            compare_product_directories(old_dir, new_dir, header_roots=["/etc"])

    def test_invalid_root_for_an_unmatched_mapping_key_is_rejected(
        self, tmp_path: Path
    ) -> None:
        # A per-library header_roots mapping key naming a library that
        # never ends up in `pairs` (misspelled, or genuinely absent from
        # this comparison) can still declare an invalid root --
        # _resolved_headers' per-pair lookup can never reach that key, so
        # only validating the whole spec up front catches it (Codex
        # review, fresh evidence).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        with pytest.raises(SnapshotError, match="absolute or escapes"):
            compare_product_directories(
                old_dir,
                new_dir,
                header_roots={"never/matched.so": ["/etc"]},
            )


class TestCompareProductDirectoriesStandaloneRemoval:
    def test_reports_a_library_removed_with_no_surviving_consumer(
        self, tmp_path: Path
    ) -> None:
        # compare_bundle()'s own BUNDLE_LIBRARY_REMOVED detection only
        # fires when a surviving sibling imports the removed library --
        # a standalone removal (this library API's only entry point, no
        # --fail-on-removed-library equivalent) previously vanished
        # entirely: NO_CHANGE, no findings (Codex review, fresh evidence).
        # No mocking here -- exercises the real build_bundle_snapshot()/
        # compare_bundle() machinery end to end; the discovered "library"
        # content doesn't need to be real ELF, since the standalone-
        # removal computation is based on raw discovery, not on whether
        # ELF parsing succeeded.
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "lib").mkdir(parents=True)
        new_dir.mkdir(parents=True)
        (old_dir / "lib" / "liba.so").write_bytes(b"not-real-elf")

        result = compare_product_directories(old_dir, new_dir)

        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        assert len(removed) == 1
        assert removed[0].provider_library == "liba.so"

    def test_reports_removal_of_one_of_two_duplicate_basename_libraries(
        self, tmp_path: Path
    ) -> None:
        # Old has plugins/a/plugin.so AND plugins/b/plugin.so; new retains
        # only plugins/a/plugin.so. old_bundle_map/new_bundle_map (bare-
        # filename-keyed, last-wins) both collapse to a single "plugin.so"
        # key, so a naive bundle-map set difference found nothing removed
        # even though a whole library plainly vanished. Detection is now
        # based on old_map/new_map (relative-path-keyed, collision-free)
        # crossed against the actual pair identities instead (Codex review,
        # fresh evidence).
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories
        from tests.test_package import _make_minimal_elf_so

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "plugins" / "a").mkdir(parents=True)
        (old_dir / "plugins" / "b").mkdir(parents=True)
        (new_dir / "plugins" / "a").mkdir(parents=True)
        # plugins/a/plugin.so is an exact-path match on both sides -- real
        # ELF content, since it's paired and run through run_compare().
        # plugins/b/plugin.so has no counterpart at all and is never
        # parsed, so fake content is fine for it.
        _make_minimal_elf_so(old_dir / "plugins" / "a" / "plugin.so")
        _make_minimal_elf_so(new_dir / "plugins" / "a" / "plugin.so")
        (old_dir / "plugins" / "b" / "plugin.so").write_bytes(b"PLUGIN-B")

        result = compare_product_directories(old_dir, new_dir)

        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        assert len(removed) == 1
        assert removed[0].provider_library == "plugin.so"

    def test_reports_both_removals_when_two_duplicate_basename_libraries_vanish(
        self, tmp_path: Path
    ) -> None:
        # Old has plugins/a/plugin.so AND plugins/b/plugin.so; new has
        # neither -- both libraries are genuinely unmatched. Projecting
        # unmatched *keys* down to a set of bare names before iterating
        # collapsed both distinct relative-path identities to the single
        # string "plugin.so", so only one finding was emitted even though
        # two libraries vanished (Codex review, fresh evidence -- the
        # sibling of test_reports_removal_of_one_of_two_duplicate_basename_
        # libraries above, which only has one unmatched key and so could
        # not have caught this).
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "plugins" / "a").mkdir(parents=True)
        (old_dir / "plugins" / "b").mkdir(parents=True)
        new_dir.mkdir(parents=True)
        (old_dir / "plugins" / "a" / "plugin.so").write_bytes(b"PLUGIN-A")
        (old_dir / "plugins" / "b" / "plugin.so").write_bytes(b"PLUGIN-B")

        result = compare_product_directories(old_dir, new_dir)

        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        assert len(removed) == 2
        assert all(f.provider_library == "plugin.so" for f in removed)

    def test_canonical_fallback_pair_is_not_reported_as_a_removal(
        self, tmp_path: Path
    ) -> None:
        # A library matched via the canonical (SONAME-major-stripped)
        # fallback -- not an exact relative-path match -- has bare
        # filenames that genuinely differ between the old and new bundle
        # maps (libfoo.so.1 vs libfoo.so.2). A naive bundle-map set
        # difference read that as a standalone removal even though the
        # per-library ABI comparison already ran for the very same
        # library (Codex review, fresh evidence).
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories
        from tests.test_package import _make_minimal_elf_so

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _make_minimal_elf_so(old_dir / "libfoo.so.1")
        _make_minimal_elf_so(new_dir / "libfoo.so.2")

        result = compare_product_directories(old_dir, new_dir)

        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        assert removed == []

    def test_case_only_dll_rename_is_not_reported_as_a_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Identical mechanism to the SONAME-major case above, reached via
        # a Windows-shaped case-only rename instead: Foo.dll -> foo.dll
        # pairs via the canonical (case-insensitive) fallback, but their
        # bare filenames differ, so the naive set difference reported it
        # as a removal (Codex review, fresh evidence). Building a PE binary
        # complete enough for real PE metadata parsing to succeed is not
        # this test's concern -- run_compare() is stubbed out so only the
        # standalone-removal exclusion logic under test actually runs;
        # _discover_library_map()/the canonical-fallback pairing (what this
        # test exercises) still run for real, unmocked.
        from abicheck import service_compare_pipeline
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import DiffResult
        from abicheck.product_baseline import compare_product_directories

        class _FakeCompareResult:
            def __init__(self, library: str) -> None:
                self.diff = DiffResult(old_version="", new_version="", library=library)

        def _fake_run_compare(old_path, new_path, **kwargs):  # type: ignore[no-untyped-def]
            return _FakeCompareResult(Path(new_path).name)

        monkeypatch.setattr(service_compare_pipeline, "run_compare", _fake_run_compare)

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "Foo.dll").write_bytes(b"MZ-not-a-real-pe")
        (new_dir / "foo.dll").write_bytes(b"MZ-not-a-real-pe")

        result = compare_product_directories(old_dir, new_dir)

        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        assert removed == []

    def test_does_not_double_report_a_sibling_consumed_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When compare_bundle() already reported a removal (a surviving
        # sibling imports it), the standalone-removal pass must not add
        # a second, duplicate finding for the same library.
        from abicheck import bundle as bundle_mod
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        class _FakeFinding:
            def __init__(self, provider_library: str) -> None:
                self.kind = ChangeKind.BUNDLE_LIBRARY_REMOVED
                self.provider_library = provider_library

        class _FakeResult:
            def __init__(self) -> None:
                self.bundle_findings = [_FakeFinding("liba.so")]

        def _fake_discover_library_map(root, *, include_private):  # type: ignore[no-untyped-def]
            # Compare the resolved Path against old_dir directly, not a
            # substring match on str(root) -- on macOS the pytest tmp
            # directory is under /private/var/folders/..., where "folders"
            # itself contains the substring "old", so a naive `"old" in
            # str(root)` check matched *both* sides (old_dir and new_dir
            # alike), silently pairing a library whose files were never
            # written to disk and blowing up in real ELF-format detection
            # instead of exercising the mocked path this test is about
            # (macOS CI, fresh evidence).
            if Path(root) == old_dir:
                return {"lib/liba.so": root / "lib" / "liba.so"}
            return {}

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            return _FakeResult()

        from abicheck import product_baseline as pb

        monkeypatch.setattr(pb, "_discover_library_map", _fake_discover_library_map)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)

        result = compare_product_directories(old_dir, new_dir)

        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        assert len(removed) == 1

    def test_a_real_second_removal_sharing_a_reported_basename_is_not_suppressed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two distinct unmatched libraries share a basename
        # (plugins/a/plugin.so, plugins/b/plugin.so). compare_bundle()'s
        # own detection reports a removal for the bare name "plugin.so"
        # (it only ever sees the *survivor* of old_bundle_map's own
        # last-wins collapse -- here plugins/b/plugin.so, inserted last).
        # A bare-name membership check alone would suppress BOTH unmatched
        # keys from the standalone-removal fallback, including
        # plugins/a/plugin.so, which compare_bundle() never analyzed at
        # all (the collapse discarded it before compare_bundle() ever saw
        # it) -- silently losing a real, distinct removal (Codex review,
        # fresh evidence).
        from abicheck import bundle as bundle_mod
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        class _FakeFinding:
            def __init__(self, provider_library: str) -> None:
                self.kind = ChangeKind.BUNDLE_LIBRARY_REMOVED
                self.provider_library = provider_library

        class _FakeResult:
            def __init__(self) -> None:
                # Simulates compare_bundle() reporting a removal for the
                # bare name -- referring to whichever path survived the
                # collapse (plugins/b/plugin.so, inserted last below).
                self.bundle_findings = [_FakeFinding("plugin.so")]

        def _fake_discover_library_map(root, *, include_private):  # type: ignore[no-untyped-def]
            if Path(root) == old_dir:
                return {
                    "plugins/a/plugin.so": root / "plugins" / "a" / "plugin.so",
                    "plugins/b/plugin.so": root / "plugins" / "b" / "plugin.so",
                }
            return {}

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            return _FakeResult()

        from abicheck import product_baseline as pb

        monkeypatch.setattr(pb, "_discover_library_map", _fake_discover_library_map)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)

        result = compare_product_directories(old_dir, new_dir)

        removed = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED
        ]
        # One from the compare_bundle() mock (plugins/b's survivor) plus
        # one standalone finding for plugins/a, which was never reported.
        assert len(removed) == 2


class TestCompareProductDirectoriesBundleMapAliasNormalization:
    def test_dev_symlink_representative_mismatch_shares_one_bundle_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reported scenario: an unchanged ELF provider is represented by a
        # dev symlink `libfoo.so -> libfoo.so.1` in the old tree, but the
        # symlink is absent in the new tree (only the real file remains).
        # _discover_library_map's (dev, ino) dedup then picks a *different*
        # bare-name representative on each side -- "libfoo.so" (the
        # symlink) on the old side, "libfoo.so.1" (the real file) on the
        # new side -- even though the canonical fallback (below) correctly
        # pairs them as one library. old_bundle_map/new_bundle_map were
        # built independently of that pairing, straight off each side's own
        # discovery representative, so they disagreed on this library's
        # bundle identity: present as "libfoo.so" only in old_snapshot,
        # "libfoo.so.1" only in new_snapshot. compare_bundle()'s own real,
        # unmocked snapshot diff read that as a removal+addition (or
        # BREAKING, when a surviving sibling imports the provider) for a
        # library that never actually changed (Codex review, fresh
        # evidence, reproduced with an unchanged libbar.so.1 importing
        # foo). Simulating the dedup representative mismatch directly via
        # _discover_library_map (rather than a real symlink) isolates the
        # bundle-map normalization under test from filesystem/platform
        # symlink semantics -- the canonical-fallback pairing this fix
        # relies on runs for real, unmocked.
        from abicheck import bundle as bundle_mod, product_baseline as pb
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_path = old_dir / "libfoo.so"
        new_path = new_dir / "libfoo.so.1"
        old_path.write_bytes(b"OLD")
        new_path.write_bytes(b"NEW")

        def _fake_discover_library_map(root, *, include_private):  # type: ignore[no-untyped-def]
            if Path(root) == old_dir:
                return {"libfoo.so": old_path}
            return {"libfoo.so.1": new_path}

        monkeypatch.setattr(pb, "_discover_library_map", _fake_discover_library_map)

        class _FakeDiffResult:
            def __init__(self, library: str) -> None:
                self.library = library

        class _FakeResult:
            def __init__(self, library: str) -> None:
                self.diff = _FakeDiffResult(library)

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            return _FakeResult(Path(old_lib).name)

        build_calls: list[dict[str, object]] = []

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            build_calls.append(dict(libraries))
            return {"libraries": dict(libraries)}

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            return type("R", (), {"bundle_findings": []})()

        from abicheck import service_compare_pipeline as scp

        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)

        compare_product_directories(old_dir, new_dir)

        assert len(build_calls) == 2
        old_call, new_call = build_calls
        # Both sides must agree on one bundle identity for the paired
        # library -- the new side's bare filename, matching the
        # DiffResult.library convention this module already established.
        assert set(old_call) == {"libfoo.so.1"}
        assert set(new_call) == {"libfoo.so.1"}
        assert old_call["libfoo.so.1"] == old_path
        assert new_call["libfoo.so.1"] == new_path

    def test_rekey_does_not_evict_an_unrelated_occupant_of_the_target_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A canonically-paired library's rekey target (the new side's bare
        # filename) can already be occupied in old_bundle_map by a
        # *different*, unrelated old-side library -- rekeying onto it would
        # silently evict that occupant from old_snapshot entirely
        # (CodeRabbit review, fresh evidence). This is reachable despite
        # _canonical_groups' own ambiguity gate (which normally blocks two
        # same-canonical-family old-side entries from ever being paired at
        # all) because the occupant here reaches the *same final bare
        # name* through a genuinely *different* canonical family:
        # old/a/libfoo.so canonically pairs with new/c/libfoo.so.1 under
        # the ELF ".so"-family key "libfoo.so", while old/b/libfoo.so.1 is
        # a real PE image (oddly named without a .dll extension) whose own
        # canonical key is the PE-content-derived "libfoo.so.1" -- a
        # separate bucket old_canonical never treats as ambiguous with
        # "libfoo.so", yet whose *bare filename* is exactly the pairing's
        # rekey target.
        from abicheck import bundle as bundle_mod, product_baseline as pb
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        (old_dir / "a").mkdir()
        (old_dir / "b").mkdir()
        (new_dir / "c").mkdir()

        old_a = old_dir / "a" / "libfoo.so"
        old_a.write_bytes(b"not-real-elf")

        # Real PE content (MZ + PE\0\0 + COFF header with IMAGE_FILE_DLL
        # set) so _pe_is_dll_content recognizes it despite the ".so.1"
        # name -- this is what puts it in its own canonical bucket.
        dos_header = bytearray(64)
        dos_header[0:2] = b"MZ"
        struct.pack_into("<I", dos_header, 0x3C, 64)
        coff_header = struct.pack("<HHIIIHH", 0x8664, 0, 0, 0, 0, 0, 0x2000)
        pe_bytes = bytes(dos_header) + b"PE\x00\x00" + coff_header
        old_b = old_dir / "b" / "libfoo.so.1"
        old_b.write_bytes(pe_bytes)

        new_c = new_dir / "c" / "libfoo.so.1"
        new_c.write_bytes(b"not-real-elf-either")

        def _fake_discover_library_map(root, *, include_private):  # type: ignore[no-untyped-def]
            if Path(root) == old_dir:
                return {"a/libfoo.so": old_a, "b/libfoo.so.1": old_b}
            return {"c/libfoo.so.1": new_c}

        monkeypatch.setattr(pb, "_discover_library_map", _fake_discover_library_map)

        class _FakeDiffResult:
            def __init__(self, library: str) -> None:
                self.library = library

        class _FakeResult:
            def __init__(self, library: str) -> None:
                self.diff = _FakeDiffResult(library)

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            return _FakeResult(Path(old_lib).name)

        build_calls: list[dict[str, object]] = []

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            build_calls.append(dict(libraries))
            return {"libraries": dict(libraries)}

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            return type("R", (), {"bundle_findings": []})()

        from abicheck import service_compare_pipeline as scp

        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)

        compare_product_directories(old_dir, new_dir)

        assert len(build_calls) == 2
        old_call, _new_call = build_calls
        # The rekey is skipped (target already occupied), so old/a/libfoo.so
        # stays under its own bare name -- AND the unrelated PE occupant
        # under "libfoo.so.1" survives untouched.
        assert old_call.get("libfoo.so") == old_a
        assert old_call.get("libfoo.so.1") == old_b


class TestCompareProductDirectoriesStandaloneAddition:
    def test_reports_a_non_elf_library_addition(self, tmp_path: Path) -> None:
        # compare_bundle()'s own BUNDLE_LIBRARY_ADDED detection reads new
        # library names straight off BundleSnapshot.libraries, which
        # build_bundle_snapshot() only ever populates from ELF metadata --
        # a new DLL/dylib added to the product never reaches that
        # detection at all, so an empty old directory versus a new
        # directory containing foo.dll previously returned NO_CHANGE with
        # no findings (Codex review, fresh evidence). Symmetric to the
        # standalone-removal fallback above.
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir(parents=True)
        (new_dir / "lib").mkdir(parents=True)
        (new_dir / "lib" / "foo.dll").write_bytes(b"MZ-not-a-real-pe")

        result = compare_product_directories(old_dir, new_dir)

        added = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_ADDED
        ]
        assert len(added) == 1
        assert added[0].provider_library == "foo.dll"

    def test_canonical_fallback_pair_is_not_reported_as_an_addition(
        self, tmp_path: Path
    ) -> None:
        # Symmetric to the removal side's identical guard: a library
        # matched via the canonical fallback has bare filenames that
        # genuinely differ (a SONAME major bump, a case-only rename), and
        # must not double-report as an addition on top of already being a
        # compared pair.
        from abicheck.checker_policy import ChangeKind
        from abicheck.product_baseline import compare_product_directories
        from tests.test_package import _make_minimal_elf_so

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _make_minimal_elf_so(old_dir / "libfoo.so.1")
        _make_minimal_elf_so(new_dir / "libfoo.so.2")

        result = compare_product_directories(old_dir, new_dir)

        added = [
            f
            for f in result.bundle_findings
            if f.kind == ChangeKind.BUNDLE_LIBRARY_ADDED
        ]
        assert added == []


class TestRootsForLibraryRejectsBareStrings:
    def test_flat_bare_string_raises(self) -> None:
        # A `str` satisfies the declared `Sequence[str]` type, so
        # header_roots="include" previously returned unchanged and was
        # iterated character-by-character by the caller -- every
        # single-character candidate failing .is_dir(), silently running
        # the comparison with zero header evidence (Codex/CodeRabbit
        # review, fresh evidence).
        from abicheck.errors import SnapshotError
        from abicheck.product_baseline import _roots_for_library

        with pytest.raises(SnapshotError, match="bare string"):
            _roots_for_library("include", "liba.so")

    def test_mapping_value_bare_string_raises(self) -> None:
        from abicheck.errors import SnapshotError
        from abicheck.product_baseline import _roots_for_library

        with pytest.raises(SnapshotError, match="bare string"):
            _roots_for_library({"liba.so": "include"}, "liba.so")


class TestDiscoverLibraryMapExtensionlessElf:
    def test_extensionless_elf_dso_is_discovered(self, tmp_path: Path) -> None:
        # A real ELF ET_DYN shared object with no conventional .so suffix
        # (a plugin named without one -- real on Linux) previously
        # dropped out of discovery entirely, since _is_shared_library is
        # a filename-only check (Codex review, fresh evidence: two
        # changed products silently comparing as NO_CHANGE).
        from abicheck.product_baseline import _discover_library_map
        from tests.test_package import _make_minimal_elf_so

        root = tmp_path / "product"
        (root / "lib").mkdir(parents=True)
        _make_minimal_elf_so(root / "lib" / "plugin_no_suffix")

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"lib/plugin_no_suffix"}

    def test_non_elf_extensionless_file_is_not_discovered(self, tmp_path: Path) -> None:
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "README").write_bytes(b"not a library at all")

        result = _discover_library_map(root, include_private=True)
        assert result == {}

    def test_split_debug_elf_sidecar_is_not_discovered_as_a_library(
        self, tmp_path: Path
    ) -> None:
        # A real objcopy --only-keep-debug sidecar (libfoo.so.1.debug) is
        # itself a valid ELF file that retains its original binary's
        # ET_DYN header -- the content-aware ELF fallback above would
        # otherwise discover it as a second, independent "library"
        # alongside the real DSO it was split from (Codex review, fresh
        # evidence).
        from abicheck.product_baseline import _discover_library_map
        from tests.test_package import _make_minimal_elf_so

        root = tmp_path / "product"
        (root / "lib").mkdir(parents=True)
        _make_minimal_elf_so(root / "lib" / "libfoo.so.1")
        _make_minimal_elf_so(root / "lib" / "libfoo.so.1.debug")

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"lib/libfoo.so.1"}


class TestDiscoverLibraryMapExtensionlessMachOAndPe:
    """The Mach-O/PE counterpart of the ELF extensionless-DSO class above:
    a real Mach-O dylib/bundle or a real PE DLL under a nonstandard
    extension (a macOS framework binary, a Python .pyd) previously never
    entered discovery at all -- the ELF content fallback had no
    counterpart for either format (Codex review, fresh evidence)."""

    def test_extensionless_macho_dylib_is_discovered(self, tmp_path: Path) -> None:
        # Mirrors a macOS framework binary's own convention
        # (Foo.framework/Foo, no suffix at all).
        from abicheck.product_baseline import _discover_library_map
        from tests.test_cross_platform_fixtures import _make_minimal_macho

        root = tmp_path / "product"
        framework = root / "Foo.framework"
        framework.mkdir(parents=True)
        _make_minimal_macho(framework, filename="Foo")

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"Foo.framework/Foo"}

    def test_extensionless_macho_be_dylib_is_discovered(self, tmp_path: Path) -> None:
        from abicheck.product_baseline import _discover_library_map
        from tests.test_cross_platform_fixtures import _make_minimal_macho_be

        root = tmp_path / "product"
        root.mkdir(parents=True)
        macho_be = _make_minimal_macho_be(root)
        macho_be.rename(root / "libbe_no_suffix")

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"libbe_no_suffix"}

    def test_macho_executable_is_not_discovered(self, tmp_path: Path) -> None:
        # MH_EXECUTE (2), not MH_DYLIB/MH_BUNDLE/MH_DYLIB_STUB -- a plain
        # executable is not a shared library regardless of content.
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        root.mkdir(parents=True)
        header = struct.pack(
            "<IIIIIIII",
            0xFEEDFACF,  # magic (MH_MAGIC_64)
            0x01000007,  # cputype (CPU_TYPE_X86_64)
            0x00000003,  # cpusubtype
            0x00000002,  # filetype (MH_EXECUTE)
            0,
            0,
            0,
            0,
        )
        (root / "an_executable").write_bytes(header)

        result = _discover_library_map(root, include_private=True)
        assert result == {}

    def test_pyd_extension_module_is_discovered_by_content(
        self, tmp_path: Path
    ) -> None:
        # A Python extension module shipped as .pyd is a real PE DLL under
        # a nonstandard extension -- IMAGE_FILE_DLL alone identifies it,
        # not the suffix.
        from abicheck.product_baseline import _discover_library_map
        from tests.test_cross_platform_fixtures import _make_minimal_pe

        root = tmp_path / "product"
        root.mkdir(parents=True)
        dll = _make_minimal_pe(root)
        pyd = root / "extension.pyd"
        pyd.write_bytes(dll.read_bytes())
        dll.unlink()

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"extension.pyd"}

    def test_pe_executable_is_not_discovered(self, tmp_path: Path) -> None:
        # Same DOS/PE header shape as _make_minimal_pe, but Characteristics
        # has no IMAGE_FILE_DLL bit set -- an ordinary .exe.
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        root.mkdir(parents=True)
        dos_header = bytearray(64)
        dos_header[0:2] = b"MZ"
        pe_offset = 64
        struct.pack_into("<I", dos_header, 0x3C, pe_offset)
        coff_header = struct.pack("<HHIIIHH", 0x8664, 0, 0, 0, 0, 0, 0x0002)
        (root / "an_executable").write_bytes(
            bytes(dos_header) + b"PE\x00\x00" + coff_header
        )

        result = _discover_library_map(root, include_private=True)
        assert result == {}

    @staticmethod
    def _make_fat_macho(*, filetype: int) -> bytes:
        """A minimal, real fat/universal Mach-O archive with one slice --
        big-endian fat_header + one fat_arch entry pointing at a thin
        mach_header_64 with the given *filetype*. Real bytes, verified
        parseable by macholib.MachO directly (not a description of the
        format)."""
        thin = struct.pack(
            ">IIIIIIII",
            0xFEEDFACF,  # MH_MAGIC_64
            0x01000007,  # cputype (CPU_TYPE_X86_64)
            0x00000003,  # cpusubtype
            filetype,
            0,
            0,
            0,
            0,
        )
        fat_header = struct.pack(">II", 0xCAFEBABE, 1)  # magic, nfat_arch=1
        arch_offset = 8 + 20  # fat_header(8) + one fat_arch(20)
        fat_arch = struct.pack(
            ">IIIII", 0x01000007, 0x00000003, arch_offset, len(thin), 0
        )
        return fat_header + fat_arch + thin

    def test_extensionless_fat_macho_dylib_is_discovered(self, tmp_path: Path) -> None:
        # A universal framework binary (x86_64 + arm64 in one file, the
        # common real-world shape for a distributed macOS framework) has
        # fat magic, not a thin one -- the thin-only header read alone
        # can't classify it (Codex review, fresh evidence).
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        framework = root / "Foo.framework"
        framework.mkdir(parents=True)
        (framework / "Foo").write_bytes(self._make_fat_macho(filetype=6))  # MH_DYLIB

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"Foo.framework/Foo"}

    def test_fat_macho_executable_is_not_discovered(self, tmp_path: Path) -> None:
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        root.mkdir(parents=True)
        (root / "an_executable").write_bytes(
            self._make_fat_macho(filetype=2)
        )  # MH_EXECUTE

        result = _discover_library_map(root, include_private=True)
        assert result == {}

    def test_malformed_fat_macho_is_not_discovered(self, tmp_path: Path) -> None:
        # A truncated/malformed fat archive must degrade to "not a
        # library" (best-effort discovery), never raise out of the
        # directory walk.
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        root.mkdir(parents=True)
        (root / "broken").write_bytes(struct.pack(">II", 0xCAFEBABE, 1))  # truncated

        result = _discover_library_map(root, include_private=True)
        assert result == {}


class TestDiscoverLibraryMapSymlinkContainment:
    def test_symlink_escaping_the_product_root_is_not_discovered(
        self, tmp_path: Path
    ) -> None:
        # A library-shaped symlink whose target resolves outside root
        # entirely (libfoo.so -> /some/host/path/libfoo.so) was previously
        # discovered and later opened by run_compare() through the symlink,
        # silently analyzing a host file that is no part of the product at
        # all -- machine-dependent results, and a potential way to hide a
        # library the product should have shipped but didn't. Rejected the
        # same way pack/unpack already reject an escaping manifest path
        # (Codex review, fresh evidence).
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "lib").mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "libfoo.so").write_bytes(b"HOST-CONTENT")
        (root / "lib" / "libfoo.so").symlink_to(outside / "libfoo.so")

        result = _discover_library_map(root, include_private=True)
        assert result == {}

    def test_in_tree_symlink_across_directories_is_still_discovered(
        self, tmp_path: Path
    ) -> None:
        # The containment check must not reject an ordinary in-tree
        # symlink -- only one whose resolved target actually escapes root.
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "a_real").mkdir(parents=True)
        (root / "z_alias").mkdir(parents=True)
        target = root / "a_real" / "libfoo.so"
        target.write_bytes(b"REAL-CONTENT")
        (root / "z_alias" / "libfoo.so").symlink_to(target)

        # Both the real file and the alias resolve within root, so exactly
        # one survives (the deterministic-walk-order dedup this class's
        # sibling already covers, unaffected by the containment check
        # here) -- "a_real" sorts first.
        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"a_real/libfoo.so"}

    def test_self_referential_symlink_loop_raises_snapshot_error(
        self, tmp_path: Path
    ) -> None:
        # A library-shaped self-referential symlink (loop.so -> loop.so)
        # first made Path.resolve() raise RuntimeError unhandled (fixed:
        # the earlier revision of this test pinned the resulting
        # tolerant behavior -- silently discovering the loop as though
        # it were a real library). A further review round found that
        # tolerance itself wrong: comparing a product containing nothing
        # but a loop against an empty one produced a spurious
        # bundle_library_removed/added finding instead of the invalid
        # product being rejected outright (Codex review, fresh
        # evidence). A loop is not a real library -- reject it.
        from abicheck.errors import SnapshotError
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "loop.so").symlink_to("loop.so")

        with pytest.raises(SnapshotError, match="symlink loop"):
            _discover_library_map(root, include_private=True)

    def test_dangling_symlink_is_silently_not_discovered(self, tmp_path: Path) -> None:
        # A library-shaped symlink with a missing target made real.stat()
        # raise ENOENT, which the ELOOP-only branch above fell through to
        # `identity = real` for -- still yielding the dangling path as a
        # discovered library, so a product containing only such an alias
        # against an empty one produced a spurious bundle_library_removed
        # finding, the same failure mode already fixed for a genuine
        # symlink loop just above (Codex review, fresh evidence).
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "libgone.so").symlink_to("missing.so")

        result = _discover_library_map(root, include_private=True)
        assert result == {}


class TestDiscoverLibraryMapDeterministicWalkOrder:
    def test_symlink_alias_survivor_is_stable_regardless_of_walk_order(
        self, tmp_path: Path
    ) -> None:
        # os.walk() yields sibling directories in filesystem order, which
        # is unspecified -- if a symlink alias and its target live in
        # different directories, the surviving representative for their
        # shared identity previously depended on which directory the walk
        # reached first (CodeRabbit review). Sorting both dirnames and
        # filenames makes the discovered key deterministic: with "a" and
        # "z" both sorting the same way regardless of the order the
        # filesystem happens to report them in, "a/target.so" must always
        # be the survivor over "z/alias.so".
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "a").mkdir(parents=True)
        (root / "z").mkdir(parents=True)
        target = root / "a" / "target.so"
        target.write_bytes(b"REAL-CONTENT")
        (root / "z" / "alias.so").symlink_to(target)

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"a/target.so"}

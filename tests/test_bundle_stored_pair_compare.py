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

"""Unit tests for :mod:`abicheck.workflows.bundle_stored_pair_compare` (CLI
cleanup phase two, PR I's stored/stored driver).

No binaries, no header AST, no live extraction on either side -- both sides
are already plain, hand-built ``AbiSnapshot``s, so unlike
``TestCompareReleaseAgainstBundleFacts`` in ``test_bundle_side_input.py``
(``@pytest.mark.integration``, needs gcc), this file needs no compiler at
all. Mirrors that file's own fixture style.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle_facts import capture_bundle_facts
from abicheck.checker_policy import Verdict
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import save_bundle_facts
from abicheck.workflows.bundle_stored_pair_compare import (
    compare_stored_bundle_facts_pair,
)


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    syms = [ElfSymbol(name=name, visibility="default") for name in exports or []]
    imps = [ElfImport(name=name) for name in imports or []]
    return ElfMetadata(soname=soname or "", needed=needed or [], symbols=syms, imports=imps)


def _per_library_snapshots(metadata: dict[str, ElfMetadata]) -> dict[str, AbiSnapshot]:
    return {
        name: AbiSnapshot(library=name, version="old", elf=meta)
        for name, meta in metadata.items()
    }


class TestCompareStoredBundleFactsPair:
    def _facts_path(
        self,
        tmp_path: Path,
        name: str,
        version: str,
        visibility: Visibility,
        *,
        variant_fingerprint: str | None = None,
    ) -> Path:
        fn = Function(
            name="core_fn", mangled="core_fn", return_type="int", visibility=visibility
        )
        snapshot = AbiSnapshot(
            library="libcore.so",
            version=version,
            elf=_meta(soname="libcore.so", exports=["core_fn"]),
            functions=[fn],
        )
        kwargs = {} if variant_fingerprint is None else {"variant_fingerprint": variant_fingerprint}
        facts = capture_bundle_facts({"libcore.so": snapshot}, **kwargs)
        path = tmp_path / name
        save_bundle_facts(facts, path)
        return path

    def test_visibility_change_is_detected_with_no_binaries_read(
        self, tmp_path: Path
    ) -> None:
        old_path = self._facts_path(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = self._facts_path(
            tmp_path, "new.bundlefacts.json", "new", Visibility.HIDDEN
        )

        result = compare_stored_bundle_facts_pair(old_path, new_path)

        assert [d.library for d in result.per_library] == ["libcore.so"]
        assert result.per_library[0].changes
        assert result.verdict != Verdict.NO_CHANGE

    def test_unchanged_pair_is_no_change(self, tmp_path: Path) -> None:
        old_path = self._facts_path(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = self._facts_path(
            tmp_path, "new.bundlefacts.json", "new", Visibility.PUBLIC
        )

        result = compare_stored_bundle_facts_pair(old_path, new_path)

        assert [d.library for d in result.per_library] == ["libcore.so"]
        assert result.verdict == Verdict.NO_CHANGE

    def test_library_present_on_only_one_side_is_not_diffed(
        self, tmp_path: Path
    ) -> None:
        old_metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
            "libonly_old.so": _meta(soname="libonly_old.so", exports=["only_old_fn"]),
        }
        new_metadata = {
            "libcore.so": _meta(soname="libcore.so", exports=["core_fn"]),
            "libonly_new.so": _meta(soname="libonly_new.so", exports=["only_new_fn"]),
        }
        old_path = tmp_path / "old.bundlefacts.json"
        new_path = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(
            capture_bundle_facts(_per_library_snapshots(old_metadata)), old_path
        )
        save_bundle_facts(
            capture_bundle_facts(_per_library_snapshots(new_metadata)), new_path
        )

        result = compare_stored_bundle_facts_pair(old_path, new_path)

        assert [d.library for d in result.per_library] == ["libcore.so"]

    def test_policy_file_override_reaches_the_per_library_diff(
        self, tmp_path: Path
    ) -> None:
        """A real ``overrides`` rule must reach each per-library
        ``service.compare_snapshots()`` call, not just a bare *policy*
        string."""
        from abicheck.policy_file import PolicyFile

        old_path = self._facts_path(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = self._facts_path(
            tmp_path, "new.bundlefacts.json", "new", Visibility.HIDDEN
        )
        baseline = compare_stored_bundle_facts_pair(old_path, new_path)
        assert baseline.verdict != Verdict.NO_CHANGE
        demoted_kind = baseline.per_library[0].changes[0].kind

        policy_file = PolicyFile(overrides={demoted_kind: Verdict.NO_CHANGE})
        result = compare_stored_bundle_facts_pair(
            old_path, new_path, policy_file=policy_file
        )
        assert result.verdict != baseline.verdict
        assert result.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)

    def test_mismatched_variant_fingerprint_is_refused(self, tmp_path: Path) -> None:
        """Two documents captured from different logical build variants
        (e.g. a CPU-only build vs. a SYCL/DPC build) must never be silently
        diffed as if they were the same build -- Codex review, PR #1060."""
        old_path = self._facts_path(
            tmp_path,
            "old.bundlefacts.json",
            "old",
            Visibility.PUBLIC,
            variant_fingerprint="cpu",
        )
        new_path = self._facts_path(
            tmp_path,
            "new.bundlefacts.json",
            "new",
            Visibility.PUBLIC,
            variant_fingerprint="sycl",
        )

        with pytest.raises(ValueError, match="different build variants"):
            compare_stored_bundle_facts_pair(old_path, new_path)

    def test_matching_explicit_variant_fingerprint_is_accepted(
        self, tmp_path: Path
    ) -> None:
        """The mismatch check above must not reject two documents that
        genuinely share the same explicit, non-default variant."""
        old_path = self._facts_path(
            tmp_path,
            "old.bundlefacts.json",
            "old",
            Visibility.PUBLIC,
            variant_fingerprint="cpu",
        )
        new_path = self._facts_path(
            tmp_path,
            "new.bundlefacts.json",
            "new",
            Visibility.PUBLIC,
            variant_fingerprint="cpu",
        )

        result = compare_stored_bundle_facts_pair(old_path, new_path)

        assert [d.library for d in result.per_library] == ["libcore.so"]
        assert result.verdict == Verdict.NO_CHANGE

    def test_empty_variant_fingerprint_is_refused_even_when_both_sides_match(
        self, tmp_path: Path
    ) -> None:
        """An empty fingerprint carries no real identity evidence --
        ``variant_fingerprint()`` itself never produces one -- so two
        documents both carrying "" must not be treated as a match just
        because they're equal (Codex review, PR #1060, fresh evidence)."""
        old_path = self._facts_path(
            tmp_path,
            "old.bundlefacts.json",
            "old",
            Visibility.PUBLIC,
            variant_fingerprint="",
        )
        new_path = self._facts_path(
            tmp_path,
            "new.bundlefacts.json",
            "new",
            Visibility.PUBLIC,
            variant_fingerprint="",
        )

        with pytest.raises(ValueError, match="empty variant_fingerprint"):
            compare_stored_bundle_facts_pair(old_path, new_path)

    def test_non_string_variant_fingerprint_is_refused(self, tmp_path: Path) -> None:
        """A malformed ``variant_fingerprint: 1`` must not silently load as
        the string ``"1"`` and then compare equal to a genuine
        ``variant_fingerprint: "1"`` on the other side -- prior to this fix
        the loader (``bundle_facts_serialization.bundle_facts_from_dict``)
        applied a blind ``str(...)`` coercion, so the two were
        indistinguishable by the time they reached this function's own
        mismatch/empty checks (Codex review, PR #1060, round 8). The
        loader now delegates to ``storage.guards.identity_text()`` (round
        11), which raises ``TypeError`` rather than this module's usual
        ``ValueError``."""
        import json

        old_path = tmp_path / "old.bundlefacts.json"
        old_path.write_text(
            json.dumps(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": 2,
                    "per_library_snapshots": {},
                    "variant_fingerprint": 1,
                }
            )
        )
        new_path = self._facts_path(
            tmp_path,
            "new.bundlefacts.json",
            "new",
            Visibility.PUBLIC,
            variant_fingerprint="1",
        )

        with pytest.raises(TypeError, match="variant_fingerprint"):
            compare_stored_bundle_facts_pair(old_path, new_path)

    def test_explicit_null_variant_fingerprint_is_refused_not_defaulted(
        self, tmp_path: Path
    ) -> None:
        """An explicit ``\"variant_fingerprint\": null`` must be rejected,
        not silently treated as an absent key and defaulted -- prior to
        this fix the loader's ``d.get(\"variant_fingerprint\")`` call (no
        default arg) returned ``None`` for both cases alike, so a malformed
        document naming an explicit null could compare as the default
        variant against a genuinely coordinate-less document, bypassing
        the stored-pair identity check entirely (Codex review, PR #1060,
        round 9)."""
        import json

        old_path = tmp_path / "old.bundlefacts.json"
        old_path.write_text(
            json.dumps(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": 2,
                    "per_library_snapshots": {},
                    "variant_fingerprint": None,
                }
            )
        )
        new_path = self._facts_path(
            tmp_path, "new.bundlefacts.json", "new", Visibility.PUBLIC
        )

        with pytest.raises(TypeError, match="variant_fingerprint"):
            compare_stored_bundle_facts_pair(old_path, new_path)

    def test_new_only_manifest_is_not_silently_discarded(self, tmp_path: Path) -> None:
        """When no explicit --manifest is given and only the NEW document
        captured one, it must still be consulted -- compare_bundle_from_
        facts()'s own precedence (explicit, else OLD's manifest) silently
        drops a NEW-only manifest, which would hide a real missing-symbol
        regression (Codex review, PR #1060)."""
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.serialization import save_bundle_facts

        old_path = self._facts_path(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        fn = Function(
            name="core_fn", mangled="core_fn", return_type="int", visibility=Visibility.PUBLIC
        )
        new_snapshot = AbiSnapshot(
            library="libcore.so",
            version="new",
            elf=_meta(soname="libcore.so", exports=["core_fn"]),
            functions=[fn],
        )
        new_facts = capture_bundle_facts(
            {"libcore.so": new_snapshot},
            manifest=InstantiationManifest(
                entries=(ManifestEntry(symbol="promised_but_missing"),)
            ),
        )
        new_path = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(new_facts, new_path)

        result = compare_stored_bundle_facts_pair(old_path, new_path)

        assert any(
            f.kind.name == "BUNDLE_MANIFEST_INSTANTIATION_REMOVED"
            for f in result.bundle_findings
        )

    def test_depth_is_forwarded_to_project_snapshot_to_depth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit *depth* must reach ``policy.depth_projection.
        project_snapshot_to_depth()`` for both sides of every matched
        library, and its *projected* return value -- not the raw stored
        snapshot -- must be what actually reaches ``compare_snapshots()``
        (Codex review, PR #1060, fresh evidence: an earlier version of this
        function rejected --depth binary outright on the mistaken premise
        that no projection primitive existed; this pins the real wiring
        instead of only exercising the ``None``/no-op default every other
        test here uses). Both fixtures carry no header evidence
        (``from_headers`` unset), so ``binary`` is the rung both sides
        genuinely reach -- the floor check ``enforce_requested_depth`` now
        also runs (round 6) does not reject it."""
        old_path = self._facts_path(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = self._facts_path(
            tmp_path, "new.bundlefacts.json", "new", Visibility.HIDDEN
        )

        import abicheck.policy.depth_projection as depth_projection_module

        real_project_snapshot_to_depth = depth_projection_module.project_snapshot_to_depth
        calls: list[tuple[object, str | None]] = []

        def _fake_project_snapshot_to_depth(snap, depth):
            calls.append((snap, depth))
            return real_project_snapshot_to_depth(snap, depth)

        monkeypatch.setattr(
            depth_projection_module,
            "project_snapshot_to_depth",
            _fake_project_snapshot_to_depth,
        )

        compare_stored_bundle_facts_pair(old_path, new_path, depth="binary")

        assert len(calls) == 2
        assert {c[1] for c in calls} == {"binary"}
        assert {c[0].library for c in calls} == {"libcore.so"}

    def test_explicit_depth_is_stamped_onto_each_per_library_result(
        self, tmp_path: Path
    ) -> None:
        """An explicit ``depth`` must reach each per-library ``DiffResult``'s
        own ``requested_depth``/``analysis_assurance.depth_satisfied`` --
        ``service_compare_pipeline.resolve_compare_request()`` stamps both
        after its own floor/ceiling pair, and this driver enforces and
        projects depth the identical way but previously never stamped
        either, so every stored/stored ``--depth`` run persisted them as
        ``None`` despite the evidence contract this driver actually
        enforced (Codex review, PR #1060, round 10). ``None`` (the default,
        no explicit depth) must leave both unset, matching every other
        comparison path's identical "no request, nothing to stamp"
        contract."""
        old_path = self._facts_path(
            tmp_path, "old.bundlefacts.json", "old", Visibility.PUBLIC
        )
        new_path = self._facts_path(
            tmp_path, "new.bundlefacts.json", "new", Visibility.HIDDEN
        )

        unrequested = compare_stored_bundle_facts_pair(old_path, new_path)
        assert unrequested.per_library[0].requested_depth is None
        assert unrequested.per_library[0].analysis_assurance.depth_satisfied is None

        result = compare_stored_bundle_facts_pair(old_path, new_path, depth="binary")
        assert result.per_library[0].requested_depth == "binary"
        assert result.per_library[0].analysis_assurance.depth_satisfied is True

    def test_depth_headers_strips_build_mode_before_diffing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact scenario Codex's round-5 review named: a stored
        document captured with build-mode evidence must reach
        ``compare_snapshots`` with it stripped under an explicit ``--depth
        headers`` (``policy.depth_projection.project_snapshot_to_depth``
        nulls ``build_mode`` below the ``build`` rung), not sent through
        unprojected -- otherwise a build-mode finding could still surface
        and change the reported verdict despite the explicit ``headers``
        ceiling. ``from_headers=True`` so the round-6 floor check
        (``enforce_requested_depth``) accepts ``--depth headers`` -- the
        fixture genuinely reached that rung, it's the *build_mode* ceiling
        this test is pinning, not the floor."""
        from abicheck.model.build_mode_facts import BuildMode, CompilerFamily

        old_snapshot = AbiSnapshot(
            library="libcore.so",
            version="old",
            elf=_meta(soname="libcore.so", exports=["core_fn"]),
            functions=[
                Function(
                    name="core_fn",
                    mangled="core_fn",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            build_mode=BuildMode(compiler_family=CompilerFamily.GCC),
            from_headers=True,
        )
        old_path = tmp_path / "old.bundlefacts.json"
        new_path = tmp_path / "new.bundlefacts.json"
        save_bundle_facts(capture_bundle_facts({"libcore.so": old_snapshot}), old_path)
        save_bundle_facts(capture_bundle_facts({"libcore.so": old_snapshot}), new_path)

        import abicheck.workflows.compare_policy as compare_policy_module

        seen: list[object] = []
        real_compare_snapshots = compare_policy_module.compare_snapshots

        def _spy_compare_snapshots(old, new, *args, **kwargs):
            seen.append(old.build_mode)
            seen.append(new.build_mode)
            return real_compare_snapshots(old, new, *args, **kwargs)

        monkeypatch.setattr(
            compare_policy_module, "compare_snapshots", _spy_compare_snapshots
        )

        compare_stored_bundle_facts_pair(old_path, new_path)
        assert seen == [
            BuildMode(compiler_family=CompilerFamily.GCC),
            BuildMode(compiler_family=CompilerFamily.GCC),
        ]

        seen.clear()
        compare_stored_bundle_facts_pair(old_path, new_path, depth="headers")
        assert seen == [None, None]

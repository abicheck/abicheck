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

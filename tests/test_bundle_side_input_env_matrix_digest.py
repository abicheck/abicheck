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

"""``compare_release_against_bundle_facts`` -- BUNDLE-scope deployment digest
with zero matched/completed library pairs.

Split out of ``tests/test_bundle_side_input.py`` (rather than growing that
file, already at its own 1200-line test-file cap) -- mirrors the release
fan-out's own ``TestReleaseJsonEnvMatrixDigestWithNoCompletedComparison``
(``tests/test_compare_release_env_matrix.py``, Codex review, P2, Finding 5,
round 7) for the stored/live BundleFacts driver instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle_side_input import compare_release_against_bundle_facts
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot
from abicheck.serialization import save_bundle_facts
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts


class TestCompareReleaseAgainstBundleFactsEnvMatrixDigestWithNoCompletedComparison:
    """A declared ``deployment:`` contract must not become indistinguishable
    from "none" just because OLD (stored facts) and NEW (live directory)
    share no matched library. The digest is a property of the *resolved
    matrix*, computed at BUNDLE scope before any per-library compare even
    runs, not of how many per-library comparisons happened to complete."""

    def _old_facts_with_library(self, tmp_path: Path, name: str) -> Path:
        elf = ElfMetadata(soname=name)
        facts = capture_bundle_facts(
            {name: AbiSnapshot(library=name, version="old", elf=elf)}
        )
        facts_path = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, facts_path)
        return facts_path

    def test_carries_the_digest_with_zero_matched_pairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.package as package_mod
        from abicheck.environment_matrix import EnvironmentMatrix

        # OLD facts name a library ("libfoo.so") that NEW's discovered file
        # ("libbar.so") never matches -- build_match_map/degraded/`new_map`
        # produce zero matched keys, so `service.resolve_input` is never
        # even called for this pair, and `per_library` stays empty.
        facts_path = self._old_facts_with_library(tmp_path, "libfoo.so")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_so = new_dir / "libbar.so"
        new_so.write_bytes(b"")
        monkeypatch.setattr(
            package_mod,
            "discover_shared_libraries",
            lambda d, include_private=False: [new_so],
        )

        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        result = compare_release_against_bundle_facts(
            facts_path, new_dir, env_matrix=matrix
        )

        assert result.per_library == [], "fixture precondition: zero matched pairs"
        assert result.env_matrix_source_sha256 is not None
        assert result.env_matrix_source_sha256.startswith("sha256:")

    def test_omits_the_digest_without_a_declared_matrix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.package as package_mod

        facts_path = self._old_facts_with_library(tmp_path, "libfoo.so")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_so = new_dir / "libbar.so"
        new_so.write_bytes(b"")
        monkeypatch.setattr(
            package_mod,
            "discover_shared_libraries",
            lambda d, include_private=False: [new_so],
        )

        result = compare_release_against_bundle_facts(facts_path, new_dir)

        assert result.per_library == [], "fixture precondition: zero matched pairs"
        assert result.env_matrix_source_sha256 is None

# Copyright 2026 Nikolay Petrov
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

"""Cross-platform honesty guard.

Linux/ELF is the CI-validated baseline; macOS and Windows are parser-level and
only partially exercised end-to-end (see docs/reference/platforms.md and
docs/development/usecase-coverage-evaluation.md, gap G1). These tests keep the
``examples/ground_truth.json`` ``platforms`` tags honest about that hierarchy so
a future case cannot silently claim e.g. Windows-only support that CI never
runs.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

_GROUND_TRUTH = Path(__file__).parent.parent / "examples" / "ground_truth.json"
_KNOWN_PLATFORMS = {"linux", "macos", "windows"}


def _verdicts() -> dict[str, dict]:
    data = json.loads(_GROUND_TRUTH.read_text(encoding="utf-8"))
    return data["verdicts"]


def test_every_case_supports_the_linux_baseline() -> None:
    """Linux is the universal CI-validated baseline — every case must list it."""
    missing = [
        name
        for name, case in _verdicts().items()
        if "linux" not in case.get("platforms", [])
    ]
    assert not missing, f"cases not tagged for the Linux baseline: {missing}"


def test_platform_tags_are_from_the_known_set() -> None:
    bad: dict[str, set[str]] = {}
    for name, case in _verdicts().items():
        unknown = set(case.get("platforms", [])) - _KNOWN_PLATFORMS
        if unknown:
            bad[name] = unknown
    assert not bad, f"unknown platform tags: {bad}"


def test_platform_lists_have_no_duplicates() -> None:
    dupes = {
        name: case["platforms"]
        for name, case in _verdicts().items()
        if "platforms" in case and len(case["platforms"]) != len(set(case["platforms"]))
    }
    assert not dupes, f"duplicate platform tags: {dupes}"


def test_non_linux_platforms_remain_a_strict_subset() -> None:
    """macOS/Windows coverage must stay a *strict subset* of Linux.

    This encodes the documented reality that non-Linux paths are partial. If a
    future change makes macOS or Windows reach Linux parity, update this test
    and docs/reference/platforms.md together (deliberately).
    """
    counts: Counter[str] = Counter()
    for case in _verdicts().values():
        for plat in case.get("platforms", []):
            counts[plat] += 1

    linux = counts["linux"]
    assert linux > 0
    assert counts["macos"] < linux, (
        f"macOS now tagged on {counts['macos']}/{linux} cases — at parity with "
        "Linux. If macOS is truly CI-validated end-to-end, update this guard and "
        "docs/reference/platforms.md."
    )
    assert counts["windows"] < linux, (
        f"Windows now tagged on {counts['windows']}/{linux} cases — at parity "
        "with Linux. If Windows is truly CI-validated end-to-end, update this "
        "guard and docs/reference/platforms.md."
    )


def test_platforms_doc_records_the_validation_caveat() -> None:
    """The honesty caveat must stay in the platforms reference doc."""
    platforms_doc = (
        Path(__file__).parent.parent / "docs" / "reference" / "platforms.md"
    ).read_text(encoding="utf-8")
    assert "Validation status" in platforms_doc
    assert "baseline" in platforms_doc.lower()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

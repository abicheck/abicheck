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

"""Codex review, findings-fixes round 10/11: a directory/package
``compare``'s per-library ``compatible_additions``/``quality_issues`` counts
must agree with what the identical one-library comparison's scalar
``compare`` report would show for the same :class:`DiffResult` -- both are
now computed by the same :func:`abicheck.report_summary.build_summary`
(``cli_compare_release_pairwise._compare_one_library``), rather than the
release path re-implementing its own raw-``ADDITION_KINDS`` formula that
(before this fix) silently disagreed with the scalar report's own
effective-category classification for the exact scenario the bug report
named: a ``COMPATIBLE`` finding whose raw kind is not an addition (e.g.
``public_surface_shrank``, modeled here with ``branch_protection_improved``
-- any ``QUALITY_KINDS`` member reproduces the same gap).

Exercises ``_compare_one_library`` directly with ``abicheck.service.
run_compare`` monkeypatched to return a synthetic, hand-built
:class:`DiffResult` -- no real compiled binary is needed since the bug is
entirely about which formula the release path applies to an already-computed
:class:`DiffResult`, not about how that result was produced.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.api_types import CompareResult
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.cli_compare_release_pairwise import _compare_one_library
from abicheck.model import AbiSnapshot
from abicheck.report_summary import build_summary


def _synthetic_diff_result() -> DiffResult:
    # One genuine addition (FUNC_ADDED) and one COMPATIBLE, non-addition
    # quality-only kind (BRANCH_PROTECTION_IMPROVED) -- the exact shape the
    # bug report named: a formula reading raw `ADDITION_KINDS` membership
    # disagrees with `build_summary`'s effective-category classification
    # only when both are present in the same result.
    return DiffResult(
        old_version="1",
        new_version="2",
        library="libdemo.so.1",
        changes=[
            Change(kind=ChangeKind.FUNC_ADDED, symbol="new_api", description="x"),
            Change(
                kind=ChangeKind.BRANCH_PROTECTION_IMPROVED,
                symbol="libdemo.so.1",
                description="x",
            ),
        ],
    )


def test_release_pairwise_compatible_additions_matches_scalar_build_summary(
    tmp_path: Path,
) -> None:
    result = _synthetic_diff_result()
    expected = build_summary(result)
    # Sanity: the bug is only observable when the two formulas can actually
    # disagree -- confirm the scalar summary really does split the two
    # counts (not e.g. both landing in the same bucket by coincidence).
    assert expected.compatible_additions == 1
    assert expected.quality_issues == 1

    old_path = tmp_path / "old.so"
    new_path = tmp_path / "new.so"
    old_path.write_bytes(b"")
    new_path.write_bytes(b"")

    with patch("abicheck.service.run_compare") as mock_run_compare:
        mock_run_compare.return_value = CompareResult(
            diff=result,
            old_snapshot=AbiSnapshot(library="libdemo.so.1", version="1"),
            new_snapshot=AbiSnapshot(library="libdemo.so.1", version="2"),
        )
        entry = _compare_one_library(
            key="libdemo.so.1",
            old_map={"libdemo.so.1": old_path},
            new_map={"libdemo.so.1": new_path},
            old_debug_dir=None,
            new_debug_dir=None,
            resolve_debug_info=lambda *_a, **_kw: None,
            old_h=[],
            new_h=[],
            old_inc=[],
            new_inc=[],
            old_version="1",
            new_version="2",
            lang="c++",
            suppress=None,
            policy="strict_abi",
            policy_file_path=None,
            output_dir=None,
        )

    entry.pop("_diff_result", None)
    # This is the parity assertion the bug report asked for: the release
    # path's per-library entry must report the identical numbers the scalar
    # `compare` report's own `ReportSummary` carries for this exact result.
    assert entry["compatible_additions"] == expected.compatible_additions == 1
    assert entry["quality_issues"] == expected.quality_issues == 1

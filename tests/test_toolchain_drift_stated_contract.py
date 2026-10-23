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

"""A toolchain-only rebuild never fails contract coverage -- stored replay.

The SVS field report (PR 387, fixed by #1751): ``libsvs_c_api.so`` rebuilt
from unchanged source exited 1 under a *stated* public contract, on 22
unsuppressible ``finding_unresolved`` records whose subjects were import
edges, libstdc++ instantiations and ``std::`` binding flips. The suite missed
it twice over: no fixture carried toolchain drift, and most tests run the
evidence-adaptive default, whose ``all`` fallback re-scores what a stated mode
leaves ``UNKNOWN_UNRESOLVED``.

This module replays a reduced, hand-written snapshot pair with the same four
finding kinds (``tests/fixtures/toolchain_drift/generate.py``) through the
public CLI, under every contract mode -- the stated ones first among them.
The compiled counterpart is
``tests/test_toolchain_drift_stated_contract_integration.py``.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.serialization import load_snapshot, snapshot_to_json

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "toolchain_drift"

#: Stated modes are the ones the SVS run used; ``auto`` is the adaptive default.
MODES = ("public", "exports", "all", "auto")

#: The SVS report's finding census, written from the field report, not read
#: back from abicheck: 10 imports added, 2 removed, 8 leaked-dependency
#: changes, 2 ``std::`` WEAK -> GLOBAL flips.
SVS_KIND_COUNTS = {
    "imported_symbol_added": 10,
    "imported_symbol_removed": 2,
    "symbol_leaked_from_dependency_changed": 8,
    "symbol_binding_strengthened": 2,
}


def _compare(old: Path, new: Path, mode: str, tmp_path: Path) -> tuple[int, dict]:
    report = tmp_path / f"report_{mode}.json"
    result = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), "--contract", mode, "-o", f"json={report}"],
    )
    assert report.is_file(), result.output
    return result.exit_code, json.loads(report.read_text(encoding="utf-8"))


def _every_recorded_finding(report: dict) -> list[dict]:
    """Retained findings plus the ones scope filtering set aside -- a finding
    may leave ``changes`` but must stay recorded (record before disposing)."""
    scope = report.get("scope") or {}
    return [*report["changes"], *scope.get("filtered_internal_changes", [])]


def test_fixture_matches_its_generator(tmp_path: Path) -> None:
    """The committed pair holds what ``generate.py`` builds, so the narrative
    in its docstring describes what this module replays. Compared after a
    load, so a snapshot schema migration does not force a regeneration."""
    sys.path.insert(0, str(FIXTURE_DIR))
    try:
        import generate  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(FIXTURE_DIR))
    for side, built in zip(("old", "new"), generate.build_pair(), strict=True):
        fresh = tmp_path / f"{side}.json"
        fresh.write_text(snapshot_to_json(built))
        stored = load_snapshot(FIXTURE_DIR / f"{side}.json")
        assert snapshot_to_json(stored) == snapshot_to_json(load_snapshot(fresh)), side


@pytest.mark.parametrize("mode", MODES)
def test_svs_drift_replay_never_fails_contract_coverage(
    mode: str, tmp_path: Path
) -> None:
    rc, report = _compare(
        FIXTURE_DIR / "old.json", FIXTURE_DIR / "new.json", mode, tmp_path
    )

    # The library's own API did not change; only the toolchain around it did.
    assert report["contract_coverage_failures"] == []
    assert report["contract_coverage_exit_contribution"] == 0
    assert rc == 0, report["exit"]
    assert report["verdict"] == "COMPATIBLE_WITH_RISK"

    # ...and the toolchain facts are still all there, none hidden by the mode.
    recorded = _every_recorded_finding(report)
    census = Counter(f["kind"] for f in recorded)
    assert {k: census[k] for k in SVS_KIND_COUNTS} == SVS_KIND_COUNTS
    assert report["disposition_audit"]["detected_total"] == sum(
        SVS_KIND_COUNTS.values()
    )
    unresolved = [
        (f["kind"], f["symbol"])
        for f in recorded
        if f.get("contract_relevance") == "UNKNOWN_UNRESOLVED"
    ]
    assert unresolved == []


@pytest.mark.parametrize("mode", MODES)
def test_byte_identical_stored_pair_is_no_change(mode: str, tmp_path: Path) -> None:
    for side in ("old", "new"):
        copy = tmp_path / f"{side}_copy.json"
        shutil.copyfile(FIXTURE_DIR / f"{side}.json", copy)
        rc, report = _compare(FIXTURE_DIR / f"{side}.json", copy, mode, tmp_path)
        assert (rc, report["verdict"]) == (0, "NO_CHANGE"), side
        assert report["contract_coverage_failures"] == [], side
        assert report["changes"] == [], side

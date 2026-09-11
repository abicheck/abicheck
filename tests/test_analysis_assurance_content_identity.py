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

"""``schema_staleness_status``'s content-identity invariant.

Bug class: an early return justified by "comparing a value against itself
can never produce a false pairwise finding" was implemented as *object*
identity (``old is new``), so the identical argument silently failed to
cover the shape a user actually hits -- ONE stored snapshot file loaded
twice (``compare baseline.abi.json baseline.abi.json``, or a CI job
re-checking an unchanged cached baseline), which reported
``schema_staleness_status == "degraded"`` and flipped ``assurance.status``
complete -> partial. Stated here as an invariant over generated inputs
(several schema vintages, clean and degraded), not a fixture pinned to the
one observed snapshot -- plus the load-bearing negative, without which the
fix could silently become "never taint anything".
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pytest

from abicheck.policy.analysis_assurance_degraded_facts import (
    degraded_reliability_facts,
)
from abicheck.policy.analysis_assurance_schema_staleness import (
    schema_staleness_status,
)
from abicheck.serialization import snapshot_from_dict

_FIXTURES = Path(__file__).parent / "fixtures" / "schema"

#: Schema vintages spanning both sides of the invariant: current-ish (no
#: degraded fact at all) through several genuinely stale ones.
_VINTAGES = (45, 38, 25, 18, 9, 4)


def _load(fixture: str, **overrides: Any):
    d = json.loads((_FIXTURES / fixture).read_text())
    d.update(overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return snapshot_from_dict(d)


@pytest.mark.parametrize("vintage", _VINTAGES)
@pytest.mark.parametrize("fixture", ["v4.json", "v5.json"])
def test_content_identical_reload_matches_self_pairing(fixture: str, vintage: int) -> None:
    """The invariant: for any snapshot ``s``, the status of two
    *independently loaded, content-equal* copies of ``s`` equals the status
    of ``(s, s)``. Holds for clean and degraded inputs alike -- the
    soundness argument ("no pairwise finding is possible") is about equal
    content, never about which Python object holds it."""
    a = _load(fixture, schema_version=vintage, from_headers=True, ast_producer="clang")
    b = _load(fixture, schema_version=vintage, from_headers=True, ast_producer="clang")
    assert a is not b
    assert a == b
    assert schema_staleness_status(a, b) == schema_staleness_status(a, a)
    assert schema_staleness_status(a, b)[0] == "clean"


@pytest.mark.parametrize("vintage", _VINTAGES)
def test_reload_invariant_is_exercised_on_genuinely_degraded_inputs(
    vintage: int,
) -> None:
    """Guard against the invariant above passing vacuously: at least the
    stale vintages must really carry degraded facts, or the parametrization
    only ever tested the clean path."""
    a = _load("v4.json", schema_version=vintage, from_headers=True, ast_producer="clang")
    if vintage >= 45:
        assert not degraded_reliability_facts(a)
    else:
        assert degraded_reliability_facts(a), vintage


@pytest.mark.parametrize("stale_vintage", [38, 25, 18, 9, 4])
def test_differing_vintages_still_report_degraded(stale_vintage: int) -> None:
    """Load-bearing negative: two snapshots of the SAME library at
    DIFFERENT schema vintages, one degraded, are a real two-sided
    comparison a stale fact can distort -- they must still report
    ``degraded``. Without this the fix could silently become "never taint
    anything", and two independent extractions of one binary differing in
    schema vintage is exactly the case this field exists to report."""
    stale = _load(
        "v4.json", schema_version=stale_vintage, from_headers=True, ast_producer="clang"
    )
    current = _load(
        "v4.json", schema_version=45, from_headers=True, ast_producer="clang"
    )
    assert stale != current
    assert degraded_reliability_facts(stale)
    status, notes = schema_staleness_status(stale, current)
    assert status == "degraded", notes
    # ... and symmetrically, with the stale side as NEW.
    assert schema_staleness_status(current, stale)[0] == "degraded"


def test_self_compare_of_stored_stale_snapshot_is_complete_end_to_end(
    tmp_path: Path,
) -> None:
    """Through the real public ``compare`` path (AGENTS.md: validate the
    user-facing result): the exact reported shape -- one pre-v45 stored
    snapshot file given as BOTH operands -- must read ``assurance.status ==
    "complete"``, since a CI job gating on that field was newly failing on
    an unchanged cached baseline."""
    from click.testing import CliRunner

    from abicheck.cli import main

    d = json.loads((_FIXTURES / "v4.json").read_text())
    d.update(schema_version=25, from_headers=True, ast_producer="clang")
    path = tmp_path / "baseline.abi.json"
    path.write_text(json.dumps(d))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert degraded_reliability_facts(snapshot_from_dict(d))

    result = CliRunner().invoke(
        main, ["compare", str(path), str(path), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assurance = payload["analysis_assurance"]
    assert assurance["schema_staleness_status"] == "clean", assurance
    assert assurance["status"] == "complete", assurance


def test_two_distinct_stored_snapshots_still_taint_end_to_end(tmp_path: Path) -> None:
    """The end-to-end mirror of the negative above: two DIFFERENT stored
    files, one stale, must still report a non-complete assurance."""
    from click.testing import CliRunner

    from abicheck.cli import main

    base = json.loads((_FIXTURES / "v4.json").read_text())
    old_d = {**base, "schema_version": 25, "from_headers": True, "ast_producer": "clang"}
    new_d = {**old_d, "schema_version": 45}
    old_p, new_p = tmp_path / "old.abi.json", tmp_path / "new.abi.json"
    old_p.write_text(json.dumps(old_d))
    new_p.write_text(json.dumps(new_d))

    result = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--format", "json"]
    )
    payload = json.loads(result.output)
    assurance = payload["analysis_assurance"]
    assert assurance["schema_staleness_status"] == "degraded", assurance
    assert assurance["status"] != "complete", assurance

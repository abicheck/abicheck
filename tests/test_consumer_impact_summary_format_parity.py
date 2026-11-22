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

"""``consumer_impact_summary`` reaches SARIF and JUnit, not only JSON.

One ``compare --used-by`` run exports JSON, SARIF and JUnit together through
the public CLI; the three must carry the same "N of M consumers affected"
numbers (format parity, tests/CLAUDE.md). The oracle is the consumer
population the test itself supplies, not the implementation's helper.
"""

from __future__ import annotations

import itertools
import json
from unittest.mock import MagicMock

import defusedxml.ElementTree as ET
import pytest
from click.testing import CliRunner

from abicheck.checker_policy import Verdict
from abicheck.cli import main
from abicheck.model import AbiSnapshot

#: The test's own oracle: a consumer is affected unless it reads clean.
_UNAFFECTED = {Verdict.COMPATIBLE, Verdict.NO_CHANGE}
_POPULATIONS = [
    combo
    for n in (1, 2, 3)
    for combo in itertools.product(
        (Verdict.BREAKING, Verdict.COMPATIBLE, Verdict.API_BREAK, Verdict.NO_CHANGE),
        repeat=n,
    )
][::3]


def _snap(version: str) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version=version, functions=[])


def _binary(path):
    path.write_bytes(b"\x7fELF" + b"\x00" * 200)
    return path


@pytest.mark.parametrize("verdicts", _POPULATIONS)
def test_sarif_and_junit_carry_the_json_consumer_impact_summary(
    tmp_path, monkeypatch, verdicts
) -> None:
    from abicheck.workflows.consumer_scope import AppCompatResult

    old = _binary(tmp_path / "old.so")
    new = _binary(tmp_path / "new.so")
    apps = [_binary(tmp_path / f"app{i}") for i in range(len(verdicts))]
    by_app = {
        str(app): AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            verdict=verdict,
            symbol_coverage=100.0,
        )
        for app, verdict in zip(apps, verdicts, strict=True)
    }

    def _fake_scope(diff, app, old_lib, new_lib, **kwargs):
        from abicheck.model.consumer_spec import as_consumer_spec

        return by_app[str(as_consumer_spec(app).path)]

    monkeypatch.setattr(
        "abicheck.workflows.consumer_scope.scope_diff_to_app", _fake_scope
    )
    monkeypatch.setattr(
        "abicheck.dumper.dump", MagicMock(side_effect=[_snap("1.0"), _snap("2.0")])
    )
    out_json = tmp_path / "r.json"
    out_sarif = tmp_path / "r.sarif"
    out_junit = tmp_path / "r.xml"
    args = ["compare", str(old), str(new)]
    for app in apps:
        args += ["--used-by", str(app)]
    args += [
        "-o",
        f"json={out_json}",
        "-o",
        f"sarif={out_sarif}",
        "-o",
        f"junit={out_junit}",
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code in (0, 2, 4), result.output

    expected_affected = sum(1 for v in verdicts if v not in _UNAFFECTED)
    summary = json.loads(out_json.read_text(encoding="utf-8"))[
        "consumer_impact_summary"
    ]
    assert (summary["total"], summary["evaluated"], summary["affected"]) == (
        len(verdicts),
        len(verdicts),
        expected_affected,
    )

    sarif = json.loads(out_sarif.read_text(encoding="utf-8"))
    block = sarif["runs"][0]["properties"]["scopedGate"]["consumerImpactSummary"]
    assert block == {
        "total": len(verdicts),
        "evaluated": len(verdicts),
        "affected": expected_affected,
        "unreadableAdvisory": 0,
        "unreadablePaths": [],
    }

    root = ET.parse(out_junit).getroot()
    props = {
        p.get("name"): p.get("value")
        for p in root.iter("property")
        if (p.get("name") or "").startswith("abicheck.consumer_impact.")
    }
    assert props == {
        "abicheck.consumer_impact.total": str(len(verdicts)),
        "abicheck.consumer_impact.evaluated": str(len(verdicts)),
        "abicheck.consumer_impact.affected": str(expected_affected),
        "abicheck.consumer_impact.unreadable_advisory": "0",
    }


def test_an_unscoped_run_carries_no_consumer_impact_in_sarif_or_junit(
    tmp_path, monkeypatch
) -> None:
    old = _binary(tmp_path / "old.so")
    new = _binary(tmp_path / "new.so")
    monkeypatch.setattr(
        "abicheck.dumper.dump", MagicMock(side_effect=[_snap("1.0"), _snap("2.0")])
    )
    out_sarif = tmp_path / "r.sarif"
    out_junit = tmp_path / "r.xml"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "-o",
            f"sarif={out_sarif}",
            "-o",
            f"junit={out_junit}",
        ],
    )
    assert result.exit_code == 0, result.output
    run_props = json.loads(out_sarif.read_text(encoding="utf-8"))["runs"][0][
        "properties"
    ]
    assert "scopedGate" not in run_props
    assert "abicheck.consumer_impact" not in out_junit.read_text(encoding="utf-8")

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

"""``action/run.sh`` hands each comparison of a ``delta-baseline`` pair its
``report.delta_baseline`` record (plan piece 4).

The oracle is the argv the stub abicheck received and the record file it
names, read with the report module's validator: the gate command says
``gate`` with the lookup's outcome, the delta command says ``delta`` with the
gate's verdict, neither carries the other's record, and the step's outputs
are the gate's whatever the record says. Harness: ``test_action_delta_baseline``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _action_run_sh_harness import REQUIRES_POSIX_SHELL
from test_action_delta_baseline import _run

from abicheck.report.delta_baseline import (
    DELTA_LOOKUP_OUTCOMES,
    DELTA_REPORT_OUTCOMES,
    DeltaBaseline,
)

pytestmark = REQUIRES_POSIX_SHELL

_KEY = "report.delta_baseline=@"


def _records(argv: list[str]) -> list[DeltaBaseline]:
    out = []
    for prev, arg in zip(argv, argv[1:], strict=False):
        if prev == "--set" and arg.startswith(_KEY):
            data = json.loads(Path(arg[len(_KEY) :]).read_text(encoding="utf-8"))
            out.append(DeltaBaseline.from_mapping(data))
    return out


@pytest.mark.parametrize("outcome", DELTA_LOOKUP_OUTCOMES)
def test_every_gate_command_records_the_lookup_outcome(
    tmp_path: Path, outcome: str
) -> None:
    snap = str(tmp_path / "base-snapshot.json")
    outputs, calls = _run(tmp_path, "artifact", outcome, snapshot=snap)
    gates = [c for c in calls if snap not in c]
    assert gates
    for argv in gates:
        assert _records(argv) == [
            DeltaBaseline("gate", outcome, base_sha="a" * 40, branch="main")
        ]
    deltas = [c for c in calls if snap in c]
    if outcome != "resolved":
        assert deltas == []
        return
    (argv,) = deltas
    assert _records(argv) == [
        DeltaBaseline(
            "delta",
            "resolved",
            base_sha="a" * 40,
            branch="main",
            gate_verdict=outputs["verdict"],
        )
    ]
    # The staged (stored) delta report is the one the record describes.
    assert outputs["verdict"] == "BREAKING"


def test_no_delta_input_means_no_record(tmp_path: Path) -> None:
    _outputs, calls = _run(tmp_path, "artifact", "")
    assert calls
    assert not any(a.startswith(_KEY) for c in calls for a in c)


def test_a_directory_comparison_records_unsupported_operand(tmp_path: Path) -> None:
    olddir, newdir = tmp_path / "old", tmp_path / "new"
    for d in (olddir, newdir):
        d.mkdir()
        (d / "libx.so").write_bytes(b"\x7fELF")
    _outputs, calls = _run(
        tmp_path,
        "direct",
        "resolved",
        extra={"INPUT_OLD_LIBRARY": str(olddir), "INPUT_NEW_LIBRARY": str(newdir)},
    )
    assert calls
    for argv in calls:
        (rec,) = _records(argv)
        assert (rec.comparison_role, rec.outcome) == ("gate", "unsupported_operand")
        assert rec.outcome in DELTA_REPORT_OUTCOMES


@pytest.mark.parametrize("bad", ["garbage", "rerun_failed", "Resolved"])
def test_an_unrecordable_outcome_never_reaches_the_gate(
    tmp_path: Path, bad: str
) -> None:
    """An outcome the record refuses is skipped with a warning, so the gate
    never turns into a usage error over an informational block."""
    (tmp_path / "plain").mkdir()
    (tmp_path / "bad").mkdir()
    plain, _ = _run(tmp_path / "plain", "artifact", "")
    outputs, calls = _run(tmp_path / "bad", "artifact", bad)
    assert not any(a.startswith(_KEY) for c in calls for a in c)
    assert outputs["verdict"] == plain["verdict"]
    assert outputs.get("exit-code") == plain.get("exit-code")


def test_a_user_set_record_for_the_gate_wins_but_never_reaches_the_delta(
    tmp_path: Path,
) -> None:
    snap = str(tmp_path / "base-snapshot.json")
    user = tmp_path / "user.json"
    _outputs, calls = _run(
        tmp_path,
        "artifact",
        "resolved",
        snapshot=snap,
        extra={"INPUT_SET": f"report.delta_baseline=@{user}"},
    )
    (gate,) = [c for c in calls if snap not in c]
    (delta,) = [c for c in calls if snap in c]
    sets = [
        a
        for p, a in zip(gate, gate[1:], strict=False)
        if p == "--set" and a.startswith(_KEY)
    ]
    assert sets[-1] == f"{_KEY}{user}"  # the CLI keeps the last one
    assert f"{_KEY}{user}" not in delta
    assert [r.comparison_role for r in _records(delta)] == ["delta"]

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
import subprocess
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
            # The pairing the Action reports as its delta-operand-match output.
            operand_match=outputs["delta-operand-match"],
        )
    ]
    assert outputs["delta-operand-match"]  # a delta report is always a matched pair
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


@pytest.mark.parametrize("spelling", ["absolute", "relative"])
def test_a_stored_bundle_facts_gate_gets_no_record(
    tmp_path: Path, spelling: str
) -> None:
    """That route refuses the key; recording it would fail the gate. A
    relative operand resolves against the step's working directory, not the
    interpreter's safe directory the probe runs from."""
    from abicheck.serialization import save_bundle_facts
    from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

    facts = tmp_path / "facts.json"
    save_bundle_facts(capture_bundle_facts({}), facts)
    snap = str(tmp_path / "base-snapshot.json")
    _outputs, calls = _run(
        tmp_path,
        "artifact",
        "resolved",
        snapshot=snap,
        extra={
            "INPUT_OLD_LIBRARY": facts.name if spelling == "relative" else str(facts)
        },
    )
    gates = [c for c in calls if snap not in c]
    assert gates
    assert all(_records(argv) == [] for argv in gates)
    assert not any(a.startswith(_KEY) for argv in gates for a in argv)


def test_an_unwritable_delta_record_clears_any_configured_one(tmp_path: Path) -> None:
    """With no gate verdict to record, the delta command clears the key, so a
    config file's record can never label the delta report as the gate."""
    snap = str(tmp_path / "base-snapshot.json")
    _outputs, calls = _run(
        tmp_path,
        "artifact",
        "resolved",
        snapshot=snap,
        extra={"ABICHECK_DELTA_BASE_SHA": "not-a-sha"},
    )
    (delta,) = [c for c in calls if snap in c]
    sets = [a for p, a in zip(delta, delta[1:], strict=False) if p == "--set"]
    assert 'report.delta_baseline=""' in sets
    assert not any(a.startswith(_KEY) for a in sets)


_QUALIFY_START = "_is_path_already_qualified() {"
_CANONICAL_START = "_mktemp_canonical() {"


def _helper(text: str, start: str) -> str:
    i = text.index(start)
    return text[i : text.index("\n}\n", i) + 3]


#: (spelling, anchored on POSIX, anchored on Windows) -- the oracle, written
#: out by hand: only a spelling neither platform qualifies gets ``$PWD/``.
_SPELLINGS = [
    ("facts.json", True, True),
    ("sub/facts.json", True, True),
    ("/abs/facts.json", False, False),
    ("C:/facts.json", True, False),
    ("C:\\facts.json", True, False),
    ("\\\\server\\share\\facts.json", True, False),
]


@pytest.mark.parametrize("windows", [False, True])
def test_the_old_operand_is_anchored_by_the_shared_qualification_rule(
    tmp_path: Path, windows: bool
) -> None:
    """``add_delta_baseline_flag`` anchors the old operand with run.sh's own
    ``_mktemp_canonical`` (``_is_path_already_qualified``), so a Windows
    drive or UNC path is never prefixed with ``$PWD`` on Git Bash, while a
    relative one always is."""
    from _workflow_exec import bash_executable, require_bash

    require_bash()
    text = (Path(__file__).resolve().parents[1] / "action" / "run.sh").read_text(
        encoding="utf-8"
    )
    fn = text[text.index("add_delta_baseline_flag() {") :]
    fn = fn[: fn.index("\n}\n")]
    assert '_mktemp_canonical "$old"' in fn
    script = (
        f"_RUNNING_ON_WINDOWS={'true' if windows else 'false'}\n"
        + _helper(text, _QUALIFY_START)
        + _helper(text, _CANONICAL_START)
        + 'for p in "$@"; do _mktemp_canonical "$p"; done\n'
    )
    spellings = [s for s, _, _ in _SPELLINGS]
    out = subprocess.run(
        [bash_executable(), "-c", script, "bash", *spellings],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    ).stdout.splitlines()
    pwd = subprocess.run(
        [bash_executable(), "-c", "pwd"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    ).stdout.strip()
    for (spelling, posix, win), got in zip(_SPELLINGS, out, strict=True):
        anchored = win if windows else posix
        assert got == (f"{pwd}/{spelling}" if anchored else spelling), spelling

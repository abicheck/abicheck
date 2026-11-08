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

"""The main Action's ``delta-baseline``: the PR comment shows this PR's
changes, the step's status stays the release gate's.

Plan: docs/contribute/plans/pr-delta-vs-main-snapshot.md (piece 3). Runs the
whole of ``action/run.sh`` against an abicheck stub that answers the gate
command and the delta command with two different real reports, so each test
can tell which one reached the comment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from _action_run_sh_harness import REQUIRES_POSIX_SHELL, _compare_env, _run_action
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_pr_comment import insert_notes, pr_comment_cmd
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot

pytestmark = REQUIRES_POSIX_SHELL

_ROOT = Path(__file__).resolve().parents[1]
_SHA = "a" * 40


def _report(tmp_path: Path, tag: str, removed: str) -> tuple[int, bytes]:
    """A real compare report in which only *removed* disappears."""
    keep = Function(name="keep", mangled="keep", return_type="void")
    gone = Function(name=removed, mangled=removed, return_type="void")
    old, new = tmp_path / f"{tag}-o.json", tmp_path / f"{tag}-n.json"
    save_snapshot(
        AbiSnapshot(library="libx.so.1", version="1", functions=[keep, gone]), old
    )
    save_snapshot(AbiSnapshot(library="libx.so.1", version="2", functions=[keep]), new)
    out = tmp_path / f"{tag}.json"
    res = CliRunner().invoke(main, ["compare", str(old), str(new), "-o", f"json={out}"])
    assert out.exists(), res.output
    return res.exit_code, out.read_bytes()


def _stub(tmp_path: Path, snapshot: str) -> tuple[Path, Path, int]:
    """abicheck answering the gate with ``release_only`` removed and the delta
    (old operand == *snapshot*) with ``pr_only`` removed; logs every argv."""
    gate_code, gate = _report(tmp_path, "gate", "release_only")
    _, delta = _report(tmp_path, "delta", "pr_only")
    (tmp_path / "gate.bin").write_bytes(gate)
    (tmp_path / "delta.bin").write_bytes(delta)
    log = tmp_path / "argv.log"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\x1f" "$@" >> "{log}"; echo >> "{log}"\n'
        f'src="{tmp_path}/gate.bin"; code={gate_code}\n'
        f'for a in "$@"; do [[ "$a" == "{snapshot}" ]] && {{ src="{tmp_path}/delta.bin"; code=0; }}; done\n'
        "prev=''\n"
        'for a in "$@"; do\n'
        '  if [[ "$prev" == -o && "$a" == *=* && "${a#*=}" != - ]]; then cp "$src" "${a#*=}"; fi\n'
        '  prev="$a"\n'
        "done\n"
        "exit $code\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{tmp_path}/gh_calls"\n'
        f'[[ " $* " == *" --input - "* ]] && cat >> "{tmp_path}/gh_bodies"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return bindir, log, gate_code


def _run(
    tmp_path: Path,
    delivery: str,
    outcome: str,
    *,
    snapshot: str = "",
    message: str = "",
) -> tuple[dict, list[list[str]]]:
    snap = snapshot or str(tmp_path / "base-snapshot.json")
    bindir, log, _ = _stub(tmp_path, snap)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": _SHA}}}))
    env = {
        **_compare_env(tmp_path),
        "INPUT_OLD_HEADER": "release/include",
        "INPUT_NEW_HEADER": "include",
        "INPUT_PR_COMMENT": "true",
        "INPUT_PR_COMMENT_ON": "always",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "o/r",
        "ABICHECK_PR_COMMENT_DELIVERY": delivery,
    }
    if outcome:
        env.update(
            {
                "ABICHECK_DELTA_OUTCOME": outcome,
                "ABICHECK_DELTA_SNAPSHOT": snap if outcome == "resolved" else "",
                "ABICHECK_DELTA_MESSAGE": message,
            }
        )
    outputs = _run_action(tmp_path, env, bindir)
    calls = [
        line.split("\x1f")[:-1]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line
    ]
    return outputs, calls


def _staged(outputs: dict) -> dict:
    staged = Path(outputs["pr-report-dir"])
    (only,) = staged.iterdir()
    return json.loads(only.read_text())


def _removed(report: dict) -> set[str]:
    return {c.get("symbol", "") for c in report.get("changes", [])}


def test_resolved_delta_renders_the_snapshot_comparison(tmp_path: Path) -> None:
    snap = str(tmp_path / "base-snapshot.json")
    outputs, calls = _run(tmp_path, "artifact", "resolved", snapshot=snap)
    assert "pr_only" in json.dumps(_staged(outputs))
    assert "release_only" not in json.dumps(_staged(outputs))
    delta = [c for c in calls if snap in c]
    assert len(delta) == 1
    argv = delta[0]
    # The snapshot replaces exactly the old operand; the new side is kept.
    assert argv[argv.index(snap) + 1] == _compare_env(tmp_path)["INPUT_NEW_LIBRARY"]
    assert str(tmp_path / "libold.so") not in argv
    # Old-side evidence describes the release, not the snapshot: dropped.
    assert not any(a.startswith("old=") for a in argv)
    assert "new=include" in argv


@pytest.mark.parametrize("outcome", ["resolved", "snapshot_missing", "base_pending"])
def test_the_step_status_is_the_gate_s_whatever_the_delta(
    tmp_path: Path, outcome: str
) -> None:
    (tmp_path / "gate").mkdir()
    (tmp_path / "delta").mkdir()
    gate_only, _ = _run(tmp_path / "gate", "artifact", "")
    with_delta, _ = _run(
        tmp_path / "delta", "artifact", outcome, message="Delta unavailable: m."
    )
    assert gate_only["_exit"] != 0  # the gate report is a break: the control
    assert with_delta["_exit"] == gate_only["_exit"]
    assert with_delta.get("verdict") == gate_only.get("verdict")


def test_unresolved_delta_keeps_the_release_report_and_says_so(tmp_path: Path) -> None:
    outputs, calls = _run(
        tmp_path, "direct", "snapshot_missing", message="Delta unavailable: gone."
    )
    assert not any(str(tmp_path / "base-snapshot.json") in c for c in calls)
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    assert "release_only" in body
    assert "> Delta unavailable: gone. Showing the release-baseline comparison." in body
    assert "> Release gate: BREAKING" in body


def test_resolved_direct_comment_carries_both_notes(tmp_path: Path) -> None:
    outputs, _calls = _run(
        tmp_path,
        "direct",
        "resolved",
        message="Changes in this PR (vs `main@aaaaaaa`).",
    )
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    assert "pr_only" in body and "release_only" not in body
    assert "> Changes in this PR (vs `main@aaaaaaa`)." in body
    assert "this check's status comes from the gate" in body


def test_no_delta_input_changes_nothing(tmp_path: Path) -> None:
    outputs, calls = _run(tmp_path, "artifact", "")
    assert len(calls) == 1
    assert "release_only" in json.dumps(_staged(outputs))


# ── the renderer's --note ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<!-- m -->\n## H\nrest", "<!-- m -->\n## H\n\n> a\n>\n> b c\nrest"),
        ("no heading", "> a\n>\n> b c\n\nno heading"),
    ],
)
def test_insert_notes(body: str, expected: str) -> None:
    assert insert_notes(body, ["a", "b\nc"]) == expected


def test_insert_notes_ignores_blank_notes() -> None:
    assert insert_notes("## H", ["", "  "]) == "## H"


def test_pr_comment_cli_renders_notes_under_the_heading(tmp_path: Path) -> None:
    _, report = _report(tmp_path, "r", "gone")
    path = tmp_path / "r.json"
    path.write_bytes(report)
    out = tmp_path / "c.md"
    res = CliRunner().invoke(
        pr_comment_cmd,
        [
            str(path),
            "--on",
            "always",
            "--note",
            "first",
            "--note",
            "second",
            "-o",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    lines = out.read_text().splitlines()
    heading = next(i for i, line in enumerate(lines) if line.startswith("#"))
    assert lines[heading + 1 : heading + 5] == ["", "> first", ">", "> second"]


# ── action.yml wiring ───────────────────────────────────────────────────────


def _action() -> dict:
    return yaml.safe_load((_ROOT / "action.yml").read_text(encoding="utf-8"))


def test_delta_fetch_runs_after_install_and_before_the_comparison() -> None:
    names = [s.get("id") or s.get("name") for s in _action()["runs"]["steps"]]
    assert (
        names.index("Install abicheck")
        < names.index("delta-base")
        < names.index("run-abicheck")
    )


def test_delta_failure_is_its_own_step_never_the_comparison_s() -> None:
    steps = {s.get("name"): s for s in _action()["runs"]["steps"]}
    require = steps["Require the delta base snapshot"]
    assert "delta-on-missing-base == 'fail'" in require["if"]
    run = steps["Run abicheck"]
    assert "delta" not in run.get("if", "")

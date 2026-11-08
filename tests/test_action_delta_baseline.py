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
from abicheck.cli_pr_comment import pr_comment_cmd
from abicheck.frontends.action.main_snapshot import delta_notes
from abicheck.model import AbiSnapshot, Function
from abicheck.pr_comment_render import insert_notes
from abicheck.serialization import save_snapshot

pytestmark = REQUIRES_POSIX_SHELL

_ROOT = Path(__file__).resolve().parents[1]
_SHA = "a" * 40


def _report(
    tmp_path: Path, tag: str, removed: str, *, added: bool = False
) -> tuple[int, bytes]:
    """A real compare report in which only *removed* disappears (a break),
    or, with *added*, only appears (compatible)."""
    keep = Function(name="keep", mangled="keep", return_type="void")
    gone = Function(name=removed, mangled=removed, return_type="void")
    old, new = tmp_path / f"{tag}-o.json", tmp_path / f"{tag}-n.json"
    before, after = ([keep], [keep, gone]) if added else ([keep, gone], [keep])
    save_snapshot(AbiSnapshot(library="libx.so.1", version="1", functions=before), old)
    save_snapshot(AbiSnapshot(library="libx.so.1", version="2", functions=after), new)
    out = tmp_path / f"{tag}.json"
    res = CliRunner().invoke(main, ["compare", str(old), str(new), "-o", f"json={out}"])
    assert out.exists(), res.output
    return res.exit_code, out.read_bytes()


def _stub(
    tmp_path: Path,
    snapshot: str,
    *,
    clean_gate: bool = False,
    delta_gates: bool = False,
) -> tuple[Path, Path, int]:
    """abicheck answering the gate with ``release_only`` removed (added, with
    *clean_gate*) and the delta (old operand == *snapshot*) with ``pr_only``
    added; logs every argv. *delta_gates* makes the delta report carry a
    blocking additions-review axis, so a delta report read by the gate's own
    exit logic is visible as a wrongly failing step."""
    gate_code, gate = _report(tmp_path, "gate", "release_only", added=clean_gate)
    _, delta = _report(tmp_path, "delta", "pr_only", added=True)
    if delta_gates:
        doc = json.loads(delta)
        doc.setdefault("exit", {})["additions_review_contribution"] = 1
        delta = json.dumps(doc).encode()
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
    extra: dict[str, str] | None = None,
    clean_gate: bool = False,
    delta_gates: bool = False,
) -> tuple[dict, list[list[str]]]:
    snap = snapshot or str(tmp_path / "base-snapshot.json")
    bindir, log, _ = _stub(
        tmp_path, snap, clean_gate=clean_gate, delta_gates=delta_gates
    )
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
                "ABICHECK_DELTA_BASE_SHA": "a" * 40,
                "ABICHECK_DELTA_BRANCH": "main",
            }
        )
    env.update(extra or {})
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


@pytest.mark.parametrize("fmt", ["json", "markdown"])
@pytest.mark.parametrize("outcome", ["resolved", "snapshot_missing", "base_pending"])
def test_the_step_status_is_the_gate_s_whatever_the_delta(
    tmp_path: Path, outcome: str, fmt: str
) -> None:
    """``markdown`` makes the gate's JSON an internal sidecar that the exit
    logic reads after the comment step: the delta must not replace it."""
    (tmp_path / "gate").mkdir()
    (tmp_path / "delta").mkdir()
    gate_only, _ = _run(tmp_path / "gate", "direct", "", extra={"INPUT_FORMAT": fmt})
    with_delta, _ = _run(
        tmp_path / "delta",
        "direct",
        outcome,
        message="Delta unavailable: m.",
        extra={"INPUT_FORMAT": fmt},
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
    # The wording is delta_notes' -- the same function the fork publisher uses.
    first, gate = delta_notes(
        "snapshot_missing", base_sha="a" * 40, gate_verdict="BREAKING"
    )
    assert first.endswith("Showing the release-baseline comparison.")
    assert f"> {first}" in body and f"> {gate}" in body


def test_resolved_direct_comment_carries_both_notes(tmp_path: Path) -> None:
    outputs, _calls = _run(
        tmp_path,
        "direct",
        "resolved",
        message="Changes in this PR (vs `main@aaaaaaa`).",
    )
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    assert "pr_only" in body and "release_only" not in body
    assert f"> {delta_notes('resolved', base_sha='a' * 40)[0]}" in body
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


def test_the_gate_s_own_report_file_keeps_the_release_comparison(
    tmp_path: Path,
) -> None:
    outputs, _calls = _run(tmp_path, "artifact", "resolved")
    gate = (tmp_path / "report.json").read_text(encoding="utf-8")
    assert "release_only" in gate and "pr_only" not in gate
    assert "pr_only" in json.dumps(_staged(outputs))


def test_delta_report_does_not_inherit_the_release_provenance(tmp_path: Path) -> None:
    snap = str(tmp_path / "base-snapshot.json")
    _outputs, calls = _run(
        tmp_path,
        "artifact",
        "resolved",
        snapshot=snap,
        extra={"INPUT_SET": "report.baseline_provenance=@/tmp/prov.json"},
    )
    (gate,) = [c for c in calls if snap not in c]
    (delta,) = [c for c in calls if snap in c]
    assert "report.baseline_provenance=@/tmp/prov.json" in gate
    assert not any("baseline_provenance" in a for a in delta)


def test_directory_comparison_gets_no_delta(tmp_path: Path) -> None:
    olddir = tmp_path / "old"
    newdir = tmp_path / "new"
    for d in (olddir, newdir):
        d.mkdir()
        (d / "libx.so").write_bytes(b"\x7fELF")
    snap = str(tmp_path / "base-snapshot.json")
    _outputs, calls = _run(
        tmp_path,
        "direct",
        "resolved",
        snapshot=snap,
        extra={"INPUT_OLD_LIBRARY": str(olddir), "INPUT_NEW_LIBRARY": str(newdir)},
    )
    assert not any(snap in c for c in calls)
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    assert "delta-baseline compares one library" in body


def test_a_failed_lookup_does_not_stop_the_gate() -> None:
    steps = {s.get("name"): s for s in _action()["runs"]["steps"]}
    script = steps["Fetch delta base snapshot"]["run"]
    assert "outcome=lookup_failed" in script
    assert "|| {" in script
    assert "pull_request_target" not in steps["Fetch delta base snapshot"]["if"]


@pytest.mark.parametrize("fmt", ["json", "markdown"])
def test_the_delta_report_never_reaches_the_gate_s_exit_axes(
    tmp_path: Path, fmt: str
) -> None:
    """A clean gate stays green even when the delta report would block
    (CodeRabbit on abicheck/abicheck#1947): with a non-JSON format the gate's
    JSON is an internal file the exit logic reads after the comment step."""
    outputs, _calls = _run(
        tmp_path,
        "direct",
        "resolved",
        extra={"INPUT_FORMAT": fmt},
        clean_gate=True,
        delta_gates=True,
    )
    assert outputs["_exit"] == 0, outputs["_stdout"]


@pytest.mark.parametrize(
    "extra_args",
    [
        "-H old=release.h -H new=pr.h",
        "--header=old=release.h --header=new=pr.h",
        "-I old=rel/inc -I new=pr/inc",
        "-Hold=release.h -Hnew=pr.h",
    ],
)
def test_every_old_side_spelling_is_dropped_and_new_side_kept(
    tmp_path: Path, extra_args: str
) -> None:
    snap = str(tmp_path / "base-snapshot.json")
    _outputs, calls = _run(
        tmp_path,
        "artifact",
        "resolved",
        snapshot=snap,
        extra={"INPUT_EXTRA_ARGS": extra_args},
    )
    (gate,) = [c for c in calls if snap not in c]
    (delta,) = [c for c in calls if snap in c]
    old_tokens = [t for t in extra_args.split() if "old=" in t or t.endswith("old")]
    new_tokens = [t for t in extra_args.split() if "new=" in t]
    assert all(t in " ".join(gate) for t in old_tokens)
    assert not any("old=" in a for a in delta)
    assert all(any(t in a for a in delta) for t in new_tokens)

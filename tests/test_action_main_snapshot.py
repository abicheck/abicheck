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

"""Which default-branch snapshot a PR delta is measured against.

Plan: docs/contribute/plans/pr-delta-vs-main-snapshot.md. Every case is a way
the delta could be measured against the *wrong* commit (other people's changes
leaking in) or a missing snapshot could read as "no changes".

The git scenarios build real histories and run the Action's own ``run.sh``
against a stub ``gh``; the oracle is the commit each scenario constructed as
the base, not the module's own selection helper.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from _workflow_exec import bash_executable, require_bash

from abicheck.frontends.action.main_snapshot import (
    DeltaOutcome,
    SnapshotExpectation,
    outcome_message,
    resolve_snapshot,
    select_base_commit,
    snapshot_artifact_name,
)

_ROOT = Path(__file__).resolve().parents[1]
_FETCH_SH = _ROOT / "actions" / "fetch-main-snapshot" / "run.sh"
_SNAP_SH = _ROOT / "actions" / "main-snapshot" / "run.sh"
_BASE = "b" * 40
_NAME = f"abicheck-main-{_BASE}"
_WF = ".github/workflows/abi-main.yml"


def _run(
    run_id: int,
    *,
    status: str = "completed",
    conclusion: str = "success",
    sha: str = _BASE,
    branch: str = "main",
    event: str = "push",
    path: str = _WF,
    repo: str = "o/r",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "run_attempt": 1,
        "path": path,
        "name": "abi-main",
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "head_branch": branch,
        "repository": {"full_name": repo},
        "head_repository": {"full_name": repo},
    }


def _art(
    art_id: int,
    run_id: int,
    *,
    name: str = _NAME,
    expired: bool = False,
    digest: str = "sha256:aa",
) -> dict[str, Any]:
    return {
        "id": art_id,
        "name": name,
        "expired": expired,
        "digest": digest,
        "workflow_run": {"id": run_id, "head_sha": _BASE},
    }


_EXPECT = SnapshotExpectation(
    repository="o/r", workflow=_WF, branch="main", base_sha=_BASE, artifact_name=_NAME
)


# ── outcome table ───────────────────────────────────────────────────────────

_CASES: list[tuple[str, list[Any], list[Any], DeltaOutcome]] = [
    ("one good artifact", [_art(1, 10)], [_run(10)], DeltaOutcome.RESOLVED),
    (
        "re-run, same digest",
        [_art(1, 10), _art(2, 11)],
        [_run(10), _run(11)],
        DeltaOutcome.RESOLVED,
    ),
    (
        "re-run, different zip digest",
        [_art(1, 10), _art(2, 11, digest="sha256:bb")],
        [_run(10), _run(11)],
        DeltaOutcome.RESOLVED,
    ),
    (
        "two artifacts, no digest field",
        [_art(1, 10, digest=""), _art(2, 11, digest="")],
        [_run(10), _run(11)],
        DeltaOutcome.RESOLVED,
    ),
    (
        "producer still running",
        [],
        [_run(10, status="in_progress", conclusion="")],
        DeltaOutcome.BASE_PENDING,
    ),
    ("producer failed", [], [_run(10, conclusion="failure")], DeltaOutcome.BASE_FAILED),
    (
        "failed run's artifact",
        [_art(1, 10)],
        [_run(10, conclusion="failure")],
        DeltaOutcome.BASE_FAILED,
    ),
    ("never ran", [], [], DeltaOutcome.BASE_NOT_SNAPSHOTTED),
    ("expired", [_art(1, 10, expired=True)], [_run(10)], DeltaOutcome.BASE_EXPIRED),
    (
        "pending beats expired",
        [_art(1, 10, expired=True)],
        [_run(10), _run(11, status="queued", conclusion="")],
        DeltaOutcome.BASE_PENDING,
    ),
    (
        "uploaded by an unknown run",
        [_art(1, 99)],
        [_run(10, conclusion="failure")],
        DeltaOutcome.BASE_FAILED,
    ),
    (
        "uploaded by an unlisted run only",
        [_art(1, 99)],
        [],
        DeltaOutcome.UNTRUSTED_SOURCE,
    ),
    (
        "pull_request run",
        [_art(1, 10)],
        [_run(10, event="pull_request")],
        DeltaOutcome.UNTRUSTED_SOURCE,
    ),
    (
        "other branch",
        [_art(1, 10)],
        [_run(10, branch="feature")],
        DeltaOutcome.UNTRUSTED_SOURCE,
    ),
    (
        "other workflow",
        [_art(1, 10)],
        [_run(10, path=".github/workflows/x.yml")],
        DeltaOutcome.UNTRUSTED_SOURCE,
    ),
    (
        "run built another commit",
        [_art(1, 10)],
        [_run(10, sha="c" * 40)],
        DeltaOutcome.UNTRUSTED_SOURCE,
    ),
    (
        "other repository",
        [_art(1, 10)],
        [_run(10, repo="evil/r")],
        DeltaOutcome.UNTRUSTED_SOURCE,
    ),
    (
        "name of another commit ignored",
        [_art(1, 10, name="abicheck-main-" + "c" * 40)],
        [_run(10)],
        DeltaOutcome.SNAPSHOT_MISSING,
    ),
    (
        "dispatch is a valid producer",
        [_art(1, 10)],
        [_run(10, event="workflow_dispatch")],
        DeltaOutcome.RESOLVED,
    ),
]


@pytest.mark.parametrize(
    ("label", "artifacts", "runs", "expected"), _CASES, ids=[c[0] for c in _CASES]
)
def test_outcome_table(
    label: str, artifacts: list[Any], runs: list[Any], expected: DeltaOutcome
) -> None:
    assert resolve_snapshot(artifacts, runs, _EXPECT).outcome is expected


def test_lookup_failure_is_never_an_absent_snapshot() -> None:
    result = resolve_snapshot([_art(1, 10)], [_run(10)], _EXPECT, lookup_failed=True)
    assert result.outcome is DeltaOutcome.LOOKUP_FAILED


def test_resolved_picks_an_eligible_artifact_not_an_untrusted_one() -> None:
    result = resolve_snapshot(
        [_art(5, 10), _art(9, 11)], [_run(10), _run(11, event="pull_request")], _EXPECT
    )
    assert (result.outcome, result.artifact_id, result.run_id) == (
        DeltaOutcome.RESOLVED,
        "5",
        "10",
    )


@pytest.mark.parametrize(
    "outcome", [o for o in DeltaOutcome if o is not DeltaOutcome.RESOLVED]
)
def test_no_unavailable_outcome_reads_as_a_clean_result(outcome: DeltaOutcome) -> None:
    text = outcome_message(outcome, sha=_BASE).lower()
    assert "unavailable" in text
    assert "no changes" not in text and "compatible" not in text


def test_merge_group_heading_names_the_queue_entry() -> None:
    assert "merge-queue entry" in outcome_message(
        DeltaOutcome.RESOLVED, sha=_BASE, subject="merge-queue entry"
    )


@pytest.mark.parametrize("bad", ["", "abc", "g" * 40, _BASE + "0"])
def test_artifact_name_needs_a_full_sha(bad: str) -> None:
    with pytest.raises(ValueError):
        snapshot_artifact_name(bad)


def test_artifact_name_rejects_path_like_profiles() -> None:
    assert (
        snapshot_artifact_name(_BASE.upper(), "linux-x86_64") == f"{_NAME}-linux-x86_64"
    )
    with pytest.raises(ValueError):
        snapshot_artifact_name(_BASE, "../x")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"mode": "merge-commit", "head_parents": ["p1", "p2"]},
            (DeltaOutcome.RESOLVED, "p1"),
        ),
        (
            {"mode": "merge-commit", "head_parents": ["p1"]},
            (DeltaOutcome.BASE_UNKNOWN, ""),
        ),
        ({"mode": "merge-commit", "head_parents": []}, (DeltaOutcome.BASE_UNKNOWN, "")),
        ({"mode": "head", "merge_bases": ["m"]}, (DeltaOutcome.RESOLVED, "m")),
        ({"mode": "head", "merge_bases": ["m", "m"]}, (DeltaOutcome.RESOLVED, "m")),
        (
            {"mode": "head", "merge_bases": ["m", "n"]},
            (DeltaOutcome.BASE_AMBIGUOUS, ""),
        ),
        ({"mode": "head", "merge_bases": []}, (DeltaOutcome.BASE_UNKNOWN, "")),
        (
            {"mode": "merge-group", "merge_group_base": "q"},
            (DeltaOutcome.RESOLVED, "q"),
        ),
        ({"mode": "merge-group"}, (DeltaOutcome.BASE_UNKNOWN, "")),
    ],
)
def test_select_base_commit(
    kwargs: dict[str, Any], expected: tuple[DeltaOutcome, str]
) -> None:
    selection = select_base_commit(**kwargs)
    assert (selection.outcome, selection.sha) == expected


# ── real histories through the Action's run.sh ─────────────────────────────


class _Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, name: str) -> str:
        (self.path / name).write_text(name, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def pr_merge_commit(self, branch: str) -> str:
        """What GitHub checks out on pull_request: main tip + the PR, first parent = main."""
        self.git("checkout", "-q", "--detach", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", branch)
        return self.git("rev-parse", "HEAD")


def _history(tmp_path: Path, scenario: str) -> tuple[_Repo, str, str]:
    """Build *scenario*; return (repo, checkout mode, expected base)."""
    r = _Repo(tmp_path / "repo")
    r.commit("a")
    fork = r.commit("b")
    r.git("checkout", "-q", "-b", "pr")
    r.commit("pr1")
    r.git("checkout", "-q", "main")
    r.commit("c")
    tip = r.commit("d")
    if scenario == "old-branch-merge-commit":
        r.pr_merge_commit("pr")
        return r, "merge-commit", tip
    if scenario == "old-branch-head":
        r.git("checkout", "-q", "pr")
        return r, "head", fork
    if scenario == "rebased-head":
        r.git("checkout", "-q", "pr")
        r.git("rebase", "-q", "main")
        return r, "head", tip
    if scenario == "merged-main-head":
        r.git("checkout", "-q", "pr")
        r.git("merge", "-q", "--no-ff", "-m", "sync", "main")
        return r, "head", tip
    if scenario == "merged-main-merge-commit":
        r.git("checkout", "-q", "pr")
        r.git("merge", "-q", "--no-ff", "-m", "sync", "main")
        r.git("checkout", "-q", "main")
        newer = r.commit("e")
        r.pr_merge_commit("pr")
        return r, "merge-commit", newer
    raise AssertionError(scenario)


def _gh_stub(bin_dir: Path, data: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do case "$a" in\n'
        f'  */actions/artifacts/*/zip) cat "{data}/snapshot.zip"; exit 0 ;;\n'
        f'  */actions/artifacts) cat "{data}/artifacts.json"; exit 0 ;;\n'
        f'  */runs) cat "{data}/runs.json"; exit 0 ;;\n'
        "esac; done\n"
        'echo "unexpected: $*" >&2; exit 1\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _fetch(
    tmp_path: Path,
    repo: _Repo,
    mode: str,
    *,
    publish_for: str | None,
    zip_members: dict[str, str] | None = None,
) -> dict[str, str]:
    """Run the Action's run.sh. *zip_members* None = the download fails."""
    require_bash()
    data = tmp_path / "data"
    data.mkdir()
    artifacts: list[Any] = []
    runs: list[Any] = []
    if publish_for:
        artifacts = [
            {
                "id": 7,
                "name": f"abicheck-main-{publish_for}",
                "expired": False,
                "digest": "sha256:aa",
                "workflow_run": {"id": 70},
            }
        ]
        runs = [_run(70, sha=publish_for)]
    # The stub answers as `gh api --paginate --jq .<key>` does: the bare array.
    (data / "artifacts.json").write_text(json.dumps(artifacts))
    (data / "runs.json").write_text(json.dumps(runs))
    members = (
        {"snapshot.json": '{"library": "base"}'} if zip_members is None else zip_members
    )
    if zip_members != {"__download_fails__": ""}:
        with zipfile.ZipFile(data / "snapshot.zip", "w") as zf:
            for member, text in members.items():
                zf.writestr(member, text)
    _gh_stub(tmp_path / "bin", data)
    out = tmp_path / "gh_output"
    out.write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": os.pathsep.join(
                [str(tmp_path / "bin"), str(Path(sys.executable).parent), env["PATH"]]
            ),
            "GITHUB_OUTPUT": str(out),
            "REPO": "o/r",
            "EVENT_NAME": "pull_request",
            "INPUT_WORKFLOW": _WF,
            "INPUT_MODE": mode,
            "INPUT_BRANCH": "main",
            "INPUT_WAIT_TIMEOUT": "0",
            "INPUT_DESTINATION": str(tmp_path / "dest"),
        }
    )
    result = subprocess.run(
        [bash_executable(), str(_FETCH_SH)],
        cwd=repo.path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    fields: dict[str, str] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        fields[key] = value  # last write wins, as in GitHub
    return fields


_SCENARIOS = [
    "old-branch-merge-commit",
    "old-branch-head",
    "rebased-head",
    "merged-main-head",
    "merged-main-merge-commit",
]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_run_sh_measures_against_the_constructed_base(
    tmp_path: Path, scenario: str
) -> None:
    repo, mode, base = _history(tmp_path, scenario)
    fields = _fetch(tmp_path, repo, mode, publish_for=base)
    assert fields["outcome"] == "resolved"
    assert fields["base-sha"] == base
    assert Path(fields["snapshot-path"]).read_text() == '{"library": "base"}'


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_run_sh_never_takes_a_different_commits_snapshot(
    tmp_path: Path, scenario: str
) -> None:
    repo, mode, base = _history(tmp_path, scenario)
    other = (
        repo.git("rev-parse", "main~1")
        if base != repo.git("rev-parse", "main~1")
        else repo.git("rev-parse", "main")
    )
    fields = _fetch(tmp_path, repo, mode, publish_for=other)
    assert fields["outcome"] == "base_not_snapshotted"
    assert fields.get("snapshot-path", "") == ""
    assert "unavailable" in fields["message"].lower()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_run_sh_reports_criss_cross_as_ambiguous(tmp_path: Path) -> None:
    r = _Repo(tmp_path / "repo")
    r.commit("a")
    r.git("checkout", "-q", "-b", "pr")
    r.commit("p1")
    r.git("checkout", "-q", "main")
    r.commit("m1")
    r.git("checkout", "-q", "pr")
    r.git("merge", "-q", "--no-ff", "-m", "x1", "main~0")
    pr_merge_parent = r.git("rev-parse", "HEAD^1")
    r.git("checkout", "-q", "main")
    r.git("merge", "-q", "--no-ff", "-m", "x2", pr_merge_parent)
    r.git("checkout", "-q", "pr")
    r.git("reset", "-q", "--hard", "HEAD")
    # Two merge-bases: make them by merging each side's previous tip.
    r.git("checkout", "-q", "-B", "pr", pr_merge_parent)
    r.git("merge", "-q", "--no-ff", "-m", "x3", "main~1")
    bases = r.git("merge-base", "--all", "main", "pr").split()
    if len(bases) < 2:
        pytest.skip("history did not produce a criss-cross on this git version")
    fields = _fetch(tmp_path, r, "head", publish_for=bases[0])
    assert fields["outcome"] == "base_ambiguous"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_run_sh_shallow_merge_commit_is_base_unknown(tmp_path: Path) -> None:
    repo, _mode, _base = _history(tmp_path, "old-branch-merge-commit")
    shallow = _Repo.__new__(_Repo)
    shallow.path = tmp_path / "shallow"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            f"file://{repo.path}",
            str(shallow.path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-q", "--detach", repo.git("rev-parse", "HEAD")],
        cwd=shallow.path,
        check=False,
        capture_output=True,
    )
    fields = _fetch(tmp_path, shallow, "merge-commit", publish_for=None)
    assert fields["outcome"] == "base_unknown"


def test_main_snapshot_refuses_both_library_and_snapshot(tmp_path: Path) -> None:
    require_bash()
    out = tmp_path / "o"
    out.write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), env["PATH"]]),
            "GITHUB_OUTPUT": str(out),
            "RUNNER_TEMP": str(tmp_path),
            "SNAPSHOT_SHA": _BASE,
            "INPUT_LIBRARY": "lib.so",
            "INPUT_SNAPSHOT": str(out),
        }
    )
    result = subprocess.run(
        [bash_executable(), str(_SNAP_SH)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not both" in result.stdout + result.stderr


def test_main_snapshot_names_an_adopted_snapshot_for_its_commit(tmp_path: Path) -> None:
    require_bash()
    out = tmp_path / "o"
    out.write_text("")
    snap = tmp_path / "s.json"
    snap.write_text("{}")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), env["PATH"]]),
            "GITHUB_OUTPUT": str(out),
            "RUNNER_TEMP": str(tmp_path),
            "SNAPSHOT_SHA": _BASE,
            "INPUT_SNAPSHOT": str(snap),
            "INPUT_PROFILE": "x86",
        }
    )
    result = subprocess.run(
        [bash_executable(), str(_SNAP_SH)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"artifact-name={_NAME}-x86" in out.read_text()


# ── the CLI commands the Action shells call, in-process ────────────────────

from click.testing import CliRunner  # noqa: E402

from abicheck.frontends.action.cli import action_cli  # noqa: E402


def _outputs(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
    )


def test_cli_snapshot_name() -> None:
    runner = CliRunner()
    ok = runner.invoke(action_cli, ["snapshot-name", _BASE, "--profile", "p"])
    assert (ok.exit_code, ok.output.strip()) == (0, f"{_NAME}-p")
    bad = runner.invoke(action_cli, ["snapshot-name", "abc"])
    assert bad.exit_code == 2
    assert "not a full commit SHA" in bad.output


@pytest.mark.parametrize(
    ("args", "code", "outcome", "base"),
    [
        (["--mode", "merge-commit", "--head-parents", "p1 p2"], 0, "resolved", "p1"),
        (["--mode", "head", "--merge-bases", "m n"], 3, "base_ambiguous", ""),
        (["--mode", "merge-group"], 3, "base_unknown", ""),
    ],
)
def test_cli_select_base(
    tmp_path: Path, args: list[str], code: int, outcome: str, base: str
) -> None:
    out = tmp_path / "o"
    result = CliRunner().invoke(
        action_cli, ["select-base", *args, "--github-output", str(out)]
    )
    fields = _outputs(out)
    assert (result.exit_code, fields["outcome"], fields["base-sha"]) == (
        code,
        outcome,
        base,
    )
    assert fields["message"]


def _resolve(
    tmp_path: Path, artifacts: object, runs: object, *extra: str
) -> tuple[int, dict[str, str], str]:
    a, r, out = tmp_path / "a.json", tmp_path / "r.json", tmp_path / "o"
    a.write_text(json.dumps(artifacts))
    r.write_text(json.dumps(runs))
    result = CliRunner().invoke(
        action_cli,
        [
            "resolve-snapshot",
            str(a),
            str(r),
            "--repository",
            "o/r",
            "--workflow",
            _WF,
            "--branch",
            "main",
            "--base-sha",
            _BASE,
            "--artifact-name",
            _NAME,
            "--github-output",
            str(out),
            *extra,
        ],
    )
    return result.exit_code, (_outputs(out) if out.exists() else {}), result.output


def test_cli_resolve_snapshot_resolved(tmp_path: Path) -> None:
    code, fields, _ = _resolve(
        tmp_path, {"artifacts": [_art(4, 10)]}, {"workflow_runs": [_run(10)]}
    )
    assert (code, fields["outcome"], fields["artifact-id"], fields["run-id"]) == (
        0,
        "resolved",
        "4",
        "10",
    )
    assert "this PR" in fields["message"]


def test_cli_resolve_snapshot_refusal_reports_rejections(tmp_path: Path) -> None:
    code, fields, output = _resolve(
        tmp_path,
        [_art(4, 10)],
        [_run(10, event="pull_request")],
        "--merge-group",
    )
    assert (code, fields["outcome"], fields["artifact-id"]) == (
        3,
        "untrusted_source",
        "",
    )
    assert "rejected artifact 4: wrong-event" in output


def test_cli_resolve_snapshot_lookup_failed(tmp_path: Path) -> None:
    code, fields, _ = _resolve(tmp_path, [], [], "--lookup-failed")
    assert (code, fields["outcome"]) == (3, "lookup_failed")


def test_cli_resolve_snapshot_rejects_a_non_array(tmp_path: Path) -> None:
    code, _fields, output = _resolve(tmp_path, {"artifacts": {}}, [])
    assert code == 1
    assert "does not hold a 'artifacts' array" in output


def test_select_base_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown base mode"):
        select_base_commit("sideways")


def test_unreadable_rows_are_ignored_not_trusted() -> None:
    result = resolve_snapshot(
        ["not a row", {"name": _NAME, "id": 1, "workflow_run": "x"}],
        ["not a run", {"id": ""}],
        _EXPECT,
    )
    assert result.outcome is DeltaOutcome.UNTRUSTED_SOURCE


def test_a_rerun_takes_the_newest_upload_and_reports_the_other() -> None:
    result = resolve_snapshot(
        [_art(3, 10), _art(8, 11, digest="sha256:bb")], [_run(10), _run(11)], _EXPECT
    )
    assert (result.artifact_id, result.run_id) == ("8", "11")
    assert result.rejected == ("artifact 3: superseded by newer upload 8",)


@pytest.mark.parametrize("bad", ["\u03bb", "x\u0663", "a b", "a/b"])
def test_artifact_name_profile_is_ascii_only(bad: str) -> None:
    with pytest.raises(ValueError):
        snapshot_artifact_name(_BASE, bad)


_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@_GIT
def test_run_sh_keeps_what_destination_already_holds(tmp_path: Path) -> None:
    repo, mode, base = _history(tmp_path, "old-branch-merge-commit")
    keep = tmp_path / "dest" / "build" / "libfoo.so"
    keep.parent.mkdir(parents=True)
    keep.write_text("built")
    fields = _fetch(tmp_path, repo, mode, publish_for=base)
    assert keep.read_text() == "built"
    snap = Path(fields["snapshot-path"])
    assert snap.name == "snapshot.json"
    assert snap.parent.parent == tmp_path / "dest"


@_GIT
@pytest.mark.parametrize(
    "members",
    [
        {"__download_fails__": ""},
        {"other.json": "{}"},
        {"notes.json": "{}", "nested/snapshot.json": "{}"},
    ],
    ids=["download fails", "no snapshot.json", "snapshot.json not at the root"],
)
def test_run_sh_never_reports_resolved_without_the_snapshot(
    tmp_path: Path, members: dict[str, str]
) -> None:
    repo, mode, base = _history(tmp_path, "old-branch-merge-commit")
    fields = _fetch(tmp_path, repo, mode, publish_for=base, zip_members=members)
    assert fields["outcome"] == "lookup_failed"
    assert fields.get("snapshot-path", "") == ""
    assert fields["message"].startswith("Delta unavailable")


def _snap_env(tmp_path: Path, **inputs: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{tmp_path}/argv"\n'
        'while [[ $# -gt 0 ]]; do [[ "$1" == -o ]] && echo "{}" > "$2"; shift; done\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": os.pathsep.join(
                [str(bin_dir), str(Path(sys.executable).parent), env["PATH"]]
            ),
            "GITHUB_OUTPUT": str(tmp_path / "o"),
            "RUNNER_TEMP": str(tmp_path),
            "SNAPSHOT_SHA": _BASE,
            **inputs,
        }
    )
    (tmp_path / "o").write_text("")
    return env


def test_main_snapshot_forwards_every_evidence_input(tmp_path: Path) -> None:
    require_bash()
    env = _snap_env(
        tmp_path,
        INPUT_LIBRARY="build/libfoo.so",
        INPUT_HEADER="include/a.h\ninclude/b\n",
        INPUT_DUMP_ARGS="-I include\n\n--define\nFOO=1\n",
    )
    result = subprocess.run(
        [bash_executable(), str(_SNAP_SH)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    argv = (tmp_path / "argv").read_text().splitlines()
    out = _outputs(tmp_path / "o")["snapshot-path"]
    assert argv == [
        "dump", "build/libfoo.so", "-H", "include/a.h", "-H", "include/b",
        "-I", "include", "--define", "FOO=1", "--label", _BASE, "-o", out,
    ]  # fmt: skip


@_GIT
def test_main_snapshot_refuses_a_checkout_of_another_commit(tmp_path: Path) -> None:
    require_bash()
    repo = _Repo(tmp_path / "repo")
    repo.commit("a")
    snap = tmp_path / "s.json"
    snap.write_text("{}")
    env = _snap_env(tmp_path, INPUT_SNAPSHOT=str(snap))
    result = subprocess.run(
        [bash_executable(), str(_SNAP_SH)],
        env=env,
        cwd=repo.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not " + _BASE in result.stdout + result.stderr


def test_main_snapshot_uses_a_new_directory_per_invocation(tmp_path: Path) -> None:
    require_bash()
    snap = tmp_path / "s.json"
    snap.write_text("{}")
    paths = []
    for _ in range(2):
        env = _snap_env(tmp_path, INPUT_SNAPSHOT=str(snap))
        result = subprocess.run(
            [bash_executable(), str(_SNAP_SH)],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        paths.append(_outputs(tmp_path / "o")["snapshot-path"])
    assert paths[0] != paths[1]


def test_a_successful_run_with_no_listed_artifact_is_not_claimed_expired() -> None:
    result = resolve_snapshot([], [_run(10)], _EXPECT)
    assert result.outcome is DeltaOutcome.SNAPSHOT_MISSING


def test_main_snapshot_refuses_a_truncated_adopted_snapshot(tmp_path: Path) -> None:
    require_bash()
    snap = tmp_path / "s.json"
    snap.write_text('{"library": ')
    env = _snap_env(tmp_path, INPUT_SNAPSHOT=str(snap))
    result = subprocess.run(
        [bash_executable(), str(_SNAP_SH)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not a readable JSON document" in result.stdout + result.stderr
    assert "artifact-name" not in (tmp_path / "o").read_text()

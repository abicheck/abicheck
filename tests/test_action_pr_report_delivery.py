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

"""The turnkey fork-PR reporting path, end to end on both sides.

Producer: the main Action's ``pr-comment-via`` (``action/pr-report-delivery.sh``
plus ``action/run.sh``'s artifact branch). Publisher: the reusable
``.github/workflows/report-pr.yml``. The invariants stated here are the ones
the integration is for:

* **one path per run** -- a run either posts directly or stages an artifact,
  never both, so wiring both sides cannot double-post;
* **no write token on the artifact path** -- run.sh makes no GitHub API call;
* **the two sides agree by default** -- the artifact name and comment
  identity derived from one ``pr-comment-key`` match on both sides;
* **the staged document is what the publisher reads** -- a staged report
  round-trips through ``read-analysis-context`` and the comment renderer;
* **"nothing uploaded" is a skip, every other refusal still fails**.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import _yaml_fast
import pytest
from _abicheck_subprocess import abicheck_env, abicheck_module_command
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _run_action,
    _stub_abicheck,
)
from _workflow_exec import (
    bash_executable,
    find_run_step,
    make_workspace,
    require_bash,
    run_step,
)
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot

pytestmark = REQUIRES_POSIX_SHELL

ROOT = Path(__file__).resolve().parents[1]
DELIVERY_SH = ROOT / "action" / "pr-report-delivery.sh"
ACTION_YML = ROOT / "action.yml"
REPORT_PR = "report-pr.yml"
REPORT_PR_PATH = ROOT / ".github" / "workflows" / REPORT_PR
REPO = "owner/lib"
SHA = "0123456789abcdef0123456789abcdef01234567"
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _bash(args: list[str], env: dict[str, str], cwd: Path) -> tuple[int, dict, str]:
    require_bash()
    bash = bash_executable()
    out = cwd / "github_output"
    out.write_text("", encoding="utf-8")
    full = {
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(cwd),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_REPOSITORY": REPO,
        "RUNNER_TEMP": str(cwd),
        **env,
    }
    proc = subprocess.run(
        [bash, *args], env=full, cwd=cwd, capture_output=True, text=True, check=False
    )
    outputs = dict(
        line.split("=", 1)
        for line in out.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return proc.returncode, outputs, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# resolve: one path per run, over the whole small input domain
# ---------------------------------------------------------------------------


def _oracle(via, event, head, actor, pr_comment, on, mode) -> str:
    """Written from pr-comment-via's description, not from the script."""
    if via == "direct":
        return "direct"
    comments = (
        pr_comment == "true"
        and mode == "compare"
        and on != "never"
        and event in ("pull_request", "pull_request_target")
    )
    # GitHub hands a read-only token to a fork's (or Dependabot's)
    # pull_request run; pull_request_target always gets a write token.
    read_only = event == "pull_request" and (actor == "dependabot[bot]" or head != REPO)
    delivery = "artifact" if via == "artifact" or read_only else "direct"
    return "none" if delivery == "artifact" and not comments else delivery


_DOMAIN = list(
    itertools.product(
        ["direct", "artifact", "auto"],
        ["pull_request", "pull_request_target", "push"],
        [REPO, "fork/lib", ""],
        ["someone", "dependabot[bot]"],
        ["true", "false"],
        ["changes", "never"],
        ["compare", "dump"],
    )
)


def test_resolve_matches_the_oracle_over_the_whole_domain(tmp_path: Path) -> None:
    mismatches = []
    for case in _DOMAIN:
        via, event, head, actor, pr_comment, on, mode = case
        rc, out, log = _bash(
            [str(DELIVERY_SH), "resolve"],
            {
                "INPUT_PR_COMMENT_VIA": via,
                "GITHUB_EVENT_NAME": event,
                "PR_HEAD_REPOSITORY": head,
                "GITHUB_ACTOR_LOGIN": actor,
                "INPUT_PR_COMMENT": pr_comment,
                "INPUT_PR_COMMENT_ON": on,
                "INPUT_MODE": mode,
            },
            tmp_path,
        )
        assert rc == 0, log
        if out["delivery"] != _oracle(*case):
            mismatches.append((case, out["delivery"]))
    assert mismatches == []


def test_auto_never_uploads_for_a_same_repository_human_pr(tmp_path: Path) -> None:
    """The dedupe half that matters most in practice: a same-repo PR under
    `auto` posts directly, so report-pr.yml finds no artifact and skips."""
    rc, out, _ = _bash(
        [str(DELIVERY_SH), "resolve"],
        {
            "INPUT_PR_COMMENT_VIA": "auto",
            "GITHUB_EVENT_NAME": "pull_request",
            "PR_HEAD_REPOSITORY": REPO,
            "GITHUB_ACTOR_LOGIN": "maintainer",
        },
        tmp_path,
    )
    assert (rc, out["delivery"]) == (0, "direct")


@pytest.mark.parametrize("via", ["Auto", "artifacts", "", " direct"])
def test_resolve_rejects_unknown_values(tmp_path: Path, via: str) -> None:
    env = {"INPUT_PR_COMMENT_VIA": via} if via else {"INPUT_PR_COMMENT_VIA": "x"}
    rc, out, log = _bash([str(DELIVERY_SH), "resolve"], env, tmp_path)
    assert rc == 1 and "delivery" not in out
    assert "pr-comment-via must be" in log


@pytest.mark.parametrize("name", ["a#b", "a/b", 'a"b', "a:b", "a*b"])
def test_resolve_rejects_artifact_names_upload_artifact_refuses(
    tmp_path: Path, name: str
) -> None:
    rc, _, log = _bash(
        [str(DELIVERY_SH), "resolve"],
        {"INPUT_PR_COMMENT_VIA": "artifact", "INPUT_PR_COMMENT_ARTIFACT_NAME": name},
        tmp_path,
    )
    assert rc == 1 and "cannot hold" in log


# ---------------------------------------------------------------------------
# Producer and publisher derive the same artifact name and identity
# ---------------------------------------------------------------------------

_KEYS = ["", "linux-gcc", "  padded  ", "x/y#z:w", "ünï", "k" * 80, "a b\tc"]


@pytest.mark.parametrize("key", _KEYS)
def test_both_sides_derive_the_same_artifact_name(tmp_path: Path, key: str) -> None:
    rc, produced, log = _bash(
        [str(DELIVERY_SH), "resolve"],
        {"INPUT_PR_COMMENT_VIA": "artifact", "INPUT_PR_COMMENT_KEY": key},
        tmp_path,
    )
    assert rc == 0, log
    step = find_run_step(REPORT_PR, "publish", "Resolve publication inputs")
    result = run_step(
        step,
        workspace=make_workspace(tmp_path / "pub"),
        env={
            "INPUT_COMMENT_KEY": key,
            "INPUT_ARTIFACT_NAME": "",
            "INPUT_REPORT_FROM": "aggregate.json",
            "INPUT_PROVENANCE_FROM": "",
            "INPUT_EXPECT_WORKFLOW": "",
            "TRIGGERING_WORKFLOW_PATH": ".github/workflows/abi.yml",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.outputs["artifact-name"] == produced["artifact-name"]
    # The identity carries the same sanitized key as run.sh's own marker.
    sanitized = produced["artifact-name"].removeprefix("abicheck-pr-report")
    assert (
        result.outputs["identity"]
        == f"abicheck:{sanitized.removeprefix('-') or 'default'}"
    )
    assert result.outputs["provenance-from"] == "aggregate.json"
    assert result.outputs["expect-workflow"] == ".github/workflows/abi.yml"


# ---------------------------------------------------------------------------
# run.sh: the artifact branch stages and never touches the API
# ---------------------------------------------------------------------------


def _real_report(tmp_path: Path) -> tuple[int, dict]:
    keep = Function(name="keep", mangled="keep", return_type="void")
    gone = Function(name="gone", mangled="gone", return_type="void")
    old, new = tmp_path / "o.json", tmp_path / "n.json"
    save_snapshot(
        AbiSnapshot(library="libx.so.1", version="1", functions=[keep, gone]), old
    )
    save_snapshot(AbiSnapshot(library="libx.so.1", version="2", functions=[keep]), new)
    out = tmp_path / "real.json"
    res = CliRunner().invoke(main, ["compare", str(old), str(new), "-o", f"json={out}"])
    assert out.exists(), res.output
    return res.exit_code, json.loads(out.read_text())


def _pr_run(tmp_path: Path, delivery: str, key: str = "") -> tuple[dict, Path]:
    code, report = _real_report(tmp_path)
    bindir = _stub_abicheck(
        tmp_path, exit_code=code, payload=json.dumps(report).encode()
    )
    calls = tmp_path / "gh_calls"
    gh = bindir / "gh"
    gh.write_text(
        f'#!/usr/bin/env bash\necho "$*" >> "{calls}"\nexit 0\n', encoding="utf-8"
    )
    gh.chmod(0o755)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": SHA}}}))
    env = {
        **_compare_env(tmp_path),
        "INPUT_PR_COMMENT": "true",
        "INPUT_PR_COMMENT_ON": "always",
        "INPUT_PR_COMMENT_KEY": key,
        "INPUT_FAIL_ON_BREAKING": "false",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": REPO,
        "ABICHECK_PR_COMMENT_DELIVERY": delivery,
    }
    return _run_action(tmp_path, env, bindir), calls


def test_artifact_delivery_stages_the_report_and_makes_no_api_call(
    tmp_path: Path,
) -> None:
    outputs, calls = _pr_run(tmp_path, "artifact", key="linux gcc")
    assert outputs["_exit"] == 0, outputs["_stdout"]
    assert not calls.exists(), calls.read_text()
    staged = Path(outputs["pr-report-dir"])
    assert sorted(p.name for p in staged.iterdir()) == ["abi-report-linux-gcc.json"]
    assert json.loads((staged / "abi-report-linux-gcc.json").read_text()) == json.loads(
        (tmp_path / "payload.bin").read_text()
    )


def test_none_delivery_neither_stages_nor_posts(tmp_path: Path) -> None:
    outputs, calls = _pr_run(tmp_path, "none")
    assert outputs["_exit"] == 0, outputs["_stdout"]
    assert "pr-report-dir" not in outputs
    assert not calls.exists()


def test_direct_delivery_posts_and_stages_nothing(tmp_path: Path) -> None:
    """The control: the same run under `direct` does reach the API, so the
    two tests above prove the branch, not a stub that never gets called."""
    outputs, calls = _pr_run(tmp_path, "direct")
    assert outputs["_exit"] == 0, outputs["_stdout"]
    assert "pr-report-dir" not in outputs
    assert "issues/7/comments" in calls.read_text()


# ---------------------------------------------------------------------------
# stage: what the producer uploads is what the publisher reads
# ---------------------------------------------------------------------------


def test_staged_document_round_trips_through_the_publisher_readers(
    tmp_path: Path,
) -> None:
    _, report = _real_report(tmp_path)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "abi-report-default.json").write_text(json.dumps(report))
    rc, out, log = _bash(
        [str(DELIVERY_SH), "stage"],
        {
            "PR_REPORT_DIR": str(staged),
            "ABICHECK_CTX_TESTED_SHA": SHA,
            "ABICHECK_CTX_PR_NUMBER": "7",
            "ABICHECK_CTX_EVENT": "pull_request",
        },
        tmp_path,
    )
    assert rc == 0, log
    assert out["dir"] == str(staged)
    # The context transport never lands among the aggregated targets.
    assert sorted(p.name for p in staged.iterdir()) == [
        "abi-report-default.json",
        "aggregate.json",
    ]
    aggregate = json.loads((staged / "aggregate.json").read_text())
    assert [t["target_id"] for t in aggregate["targets"]] == ["default"]

    cli = abicheck_module_command("abicheck.frontends.action.cli")
    ctx = tmp_path / "ctx.json"
    subprocess.run(
        [
            *cli,
            "read-analysis-context",
            str(staged / "aggregate.json"),
            "--out",
            str(ctx),
        ],
        check=True,
        capture_output=True,
        env=abicheck_env(),
    )
    assert json.loads(ctx.read_text())["tested_sha"] == SHA

    body = tmp_path / "body.md"
    plan = tmp_path / "plan.json"
    subprocess.run(
        [
            *cli, "comment", str(staged / "aggregate.json"),
            "--identity", "abicheck:default", "--run-id", "5", "--run-attempt", "1",
            "--sha", SHA, "--on", "changes",
            "--existing-comments", os.devnull,
            "--body-out", str(body), "--request-out", str(tmp_path / "req.json"),
            "--summary-out", str(tmp_path / "sum.md"), "--plan-out", str(plan),
        ],
        check=True,
        capture_output=True,
        env=abicheck_env(),
    )  # fmt: skip
    assert json.loads(plan.read_text())["action"] == "create"
    assert "func_removed" in body.read_text() and "`gone`" in body.read_text()


def test_stage_refuses_a_malformed_context_and_publishes_no_dir(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "abi-report-default.json").write_text("{}")
    rc, out, _ = _bash(
        [str(DELIVERY_SH), "stage"],
        {"PR_REPORT_DIR": str(staged), "ABICHECK_CTX_TESTED_SHA": "not-a-sha"},
        tmp_path,
    )
    assert rc == 1 and "dir" not in out


# ---------------------------------------------------------------------------
# report-pr.yml: "no artifact" skips, every other refusal fails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "code", "available", "require", "rc", "publish", "reason"),
    [
        ("success", "", "true", "false", 0, "true", None),
        ("failure", "artifact-not-found", "", "false", 0, "false", "no-artifact"),
        ("failure", "artifact-not-found", "", "true", 1, None, None),
        ("failure", "wrong-workflow", "", "false", 1, None, None),
        ("failure", "unverified-tested-sha", "", "false", 1, None, None),
        ("failure", "", "", "false", 1, None, None),
        ("success", "", "false", "false", 1, None, None),
    ],
)
def test_decide_step(tmp_path, outcome, code, available, require, rc, publish, reason):
    step = find_run_step(REPORT_PR, "publish", "Decide whether to publish")
    result = run_step(
        step,
        workspace=make_workspace(tmp_path),
        env={
            "OUTCOME": outcome,
            "REFUSAL_CODE": code,
            "REPORT_AVAILABLE": available,
            "REPORT_FROM": "aggregate.json",
            "ARTIFACT_NAME": "abicheck-pr-report",
            "REQUIRE_ARTIFACT": require,
            "RUN_URL": "https://example.invalid/run/1",
        },
    )
    assert result.returncode == rc, result.stdout + result.stderr
    assert result.outputs.get("publish") == publish
    assert result.outputs.get("skipped-reason") == reason


# ---------------------------------------------------------------------------
# Structure: permissions, pinning, ordering
# ---------------------------------------------------------------------------


def _report_pr() -> dict:
    return _yaml_fast.safe_load(REPORT_PR_PATH.read_text(encoding="utf-8"))


def test_report_pr_permissions_are_exactly_the_three_it_uses() -> None:
    wf = _report_pr()
    assert wf["permissions"] == {}
    assert wf["jobs"]["publish"]["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "write",
    }


def test_report_pr_pins_third_party_actions_and_uses_its_own_checkout() -> None:
    steps = _report_pr()["jobs"]["publish"]["steps"]
    uses = [s["uses"] for s in steps if "uses" in s]
    local = [u for u in uses if u.startswith("./")]
    assert local == [
        "./.report-pr-src/actions/verify-source-run",
        "./.report-pr-src/actions/report",
    ]
    assert all(_PINNED.match(u) for u in uses if not u.startswith("./")), uses
    # The only checkout is abicheck's own, at this workflow's identity: never
    # the contributor's head, never with persisted credentials.
    checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {
        "repository": "${{ steps.identity.outputs.repository }}",
        "ref": "${{ steps.identity.outputs.ref }}",
        "path": ".report-pr-src",
        "persist-credentials": False,
    }


def test_report_pr_defaults_match_the_producer() -> None:
    inputs = _report_pr()[True]["workflow_call"]["inputs"]
    assert inputs["report-from"]["default"] == "aggregate.json"
    assert inputs["comment-key"]["default"] == ""
    assert inputs["artifact-name"]["default"] == ""
    verify = next(
        s for s in _report_pr()["jobs"]["publish"]["steps"] if s.get("id") == "verify"
    )
    assert verify["continue-on-error"] is True
    assert verify["with"]["expect-event"] == "pull_request"
    assert verify["with"]["source-run-id"] == "${{ github.event.workflow_run.id }}"


def test_action_resolves_delivery_before_installing_anything() -> None:
    steps = _yaml_fast.safe_load(ACTION_YML.read_text(encoding="utf-8"))["runs"][
        "steps"
    ]
    names = [s.get("name") for s in steps]
    assert names.index("Resolve PR comment delivery") < names.index("Set up Python")
    run = steps[names.index("Run abicheck")]
    assert (
        run["env"]["ABICHECK_PR_COMMENT_DELIVERY"]
        == "${{ steps.pr-delivery.outputs.delivery }}"
    )
    stage = steps[names.index("Record analysis context for the PR report")]
    upload = steps[names.index("Upload PR report artifact")]
    assert (
        names.index("Run abicheck")
        < names.index(stage["name"])
        < names.index(upload["name"])
    )
    # A gated break exits run.sh non-zero; the fork comment must still go out.
    assert stage["if"].startswith("always() &&") and upload["if"].startswith(
        "always() &&"
    )
    assert stage["env"]["ABICHECK_CTX_TESTED_SHA"] == "${{ github.sha }}"
    assert _PINNED.match(upload["uses"].split()[0])
    assert upload["with"]["if-no-files-found"] == "error"

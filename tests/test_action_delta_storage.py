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

"""The main Action's ``delta-storage: cache``.

Plan: docs/contribute/plans/pr-delta-vs-main-snapshot.md (open item "a
``delta-storage`` input on the main Action"). The lookup is
``actions/fetch-main-snapshot``'s own cache path; what this file checks is
the *main Action's wiring* of it, by executing the three delta steps exactly
as ``action.yml`` declares them -- their ``run`` text, their ``env``
expressions and their ``if`` conditions -- with ``actions/cache/restore``
simulated by an exact-key store. The merged lookup result is then what
``Run abicheck`` receives, and one case carries it through the whole of
``action/run.sh``.

Oracle for every outcome: GitHub's cache-scope rule (a job reads its own
ref's, its base branch's and the default branch's scope) and the plan's
outcome table -- not the decision module's own helpers.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from _action_run_sh_harness import REQUIRES_POSIX_SHELL
from _workflow_exec import bash_executable, require_bash
from _yaml_fast import safe_load
from click.testing import CliRunner
from test_action_delta_base_build import _delta_record, _run_sh
from test_action_delta_baseline import _run

from abicheck.frontends.action.cli import action_cli
from abicheck.frontends.action.main_snapshot import BUILDABLE_OUTCOMES, DeltaOutcome
from abicheck.frontends.action.main_snapshot_cache import (
    CACHE_STAGING_DIR,
    provenance_record,
    snapshot_cache_key,
)
from abicheck.model import AbiSnapshot
from abicheck.report.delta_baseline import DeltaBaseline
from abicheck.serialization import save_snapshot
from abicheck.workflows.delta_operand import operand_record

pytestmark = REQUIRES_POSIX_SHELL

_ROOT = Path(__file__).resolve().parents[1]
_ACTION = safe_load((_ROOT / "action.yml").read_text(encoding="utf-8"))
_STEPS = {s.get("id"): s for s in _ACTION["runs"]["steps"] if s.get("id")}
_DEFAULTS = {k: str(v.get("default", "")) for k, v in _ACTION["inputs"].items()}
_REPO = "o/r"


# ── a minimal composite-step runner for the three delta steps ───────────────

_EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def _value(ref: str, ctx: dict[str, str]) -> str:
    ref = ref.strip()
    if ref.startswith("'") and ref.endswith("'"):
        return ref[1:-1]
    if ref.startswith("steps."):
        return ctx.get(ref, "")  # an output a step did not write is ""
    return ctx[ref]  # KeyError: an expression this runner does not model


def _expand(text: str, ctx: dict[str, str]) -> str:
    """``${{ a || b }}`` with dotted references only -- every expression the
    delta steps and their consumers use."""

    def one(match: re.Match[str]) -> str:
        for part in match.group(1).split("||"):
            got = _value(part, ctx)
            if got:
                return got
        return ""

    return _EXPR.sub(one, text)


_CLAUSE = re.compile(r"^\s*(\S+)\s*(==|!=)\s*'([^']*)'\s*$")


def _condition(text: str, ctx: dict[str, str]) -> bool:
    """An ``if:`` made of ``X == 'v'`` / ``X != 'v'`` clauses joined by ``&&``."""
    result = True
    for clause in text.split("&&"):
        match = _CLAUSE.match(clause)
        assert match, f"unmodelled clause {clause!r}"
        left, op, right = match.groups()
        got = _value(left, ctx)
        result = result and ((got == right) if op == "==" else (got != right))
    return result


def _outputs(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        fields[key] = value  # last write wins, as in GitHub
    return fields


def _run_step(
    step: dict, ctx: dict[str, str], env: dict[str, str], cwd: Path, out: Path
) -> int:
    require_bash()
    script = out.parent / f"{step['id']}.sh"
    script.write_text(_expand(step["run"], ctx), encoding="utf-8")
    out.write_text("")
    step_env = {
        **env,
        **{k: _expand(str(v), ctx) for k, v in step.get("env", {}).items()},
    }
    step_env["GITHUB_OUTPUT"] = str(out)
    # The composite's `shell: bash` is `bash --noprofile --norc -eo pipefail {0}`.
    proc = subprocess.run(
        [bash_executable(), "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env=step_env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    (out.parent / f"{step['id']}.log").write_text(proc.stdout + proc.stderr)
    return proc.returncode


@dataclass
class Lookup:
    ctx: dict[str, str]
    restored: bool
    verified: bool
    log: str
    #: What `Run abicheck` receives, evaluated from its own env block.
    run_env: dict[str, str] = field(default_factory=dict)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _pr_checkout(ws: Path) -> str:
    """A PR merge commit whose first parent (the base) is returned."""
    ws.mkdir(parents=True)
    _git(ws, "init", "-q", "-b", "main")
    for key, value in (
        ("user.email", "t@e"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(ws, "config", key, value)
    (ws / "a").write_text("a")
    _git(ws, "add", "a")
    _git(ws, "commit", "-q", "-m", "a")
    _git(ws, "checkout", "-q", "-b", "pr")
    (ws / "b").write_text("b")
    _git(ws, "add", "b")
    _git(ws, "commit", "-q", "-m", "b")
    _git(ws, "checkout", "-q", "--detach", "main")
    (ws / "c").write_text("c")
    _git(ws, "add", "c")
    _git(ws, "commit", "-q", "-m", "c")
    base = _git(ws, "rev-parse", "HEAD")
    _git(ws, "merge", "-q", "--no-ff", "-m", "merge", "pr")
    return base


def _entry(store: Path, commit: str, *, operand: bool = True, **prov: str) -> Path:
    """What `actions/main-snapshot` with `storage: cache` saves for *commit*."""
    key = snapshot_cache_key(commit)
    entry = store / key
    entry.mkdir(parents=True)
    save_snapshot(
        AbiSnapshot(library="libnew.so", version="1"), entry / "snapshot.json"
    )
    record = provenance_record(
        sha=commit, key=key, repository=_REPO, ref="refs/heads/main"
    )
    record.update(prov)
    (entry / "provenance.json").write_text(json.dumps(record))
    if operand:
        (entry / "operand.json").write_text(
            json.dumps(operand_record("build/libnew.so"))
        )
    return entry


def _lookup(
    tmp_path: Path,
    store: Path,
    *,
    event: str = "pull_request",
    base_ref: str = "main",
    ref: str = "refs/pull/7/merge",
    merge_group_base: str = "",
    restore_fails: bool = False,
    inputs: dict[str, str] | None = None,
    path_prefix: Path | None = None,
) -> Lookup:
    ws = tmp_path / "ws"
    if not ws.exists():
        _pr_checkout(ws)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(exist_ok=True)
    ctx = {f"inputs.{k}": v for k, v in _DEFAULTS.items()}
    ctx.update(
        {
            f"inputs.{k}": v
            for k, v in {
                "delta-baseline": "main",
                "delta-storage": "cache",
                **(inputs or {}),
            }.items()
        }
    )
    ctx.update(
        {
            "github.action_path": str(_ROOT),
            "github.repository": _REPO,
            "github.event_name": event,
            "github.base_ref": base_ref,
            "github.event.merge_group.base_sha": merge_group_base,
            "github.event.repository.default_branch": "main",
            "runner.temp": str(runner_temp),
            "inputs.github-token": "",
        }
    )
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("INPUT_", "GITHUB_"))
    }
    path = [str(Path(sys.executable).parent), env["PATH"]]
    if path_prefix is not None:
        path.insert(0, str(path_prefix))
    env.update(
        {
            "PATH": os.pathsep.join(path),
            "GITHUB_WORKSPACE": str(ws),
            "GITHUB_REF": ref,
            "GITHUB_REPOSITORY": _REPO,
            "RUNNER_TEMP": str(runner_temp),
        }
    )
    fetch = _STEPS["delta-base"]
    rc = _run_step(fetch, ctx, env, ws, tmp_path / "fetch.out")
    ctx["steps.delta-base.outcome"] = "success" if rc == 0 else "failure"
    for name, value in _outputs(tmp_path / "fetch.out").items():
        ctx[f"steps.delta-base.outputs.{name}"] = value
    log = (tmp_path / "delta-base.log").read_text()

    restore = _STEPS["delta-cache-restore"]
    restored = _condition(restore["if"], ctx)
    if restored:
        key = _expand(restore["with"]["key"], ctx)
        if restore_fails:
            ctx["steps.delta-cache-restore.outcome"] = "failure"
        else:
            ctx["steps.delta-cache-restore.outcome"] = "success"
            hit = (store / key).is_dir()  # exact key only: no restore-keys
            if hit:
                shutil.copytree(store / key, ws / restore["with"]["path"])
            ctx["steps.delta-cache-restore.outputs.cache-hit"] = (
                "true" if hit else "false"
            )
            ctx["steps.delta-cache-restore.outputs.cache-matched-key"] = (
                key if hit else ""
            )

    verify = _STEPS["delta-cache-verify"]
    verified = _condition(verify["if"], ctx)
    if verified:
        _run_step(verify, ctx, env, ws, tmp_path / "verify.out")
        for name, value in _outputs(tmp_path / "verify.out").items():
            ctx[f"steps.delta-cache-verify.outputs.{name}"] = value
        log += (tmp_path / "delta-cache-verify.log").read_text()

    run_env = {
        k: _expand(str(v), ctx)
        for k, v in _STEPS["run-abicheck"]["env"].items()
        if k.startswith("ABICHECK_DELTA_")
    }
    run_env["delta-outcome"] = _expand(
        _ACTION["outputs"]["delta-outcome"]["value"], ctx
    )
    return Lookup(ctx, restored, verified, log, run_env)


# ── the outcome table, through the main Action's own steps ─────────────────


def test_resolved_entry_reaches_the_comparison_with_its_operand_record(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    base = _pr_checkout(tmp_path / "ws")
    entry = _entry(store, base)
    got = _lookup(tmp_path, store)
    assert got.restored and got.verified, got.log
    env = got.run_env
    assert env["ABICHECK_DELTA_OUTCOME"] == env["delta-outcome"] == "resolved"
    assert env["ABICHECK_DELTA_BASE_SHA"] == base
    snap = Path(env["ABICHECK_DELTA_SNAPSHOT"])
    assert snap.read_bytes() == (entry / "snapshot.json").read_bytes()
    assert (snap.parent / "operand.json").read_bytes() == (
        entry / "operand.json"
    ).read_bytes()
    # The copy is outside the workspace; the staging directory is gone.
    assert not (tmp_path / "ws" / CACHE_STAGING_DIR).exists()
    assert str(tmp_path / "runner") in str(snap)
    # No API call and no token: the cache path never needed `actions: read`.
    assert "gh api" not in got.log


def _base_and_lookup(tmp_path: Path, **kw: object) -> tuple[str, Lookup]:
    store = tmp_path / "store"
    base = _pr_checkout(tmp_path / "ws")
    entry_kw = kw.pop("entry", {})
    if entry_kw is not None:
        _entry(store, base, **entry_kw)  # type: ignore[arg-type]
    return base, _lookup(tmp_path, store, **kw)  # type: ignore[arg-type]


# (case, lookup kwargs, outcome, restore ran). A foreign scope is refused
# before anything is restored, even when the right entry exists.
_CASES = [
    ("miss", {"entry": None}, "base_not_snapshotted", True),
    ("pr-into-other-branch", {"base_ref": "release/1"}, "untrusted_source", False),
    ("pr-with-no-base-ref", {"base_ref": ""}, "untrusted_source", False),
    ("merge-queue", {"event": "merge_group", "base_ref": "", "ref": "refs/heads/gh-readonly-queue/main/pr-7-abc", "merge_group_base": "QUEUE"}, "untrusted_source", False),
    ("forged-sha", {"entry": {"sha": "f" * 40}}, "untrusted_source", True),
    ("forged-ref", {"entry": {"ref": "refs/heads/dev"}}, "untrusted_source", True),
    ("forged-repo", {"entry": {"repository": "x/y"}}, "untrusted_source", True),
    ("restore-failed", {"restore_fails": True}, "lookup_failed", True),
]  # fmt: skip


@pytest.mark.parametrize(
    ("case", "kw", "outcome", "restore_ran"), _CASES, ids=[c[0] for c in _CASES]
)
def test_every_unavailable_entry_is_a_named_outcome(
    tmp_path: Path, case: str, kw: dict, outcome: str, restore_ran: bool
) -> None:
    queue_base = "c" * 40
    if kw.get("merge_group_base") == "QUEUE":
        # merge_group's base comes from the event; an entry for it exists.
        kw = {**kw, "merge_group_base": queue_base}
        _entry(tmp_path / "store", queue_base)
    base, got = _base_and_lookup(tmp_path, **kw)
    if case == "merge-queue":
        base = queue_base
    env = got.run_env
    assert env["ABICHECK_DELTA_OUTCOME"] == env["delta-outcome"] == outcome, got.log
    assert got.restored is restore_ran
    assert env["ABICHECK_DELTA_SNAPSHOT"] == ""  # never a snapshot without `resolved`
    assert env["ABICHECK_DELTA_MESSAGE"].startswith("Delta unavailable"), env
    # A base commit was selected, so delta-on-missing-base: build applies.
    assert env["ABICHECK_DELTA_BASE_SHA"] == base
    assert DeltaOutcome(outcome) in BUILDABLE_OUTCOMES
    res = CliRunner().invoke(action_cli, ["delta-buildable", outcome])
    assert res.exit_code == 0


def test_artifact_storage_never_touches_the_cache_steps(tmp_path: Path) -> None:
    store = tmp_path / "store"
    base = _pr_checkout(tmp_path / "ws")
    _entry(store, base)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text("#!/bin/sh\nexit 1\n")
    (bindir / "gh").chmod(0o755)
    got = _lookup(
        tmp_path,
        store,
        inputs={
            "delta-storage": "artifact",
            "delta-snapshot-workflow": "abi-main.yml",
            "delta-wait-timeout": "0",
        },
        path_prefix=bindir,
    )
    assert not got.restored and not got.verified
    # The API is unreachable here: the artifact path says so, it never falls
    # back to the cache entry that does exist.
    assert got.run_env["ABICHECK_DELTA_OUTCOME"] == "lookup_failed"
    assert got.run_env["ABICHECK_DELTA_SNAPSHOT"] == ""


# ── every cache outcome is a valid delta_baseline record ───────────────────

_CACHE_OUTCOMES = ["resolved", "base_not_snapshotted", "untrusted_source", "lookup_failed", "base_unknown", "base_ambiguous"]  # fmt: skip


@pytest.mark.parametrize("outcome", _CACHE_OUTCOMES)
def test_cache_outcomes_are_valid_gate_records(outcome: str) -> None:
    record = DeltaBaseline.from_mapping(
        {
            "comparison_role": "gate",
            "outcome": outcome,
            "base_sha": "a" * 40,
            "branch": "main",
        }
    )
    assert record.outcome == outcome


# ── through the whole of run.sh ─────────────────────────────────────────────


def test_cache_resolved_snapshot_is_the_delta_s_old_side(tmp_path: Path) -> None:
    got = _base_and_lookup(tmp_path / "lookup")[1]
    env = got.run_env
    snap = env["ABICHECK_DELTA_SNAPSHOT"]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outputs, calls = _run(
        run_dir,
        "artifact",
        "resolved",
        snapshot=snap,
        before=lambda: save_snapshot(
            AbiSnapshot(library="libnew.so", version="2"), run_dir / "libnew.so"
        ),
        extra={k: v for k, v in env.items() if k.startswith("ABICHECK_DELTA_")},
    )
    (gate,) = [c for c in calls if snap not in c]
    (delta,) = [c for c in calls if snap in c]
    # The gate's exit code and verdict are the release comparison's.
    assert outputs["_exit"] != 0 and outputs["verdict"] == "BREAKING"
    assert any(a.startswith("report.delta_baseline=@") for a in gate)
    assert outputs["delta-comment-outcome"] == "resolved"


def test_cache_refusal_keeps_the_gate_s_report_and_names_why(tmp_path: Path) -> None:
    got = _base_and_lookup(tmp_path / "lookup", base_ref="release/1")[1]
    env = got.run_env
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outputs, calls = _run(
        run_dir,
        "direct",
        env["ABICHECK_DELTA_OUTCOME"],
        message=env["ABICHECK_DELTA_MESSAGE"],
        extra={k: v for k, v in env.items() if k.startswith("ABICHECK_DELTA_")},
    )
    assert len(calls) == 1  # no delta comparison
    assert outputs["delta-comment-outcome"] == "untrusted_source"
    body = json.loads((run_dir / "gh_bodies").read_text())["body"]
    assert "Delta unavailable" in body


@pytest.mark.parametrize(
    ("case", "kw"),
    [("miss", {"entry": None}), ("pr-into-other-branch", {"base_ref": "release/1"}), ("forged-sha", {"entry": {"sha": "f" * 40}})],
    ids=["miss", "pr-into-other-branch", "forged-sha"],
)  # fmt: skip
def test_delta_on_missing_base_build_measures_a_cache_refusal(
    tmp_path: Path, case: str, kw: dict
) -> None:
    """Every unavailable cache entry selected a commit, so `build` builds it:
    the delta is measured against the built base, the gate's record keeps the
    cache lookup's own outcome, and the step's status stays the gate's."""
    got = _base_and_lookup(tmp_path / "lookup", **kw)[1]
    outcome = got.run_env["ABICHECK_DELTA_OUTCOME"]
    assert outcome in {"base_not_snapshotted", "untrusted_source"}, case
    outputs, calls, body = _run_sh(
        tmp_path / "run",
        extra={
            "ABICHECK_DELTA_OUTCOME": outcome,
            "ABICHECK_DELTA_MESSAGE": got.run_env["ABICHECK_DELTA_MESSAGE"],
        },
    )
    assert outputs["delta-comment-outcome"] == "base_built"
    gate, delta = (c for c in calls if c[0] == "compare")
    assert _delta_record(gate)["outcome"] == outcome
    assert _delta_record(delta)["outcome"] == "base_built"
    assert "pr_only" in body and "release_only" not in body


# ── wiring that the runner above takes as given ────────────────────────────


def test_main_action_restores_with_fetch_main_snapshot_s_exact_path_and_key() -> None:
    fetch_action = safe_load(
        (_ROOT / "actions" / "fetch-main-snapshot" / "action.yml").read_text(
            encoding="utf-8"
        )
    )
    (theirs,) = [
        s
        for s in fetch_action["runs"]["steps"]
        if "actions/cache/restore" in s.get("uses", "")
    ]
    ours = _STEPS["delta-cache-restore"]
    assert ours["uses"] == theirs["uses"]
    assert set(ours["with"]) == {"path", "key"}  # never restore-keys
    assert ours["with"]["path"] == theirs["with"]["path"] == CACHE_STAGING_DIR
    assert ours.get("continue-on-error") is True
    assert _STEPS["delta-cache-verify"].get("continue-on-error") is True


def test_every_lookup_consumer_reads_the_verified_result_first() -> None:
    """`delta-base.outputs.{outcome,message,snapshot-path}` is only the
    pre-restore placeholder with cache storage: every reader must prefer the
    verify step's value."""
    text = (_ROOT / "action.yml").read_text(encoding="utf-8")
    for name in ("outcome", "message", "snapshot-path"):
        bare = len(
            re.findall(rf"steps\.delta-base\.outputs\.{re.escape(name)}\b", text)
        )
        guarded = text.count(
            f"steps.delta-cache-verify.outputs.{name} || steps.delta-base.outputs.{name}"
        )
        assert bare == guarded > 0, name


# ── validate-inputs.sh: the closed vocabulary and what each storage needs ──


def _validate(tmp_path: Path, **inputs: str) -> subprocess.CompletedProcess[str]:
    require_bash()
    return subprocess.run(
        [bash_executable(), str(_ROOT / "action" / "validate-inputs.sh")],
        env={
            "PATH": "/usr/bin:/bin",
            "INPUT_MODE": "compare",
            "INPUT_DELTA_BASELINE": "main",
            "GITHUB_ACTION_PATH": str(_ROOT),
            **inputs,
        },
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )


# (delta-storage, delta-snapshot-workflow) -> (accepted, warning expected)
_STORAGE_INPUTS = [
    ("", "abi-main.yml", True, False),
    ("artifact", "abi-main.yml", True, False),
    ("artifact", "", False, False),
    ("", "", False, False),
    ("cache", "", True, False),
    ("cache", "abi-main.yml", True, True),
    ("s3", "abi-main.yml", False, False),
    ("Cache", "", False, False),
]


@pytest.mark.parametrize(("storage", "workflow", "accepted", "warns"), _STORAGE_INPUTS)
def test_delta_storage_is_validated_before_any_install(
    tmp_path: Path, storage: str, workflow: str, accepted: bool, warns: bool
) -> None:
    inputs = {"INPUT_DELTA_SNAPSHOT_WORKFLOW": workflow}
    if storage:
        inputs["INPUT_DELTA_STORAGE"] = storage
    proc = _validate(tmp_path, **inputs)
    text = proc.stdout + proc.stderr
    assert (proc.returncode == 0) is accepted, text
    assert ("delta-snapshot-workflow is set but has no effect" in text) is warns
    if storage not in ("", "artifact", "cache"):
        assert f"delta-storage '{storage}' is not recognized" in text


# ── every Action manifest's expressions are well-formed ─────────────────────
# The runner rejects the whole action.yml on one malformed expression (this
# PR's first push dropped a `(` from the `Require` step's `if:` and every job
# using `./` failed to load the Action). Nothing above parses an `if:` the
# runner evaluates, so this checks the grammar's bracket and string structure
# over every expression in every manifest.

_MANIFESTS = sorted([_ROOT / "action.yml", *(_ROOT / "actions").glob("*/action.yml")])


def _expression_problem(expr: str) -> str:
    """Why *expr* cannot parse as a GitHub Actions expression, or ``""``.

    Single-quoted strings (``''`` escapes a quote) are skipped; outside them
    ``(``/``)`` and ``[``/``]`` must nest and close.
    """
    stack: list[str] = []
    pairs = {")": "(", "]": "["}
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            i += 1
            while True:
                if i >= len(expr):
                    return "unterminated string"
                if expr[i] == "'":
                    if expr[i + 1 : i + 2] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch in "([":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return f"unexpected {ch!r} at {i}"
        i += 1
    return f"unclosed {stack[-1]!r}" if stack else ""


def _expressions(node: object, where: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}"
            if key == "if" and isinstance(value, str) and "${{" not in value:
                yield here, value
            yield from _expressions(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _expressions(value, f"{where}[{index}]")
    elif isinstance(node, str):
        for match in _EXPR.finditer(node):
            yield where, match.group(1)


@pytest.mark.parametrize(
    "manifest", _MANIFESTS, ids=lambda p: str(p.relative_to(_ROOT))
)
def test_every_expression_in_an_action_manifest_is_well_formed(manifest: Path) -> None:
    doc = safe_load(manifest.read_text(encoding="utf-8"))
    found = list(_expressions(doc))
    assert found, manifest  # the walk reached the manifest's expressions
    bad = [
        (where, expr, why)
        for where, expr in found
        if (why := _expression_problem(expr))
    ]
    assert not bad, bad


@pytest.mark.parametrize(
    ("expr", "ok"),
    [
        ("a && (b || c)", True),
        ("(a || b) != 'x' || f(c, 'y'))", False),  # this PR's first push
        ("((a || b) != 'x' || f(c, 'y'))", True),
        ("f('a)b')", True),
        ("f('it''s')", True),
        ("f('open)", False),
        ("x[0] && (y", False),
        ("x[0)]", False),
    ],
)
def test_expression_checker_on_known_shapes(expr: str, ok: bool) -> None:
    assert (_expression_problem(expr) == "") is ok

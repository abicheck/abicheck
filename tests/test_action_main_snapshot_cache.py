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

"""``storage: cache`` for main-snapshot / fetch-main-snapshot.

Plan: docs/contribute/plans/pr-delta-vs-main-snapshot.md § Storage backends.
Every case is a way a cache entry could be taken for the wrong commit's
snapshot, or a missing/malformed one could read as ``resolved``. The shell
cases run the Actions' own scripts: the producer writes an entry into the
workspace staging directory, and that very directory -- standing in for what
``actions/cache`` saves and restores -- is what the consumer verifies.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from _workflow_exec import bash_executable, require_bash
from click.testing import CliRunner

from abicheck.frontends.action.cli import action_cli
from abicheck.frontends.action.main_snapshot import (
    DeltaOutcome,
    snapshot_artifact_name,
)
from abicheck.frontends.action.main_snapshot_cache import (
    CACHE_STAGING_DIR,
    CacheExpectation,
    cache_scope_refusal,
    provenance_record,
    snapshot_cache_key,
    verify_cached_snapshot,
)

_ROOT = Path(__file__).resolve().parents[1]
_ACTIONS = _ROOT / "actions"
_SNAP_SH = _ACTIONS / "main-snapshot" / "run.sh"
_FETCH_SH = _ACTIONS / "fetch-main-snapshot" / "run.sh"
_VERIFY_SH = _ACTIONS / "fetch-main-snapshot" / "verify-cache.sh"
_BASE = "b" * 40
_REPO = "o/r"
_KEY = snapshot_cache_key(_BASE)
_EXPECT = CacheExpectation(repository=_REPO, branch="main", base_sha=_BASE, key=_KEY)


def _entry(
    tmp_path: Path,
    *,
    snapshot: str | None = '{"library": "base"}',
    provenance: dict[str, str] | str | None = None,
    extra: dict[str, str] | None = None,
) -> Path:
    stage = tmp_path / "stage"
    stage.mkdir()
    if snapshot is not None:
        (stage / "snapshot.json").write_text(snapshot)
    if provenance is None:
        provenance = provenance_record(
            sha=_BASE, key=_KEY, repository=_REPO, ref="refs/heads/main"
        )
    if provenance != "":
        text = provenance if isinstance(provenance, str) else json.dumps(provenance)
        (stage / "provenance.json").write_text(text)
    for name, text in (extra or {}).items():
        (stage / name).write_text(text)
    return stage


def _verify(stage: Path, tmp_path: Path, **kw: object):
    options: dict[str, object] = {"cache_hit": True, "matched_key": _KEY}
    options.update(kw)
    return verify_cached_snapshot(
        stage, _EXPECT, destination=tmp_path / "dest", **options
    )  # type: ignore[arg-type]


# ── the key binds one exact commit ──────────────────────────────────────────

_SHAS = ["a" * 40, "b" * 40, "0123456789abcdef" * 2 + "01234567"]
_PROFILES = ["", "x86", "linux-gcc.13"]


def test_cache_keys_are_injective_over_commit_and_profile() -> None:
    """Distinct (sha, profile) never share a key, and each key names its sha."""
    pairs = list(itertools.product(_SHAS, _PROFILES))
    keys = {snapshot_cache_key(sha, prof): (sha, prof) for sha, prof in pairs}
    assert len(keys) == len(pairs)
    for key, (sha, prof) in keys.items():
        assert key == f"abicheck-main-snapshot-{sha}" + (f"-{prof}" if prof else "")


@pytest.mark.parametrize("bad", ["", "b" * 39, "B" * 39 + "g", "main", "b" * 41])
def test_cache_key_needs_a_full_sha(bad: str) -> None:
    with pytest.raises(ValueError):
        snapshot_cache_key(bad)


def test_cache_key_does_not_share_the_accepted_main_namespace() -> None:
    # update-main-baseline.yml restores `abicheck-baseline-main-<profile>-` by
    # prefix; a snapshot key must never match that lookup, nor an artifact name.
    assert not _KEY.startswith("abicheck-baseline-main")
    assert _KEY != snapshot_artifact_name(_BASE)


# ── what a restored entry may be used for ──────────────────────────────────


def _prov(**override: str) -> dict[str, str]:
    record = provenance_record(
        sha=_BASE, key=_KEY, repository=_REPO, ref="refs/heads/main"
    )
    record.update(override)
    return record


_TABLE = [
    ("miss", {}, {"cache_hit": False, "matched_key": ""}, "base_not_snapshotted"),
    ("prefix match", {}, {"matched_key": snapshot_cache_key("a" * 40)}, "untrusted_source"),
    ("other repo", {"provenance": _prov(repository="evil/r")}, {}, "untrusted_source"),
    ("pr ref", {"provenance": _prov(ref="refs/pull/1/merge")}, {}, "untrusted_source"),
    ("other branch", {"provenance": _prov(ref="refs/heads/dev")}, {}, "untrusted_source"),
    ("other sha", {"provenance": _prov(sha="a" * 40)}, {}, "untrusted_source"),
    ("other key", {"provenance": _prov(key=snapshot_cache_key(_BASE, "p"))}, {}, "untrusted_source"),
    ("old schema", {"provenance": _prov(schema="x/0")}, {}, "untrusted_source"),
    ("provenance not json", {"provenance": "{"}, {}, "untrusted_source"),
    ("provenance array", {"provenance": "[]"}, {}, "untrusted_source"),
    ("no provenance", {"provenance": ""}, {}, "lookup_failed"),
    ("no snapshot", {"snapshot": None}, {}, "lookup_failed"),
    ("truncated snapshot", {"snapshot": '{"library": '}, {}, "lookup_failed"),
    ("snapshot array", {"snapshot": "[]"}, {}, "lookup_failed"),
    ("extra file", {"extra": {"run.sh": "x"}}, {}, "lookup_failed"),
    ("oversize entry", {}, {"max_entry_bytes": 8}, "lookup_failed"),
    ("oversize total", {}, {"max_total_bytes": 100}, "lookup_failed"),
    ("resolved", {}, {}, "resolved"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("entry", "options", "expected"),
    [row[1:] for row in _TABLE],
    ids=[row[0] for row in _TABLE],
)
def test_cached_entry_outcome_table(
    tmp_path: Path, entry: dict, options: dict, expected: str
) -> None:
    result = _verify(_entry(tmp_path, **entry), tmp_path, **options)
    assert result.outcome.value == expected
    # Only `resolved` hands over a file, and no refusal reads as a clean result.
    if expected == "resolved":
        assert Path(result.snapshot_path).read_text() == '{"library": "base"}'
        assert result.message.startswith("Changes in this PR (vs `main@bbbbbbb`)")
    else:
        assert result.snapshot_path == ""
        assert result.message.startswith("Delta unavailable")
        assert not (tmp_path / "dest").exists()


def test_a_symlinked_snapshot_is_refused(tmp_path: Path) -> None:
    stage = _entry(tmp_path, snapshot=None)
    outside = tmp_path / "secret.json"
    outside.write_text("{}")
    try:
        (stage / "snapshot.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    result = _verify(stage, tmp_path)
    assert result.outcome is DeltaOutcome.LOOKUP_FAILED
    assert "not a regular file" in result.reason


def test_a_missing_restore_directory_on_a_hit_is_not_absent(tmp_path: Path) -> None:
    result = _verify(tmp_path / "never-restored", tmp_path)
    assert result.outcome is DeltaOutcome.LOOKUP_FAILED


def test_resolved_copies_into_a_fresh_directory_keeping_destination(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    first = _verify(_entry(tmp_path), tmp_path)
    second = verify_cached_snapshot(
        tmp_path / "stage", _EXPECT, cache_hit=True, matched_key=_KEY, destination=dest
    )
    assert first.ok and second.ok
    assert first.snapshot_path != second.snapshot_path
    assert (dest / "keep.txt").read_text() == "mine"


def test_untrusted_reason_stays_out_of_the_comment(tmp_path: Path) -> None:
    hostile = "`@everyone` <img src=x>"
    result = _verify(_entry(tmp_path, provenance=_prov(repository=hostile)), tmp_path)
    assert result.outcome is DeltaOutcome.UNTRUSTED_SOURCE
    assert hostile in result.reason
    assert hostile not in result.message


# ── the CLI ─────────────────────────────────────────────────────────────────


def _outputs(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        fields[key] = value  # last write wins, as in GitHub
    return fields


def test_cli_cache_key_and_refusal() -> None:
    runner = CliRunner()
    ok = runner.invoke(action_cli, ["snapshot-cache-key", _BASE, "--profile", "p"])
    assert (
        ok.exit_code == 0 and ok.output.strip() == f"abicheck-main-snapshot-{_BASE}-p"
    )
    bad = runner.invoke(action_cli, ["snapshot-cache-key", "abc"])
    assert bad.exit_code == 2


@pytest.mark.parametrize(("hit", "code", "outcome"), [("true", 0, "resolved"), ("false", 3, "base_not_snapshotted"), ("", 3, "base_not_snapshotted")])  # fmt: skip
def test_cli_verify_cached_snapshot(
    tmp_path: Path, hit: str, code: int, outcome: str
) -> None:
    stage = _entry(tmp_path)
    out = tmp_path / "o"
    result = CliRunner().invoke(
        action_cli,
        [
            "verify-cached-snapshot", str(stage), "--repository", _REPO,
            "--branch", "main", "--base-sha", _BASE, "--key", _KEY,
            "--cache-hit", hit, "--matched-key", _KEY if hit == "true" else "",
            "--destination", str(tmp_path / "dest"), "--github-output", str(out),
        ],
    )  # fmt: skip
    assert result.exit_code == code, result.output
    fields = _outputs(out)
    assert fields["outcome"] == outcome
    assert bool(fields["snapshot-path"]) == (outcome == "resolved")


# ── the Actions' shells, end to end through the staging directory ──────────


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("INPUT_", "GITHUB_"))
    }
    env.update(
        {
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), env["PATH"]]),
            "GITHUB_OUTPUT": str(tmp_path / "o"),
            "GITHUB_WORKSPACE": str(tmp_path / "ws"),
            "GITHUB_REPOSITORY": _REPO,
            "RUNNER_TEMP": str(tmp_path / "tmp"),
        }
    )
    env.update(extra)
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    (tmp_path / "o").write_text("")
    return env


def _sh(script: Path, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    require_bash()
    return subprocess.run(
        [bash_executable(), str(script)],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _produce(tmp_path: Path, ref: str = "refs/heads/main", **extra: str):
    require_bash()
    snap = tmp_path / "s.json"
    snap.write_text('{"library": "base"}')
    fields = {
        "SNAPSHOT_SHA": _BASE,
        "INPUT_SNAPSHOT": str(snap),
        "INPUT_STORAGE": "cache",
        "DEFAULT_BRANCH": "main",
        "GITHUB_REF": ref,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ID": "42",
    }
    fields.update(extra)
    env = _env(tmp_path, **fields)
    return _sh(_SNAP_SH, env, tmp_path / "tmp")


@pytest.mark.parametrize(
    "ref", ["refs/pull/7/merge", "refs/heads/dev", "refs/tags/v1", ""]
)
def test_producer_saves_only_from_the_default_branch(tmp_path: Path, ref: str) -> None:
    result = _produce(tmp_path, ref=ref)
    assert result.returncode != 0
    assert "saves only from refs/heads/main" in result.stdout
    assert not (tmp_path / "ws" / CACHE_STAGING_DIR).exists()
    assert "cache-key" not in (tmp_path / "o").read_text()


def test_producer_needs_a_default_branch(tmp_path: Path) -> None:
    result = _produce(tmp_path, DEFAULT_BRANCH="")
    assert result.returncode != 0
    assert "needs 'branch'" in result.stdout


def test_producer_entry_is_accepted_by_the_consumer(tmp_path: Path) -> None:
    produced = _produce(tmp_path)
    assert produced.returncode == 0, produced.stdout + produced.stderr
    key = _outputs(tmp_path / "o")["cache-key"]
    assert key == _KEY
    stage = tmp_path / "ws" / CACHE_STAGING_DIR
    record = json.loads((stage / "provenance.json").read_text())
    assert record["run_id"] == "42" and record["ref"] == "refs/heads/main"

    env = _env(
        tmp_path,
        REPO=_REPO,
        INPUT_BRANCH="main",
        INPUT_DESTINATION=str(tmp_path / "dest"),
        BASE_SHA=_BASE,
        CACHE_KEY=key,
        CACHE_HIT="true",
        CACHE_MATCHED_KEY=key,
    )
    result = _sh(_VERIFY_SH, env, tmp_path / "ws")
    assert result.returncode == 0, result.stdout + result.stderr
    fields = _outputs(tmp_path / "o")
    assert fields["outcome"] == "resolved"
    assert Path(fields["snapshot-path"]).read_text() == '{"library": "base"}'
    assert not stage.exists()  # the staging copy never lingers


def test_consumer_entry_from_a_profile_key_is_not_this_profile(tmp_path: Path) -> None:
    produced = _produce(tmp_path, INPUT_PROFILE="x86")
    assert produced.returncode == 0, produced.stdout + produced.stderr
    env = _env(
        tmp_path,
        REPO=_REPO,
        INPUT_BRANCH="main",
        INPUT_DESTINATION=str(tmp_path / "dest"),
        BASE_SHA=_BASE,
        CACHE_KEY=_KEY,  # the no-profile lookup
        CACHE_HIT="true",
        CACHE_MATCHED_KEY=_KEY,
    )
    result = _sh(_VERIFY_SH, env, tmp_path / "ws")
    assert result.returncode == 0
    assert _outputs(tmp_path / "o")["outcome"] == "untrusted_source"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_fetch_cache_mode_selects_the_base_without_any_api_call(tmp_path: Path) -> None:
    """Cache mode makes no `gh` call (no `actions: read`), keys on the exact
    first parent, and leaves a fail-safe outcome until the entry is verified."""
    require_bash()
    repo = tmp_path / "ws"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, value in (
        ("user.email", "t@e"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", name, value)
    (repo / "a").write_text("a")
    _git(repo, "add", "a")
    _git(repo, "commit", "-q", "-m", "a")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "b").write_text("b")
    _git(repo, "add", "b")
    _git(repo, "commit", "-q", "-m", "b")
    _git(repo, "checkout", "-q", "--detach", "main")
    (repo / "c").write_text("c")
    _git(repo, "add", "c")
    _git(repo, "commit", "-q", "-m", "c")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "pr")
    assert tip != base
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text('#!/bin/sh\necho called >> "$0.log"; exit 1\n')
    (bin_dir / "gh").chmod(0o755)
    env = _env(
        tmp_path,
        INPUT_STORAGE="cache",
        INPUT_MODE="merge-commit",
        INPUT_BRANCH="main",
        INPUT_DESTINATION=str(tmp_path / "dest"),
        EVENT_NAME="pull_request",
        PR_BASE_REF="main",
    )
    env["PATH"] = os.pathsep.join([str(bin_dir), env["PATH"]])
    result = _sh(_FETCH_SH, env, repo)
    assert result.returncode == 0, result.stdout + result.stderr
    fields = _outputs(tmp_path / "o")
    assert fields["base-sha"] == tip
    assert fields["cache-key"] == snapshot_cache_key(tip)
    assert fields["outcome"] == "lookup_failed"  # until verify-cache.sh runs
    assert not (bin_dir / "gh.log").exists()


def test_fetch_cache_mode_refuses_an_occupied_staging_directory(tmp_path: Path) -> None:
    require_bash()
    env = _env(
        tmp_path,
        INPUT_STORAGE="cache",
        INPUT_MODE="merge-group",
        MERGE_GROUP_BASE=_BASE,
        EVENT_NAME="pull_request",
        PR_BASE_REF="main",
        INPUT_DESTINATION=str(tmp_path / "dest"),
    )
    (tmp_path / "ws" / CACHE_STAGING_DIR).mkdir()
    result = _sh(_FETCH_SH, env, tmp_path / "ws")
    assert result.returncode != 0
    assert "reserved for the restored cache entry" in result.stdout


@pytest.mark.parametrize("script", [_SNAP_SH, _FETCH_SH])
def test_unknown_storage_is_refused(tmp_path: Path, script: Path) -> None:
    require_bash()
    env = _env(tmp_path, INPUT_STORAGE="s3", SNAPSHOT_SHA=_BASE, INPUT_WORKFLOW="w.yml")
    result = _sh(script, env, tmp_path / "ws")
    assert result.returncode != 0
    assert "storage must be artifact or cache" in result.stdout


# ── the composite wiring ────────────────────────────────────────────────────


def _steps(action: str) -> list[dict]:
    doc = yaml.safe_load((_ACTIONS / action / "action.yml").read_text(encoding="utf-8"))
    return doc["runs"]["steps"]


def test_both_actions_use_the_one_cache_path_and_the_exact_key() -> None:
    save = [
        s for s in _steps("main-snapshot") if "actions/cache/save" in s.get("uses", "")
    ]
    restore = [
        s
        for s in _steps("fetch-main-snapshot")
        if "actions/cache/restore" in s.get("uses", "")
    ]
    assert len(save) == len(restore) == 1
    # The cache version hashes the literal path: one spelling on both sides.
    assert save[0]["with"]["path"] == restore[0]["with"]["path"] == CACHE_STAGING_DIR
    assert set(restore[0]["with"]) == {"path", "key"}  # never restore-keys
    assert save[0]["uses"].split("@")[1] == restore[0]["uses"].split("@")[1]


# ── which cache scopes a lookup may read ───────────────────────────────────

# (event, GITHUB_REF, base_ref) -> allowed. Oracle: GitHub's cache-scope rule
# -- a job reads its own ref, its base branch and the default branch -- and
# the requirement that every scope other than the job's own is `main`'s.
_SCOPES = [
    ("pull_request", "refs/pull/1/merge", "main", True),
    ("pull_request", "refs/pull/1/merge", "release/x", False),
    ("pull_request", "refs/pull/1/merge", "", False),
    ("pull_request_target", "refs/heads/main", "main", False),
    ("merge_group", "refs/heads/gh-readonly-queue/main/pr-1-abc", "", False),
    ("push", "refs/heads/main", "", True),
    ("push", "refs/heads/dev", "", False),
    ("push", "refs/tags/v1", "", False),
    ("workflow_dispatch", "refs/heads/main", "", True),
    ("workflow_dispatch", "refs/heads/feature", "", False),
    ("schedule", "refs/heads/main", "", True),
    ("workflow_run", "refs/heads/main", "", False),
    ("", "", "", False),
]


@pytest.mark.parametrize(("event", "ref", "base_ref", "allowed"), _SCOPES)
def test_cache_lookup_reads_only_scopes_the_branch_owns(
    event: str, ref: str, base_ref: str, allowed: bool
) -> None:
    refusal = cache_scope_refusal(
        event=event, ref=ref, base_ref=base_ref, branch="main"
    )
    assert (refusal is None) == allowed
    if refusal is not None:
        assert refusal.outcome is DeltaOutcome.UNTRUSTED_SOURCE
        assert refusal.message.startswith("Delta unavailable")


@pytest.mark.parametrize(
    ("event", "base_ref", "mode"),
    [("pull_request", "release/x", "merge-group"), ("merge_group", "", "merge-group")],
)
def test_fetch_refuses_a_foreign_scope_before_any_restore(
    tmp_path: Path, event: str, base_ref: str, mode: str
) -> None:
    require_bash()
    env = _env(
        tmp_path,
        INPUT_STORAGE="cache",
        INPUT_MODE=mode,
        INPUT_BRANCH="main",
        MERGE_GROUP_BASE=_BASE,
        EVENT_NAME=event,
        PR_BASE_REF=base_ref,
        INPUT_DESTINATION=str(tmp_path / "dest"),
    )
    result = _sh(_FETCH_SH, env, tmp_path / "ws")
    assert result.returncode == 0, result.stdout + result.stderr
    fields = _outputs(tmp_path / "o")
    assert fields["outcome"] == "untrusted_source"
    # No key -> the composite's restore and verify steps never run.
    assert "cache-key" not in fields


def test_verify_step_always_writes_an_outcome(tmp_path: Path) -> None:
    """The composite reads `cache-verify.outputs.outcome || fetch.outputs.outcome`;
    a crashed check must still overwrite the fetch step's value, never leave
    a stale one, and never claim `resolved`."""
    require_bash()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text("#!/bin/sh\necho boom >&2; exit 1\n")
    (bin_dir / "python").chmod(0o755)
    (tmp_path / "ws" / CACHE_STAGING_DIR).mkdir(parents=True)
    env = _env(
        tmp_path,
        REPO=_REPO,
        INPUT_BRANCH="main",
        INPUT_DESTINATION=str(tmp_path / "dest"),
        BASE_SHA=_BASE,
        CACHE_KEY=_KEY,
        CACHE_HIT="true",
        CACHE_MATCHED_KEY=_KEY,
    )
    env["PATH"] = os.pathsep.join([str(bin_dir), env["PATH"]])
    result = _sh(_VERIFY_SH, env, tmp_path / "ws")
    assert result.returncode == 0, result.stdout + result.stderr
    fields = _outputs(tmp_path / "o")
    assert fields["outcome"] == "lookup_failed"
    assert fields["message"].startswith("Delta unavailable")
    assert not (tmp_path / "ws" / CACHE_STAGING_DIR).exists()


def test_producer_records_the_runner_os(tmp_path: Path) -> None:
    produced = _produce(tmp_path, RUNNER_OS="Linux")
    assert produced.returncode == 0, produced.stdout + produced.stderr
    stage = tmp_path / "ws" / CACHE_STAGING_DIR
    assert json.loads((stage / "provenance.json").read_text())["runner_os"] == "Linux"


def test_operand_record_travels_through_the_cache(tmp_path: Path) -> None:
    """The producer stages the operand.json main-snapshot records, and the
    consumer copies it beside the snapshot, where the operand pairing reads it."""
    produced = _produce(tmp_path)
    assert produced.returncode == 0, produced.stdout + produced.stderr
    stage = tmp_path / "ws" / CACHE_STAGING_DIR
    assert sorted(p.name for p in stage.iterdir()) == [
        "operand.json", "provenance.json", "snapshot.json",
    ]  # fmt: skip
    staged = (stage / "operand.json").read_text()
    result = verify_cached_snapshot(
        stage, _EXPECT, cache_hit=True, matched_key=_KEY, destination=tmp_path / "dest"
    )
    assert result.ok, result.reason
    assert (Path(result.snapshot_path).parent / "operand.json").read_text() == staged


@pytest.mark.parametrize("with_operand", [True, False])
def test_operand_record_is_optional_but_must_be_a_regular_file(
    tmp_path: Path, with_operand: bool
) -> None:
    stage = _entry(tmp_path, extra={"operand.json": "{}"} if with_operand else None)
    result = _verify(stage, tmp_path)
    assert result.ok
    copied = Path(result.snapshot_path).parent / "operand.json"
    assert copied.exists() == with_operand
    if with_operand:
        (stage / "operand.json").unlink()
        (stage / "operand.json").mkdir()
        refused = _verify(stage, tmp_path)
        assert refused.outcome is DeltaOutcome.LOOKUP_FAILED
        assert "operand.json is not a regular file" in refused.reason

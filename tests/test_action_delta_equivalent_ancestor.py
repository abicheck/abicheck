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

"""``base-equivalent-paths``: the path-equivalent ancestor rule.

Plan: docs/contribute/plans/pr-delta-vs-main-snapshot.md (§ Optional:
path-equivalent ancestor). Invariants:

* a substitute snapshot is used only when the exact base has none, and only
  if it is the nearest first-parent ancestor with a snapshot such that no
  declared path changed between it and the base -- the oracle is the
  generated history itself (which commits touched an ABI path), not
  ``git diff`` or the implementation's planner;
* when no ancestor qualifies, the base's own outcome stands and the walk's
  reason is named; a non-equivalent snapshot is never used;
* the PR comment and the report name both commits, through the one
  ``delta_notes`` wording on the direct and the fork path.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from _action_run_sh_harness import REQUIRES_POSIX_SHELL
from _workflow_exec import bash_executable, require_bash
from click.testing import CliRunner
from test_action_delta_baseline import _run as _run_action
from test_action_delta_baseline_report import _records

from abicheck.frontends.action.cli import action_cli
from abicheck.frontends.action.main_snapshot import (
    DeltaOutcome,
    delta_notes,
)
from abicheck.frontends.action.main_snapshot_ancestor import (
    MAX_ANCESTORS,
    WALKABLE_OUTCOMES,
    AncestorFact,
    EquivalentPathsError,
    WalkResult,
    equivalent_pathspecs,
    plan_ancestor_walk,
)
from abicheck.frontends.action.main_snapshot_cache import (
    CACHE_PROVENANCE_SCHEMA,
    CacheExpectation,
    snapshot_cache_key,
    verify_cached_snapshot,
)
from abicheck.frontends.action.report_publication import context_notes
from abicheck.report.delta_baseline import DeltaBaseline

_ROOT = Path(__file__).resolve().parents[1]
_FETCH_SH = _ROOT / "actions" / "fetch-main-snapshot" / "run.sh"
_WF = "abi-main.yml"
_BASE = "a" * 40
_ANCESTOR = "e" * 40

needs_git = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)


# ── the declaration ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ()),
        ("\n  \n# only a comment\n", ()),
        ("include/**\nsrc/**", (":(glob)include/**", ":(glob)src/**")),
        ("  CMakeLists.txt  \nCMakeLists.txt", (":(glob)CMakeLists.txt",)),
        ("a..b/x.h", (":(glob)a..b/x.h",)),
    ],
)
def test_pathspecs_are_glob_magic_relative_paths(
    text: str, expected: tuple[str, ...]
) -> None:
    assert equivalent_pathspecs(text) == expected


@pytest.mark.parametrize(
    "bad",
    [
        ":(exclude)src",
        "!src/**",
        "/abs/include",
        "C:\\include",
        "../outside/**",
        "include/../../x",
        "inc\x01lude",
    ],
)
def test_a_declaration_that_excludes_or_escapes_is_refused(bad: str) -> None:
    with pytest.raises(EquivalentPathsError, match="line 2"):
        equivalent_pathspecs(f"include/**\n{bad}")


def test_the_cli_refuses_a_bad_declaration_with_exit_3() -> None:
    res = CliRunner().invoke(action_cli, ["equivalent-pathspecs", "--paths", "!x"])
    assert res.exit_code == 3
    ok = CliRunner().invoke(action_cli, ["equivalent-pathspecs", "--paths", "a/**"])
    assert ok.exit_code == 0 and ok.output == ":(glob)a/**\0"


def test_only_a_base_with_no_snapshot_is_walked() -> None:
    # Pending: wait for the exact one. Untrusted / lookup failed: report,
    # never step around. Unknown / ambiguous: no base to be equivalent to.
    assert {o.value for o in WALKABLE_OUTCOMES} == {
        "base_not_snapshotted",
        "snapshot_missing",
        "base_expired",
        "base_failed",
    }
    for outcome in DeltaOutcome:
        res = CliRunner().invoke(action_cli, ["ancestor-walkable", outcome.value])
        assert res.exit_code == (0 if outcome in WALKABLE_OUTCOMES else 3), outcome


# ── the walk plan, over an exhaustive small domain ──────────────────────────


@pytest.mark.parametrize("limit", [1, 2, 3, 4])
@pytest.mark.parametrize("complete", [True, False])
def test_plan_takes_the_equivalent_prefix_and_names_its_end(
    limit: int, complete: bool
) -> None:
    for length in range(5):
        for changed in itertools.product((False, True), repeat=length):
            facts = [
                AncestorFact(f"{i:040x}", ("include/x.h",) if c else ())
                for i, c in enumerate(changed)
            ]
            plan = plan_ancestor_walk(facts, limit=limit, history_complete=complete)
            # Oracle: ancestors are tried nearest first while none of the
            # considered (at most `limit`) has a declared-path change.
            seen = list(changed)[:limit]
            first_change = seen.index(True) if True in seen else None
            n = first_change if first_change is not None else len(seen)
            assert plan.candidates == tuple(f"{i:040x}" for i in range(n))
            if first_change is not None:
                assert plan.exhausted is WalkResult.PATHS_CHANGED
                assert plan.changed_at == f"{first_change:040x}"
            elif len(seen) == limit:
                assert plan.exhausted is WalkResult.LIMIT_REACHED
            elif complete:
                assert plan.exhausted is WalkResult.HISTORY_ENDED
            else:
                assert plan.exhausted is WalkResult.HISTORY_UNAVAILABLE


# ── real histories ──────────────────────────────────────────────────────────


class _Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        # A file:// remote serves any reachable commit by SHA, as GitHub does.
        self.git("config", "uploadpack.allowReachableSHA1InWant", "true")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, kind: str, n: int) -> str:
        """*kind* ``abi``: touches a declared path; ``doc``: only an undeclared one."""
        rel = Path("include" if kind == "abi" else "docs") / f"f{n}.txt"
        (self.path / rel).parent.mkdir(exist_ok=True)
        (self.path / rel).write_text(f"{kind}{n}", encoding="utf-8")
        self.git("add", str(rel))
        self.git("commit", "-q", "-m", f"{kind}{n}")
        return self.git("rev-parse", "HEAD")


def _main_history(tmp_path: Path, kinds: tuple[str, ...]) -> tuple[_Repo, list[str]]:
    """Root commit, then one commit per *kinds* entry; the last one is the base.

    Returns the first-parent chain, oldest first.
    """
    repo = _Repo(tmp_path / "repo")
    chain = [repo.commit("abi", 0)]
    for i, kind in enumerate(kinds, start=1):
        chain.append(repo.commit(kind, i))
    return repo, chain


def _oracle(
    kinds: tuple[str, ...], chain: list[str], snapshotted: set[str]
) -> str | None:
    """The definition: nearest ancestor with a snapshot such that no commit
    after it, up to and including the base, touched a declared path."""
    base = chain[-1]
    if base in snapshotted:
        return base
    touched = ("abi", *kinds)  # touched[i] is chain[i]'s kind
    for i in range(len(chain) - 2, -1, -1):
        if any(t == "abi" for t in touched[i + 1 :]):
            return None
        if chain[i] in snapshotted:
            return chain[i]
    return None


@needs_git
@pytest.mark.parametrize("kinds", list(itertools.product(("abi", "doc"), repeat=3)))
def test_planner_on_real_history_matches_the_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kinds: tuple[str, ...]
) -> None:
    repo, chain = _main_history(tmp_path, kinds)
    monkeypatch.chdir(repo.path)
    out = tmp_path / "plan.out"
    res = CliRunner().invoke(
        action_cli,
        [
            "plan-ancestor-walk",
            "--base-sha",
            chain[-1],
            "--paths",
            "include/**",
            "--github-output",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    lines = out.read_text().splitlines()
    candidates = [
        line.split("=", 1)[1] for line in lines if line.startswith("candidate=")
    ]
    # Every snapshot subset: the first candidate that has one is the oracle's
    # answer, and no candidate is past a declared-path change.
    for size in range(len(chain) + 1):
        for subset in itertools.combinations(chain[:-1], size):
            picked = next((c for c in candidates if c in subset), None)
            assert picked == _oracle(kinds, chain, set(subset)), (kinds, subset)
    exhausted = dict(
        line.split("=", 1) for line in lines if not line.startswith("candidate=")
    )
    if "abi" in kinds:
        assert exhausted["exhausted"] == "paths_changed"
    else:
        assert exhausted["exhausted"] == "history_ended"


def _gh_stub(bin_dir: Path, data: Path) -> None:
    """``gh api`` answering per SHA: ``data/<sha>.json`` holds that commit's
    artifact rows (absent = none); its runs are the producer's successful
    push run. ``caches`` answers with ``data/caches.txt``'s keys."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{data}/calls"\n'
        'name="" sha="" key=""\n'
        'for a in "$@"; do case "$a" in\n'
        "  name=abicheck-main-*) name=${a#name=abicheck-main-} ;;\n"
        "  head_sha=*) sha=${a#head_sha=} ;;\n"
        "  key=*) key=${a#key=} ;;\n"
        "esac; done\n"
        'for a in "$@"; do case "$a" in\n'
        f'  */actions/artifacts/*/zip) cat "{data}/snapshot.zip"; exit 0 ;;\n'
        f'  */actions/caches) grep -Fx -- "$key" "{data}/caches.txt" || true; exit 0 ;;\n'
        f'  */actions/artifacts) [[ -f "{data}/$name.json" ]] && cat "{data}/$name.json" || echo "[]"; exit 0 ;;\n'
        f'  */runs) [[ -f "{data}/$sha.json" ]] && cat "{data}/runs-$sha.json" || echo "[]"; exit 0 ;;\n'
        "esac; done\n"
        'echo "unexpected: $*" >&2; exit 1\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _publish(data: Path, sha: str, run_id: int) -> None:
    (data / f"{sha}.json").write_text(
        json.dumps(
            [
                {
                    "id": run_id * 10,
                    "name": f"abicheck-main-{sha}",
                    "expired": False,
                    "workflow_run": {"id": run_id},
                }
            ]
        )
    )
    (data / f"runs-{sha}.json").write_text(
        json.dumps(
            [
                {
                    "id": run_id,
                    "path": f".github/workflows/{_WF}",
                    "repository": {"full_name": "o/r"},
                    "head_repository": {"full_name": "o/r"},
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                    "head_sha": sha,
                }
            ]
        )
    )


def _fetch(
    tmp_path: Path,
    cwd: Path,
    *,
    snapshotted: list[str],
    paths: str,
    mode: str = "merge-group",
    base: str = "",
    storage: str = "artifact",
    cached: tuple[str, ...] | list[str] = (),
) -> tuple[dict[str, str], str]:
    require_bash()
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    for i, sha in enumerate(snapshotted, start=1):
        _publish(data, sha, 100 + i)
    (data / "caches.txt").write_text("".join(f"{k}\n" for k in cached))
    with zipfile.ZipFile(data / "snapshot.zip", "w") as zf:
        zf.writestr("snapshot.json", '{"library": "base"}')
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
            "GITHUB_WORKSPACE": str(tmp_path / "ws"),
            "REPO": "o/r",
            "EVENT_NAME": "pull_request",
            "PR_BASE_REF": "main",
            "MERGE_GROUP_BASE": base,
            "INPUT_WORKFLOW": _WF,
            "INPUT_STORAGE": storage,
            "INPUT_MODE": mode,
            "INPUT_BRANCH": "main",
            "INPUT_WAIT_TIMEOUT": "0",
            "INPUT_DESTINATION": str(tmp_path / "dest"),
            "INPUT_BASE_EQUIVALENT_PATHS": paths,
        }
    )
    (tmp_path / "ws").mkdir(exist_ok=True)
    result = subprocess.run(
        [bash_executable(), str(_FETCH_SH)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    fields: dict[str, str] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        fields[key] = value  # last write wins, as in GitHub
    calls = (data / "calls").read_text() if (data / "calls").exists() else ""
    return fields, calls


@needs_git
@pytest.mark.parametrize(
    ("kinds", "snap_at", "expected_at", "walk"),
    [
        # base (index 3) has a snapshot: exact, no walk
        (("doc", "doc", "doc"), [3, 1], 3, ""),
        # docs-only commits since a snapshotted ancestor: substitute
        (("abi", "doc", "doc"), [1], 1, "found"),
        # the nearest snapshotted equivalent ancestor wins over a farther one
        (("doc", "doc", "doc"), [0, 2], 2, "found"),
        # an ABI path changed after the only snapshot: refused
        (("doc", "abi", "doc"), [1], None, "paths_changed"),
        # a snapshot past the change is never reached, even an older one
        (("abi", "doc", "abi"), [0, 1], None, "paths_changed"),
        # nothing snapshotted back to the root
        (("doc", "doc", "doc"), [], None, "history_ended"),
    ],
)
def test_run_sh_uses_only_an_equivalent_ancestor(
    tmp_path: Path,
    kinds: tuple[str, ...],
    snap_at: list[int],
    expected_at: int | None,
    walk: str,
) -> None:
    repo, chain = _main_history(tmp_path, kinds)
    snapshotted = [chain[i] for i in snap_at]
    assert (chain[expected_at] if expected_at is not None else None) == _oracle(
        kinds, chain, set(snapshotted)
    )
    fields, _calls = _fetch(
        tmp_path, repo.path, snapshotted=snapshotted, paths="include/**", base=chain[-1]
    )
    assert fields["base-sha"] == chain[-1]
    assert fields.get("ancestor-walk", "") == walk
    if expected_at is None:
        assert fields["outcome"] == "base_not_snapshotted"
        assert fields.get("snapshot-path", "") == ""
        assert fields.get("snapshot-sha", "") == ""
        assert "No path-equivalent ancestor" in fields["message"]
        return
    assert fields["outcome"] == "resolved"
    assert fields["snapshot-sha"] == chain[expected_at]
    assert Path(fields["snapshot-path"]).read_text() == '{"library": "base"}'
    if walk == "found":
        assert (
            f"vs `main@{chain[expected_at][:7]}` (equivalent to `{chain[-1][:7]}`)"
            in fields["message"]
        )


@needs_git
def test_without_the_input_no_ancestor_is_ever_looked_up(tmp_path: Path) -> None:
    repo, chain = _main_history(tmp_path, ("doc", "doc"))
    fields, calls = _fetch(
        tmp_path, repo.path, snapshotted=[chain[1]], paths="", base=chain[-1]
    )
    assert fields["outcome"] == "base_not_snapshotted"
    assert "ancestor-walk" not in fields
    assert chain[1] not in calls


@needs_git
def test_an_untrusted_base_is_reported_not_walked_past(tmp_path: Path) -> None:
    repo, chain = _main_history(tmp_path, ("doc", "doc"))
    data = tmp_path / "data"
    data.mkdir()
    # An artifact named for the base, uploaded by a pull_request run.
    _publish(data, chain[-1], 9)
    runs = json.loads((data / f"runs-{chain[-1]}.json").read_text())
    runs[0]["event"] = "pull_request"
    (data / f"runs-{chain[-1]}.json").write_text(json.dumps(runs))
    fields, calls = _fetch(
        tmp_path, repo.path, snapshotted=[chain[1]], paths="include/**", base=chain[-1]
    )
    assert fields["outcome"] == "untrusted_source"
    assert "ancestor-walk" not in fields and chain[1] not in calls


@needs_git
def test_a_failed_lookup_during_the_walk_stops_it(tmp_path: Path) -> None:
    repo, chain = _main_history(tmp_path, ("doc", "doc", "doc"))
    data = tmp_path / "data"
    data.mkdir()
    # The nearest ancestor's artifact listing is unreadable: the farther,
    # snapshotted one is not reached, since the nearer one may have one.
    (data / f"{chain[2]}.json").write_text("not json")
    (data / f"runs-{chain[2]}.json").write_text("[]")
    fields, _calls = _fetch(
        tmp_path, repo.path, snapshotted=[chain[1]], paths="include/**", base=chain[-1]
    )
    assert fields["outcome"] == "base_not_snapshotted"
    assert fields["ancestor-walk"] == "lookup_failed"


@needs_git
@pytest.mark.parametrize("reachable", [True, False])
def test_a_shallow_checkout_is_deepened_or_reported(
    tmp_path: Path, reachable: bool
) -> None:
    """``fetch-depth: 2`` of a PR merge commit holds the base but none of its
    ancestors: the walk deepens along the base, or says it could not."""
    repo, chain = _main_history(tmp_path, ("abi", "doc", "doc"))
    repo.git("checkout", "-q", "-b", "pr", chain[0])
    (repo.path / "pr.txt").write_text("pr")
    repo.git("add", "pr.txt")
    repo.git("commit", "-q", "-m", "pr")
    repo.git("checkout", "-q", "-b", "merge", chain[-1])
    repo.git("merge", "-q", "--no-ff", "-m", "merge", "pr")
    clone = tmp_path / "clone"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "2",
            "-b",
            "merge",
            f"file://{repo.path}",
            str(clone),
        ],
        check=True,
        capture_output=True,
    )
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{chain[2]}^{{commit}}"],
            cwd=clone,
            capture_output=True,
        ).returncode
        != 0
    )  # the control: the ancestor is not in the clone
    if not reachable:
        subprocess.run(
            ["git", "remote", "set-url", "origin", str(tmp_path / "gone")],
            cwd=clone,
            check=True,
        )
    fields, _calls = _fetch(
        tmp_path, clone, snapshotted=[chain[1]], paths="include/**", mode="merge-commit"
    )
    assert fields["base-sha"] == chain[-1]
    if reachable:
        assert fields["outcome"] == "resolved"
        assert fields["snapshot-sha"] == chain[1]
        assert fields["ancestor-walk"] == "found"
    else:
        assert fields["outcome"] == "base_not_snapshotted"
        assert fields["ancestor-walk"] == "history_unavailable"
        assert "fetch-depth: 0" in fields["message"]


@needs_git
@pytest.mark.parametrize(
    ("cached_at", "walk", "key_at"),
    [
        ([3], "", 3),  # the base's own entry: no walk
        ([1], "found", 1),  # an equivalent ancestor's entry
        ([0], "paths_changed", None),  # only an entry from before an ABI change
    ],
)
def test_cache_storage_walks_exact_keys(
    tmp_path: Path, cached_at: list[int], walk: str, key_at: int | None
) -> None:
    repo, chain = _main_history(tmp_path, ("abi", "doc", "doc"))
    fields, calls = _fetch(
        tmp_path,
        repo.path,
        snapshotted=[],
        paths="include/**",
        base=chain[-1],
        storage="cache",
        cached=[snapshot_cache_key(chain[i]) for i in cached_at],
    )
    assert fields.get("ancestor-walk", "") == walk
    # Every listing is scoped to the default branch's own cache.
    assert all("ref=refs/heads/main" in c for c in calls.splitlines())
    if key_at is None:
        assert fields["outcome"] == "base_not_snapshotted"
        assert "cache-key" not in fields
        return
    assert fields["cache-key"] == snapshot_cache_key(chain[key_at])
    assert fields["cache-sha"] == chain[key_at]
    assert fields["base-sha"] == chain[-1]


def _cache_entry(directory: Path, sha: str) -> None:
    directory.mkdir(parents=True)
    (directory / "snapshot.json").write_text('{"library": "x"}')
    (directory / "provenance.json").write_text(
        json.dumps(
            {
                "schema": CACHE_PROVENANCE_SCHEMA,
                "repository": "o/r",
                "ref": "refs/heads/main",
                "sha": sha,
                "key": snapshot_cache_key(sha),
            }
        )
    )


@pytest.mark.parametrize("recorded", [_ANCESTOR, _BASE])
def test_an_ancestor_s_cache_entry_must_record_the_ancestor(
    tmp_path: Path, recorded: str
) -> None:
    """The entry is bound to the commit it stands for, not the base: an
    entry recording the base under the ancestor's key is refused."""
    _cache_entry(tmp_path / "stage", recorded)
    if recorded == _BASE:
        # Under the ancestor's key, but naming another commit.
        prov = json.loads((tmp_path / "stage" / "provenance.json").read_text())
        prov["key"] = snapshot_cache_key(_ANCESTOR)
        (tmp_path / "stage" / "provenance.json").write_text(json.dumps(prov))
    key = snapshot_cache_key(_ANCESTOR)
    result = verify_cached_snapshot(
        tmp_path / "stage",
        CacheExpectation(repository="o/r", branch="main", base_sha=_ANCESTOR, key=key),
        cache_hit=True,
        matched_key=key,
        destination=tmp_path / "dest",
        equivalent_to=_BASE,
    )
    if recorded == _ANCESTOR:
        assert result.outcome is DeltaOutcome.RESOLVED
        assert result.message == (
            "Changes in this PR vs `main@eeeeeee` (equivalent to `aaaaaaa`)."
        )
    else:
        assert result.outcome is DeltaOutcome.UNTRUSTED_SOURCE
        assert "path-equivalent ancestor of `aaaaaaa`" in result.message


# ── the comment and the report ──────────────────────────────────────────────


def test_the_heading_names_the_snapshot_and_the_base() -> None:
    first, note, gate = delta_notes(
        "resolved", base_sha=_BASE, snapshot_sha=_ANCESTOR, gate_verdict="BREAKING"
    )
    assert first == "Changes in this PR vs `main@eeeeeee` (equivalent to `aaaaaaa`)."
    assert "no path declared in base-equivalent-paths differs" in note
    assert gate.startswith("Release gate: BREAKING")


@pytest.mark.parametrize("snapshot", ["", _BASE])
def test_an_exact_snapshot_reads_as_before(snapshot: str) -> None:
    assert delta_notes("resolved", base_sha=_BASE, snapshot_sha=snapshot) == (
        delta_notes("resolved", base_sha=_BASE)
    )


@pytest.mark.parametrize(
    "outcome", ["base_built", "rerun_failed", "operand_unmatched", "snapshot_missing"]
)
def test_no_other_outcome_names_a_stand_in_snapshot(outcome: str) -> None:
    lines = delta_notes(outcome, base_sha=_BASE, snapshot_sha=_ANCESTOR)
    assert lines == delta_notes(outcome, base_sha=_BASE)
    assert not any("eeeeeee" in line for line in lines)


def test_a_malformed_snapshot_sha_is_refused() -> None:
    with pytest.raises(ValueError, match="snapshot sha"):
        delta_notes("resolved", base_sha=_BASE, snapshot_sha="e" * 7)


def test_the_fork_path_words_it_identically() -> None:
    direct = delta_notes(
        "resolved", base_sha=_BASE, snapshot_sha=_ANCESTOR, gate_verdict="COMPATIBLE"
    )
    context = {
        "analysis_context": {
            "delta_outcome": "resolved",
            "delta_base_sha": _BASE,
            "delta_snapshot_sha": _ANCESTOR,
            "gate_verdict": "COMPATIBLE",
            "pr_base_ref": "main",
        }
    }
    assert context_notes(context) == direct
    record = DeltaBaseline.from_mapping(
        {
            "comparison_role": "delta",
            "outcome": "resolved",
            "base_sha": _BASE,
            "snapshot_sha": _ANCESTOR,
            "branch": "main",
            "gate_verdict": "COMPATIBLE",
        }
    )
    assert record.equivalent_ancestor
    # A stored delta report with no analysis context renders the same notes.
    assert context_notes({"delta_baseline": record.to_report_dict()}) == direct


def test_the_snapshot_limit_is_a_bounded_constant() -> None:
    # The walk is bounded: a 51st ancestor is never considered.
    facts = [AncestorFact(f"{i:040x}") for i in range(MAX_ANCESTORS + 5)]
    plan = plan_ancestor_walk(facts, history_complete=True)
    assert len(plan.candidates) == MAX_ANCESTORS == 50
    assert plan.exhausted is WalkResult.LIMIT_REACHED


# ── through the main Action's run.sh ────────────────────────────────────────


@REQUIRES_POSIX_SHELL
@pytest.mark.parametrize("snapshot_sha", [_ANCESTOR, "a" * 40])
def test_the_main_action_comment_and_records_name_both_commits(
    tmp_path: Path, snapshot_sha: str
) -> None:
    snap = str(tmp_path / "base-snapshot.json")
    outputs, calls = _run_action(
        tmp_path,
        "direct",
        "resolved",
        snapshot=snap,
        extra={"ABICHECK_DELTA_SNAPSHOT_SHA": snapshot_sha},
    )
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    expected = delta_notes(
        "resolved",
        base_sha="a" * 40,
        snapshot_sha=snapshot_sha,
        gate_verdict=outputs["verdict"],
    )
    for line in expected:
        assert f"> {line}" in body
    equivalent = snapshot_sha != "a" * 40
    assert ("(equivalent to `aaaaaaa`)" in body) is equivalent
    (delta,) = [c for c in calls if snap in c]
    gate = next(c for c in calls if snap not in c)
    (delta_record,) = _records(delta)
    (gate_record,) = _records(gate)
    # Both reports say which snapshot stood in for the base, apart from it.
    for record in (delta_record, gate_record):
        assert record.base_sha == "a" * 40
        assert record.snapshot_sha == snapshot_sha
        assert record.equivalent_ancestor is equivalent


@REQUIRES_POSIX_SHELL
def test_an_unresolved_lookup_records_no_snapshot(tmp_path: Path) -> None:
    _outputs, calls = _run_action(
        tmp_path,
        "direct",
        "base_not_snapshotted",
        message="Delta unavailable: m.",
        extra={"ABICHECK_DELTA_SNAPSHOT_SHA": _ANCESTOR},
    )
    (gate_record,) = _records(calls[0])
    assert gate_record.outcome == "base_not_snapshotted"
    assert gate_record.snapshot_sha is None

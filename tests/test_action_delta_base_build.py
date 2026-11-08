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

"""``delta-on-missing-base: build``: the base commit is built in the PR job.

Plan: docs/contribute/plans/pr-delta-vs-main-snapshot.md (§ Outcomes). Three
layers, each against its own oracle:

- the pure argv rewrite (``delta_base_dump``): the dump of the base sees the
  gate's *new-side* evidence, every path moved into the base checkout, and
  refuses a path the base build cannot provide;
- ``action/build-delta-base.sh`` against a real git repository (shallow
  clone, a merge commit's first parent): ``built`` or a named failure, never
  a non-zero step;
- the whole of ``action/run.sh`` with a built (or failed) base: the comment
  is labelled with what happened and the step's status is the gate's.
"""

from __future__ import annotations

import json
import posixpath
import subprocess
from pathlib import Path

import pytest
import yaml
from _action_run_sh_harness import REQUIRES_POSIX_SHELL, _compare_env, _run_action
from _workflow_exec import bash_executable, require_bash
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.frontends.action.delta_base_dump import (
    BaseDumpRefused,
    base_dump_argv,
    map_into_base,
)
from abicheck.frontends.action.main_snapshot import (
    BUILDABLE_OUTCOMES,
    COMMENT_ONLY_OUTCOMES,
    DeltaOutcome,
    delta_notes,
)
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot

_ROOT = Path(__file__).resolve().parents[1]
_SHA = "b" * 40
_TOP = "/w/repo"
_BASE = "/t/base"


def _argv(tail: list[str], *, cwd: str = _TOP, lib: str = "build/libx.so") -> list[str]:
    return base_dump_argv(
        tail,
        new_library=lib,
        cwd=cwd,
        checkout=_TOP,
        base_root=_BASE,
        output="/t/out.json",
        value_options=frozenset({"--policy", "--suppress", "-o", "--label"}),
    )


# ── the pure rewrite ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cwd", "path", "expected"),
    [
        (_TOP, "include", f"{_BASE}/include"),
        (_TOP, "./include/../include/a.h", f"{_BASE}/include/a.h"),
        (_TOP, f"{_TOP}/build/libx.so", f"{_BASE}/build/libx.so"),
        (f"{_TOP}/sub", "../include", f"{_BASE}/include"),
        (f"{_TOP}/sub", "lib.so", f"{_BASE}/sub/lib.so"),
        (_TOP, _TOP, _BASE),
    ],
)
def test_paths_keep_their_place_relative_to_the_checkout(
    cwd: str, path: str, expected: str
) -> None:
    got = map_into_base(path, cwd=cwd, checkout=_TOP, base_root=_BASE)
    assert got == expected
    # Oracle independent of the implementation: the same position below
    # the two roots.
    assert posixpath.relpath(got, _BASE) == posixpath.relpath(
        posixpath.normpath(posixpath.join(cwd, path)), _TOP
    )


@pytest.mark.parametrize(
    "path", ["/opt/pr/include", "/w/repository/x", "../outside", "/w"]
)
def test_a_path_outside_the_checkout_is_refused(path: str) -> None:
    with pytest.raises(BaseDumpRefused, match="outside the checkout"):
        map_into_base(path, cwd=_TOP, checkout=_TOP, base_root=_BASE)


def test_an_outside_include_dir_is_kept_as_a_toolchain_dir() -> None:
    assert _argv(["-I", "/usr/include/foo"])[-4:-2] == ["-I", "/usr/include/foo"]


@pytest.mark.parametrize(
    "tail",
    [
        ["-H", "/opt/pr/include"],
        ["--header", "new=/opt/pr/include"],
        ["--debug-info", "new=/tmp/pr.debug"],
        ["--sources", "new=/src/elsewhere"],
    ],
)
def test_new_side_evidence_outside_the_checkout_refuses_the_dump(
    tail: list[str],
) -> None:
    """Reusing the PR's evidence for the base would hide the very changes
    the delta exists to show."""
    with pytest.raises(BaseDumpRefused):
        _argv(tail)


def test_a_library_outside_the_checkout_refuses_the_dump() -> None:
    with pytest.raises(BaseDumpRefused):
        _argv([], lib="/opt/install/libx.so")


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        (["-H", "include"], ["-H", f"{_BASE}/include"]),
        (["--header", "new=include"], ["-H", f"{_BASE}/include"]),
        (["--header=new=include"], ["-H", f"{_BASE}/include"]),
        (["-Hnew=include"], ["-H", f"{_BASE}/include"]),
        (["-vH", "new=include"], ["-H", f"{_BASE}/include"]),
        (["--include", "new=inc"], ["-I", f"{_BASE}/inc"]),
        (["--debug-info", "new=dbg"], ["--debug-info", f"{_BASE}/dbg"]),
        (["--sources", "new=."], ["--sources", _BASE]),
        (["--build-info", "new=build"], ["--build-info", f"{_BASE}/build"]),
        (["--depth", "source"], ["--depth", "source"]),
        (["--set", "compile.lang=c"], ["--set", "compile.lang=c"]),
        (["-DFOO=1"], ["-DFOO=1"]),
        (["-D", "FOO"], ["-D", "FOO"]),
        (["--no-follow-deps"], ["--no-follow-deps"]),
    ],
)
def test_new_side_evidence_reaches_the_base_dump(
    tail: list[str], expected: list[str]
) -> None:
    argv = _argv(tail)
    assert argv[:2] == ["dump", f"{_BASE}/build/libx.so"]
    assert argv[2:-2] == expected
    assert argv[-2:] == ["-o", "/t/out.json"]


@pytest.mark.parametrize(
    "tail",
    [
        ["--header", "old=release/include"],
        ["-Hold=release/include"],
        ["--debug-info", "old=/release/dbg"],
        ["--label", "old=1.0"],
        ["--policy", "strict.yml"],
        ["--set", "report.baseline_provenance=@/tmp/p.json"],
        ["--set=gate.fail_on_removed_library=true"],
        ["-o", "json=/tmp/r.json"],
        ["--suppress", "s.yml"],
    ],
)
def test_old_side_and_comparison_only_options_never_reach_the_dump(
    tail: list[str],
) -> None:
    """Old-side evidence describes the release; report/gate/policy options
    describe the comparison. A dump of the base carries neither."""
    assert _argv(tail) == ["dump", f"{_BASE}/build/libx.so", "-o", "/t/out.json"]


def test_a_value_option_s_value_is_not_read_as_a_flag() -> None:
    # `--label new=-H` must not turn `-H` into header evidence.
    assert _argv(["--label", "-H", "--header", "new=inc"])[2:-2] == [
        "-H",
        f"{_BASE}/inc",
    ]


# ── outcome vocabulary ──────────────────────────────────────────────────────


def test_only_outcomes_with_a_selected_commit_are_buildable() -> None:
    assert DeltaOutcome.BASE_UNKNOWN not in BUILDABLE_OUTCOMES
    assert DeltaOutcome.BASE_AMBIGUOUS not in BUILDABLE_OUTCOMES
    assert DeltaOutcome.RESOLVED not in BUILDABLE_OUTCOMES
    assert BUILDABLE_OUTCOMES == set(DeltaOutcome) - {
        DeltaOutcome.BASE_UNKNOWN,
        DeltaOutcome.BASE_AMBIGUOUS,
        DeltaOutcome.RESOLVED,
    }


def test_action_builds_on_exactly_the_buildable_outcomes() -> None:
    steps = {s.get("id"): s for s in _action()["runs"]["steps"]}
    cond = steps["delta-build"]["if"]
    listed = set(json.loads(cond.split("fromJSON('")[1].split("')")[0]))
    assert listed == {o.value for o in BUILDABLE_OUTCOMES}
    assert "delta-on-missing-base == 'build'" in cond
    assert "base-sha != ''" in cond


@pytest.mark.parametrize("outcome", ["base_build_failed", "base_dump_failed"])
def test_a_failed_build_reads_as_unavailable_never_as_a_delta(outcome: str) -> None:
    first, gate = delta_notes(outcome, base_sha=_SHA, gate_verdict="COMPATIBLE")
    assert first.startswith("Delta unavailable")
    assert "`main@bbbbbbb`" in first
    assert first.endswith("Showing the release-baseline comparison.")
    assert "status comes from the gate" in gate


def test_a_built_base_is_labelled_as_built_in_this_job() -> None:
    (first, _gate) = delta_notes("base_built", base_sha=_SHA, merge_group=True)
    assert first.startswith("Changes in this merge-queue entry (vs `main@bbbbbbb`")
    assert "built in this job" in first
    assert set(COMMENT_ONLY_OUTCOMES) >= {
        "base_built",
        "base_build_failed",
        "base_dump_failed",
    }


# ── action.yml wiring ───────────────────────────────────────────────────────


def _action() -> dict:
    return yaml.safe_load((_ROOT / "action.yml").read_text(encoding="utf-8"))


def test_the_base_build_runs_without_a_token_between_lookup_and_comparison() -> None:
    steps = _action()["runs"]["steps"]
    ids = [s.get("id") for s in steps]
    assert (
        ids.index("delta-base") < ids.index("delta-build") < ids.index("run-abicheck")
    )
    build = steps[ids.index("delta-build")]
    assert not any("token" in str(v).lower() for v in build["env"].values())
    assert "${{" not in build["run"].replace("${{ github.action_path }}", "")
    assert "continue-on-error" not in build


def test_build_mode_needs_its_command(tmp_path: Path) -> None:
    require_bash()
    proc = subprocess.run(
        [bash_executable(), str(_ROOT / "action" / "validate-inputs.sh")],
        env={
            "PATH": "/usr/bin:/bin",
            "INPUT_MODE": "compare",
            "INPUT_DELTA_BASELINE": "main",
            "INPUT_DELTA_SNAPSHOT_WORKFLOW": "abi-main.yml",
            "INPUT_DELTA_ON_MISSING_BASE": "build",
            "GITHUB_ACTION_PATH": str(_ROOT),
        },
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode != 0
    assert "needs delta-base-build-command" in proc.stdout + proc.stderr


# ── build-delta-base.sh against a real repository ───────────────────────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def pr_checkout(tmp_path: Path) -> tuple[Path, str]:
    """A depth-2 clone of a PR merge commit, as `fetch-depth: 2` gives."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "v.txt").write_text("base\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "base")
    base = _git(origin, "rev-parse", "HEAD")
    _git(origin, "checkout", "-qb", "pr")
    (origin / "v.txt").write_text("pr\n")
    _git(origin, "commit", "-qam", "pr")
    _git(origin, "checkout", "-q", "main")
    _git(origin, "merge", "-q", "--no-ff", "-m", "merge", "pr")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--depth", "2", f"file://{origin}", str(clone))
    return clone, base


def _build(clone: Path, sha: str, command: str, tmp_path: Path) -> dict[str, str]:
    require_bash()
    out = tmp_path / "out"
    out.write_text("")
    proc = subprocess.run(
        [bash_executable(), str(_ROOT / "action" / "build-delta-base.sh")],
        env={
            "PATH": "/usr/bin:/bin",
            "BASE_SHA": sha,
            "ABICHECK_BASE_BUILD_COMMAND": command,
            "DEST": str(tmp_path / "base-src"),
            "GITHUB_OUTPUT": str(out),
        },
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


@REQUIRES_POSIX_SHELL
def test_the_base_commit_is_built_in_its_own_worktree(
    pr_checkout: tuple[Path, str], tmp_path: Path
) -> None:
    clone, base = pr_checkout
    first_parent = _git(clone, "rev-parse", "HEAD^1")
    assert first_parent == base
    got = _build(
        clone, base, 'cp v.txt built.txt; echo "$ABICHECK_BASE_SHA" > sha', tmp_path
    )
    assert got == {"outcome": "built", "root": str(tmp_path / "base-src")}
    root = Path(got["root"])
    assert (root / "built.txt").read_text() == "base\n"
    assert (root / "sha").read_text().strip() == base
    # The PR checkout itself is untouched.
    assert (clone / "v.txt").read_text() == "pr\n"
    assert not (clone / "built.txt").exists()


@REQUIRES_POSIX_SHELL
@pytest.mark.parametrize(
    ("command", "sha", "stage"),
    [
        ("false", None, "build"),
        ("false || true", None, None),  # a handled failure is not a failed build
        (
            "echo a | false; true",
            None,
            "build",
        ),  # pipefail: a failing pipe stage fails it
        ("exit 3", None, "build"),
        ("true", "c" * 40, "checkout"),
        ("true", "not-a-sha", "checkout"),
        ("  ", None, "build"),
    ],
)
def test_every_failure_is_a_named_outcome_and_a_successful_step(
    pr_checkout: tuple[Path, str],
    tmp_path: Path,
    command: str,
    sha: str | None,
    stage: str | None,
) -> None:
    clone, base = pr_checkout
    got = _build(clone, sha or base, command, tmp_path)
    if stage is None:
        assert got["outcome"] == "built"
    else:
        assert got == {"outcome": "build_failed", "stage": stage}


# ── the whole of run.sh with a built base ───────────────────────────────────


def _report(
    tmp_path: Path, tag: str, symbol: str, *, removed: bool
) -> tuple[int, bytes]:
    keep = Function(name="keep", mangled="keep", return_type="void")
    sym = Function(name=symbol, mangled=symbol, return_type="void")
    before, after = ([keep, sym], [keep]) if removed else ([keep], [keep, sym])
    old, new = tmp_path / f"{tag}-o.json", tmp_path / f"{tag}-n.json"
    save_snapshot(AbiSnapshot(library="libx.so.1", version="1", functions=before), old)
    save_snapshot(AbiSnapshot(library="libx.so.1", version="2", functions=after), new)
    out = tmp_path / f"{tag}.json"
    res = CliRunner().invoke(main, ["compare", str(old), str(new), "-o", f"json={out}"])
    assert out.exists(), res.output
    return res.exit_code, out.read_bytes()


def _stub(tmp_path: Path, *, dump_ok: bool) -> tuple[Path, Path]:
    """abicheck: `dump` writes a snapshot (or fails); `compare` answers the
    gate with ``release_only`` removed, and a compare whose old operand is
    the dumped base with ``pr_only`` added."""
    gate_code, gate = _report(tmp_path, "gate", "release_only", removed=True)
    _, delta = _report(tmp_path, "delta", "pr_only", removed=False)
    (tmp_path / "gate.bin").write_bytes(gate)
    (tmp_path / "delta.bin").write_bytes(delta)
    log = tmp_path / "argv.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\x1f" "$@" >> "{log}"; echo >> "{log}"\n'
        'prev=""\n'
        'if [[ "$1" == dump ]]; then\n'
        f"  {'' if dump_ok else 'exit 1'}\n"
        '  for a in "$@"; do [[ "$prev" == -o ]] && echo "{}" > "$a"; prev="$a"; done\n'
        "  exit 0\n"
        "fi\n"
        f'src="{tmp_path}/gate.bin"; code={gate_code}\n'
        'for a in "$@"; do [[ "$a" == */base-snapshot.json ]] && { src="'
        f'{tmp_path}/delta.bin"; code=0; }}; done\n'
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
        f'[[ " $* " == *" --input - "* ]] && cat >> "{tmp_path}/gh_bodies"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return bindir, log


def _run_sh(
    tmp_path: Path,
    build_outcome: str,
    *,
    dump_ok: bool = True,
    extra: dict | None = None,
) -> tuple[dict, list[list[str]], str]:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    bindir, log = _stub(tmp_path, dump_ok=dump_ok)
    base_root = tmp_path / "base-src"
    base_root.mkdir()
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": 7, "head": {"sha": "a" * 40}}})
    )
    env = {
        **_compare_env(tmp_path),
        "INPUT_OLD_HEADER": "release/include",
        "INPUT_NEW_HEADER": "include",
        "INPUT_PR_COMMENT": "true",
        "INPUT_PR_COMMENT_ON": "always",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "o/r",
        "ABICHECK_PR_COMMENT_DELIVERY": "direct",
        "ABICHECK_DELTA_OUTCOME": "snapshot_missing",
        "ABICHECK_DELTA_SNAPSHOT": "",
        "ABICHECK_DELTA_BASE_SHA": _SHA,
        "ABICHECK_DELTA_BRANCH": "main",
        "ABICHECK_DELTA_BUILD_OUTCOME": build_outcome,
        "ABICHECK_DELTA_BUILD_ROOT": str(base_root) if build_outcome == "built" else "",
        **(extra or {}),
    }
    outputs = _run_action(tmp_path, env, bindir)
    calls = [line.split("\x1f")[:-1] for line in log.read_text().splitlines() if line]
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    return outputs, calls, body


@REQUIRES_POSIX_SHELL
def test_a_built_base_is_dumped_with_new_side_evidence_and_becomes_the_delta(
    tmp_path: Path,
) -> None:
    outputs, calls, body = _run_sh(tmp_path, "built")
    base_root = str(tmp_path / "base-src")
    (dump,) = [c for c in calls if c[0] == "dump"]
    assert dump[1] == f"{base_root}/libnew.so"
    assert dump[dump.index("-H") + 1] == f"{base_root}/include"
    assert not any("release" in a for a in dump)
    snap = dump[dump.index("-o") + 1]
    (delta,) = [c for c in calls if snap in c and c[0] == "compare"]
    assert delta[delta.index(snap) + 1] == _compare_env(tmp_path)["INPUT_NEW_LIBRARY"]
    assert "pr_only" in body and "release_only" not in body
    assert f"> {delta_notes('base_built', base_sha=_SHA)[0]}" in body
    assert outputs["delta-comment-outcome"] == "base_built"


@REQUIRES_POSIX_SHELL
@pytest.mark.parametrize(
    ("build_outcome", "dump_ok", "extra", "label"),
    [
        ("build_failed", True, None, "base_build_failed"),
        ("built", False, None, "base_dump_failed"),
        # New-side evidence the base build cannot provide: refused, not reused.
        (
            "built",
            True,
            {"INPUT_NEW_HEADER": "/opt/elsewhere/include"},
            "base_dump_failed",
        ),
    ],
)
def test_a_failed_base_build_is_explicit_and_the_status_stays_the_gate_s(
    tmp_path: Path, build_outcome: str, dump_ok: bool, extra: dict | None, label: str
) -> None:
    (tmp_path / "gate").mkdir()
    (tmp_path / "build").mkdir()
    gate_only, _, _ = _run_sh(tmp_path / "gate", "", extra=extra)
    outputs, calls, body = _run_sh(
        tmp_path / "build", build_outcome, dump_ok=dump_ok, extra=extra
    )
    assert gate_only["_exit"] != 0  # the gate is a break: the control
    assert outputs["_exit"] == gate_only["_exit"]
    assert outputs.get("verdict") == gate_only.get("verdict")
    assert outputs["delta-comment-outcome"] == label
    # The release comparison is shown, and said to be shown.
    assert "release_only" in body and "pr_only" not in body
    first = delta_notes(label, base_sha=_SHA)[0]
    assert first.startswith("Delta unavailable") and f"> {first}" in body
    assert not any(
        c[0] == "compare" and any(a.endswith("base-snapshot.json") for a in c)
        for c in calls
    )

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
from abicheck.elf_metadata import ElfMetadata
from abicheck.frontends.action.cli import action_cli
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


#: Options both `compare` and `dump` accept that describe the comparison or
#: the run, not how one side is extracted -- stated here, not read from the
#: module under test.
_COMPARISON_ONLY = {
    "--label",  # names a side of the comparison
    "--variant",  # selects a variant of a stored ProjectSnapshot package
    "--bundle-facts-out",  # an output of the comparison
    "--output",
    "-o",
    "--dry-run",
    "--verbose",
    "-v",
    "--help",
    "--help-all",
}


def _cli_options(command: str) -> set[str]:
    import click

    cmd = main.commands[command]
    return {
        o
        for p in cmd.params
        if isinstance(p, click.Option)
        for o in (*p.opts, *p.secondary_opts)
    }


@pytest.mark.parametrize(
    "option", sorted(_cli_options("compare") & _cli_options("dump") - _COMPARISON_ONLY)
)
def test_every_shared_extraction_option_is_carried_or_refused(option: str) -> None:
    """An extraction option the rewrite silently dropped would capture the
    base at a different depth than the new side (CodeRabbit/peer review).
    Each shared option either reaches the dump or refuses it."""
    value = (
        []
        if option
        in {"--follow-deps", "--no-follow-deps", "--include-system-declarations"}
        else [
            "new=x"
            if option
            in {
                "-H",
                "--header",
                "-I",
                "--include",
                "--debug-info",
                "--sources",
                "--build-info",
            }
            else "compile.lang=c"
            if option == "--set"
            else "v"
        ]
    )
    try:
        argv = _argv([option, *value])
    except BaseDumpRefused:
        return
    carried = argv[2:-2]
    assert carried, f"{option} was dropped from the base dump"


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


@pytest.mark.parametrize("outcome", [o.value for o in DeltaOutcome] + ["", "x"])
def test_the_shell_s_buildable_check_is_the_registry_s(outcome: str) -> None:
    res = CliRunner().invoke(action_cli, ["delta-buildable", outcome])
    assert (res.exit_code == 0) == (outcome in {o.value for o in BUILDABLE_OUTCOMES})


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


def test_the_base_build_is_no_step_of_its_own_before_the_gate() -> None:
    """The build runs inside run.sh after the gate comparison (see the
    run.sh tests below); a step placed before it could rewrite what the
    gate reads or the environment it runs in."""
    steps = _action()["runs"]["steps"]
    assert not any("build-delta-base" in str(s.get("run", "")) for s in steps)
    (run,) = [s for s in steps if s.get("id") == "run-abicheck"]
    assert (
        run["env"]["ABICHECK_DELTA_ON_MISSING_BASE"]
        == "${{ inputs.delta-on-missing-base }}"
    )
    assert (
        run["env"]["ABICHECK_DELTA_BASE_BUILD_COMMAND"]
        == "${{ inputs.delta-base-build-command }}"
    )


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


def _build(
    clone: Path, sha: str, command: str, tmp_path: Path, env: dict | None = None
) -> dict[str, str]:
    require_bash()
    result = tmp_path / "result"
    result.write_text("")
    proc = subprocess.run(
        [bash_executable(), str(_ROOT / "action" / "build-delta-base.sh")],
        env={
            "PATH": "/usr/bin:/bin",
            "BASE_SHA": sha,
            "ABICHECK_BASE_BUILD_COMMAND": command,
            "DEST": str(tmp_path / "base-src"),
            "RESULT_FILE": str(result),
            "REPO_DIR": str(clone),
            "RUNNER_TEMP": str(tmp_path),
            **(env or {}),
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dict(line.split("=", 1) for line in result.read_text().splitlines())


@REQUIRES_POSIX_SHELL
def test_the_build_command_is_sandboxed_from_the_rest_of_the_job(
    pr_checkout: tuple[Path, str], tmp_path: Path
) -> None:
    """No token or Action input in its environment; its runner-file writes
    go nowhere; it cannot forge the script's own result."""
    clone, base = pr_checkout
    real = {n: tmp_path / n for n in ("env", "path", "output", "summary")}
    for f in real.values():
        f.write_text("")
    got = _build(
        clone,
        base,
        "env > env.txt; git config --get http.https://github.com/.extraheader > hdr.txt || true\n"
        'echo LEAK=1 >> "$GITHUB_ENV"; echo /evil >> "$GITHUB_PATH"\n'
        'echo outcome=built >> "$GITHUB_OUTPUT"; echo x >> "$GITHUB_STEP_SUMMARY"\n'
        'echo outcome=built >> "${RESULT_FILE:-/dev/null}"; exit 1',
        tmp_path,
        env={
            "GH_TOKEN": "secret-gh",
            "GITHUB_TOKEN": "secret-github",
            "INPUT_GITHUB_TOKEN": "secret-input",
            "ABICHECK_DELTA_BASE_BUILD_COMMAND": "secret-cmd",
            "GITHUB_ENV": str(real["env"]),
            "GITHUB_PATH": str(real["path"]),
            "GITHUB_OUTPUT": str(real["output"]),
            "GITHUB_STEP_SUMMARY": str(real["summary"]),
        },
    )
    # The command failed; nothing it wrote makes that read as `built`.
    assert got == {"outcome": "build_failed", "stage": "build"}
    assert all(f.read_text() == "" for f in real.values())
    seen = (tmp_path / "base-src" / "env.txt").read_text()
    assert "secret" not in seen
    assert "\nINPUT_" not in "\n" + seen
    assert (tmp_path / "base-src" / "hdr.txt").read_text().strip() == ""


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


def _stub(
    tmp_path: Path, *, dump_ok: bool, base_soname: str = "libnew.so.1"
) -> tuple[Path, Path]:
    """abicheck: `dump` writes a snapshot (or fails); `compare` answers the
    gate with ``release_only`` removed, and a compare whose old operand is
    the dumped base with ``pr_only`` added."""
    gate_code, gate = _report(tmp_path, "gate", "release_only", removed=True)
    _, delta = _report(tmp_path, "delta", "pr_only", removed=False)
    (tmp_path / "gate.bin").write_bytes(gate)
    (tmp_path / "delta.bin").write_bytes(delta)
    # What the base dump writes: a real snapshot, so operand identity (which
    # reads both sides) sees the same library as new-library -- or not.
    base_snap = tmp_path / "base-dump.json"
    save_snapshot(
        AbiSnapshot(
            library="libnew.so", version="1", elf=ElfMetadata(soname=base_soname)
        ),
        base_snap,
    )
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
        f'  for a in "$@"; do [[ "$prev" == -o ]] && cp "{base_snap}" "$a"; prev="$a"; done\n'
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
    command: str = "touch libnew.so",
    *,
    dump_ok: bool = True,
    on_missing: str = "build",
    extra: dict | None = None,
    base_soname: str = "libnew.so.1",
) -> tuple[dict, list[list[str]], str]:
    """run.sh in a real repository whose first commit is the base."""
    tmp_path.mkdir(exist_ok=True)
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "x.h").write_text("int keep(void);\n")
    _git(tmp_path, "add", "include")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    bindir, log = _stub(tmp_path, dump_ok=dump_ok, base_soname=base_soname)
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": 7, "head": {"sha": "a" * 40}}})
    )
    base_env = _compare_env(tmp_path)
    # The PR's new-library as a readable snapshot of libnew.so (operand
    # identity reads it), the way test_action_delta_baseline does.
    save_snapshot(
        AbiSnapshot(
            library="libnew.so", version="2", elf=ElfMetadata(soname="libnew.so.1")
        ),
        Path(base_env["INPUT_NEW_LIBRARY"]),
    )
    env = {
        **base_env,
        "INPUT_OLD_HEADER": "release/include",
        "INPUT_NEW_HEADER": "include",
        "INPUT_PR_COMMENT": "true",
        "INPUT_PR_COMMENT_ON": "always",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "o/r",
        "GH_TOKEN": "secret-gh",
        "ABICHECK_PR_COMMENT_DELIVERY": "direct",
        "ABICHECK_DELTA_OUTCOME": "snapshot_missing",
        "ABICHECK_DELTA_SNAPSHOT": "",
        "ABICHECK_DELTA_BASE_SHA": base,
        "ABICHECK_DELTA_BRANCH": "main",
        "ABICHECK_DELTA_ON_MISSING_BASE": on_missing,
        "ABICHECK_DELTA_BASE_BUILD_COMMAND": command.replace("@LOG@", str(log)),
        **(extra or {}),
    }
    outputs = _run_action(tmp_path, env, bindir)
    outputs["_base"] = base
    calls = [line.split("\x1f")[:-1] for line in log.read_text().splitlines() if line]
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    return outputs, calls, body


def _base_root(tmp_path: Path) -> str:
    return str((tmp_path / "runner_temp" / "abicheck-delta-base-src").resolve())


@REQUIRES_POSIX_SHELL
def test_a_built_base_is_dumped_with_new_side_evidence_and_becomes_the_delta(
    tmp_path: Path,
) -> None:
    outputs, calls, body = _run_sh(tmp_path)
    root = _base_root(tmp_path)
    (dump,) = [c for c in calls if c[0] == "dump"]
    assert dump[1] == f"{root}/libnew.so"
    assert dump[dump.index("-H") + 1] == f"{root}/include"
    assert not any("release" in a for a in dump)
    snap = dump[dump.index("-o") + 1]
    (delta,) = [c for c in calls if snap in c and c[0] == "compare"]
    assert delta[delta.index(snap) + 1] == _compare_env(tmp_path)["INPUT_NEW_LIBRARY"]
    assert "pr_only" in body and "release_only" not in body
    base = outputs["_base"]
    assert f"> {delta_notes('base_built', base_sha=base)[0]}" in body
    assert outputs["delta-comment-outcome"] == "base_built"
    # The worktree is gone once the delta has been measured.
    assert not Path(root).exists()
    assert root not in _git(tmp_path, "worktree", "list")


@REQUIRES_POSIX_SHELL
def test_the_base_is_built_only_after_the_gate_compared(tmp_path: Path) -> None:
    _outputs, calls, _body = _run_sh(
        tmp_path, 'touch libnew.so; printf "BUILD\\x1f\\n" >> "@LOG@"'
    )
    order = [c[0] for c in calls]
    gate = order.index("compare")
    assert gate < order.index("BUILD") < order.index("dump")


@REQUIRES_POSIX_SHELL
@pytest.mark.parametrize(
    ("command", "dump_ok", "extra", "label"),
    [
        ("exit 1", True, None, "base_build_failed"),
        ("touch libnew.so", False, None, "base_dump_failed"),
        # New-side evidence the base build cannot provide: refused, not reused.
        (
            "touch libnew.so",
            True,
            {"INPUT_NEW_HEADER": "/opt/elsewhere/include"},
            "base_dump_failed",
        ),
        # The build overwrote the PR's own library (an absolute install):
        # the delta would compare the base with itself.
        ("touch libnew.so; echo tampered >> @LIB@", True, None, "base_build_failed"),
    ],
)
def test_a_failed_base_build_is_explicit_and_the_status_stays_the_gate_s(
    tmp_path: Path, command: str, dump_ok: bool, extra: dict | None, label: str
) -> None:
    gate_only, _, _ = _run_sh(tmp_path / "gate", on_missing="report", extra=extra)
    lib = str(tmp_path / "build" / "libnew.so")  # _compare_env's new-library
    outputs, calls, body = _run_sh(
        tmp_path / "build",
        command.replace("@LIB@", lib),
        dump_ok=dump_ok,
        extra=extra,
    )
    assert gate_only["_exit"] != 0  # the gate is a break: the control
    assert outputs["_exit"] == gate_only["_exit"]
    assert outputs.get("verdict") == gate_only.get("verdict")
    assert outputs["delta-comment-outcome"] == label
    # The release comparison is shown, and said to be shown.
    assert "release_only" in body and "pr_only" not in body
    first = delta_notes(label, base_sha=outputs["_base"])[0]
    assert first.startswith("Delta unavailable") and f"> {first}" in body
    assert not any(
        c[0] == "compare" and any(a.endswith("base-snapshot.json") for a in c)
        for c in calls
    )


@REQUIRES_POSIX_SHELL
def test_no_build_when_the_outcome_selected_no_commit(tmp_path: Path) -> None:
    _outputs, calls, body = _run_sh(
        tmp_path,
        'printf "BUILD\\x1f\\n" >> "@LOG@"',
        extra={"ABICHECK_DELTA_OUTCOME": "base_ambiguous"},
    )
    assert [c[0] for c in calls] == ["compare"]
    assert "several merge-bases" in body


# ── the delta-base-dump-args command (run.sh's interface to the rewrite) ────


def test_dump_args_command_prints_the_argv_nul_separated(tmp_path: Path) -> None:
    tail = tmp_path / "tail"
    tail.write_bytes(b"--header\0new=include\0--policy\0p.yml\0\0")
    res = CliRunner().invoke(
        action_cli,
        [
            "delta-base-dump-args",
            str(tail),
            "--new-library",
            "build/libx.so",
            "--cwd",
            _TOP,
            "--checkout",
            _TOP,
            "--base-root",
            _BASE,
            "--output",
            "/t/out.json",
            "--value-options",
            "|--policy|-o|",
        ],
    )
    assert res.exit_code == 0, res.output
    assert res.output.split("\0")[:-1] == [
        "dump",
        f"{_BASE}/build/libx.so",
        "-H",
        f"{_BASE}/include",
        "-o",
        "/t/out.json",
    ]


@pytest.mark.parametrize(
    "tail", [b"-H\0/elsewhere/include\0", b"--dump-manifest\0new=m.yml\0", b"-H\0"]
)
def test_dump_args_command_refuses_with_exit_3(tmp_path: Path, tail: bytes) -> None:
    path = tmp_path / "tail"
    path.write_bytes(tail)
    res = CliRunner().invoke(
        action_cli,
        [
            "delta-base-dump-args",
            str(path),
            "--new-library",
            "libx.so",
            "--cwd",
            _TOP,
            "--checkout",
            _TOP,
            "--base-root",
            _BASE,
            "--output",
            "/t/o.json",
        ],
    )
    assert res.exit_code == 3
    assert "\0" not in res.stdout


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        (["--depth"], ["--depth", ""]),  # a trailing value option without its value
        (["--set"], []),
        (["--depth=binary"], ["--depth", "binary"]),
        (["--unknown-flag"], []),
    ],
)
def test_rewrite_edges(tail: list[str], expected: list[str]) -> None:
    assert _argv(tail)[2:-2] == expected


def test_empty_path_is_refused() -> None:
    with pytest.raises(BaseDumpRefused, match="empty"):
        map_into_base("", cwd=_TOP, checkout=_TOP, base_root=_BASE)


@REQUIRES_POSIX_SHELL
def test_a_built_base_of_another_library_is_never_compared(tmp_path: Path) -> None:
    """The built base goes through the same operand-identity check as a
    fetched snapshot: a base whose SONAME names a different library (the
    build produced something else at new-library's path) is refused, not
    compared as if every symbol were removed (ADR-065)."""
    outputs, calls, body = _run_sh(tmp_path, base_soname="libother.so.3")
    assert outputs["delta-comment-outcome"].startswith("operand_")
    assert "release_only" in body and "pr_only" not in body
    assert not any(
        c[0] == "compare" and any(a.endswith("base-snapshot.json") for a in c)
        for c in calls
    )
    # The dump wrote the same operand record a stored snapshot carries.
    (dump,) = [c for c in calls if c[0] == "dump"]
    record = Path(dump[dump.index("-o") + 1]).parent / "operand.json"
    assert json.loads(record.read_text())["path"].endswith("libnew.so")


@pytest.mark.parametrize("outcome", ["resolved", "base_built"])
def test_a_measured_delta_states_a_non_trivial_operand_pairing(outcome: str) -> None:
    lines = delta_notes(outcome, base_sha=_SHA, operand_match="declared")
    assert "paired with this PR's by `delta-operand-map`" in lines[1]
    assert len(lines) == 3


def test_an_unavailable_delta_never_states_a_pairing() -> None:
    assert (
        len(delta_notes("base_build_failed", base_sha=_SHA, operand_match="declared"))
        == 2
    )


# ── storage: cache (fetch-main-snapshot) refusals are buildable ─────────────


def _cache_refusals(tmp_path: Path) -> dict[str, DeltaOutcome]:
    """Every non-resolved answer the real cache verifier gives, by case."""
    from abicheck.frontends.action.main_snapshot_cache import (
        CacheExpectation,
        cache_scope_refusal,
        provenance_record,
        snapshot_cache_key,
        verify_cached_snapshot,
    )

    key = snapshot_cache_key(_SHA)
    expected = CacheExpectation(repository="o/r", branch="main", base_sha=_SHA, key=key)

    def entry(name: str, provenance: dict | None, snapshot: str = "{}") -> Path:
        d = tmp_path / name
        d.mkdir()
        (d / "snapshot.json").write_text(snapshot)
        if provenance is not None:
            (d / "provenance.json").write_text(json.dumps(provenance))
        return d

    good = provenance_record(sha=_SHA, key=key, repository="o/r", ref="refs/heads/main")
    forged = {**good, "repository": "evil/fork"}

    def verify(d: Path, *, hit: bool = True, matched: str = key) -> DeltaOutcome:
        return verify_cached_snapshot(
            d,
            expected,
            cache_hit=hit,
            matched_key=matched,
            destination=tmp_path / "dest",
        ).outcome

    scope = cache_scope_refusal(
        event="merge_group",
        ref="refs/heads/gh-readonly-queue/main/x",
        base_ref="",
        branch="main",
    )
    assert scope is not None
    return {
        "miss": verify(tmp_path / "absent", hit=False),
        "other key": verify(entry("k", good), matched=key + "-x"),
        "forged provenance": verify(entry("p", forged)),
        "malformed entry": verify(entry("m", None)),
        "unreadable snapshot": verify(entry("u", good, snapshot="[")),
        "scope": scope.outcome,
    }


def test_every_cache_refusal_selected_a_commit_and_is_buildable(tmp_path: Path) -> None:
    """A cache refusal is about the cache entry, never about which commit is
    the base -- git chose that before the lookup -- so `build` applies to
    every one of them, and building it does not read the refused entry."""
    outcomes = _cache_refusals(tmp_path)
    assert DeltaOutcome.RESOLVED not in outcomes.values(), outcomes
    for case, outcome in outcomes.items():
        res = CliRunner().invoke(action_cli, ["delta-buildable", outcome.value])
        assert res.exit_code == 0, f"{case}: {outcome} is not buildable"

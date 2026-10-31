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

"""ADR-091 Phase 4: the renamed Action inputs.

Two guarantees, both executed rather than read from the text of ``run.sh``:

* each **new** input forwards the same CLI argv its predecessor did, so a
  rename changes spelling and nothing else;
* each **old** input is a fail-closed tombstone: setting it fails the run
  before any analysis, and the ``::error::`` names the replacement. The
  generic tombstone contract (``test_action_retired_inputs_contract.py``)
  already proves the rejection happens; this module pins the replacement
  name each one must point at.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import _yaml_fast
import pytest
from _action_run_sh_harness import REQUIRES_POSIX_SHELL, _compare_env, _run_action
from _workflow_exec import bash_executable, require_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_DIR = REPO_ROOT / "action"
VALIDATE_SH = ACTION_DIR / "validate-inputs.sh"

pytestmark = REQUIRES_POSIX_SHELL


def _logging_abicheck(tmp_path: Path) -> tuple[Path, Path]:
    """An ``abicheck`` stub that records every invocation's argv, one
    argument per line, each invocation ended by a ``---`` line, and exits 0.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "argv.log"
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do printf \'%s\\n\' "$arg"; done >> "' + str(log) + '"\n'
        "printf -- '---\\n' >> \"" + str(log) + '"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir, log


def _invocations(log: Path) -> list[list[str]]:
    """Each recorded abicheck invocation as its argv list."""
    if not log.exists():
        return []
    calls: list[list[str]] = []
    current: list[str] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line == "---":
            calls.append(current)
            current = []
        else:
            current.append(line)
    return calls


def _compare_argv(log: Path, report: Path) -> list[str]:
    """The argv of the user's own ``compare`` call, identified by its report.

    On a CI runner, run.sh also injects a second JSON report for the PR
    comment (``runner_temp/abicheck-pr-json.*``), so counting ``compare`` calls
    depends on the runner's environment. The user's report path is fixed by
    ``_compare_env``, so select on it instead.
    """
    matches = [
        c
        for c in _invocations(log)
        if c and c[0] == "compare" and f"json={report}" in c
    ]
    assert len(matches) == 1, f"expected one user compare call, got {_invocations(log)}"
    return matches[0]


def _values_after(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


def _run_compare(tmp_path: Path, extra: dict[str, str]) -> tuple[dict, Path]:
    bindir, log = _logging_abicheck(tmp_path)
    env = _compare_env(tmp_path)
    env.update(extra)
    outputs = _run_action(tmp_path, env, bindir)
    return outputs, log


# --- forwarding: each new input produces the argv its predecessor did ---


def test_policy_name_forwards_as_one_policy_flag(tmp_path: Path) -> None:
    """`policy` takes a built-in profile name: one `--policy NAME`."""
    _, log = _run_compare(tmp_path, {"INPUT_POLICY": "security"})
    argv = _compare_argv(log, tmp_path / "report.json")
    assert _values_after(argv, "--policy") == ["security"]


def test_policy_file_path_forwards_through_the_same_input(tmp_path: Path) -> None:
    """`policy` also takes a policy document path: the same single flag,
    with the path as its value (the retired `policy-file` carried this)."""
    _, log = _run_compare(tmp_path, {"INPUT_POLICY": "policies/strict.yml"})
    argv = _compare_argv(log, tmp_path / "report.json")
    assert _values_after(argv, "--policy") == ["policies/strict.yml"]


def test_old_and_new_labels_forward_as_label_flags(tmp_path: Path) -> None:
    """`old-label`/`new-label` replace `old-version`/`new-version` and reach
    the CLI as `--label old=`/`new=`, as the predecessor inputs did."""
    _, log = _run_compare(
        tmp_path, {"INPUT_OLD_LABEL": "1.0", "INPUT_NEW_LABEL": "2.0"}
    )
    labels = _values_after(_compare_argv(log, tmp_path / "report.json"), "--label")
    assert "old=1.0" in labels
    assert "new=2.0" in labels


def test_old_header_forwards_as_sided_header_flag(tmp_path: Path) -> None:
    """`old-header` is the sided header input the retired `devel-pkg1` now
    folds into; the CLI receives it as `--header old=`."""
    _, log = _run_compare(tmp_path, {"INPUT_OLD_HEADER": "v1.h"})
    headers = _values_after(_compare_argv(log, tmp_path / "report.json"), "--header")
    assert "old=v1.h" in headers


def test_public_header_dirs_is_retired_and_not_forwarded_as_a_header_root(
    tmp_path: Path,
) -> None:
    """`public-header-dirs` is RETIRED to `set: scope.public_header_dirs=[...]`
    (ADR-091 D7): run.sh no longer forwards it as a -H root."""
    bindir, log = _logging_abicheck(tmp_path)
    env = {
        "INPUT_MODE": "dump",
        "INPUT_NEW_LIBRARY": _compare_env(tmp_path)["INPUT_NEW_LIBRARY"],
        "INPUT_PUBLIC_HEADER_DIRS": "include/",
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "snap.json"),
    }
    _run_action(tmp_path, env, bindir)
    dump = [c for c in _invocations(log) if c and c[0] == "dump"]
    assert dump, f"no dump invocation recorded: {_invocations(log)}"
    assert "include/" not in _values_after(dump[0], "-H")


# --- tombstones: each retired name fails before analysis and names its replacement ---

RENAMED: list[tuple[str, str, str]] = [
    # (retired input, its env var, replacement input named in the error)
    ("policy-file", "INPUT_POLICY_FILE", "policy"),
    ("ast-frontend", "INPUT_AST_FRONTEND", "frontend"),
    ("gcc-path", "INPUT_GCC_PATH", "compiler"),
    ("gcc-prefix", "INPUT_GCC_PREFIX", "compiler"),
    ("gcc-options", "INPUT_GCC_OPTIONS", "compile-options"),
    (
        "analysis-assurance-complete",
        "INPUT_ANALYSIS_ASSURANCE_COMPLETE",
        "require-complete",
    ),
    ("public-header-dir", "INPUT_PUBLIC_HEADER_DIR", "public-header-dirs"),
    ("debug-info1", "INPUT_DEBUG_INFO1", "old-debug-info"),
    ("debug-info2", "INPUT_DEBUG_INFO2", "new-debug-info"),
    ("devel-pkg1", "INPUT_DEVEL_PKG1", "old-header"),
    ("devel-pkg2", "INPUT_DEVEL_PKG2", "new-header"),
    ("old-version", "INPUT_OLD_VERSION", "old-label"),
    ("new-version", "INPUT_NEW_VERSION", "new-label"),
]


@pytest.mark.parametrize(("retired", "env_var", "replacement"), RENAMED)
def test_retired_input_fails_preflight_and_names_its_replacement(
    tmp_path: Path, retired: str, env_var: str, replacement: str
) -> None:
    require_bash()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": "libfoo.so",
            env_var: "some-value",
        }
    )
    result = subprocess.run(
        [bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=REPO_ROOT,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "::error::" in combined, combined
    assert f"{retired} is RETIRED" in combined, combined
    assert f"renamed to {replacement}" in combined, combined


def test_require_complete_analysis_points_at_require_complete(tmp_path: Path) -> None:
    """The existing tombstone keeps failing, and now names `require-complete`."""
    require_bash()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": "libfoo.so",
            "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true",
        }
    )
    result = subprocess.run(
        [bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=REPO_ROOT,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "require-complete-analysis" in combined, combined
    assert "require-complete" in combined, combined


# The validate step's tombstone wires must read the RETIRED input, not its
# replacement. A wire reading the replacement makes a plain, valid run fail
# (old-label defaults to 'old', so every run was rejected) or misses the
# retired spelling altogether. Asserted on the parsed workflow, because the
# shell-level tests above set INPUT_* directly and never see these expressions.
_TOMBSTONE_WIRES = {
    "INPUT_POLICY_FILE": "policy-file",
    "INPUT_AST_FRONTEND": "ast-frontend",
    "INPUT_GCC_PATH": "gcc-path",
    "INPUT_GCC_PREFIX": "gcc-prefix",
    "INPUT_GCC_OPTIONS": "gcc-options",
    "INPUT_ANALYSIS_ASSURANCE_COMPLETE": "analysis-assurance-complete",
    "INPUT_PUBLIC_HEADER_DIR": "public-header-dir",
    "INPUT_DEBUG_INFO1": "debug-info1",
    "INPUT_DEBUG_INFO2": "debug-info2",
    "INPUT_DEVEL_PKG1": "devel-pkg1",
    "INPUT_DEVEL_PKG2": "devel-pkg2",
    "INPUT_OLD_VERSION": "old-version",
    "INPUT_NEW_VERSION": "new-version",
}


def _step(action: dict, name: str) -> dict:
    return next(s for s in action["runs"]["steps"] if s.get("name") == name)


def test_validate_step_tombstone_wires_read_the_retired_inputs() -> None:
    action = _yaml_fast.safe_load(
        (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
    )
    env = _step(action, "Validate mode/input combination")["env"]
    for var, retired in _TOMBSTONE_WIRES.items():
        assert env[var] == f"${{{{ inputs.{retired} }}}}", (var, env[var])


def test_run_step_never_forwards_a_tombstone() -> None:
    action = _yaml_fast.safe_load(
        (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
    )
    env = _step(action, "Run abicheck")["env"]
    assert not set(_TOMBSTONE_WIRES) & set(env)

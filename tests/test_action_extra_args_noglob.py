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

"""``extra-args`` is word-split, never pathname-expanded.

Bug class: a user-supplied Action input expanded unquoted in shell, so
bash both word-splits it AND globs each word against the runner's working
directory. ``action/run.sh`` split ``extra-args`` that way at three sites
(the real ``CMD`` append and two tokenizers that predict it), and
``actions/check-target``'s overlay step at a fourth: ``extra-args: '*'`` in
a checkout holding a file named ``--format=json`` injected that file name
as a real flag.

General invariant: for every ``extra-args`` value, the argv the CLI
receives ends with exactly ``value.split()`` -- Python's whitespace split,
an oracle independent of bash -- whatever files the workspace holds; and
no Action shell script expands a user input unquoted where bash would glob
it (``TestNoUnquotedInputExpansion``, a whole-tree scan).
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _run_action,
    _stub_abicheck,
)
from _assurance_overlay_exec import _run_overlay
from _shell_unquoted_expansions import unquoted_expansions
from _workflow_exec import make_workspace
from _yaml_fast import safe_load

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Workspace file names chosen to match the glob tokens below; two of them
#: are spelled as real CLI flags, the injection the gap described.
_WORKSPACE_FILES = ("a", "b", "--format=json", "--label=FROM_GLOB", "libz.so")

#: ``extra-args`` values: each holds at least one token that matches a
#: workspace file as a glob, across ``*``, ``?``, ``[...]`` and mixed
#: literal/glob words, with the space/tab/newline separators the input has
#: always been split on.
_GLOB_VALUES = (
    "*",
    "?",
    "[a]",
    "[ab]",
    "--*",
    "lib*.so",
    "--severity-preset strict *",
    "--severity-preset\tstrict\n?\n[a]",
    "-- *",
)


def _logging_abicheck(tmp_path: Path) -> tuple[Path, Path]:
    """The harness's own ``-o``-honouring stub (a COMPATIBLE report), which
    additionally records each invocation's argv, NUL-separated so a token
    holding a newline could not pass for two."""
    payload = json.dumps({"report_schema_version": "4.4", "verdict": "COMPATIBLE"})
    bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode())
    log = tmp_path / "argv.log"
    stub = bindir / "abicheck"
    shebang, body = stub.read_text(encoding="utf-8").split("\n", 1)
    record = f"{{ printf '%s\\0' \"$@\"; printf '\\1\\0'; }} >> \"{log}\""
    stub.write_text(f"{shebang}\n{record}\n{body}", encoding="utf-8")
    return bindir, log


def _compare_argv(log: Path, report: Path) -> list[str]:
    calls: list[list[str]] = [[]]
    for field in log.read_bytes().split(b"\0")[:-1]:
        if field == b"\1":
            calls.append([])
        else:
            calls[-1].append(field.decode("utf-8"))
    matches = [c for c in calls if c and c[0] == "compare" and f"json={report}" in c]
    assert len(matches) == 1, calls
    return matches[0]


@REQUIRES_POSIX_SHELL
class TestRunShExtraArgsIsNotGlobbed:
    """The whole of ``action/run.sh``, in a workspace whose files match."""

    def _argv(self, tmp_path: Path, extra_args: str) -> list[str]:
        for name in _WORKSPACE_FILES:
            (tmp_path / name).write_text("", encoding="utf-8")
        bindir, log = _logging_abicheck(tmp_path)
        env = _compare_env(tmp_path)
        env.update(
            {
                "INPUT_EXTRA_ARGS": extra_args,
                "INPUT_OLD_LABEL": "v1",
                "INPUT_NEW_LABEL": "v2",
            }
        )
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["_exit"] == 0, outputs["_stdout"]
        return _compare_argv(log, tmp_path / "report.json")

    @pytest.mark.parametrize("extra_args", _GLOB_VALUES)
    def test_argv_ends_with_the_whitespace_split_value(
        self, tmp_path: Path, extra_args: str
    ) -> None:
        # The corpus is only meaningful if each value really globs here.
        for name in _WORKSPACE_FILES:
            (tmp_path / name).write_text("", encoding="utf-8")
        assert any(glob.glob(t, root_dir=tmp_path) for t in extra_args.split())
        # Every glob token matches a workspace file, so a globbing split
        # would have replaced it: the oracle is the literal token list.
        tokens = extra_args.split()
        argv = self._argv(tmp_path, extra_args)
        assert argv[-len(tokens) :] == tokens
        for name in _WORKSPACE_FILES:
            if name not in tokens:
                assert name not in argv

    def test_a_flag_shaped_file_does_not_steer_the_label_decision(
        self, tmp_path: Path
    ) -> None:
        """``_extra_args_names_label`` tokenizes the same way: a workspace
        file named ``--label=FROM_GLOB`` matched by ``*`` once made it drop
        the Action's own ``--label`` values. Both labels must stay."""
        argv = self._argv(tmp_path, "*")
        labels = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--label"]
        assert labels == ["old=v1", "new=v2"]
        assert "--label=FROM_GLOB" not in argv

    def test_report_format_is_not_read_from_a_globbed_file_name(
        self, tmp_path: Path
    ) -> None:
        """``_effective_format`` tokenizes the same way: a globbed
        ``--format=json`` file name is not a format override, so the report
        destination the Action chose (``-o json=<output-file>``) stays."""
        argv = self._argv(tmp_path, "--*")
        assert "--format=json" not in argv
        assert argv[-1] == "--*"


class TestCheckTargetOverlayDoesNotGlob:
    """``actions/check-target``'s overlay step re-tokenizes ``extra-args``
    to strip a consumed ``--config``; the remainder it forwards must be the
    literal tokens, not the workspace files they match."""

    @pytest.mark.parametrize("extra_args", _GLOB_VALUES)
    def test_effective_extra_args_keeps_glob_tokens_literal(
        self, tmp_path: Path, extra_args: str
    ) -> None:
        workspace = make_workspace(tmp_path)
        (workspace / "custom.yml").write_text("targets: {}\n", encoding="utf-8")
        for name in _WORKSPACE_FILES:
            (workspace / name).write_text("", encoding="utf-8")
        value = f"--config custom.yml {extra_args}"
        result = _run_overlay(workspace, {"BASE_CONFIG": "", "EXTRA_ARGS": value})
        assert result.returncode == 0, result.stderr
        forwarded = result.outputs["effective-extra-args"]
        assert forwarded.split() == extra_args.split()


# --- repo_scan guard: no user input expanded where bash would glob it -----


def _action_shell_scripts() -> list[tuple[str, str, frozenset[str]]]:
    """``(label, script text, names bound to user input)`` for every shell
    the Action runs: ``action/*.sh`` and every bash ``run:`` step of the
    root and nested ``action.yml`` files. In a step, an ``env:`` key whose
    value reads ``inputs.*`` is user input under any name."""
    scripts: list[tuple[str, str, frozenset[str]]] = []
    for path in sorted((REPO_ROOT / "action").glob("*.sh")):
        scripts.append(
            (
                str(path.relative_to(REPO_ROOT)),
                path.read_text(encoding="utf-8"),
                frozenset(),
            )
        )
    action_files = [
        REPO_ROOT / "action.yml",
        *sorted((REPO_ROOT / "actions").glob("*/action.yml")),
    ]
    for path in action_files:
        steps = safe_load(path.read_text(encoding="utf-8"))["runs"].get("steps", [])
        for index, step in enumerate(steps):
            if "run" not in step or step.get("shell", "bash") not in {"bash", "sh"}:
                continue
            bound = frozenset(
                k for k, v in (step.get("env") or {}).items() if "inputs." in str(v)
            )
            label = f"{path.relative_to(REPO_ROOT)} step {index} ({step.get('name', step.get('id'))})"
            scripts.append((label, step["run"], bound))
    return scripts


@pytest.mark.repo_scan
class TestNoUnquotedInputExpansion:
    def test_the_scan_covers_the_action_shells(self) -> None:
        labels = [label for label, _, _ in _action_shell_scripts()]
        assert "action/run.sh" in labels
        assert any(
            label.startswith("actions/check-target/action.yml") for label in labels
        )

    def test_no_user_input_is_split_and_globbed(self) -> None:
        offenders = []
        for label, text, bound in _action_shell_scripts():
            for hit in unquoted_expansions(
                text,
                lambda name, bound=bound: name.startswith("INPUT_") or name in bound,
            ):
                offenders.append(f"{label}:{hit.line}: {hit.text}")
        assert offenders == [], (
            "unquoted expansion of a user input outside `set -f`: bash globs "
            "each word against the workspace. Quote it, or split it through "
            "run.sh's `_extra_args_split`/`_split_legacy_value`:\n"
            + "\n".join(offenders)
        )


class TestScannerOracle:
    """The scanner against hand-checked bash, so the guard above cannot pass
    by finding nothing. Each case states where bash itself globs."""

    @pytest.mark.parametrize(
        ("script", "globbed"),
        [
            ("set -- ${INPUT_X:-}\n", True),
            ("CMD+=($INPUT_X)\n", True),
            ("for t in $INPUT_X; do :; done\n", True),
            ("cmd foo=$INPUT_X\n", True),
            ("echo `echo $INPUT_X`\n", True),
            ('echo "$(echo $INPUT_X)"\n', True),
            ("f() {\n  local a\n  set -- ${INPUT_X:-}\n}\n", True),
            ('if [[ "$y" =~ ^(#|$) ]]; then\n  echo $INPUT_X\nfi\n', True),
            ('echo "$INPUT_X"\n', False),
            ("echo '$INPUT_X'\n", False),
            ("x=$INPUT_X\n", False),
            ("local x=$INPUT_X\n", False),
            ("[[ -n $INPUT_X ]]\n", False),
            ("case $INPUT_X in a) ;; esac\n", False),
            ("cat <<< $INPUT_X\n", False),
            ("echo ${#INPUT_X}\n", False),
            ('echo ${INPUT_X:+--flag "$INPUT_X"}\n', False),
            ("cat <<EOF\n$INPUT_X\nEOF\n", False),
            ("# echo $INPUT_X\n", False),
            ("set -f\nset -- $INPUT_X\nset +f\n", False),
            ("set -f\nset +f\nset -- $INPUT_X\n", True),
        ],
    )
    def test_reports_exactly_the_globbed_expansions(
        self, script: str, globbed: bool
    ) -> None:
        hits = unquoted_expansions(script, lambda name: name == "INPUT_X")
        assert bool(hits) is globbed, hits

    def test_reports_the_three_pre_fix_run_sh_sites(self) -> None:
        """The pre-fix shapes of the three ``run.sh`` sites, verbatim."""
        script = (
            "_f() {\n"
            "  for _tok in ${INPUT_EXTRA_ARGS:-}; do\n"
            '    case "$_tok" in --label|--label=*) return 0 ;; esac\n'
            "  done\n"
            "}\n"
            "_g() {\n"
            "  set -- ${INPUT_EXTRA_ARGS:-}\n"
            "}\n"
            "CMD+=($INPUT_EXTRA_ARGS)\n"
        )
        hits = unquoted_expansions(script, lambda name: name.startswith("INPUT_"))
        assert [h.line for h in hits] == [2, 7, 9]

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

"""The Action's value-taking-CLI-option knowledge agrees with the real CLI.

**History, because the shape of this module changed twice and both changes
matter.** `action/run.sh` and `actions/check-target/action.yml` each carried a
hand-maintained ``case`` list of every ``compare`` option that consumes a
following token as its own value, which their ``extra-args`` tokenizers use to
tell a real flag from some other option's literal value. An earlier revision
of this module diffed those two lists against ``compare``'s Click parameter
table -- in one direction (``real - listed``), against one command.

Both restrictions were load-bearing. The audit in
``docs/contribute/plans/action-cli-surface-drift.md`` found:

* **Twelve retired option names** still listed (``--ast-frontend``,
  ``--compiler*``, ``--debug-format``, ``--debuginfod-url``,
  ``--frontend-context``, ``--lang``, ``--manifest``, ``--max-findings``,
  ``--pdb-path``, ``--public-header-dir``), invisible to a ``real - listed``
  assertion by construction. A surplus entry is not the safe direction both
  lists' docstrings claimed: the tokenizer still reports it as consuming the
  next token, so a real flag after it is swallowed as a value.
* **Four live options missing** because they belong to ``dump``
  (``--compression``, ``--provenance``) or ``deps compare``
  (``--old-root``, ``--new-root``) rather than ``compare`` -- and the
  tokenizer runs for every mode, since ``_effective_format`` is evaluated
  after ``run.sh``'s mode dispatch.
* ``_extra_args_expand_short_clusters``' terminal set listing ``j``, which
  ``compare`` has never had since ``jobs``/``-j`` was retired (ADR-068 D5).

Making the diff bidirectional and multi-command caught all of that. It is
**not** what this module tests any more, because ADR-070 D3 removed the thing
it was checking: both lists are gone, and each tokenizer now asks the
*installed* abicheck directly (``_cli_value_options_init`` /
``_ct_cli_value_options_init``). A list that does not exist cannot drift.

So these tests execute the real shell functions and compare their answers to
live Click introspection. Three properties that a source-text diff could not
express, and which are the reason this is the better shape:

1. It is **behavioural**. It asserts what the tokenizer decides, not what a
   ``case`` body spells -- so it still holds if the derivation is rewritten.
2. It is **per command**. ``run.sh`` scopes its query to whichever command
   ``MODE`` selects, so ``--compression`` must be value-taking under
   ``mode: dump`` and *not* under ``mode: compare``. A union-based check
   cannot state that, and the union is what reintroduces surplus entries.
3. It covers the **fallback**, which is new surface: with no importable
   abicheck the derivation must degrade toward under-recognition (every token
   opaque) and must not silently resurrect a baked list.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import pytest
from _workflow_exec import bash_executable

from abicheck.cli import main as abicheck_main

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = REPO_ROOT / "actions" / "check-target" / "action.yml"
RUN_SH = REPO_ROOT / "action" / "run.sh"
_HELPERS_MARKER = "# Build the abicheck command"

#: ``run.sh``'s ``MODE`` input -> the command path its tokenizer must ask about.
MODE_TO_COMMAND: dict[str, tuple[str, ...]] = {
    "compare": ("compare",),
    "dump": ("dump",),
    "deps-tree": ("deps", "tree"),
    "deps-compare": ("deps", "compare"),
}


def _resolve_command(path: tuple[str, ...]) -> click.Command:
    node: click.Command = abicheck_main
    for segment in path:
        assert isinstance(node, click.Group), f"{path} is not a group path"
        resolved = node.commands.get(segment)
        assert resolved is not None, f"no such command: {' '.join(path)}"
        node = resolved
    return node


def _value_taking_options(path: tuple[str, ...]) -> set[str]:
    """Every option name on the command at *path* that consumes a following
    token as its own value (``nargs != 0`` and not a boolean flag) -- the same
    ``is_flag`` test Click itself uses to render an option without a metavar.
    """
    return {
        opt
        for param in _resolve_command(path).params
        if isinstance(param, click.Option) and not param.is_flag and param.nargs != 0
        for opt in param.opts
    }


def _run_shell(body: str, *, prelude: str = "") -> str:
    """Source ``run.sh``'s helper region plus *prelude*, run *body*, return
    stdout. *prelude* establishes (or deliberately omits) the interpreter
    variables ``_cli_value_options_init`` reads."""
    script = RUN_SH.read_text(encoding="utf-8")
    helpers = script[: script.index(_HELPERS_MARKER)]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(helpers + prelude + "\n" + body + "\n")
        path = handle.name
    try:
        return subprocess.run(
            [bash_executable(), path], capture_output=True, text=True, encoding="utf-8"
        ).stdout
    finally:
        os.unlink(path)


def _real_prelude(mode: str) -> str:
    return (
        f"\nMODE={shlex.quote(mode)}\n"
        f"_PY_BIN={shlex.quote(sys.executable)}\n"
        '_PY_SAFE_DIR="$(mktemp -d)"\n'
        "_PY_BIN_HAS_ABICHECK=true\n"
        "trap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"
    )


def _derived_options(mode: str) -> set[str]:
    """What ``run.sh`` itself derives for *mode*, read back out of its cache."""
    out = _run_shell(
        '_cli_value_options_init\nprintf \'%s\' "$_CLI_VALUE_OPTIONS" | tr "|" "\\n"',
        prelude=_real_prelude(mode),
    )
    return {line for line in out.splitlines() if line.startswith("-")}


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestRunShDerivesExactlyTheRealOptionSet:
    """``run.sh``'s derivation must equal Click's own answer for the command
    its ``MODE`` selects -- in both directions, which is what the twelve
    surplus and four missing entries each violated."""

    @pytest.mark.parametrize("mode", sorted(MODE_TO_COMMAND))
    def test_derived_set_equals_click(self, mode: str) -> None:
        expected = _value_taking_options(MODE_TO_COMMAND[mode])
        assert expected, f"introspection found no value-taking options for {mode}"
        derived = _derived_options(mode)
        assert derived == expected, (
            f"mode: {mode} derived {sorted(derived)} but the real "
            f"{' '.join(MODE_TO_COMMAND[mode])} takes values for "
            f"{sorted(expected)}"
        )

    def test_derivation_is_command_scoped_not_a_union(self) -> None:
        """The surplus-entry failure mode in its live form: ``--compression``
        is real on ``dump`` and absent from ``compare``. A union-scoped
        derivation would treat it as value-taking under ``mode: compare`` and
        swallow the following real flag as its value -- which is exactly what
        the retired names did. Pinned separately from the parametrized
        equality above so a regression to a union is named, not merely
        implied by a set difference."""
        assert "--compression" in _derived_options("dump")
        assert "--compression" not in _derived_options("compare")
        assert "--old-root" in _derived_options("deps-compare")
        assert "--old-root" not in _derived_options("compare")

    @pytest.mark.parametrize(
        "retired",
        [
            "--ast-frontend",
            "--compiler",
            "--compiler-option",
            "--debug-format",
            "--frontend-context",
            "--lang",
            "--max-findings",
            "--pdb-path",
            "--public-header-dir",
            "-j",
        ],
    )
    def test_a_retired_option_is_never_value_taking(self, retired: str) -> None:
        """The exact names that survived in both hand-maintained lists. They
        are not merely absent from today's derivation -- no derivation from
        the real CLI can reintroduce them, which is the point of ADR-070 D3.
        Kept as an explicit roll-call so the regression has a named test
        rather than only a set comparison."""
        out = _run_shell(
            f"_extra_args_is_value_option {shlex.quote(retired)} "
            "&& echo VALUE || echo OPAQUE",
            prelude=_real_prelude("compare"),
        )
        assert out.strip() == "OPAQUE", f"{retired} is not a live compare option"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestTokenizerConsumesValuesPerMode:
    """End-to-end through ``_extra_args_options``: the tokenizer must consume a
    value-taking option's argument, so a following real flag is recognized
    rather than swallowed (and vice versa).

    ``--provenance`` is the live repro of the *missing*-entry direction, which
    is the one with shipped harm (a false rejection -- see #1222's
    ``--used-by-manifest`` case in
    ``tests/test_reusable_workflows_assurance_overlay_extra_args_config.py``).
    It is a real ``dump`` option that both hand-maintained lists omitted.
    """

    def _tokens(self, mode: str, extra_args: str) -> list[tuple[str, str]]:
        out = _run_shell(
            f"INPUT_EXTRA_ARGS={shlex.quote(extra_args)} _extra_args_options",
            prelude=_real_prelude(mode),
        )
        rows = []
        for line in out.splitlines():
            name, _, value = line.partition("\t")
            rows.append((name, value))
        return rows

    def test_dump_only_option_consumes_its_value(self) -> None:
        assert self._tokens("dump", "--provenance git --config x.yml") == [
            ("--provenance", "git"),
            ("--config", "x.yml"),
        ]

    def test_a_value_shaped_like_a_flag_is_not_read_as_one(self) -> None:
        """#1222's bug class, stated as an invariant: a value-taking option's
        argument is its argument even when it is spelled like a flag."""
        assert self._tokens("compare", "--policy --config") == [
            ("--policy", "--config")
        ]

    def test_a_retired_name_does_not_consume_the_following_flag(self) -> None:
        """The surplus direction, end to end: with the old list, ``--lang``
        consumed ``--config`` as its value and the real ``--config``
        occurrence went unseen."""
        assert self._tokens("compare", "--lang --config x.yml") == [
            ("--lang", ""),
            ("--config", "x.yml"),
        ]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestShortClusterTerminalsAreDerivedToo:
    """``_extra_args_expand_short_clusters`` expands ``-vH`` into ``-v -H``
    only when the last character is a value-taking short option. That terminal
    set was hand-listed and carried ``j``, so ``-vj`` was expanded into an
    option Click itself rejects; it is now the same derivation."""

    def _expand(self, mode: str, token: str) -> str:
        return _run_shell(
            f"_extra_args_expand_short_clusters {shlex.quote(token)} || true",
            prelude=_real_prelude(mode),
        )

    def test_every_real_short_value_option_is_a_terminal(self) -> None:
        shorts = sorted(
            opt
            for opt in _value_taking_options(("compare",))
            if not opt.startswith("--")
        )
        assert shorts, "no short value-taking compare options found"
        for opt in shorts:
            char = opt.lstrip("-")
            assert self._expand("compare", f"-v{char}") == f"-v\n-{char}\n"

    def test_a_retired_short_option_is_not_a_terminal(self) -> None:
        assert self._expand("compare", "-vj") == ""


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestUndeterminedOptionTableFailsClosed:
    """ADR-070 D3 fails closed rather than guessing -- and that is a
    *correction* of this branch's own earlier design, worth stating because
    the wrong version looked principled.

    The first implementation treated an undeterminable table as "nothing is
    value-taking", reasoning that under-recognition is the safe direction. A
    reviewer's counterexample (Codex, PR #1234, P2) disproved it:
    ``extra-args: --version --dry-run`` is argv the CLI accepts by consuming
    ``--dry-run`` as ``--version``'s own value, leaving ``dry_run=False``. An
    opaque tokenizer invents a ``--dry-run`` that is not there, so the Action
    skips its ``--write json=``/``-o`` injection as it must for a real dry
    run -- and a full comparison then runs while the requested output is never
    written. Silent, and an *over*-detection, so the direction argument was
    wrong as well as the severity.

    An undetermined table is therefore fatal -- but only when ``extra-args``
    is non-empty, since with nothing to tokenize there is no decision to get
    wrong and a runner whose interpreter cannot import abicheck should keep
    working.
    """

    _NO_CLI_PRELUDE = (
        "\nMODE=compare\n"
        "_PY_BIN=/nonexistent/python\n"
        '_PY_SAFE_DIR="$(mktemp -d)"\n'
        "_PY_BIN_HAS_ABICHECK=false\n"
        "trap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"
    )

    def _run(self, body: str) -> subprocess.CompletedProcess[str]:
        script = RUN_SH.read_text(encoding="utf-8")
        helpers = script[: script.index(_HELPERS_MARKER)]
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(helpers + self._NO_CLI_PRELUDE + "\n" + body + "\n")
            path = handle.name
        try:
            return subprocess.run(
                [bash_executable(), path],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        finally:
            os.unlink(path)

    def test_extra_args_with_an_undetermined_table_is_fatal(self) -> None:
        result = self._run(
            'INPUT_EXTRA_ARGS="--version --dry-run"\n'
            "_cli_value_options_init\n"
            "_require_cli_value_options_or_fail\n"
            "echo UNREACHABLE"
        )
        assert result.returncode == 1
        assert "UNREACHABLE" not in result.stdout
        assert "::error::" in result.stderr

    def test_the_error_explains_why_guessing_was_refused(self) -> None:
        """If the message only named the symptom, the next maintainer hitting
        it would "fix" it by reinstating the fallback."""
        result = self._run(
            'INPUT_EXTRA_ARGS="--policy x"\n'
            "_cli_value_options_init\n_require_cli_value_options_or_fail"
        )
        assert "--version --dry-run" in result.stderr
        assert (
            "cannot determine which abicheck CLI options take a value" in result.stderr
        )

    def test_without_extra_args_it_is_not_fatal(self) -> None:
        result = self._run(
            'INPUT_EXTRA_ARGS=""\n'
            "_cli_value_options_init\n_require_cli_value_options_or_fail\n"
            "echo CONTINUED"
        )
        assert result.returncode == 0
        assert "CONTINUED" in result.stdout

    def test_undetermined_is_not_spelled_as_an_empty_option_set(self) -> None:
        """``_CLI_VALUE_OPTIONS_DERIVED`` must distinguish "no answer" from
        "no option takes a value" -- conflating them was the original defect."""
        result = self._run(
            "_cli_value_options_init\nprintf '%s' \"$_CLI_VALUE_OPTIONS_DERIVED\""
        )
        assert result.stdout == "false"

    def test_the_undetermined_path_does_not_carry_a_baked_list(self) -> None:
        result = self._run(
            "_cli_value_options_init\nprintf '%s' \"$_CLI_VALUE_OPTIONS\""
        )
        assert result.stdout == "", result.stdout

    def test_a_real_derivation_agrees_with_click_on_the_counterexample(self) -> None:
        """The positive half: with the table derived, the tokenizer's answer for
        the disproving input matches Click's own parse."""
        out = _run_shell(
            'INPUT_EXTRA_ARGS="--version --dry-run" _extra_args_has_dry_run_flag '
            "&& echo DRYRUN || echo NOT_DRYRUN",
            prelude=_real_prelude("compare"),
        )
        assert out.strip() == "NOT_DRYRUN"


@pytest.mark.skipif(
    not ACTION_YML.is_file(), reason="check-target action.yml not found"
)
class TestCheckTargetDerivesTheSameWay:
    """``actions/check-target/action.yml`` carried a byte-identical copy of the
    same list, documented as kept "in sync with run.sh's by hand". Both are now
    derivations, so the invariant is no longer "the two copies agree" (two
    wrong copies can agree -- and did) but "each equals the real CLI"."""

    def _derived(self) -> set[str]:
        text = ACTION_YML.read_text(encoding="utf-8")
        start = text.index('        _ct_cli_value_options=""')
        end = text.index(
            "        }\n", text.index("        _ct_extra_args_is_value_option() {")
        )
        body = "\n".join(
            line[8:] if line.startswith("        ") else line
            for line in text[start : end + len("        }\n")].splitlines()
        )
        script = (
            "set -uo pipefail\n"
            f"PY={shlex.quote(sys.executable)}\n"
            '_py_safe_dir="$(mktemp -d)"\n'
            "trap 'rm -rf \"$_py_safe_dir\"' EXIT\n"
            + body
            + "\n_ct_cli_value_options_init\n"
            'printf \'%s\' "$_ct_cli_value_options" | tr "|" "\\n"\n'
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(script)
            path = handle.name
        try:
            out = subprocess.run(
                [bash_executable(), path],
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout
        finally:
            os.unlink(path)
        return {line for line in out.splitlines() if line.startswith("-")}

    def test_equals_the_real_compare_option_set(self) -> None:
        """Scoped to ``compare`` deliberately: check-target forwards
        ``extra-args`` to the root Action under ``mode: compare`` only, so
        unlike ``run.sh`` it has no mode to vary over. ``--compression``
        (``dump``-only) must therefore be absent here -- the old shared list
        wrongly carried it."""
        expected = _value_taking_options(("compare",))
        derived = self._derived()
        assert derived == expected, (
            "check-target's derivation disagrees with the real compare "
            f"surface; only derived: {sorted(derived - expected)}; "
            f"only real: {sorted(expected - derived)}"
        )

    def _run_guard(self, env_assignments: str) -> subprocess.CompletedProcess[str]:
        """Execute check-target's derivation + guard with *env_assignments*
        prepended, so a test can choose which extra-args variable is set."""
        text = ACTION_YML.read_text(encoding="utf-8")
        start = text.index('        _ct_cli_value_options=""')
        end = text.index(
            "        }\n",
            text.index("        _ct_require_cli_value_options_or_fail() {"),
        ) + len("        }\n")
        body = "\n".join(
            line[8:] if line.startswith("        ") else line
            for line in text[start:end].splitlines()
        )
        script = (
            "set -uo pipefail\n"
            "PY=/nonexistent/python\n"
            '_py_safe_dir=""\n'
            + env_assignments
            + body
            + "\n_ct_cli_value_options_init\n"
            "_ct_require_cli_value_options_or_fail\n"
            "echo CONTINUED\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(script)
            path = handle.name
        try:
            return subprocess.run(
                [bash_executable(), path],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        finally:
            os.unlink(path)

    def test_the_guard_reads_this_steps_own_extra_args_variable(self) -> None:
        """Regression for a bug introduced *by* the fail-closed fix (Codex
        review, PR #1234).

        run.sh's guard reads ``$INPUT_EXTRA_ARGS``; this step receives the
        input as ``$EXTRA_ARGS`` (its own ``env:`` block), and every other
        consumer in the step reads that name. The guard was transplanted
        unchanged, so it tested a variable that is never set here -- always
        empty, always "nothing to tokenize", so the hard failure never fired
        and check-target silently kept the opaque-token behaviour the fix
        exists to remove.

        Asserted behaviourally with ``EXTRA_ARGS`` set and
        ``INPUT_EXTRA_ARGS`` deliberately absent, which is the real shape of
        that step's environment -- a test that set both would have passed
        against the bug."""
        result = self._run_guard('EXTRA_ARGS="--version --config"\n')
        assert result.returncode == 1, (
            "check-target's guard did not fail closed with EXTRA_ARGS set; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "CONTINUED" not in result.stdout
        assert "::error::" in result.stderr

    def test_the_guard_still_allows_an_empty_extra_args(self) -> None:
        """Same scoping as run.sh's: nothing to tokenize, nothing to get
        wrong, so an interpreter that cannot import abicheck is not fatal."""
        result = self._run_guard('EXTRA_ARGS=""\n')
        assert result.returncode == 0
        assert "CONTINUED" in result.stdout

    def test_neither_file_carries_a_hand_maintained_option_list_any_more(self) -> None:
        """ADR-070 D3, as an executable check rather than a convention: the
        whole point is that the enumeration is gone. A future PR
        reintroducing a ``case`` list of option names -- the obvious
        "optimisation" to avoid one Python call -- fails here."""
        for path in (RUN_SH, ACTION_YML):
            text = path.read_text(encoding="utf-8")
            for retired in ("--pdb-path", "--public-header-dir", "--frontend-context"):
                assert retired not in text, (
                    f"{path.name} mentions {retired}; a hand-maintained "
                    "value-option list appears to have been reintroduced"
                )

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

"""Systematic, generative regression test for the bug class behind Codex's
``--used-by-manifest`` finding on PR #1222 (merged as commit 1acebd605;
this fix follows up on ``main`` post-merge).

Both ``actions/check-target/action.yml``'s ``_ct_extra_args_is_value_option``
and ``action/run.sh``'s own hand-synced sibling ``_extra_args_is_value_option``
are hand-maintained, static ``case`` lists of every ``compare`` CLI option
that consumes a following token as its own value -- deliberately duplicated
rather than sourced (see each function's own docstring), and deliberately
NOT derived at run time (the Action has no live installed CLI to introspect
before it even resolves which Python/``abicheck`` install is on PATH). An
option missing from either list is misread as a bare boolean flag, which
under-recognizes a real ``--config`` occurrence following it and can
therefore reject an otherwise-valid ``extra-args`` string outright (see
``tests/test_reusable_workflows_assurance_overlay_extra_args_config.py``'s
``TestAssuranceOverlayRecognizesUsedByManifestAsValueOption`` for the exact
reported repro).

A *surplus* entry -- a name the CLI no longer takes a value for -- is the
opposite and worse failure: the list still reports it as consuming the
following token, so the real flag after it is swallowed as its value.

**This module originally checked only one of those two directions, and only
against ``compare``.** A later Action-vs-CLI surface audit
(``docs/contribute/plans/action-cli-surface-drift.md``) found both omissions
load-bearing:

* Twelve retired option names (``--ast-frontend``, ``--compiler*``,
  ``--debug-format``, ``--debuginfod-url``, ``--frontend-context``,
  ``--lang``, ``--manifest``, ``--max-findings``, ``--pdb-path``,
  ``--public-header-dir``) survived in both lists across multiple merged
  PRs, invisible to a ``real - list`` assertion by construction.
* Four genuine value-taking options were missing because they belong to
  ``dump`` (``--compression``, ``--provenance``) or ``deps compare``
  (``--old-root``, ``--new-root``) rather than ``compare`` -- and the
  tokenizer runs for every mode, not just ``compare``
  (``_effective_format`` is evaluated after ``run.sh``'s mode dispatch).
* ``_extra_args_expand_short_clusters``' cluster-terminal set listed ``j``
  as a value-taking short option; ``compare`` has no ``-j`` at all.

So the invariant here is now **bidirectional**, and its ground truth is the
**union** over every command the Action invokes.

``--used-by-manifest`` was the ONE option Codex's review flagged -- but per
root ``AGENTS.md``'s "fix the cause, not the instance" principle, the real
bug class is "this hand-maintained enumeration can silently go stale," not
"this one option's name is missing." Investigating that class directly
(rather than patching only the reported instance) found THREE more silent
omissions shared by both lists: ``--select``, ``--select-required``, and
``--max-findings-per-library`` -- none reported by Codex, all found by
asking the systematic question this module now asks mechanically.

This module is the generative invariant test the "General invariant" row of
the bug-fix test contract (``.github/PULL_REQUEST_TEMPLATE.md``,
``scripts/check_bugfix_test_contract.py``) requires: rather than hand-listing
"the CLI's value-taking options" a second time (which would just be a third
copy of the same list, equally capable of going stale), it introspects the
REAL ``compare`` Click command's own parameter table -- the same
``python -c "...click introspection..."`` technique each hand-maintained
list's own docstring cites as its (never re-run) provenance -- and diffs
that ground truth against both files' hand-maintained lists directly. A
future PR adding a new value-taking ``compare`` option without touching
either scanner now fails this test immediately, instead of waiting for a
sixth Codex round to notice by inspection.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest

from abicheck.cli import main as abicheck_main

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = REPO_ROOT / "actions" / "check-target" / "action.yml"
RUN_SH = REPO_ROOT / "action" / "run.sh"

_OPTION_TOKEN_RE = re.compile(r"-{1,2}[A-Za-z][A-Za-z0-9-]*")


def _extract_case_options(text: str, function_name: str) -> set[str]:
    """Parse *function_name*'s own ``case "$1" in ... esac`` body out of
    *text* and return every option token (``--foo``/``-f``) it lists.

    Deliberately a real parse of the shell source, not a hand-copied
    expectation: this is what lets a future edit to either list be checked
    against ground truth automatically rather than against another
    hand-maintained copy that could drift right alongside it.
    """
    func_start = text.index(f"{function_name}() {{")
    case_start = text.index('case "$1" in', func_start)
    esac_idx = text.index("esac", case_start)
    body = text[case_start:esac_idx]
    # Join backslash-newline continuations so the option list reads as one
    # logical line regardless of how it's wrapped.
    body = body.replace("\\\n", " ")
    return set(_OPTION_TOKEN_RE.findall(body))


#: Every root command the Action's own mode dispatch can invoke
#: (``action/run.sh``'s ``MODE`` cases: compare / dump / deps-tree /
#: deps-compare). The tokenizer is NOT compare-only: ``_effective_format``
#: is evaluated at ``action/run.sh:3199``, *after* the mode dispatch, so a
#: ``dump``- or ``deps``-only value-taking option left out of the list is
#: misread exactly the same way ``--used-by-manifest`` was.
ACTION_INVOKED_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("compare",),
    ("dump",),
    ("deps", "tree"),
    ("deps", "compare"),
)


def _resolve_command(path: tuple[str, ...]) -> click.Command:
    """Walk *path* from ``abicheck.cli.main`` down to a concrete command."""
    node: click.Command = abicheck_main
    for segment in path:
        assert isinstance(node, click.Group), f"{segment!r}: {path} is not a group path"
        resolved = node.commands.get(segment)
        assert resolved is not None, f"no such command: {' '.join(path)}"
        node = resolved
    return node


def _value_taking_options(cmd: click.Command) -> set[str]:
    """Every option name on *cmd* that consumes a following token as its own
    value (``nargs != 0`` and not a boolean flag).

    A ``--foo/--no-foo`` boolean toggle (e.g.
    ``--scope-public-headers/--no-scope-public-headers``) is excluded by the
    same ``is_flag`` check Click itself uses to render it without a metavar
    in ``--help``.
    """
    return {
        opt
        for param in cmd.params
        if isinstance(param, click.Option) and not param.is_flag and param.nargs != 0
        for opt in param.opts
    }


def _compare_value_taking_options() -> set[str]:
    """The ground truth: the **union** of value-taking options over every
    command :data:`ACTION_INVOKED_COMMANDS` names -- introspected directly
    off ``abicheck.cli.main``, the same mechanism each hand-maintained
    scanner's own docstring cites as its (static, point-in-time) provenance.

    The union, not ``compare`` alone, is the correct ground truth for two
    independent reasons, and the original ``compare``-only form of this
    helper was wrong on both counts:

    * **Completeness.** The tokenizer runs for every mode (see
      :data:`ACTION_INVOKED_COMMANDS`), so ``dump``'s ``--compression``/
      ``--provenance`` and ``deps compare``'s ``--old-root``/``--new-root``
      are as load-bearing as any ``compare`` option. All four were missing.
    * **Surplus.** ``--sysroot`` is no longer a ``compare`` option at all --
      it survives only on ``deps tree`` -- so a ``compare``-only ground
      truth cannot tell a legitimately cross-command entry from a stale one.
    """
    options: set[str] = set()
    for path in ACTION_INVOKED_COMMANDS:
        options |= _value_taking_options(_resolve_command(path))
    return options


@pytest.fixture(scope="module")
def compare_value_options() -> set[str]:
    options = _compare_value_taking_options()
    # Sanity floor: if this ever collapses to a handful of entries, the
    # introspection itself broke silently (e.g. `commands["compare"]`
    # started raising and got swallowed) rather than compare's real
    # surface actually shrinking that far -- fail loud instead of
    # vacuously passing an empty-set comparison below.
    assert len(options) > 30, (
        f"only {len(options)} value-taking options found via Click "
        "introspection across "
        f"{[' '.join(p) for p in ACTION_INVOKED_COMMANDS]} -- suspiciously "
        "low, investigate before trusting the checks below"
    )
    return options


@pytest.fixture(scope="module")
def action_yml_options() -> set[str]:
    return _extract_case_options(
        ACTION_YML.read_text(encoding="utf-8"),
        "_ct_extra_args_is_value_option",
    )


@pytest.fixture(scope="module")
def run_sh_options() -> set[str]:
    return _extract_case_options(
        RUN_SH.read_text(encoding="utf-8"),
        "_extra_args_is_value_option",
    )


class TestExtraArgsIsValueOptionCompleteness:
    """Every value-taking ``compare`` CLI option must be recognized by both
    hand-synced ``_extra_args_is_value_option`` scanners -- an omission in
    EITHER one under-recognizes that option and can misread its own value as
    a bare flag/unknown token (see this module's own docstring)."""

    def test_used_by_manifest_is_recognized_by_both(
        self, action_yml_options: set[str], run_sh_options: set[str]
    ) -> None:
        """The exact option Codex's review named -- pinned individually so a
        regression in this ONE name is never masked by the broader
        set-difference assertions below happening to pass for other
        reasons."""
        assert "--used-by-manifest" in action_yml_options
        assert "--used-by-manifest" in run_sh_options

    @pytest.mark.parametrize(
        "missing_option",
        ["--select", "--select-required", "--max-findings-per-library"],
    )
    def test_previously_undetected_gaps_are_recognized_by_both(
        self,
        missing_option: str,
        action_yml_options: set[str],
        run_sh_options: set[str],
    ) -> None:
        """Three further genuine value-taking `compare` options this
        module's own systematic check found missing from BOTH scanners --
        never individually reported by Codex, found only by asking the
        general question ("does every real value-taking compare option
        appear in both lists?") this module now asks mechanically rather
        than needing a human to enumerate options by hand."""
        assert missing_option in action_yml_options
        assert missing_option in run_sh_options

    def test_every_real_value_option_is_recognized_by_action_yml(
        self, compare_value_options: set[str], action_yml_options: set[str]
    ) -> None:
        """The exhaustive, generative form of the two tests above: EVERY
        value-taking `compare` option -- not just the four named above --
        must appear in `actions/check-target/action.yml`'s scanner. This is
        what catches a FUTURE missing option mechanically: a PR that adds a
        new value-taking `compare` CLI option without updating this scanner
        fails here immediately, rather than needing another Codex round."""
        missing = compare_value_options - action_yml_options
        assert not missing, (
            "actions/check-target/action.yml's _ct_extra_args_is_value_option "
            f"is missing these real compare value-taking options: {sorted(missing)}"
        )

    def test_every_real_value_option_is_recognized_by_run_sh(
        self, compare_value_options: set[str], run_sh_options: set[str]
    ) -> None:
        """Same invariant, `action/run.sh`'s own sibling scanner."""
        missing = compare_value_options - run_sh_options
        assert not missing, (
            "action/run.sh's _extra_args_is_value_option is missing these "
            f"real compare value-taking options: {sorted(missing)}"
        )

    def test_the_two_hand_synced_lists_agree_with_each_other(
        self, action_yml_options: set[str], run_sh_options: set[str]
    ) -> None:
        """Both files' own docstrings state the two lists are meant to be
        kept in sync by hand (`action.yml`'s: "Keeps this list in sync with
        run.sh's by hand"). Assert that invariant directly rather than
        trusting the comment: a future edit to only one side is exactly how
        this class of gap reappears even after every option above is fixed
        here once."""
        assert action_yml_options == run_sh_options, (
            "actions/check-target/action.yml's and action/run.sh's "
            "value-option scanners have drifted apart: "
            f"only in action.yml: {sorted(action_yml_options - run_sh_options)}; "
            f"only in run.sh: {sorted(run_sh_options - action_yml_options)}"
        )

    def test_action_yml_lists_no_option_the_cli_does_not_take_a_value_for(
        self, compare_value_options: set[str], action_yml_options: set[str]
    ) -> None:
        """The **other** direction, which this module originally did not check
        at all -- and which is why twelve retired option names survived in
        both lists for multiple merged PRs.

        Its own docstring claimed a stale list "can only under-recognize a
        value-taking option ... never mis-attribute some other option's value
        as one". That is true of a *missing* entry and false of a *surplus*
        one: a name the CLI no longer takes a value for is still treated here
        as consuming the following token, so the token after it is swallowed
        as a value instead of being recognized as the real flag it is. The
        "safe by construction" direction both files document therefore only
        ever held in one direction, and nothing asserted the other.
        """
        surplus = action_yml_options - compare_value_options
        assert not surplus, (
            "actions/check-target/action.yml's _ct_extra_args_is_value_option "
            "lists options the CLI does not take a value for on any command "
            f"the Action invokes: {sorted(surplus)}"
        )

    def test_run_sh_lists_no_option_the_cli_does_not_take_a_value_for(
        self, compare_value_options: set[str], run_sh_options: set[str]
    ) -> None:
        """Same surplus invariant, `action/run.sh`'s own sibling scanner."""
        surplus = run_sh_options - compare_value_options
        assert not surplus, (
            "action/run.sh's _extra_args_is_value_option lists options the "
            "CLI does not take a value for on any command the Action "
            f"invokes: {sorted(surplus)}"
        )


def _short_cluster_terminals(text: str, function_name: str) -> set[str]:
    """The single-character terminal set *function_name*'s own
    ``case "$_last" in H | I | o) ;;`` arm accepts."""
    start = text.index(f"{function_name}() {{")
    arm = text.index('case "$_last" in', start)
    end = text.index(") ;;", arm)
    return {
        tok.strip()
        for tok in text[text.index("\n", arm) : end].split("|")
        if tok.strip()
    }


class TestShortClusterTerminalsMatchTheCli:
    """``_extra_args_expand_short_clusters`` expands a clustered short-option
    token (``-vH`` for ``-v -H``) only when its last character is one of a
    hand-listed set of value-taking short options.

    That set listed ``j`` as a fourth terminal alongside ``H``/``I``/``o``,
    and both copies' comments asserted ``compare`` had "four value-taking"
    short options. ``compare`` has no ``-j`` at all -- ``jobs``/``-j`` was
    retired with ADR-068 D5 -- so a ``-vj`` token was expanded into an
    option Click itself would reject. Same stale-snapshot class as the
    option tables above, in a second place, with its own false comment; so
    it gets the same derived-from-Click invariant rather than another
    hand-checked comment.
    """

    def test_terminals_are_exactly_the_real_short_value_options(self) -> None:
        expected = {
            opt.lstrip("-")
            for opt in _compare_value_taking_options()
            if not opt.startswith("--")
        }
        for path, function_name in (
            (RUN_SH, "_extra_args_expand_short_clusters"),
            (ACTION_YML, "_ct_extra_args_expand_short_clusters"),
        ):
            actual = _short_cluster_terminals(
                path.read_text(encoding="utf-8"), function_name
            )
            assert actual == expected, (
                f"{path.name}'s {function_name} expands cluster terminals "
                f"{sorted(actual)} but the real short value-taking options "
                f"across {[' '.join(p) for p in ACTION_INVOKED_COMMANDS]} are "
                f"{sorted(expected)}"
            )

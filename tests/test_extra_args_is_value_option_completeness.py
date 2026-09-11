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


def _compare_value_taking_options() -> set[str]:
    """The ground truth: every option name (long and short) the REAL
    ``compare`` Click command accepts that consumes a following token as its
    own value (``nargs != 0`` and not a boolean flag) -- introspected
    directly off ``abicheck.cli.main.commands["compare"]``, the same
    mechanism each hand-maintained scanner's own docstring cites as its
    (static, point-in-time) provenance. A ``--foo/--no-foo`` boolean toggle
    (e.g. ``--scope-public-headers/--no-scope-public-headers``) is excluded
    by the same ``is_flag`` check Click itself uses to render it without a
    metavar in ``--help``.
    """
    compare_cmd = abicheck_main.commands["compare"]
    value_options: set[str] = set()
    for param in compare_cmd.params:
        if not isinstance(param, click.Option):
            continue
        if param.is_flag or param.nargs == 0:
            continue
        value_options.update(param.opts)
    return value_options


@pytest.fixture(scope="module")
def compare_value_options() -> set[str]:
    options = _compare_value_taking_options()
    # Sanity floor: if this ever collapses to a handful of entries, the
    # introspection itself broke silently (e.g. `commands["compare"]`
    # started raising and got swallowed) rather than compare's real
    # surface actually shrinking that far -- fail loud instead of
    # vacuously passing an empty-set comparison below.
    assert len(options) > 30, (
        f"only {len(options)} value-taking compare options found via "
        "Click introspection -- suspiciously low, investigate before "
        "trusting the completeness check below"
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

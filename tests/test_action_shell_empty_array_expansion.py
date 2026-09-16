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

"""Empty-array expansion under ``set -u`` on macOS's stock bash 3.2.

Pre-4.4 bash -- which macOS still ships, GPLv2-frozen at 3.2 -- treats an
*empty* array expanded as ``"${arr[@]}"`` under ``set -u`` as an
unbound-variable reference and aborts the script. Bash 4.4 special-cased it
away, so the bug is invisible on every Linux runner and on a developer's own
machine, and surfaces only on the macOS lane -- which is exactly how it
reached CI here: seven tests failed at once on
``GATE_ARGS[@]: unbound variable``, with the Linux legs green.

This is a *product* bug, not a test-only one: the published Actions run on
whatever shell the runner has, so a macOS consumer of ``actions/aggregate``
or ``actions/verify-baseline-source`` would have hit it for real on the
ordinary path where the array is legitimately empty (no ``--gate``, no
annotated tag, no ``--lookup-failed``).

The guarded spelling ``${arr[@]+"${arr[@]}"}`` is safe on every bash and is
what ``action/run.sh`` already uses, so this states that contract for the two
shells this change introduces.

**A note on what this can and cannot prove.** It is a static check, and the
repository's own guidance is that asserting on a file's *text* proves nothing
about behaviour. The behavioural test is impossible here -- reproducing it
needs a bash 3.2 binary, which no lane has -- so the compensating design is
that the detector is a real function exercised against crafted unsafe and safe
inputs (including the ways a naive implementation gets it wrong: a mention
inside a comment, and the guarded form's own inner ``"${arr[@]}"``), rather
than a regex inlined into an assertion. A detector that cannot fail is worse
than none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ACTIONS = Path(__file__).resolve().parents[1] / "actions"

#: Shells this change introduces. Deliberately not the whole tree: the other
#: action shells carry ~22 pre-existing bare expansions whose arrays are
#: non-empty in practice, and sweeping them into a baseline here would widen
#: this change well past the failure it fixes. The detector below is written
#: to take any path, so making it repo-wide later is a scoping decision, not
#: a rewrite.
_COVERED = (
    _ACTIONS / "aggregate" / "run.sh",
    _ACTIONS / "verify-baseline-source" / "run.sh",
)

#: A bare ``"${NAME[@]}"``. The lookbehind rejects the *inner* occurrence of
#: the guarded form ``${NAME[@]+"${NAME[@]}"}``, whose text contains this
#: pattern verbatim -- the first thing a naive detector gets wrong, and the
#: reason every guarded expansion would otherwise report itself as a
#: violation.
_BARE = re.compile(r'(?<!\+)"\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}"')


def unguarded_expansions(script: str) -> list[tuple[int, str]]:
    """``(line number, array name)`` for each unguarded ``"${arr[@]}"``.

    Full-line comments are skipped: the explanations of this very hazard spell
    the unsafe form out, and flagging the comment that documents the rule is
    both wrong and the kind of finding that gets a gate switched off.
    """
    found: list[tuple[int, str]] = []
    for number, line in enumerate(script.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        found.extend((number, match.group(1)) for match in _BARE.finditer(line))
    return found


class TestDetector:
    """The detector's own contract, before it is trusted about real files."""

    def test_a_bare_expansion_is_reported(self) -> None:
        assert unguarded_expansions('cmd "${ARGS[@]}"\n') == [(1, "ARGS")]

    def test_the_guarded_form_is_not_reported(self) -> None:
        """Its text contains the bare form verbatim -- the trap for a naive regex."""
        assert unguarded_expansions('cmd ${ARGS[@]+"${ARGS[@]}"}\n') == []

    def test_a_comment_explaining_the_hazard_is_not_reported(self) -> None:
        assert unguarded_expansions('  # never write "${ARGS[@]}" here\n') == []

    def test_line_numbers_and_several_findings_are_reported(self) -> None:
        script = 'a "${ONE[@]}"\n# "${SKIP[@]}"\nb "${TWO[@]}"\n'
        assert unguarded_expansions(script) == [(1, "ONE"), (3, "TWO")]

    @pytest.mark.parametrize(
        "line",
        [
            'cmd "${ARGS[*]}"\n',  # [*] is a different (and safe) expansion
            'cmd "$ARGS"\n',
            "cmd ${#ARGS[@]}\n",
            "cmd ${ARGS[@]:-}\n",
        ],
    )
    def test_unrelated_spellings_are_not_reported(self, line: str) -> None:
        assert unguarded_expansions(line) == []


class TestCoveredShells:
    @pytest.mark.parametrize("script", _COVERED, ids=lambda p: p.parent.name)
    def test_no_unguarded_expansion(self, script: Path) -> None:
        findings = unguarded_expansions(script.read_text(encoding="utf-8"))
        assert not findings, (
            f"{script} expands a possibly-empty array without the "
            f'${{arr[@]+"${{arr[@]}}"}} guard at: '
            + ", ".join(f"line {line} ({name})" for line, name in findings)
            + ". Under macOS's stock bash 3.2 and set -u this aborts the step."
        )

    def test_the_covered_shells_exist(self) -> None:
        """Vacuity guard: a renamed script must not silently empty this gate."""
        assert _COVERED
        for script in _COVERED:
            assert script.is_file(), script

    def test_the_guard_is_actually_used(self) -> None:
        """...and that they use the guarded form somewhere.

        Without this, deleting every array from both scripts would also make
        the gate above pass, which would not mean the contract is held.
        """
        for script in _COVERED:
            text = script.read_text(encoding="utf-8")
            assert re.search(r'\$\{[A-Za-z_][A-Za-z0-9_]*\[@\]\+"', text), script

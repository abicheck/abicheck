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
import yaml
from _workflow_files import read_repo_text

_ACTIONS = Path(__file__).resolve().parents[1] / "actions"

#: The two shells whose failure created this module. Kept as a named,
#: unconditional pair alongside the repo-wide sweep below: they are the
#: regression this file was written for, and a scoping change to the sweep
#: must not be able to stop covering them.
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
        findings = unguarded_expansions(read_repo_text(script))
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
            text = read_repo_text(script)
            assert re.search(r'\$\{[A-Za-z_][A-Za-z0-9_]*\[@\]\+"', text), script


# ── the repo-wide half ───────────────────────────────────────────────────
#
# The pair above is the regression; this is the class. The note at the top
# of this module said making the detector repo-wide was "a scoping decision,
# not a rewrite" -- and the decision was made the next time the same bug
# shipped, in `publish-baseline.yml`'s tag-resolution step, where the empty
# case is a *lightweight* tag, i.e. the ordinary one.
#
# Two things differ from the pair above, and both narrow rather than widen.
# The rule applies only to an array *declared empty* (`name=()`), which is
# exactly the set that can reach an expansion with no elements -- so the ~22
# pre-existing bare expansions the note mentions are out of scope by
# construction, with no baseline to curate. And it covers workflow and
# composite-action `run:` blocks too, since that is where it shipped.

REPO_ROOT = Path(__file__).resolve().parents[1]

#: `name=()` — an array that starts with no elements.
_DECLARED_EMPTY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=\(\s*\)\s*$", re.M)

#: `${name[@]}` in any form, with the offset of the match.
_EXPANSION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]")

#: The one safe spelling: `${name[@]+"${name[@]}"}`.
_GUARDED = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\+"\$\{\1\[@\]\}"\}')


def _shell_sources() -> list[tuple[str, str]]:
    """``(label, script)`` for every first-party shell body in the repo.

    Three shapes carry shell here and all three have hit this trap: a
    standalone ``.sh``, a workflow step's ``run:``, and a composite Action
    step's ``run:``.
    """
    sources: list[tuple[str, str]] = []

    for script in sorted(REPO_ROOT.glob("actions/*/*.sh")) + sorted(
        REPO_ROOT.glob("action/*.sh")
    ):
        sources.append((str(script.relative_to(REPO_ROOT)), read_repo_text(script)))

    documents = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")) + sorted(
        REPO_ROOT.glob("actions/*/action.yml")
    )
    documents += [REPO_ROOT / "action.yml"]
    for path in documents:
        if not path.is_file():
            continue
        try:
            parsed = yaml.safe_load(read_repo_text(path))
        except yaml.YAMLError:  # pragma: no cover - a parse failure is its own test
            continue
        label = str(path.relative_to(REPO_ROOT))
        for index, body in enumerate(_run_blocks(parsed)):
            sources.append((f"{label}#run[{index}]", body))
    return sources


def _run_blocks(node: object) -> list[str]:
    """Every ``run:`` string anywhere in a parsed workflow or action."""
    found: list[str] = []
    if isinstance(node, dict):
        run = node.get("run")
        if isinstance(run, str):
            found.append(run)
        for value in node.values():
            found.extend(_run_blocks(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_run_blocks(item))
    return found


def _strip_comments(script: str) -> str:
    """Blank out `#` comment bodies, keeping every byte offset intact.

    A comment is prose, not shell: this module's own guard comment quotes
    the unsafe spelling in order to explain it, and the first version of
    this scan dutifully reported that sentence as a violation. Same trap
    `AGENTS.md` records for the `performance.yml` assertion, and the same
    one `test_docs_action_examples.py` already handles. Replacing with
    spaces rather than deleting keeps the guarded-span offsets computed
    below aligned with the original text.

    Two things this must get right, because both fail in the *permissive*
    direction -- they blank real shell, so the scan stops seeing a bare
    expansion and the guard silently passes:

    * **A backslash escapes the next character.** Reading `\\"` as closing a
      double-quoted string leaves the rest of the line looking unquoted,
      so the next ` #` starts a "comment" that swallows real code.
    * **A quoted string may span lines.** Resetting the quote state per
      line does the same thing from the second line onward.

    Single quotes are the exception and not an oversight: in shell, a
    backslash inside `'...'` is a literal backslash, so escape tracking
    applies to double quotes only.
    """
    out: list[str] = []
    quote: str | None = None  # carried ACROSS lines, not reset per line
    for line in script.splitlines(keepends=True):
        cut: int | None = None
        escaped = False
        for index, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if char == "\\" and quote != "'":
                # Outside quotes and inside "..." alike, a backslash makes
                # the next character literal. Inside '...' it does not.
                escaped = True
            elif quote is not None:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1] in " \t"):
                cut = index
                break
        if cut is None:
            out.append(line)
        else:
            tail = line[cut:]
            out.append(
                line[:cut]
                + " " * len(tail.rstrip("\n"))
                + tail[len(tail.rstrip("\n")) :]
            )
    return "".join(out)


def _unguarded(script: str) -> list[str]:
    """Names declared empty and expanded somewhere without the guard."""
    script = _strip_comments(script)
    declared = set(_DECLARED_EMPTY.findall(script))
    if not declared:
        return []
    guarded_spans = [m.span() for m in _GUARDED.finditer(script)]
    offenders: set[str] = set()
    for match in _EXPANSION.finditer(script):
        name = match.group(1)
        if name not in declared:
            continue
        inside = any(
            start <= match.start() and match.end() <= end
            for start, end in guarded_spans
        )
        if not inside:
            offenders.add(name)
    return sorted(offenders)


SOURCES = _shell_sources()


def test_the_scan_found_shell_to_check() -> None:
    """Vacuity guard: a glob that stopped matching would pass in silence."""
    assert len(SOURCES) >= 20, len(SOURCES)
    assert any("publish-baseline" in label for label, _ in SOURCES)


@pytest.mark.parametrize("label,script", SOURCES, ids=[s[0] for s in SOURCES])
def test_an_empty_capable_array_is_never_expanded_bare(label: str, script: str) -> None:
    offenders = _unguarded(script)
    assert not offenders, (
        f"{label}: {offenders} may be empty and are expanded as "
        '"${name[@]}" — on macOS\'s bash 3.2 under `set -u` that is an '
        "unbound-variable error, not an empty expansion. Use "
        '${name[@]+"${name[@]}"}.'
    )


class TestTheRuleItself:
    """Negative and positive controls, so the scan cannot pass vacuously."""

    def test_a_bare_expansion_of_an_empty_array_is_flagged(self) -> None:
        assert _unguarded('args=()\nfoo "${args[@]}"\n') == ["args"]

    def test_the_guarded_form_is_accepted(self) -> None:
        assert _unguarded('args=()\nfoo ${args[@]+"${args[@]}"}\n') == []

    def test_an_array_never_declared_empty_is_out_of_scope(self) -> None:
        # `args=(a b)` cannot reach an expansion with no elements, so the
        # rule does not apply and no allowlist entry is needed.
        assert _unguarded('args=(a b)\nfoo "${args[@]}"\n') == []

    def test_a_guarded_and_a_bare_use_of_the_same_array_still_flags(self) -> None:
        script = 'args=()\nfoo ${args[@]+"${args[@]}"}\nbar "${args[@]}"\n'
        assert _unguarded(script) == ["args"]

    def test_a_comment_quoting_the_unsafe_form_is_not_a_violation(self) -> None:
        # This module's own fix comment does exactly this, and the first
        # version of the scan flagged the sentence.
        script = (
            'args=()\n# never write "${args[@]}" here\nfoo ${args[@]+"${args[@]}"}\n'
        )
        assert _unguarded(script) == []

    def test_a_hash_inside_a_quoted_string_does_not_start_a_comment(self) -> None:
        # Blanking from the first `#` regardless of quoting would hide a
        # real violation living after one on the same line.
        assert _unguarded('args=()\nfoo "a#b" "${args[@]}"\n') == ["args"]

    def test_stripping_preserves_byte_offsets(self) -> None:
        # The guarded-span containment check compares offsets against the
        # stripped text, so a strip that shortened lines would misjudge it.
        source = 'args=()\nfoo ${args[@]+"${args[@]}"} # trailing note\n'
        assert len(_strip_comments(source)) == len(source)

    def test_an_escaped_quote_does_not_end_the_string(self) -> None:
        # Reading `\\"` as a closing quote leaves the rest of the line
        # looking unquoted, so the ` #` inside the string starts a
        # "comment" that blanks the real expansion after it. The scan then
        # reports clean. Fails if escape tracking is removed.
        script = 'args=()\nfoo "a\\" # b" "${args[@]}"\n'
        assert _unguarded(script) == ["args"]

    def test_a_quoted_string_spanning_lines_keeps_its_state(self) -> None:
        # Same failure from the second line on, if quote state resets per
        # line: the ` #` on line 2 is inside the string, not a comment.
        # The expansion must sit AFTER the `#` on the continuation line:
        # that is the text a per-line reset blanks. A first version put it
        # on the next line instead, where the mutation does no damage, and
        # the test passed with the fix removed -- vacuous for the very
        # thing it names.
        script = 'args=()\nfoo "opening\nstill inside # not a comment" "${args[@]}"\n'
        assert _unguarded(script) == ["args"]

    def test_a_backslash_inside_single_quotes_is_literal(self) -> None:
        # The shell rule the fix must NOT over-apply: inside '...' a
        # backslash escapes nothing, so the quote still closes here and the
        # trailing ` #` really is a comment.
        assert _strip_comments("a='x\\' # real comment\n").rstrip() == "a='x\\'"

    def test_stripping_still_preserves_offsets_with_escapes(self) -> None:
        source = 'foo "a\\" b" # note\n'
        assert len(_strip_comments(source)) == len(source)

    def test_the_real_regression_would_have_been_caught(self) -> None:
        # The exact shape that shipped: an array built conditionally and
        # then expanded bare. This is the line the macOS lane failed on.
        script = (
            "args=()\n"
            'if [[ -n "$peel" ]]; then args+=(--tag-object-json "$f"); fi\n'
            'cmd "$TAG" "$REF" "${args[@]}" --out "$OUT"\n'
        )
        assert _unguarded(script) == ["args"]

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

"""No documented ``compare --no-baseline`` example may combine it with a flag
that path actually rejects (exit 64, before any auditing happens).

Bug class: a doc-migration replacement was verified against *whether a flag
exists on `compare` at all* (the full two-sided flag set), not against
whether it is *specifically supported under `--no-baseline`*'s narrower
rejection surface -- so a copy-pasted example looked plausible (the flag is
real, `compare` documents it) while actually exiting 64 before auditing
anything. Three real instances shipped this way in one PR (`docs/use/python-
extensions.md`/`docs/learn/packages-and-consumers.md`'s `--abi3`, `docs/
learn/where-in-the-pipeline.md`'s `--budget`, `docs/learn/template-heavy-
libraries.md`'s `--since`), each found only by a human/Codex re-reading the
rendered page, not by running anything -- exactly the kind of escape this
repo's own bug-fix contract exists to close with an executable check rather
than a one-off fix per instance (AGENTS.md "A bug fix's regression test
targets the bug class, not the one reported input").

**The rejection surface is not one table.** The first version of this file
only drove off ``no_baseline_rulings._UNSUPPORTED_OPTIONS`` -- but that
module's own docstring documents three *more* independent rejection
mechanisms, each with its own guard function, none of which
``_UNSUPPORTED_OPTIONS`` lists (Codex review, fresh evidence, verified live
against the real CLI before this widening, not merely read from source):

- ``_reject_view_tokens_for_no_baseline`` -- any non-default ``--view``
  token (verified live: ``compare --no-baseline <snap> --view leaf`` exits
  64).
- ``_reject_old_sided_inputs`` -- an explicitly OLD-scoped evidence input
  (``_OLD_ONLY_DESTS``'s bare ``old=``-only flags, plus ``_SIDED_SINGLE_
  DESTS``/``_SIDED_LABEL_DESTS``'s ``old=``-prefixed sided flags; verified
  live: ``--header old=...`` and ``--sources old=...`` both exit 64, while
  the identical flag *without* the ``old=`` prefix is supported -- the
  ``old=`` suffix each spelling already carries is exactly what
  distinguishes the two, so a plain substring match stays correct).
- ``_reject_context_stashed_options`` -- ``--variant``, which never reaches
  an ordinary Click destination at all (``expose_value=False``); verified
  live: ``compare --no-baseline <snap> --variant v1`` exits 64. No data
  table exports this spelling (it lives only in the guard's own error
  message), so it is named here directly rather than derived.

General invariant, checked here against the real, current rejection tables/
guards (not a hand-copied list that could itself drift from the source of
truth): no manual page, generated example page, or scenario file may show a
``--no-baseline`` command line also naming a flag any of the four mechanisms
above rejects. Structural (textual co-occurrence on one line), not an
execution of every example -- but it is driven by the real dispatch data
(three of the four; the fourth is a single verified-live literal), so the
check fails loudly the next time a doc example makes the same mistake with a
different flag, not only the ones already found.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from abicheck.frontends.cli.commands.no_baseline_rulings import (
    _OLD_ONLY_DESTS,
    _SIDED_LABEL_DESTS,
    _SIDED_SINGLE_DESTS,
    _UNSUPPORTED_OPTIONS,
)

_REPO = Path(__file__).resolve().parent.parent

#: `_reject_unsupported_options`'s table: every CLI spelling this path
#: rejects outright, derived from the real dispatch table rather than
#: hand-copied.
_UNSUPPORTED_SPELLINGS: tuple[str, ...] = tuple(
    sorted({spelling for spelling, _reason in _UNSUPPORTED_OPTIONS.values()})
)

#: `_reject_old_sided_inputs`'s three tables: every spelling already carries
#: its own `old=` suffix (e.g. `"--sources old="`), which is what makes a
#: plain substring match safe -- it cannot also match that same flag's
#: supported bare/`both=` form.
_OLD_SIDED_SPELLINGS: tuple[str, ...] = tuple(
    sorted(
        set(_OLD_ONLY_DESTS.values())
        | {spelling for _new_dest, spelling in _SIDED_SINGLE_DESTS.values()}
        | {spelling for _new_dest, spelling in _SIDED_LABEL_DESTS.values()}
    )
)

#: `_reject_view_tokens_for_no_baseline`: the guard rejects any *non-default*
#: `--view` token, not the bare flag -- but no real doc example would show
#: `--view` alongside `--no-baseline` with an explicit *default* value
#: (`--view full`), so flagging the flag's mere presence is a safe,
#: verified-live proxy for "a non-default token was given".
_VIEW_SPELLING = "--view"

#: `_reject_context_stashed_options`: `--variant` is declared with
#: `expose_value=False` and stashed on the context by its own callback, so
#: it never reaches an ordinary destination -- neither `_UNSUPPORTED_
#: OPTIONS` nor any sided table can see it. Its spelling exists only inside
#: the guard's own error message, not as an importable constant, so it is
#: named here directly (verified live rather than derived).
_VARIANT_SPELLING = "--variant"

#: The full rejection surface this file scans docs against -- all four
#: mechanisms `no_baseline_rulings.py` documents, not just one table.
_ALL_REJECTED_SPELLINGS: tuple[str, ...] = tuple(
    sorted(
        {
            *_UNSUPPORTED_SPELLINGS,
            *_OLD_SIDED_SPELLINGS,
            _VIEW_SPELLING,
            _VARIANT_SPELLING,
        }
    )
)

#: Roots scanned: hand-authored docs, the generated example pages (a stale
#: flag in a source README reproduces here, so the published page is
#: checked too -- mirrors `scripts/retired_surfaces.py`'s own target set),
#: and the scenario catalogue, whose `flow:` entries are commands a reader
#: is meant to be able to run.
_ROOTS: tuple[Path, ...] = (
    _REPO / "docs",
    _REPO / "catalog" / "cases",
    _REPO / "tests" / "scenarios",
)
_SUFFIXES = frozenset({".md", ".yaml", ".yml"})


def _candidate_files() -> list[Path]:
    files: list[Path] = []
    for root in _ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in _SUFFIXES:
                files.append(path)
    return files


def _yaml_flow_commands(text: str) -> list[tuple[int, str]]:
    """Every scalar value under a ``flow:`` key, real YAML parsed.

    A ``flow:`` list entry is not always one physical line -- a folded
    (``- >``) or literal (``- |``) block scalar spreads the actual command
    text across several indented lines, and a naive line-based scan (the
    first version of this function) only ever saw the bare ``- >`` marker
    line itself, silently missing any flag co-occurring on a continuation
    line (CodeRabbit review, fresh evidence: a deliberately-injected
    multiline violation slipped past undetected). ``yaml.compose`` resolves
    block-scalar folding for us and keeps each scalar node's source line via
    ``start_mark`` -- real YAML semantics, not a second hand-rolled parser
    that could itself drift from PyYAML's own folding rules.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return []
    if root is None:
        return []
    hits: list[tuple[int, str]] = []

    def walk(node: yaml.Node, under_flow: bool) -> None:
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                key_is_flow = (
                    isinstance(key_node, yaml.ScalarNode) and key_node.value == "flow"
                )
                walk(value_node, under_flow or key_is_flow)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item, under_flow)
        elif isinstance(node, yaml.ScalarNode) and under_flow:
            hits.append((node.start_mark.line + 1, node.value))

    walk(root, False)
    return hits


def _command_blocks(text: str, *, is_yaml: bool) -> list[tuple[int, str]]:
    """(start_line, joined_text) for each real, copyable ``abicheck``
    invocation.

    Deliberately narrow: prose explaining *why* a flag is rejected
    routinely names both ``--no-baseline`` and the rejected flag in one
    sentence or table cell (that is the correct, intentional shape of that
    explanation) -- scanning those would flag the very sentences that
    document this test's own invariant. Only a line inside a fenced
    ```bash/```console/```sh code block, or a YAML ``flow:`` scenario
    entry, is a real command a reader would run. A shell line-continuation
    (``\\`` at end of line) is joined with the next line so a multi-line
    invocation is checked as one command, not two independently-innocent
    halves.
    """
    if is_yaml:
        return [
            (line_no, value)
            for line_no, value in _yaml_flow_commands(text)
            if "abicheck " in value
        ]
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    in_shell_fence = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            fence_lang = stripped[3:].strip().lower()
            if not in_shell_fence:
                in_shell_fence = fence_lang in {"bash", "console", "sh", "shell"}
            else:
                in_shell_fence = False
            i += 1
            continue
        if in_shell_fence and "abicheck " in line:
            start = i
            joined = line
            while joined.rstrip().endswith("\\") and i + 1 < n:
                i += 1
                joined = joined.rstrip()[:-1] + " " + lines[i]
            out.append((start + 1, joined))
        i += 1
    return out


def _violations() -> list[tuple[Path, int, str, str]]:
    """Every (file, line_no, flag, command_text) where a real command
    names both ``--no-baseline`` and an unsupported flag."""
    hits: list[tuple[Path, int, str, str]] = []
    for path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "--no-baseline" not in text:
            continue
        is_yaml = path.suffix in {".yaml", ".yml"}
        for line_no, command in _command_blocks(text, is_yaml=is_yaml):
            if "--no-baseline" not in command:
                continue
            for flag in _ALL_REJECTED_SPELLINGS:
                if flag in command:
                    hits.append((path, line_no, flag, command.strip()))
    return hits


def test_no_documented_no_baseline_example_uses_an_unsupported_flag() -> None:
    violations = _violations()
    if not violations:
        return
    lines = [
        f"{p.relative_to(_REPO)}:{n}: names {flag!r} alongside --no-baseline "
        f"(rejected -- exit 64, see no_baseline_rulings.py's four rejection "
        f"mechanisms): {text!r}"
        for p, n, flag, text in violations
    ]
    pytest.fail(
        "compare --no-baseline example(s) use a flag that path rejects:\n"
        + "\n".join(lines)
    )


def test_yaml_folded_block_scalar_flow_entry_is_caught() -> None:
    """A deliberately-injected multiline violation, proving the YAML-aware
    parser (not a per-physical-line scan) is what actually runs.

    The violation sits entirely on a *continuation* line of a folded
    (``- >``) block scalar -- the exact shape a naive line-based scan (this
    file's first version) missed, since the ``- >`` marker line itself
    never contains ``abicheck`` or the offending flag at all. Written
    in-process against the same helpers ``_violations()`` uses, rather than
    a committed fixture file, so this test cannot itself go stale by
    quietly matching a real doc fix later.
    """
    text = (
        "scenarios:\n"
        "  - id: SC-INJECTED-MULTILINE-VIOLATION\n"
        "    flow:\n"
        "      - >\n"
        "        abicheck compare --no-baseline snapshot.abi.json\n"
        "        --since origin/main\n"
    )
    commands = _command_blocks(text, is_yaml=True)
    assert commands, "the folded block scalar itself was not even parsed"
    joined = commands[0][1]
    assert "--no-baseline" in joined
    assert "--since" in joined
    assert any(
        "--since" in command
        for _line_no, command in commands
        if "--no-baseline" in command
    ), (
        "the injected --since violation must be visible on the same "
        "resolved command text the real scan checks"
    )


@pytest.mark.parametrize(
    ("injected_flag", "label"),
    [
        ("--view leaf", "view-token"),
        ("--header old=include/foo.h", "old-sided (bare-table)"),
        ("--sources old=./src", "old-sided (sided-single-table)"),
        ("--version old=1.2", "old-sided (sided-label-table)"),
        ("--variant v1", "context-stashed"),
    ],
)
def test_each_rejection_mechanism_is_caught_in_a_bash_example(
    injected_flag: str, label: str
) -> None:
    """A deliberately-injected violation per mechanism, proving the scan
    covers all four -- not only `_UNSUPPORTED_OPTIONS`.

    Each of these was verified live against the real CLI before being added
    here (see the module docstring): every one of these five commands
    actually exits 64 under `--no-baseline` today. Written in-process
    against the same helpers `_violations()` uses, rather than a committed
    fixture file, so these cannot themselves go stale by quietly matching a
    real doc fix later.
    """
    text = (
        "```bash\n"
        f"abicheck compare --no-baseline snapshot.abi.json {injected_flag}\n"
        "```\n"
    )
    commands = _command_blocks(text, is_yaml=False)
    assert commands, f"[{label}] the fenced block itself was not even parsed"
    joined = commands[0][1]
    assert "--no-baseline" in joined
    assert any(flag in joined for flag in _ALL_REJECTED_SPELLINGS), (
        f"[{label}] {injected_flag!r} is not covered by _ALL_REJECTED_SPELLINGS"
    )


def test_unsupported_options_table_is_non_empty() -> None:
    """A negative control for the scan above: if a source-of-truth table
    were ever accidentally emptied, the positive test above would pass
    vacuously (scanning for zero flags always finds zero hits) without
    actually checking anything. Pins that each table/mechanism this test
    drives off keeps naming real, rejected flags."""
    assert len(_UNSUPPORTED_SPELLINGS) >= 20
    assert "--abi3" in _UNSUPPORTED_SPELLINGS
    assert "--budget" in _UNSUPPORTED_SPELLINGS
    assert "--since" in _UNSUPPORTED_SPELLINGS
    assert len(_OLD_SIDED_SPELLINGS) >= 8
    assert "--header old=" in _OLD_SIDED_SPELLINGS
    assert "--sources old=" in _OLD_SIDED_SPELLINGS
    assert "--version old=" in _OLD_SIDED_SPELLINGS
    assert _VIEW_SPELLING in _ALL_REJECTED_SPELLINGS
    assert _VARIANT_SPELLING in _ALL_REJECTED_SPELLINGS
    # The combined set must be strictly larger than any one table alone --
    # otherwise the widening above silently collapsed back to one mechanism.
    assert len(_ALL_REJECTED_SPELLINGS) > len(_UNSUPPORTED_SPELLINGS)

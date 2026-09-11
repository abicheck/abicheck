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
`no_baseline_rulings._UNSUPPORTED_OPTIONS` table -- so a copy-pasted example
looked plausible (the flag is real, `compare` documents it) while actually
exiting 64 before auditing anything. Three real instances shipped this way
in one PR (`docs/use/python-extensions.md`/`docs/learn/packages-and-
consumers.md`'s `--abi3`, `docs/learn/where-in-the-pipeline.md`'s
`--budget`, `docs/learn/template-heavy-libraries.md`'s `--since`), each
found only by a human/Codex re-reading the rendered page, not by running
anything -- exactly the kind of escape this repo's own bug-fix contract
exists to close with an executable check rather than a one-off fix per
instance (AGENTS.md "A bug fix's regression test targets the bug class,
not the one reported input").

General invariant, checked here against the real, current
``_UNSUPPORTED_OPTIONS``/``_reject_*`` tables (not a hand-copied list that
could itself drift from the source of truth): no manual page, generated
example page, or scenario file may show a ``--no-baseline`` command line
also naming one of those flags. Structural (textual co-occurrence on one
line), not an execution of every example -- but it is driven by the exact
table the real CLI dispatch consults, so a flag added to (or renamed within)
that table is covered automatically, and the check fails loudly the next
time a doc example makes the same mistake with a *different* flag, not only
the three named above.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.frontends.cli.commands.no_baseline_rulings import _UNSUPPORTED_OPTIONS

_REPO = Path(__file__).resolve().parent.parent

#: Every CLI spelling this path rejects, derived from the real dispatch
#: table rather than hand-copied -- see the module docstring.
_UNSUPPORTED_SPELLINGS: tuple[str, ...] = tuple(
    sorted({spelling for spelling, _reason in _UNSUPPORTED_OPTIONS.values()})
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
    lines = text.splitlines()
    if is_yaml:
        return [
            (i, line)
            for i, line in enumerate(lines, start=1)
            if "abicheck " in line and line.lstrip().startswith("- ")
        ]
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
            for flag in _UNSUPPORTED_SPELLINGS:
                if flag in command:
                    hits.append((path, line_no, flag, command.strip()))
    return hits


def test_no_documented_no_baseline_example_uses_an_unsupported_flag() -> None:
    violations = _violations()
    if not violations:
        return
    lines = [
        f"{p.relative_to(_REPO)}:{n}: names {flag!r} alongside --no-baseline "
        f"(rejected -- exit 64, see no_baseline_rulings._UNSUPPORTED_OPTIONS): "
        f"{text!r}"
        for p, n, flag, text in violations
    ]
    pytest.fail(
        "compare --no-baseline example(s) use a flag that path rejects:\n"
        + "\n".join(lines)
    )


def test_unsupported_options_table_is_non_empty() -> None:
    """A negative control for the scan above: if the source-of-truth table
    were ever accidentally emptied, the positive test above would pass
    vacuously (scanning for zero flags always finds zero hits) without
    actually checking anything. Pins that the table this test drives off
    keeps naming real, rejected flags."""
    assert len(_UNSUPPORTED_SPELLINGS) >= 20
    assert "--abi3" in _UNSUPPORTED_SPELLINGS
    assert "--budget" in _UNSUPPORTED_SPELLINGS
    assert "--since" in _UNSUPPORTED_SPELLINGS

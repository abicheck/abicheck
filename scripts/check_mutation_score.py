#!/usr/bin/env python3
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

"""Mutation-score gate — the direct measure of whether tests *verify* or merely
*execute* the detector core.

Coverage measures reach; a surviving mutant is a line that runs but is not
checked by any assertion. ``mutmut`` mutates the modules listed under
``[tool.mutmut]`` in pyproject.toml and re-runs the suite.

Three gates, in increasing order of how much they need a baseline:

``--diff-scoped``
    **Absolute, needs no baseline.** Any survivor in a function this branch
    touched fails. This is the gate that makes mutation testing useful on a
    PR: a global count can stay flat while a newly-weakened function quietly
    accumulates survivors and an unrelated module's improvement pays for it.

``--baseline-file`` (per-module)
    Survivors are attributed to their source module, and *each module* is
    compared to its own recorded number. A module going 3 -> 5 fails even if
    the repository total went down.

``--baseline`` / :data:`SURVIVOR_BASELINE` (global total)
    The original whole-repository drift check, kept for continuity.

Regardless of gate, an *unresolved* run (timeout / suspicious / no-tests /
segfault / interrupted) is a failed measurement, never a clean zero.

Usage::

    # CI (scheduled): full run, write/refresh the per-module baseline
    python scripts/check_mutation_score.py --run --write-baseline

    # CI (PR touching the detector core): only gate what this branch changed
    python scripts/check_mutation_score.py --run --diff-scoped --base-ref origin/main

    # Check an existing run's output without re-running mutmut
    python scripts/check_mutation_score.py --results-file mutmut-results.txt

Why the parsing is careful (a real defect this gate had): ``mutmut run``
re-renders its progress summary continuously, starting at ``🙁 0``, and those
renders survive into piped CI output. Reading the *first* ``🙁 <n>`` — which a
plain ``re.search`` over run-then-results text does — reported **0 survivors
for a run that genuinely had 4**, reproduced against mutmut 3.7.0. Had
``SURVIVOR_BASELINE`` been set to ``0`` as intended, the gate would have passed
green forever. Survivor counting now comes from the per-mutant ``mutmut
results`` listing, with ``mutants/mutmut-cicd-stats.json`` as the completeness
witness; see ``scripts/mutation_results.py``.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mutation_results import (  # noqa: E402
    # Re-exported for tests/test_mutation_score_gate.py, which reads it as
    # `gate.MODULE_SCOPE`; `as` keeps `ruff --fix` from stripping it as unused.
    MODULE_SCOPE as MODULE_SCOPE,
    MutantRecord,
    count_unresolved,
    functions_covering_lines,
    load_cicd_stats,
    parse_mutant_records,
    parse_survivors,
    summary_run_is_complete,
    survivors_by_module,
)

__all__ = [
    "SURVIVOR_BASELINE",
    "count_unresolved",
    "load_baseline",
    "main",
    "parse_survivors",
    "render_baseline",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Global-total baseline, kept for continuity with the original gate. ``None``
#: means "not established" — report-only. The per-module baseline file
#: (``--baseline-file``) is the preferred gate; see the module docstring.
SURVIVOR_BASELINE: int | None = None

#: Default per-module baseline, committed alongside the code.
DEFAULT_BASELINE_FILE = REPO_ROOT / "mutation-baseline.json"

# Bounded by, but deliberately below, the 355-minute GitHub Actions job
# limit in mutation.yml. The subprocess cap formerly stayed at 7,200
# seconds, silently aborting the run at 120 minutes even after the job
# itself was given more time -- but setting it to *exactly* the job's own
# ceiling reintroduces a different version of the same failure: the job's
# checkout/dependency-install/parser-verification steps run *before* this
# subprocess call and its own "Save mutmut results"/"Upload mutmut results"
# steps run *after*, so a subprocess timeout equal to the full job budget
# leaves no room for any of that -- GitHub kills the whole job at its own
# timeout mid-subprocess, before the surrounding steps can produce a
# receipt or upload anything, which is exactly the "cancelled, no receipt"
# failure this constant exists to prevent (Codex review). 10 minutes of
# headroom (comfortably more than the ~1 minute those surrounding steps
# have taken in practice) keeps the cap close to the job's real ceiling
# without eating into it.
MUTMUT_RUN_TIMEOUT_SECONDS = 20_700

#: ``@@ -old,cnt +new,cnt @@`` — we only need the new-side range.
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

#: ``Binary files a/<path> and b/<path> differ`` — GNU diffutils' own binary
#: marker, emitted by plain ``diff``/``diff -u`` (not just ``git diff``) with
#: no ``diff --git`` header at all. The two path forms it's used against
#: (git's ``a/``/``b/``-prefixed style, and diffutils' own bare-path style
#: from ``diff file1 file2``) are both matched, greedily-but-anchored so a
#: literal " and " inside a filename doesn't split it wrong more often than
#: necessary — deliberately permissive, since over-matching here only ever
#: widens what `diff_lacks_git_headers_for_its_hunks` treats as needing a
#: header, never narrows it.
_BINARY_MARKER = re.compile(r"^Binary files (.+) and (.+) differ$")

#: ``Only in <dir>: <name>`` — GNU diffutils' own marker for a one-sided file
#: under recursive comparison (``diff -r``/``diff -ur dir1 dir2``), naming a
#: file present on only one side with no hunk, no binary marker, and no
#: ``diff --git`` header at all.
_ONLY_IN_MARKER = re.compile(r"^Only in (.+): (.+)$")

#: Line-start prefixes for git's own per-entry metadata — present only
#: between a ``diff --git`` line and that entry's ``--- ``/``+++ ``/hunk
#: content, never meaningful on their own and never carrying a path this
#: module needs to resolve (the owning ``diff --git`` header already named
#: it). Recognized by `diff_has_unrecognized_content` purely so a real git
#: diff entry carrying one of these doesn't itself read as "unrecognized".
_GIT_ENTRY_METADATA_PREFIXES = (
    "index ",
    "old mode ",
    "new mode ",
    "deleted file mode ",
    "new file mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
)

#: A hunk *body* line's leading character — context, addition, removal, or
#: the "\\ No newline at end of file" marker. Only trusted while a hunk is
#: actually open (`diff_has_unrecognized_content` tracks that itself); an
#: empty line is never one of these, since a real diff always carries at
#: least the one prefix character even for a blank context/added/removed
#: source line.
_HUNK_BODY_PREFIXES = (" ", "+", "-", "\\")

#: ``diff --git a/<path> b/<path>`` — present on *every* diff entry regardless
#: of what follows (a hunk, a binary-file marker, a rename with no content
#: change, a mode-only change). Deliberately not anchored past the two
#: capture groups: an unusual filename (spaces, a literal `` b/`` substring)
#: can defeat this the same way it can any line-based diff parser, and
#: over-matching here only ever makes `diff_touches_outside_only_mutate`
#: *more* conservative, never less — the safe direction to err in.
_DIFF_GIT_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")

PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


# ---------------------------------------------------------------------------
# Run-scoping — restrict *which mutants get test-executed*, not just gated
# ---------------------------------------------------------------------------
#
# `mutmut run` always *generates* mutants for every file `[tool.mutmut]`'s
# `only_mutate` names (cheap: an AST pass per file) — that part cannot be
# scoped per invocation, since mutmut reads its config fresh from
# pyproject.toml on every run and has no CLI/env override for it. But the
# *expensive* part — re-running the test suite once per mutant — is scoped by
# `mutmut run`'s own optional positional `MUTANT_NAMES` argument: verified
# directly against mutmut 3.7.0's source
# (`collect_source_file_mutation_data`), a name there is matched against every
# mutant key via `fnmatch`, and the tests actually executed
# (`tests_for_mutant_names(mutant_names)`) are filtered to that set — nothing
# to do with `mutmut show`/`mutmut run <id>` "re-run one already-generated
# mutant", which is a different, narrower use of the same argument covered
# in mutmut's own docs. This is what lets a PR that only touched
# `diff_symbols.py` skip paying for `diff_types.py`/`checker_policy.py`/…'s
# entire test-suite-per-mutant cost, without touching `only_mutate` itself.
#
# Deliberately conservative: this only ever *narrows* which mutants get
# tested, and only when every touched, `only_mutate`-scoped module can be
# named — any uncertainty (can't read the diff, can't read pyproject.toml,
# every module touched) falls back to the unscoped, unconditionally-correct
# full run rather than guessing.


def load_only_mutate_globs(pyproject_path: Path | None = None) -> list[str] | None:
    """``[tool.mutmut].only_mutate`` from pyproject.toml, or ``None`` if unreadable.

    *pyproject_path* defaults to ``REPO_ROOT / "pyproject.toml"`` resolved at
    *call* time (not a frozen default argument) so a test that monkeypatches
    ``gate.REPO_ROOT`` — the established pattern in
    tests/test_mutation_score_gate.py — affects this too.

    A local import: this module runs on any supported Python (3.10+), but the
    ``--run`` scoping path this feeds only ever executes inside the mutation
    CI lane, which pins Python 3.13 (``tomllib`` is stdlib since 3.11). A
    stale-tomllib environment simply gets no scoping — the safe direction to
    fail in — rather than an import error on every other invocation of this
    script.
    """
    try:
        import tomllib
    except ImportError:
        return None
    path = (
        pyproject_path if pyproject_path is not None else REPO_ROOT / "pyproject.toml"
    )
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    only_mutate = doc.get("tool", {}).get("mutmut", {}).get("only_mutate")
    if not isinstance(only_mutate, list) or not all(
        isinstance(p, str) for p in only_mutate
    ):
        return None
    return only_mutate


def mutant_scope_pattern(module_path: str) -> str:
    """``"abicheck/diff_symbols.py"`` -> ``"abicheck.diff_symbols.*"``.

    Matches mutmut's own dotted-module mutant-key prefix (`mutation_results.
    parse_mutant_module_and_function`'s ``pkg.gamma`` half of ``pkg.gamma.
    xǁWidgetǁarea__mutmut_1``) — an ``fnmatch`` pattern against the full key,
    so the trailing ``.*`` covers every function, nested class, and mutant
    number in that one module without needing to know any of them in advance.
    """
    dotted = module_path[: -len(".py")] if module_path.endswith(".py") else module_path
    return dotted.replace("/", ".") + ".*"


def diff_touched_only_mutate_modules(
    diff_text: str, only_mutate: list[str]
) -> set[str]:
    """Which ``only_mutate`` modules this diff added, modified, or removed lines in.

    File granularity only — `only_mutate` itself has no finer scope, so there
    is nothing function-level to gain here that `check_diff_scoped`'s
    post-run gating does not already do. Both added/modified (new-side) and
    removed (old-side) lines count: a module whose only edit in this diff is
    a deleted guard still needs its mutants test-executed, or a survivor
    `check_diff_scoped` would have reported is silently never measured.
    """
    only_mutate_set = set(only_mutate)
    touched = set(parse_changed_lines(diff_text)) | set(parse_removed_lines(diff_text))
    return touched & only_mutate_set


def diff_touched_paths(diff_text: str) -> set[str]:
    """Every path named by a ``diff --git a/... b/...`` header, either side.

    The authoritative "did this diff touch this path at all" — unlike
    `parse_changed_lines`/`parse_removed_lines` (built on `_hunks()`, i.e.
    on ``@@`` hunks), this also sees a binary-file diff, a pure rename with
    no content change, and a mode-only change, none of which produce a
    hunk at all. A diff-scoping safety check built on the hunk-based
    readers alone stayed blind to exactly those three shapes — reported
    against the fourth revision of `diff_touches_outside_only_mutate`,
    which by then had *no allowlist left to narrow* (Codex review, PR
    #877): the gap was never in what counted as "outside `only_mutate`",
    it was in what this function's *inputs* could see in the first place.
    """
    touched: set[str] = set()
    for line in diff_text.splitlines():
        m = _DIFF_GIT_HEADER.match(line)
        if m:
            touched.add(m.group(1))
            touched.add(m.group(2))
    return touched


def diff_has_unparseable_git_header(diff_text: str) -> bool:
    """Any ``diff --git`` line `_DIFF_GIT_HEADER`'s plain ``a/... b/...``
    form can't parse.

    Most commonly a git-quoted path: `core.quotepath` (on by default) makes
    git wrap the *whole* header in double quotes and C-style-escape it the
    moment a path needs it — a space, a non-ASCII byte, an embedded quote,
    backslash, tab, or control character — e.g. ``diff --git "a/caf\\303\\251"
    "b/caf\\303\\251"``. `diff_touched_paths` silently drops such an entry
    (Codex review, PR #877, fifth round on this same predicate — the fourth
    round's fix moved detection off `_hunks()` and onto `diff --git`
    headers, and this is a gap in *that* parser rather than a case it
    already covered). Decoding git's own quoting/escaping correctly is a
    real, if narrow, parser in its own right; conservatively treating any
    unparseable header as "touches something outside `only_mutate`" is
    simpler and can only ever make scoping more cautious, never less.
    """
    return any(
        line.startswith("diff --git ") and not _DIFF_GIT_HEADER.match(line)
        for line in diff_text.splitlines()
    )


def _binary_marker_paths(diff_text: str) -> set[str]:
    """Paths named by a GNU-diffutils/git ``Binary files ... differ`` line.

    Unlike a rename (``rename from``/``rename to``) or a mode-only change
    (``old mode``/``new mode``), which are pure ``git diff`` vocabulary that
    never appears without a preceding ``diff --git`` header, this one line
    shape is also emitted by plain ``diff``/``diff -u`` comparing two binary
    files directly — a real, reachable headerless source, not a hypothetical
    one (Codex review, PR #877, eighth round on this same predicate).
    """
    paths: set[str] = set()
    for line in diff_text.splitlines():
        m = _BINARY_MARKER.match(line)
        if not m:
            continue
        for group in m.group(1), m.group(2):
            paths.add(group[2:] if group.startswith(("a/", "b/")) else group)
    return paths


def _only_in_marker_paths(diff_text: str) -> set[str]:
    """Paths named by a GNU-diffutils ``Only in <dir>: <name>`` marker.

    ``diff -r``/``diff -ur dir1 dir2`` reports a file present on only one
    side this way — no hunk, no binary marker, no ``diff --git`` header —
    so it was invisible to every path source `diff_lacks_git_headers_for_
    its_hunks` checked before this one (Codex review, PR #877, tenth round
    on this same predicate). The directory and name combine into one path
    (``examples`` + ``oracle.json`` -> ``examples/oracle.json``), stripping
    a leading ``a/``/``b/`` from the directory half if git's own recursive
    ``diff --git ... -r`` invocation put one there.
    """
    paths: set[str] = set()
    for line in diff_text.splitlines():
        m = _ONLY_IN_MARKER.match(line)
        if not m:
            continue
        directory, name = m.group(1), m.group(2)
        if directory.startswith(("a/", "b/")):
            directory = directory[2:]
        paths.add(f"{directory}/{name}" if directory else name)
    return paths


def _hunk_file_targets(diff_text: str) -> set[str]:
    """Every ``--- ``/``+++ `` hunk-header target this diff names, whether or
    not it carries git's own ``a/``/``b/`` prefix.

    `_hunks()` deliberately requires the ``a/``/``b/`` prefix before trusting
    a ``--- ``/``+++ `` line as naming a real path — correct for its own
    job (feeding `parse_changed_lines`/`parse_removed_lines`'s per-line
    attribution, where a bare, unprefixed target would be genuinely
    ambiguous to resolve against the repo tree) — but that means a bare-path
    unified-diff header (``--- file.py`` / ``+++ file.py``, with no ``a/``/
    ``b/`` at all — real output of plain ``diff -u file1 file2``, or of
    ``git diff --no-prefix``) yields *nothing* from `_hunks()`: both sides
    read `None`, and the line-635 guard skips the hunk entirely (Codex
    review, PR #877, ninth round on this same predicate — the eighth
    round's fix covered a headerless *binary* marker; this is a headerless
    *text* hunk whose header shape itself, not its absence, is what defeats
    `_hunks()`). This function exists purely to answer "is there a file
    identity here at all" for `diff_lacks_git_headers_for_its_hunks`'s own
    safety comparison, not to resolve one precisely, so it accepts the bare
    form too — stripping a leading ``a/``/``b/`` when present, keeping the
    bare path otherwise, and (matching real GNU diff output) trimming a
    trailing ``\\t<timestamp>`` some ``--- ``/``+++ `` lines carry.
    """
    old_target: str | None = None
    new_target: str | None = None
    targets: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            old_target = new_target = None
            continue
        if line.startswith("--- "):
            raw = line[4:].split("\t", 1)[0].strip()
            old_target = None if raw == "/dev/null" else raw
            continue
        if line.startswith("+++ "):
            raw = line[4:].split("\t", 1)[0].strip()
            new_target = None if raw == "/dev/null" else raw
            continue
        if old_target is None and new_target is None:
            continue
        if _HUNK.match(line):
            for target in (old_target, new_target):
                if target is not None:
                    prefix = target[:2]
                    targets.add(target[2:] if prefix in ("a/", "b/") else target)
    return targets


def diff_has_unrecognized_content(diff_text: str) -> bool:
    """True when the diff has a non-blank line that isn't one of the content
    shapes this module already understands.

    Ten rounds of review each found one more real diff-tool output shape
    this predicate's path-comparison approach didn't have a dedicated
    reader for — a git-quoted header, a headerless hunk, a headerless
    binary marker, a bare-path header, a recursive-diff ``Only in`` marker,
    and (the finding that prompted this function) GNU diffutils' ``-q``/
    ``--brief`` ``Files X and Y differ`` form. Each fix so far added one
    more marker-specific path extractor to the union
    `diff_lacks_git_headers_for_its_hunks` compares against
    `diff_touched_paths()` — sound for the shape it targets, but leaves an
    *eleventh*, still-undiscovered shape exactly as invisible as the tenth
    was before this round.

    This function closes that class generically instead of naming a
    twelfth marker: rather than asking "does this line carry a path we can
    extract", it asks "is this line one of the small, closed set of shapes
    a real ``diff``/``git diff`` invocation can ever emit at all" — a
    ``diff --git`` header, one of git's own per-entry metadata lines
    (`_GIT_ENTRY_METADATA_PREFIXES`), a ``--- ``/``+++ `` header, a hunk
    header or body line, or one of the two marker shapes this module
    already extracts a path from (binary, ``Only in``). Deliberately
    *not* recognizing GNU diffutils' ``-q``/``--brief`` ``Files X and Y
    differ`` form (the finding that prompted writing this function) —
    unlike the binary/``Only in`` markers, nothing extracts a path from it
    into `diff_lacks_git_headers_for_its_hunks`'s own comparison, so
    treating it as "recognized" here would make the line invisible to
    *both* checks at once rather than caught by this one. A line that
    fits none of the recognized shapes — including a marker format no
    round of review has reported yet — is flagged directly, with no path
    extraction needed to prove the diff unsafe to scope. This still isn't
    a full grammar validator (it doesn't check that a ``--- ``/``+++ ``/
    marker's own path matches its governing ``diff --git`` header —
    `diff_lacks_git_headers_for_its_hunks`'s existing path-set comparison,
    kept unchanged alongside this function, is what catches that), but it
    does mean a wholly new content shape disables scoping on sight rather
    than needing its own review round and its own extractor before it's
    caught — provided nobody ever adds it to this function's own
    recognized set without also giving it a path extractor, exactly the
    mistake this docstring exists to warn the next round away from.

    That mistake is exactly what the first version of this function made
    for `_GIT_ENTRY_METADATA_PREFIXES`: those lines carry no path extractor
    anywhere in this module (the owning ``diff --git`` header already
    names the path; a rename/mode/index line never needs its own), so
    recognizing one unconditionally — in *any* parser state, not only
    immediately after the ``diff --git`` line that makes it legitimate —
    left a headerless ``rename from``/``rename to`` pair (pasted after an
    already-open, properly-headed hunk, say) invisible to *both* checks at
    once: not path-extracted, and now also not flagged as unrecognized
    (Codex review, PR #877, thirteenth round on this same predicate — the
    same class of self-inflicted gap the eleventh round's fix already
    warned future rounds away from, reproduced anyway one round later, this
    time by this function's own first draft rather than by the code it was
    meant to guard). Fixed with an explicit ``in_entry_metadata_zone``
    flag: metadata prefixes are only recognized immediately after a
    ``diff --git`` line (or after another recognized metadata line in that
    same run), and the zone closes — same as `in_hunk` — the moment any
    other recognized content type appears, so a metadata-shaped line
    anywhere else falls straight through to the unrecognized-content
    fallback instead of being waved through by a prefix match alone.
    """
    in_hunk = False
    in_entry_metadata_zone = False
    for line in diff_text.splitlines():
        if not line:
            return True
        if line.startswith("diff --git "):
            in_hunk = False
            in_entry_metadata_zone = True
            continue
        if in_entry_metadata_zone and line.startswith(_GIT_ENTRY_METADATA_PREFIXES):
            continue
        if line.startswith(("--- ", "+++ ")):
            in_hunk = False
            in_entry_metadata_zone = False
            continue
        if _HUNK.match(line):
            in_hunk = True
            in_entry_metadata_zone = False
            continue
        if _BINARY_MARKER.match(line) or _ONLY_IN_MARKER.match(line):
            in_hunk = False
            in_entry_metadata_zone = False
            continue
        if in_hunk and line.startswith(_HUNK_BODY_PREFIXES):
            continue
        return True
    return False


def diff_lacks_git_headers_for_its_hunks(diff_text: str) -> bool:
    """True when some hunk's (or binary marker's) file isn't named by any
    ``diff --git`` header.

    A "headerless" unified diff — e.g. produced by plain ``diff -u`` rather
    than ``git diff``, or a hand-assembled/stripped patch file passed via
    ``--diff-file`` — still parses fine under `_hunk_file_targets()` (which
    keys off the ``--- ``/``+++ `` file markers, not ``diff --git``), so
    `diff_touched_only_mutate_modules` can still name a touched
    ``only_mutate`` module from it. But `diff_touched_paths` (and
    `diff_has_unparseable_git_header`) can only ever see a path named by a
    ``diff --git`` header. Checking merely "does *any* `diff --git` line
    exist anywhere in the text" (the first version of this check) is not
    enough: a diff that concatenates one ordinary `diff --git`-headed entry
    with a second, headerless unified-diff section (e.g. two files pasted
    together, or a hand-assembled `--diff-file`) has a header *somewhere*,
    so that version read the whole diff as headered and never looked at
    whether the headerless section's own file was actually covered by one
    (Codex review, PR #877, seventh round on this same predicate — the
    sixth round's fix closed a diff with *zero* headers; this is a diff
    with *some*, just not covering every hunk). Fixed by comparing the
    file set each reader actually sees: every path a real hunk names must
    also appear in `diff_touched_paths()`'s header-derived set, or a real
    hunk exists that the header-based reader — and therefore
    `diff_touches_outside_only_mutate` — cannot see at all. A hunk isn't
    the only content shape carrying file identity, though: a headerless
    binary-file diff has no ``@@`` hunk either, so `_binary_marker_paths`
    is folded into the same comparison (eighth round); nor is git's own
    ``a/``/``b/``-prefixed hunk-header spelling the only shape a real hunk
    can carry, so this reads targets via `_hunk_file_targets` rather than
    `_hunks()` directly, to also catch a bare-path header (ninth round);
    nor is a hunk or a binary marker the only shape a one-sided file can
    take at all — GNU diffutils' recursive ``diff -r``/``diff -ur`` mode
    reports a file present on only one side as a hunkless, markerless
    ``Only in <dir>: <name>`` line, folded in via `_only_in_marker_paths`
    (tenth round).

    Ten rounds of enumerating one more content shape at a time (an
    eleventh — GNU diffutils' ``-q``/``--brief`` ``Files X and Y differ``
    form — arrived the same session this docstring was last revised) is
    what motivated `diff_has_unrecognized_content`: rather than a
    thirteenth marker-specific path extractor, that function closes the
    *class* by flagging any line that isn't one of the small, closed set
    of shapes a real diff tool can emit at all, catching a still-
    undiscovered twelfth shape by construction instead of needing its own
    review round first. It's checked here alongside the path-set
    comparison — not in place of it, since recognizing a shape and
    verifying its path against the diff's own headers are different
    questions (see that function's own docstring for why neither
    subsumes the other).
    """
    hunk_paths = _hunk_file_targets(diff_text)
    hunk_paths |= _binary_marker_paths(diff_text)
    hunk_paths |= _only_in_marker_paths(diff_text)
    if hunk_paths and not hunk_paths <= diff_touched_paths(diff_text):
        return True
    return diff_has_unrecognized_content(diff_text)


def diff_touches_outside_only_mutate(diff_text: str, only_mutate: list[str]) -> bool:
    """Any path this diff touches — of *any* kind — that isn't itself in ``only_mutate``.

    Five widening review rounds on this same predicate (Codex + CodeRabbit,
    PR #877) each found the previous version still let something through
    that could change an *untouched* module's behavior under mutation
    without touching that module's own file: "any `tests/` path" (a shared
    fixture), then "any `.py` file, of any kind" (a shared production
    helper an untouched module imports), then a non-Python `also_copy`
    input (`examples/**/*.json` and the like — read as fixture/oracle data
    by tests that exercise mutated modules), then a class of change
    invisible to the *diff-line* readers entirely (a binary-file diff, a
    pure rename, or a mode-only change, none of which contain a ``@@``
    hunk — fixed by reading `diff --git` headers directly instead), and
    then a header this new reader still couldn't parse: a git-quoted
    path (`diff --git "a/..." "b/..."`, the form `core.quotepath`
    produces for a non-ASCII/space/special-character path), silently
    dropped by `diff_touched_paths` rather than raising, and finally a
    diff with *no* `diff --git` headers at all (a headerless unified diff
    from plain `diff -u` or a hand-assembled `--diff-file`), which is
    invisible to the header-based reader regardless of content even
    though the hunk-based reader still sees it fine, and — a diff mixing
    an ordinary `diff --git`-headed entry with a *second*, headerless
    section, whose own file a naive "does any header exist at all" check
    still misses (`diff_lacks_git_headers_for_its_hunks`, which instead
    compares the two readers' own file sets directly). The first three
    rounds each narrowed what counted as "outside `only_mutate`"; the
    last four each showed that no such narrowing could have helped, since
    the affected path was never in the detected set at all — fixed at the
    detection layer each time: first by moving off `_hunks()` onto
    `diff --git` headers (`diff_touched_paths`), then by treating any
    header that reader still can't parse as itself a signal to disable
    scoping (`diff_has_unparseable_git_header`) rather than writing a
    git-quote/octal-escape decoder, then by comparing what the hunk-based
    and header-based readers each see and falling back whenever a hunk's
    file isn't covered by any header at all — whether because the diff
    has none, or because one section of it does and another doesn't.

    Every mutant, however scoped, is tested against the diff's *entire*
    current tree — mutmut's own `copy_src_dir`/`copy_also_copy_files` copy
    every source file *and* every `also_copy` path (which, per
    pyproject.toml's own comment, is deliberately generous rather than
    minimal: `docs`, `examples`, `scripts`, `.github`, lockfiles, ...) fresh
    from what's checked out. Given that surface, trying to name every
    input a test *might* read as fixture/oracle data and enumerate it as
    "safe" is exactly the reactive whack-a-mole this repository's own
    "Known gaps" convention (AGENTS.md) warns against repeating. The only
    version of this check immune to a further round is the one with no
    allowlist at all: if literally nothing outside `only_mutate` changed —
    checked the one way that can't miss a hunkless diff entry — no other
    path's content, read by any test in any way this function does not
    have to know about, differs from what any other measurement (a prior
    baseline, a full run) already reflects.

    Costs real applicability: this repo's own changelog-fragment
    convention (a `changelog.d/*.md` addition, required for most
    `abicheck/**/*.py` changes) means a real `fix:`/`perf:`/`security:` PR
    touching one `only_mutate` module will usually also touch a changelog
    fragment, and this predicate treats that the same as anything else —
    disabling scoping for it. A reviewed, narrow allowlist for a handful
    of paths verified never to be read as test fixture/oracle content
    (`changelog.d/`, say) is a real, separate improvement, not attempted
    here after four consecutive rounds on the same check.
    """
    if diff_has_unparseable_git_header(diff_text):
        return True
    if diff_lacks_git_headers_for_its_hunks(diff_text):
        return True
    touched = diff_touched_paths(diff_text)
    only_mutate_set = set(only_mutate)
    return any(p not in only_mutate_set for p in touched)


def mutant_run_scope(
    diff_text: str | None, only_mutate: list[str] | None
) -> list[str] | None:
    """``MUTANT_NAMES`` patterns to pass to ``mutmut run``, or ``None`` for "full run".

    ``None`` — not scoping — is the answer whenever scoping cannot be proven
    safe: no diff, no readable ``only_mutate``, no ``only_mutate`` module
    touched (the diff may still be real — e.g. only this lane's own
    infrastructure changed — just not one this function can attribute to a
    mutated module), every ``only_mutate`` module touched (scoping would
    filter nothing, so it's not worth the extra mutmut invocation shape),
    or the diff touches *anything at all* outside ``only_mutate`` (see
    `diff_touches_outside_only_mutate`).
    """
    if diff_text is None or not only_mutate:
        return None
    if diff_touches_outside_only_mutate(diff_text, only_mutate):
        return None
    touched = diff_touched_only_mutate_modules(diff_text, only_mutate)
    if not touched or touched >= set(only_mutate):
        return None
    return sorted(mutant_scope_pattern(m) for m in touched)


def _run_mutmut(cmd: list[str]) -> tuple[str, int]:
    """Run a mutmut subcommand, returning ``(output, returncode)``.

    The return code matters for ``mutmut run`` specifically, and only for
    telling "this run aborted" from "this run completed": verified against
    mutmut 3.7.0, a completed run exits **0 even when mutants survived**, and a
    configuration/collection abort exits nonzero. So a nonzero exit here is
    never "there are survivors" — it is "no measurement happened".

    That distinction is load-bearing once ``mutants/`` is a restored CI cache
    (see mutation.yml): a run that aborts leaves the *previous* commit's result
    database in place, which would otherwise satisfy the completeness witness
    and let the gate pass on stale results (Codex review).
    """
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=MUTMUT_RUN_TIMEOUT_SECONDS
    )
    return proc.stdout + proc.stderr, proc.returncode


# ---------------------------------------------------------------------------
# Baseline file
# ---------------------------------------------------------------------------


def render_baseline(records: list[MutantRecord]) -> dict[str, object]:
    """Build the committed baseline document from a measurement.

    Surviving mutant *keys* are recorded for diagnostics only and are
    deliberately **not** gated on. mutmut numbers mutants per function
    (``…__mutmut_3``), so editing a function renumbers its keys — gating on key
    identity would fire on every ordinary refactor while proving nothing. The
    per-module *count* is the stable signal; the keys are there so a reviewer
    can see which mutants were accepted.
    """
    by_module = survivors_by_module(records)
    functions: dict[str, dict[str, int]] = {}
    for record in records:
        if record.is_survivor:
            functions.setdefault(record.module_path, {}).setdefault(record.function, 0)
            functions[record.module_path][record.function] += 1
    return {
        "_comment": (
            "Per-module surviving-mutant baseline. Regenerate with "
            "`python scripts/check_mutation_score.py --run --write-baseline`. "
            "'keys' is diagnostic only — the gate compares counts, because "
            "mutmut renumbers a function's mutants whenever it is edited."
        ),
        "total_survivors": sum(len(v) for v in by_module.values()),
        "modules": {
            module: {
                "survivors": len(keys),
                "keys": keys,
                # Counts, rather than mutant keys: mutmut renumbers a function
                # when it is edited. This is the baseline needed to score a
                # changed legacy function for *new* survivors only.
                "functions": functions.get(module, {}),
            }
            for module, keys in by_module.items()
        },
    }


def load_baseline(path: Path) -> dict[str, int] | None:
    """Read ``{module_path: survivor_count}``, or ``None`` if absent/invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    modules = doc.get("modules") if isinstance(doc, dict) else None
    if not isinstance(modules, dict):
        return None
    out: dict[str, int] = {}
    for module, entry in modules.items():
        if isinstance(entry, dict) and isinstance(entry.get("survivors"), int):
            out[module] = entry["survivors"]
        elif isinstance(entry, int):
            out[module] = entry
    return out


def load_function_baseline(path: Path) -> dict[tuple[str, str], int]:
    """Read per-function survivor counts from a current baseline document.

    Old baselines intentionally yield no entries: treating their module total
    as a function total would hide a regression in one function paid for by an
    improvement in another.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    modules = doc.get("modules") if isinstance(doc, dict) else None
    if not isinstance(modules, dict):
        return {}
    out: dict[tuple[str, str], int] = {}
    for module, entry in modules.items():
        funcs = entry.get("functions") if isinstance(entry, dict) else None
        if not isinstance(funcs, dict):
            continue
        for function, survivors in funcs.items():
            if isinstance(function, str) and isinstance(survivors, int):
                out[(module, function)] = survivors
    return out


def check_per_module(
    records: list[MutantRecord],
    baseline: dict[str, int],
    scope_modules: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Modules whose survivor count rose above their own recorded number.

    Returns ``(failures, skipped)``. ``skipped`` is every baseline module this
    call declined to compare because it was outside ``scope_modules`` — never
    silently folded into "no failures".

    A scoped run's own mutant *test-execution* never touches a module outside
    ``scope_modules`` (see ``--scope-run-to-diff``'s own docstring): every
    mutant there reads ``not checked``, so `survivors_by_module` reports zero
    survivors for it regardless of the module's true state. Comparing that
    unconditionally against `baseline` would read as "still within baseline"
    for a module this run never measured at all — a false "OK" the caller
    could print unchallenged, exactly the "false 'safe to scope' is a
    correctness bug" failure mode this file's own diff-parsing predicates are
    built to avoid elsewhere (Codex review: modules in `only_mutate` can
    import each other, e.g. ``diff_types.py`` imports ``diff_symbols``, so a
    diff touching only one can change what an *omitted* module's mutants
    would report without ever touching a path the scoping predicate would
    reject). So when `scope_modules` is given and non-empty, comparison is
    restricted to it — mirroring `unresolved_for_gate`'s identical scoping in
    `main()` — and every other baseline module is reported as skipped rather
    than silently scored as unchanged.
    """
    current = {m: len(k) for m, k in survivors_by_module(records).items()}
    universe = set(current) | set(baseline)
    skipped: list[str] = []
    if scope_modules:
        skipped = sorted(m for m in universe if m not in scope_modules)
        universe = {m for m in universe if m in scope_modules}
    failures = []
    for module in sorted(universe):
        now = current.get(module, 0)
        was = baseline.get(module, 0)
        if now > was:
            failures.append(f"  {module}: {was} -> {now} (+{now - was})")
    return failures, skipped


# ---------------------------------------------------------------------------
# Diff-scoped gate
# ---------------------------------------------------------------------------


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Map ``path -> changed line numbers`` from ``git diff --unified=0``.

    New-side numbers only. Removed lines have no new-side number at all and
    are answered separately by `parse_removed_lines`, against the base.
    """
    changed: dict[str, set[int]] = {}
    for _old_path, new_path, _old, new in _hunks(diff_text):
        start, count = new
        if count and new_path is not None:
            changed.setdefault(new_path, set()).update(range(start, start + count))
    return changed


def parse_removed_lines(diff_text: str) -> dict[str, tuple[str, set[int]]]:
    """Map ``key path -> (base-side path, base-side line numbers removed)``.

    Deleting a guard is one of the most direct ways to weaken a detector, so
    the removed lines have to reach the gate somehow — but they exist only in
    the base, and every attempt to express them as new-side numbers is a
    guess. The previous one (attribute the new-side anchor and its successor)
    was wrong in both directions: for a deleted *function* the successor is
    the first line of the next, surviving function, so the gate failed on
    pre-existing survivors in code the branch never touched (Codex review).

    Base-side numbers are not a guess — they are what the hunk header says —
    and resolving them against the base file's own AST answers exactly "which
    function did this line live in".

    Two paths, because a hunk can name two. The *base* path is the one to
    read the removed lines out of (`--- a/...`, which git still emits for a
    rename or a whole-file deletion, where the new side is a different name
    or `/dev/null`). The *key* path is the one to attribute the result to —
    the new name, because that is what a mutant key carries. Conflating them
    meant a renamed module's removals were looked up under a name the base
    does not have, answered `None`, and silently dropped (Codex, CodeRabbit).
    """
    removed: dict[str, tuple[str, set[int]]] = {}
    for old_path, new_path, old, _new in _hunks(diff_text):
        start, count = old
        if not count or old_path is None:
            continue
        # A deleted file has no new name; key it by its old one. Nothing in
        # the new tree carries mutants for it, so this normally matches
        # nothing — but a `--results-file` saved from an earlier revision can
        # still name it, and a whole deleted module having no entry at all is
        # the one shape nothing downstream can notice (CodeRabbit).
        key = new_path or old_path
        removed.setdefault(key, (old_path, set()))[1].update(
            range(start, start + count)
        )
    return removed


def pure_deletion_paths(diff_text: str) -> set[str]:
    """Key paths with at least one hunk that removes lines and adds none.

    The narrower question behind "can an unresolvable base hide a failure".
    A *modification* removes and adds in the same hunk, so its new side is
    already attributed and the removed side adds nothing the gate needs. Only
    a hunk with no new side at all has its evidence exclusively in the base.
    """
    return {
        (new_path or old_path)
        for old_path, new_path, old, new in _hunks(diff_text)
        if old[1] and not new[1] and (new_path or old_path)
    }


def unresolved_removals(
    removed: dict[str, tuple[str, set[int]]],
    read_base: Callable[[str], str | None] | None,
) -> set[str]:
    """Key paths whose removed lines could not be read out of the base.

    Per path, not "is there a reader at all". A reader can exist and still
    answer `None` for one path — a file absent from the merge base, an
    unreadable blob — and keying the risk check on the reader alone let
    exactly that case through as a silent skip (Codex, CodeRabbit).
    """
    return {
        key
        for key, (base_path, _lines) in removed.items()
        if key.endswith(".py") and (read_base is None or read_base(base_path) is None)
    }


def _hunks(
    diff_text: str,
) -> Iterator[tuple[str | None, str | None, tuple[int, int], tuple[int, int]]]:
    """`(old_path, new_path, (old_start, old_count), (new_start, new_count))`.

    Either path is `None` when its side is `/dev/null` — an added file has no
    old side, a deleted file no new side. Both are tracked because the two
    sides answer different questions: the new path is what a mutant key
    carries, the old path is where the removed lines can actually be read.

    A deleted file's `+++ /dev/null` used to leave the *previous* file's name
    in scope, so its `@@ -1,5 +0,0 @@` hunk was recorded against a file the
    branch never touched (CodeRabbit review); resetting on `diff --git` and
    tracking both sides explicitly removes that whole class.
    """
    old_path: str | None = None
    new_path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            old_path = new_path = None
            continue
        if line.startswith("--- "):
            target = line[4:].strip()
            old_path = target[2:] if target.startswith("a/") else None
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            new_path = target[2:] if target.startswith("b/") else None
            continue
        if old_path is None and new_path is None:
            continue
        m = _HUNK.match(line)
        if m:
            yield (
                old_path,
                new_path,
                (int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1),
                (int(m.group(3)), int(m.group(4)) if m.group(4) is not None else 1),
            )


def changed_functions(
    changed: dict[str, set[int]],
    repo_root: Path,
    removed: dict[str, tuple[str, set[int]]] | None = None,
    read_base: Callable[[str], str | None] | None = None,
) -> dict[str, set[str]]:
    """Map ``path -> qualnames of functions this diff touched``.

    Two resolutions, against two different revisions: added and modified lines
    against the working tree, removed lines against *the base*, since that is
    the only revision in which they exist. Which path each is read from and
    which it is filed under can differ — see `parse_removed_lines`. Without
    *read_base*, or for a path the base cannot answer for, the removed half
    is simply not resolved — an honest gap the caller reports through
    `unresolved_removals`, rather than a new-side guess naming the wrong
    function.
    """
    out: dict[str, set[str]] = {}
    for path, lines in changed.items():
        if not path.endswith(".py"):
            continue
        try:
            source = (repo_root / path).read_text(encoding="utf-8")
        except OSError:
            continue
        funcs = functions_covering_lines(source, lines)
        if funcs:
            out[path] = funcs
    if removed and read_base is not None:
        for key_path, (base_path, lines) in removed.items():
            if not key_path.endswith(".py"):
                continue
            base_source = read_base(base_path)
            if base_source is None:
                continue
            funcs = functions_covering_lines(base_source, lines)
            if funcs:
                # Filed under the *new* name: that is what a mutant key
                # carries, even though the lines were read from the old one.
                out.setdefault(key_path, set()).update(funcs)
    return out


def _base_reader(base_ref: str) -> Callable[[str], str | None] | None:
    """A reader for file contents *as of the merge base*, or None.

    The merge base, not `base_ref` itself: `git diff base...HEAD` is defined
    against it, so its line numbers are the ones the removed-side hunk headers
    carry. Reading `base_ref`'s own tip would silently resolve them against a
    different file.
    """
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "merge-base", base_ref, "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    base_sha = proc.stdout.strip()

    @functools.cache
    def _read(path: str) -> str | None:
        shown = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "show", f"{base_sha}:{path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # A path absent from the base is an added file; its lines are all on
        # the new side, so there is nothing here to resolve.
        return shown.stdout if shown.returncode == 0 else None

    return _read


def check_diff_scoped(
    records: list[MutantRecord],
    touched: dict[str, set[str]],
    baseline: dict[tuple[str, str], int] | None = None,
) -> list[str]:
    """Survivors living in a function this branch changed.

    A function recorded in a baseline is delta-scored: existing survivor debt
    must not make every edit impossible, but its count may not rise. Functions
    without a recorded baseline remain absolute. Module-scope edits are scored
    by the per-module drift gate below; mutmut has no module-scope mutant to
    attribute precisely.
    """
    baseline = baseline or {}
    current: dict[tuple[str, str], list[str]] = {}
    for r in records:
        if r.is_survivor and r.function in touched.get(r.module_path, set()):
            current.setdefault((r.module_path, r.function), []).append(r.key)
    failures = []
    for (module, function), keys in sorted(current.items()):
        was = baseline.get((module, function))
        if was is None:
            failures.extend(f"  {module}::{function}  [{key}]" for key in sorted(keys))
        elif len(keys) > was:
            failures.append(
                f"  {module}::{function}: {was} -> {len(keys)} (+{len(keys) - was})"
            )
    return failures


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _gather(
    args: argparse.Namespace, mutant_name_patterns: list[str] | None = None
) -> tuple[str | None, dict[str, int] | None]:
    """Return ``(results_text, cicd_stats)``.

    ``mutmut run``'s own stdout is deliberately **not** folded into the text
    used for counting — see the module docstring. It is still executed (and its
    output shown) under ``--run``; only the *measurement* comes from ``mutmut
    results`` plus the exported stats.

    *mutant_name_patterns*, when given, are passed as ``mutmut run``'s own
    ``MUTANT_NAMES`` positional argument — see ``mutant_run_scope``'s
    docstring for what that actually scopes (test execution, not mutant
    generation) and why that is still the dominant cost.
    """
    if args.results_file:
        if args.results_file == "-":
            return sys.stdin.read(), load_cicd_stats(Path(args.mutants_dir))
        try:
            with open(args.results_file, encoding="utf-8") as fh:
                return fh.read(), load_cicd_stats(Path(args.mutants_dir))
        except OSError as e:
            print(f"ERROR: cannot read --results-file: {e}")
            return None, None

    if shutil.which("mutmut") is None:
        print("mutation-score: mutmut not installed, skipping")
        return None, None

    if args.run:
        run_cmd = ["mutmut", "run", *(mutant_name_patterns or [])]
        if mutant_name_patterns:
            print(
                "mutation-score: running `mutmut run` scoped to "
                f"{len(mutant_name_patterns)} changed module(s) (this is still "
                "slow, just less of it)…"
            )
        else:
            print("mutation-score: running `mutmut run` (this is slow)…")
        run_out, run_rc = _run_mutmut(run_cmd)
        tail = "\n".join(run_out.splitlines()[-5:])
        print(f"mutation-score: mutmut run tail:\n{tail}")
        # Keep the complete diagnostic for the workflow artifact. A nonzero
        # status is an obvious abort, but mutmut can also return zero after a
        # run that measures no mutants; in both cases its final progress line
        # hides the pytest node or internal condition we need to repair.
        diagnostic = Path("mutmut-run-output.txt")
        try:
            diagnostic.write_text(run_out, encoding="utf-8")
        except OSError as e:
            print(f"WARNING: could not save mutmut run output: {e}")
        else:
            print(f"mutation-score: saved full mutmut run output to {diagnostic}")
        if run_rc != 0:
            # Fail here rather than reading results: with a restored cache the
            # results on disk may be a previous commit's, and they would look
            # perfectly complete.
            print(
                f"ERROR: `mutmut run` exited {run_rc} — the run aborted, so any "
                "results on disk are from an earlier run, not this one. Not "
                "reading them."
            )
            return None, None
        _run_mutmut(["mutmut", "export-cicd-stats"])
    results_out, results_rc = _run_mutmut(["mutmut", "results"])
    if results_rc != 0:
        # Its stderr would otherwise be parsed as "no per-mutant lines", and if
        # exported stats happen to exist with total > 0 the completeness check
        # passes and the unparseable output becomes zero survivors — passing
        # even an explicit zero baseline while the stats report survivors
        # (Codex review).
        print(
            f"ERROR: `mutmut results` exited {results_rc} — cannot read the "
            "per-mutant statuses, so no survivor count is trustworthy."
        )
        return None, None
    return results_out, load_cicd_stats(Path(args.mutants_dir))


def _load_diff_text(args: argparse.Namespace) -> tuple[str | None, int | None]:
    """``(diff_text, None)`` on success, ``(None, exit_code)`` on failure.

    Factored out of the diff-scoped gating block so it can also run *before*
    ``--run``, for run-scoping: fetching it once and reusing it there too
    avoids a second ``git diff`` invocation, and — a genuine side benefit,
    not just tidiness — a bad ``--base-ref`` now fails before paying for a
    multi-hour mutmut run instead of after (previously: the diff was only
    ever fetched post-run, so a typo here wasted the entire run before
    reporting the same error it reports now up front).
    """
    if args.diff_file:
        return Path(args.diff_file).read_text(encoding="utf-8"), None
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", "diff", "--unified=0", f"{args.base_ref}...HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        # Without this the fatal stderr is parsed as a diff, yielding zero
        # changed functions — so the diff-scoped gate reports OK with
        # survivors present. A typo in --base-ref silently disables the gate
        # (reproduced with `--base-ref does-not-exist`, Codex review).
        print(
            f"ERROR: `git diff {args.base_ref}...HEAD` failed "
            f"(exit {proc.returncode}): {proc.stderr.strip()}\n"
            "Cannot determine which functions this branch changed, so the "
            "diff-scoped gate would pass vacuously."
        )
        return None, 1
    return proc.stdout, None


def _run_reached_its_end(text: str, stats: dict[str, int] | None) -> bool:
    """Is there positive evidence the run *finished*, not merely that it ran?

    Two witnesses, both independent of the results text's own length: the
    exported stats (`total > 0` with a survivor count), and a completed
    progress render (`N/N`). A per-mutant listing is deliberately not one — it
    carries no counter, so a truncated capture reads exactly like a whole one.
    """
    if (
        stats is not None
        and stats.get("total", 0) > 0
        and isinstance(stats.get("survived"), int)
    ):
        return True
    return summary_run_is_complete(text)


def _measurement_is_complete(
    text: str, stats: dict[str, int] | None
) -> tuple[bool, str]:
    """Did we actually measure anything? ``(ok, reason_if_not)``.

    A perfect run prints no per-mutant lines at all, so "empty results" is only
    trustworthy when the exported stats prove mutants ran (``total > 0``).
    Without that witness, empty is unmeasurable — never zero.

    The stats witness needs ``survived`` as well as ``total``. The
    parsed-vs-exported cross-check below is conditional on that key, so a
    receipt carrying only ``total`` — a corrupt file, or a schema change in a
    permitted future ``mutmut>=3.7,<4`` — silently skipped the cross-check
    while still counting as proof the run finished; with an unrecognised
    results format alongside it, the survivor count then defaulted to zero and
    passed a zero baseline unvalidated (Codex review).
    """
    if (
        stats is not None
        and stats.get("total", 0) > 0
        and isinstance(stats.get("survived"), int)
    ):
        return True, ""
    if parse_mutant_records(text):
        return True, ""
    if parse_survivors(text) is not None and summary_run_is_complete(text):
        return True, ""
    return False, (
        "no per-mutant results, no mutants/mutmut-cicd-stats.json carrying "
        "total > 0 and a survived count, and no completed progress render "
        "(N/N) — cannot tell 'all mutants killed' from 'mutmut never ran' or "
        "was interrupted"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run `mutmut run` first.")
    parser.add_argument(
        "--results-file", help="Read results from a file ('-' for stdin)."
    )
    parser.add_argument(
        "--mutants-dir",
        default="mutants",
        help="Directory holding mutmut's artifacts (default: mutants).",
    )
    parser.add_argument(
        "--baseline", type=int, default=None, help="Global-total baseline override."
    )
    parser.add_argument(
        "--baseline-file",
        default=str(DEFAULT_BASELINE_FILE),
        help="Per-module baseline JSON (default: mutation-baseline.json).",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write the measurement to --baseline-file instead of gating on it.",
    )
    parser.add_argument(
        "--diff-scoped",
        action="store_true",
        help="Fail on any survivor in a function changed vs --base-ref.",
    )
    parser.add_argument(
        "--base-ref", default="origin/main", help="Base ref for --diff-scoped."
    )
    parser.add_argument("--diff-file", help="Read the diff from a file instead of git.")
    parser.add_argument(
        "--scope-run-to-diff",
        action="store_true",
        help=(
            "With --run --diff-scoped (and not --require-baseline): pass "
            "mutmut run the MUTANT_NAMES of only the only_mutate module(s) "
            "this diff touches, so the expensive test-execution phase runs "
            "against a fraction of the mutant population instead of all of "
            "[tool.mutmut].only_mutate every time. Falls back to an "
            "unscoped (full) run whenever the scope cannot be established "
            "as safe — never widens the gate, only ever the amount of work "
            "it costs to satisfy it."
        ),
    )
    parser.add_argument(
        "--require-baseline",
        action="store_true",
        help=(
            "Fail if no baseline is available. The scheduled drift lane uses "
            "this so it cannot silently degrade to report-only."
        ),
    )
    parser.add_argument("--json", help="Write a machine-readable receipt here.")
    args = parser.parse_args(argv)

    # Fetched once, up front, whenever it will be needed at all: by run-scoping
    # below (if requested) and, either way, by the diff-scoped gate further
    # down. See _load_diff_text's own docstring for why this also has to
    # happen before --run, not just before gating.
    #
    # Not fetched at all under --write-baseline, even with --diff-scoped also
    # given: that branch returns before the diff-scoped gate below ever runs
    # (records the *whole* population, unconditionally), and run-scoping is
    # separately disabled under --write-baseline already (`and not
    # args.write_baseline` a few lines down) -- so nothing downstream ever
    # reads diff_text in this mode. Loading it anyway meant an offline
    # checkout with no origin/main, a stale --base-ref, or an unreadable
    # --diff-file could fail a baseline-recording run before the multi-hour
    # mutmut invocation it exists to protect even started -- exactly the
    # "fails before paying for the run" case _load_diff_text's own docstring
    # argues for elsewhere, but here failing the thing that doesn't need the
    # diff at all (Codex review).
    diff_text: str | None = None
    if args.diff_scoped and not args.write_baseline:
        diff_text, err = _load_diff_text(args)
        if diff_text is None:
            return err if err is not None else 1

    # Read early (cheap, side-effect-free) so the scoping decision below can
    # see them: whether a per-module baseline exists changes whether scoping
    # is safe at all, not just how a later gate reads. Reused verbatim by the
    # baseline-comparison block further down instead of re-reading.
    baseline_modules = load_baseline(Path(args.baseline_file))
    function_baseline = load_function_baseline(Path(args.baseline_file))
    total_baseline = args.baseline if args.baseline is not None else SURVIVOR_BASELINE

    scope_patterns: list[str] | None = None
    scope_modules: set[str] = set()
    if (
        args.run
        # _gather() checks --results-file *before* --run and returns those
        # saved results unconditionally when both are given (a pre-existing
        # quirk, unchanged here) — so --run alone does not mean mutmut is
        # about to be re-executed. Scoping (and the scope-aware unresolved
        # gate below, which depends on it) must not activate over a saved
        # results file: nothing there is guaranteed to reflect a scoped run,
        # and treating its out-of-scope "not checked"/timeout/etc. records as
        # exempt could mask a real gap in what was actually measured (Codex
        # review).
        and not args.results_file
        and args.diff_scoped
        and args.scope_run_to_diff
        and not args.require_baseline
        and not args.write_baseline
        # A module-scope edit (one outside every function) has no mutant of
        # its own for check_diff_scoped() to attribute — by design, it is
        # "scored by the per-module drift gate below" instead (that
        # function's own docstring). check_per_module() is the only thing
        # that can catch it under scoping (the global-total gate now skips
        # itself for a scoped run, on purpose — see that block's own
        # comment for why comparing a scoped population against a
        # whole-repository total is unsound). Without a per-module baseline
        # to run check_per_module() against, a scoped run with only the
        # legacy global total configured would have *no* gate left standing
        # for such an edit at all: check_diff_scoped() can't attribute it,
        # check_per_module() doesn't run (no baseline_modules), and the
        # global-total check now declines to score a partial population
        # (Codex review). Falls back to a full run instead — the same
        # "when in doubt, pay for the full population" answer this predicate
        # family already gives for every other case it can't establish as
        # safe, not a new heuristic.
        and not (total_baseline is not None and baseline_modules is None)
    ):
        # Guaranteed non-None: this branch requires args.diff_scoped, which is
        # exactly the condition under which the block above either set
        # diff_text or already returned.
        assert diff_text is not None
        only_mutate = load_only_mutate_globs()
        scope_patterns = mutant_run_scope(diff_text, only_mutate)
        if scope_patterns is not None and only_mutate is not None:
            scope_modules = diff_touched_only_mutate_modules(diff_text, only_mutate)
            print(
                "mutation-score: scoping this run to "
                f"{len(scope_modules)}/{len(only_mutate)} only_mutate module(s): "
                + ", ".join(sorted(scope_modules))
            )

    gather_started = time.monotonic()
    text, stats = _gather(args, scope_patterns)
    gather_seconds = time.monotonic() - gather_started
    if text is None:
        if args.run:
            print(
                "ERROR: --run requested but mutmut produced no output (not "
                "installed / could not start). Failing so the mutation gate is "
                "not a silent no-op."
            )
            return 1
        if args.results_file:
            # An explicitly named input that could not be read is a failed
            # invocation, not an optional one. Sharing the "no tool, nothing
            # to do" exit meant a scripted offline check reported success
            # having processed no results at all (Codex review) — the same
            # can't-fail shape as the --run branch above, reached from the
            # other direction.
            print(
                f"ERROR: --results-file {args.results_file} could not be read, "
                "so there are no mutation results to gate on."
            )
            return 1
        return 0

    complete, why = _measurement_is_complete(text, stats)
    if not complete:
        print(f"mutation-score: unmeasurable — {why}")
        # An explicitly named input counts the same as --run: the caller
        # supplied results and asked for a verdict on them, so "cannot tell"
        # is a failure, not a skip. Only a bare invocation — no run, no input
        # — has genuinely nothing to measure (Codex review).
        return 1 if (args.run or args.results_file) else 0

    records = parse_mutant_records(text)
    # Per-mutant listings are the strong source; a summary-only text (an older
    # capture, or a clean run whose only signal is the final render) still has
    # to yield a count, so fall back to the summary readers -- which take the
    # LAST render, never the first. `by_module` is necessarily empty in that
    # case: a summary carries no attribution, and inventing one would be worse
    # than reporting none.
    if records:
        survivors = sum(1 for r in records if r.is_survivor)
    else:
        survivors = parse_survivors(text) or 0
    unresolved = count_unresolved(text)
    # A scoped run deliberately never test-executes mutants outside
    # `scope_modules` — every one of them reads "not checked", which
    # `count_unresolved`/`MutantRecord.is_unresolved` correctly treat as an
    # unresolved measurement (as they must for a *genuinely* interrupted run).
    # Gating on the unscoped total here would fail every scoped run on the
    # out-of-scope population it deliberately never measured. `unresolved`
    # itself (the informational, whole-population figure printed below and
    # written to the receipt) is untouched.
    unresolved_for_gate = (
        sum(1 for r in records if r.is_unresolved and r.module_path in scope_modules)
        if scope_modules
        else unresolved
    )
    by_module = survivors_by_module(records)

    # Two independent sources must agree. `mutmut results`' per-mutant listing
    # and `mutmut export-cicd-stats`' counters are produced by the same run, so
    # a disagreement means this parser no longer understands the output — which
    # a permitted `mutmut>=3.7,<4` update can cause. Without this check the
    # unparsed output degrades to zero survivors while the stats report real
    # ones, and `_measurement_is_complete` accepts the stats as proof the run
    # happened, so every gate passes (Codex review).
    # A summary-only text ("🙁 3") yields a survivor *count* with no
    # attribution: `by_module` is empty, so the per-module gate compares
    # nothing and reports success, and `--write-baseline` would record
    # `total_survivors: 0` — silently discarding every survivor and then
    # gating future runs against that fiction (Codex review). Counting is fine
    # for the global-total check; anything that needs to know *where* the
    # survivors are must refuse this input.
    if survivors and not records:
        needs_attribution = (
            args.write_baseline
            or args.diff_scoped
            or (load_baseline(Path(args.baseline_file)) is not None)
        )
        if needs_attribution:
            print(
                f"ERROR: {survivors} surviving mutant(s) reported, but the "
                "results carry no per-mutant listing to attribute them to a "
                "module or function. Per-module gating, --diff-scoped and "
                "--write-baseline all need that attribution; re-run with "
                "`mutmut results` output rather than a summary."
            )
            return 1

    if stats is not None and "survived" in stats and stats["survived"] != survivors:
        print(
            f"ERROR: parsed {survivors} surviving mutant(s) but "
            f"mutants/mutmut-cicd-stats.json reports {stats['survived']}. The "
            "two disagree, so this build's parser no longer matches mutmut's "
            "output — refusing to gate on either number. Check whether mutmut "
            "changed its `results` format."
        )
        return 1

    msg = f"mutation-score: {survivors} surviving mutant(s)"
    if stats:
        msg += f" of {stats.get('total', '?')} total"
    if unresolved:
        msg += f", {unresolved} unresolved (timeout/suspicious/no-tests/segfault)"
    print(msg)
    for module, keys in by_module.items():
        print(f"  {module}: {len(keys)}")

    exit_code = 0
    # baseline_modules / function_baseline / total_baseline: read early, above
    # the run-scoping decision — reused as-is here.
    #: Is there a *drift* reference — the only thing that can answer "did the
    #: survivor set grow", for any function, changed or not?
    baseline_available = baseline_modules is not None or total_baseline is not None
    #: Did any check in this run actually have something to compare against?
    #: A run that gated nothing must never be reported as a pass. Derived
    #: rather than defaulted to True: a plain report-only invocation (no
    #: --diff-scoped, no baseline of either kind) passes through none of the
    #: branches below, so a `True` default made its receipt claim a gate that
    #: never ran — exactly the "green for a run that checked nothing" shape
    #: this flag exists to expose (Codex review). A baseline is a real gate on
    #: its own; --diff-scoped only becomes one once it has a changed function
    #: to scope to, which is decided below.
    gated = baseline_available
    # "Report-only" means there is genuinely nothing to be measured against.
    # Once any gate is active, an unresolved run is a failed measurement.
    gating_active = args.diff_scoped or baseline_available

    if args.require_baseline and not baseline_available:
        # Deliberately keyed on the baseline, not on `gating_active`, even
        # though --diff-scoped is a real gate. It answers a different
        # question: only whether the functions *this branch changed* have
        # surviving mutants. A diff that changes one detector function and
        # weakens a test covering another leaves the second one's new
        # survivors entirely outside its scope, and counting --diff-scoped as
        # satisfying --require-baseline let exactly that exit 0 (Codex
        # review). Without this, a "baseline drift" lane with no baseline
        # returns 0 no matter how many mutants survive — the can't-fail shape
        # this whole gate exists to remove.
        #
        # This is the *only* --require-baseline check: once a baseline of
        # either kind exists, its own gate below always runs, so `gated` is
        # already True and a second, later "nothing was gated" check could
        # never fire. A trailing copy of it existed and was unreachable.
        print(
            "ERROR: --require-baseline was passed but no baseline is available "
            f"({args.baseline_file} is missing/invalid and SURVIVOR_BASELINE is "
            "unset), so nothing in this run can answer whether the survivor set "
            "grew. --diff-scoped does not substitute: it only looks at the "
            "functions this branch changed. Establish the baseline once with "
            "the workflow_dispatch lane (write_baseline: true), then re-enable "
            "this lane."
        )
        return 1

    if unresolved_for_gate and (gating_active or args.write_baseline):
        print(
            f"ERROR: {unresolved_for_gate} mutant(s) did not resolve (timeout/"
            "suspicious/no-tests/segfault) — the measurement is incomplete; fix "
            "or silence them so the survivor count is trustworthy."
        )
        exit_code = 1
        if args.write_baseline:
            # Never bake an under-resolved run into the committed baseline: the
            # recorded number would understate the real survivor set and every
            # later run would gate against a fiction.
            print(
                "ERROR: refusing to write a baseline from an unresolved run "
                f"({args.baseline_file} left untouched)."
            )
            return 1

    if args.write_baseline and not _run_reached_its_end(text, stats):
        # `mutmut results` prints a plain listing with no progress counter, so
        # a capture truncated after a few lines is indistinguishable from a
        # complete one by its own content. That is tolerable for gating (a
        # short capture can only under-report, and the run that produced it is
        # the caller's own), but not for *recording* — a partial survivor set
        # written as the baseline becomes the thing every later run is scored
        # against, and the loss is silent and permanent (Codex review).
        print(
            "ERROR: refusing to write a baseline from a capture that cannot "
            "prove it is complete. Record it from a real run (which exports "
            "mutants/mutmut-cicd-stats.json), not from a saved `mutmut "
            "results` listing."
        )
        return 1

    if args.write_baseline:
        doc = render_baseline(records)
        Path(args.baseline_file).write_text(
            json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        print(f"mutation-score: wrote baseline to {args.baseline_file}")
        return exit_code

    if args.diff_scoped:
        # Fetched once, at the top of main() (before --run, for run-scoping);
        # reused here rather than fetched a second time.
        assert diff_text is not None, "args.diff_scoped implies diff_text was fetched"
        removed = parse_removed_lines(diff_text)
        read_base = _base_reader(args.base_ref)
        touched = changed_functions(
            parse_changed_lines(diff_text), REPO_ROOT, removed, read_base
        )
        # Not `unresolved`: that name already holds the count of mutants
        # that did not resolve, and it is written to the receipt further
        # down. Shadowing it put a set of paths in that field (caught by the
        # receipt's own test).
        unresolved_paths = unresolved_removals(removed, read_base)
        if unresolved_paths:
            # Not a warning about a corner case: a branch whose only edit is a
            # deleted guard has *all* of its evidence in the removed half, so
            # saying nothing here would let a gate that resolved none of it
            # print the same "OK" line as one that resolved all of it.
            #
            # Whether that is fatal depends on what the unresolved removals
            # could have hidden. A removal in a module with no surviving
            # mutant cannot hide a diff-scoped failure — there is no survivor
            # to attribute — so warning is the honest answer. A removal in a
            # module that *does* carry a survivor is a failed measurement:
            # exiting 0 there is a pass the run never earned, whatever the
            # warning says (Codex review).
            at_risk = sorted(
                {r.module_path for r in records if r.is_survivor}
                & unresolved_paths
                & pure_deletion_paths(diff_text)
            )
            print(
                "mutation-score: WARNING — could not read "
                + ", ".join(sorted(unresolved_paths))
                + f" out of the base revision ({args.base_ref}), so lines "
                "this branch *removed* from them were not attributed to any "
                "function. The diff-scoped result below covers added and "
                "modified lines only for those paths."
            )
            if at_risk:
                print(
                    "ERROR: those unattributed removals are in module(s) that "
                    "carry surviving mutants, so a survivor in a function this "
                    "branch gutted would go unreported: "
                    + ", ".join(at_risk)
                    + ". Fetch the base revision (the PR lane checks out with "
                    "fetch-depth: 0) and re-run."
                )
                return 1
        n_funcs = sum(len(v) for v in touched.values())
        print(f"mutation-score: diff-scoped over {n_funcs} changed function(s)")
        if n_funcs:
            # --diff-scoped is a real gate exactly when it has a changed
            # function to scope to, whether or not that function has
            # survivors — so this is set before the pass/fail split, not
            # inside the "no survivors" arm.
            gated = True
        failures = check_diff_scoped(records, touched, function_baseline)
        if failures:
            print(
                "ERROR: surviving mutants in functions this branch changed — a "
                "mutation of code you just edited still passes the suite, so "
                "the edit is executed but not verified:"
            )
            print("\n".join(failures))
            exit_code = 1
        elif n_funcs == 0 and not baseline_available:
            # The test-only diff. This gate is attribution-based, so a branch
            # that weakens assertions without touching a production function
            # gives it nothing to scope to — and "OK" for a run that examined
            # zero functions reads as a pass it never made (Codex review). The
            # survivors such a branch creates are only visible as *drift*,
            # which needs a baseline.
            print(
                "mutation-score: diff-scoped examined 0 changed functions and "
                f"there is no baseline ({args.baseline_file} is absent and "
                "SURVIVOR_BASELINE is unset), so this run GATED NOTHING. A "
                "test-only change can only be checked as drift; record the "
                "baseline once with the workflow_dispatch lane "
                "(write_baseline: true)."
            )
        else:
            print("mutation-score: diff-scoped OK (no survivors in changed functions)")

    if baseline_modules is not None:
        failures, skipped_modules = check_per_module(
            records, baseline_modules, scope_modules
        )
        if skipped_modules:
            # A scoped run's own mutant test-execution never touched these
            # modules (see check_per_module's own docstring) — say so rather
            # than letting the "OK" below imply they were re-verified this
            # run. Their baseline counts stand unchanged until a full
            # (unscoped) run measures them again.
            print(
                "mutation-score: per-module baseline check skipped "
                f"{len(skipped_modules)} module(s) this scoped run did not "
                "test-execute (their baseline count was not re-verified): "
                + ", ".join(skipped_modules)
            )
        if failures:
            print(
                "ERROR: per-module survivor count rose above baseline — a test "
                "was weakened or new under-verified code landed:"
            )
            print("\n".join(failures))
            exit_code = 1
        elif scope_modules:
            print(
                "mutation-score: per-module baseline OK for the scoped "
                f"module(s): {', '.join(sorted(scope_modules))}"
            )
        else:
            print("mutation-score: per-module baseline OK")
    else:
        print(
            f"mutation-score: no per-module baseline at {args.baseline_file} — "
            "run with --write-baseline to establish it."
        )

    if total_baseline is None:
        print(
            "mutation-score: global-total baseline not set — report-only. The "
            "per-module baseline file is the preferred gate."
        )
    elif scope_modules:
        # The identical blind spot check_per_module() had (Codex review,
        # round 14) applies here too, one level flatter: `survivors` is the
        # *whole-records* survivor count, but a scoped run never
        # test-executes a mutant outside `scope_modules` — every one of
        # those reads "not checked", never "survived" — so `survivors` here
        # is really only the scoped module(s)' own count, not the whole
        # population's. Comparing it against a baseline established from a
        # full run is meaningless in both directions: a real out-of-scope
        # regression would silently read as "improved, please lower the
        # baseline", and there's no partial baseline to fall back to the way
        # check_per_module() has one per module — a "total" has no narrower
        # population to restrict to, so the only sound answer is to skip the
        # comparison outright rather than score it against the wrong
        # population (Codex review).
        print(
            "mutation-score: global-total baseline check skipped — this run "
            f"was scoped to {len(scope_modules)} module(s), so its "
            f"{survivors} surviving mutant(s) is not the whole population's "
            f"count and cannot be compared against the recorded baseline of "
            f"{total_baseline}."
        )
    elif survivors > total_baseline:
        print(f"ERROR: surviving mutants {survivors} exceed baseline {total_baseline}.")
        exit_code = 1
    elif survivors < total_baseline:
        print(
            f"mutation-score: {survivors} < baseline {total_baseline} — please "
            "lower SURVIVOR_BASELINE to lock in the improvement."
        )
    else:
        print(f"mutation-score: OK ({survivors} == baseline {total_baseline})")

    if args.json:
        mutants_measured = None
        mutants_per_second = None
        # These are budget/efficiency metrics for *this invocation's own*
        # measurement, not a report of whatever the local mutmut database
        # happens to currently hold — so both require the same
        # `args.run and not args.results_file` provenance bar run_seconds
        # already holds itself to (that combination genuinely executes
        # mutmut; --run alone does not, since --results-file wins and
        # returns saved results unconditionally when both are given — see
        # this same condition a few lines above `_gather()`'s own call).
        # Without the bare-database-read case excluded too, a bare `mutmut
        # results` read (no --run, no --results-file) would publish a
        # fresh-looking mutants_measured from a database that could be
        # arbitrarily old or itself the product of an earlier *scoped*
        # run — self-consistent with `results_out` (both come from the same
        # local mutants_dir at read time), but not evidence this invocation
        # measured anything at all (Codex review). --results-file is the
        # sibling, narrower case already fixed above: there `stats` isn't
        # even self-consistent with `results_out`, since the latter is an
        # arbitrary externally-supplied file with no relationship to
        # whatever happens to be sitting in args.mutants_dir locally.
        if stats is not None and args.run and not args.results_file:
            not_checked = stats.get("not_checked", 0)
            mutants_measured = max(stats.get("total", 0) - not_checked, 0)
            if gather_seconds > 0:
                mutants_per_second = round(mutants_measured / gather_seconds, 3)
        Path(args.json).write_text(
            json.dumps(
                {
                    "survivors": survivors,
                    "unresolved": unresolved,
                    "unresolved_in_scope": unresolved_for_gate,
                    "stats": stats,
                    "by_module": by_module,
                    "gated": gated,
                    "exit_code": exit_code,
                    #: Budget/efficiency metrics (only meaningful for a real
                    #: --run that actually invoked mutmut): how long
                    #: `_gather` took, how many mutants were actually
                    #: test-executed and at what rate, and whether this run
                    #: scoped the expensive phase to a subset of
                    #: `only_mutate` or fell back to the full population.
                    #: `_gather()` checks --results-file *before* --run and
                    #: returns those saved results unconditionally when both
                    #: are given, without ever invoking mutmut — so
                    #: `run_seconds` under that combination would otherwise
                    #: be a near-zero file-read duration, not a real run
                    #: time, and `mutants_per_second` derived from it would
                    #: read as an implausibly fast "live" rate to anything
                    #: consuming this receipt (Codex review).
                    "run_seconds": (
                        round(gather_seconds, 3)
                        if args.run and not args.results_file
                        else None
                    ),
                    "mutants_measured": mutants_measured,
                    "mutants_per_second": mutants_per_second,
                    #: "unknown" rather than "full" whenever this invocation
                    #: didn't itself execute an unscoped `mutmut run` — a
                    #: saved --results-file, or (Codex review, PR #877,
                    #: twelfth round on this finding) a bare `--json` with
                    #: neither --run nor --results-file, which reads the
                    #: existing mutmut database as-is (the `mutmut results`
                    #: path). Either way the measurement's own provenance
                    #: carries no record of whether the run that produced it
                    #: was itself scoped, so labeling it "full" would assert
                    #: a fact this invocation cannot see — misleading trend/
                    #: budget tooling that trusts this field the same way
                    #: "full" is otherwise never a guess. Reuses the exact
                    #: `args.run and not args.results_file` predicate
                    #: `run_seconds` above already uses for "did this
                    #: invocation actually invoke mutmut itself".
                    "run_scope": {
                        "mode": (
                            "diff"
                            if scope_modules
                            else (
                                "full"
                                if args.run and not args.results_file
                                else "unknown"
                            )
                        ),
                        "modules": sorted(scope_modules),
                        "requested": bool(args.scope_run_to_diff),
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

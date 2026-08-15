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

"""Parsing and attribution primitives for the mutation-score gate.

Split out of ``check_mutation_score.py`` so the *data model* (which mutant is
in which module and function, and which statuses mean "measured") is a leaf
module with its own property tests, per AGENTS.md's "Primitive-level property
tests" guidance — the gate's own drift logic sits on top of it.

Everything here is derived from **real** ``mutmut`` 3.7.0 output, captured by
running it against a scratch package, not from the documentation. Three shapes
matter and are pinned by tests:

1. ``mutmut results`` per-mutant lines — the authoritative status listing::

       pkg.alpha.x_scale__mutmut_1: survived
       pkg.gamma.xǁWidgetǁarea__mutmut_1: survived
       pkg.gamma.x_fetch__mutmut_1: no tests

   Note ``results`` lists only *non-killed* mutants, so a perfect run prints
   nothing at all — which is why "empty" can never be read as "zero survivors"
   without an independent completeness witness (see 3).

2. Mangled function names. mutmut prefixes a module-level function with ``x_``
   and joins a method's class path with U+01C1 (``ǁ``), e.g. ``xǁWidgetǁarea``
   for ``Widget.area``. A nested function's mutants are attributed to its
   *enclosing* top-level function, not to the nested name.

3. ``mutmut export-cicd-stats`` → ``mutants/mutmut-cicd-stats.json`` — machine
   readable totals (``survived``/``killed``/``total``/…). This is the
   completeness witness: ``total > 0`` proves mutants actually ran.

**What is deliberately NOT a source of truth: the ``mutmut run`` progress
stream.** It re-renders a summary line (``… 🙁 4 …``) many times, starting from
``🙁 0`` before any mutant is classified, and those renders survive into piped,
non-TTY output. Reading the *first* one — which is what a plain ``re.search``
over run-then-results text does — reports zero survivors for a run that had
several. That was a real defect in this gate (see ``parse_survivors`` below).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

#: mutmut's own status vocabulary, transcribed from ``mutmut/__main__.py``'s
#: ``status_by_exit_code`` / ``emoji_by_status`` tables (3.7.0).
STATUS_KILLED = "killed"
STATUS_SURVIVED = "survived"
STATUS_SKIPPED = "skipped"
STATUS_TYPE_CHECKED = "caught by type check"

#: Statuses that represent a *resolved, acceptable* outcome: the mutant was
#: killed by a test, deliberately excluded, or rejected by the type checker.
RESOLVED_OK_STATUSES = frozenset({STATUS_KILLED, STATUS_SKIPPED, STATUS_TYPE_CHECKED})

#: The statuses mutmut 3.7.0 emits that mean the measurement did not resolve.
#: Accepting any of these as "not a survivor" would let an under-resolved run
#: pass a zero baseline — ``no tests`` in particular is a *real* gap (nothing
#: covers the mutant), not a neutral outcome.
#:
#: This is documentation of the observed vocabulary, **not** the predicate:
#: :attr:`MutantRecord.is_unresolved` is the complement of
#: :data:`RESOLVED_OK_STATUSES` plus ``survived``, so a status this list does
#: not name still counts as unresolved.
UNRESOLVED_STATUSES = frozenset(
    {
        "no tests",
        "timeout",
        "suspicious",
        "not checked",
        "check was interrupted by user",
        "segfault",
    }
)

#: mutmut mangles a module-level function ``foo`` to ``x_foo`` and a method
#: ``Widget.area`` to ``xǁWidgetǁarea`` (U+01C1 LATIN LETTER LATERAL CLICK as
#: the class separator). Verified against real 3.7.0 output.
_MANGLE_CLASS_SEP = "ǁ"
_MANGLE_PREFIXES = ("x_", "x" + _MANGLE_CLASS_SEP)

#: ``<dotted.mangled.key>__mutmut_<n>: <status>`` — the per-mutant listing.
#: The status is free text with spaces ("no tests", "caught by type check"),
#: so it is captured greedily to end-of-line and stripped.
_MUTANT_LINE = re.compile(
    r"^\s*(?P<key>[^\s:]+__mutmut_\d+)\s*:\s*(?P<status>.+?)\s*$",
    re.MULTILINE,
)

#: A progress/summary render from ``mutmut run``. Only ever read as a *last*
#: match, never a first one; see the module docstring.
_PROGRESS_SUMMARY = re.compile(r"🙁\s*(\d+)")

#: Emoji counters for the unresolved statuses, read from a summary render when
#: there is no per-mutant listing to count instead. As with the survivor
#: counter, only the *last* render is trusted.
_EMOJI_TIMEOUT = re.compile(r"⏰\s*(\d+)")
_EMOJI_SUSPICIOUS = re.compile(r"🤔\s*(\d+)")
_EMOJI_NO_TESTS = re.compile(r"🫥\s*(\d+)")

#: Plain-text fallback. Not observed in mutmut 3.7.0's own output — retained
#: only as a last-resort reader for a differently-worded summary, and ordered
#: after both evidence-bearing sources so it can never override them.
_WORD_SURVIVED_COUNT = re.compile(r"(\d+)\s+survived\b", re.IGNORECASE)


@dataclass(frozen=True)
class MutantRecord:
    """One mutant's identity and outcome, attributed to its source location."""

    key: str
    #: Dotted module path, e.g. ``abicheck.diff_types``.
    module: str
    #: Source-relative qualname of the function the mutant lives in, with
    #: mutmut's mangling undone, e.g. ``Widget.area`` or ``compare``.
    function: str
    status: str

    @property
    def module_path(self) -> str:
        """POSIX-style source path, e.g. ``abicheck/diff_types.py``."""
        return self.module.replace(".", "/") + ".py"

    @property
    def is_survivor(self) -> bool:
        return self.status == STATUS_SURVIVED

    @property
    def is_unresolved(self) -> bool:
        """Anything that is neither a survivor nor an *explicitly* good outcome.

        Deliberately the complement of :data:`RESOLVED_OK_STATUSES` rather than
        a membership test against :data:`UNRESOLVED_STATUSES`: the pin allows
        any ``mutmut>=3.7,<4``, and a future 3.x that adds a status neither
        frozenset names would otherwise be counted as neither a survivor nor
        unresolved — vanishing from both halves of the gate and letting an
        outcome this parser does not understand pass a zero baseline. Failing
        closed makes an unrecognised status an unresolved measurement, which
        the gate already refuses to score (Codex review).
        """
        return self.status not in RESOLVED_OK_STATUSES and not self.is_survivor


def demangle_function(component: str) -> str:
    """Undo mutmut's function-name mangling for one dotted component.

    ``x_scale`` -> ``scale``; ``xǁWidgetǁarea`` -> ``Widget.area``. A component
    that carries no mutmut prefix is returned unchanged, so this is safe to
    call on anything.
    """
    if component.startswith("x" + _MANGLE_CLASS_SEP):
        return component[1:].replace(_MANGLE_CLASS_SEP, ".").lstrip(".")
    if component.startswith("x_"):
        return component[2:]
    return component


def split_mutant_key(key: str) -> tuple[str, str] | None:
    """Split a mutant key into ``(dotted_module, source_function_qualname)``.

    ``pkg.gamma.xǁWidgetǁarea__mutmut_1`` -> ``("pkg.gamma", "Widget.area")``.

    The *last* dotted component is always the mangled function, so the module
    is everything before it — this stays correct even for a module whose own
    name happens to start with ``x_``. Returns ``None`` for a key that does not
    carry the ``__mutmut_<n>`` suffix or has no module part, rather than
    guessing, so a caller can tell "not a mutant key" from a real attribution.
    """
    head, sep, tail = key.rpartition("__mutmut_")
    if not sep or not tail.isdigit() or not head:
        return None
    module, dot, func_component = head.rpartition(".")
    if not dot or not module:
        return None
    if not func_component.startswith(_MANGLE_PREFIXES):
        return None
    return module, demangle_function(func_component)


def parse_mutant_records(text: str) -> list[MutantRecord]:
    """Parse every ``<key>: <status>`` line from ``mutmut results`` output.

    Lines that are not per-mutant listings (progress renders, banners) simply
    do not match and are ignored. Keys that cannot be attributed to a module
    are skipped rather than guessed at.
    """
    records: list[MutantRecord] = []
    for m in _MUTANT_LINE.finditer(text or ""):
        key = m.group("key")
        split = split_mutant_key(key)
        if split is None:
            continue
        module, function = split
        records.append(
            MutantRecord(
                key=key, module=module, function=function, status=m.group("status")
            )
        )
    return records


def parse_survivors(text: str) -> int | None:
    """Number of surviving mutants, or ``None`` when the text carries no signal.

    Precedence — strongest evidence first:

    1. Per-mutant ``results`` lines. Authoritative: every non-killed mutant is
       listed individually with its own status.
    2. Failing that, the *last* ``🙁 <n>`` summary render. **Last, not first**:
       ``mutmut run``'s progress line is redrawn continuously starting from
       ``🙁 0``, so the first match is an artifact of the run having just
       started, not a measurement. Reading the first one made this gate report
       ``0`` for a run with 4 real survivors (reproduced against mutmut 3.7.0).

    A perfectly clean run prints no per-mutant lines *and* no summary once the
    text is only ``mutmut results`` output — that is genuinely unmeasurable
    from this text alone, so callers must pair it with a completeness witness
    (``load_cicd_stats``) rather than reading ``None`` as zero.
    """
    if not text or not text.strip():
        return None
    records = parse_mutant_records(text)
    if records:
        return sum(1 for r in records if r.is_survivor)
    matches = _PROGRESS_SUMMARY.findall(text)
    if matches:
        return int(matches[-1])
    m = _WORD_SURVIVED_COUNT.search(text)
    if m:
        return int(m.group(1))
    return None


def count_unresolved(text: str) -> int:
    """Mutants that neither died nor survived (timeout / suspicious / no-tests).

    Same precedence as :func:`parse_survivors`: the per-mutant listing when
    there is one, otherwise the *last* summary render. ``🔇`` (skipped) is
    deliberately excluded — that status means "intentionally not mutated", not
    "not measured".
    """
    records = parse_mutant_records(text)
    if records:
        return sum(1 for r in records if r.is_unresolved)
    total = 0
    for pat in (_EMOJI_TIMEOUT, _EMOJI_SUSPICIOUS, _EMOJI_NO_TESTS):
        hits = pat.findall(text or "")
        if hits:
            total += int(hits[-1])
    return total


def survivors_by_module(records: list[MutantRecord]) -> dict[str, list[str]]:
    """Map ``source/path.py`` -> sorted surviving mutant keys."""
    out: dict[str, list[str]] = {}
    for r in records:
        if r.is_survivor:
            out.setdefault(r.module_path, []).append(r.key)
    return {k: sorted(v) for k, v in sorted(out.items())}


def load_cicd_stats(mutants_dir: Path) -> dict[str, int] | None:
    """Read ``mutants/mutmut-cicd-stats.json`` (``mutmut export-cicd-stats``).

    This is the completeness witness: it carries an explicit ``total``, so a
    run that produced zero survivors is distinguishable from a run that never
    executed a mutant. Returns ``None`` when absent or unreadable.
    """
    path = Path(mutants_dir) / "mutmut-cicd-stats.json"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if isinstance(v, int)}


# ---------------------------------------------------------------------------
# Changed-function attribution (for the diff-scoped gate)
# ---------------------------------------------------------------------------


#: Sentinel for a change at module scope — outside every function body.
#: Several mutated modules carry behaviour in top-level tables
#: (`checker_policy`'s kind sets, `finding_identity`'s discriminator kinds), so
#: "no function contains this line" must not collapse to "nothing changed".
MODULE_SCOPE = "<module>"


def functions_covering_lines(source: str, lines: set[int]) -> set[str]:
    """Qualnames of every function whose body encloses any of ``lines``.

    Every *enclosing* def is returned, not just the innermost one: mutmut
    attributes a nested function's mutants to the enclosing top-level function
    (verified against 3.7.0), so a change inside ``outer.nested`` must also
    mark ``outer`` as touched or the diff-scoped gate would miss its survivors.

    A syntactically invalid source yields an empty set rather than raising —
    the gate must not crash on a file the diff left mid-edit.
    """
    if not lines:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    touched: set[str] = set()

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = getattr(child, "name", None)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}{name}"
                start = child.lineno
                # A change confined to a decorator — renaming a detector in
                # `@registry.detector("...")`, which is how every detector in
                # this codebase is registered — sits above `def`, so anchoring
                # at `child.lineno` matched no function and the diff-scoped
                # gate ignored every survivor in it (Codex review).
                for decorator in getattr(child, "decorator_list", ()):
                    start = min(start, decorator.lineno)
                end = getattr(child, "end_lineno", child.lineno) or child.lineno
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    start <= ln <= end for ln in lines
                ):
                    touched.add(qual)
                walk(child, qual + ".")
            else:
                walk(child, prefix)

    walk(tree, "")

    # A changed line inside no function is a module-scope edit. mutmut only
    # mutates function bodies, so such a line has no mutants of its own — but
    # it can change what every function in the module does, and leaving
    # `touched` empty made the diff-scoped gate match nothing and report OK
    # for exactly those edits (Codex review). Blank and comment-only lines are
    # excluded so reformatting does not gate a whole module.
    covered = _lines_inside_functions(tree)
    source_lines = source.splitlines()
    for line in lines:
        if line in covered:
            continue
        text = source_lines[line - 1].strip() if 0 < line <= len(source_lines) else ""
        if text and not text.startswith("#"):
            touched.add(MODULE_SCOPE)
            break
    return touched


def _lines_inside_functions(tree: ast.AST) -> set[int]:
    """Every line number covered by some function body."""
    covered: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            for decorator in getattr(node, "decorator_list", ()):
                start = min(start, decorator.lineno)
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            covered.update(range(start, end + 1))
    return covered

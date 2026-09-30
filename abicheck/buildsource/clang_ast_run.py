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

"""One bounded ``clang -Xclang -ast-dump=json`` run, shared by the L5 passes.

A **leaf** module: the call-graph (``call_graph.py``) and type-graph
(``type_graph.py``) extractors run the *identical* procedure — same argv shape,
same 120s local cap folded against the active ``--budget`` deadline, same
degrade-to-``diagnostic``-and-give-up contract at every failure point — and
differ only in which pure parser they hand the resulting AST to. Both used to
carry their own copy, kept in step by hand through comments pointing at each
other ("mirrors ``call_graph.ClangCallGraphExtractor._extract_from_safe_args``")
across several review rounds of deadline fixes; stated once here instead, so a
fix to the bounding or the diagnostics cannot land on one pass only (CodeFactor:
duplicate code).

Imports nothing from either caller, so neither has to import the other's
internals for this.

**One dump per TU across every pass** (:func:`parse_clang_ast` +
:func:`shared_ast_scope`). The six L5 graph passes (call, type, override,
template, macro-range, callback) build the *same* argv for a TU
(``call_graph._safe_clang_args_from_compile_unit``) and so used to run the
same multi-GiB ``clang -ast-dump=json`` six times per TU, one full pass over
the compile DB each -- measured on PVXS (34 TUs) as the dominant cost of a
``--depth source`` dump. Inside a :func:`shared_ast_scope` naming every
pass's parser, the first request for a ``(clang_bin, argv, cwd)`` key runs
clang once, applies *every* registered parser to that one AST, and keeps only
their (small) results plus the dump's own diagnostics; the AST itself is
dropped immediately. Each later pass is answered from that record with the
same diagnostics replayed, so what each pass observes -- result, diagnostics,
and the exception its own ``except`` clause handles -- is what an independent
dump of the same argv would have produced. Outside a scope, or for a parser
the scope did not register, :func:`parse_clang_ast` dumps and parses directly.
"""

from __future__ import annotations

import json
import subprocess  # noqa: S404 - AST extraction shells out to clang (never shell=True)
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from .. import deadline
from .._compiler_options import CLANG_GCC_HEADER_COMPAT_DEFINES

#: Per-TU wall-clock ceiling for one AST dump. Folded against the scan's own
#: remaining ``--budget`` so one hung TU can never eat the whole scan.
LOCAL_CAP_S = 120.0


def run_clang_ast_dump(
    clang_bin: str,
    argv: list[str],
    *,
    cwd: str | None,
    diagnostics: list[str],
) -> dict[str, Any] | None:
    """Dump one TU's clang AST as JSON, or ``None`` with a recorded diagnostic.

    *argv* must already be the caller's sanitized, allowlisted flag subset —
    this function only prepends the fixed dump flags and never consults a
    shell. Every failure path (a failed invocation, empty output, an exhausted
    budget, unparseable JSON) appends to *diagnostics* and answers ``None``:
    these passes are advisory (ADR-028 D3), never authoritative, so a probe
    failure must degrade rather than abort the scan. Whether ``clang`` is
    present at all stays the caller's own ``available()`` check — that is an
    overridable method on each extractor, not a property of this run.

    A non-zero clang exit is *not* a failure path — clang still prints the
    partial, error-recovered tree it built from the necessarily approximate
    replayed flags, and salvaging edges from it is deliberate. It does record a
    diagnostic, so ``extractor_pass_fully_covered`` (ADR-041 P0 slice 3) never
    counts that TU as cleanly, fully parsed.
    """
    cmd = [
        clang_bin,
        "-Xclang",
        "-ast-dump=json",
        "-fsyntax-only",
        *CLANG_GCC_HEADER_COMPAT_DEFINES,
        *argv,
    ]
    scan_remaining = deadline.remaining()
    effective_timeout = (
        LOCAL_CAP_S if scan_remaining is None else min(LOCAL_CAP_S, scan_remaining)
    )
    try:
        # Bound by min(LOCAL_CAP_S, active --budget deadline) — run_bounded()
        # alone would honor a generous outer deadline verbatim instead of this
        # pass's own cap, letting one hung TU eat the whole remaining scan
        # budget — and process-group-safe on timeout, same as the L2/L4 clang
        # calls (Codex review, PR #591, round 8).
        with deadline.deadline_scope(effective_timeout):
            proc = deadline.run_bounded(  # noqa: S603 - fixed argv, never shell=True
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=LOCAL_CAP_S,
            )
    except (OSError, subprocess.SubprocessError, deadline.DeadlineExceeded) as exc:
        diagnostics.append(f"clang invocation failed: {exc}")
        return None
    if not proc.stdout.strip():
        diagnostics.append(f"clang produced no AST (stderr: {proc.stderr[:200]})")
        return None
    if proc.returncode != 0:
        diagnostics.append(
            f"clang exited {proc.returncode} (stderr: {proc.stderr[:200]})"
        )
    try:
        # clang can exit successfully right as the budget expires; recheck
        # before the CPU/RSS-heavy parse+walk, same as the L2/L4 post-run
        # checks (Codex review, PR #591).
        deadline.check()
    except deadline.DeadlineExceeded as exc:
        diagnostics.append(f"scan deadline exceeded before parsing clang AST: {exc}")
        return None
    try:
        # Both json.loads and the caller's recursive AST walk can hit Python's
        # recursion limit on a pathologically deep TU; guard so a degenerate AST
        # degrades to "no edges" rather than aborting collection.
        ast: dict[str, Any] = json.loads(proc.stdout)
    except (ValueError, RecursionError) as exc:
        diagnostics.append(f"could not parse clang AST JSON: {exc}")
        return None
    try:
        # The JSON load itself can consume the rest of the budget on a huge AST;
        # re-check before the recursive walk (Codex review, PR #591, round 4).
        deadline.check()
    except deadline.DeadlineExceeded as exc:
        diagnostics.append(f"scan deadline exceeded before walking clang AST: {exc}")
        return None
    return ast


T = TypeVar("T")

#: A pure parser over one ``clang -ast-dump=json`` tree.
AstParser = Callable[[dict[str, Any]], Any]


class NoAst:
    """Sentinel: the dump produced no usable AST (see *diagnostics*)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "NO_AST"


NO_AST = NoAst()


@dataclass
class _DumpRecord:
    """One TU's single dump: its diagnostics and every registered parser's outcome."""

    done: threading.Event = field(default_factory=threading.Event)
    diagnostics: list[str] = field(default_factory=list)
    has_ast: bool = False
    #: parser -> ("ok", result) | ("err", exception)
    outcomes: dict[AstParser, tuple[str, Any]] = field(default_factory=dict)


class _SharedAstRegistry:
    """Process-wide, reference-counted memo of per-TU parse outcomes.

    Module-level rather than a ``contextvar`` because the passes' workers run
    on ``BudgetedExecutor`` threads, which do not inherit context. Scopes
    nest and may overlap (two sides of a compare folding concurrently): the
    registered parser set is their union, and records are dropped only when
    the last scope closes. Sharing a record between overlapping scopes is
    sound because the key is the complete dump input.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depth = 0
        self._parsers: dict[AstParser, int] = {}
        self._records: dict[tuple[str, tuple[str, ...], str | None], _DumpRecord] = {}

    def enter(self, parsers: Iterable[AstParser]) -> list[AstParser]:
        added = list(dict.fromkeys(parsers))
        with self._lock:
            self._depth += 1
            for p in added:
                self._parsers[p] = self._parsers.get(p, 0) + 1
        return added

    def exit(self, added: list[AstParser]) -> None:
        with self._lock:
            self._depth -= 1
            for p in added:
                n = self._parsers[p] - 1
                if n:
                    self._parsers[p] = n
                else:
                    del self._parsers[p]
            if self._depth == 0:
                self._records.clear()

    def lookup(
        self, key: tuple[str, tuple[str, ...], str | None], parser: AstParser
    ) -> tuple[_DumpRecord, bool] | None:
        """``(record, owner)`` for *key*, or ``None`` when *parser* is not shared.

        The first caller for a key becomes its *owner* and must fill it
        (:meth:`fill`); every other caller waits on ``record.done``.
        """
        with self._lock:
            if parser not in self._parsers:
                return None
            record = self._records.get(key)
            if record is not None:
                return record, False
            record = _DumpRecord()
            self._records[key] = record
            return record, True

    def registered(self) -> list[AstParser]:
        with self._lock:
            return list(self._parsers)


_REGISTRY = _SharedAstRegistry()


@contextmanager
def shared_ast_scope(parsers: Iterable[AstParser]) -> Iterator[None]:
    """Share one clang AST dump per TU among *parsers* for the scope's duration."""
    added = _REGISTRY.enter(parsers)
    try:
        yield
    finally:
        _REGISTRY.exit(added)


def _apply(parser: AstParser, ast: dict[str, Any]) -> tuple[str, Any]:
    try:
        return ("ok", parser(ast))
    except Exception as exc:  # noqa: BLE001 - replayed to the pass that owns it
        return ("err", exc)


def parse_clang_ast(
    clang_bin: str,
    argv: list[str],
    *,
    cwd: str | None,
    diagnostics: list[str],
    parser: Callable[[dict[str, Any]], T],
) -> T | NoAst:
    """Dump the TU (once per scope) and return ``parser(ast)``.

    Returns :data:`NO_AST` when the dump failed -- *diagnostics* then says
    why, exactly as :func:`run_clang_ast_dump` records it. An exception the
    parser raised is raised here, so the calling pass's own ``except`` clause
    decides how to degrade, as it did when it parsed the AST itself.
    """
    key = (clang_bin, tuple(argv), cwd)
    found = _REGISTRY.lookup(key, parser)
    if found is None:
        ast = run_clang_ast_dump(clang_bin, argv, cwd=cwd, diagnostics=diagnostics)
        return NO_AST if ast is None else parser(ast)
    record, owner = found
    if owner:
        try:
            ast = run_clang_ast_dump(
                clang_bin, argv, cwd=cwd, diagnostics=record.diagnostics
            )
            if ast is not None:
                record.has_ast = True
                for p in _REGISTRY.registered():
                    record.outcomes[p] = _apply(p, ast)
                del ast
        finally:
            record.done.set()
    else:
        record.done.wait()
    diagnostics.extend(record.diagnostics)
    if not record.has_ast:
        return NO_AST
    outcome = record.outcomes.get(parser)
    if outcome is None:
        # Registered after this TU was dumped (an overlapping scope): the
        # record cannot answer it without the AST, so dump for it alone.
        ast = run_clang_ast_dump(clang_bin, argv, cwd=cwd, diagnostics=[])
        return NO_AST if ast is None else parser(ast)
    status, value = outcome
    if status == "err":
        raise value
    return value  # type: ignore[no-any-return]

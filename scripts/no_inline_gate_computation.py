#!/usr/bin/env python3
"""``no-inline-gate-computation`` gate (ADR-063 Phase 7, WARN).

A leaf module imported by ``check_ai_readiness.py`` (split out rather than
added inline, the same reason -- and the same pattern -- as
``engine_cli_boundary.py``/``adr_status_sync.py``/``fact_detector_misuse.py``:
``check_ai_readiness.py`` is already well past the 2000-line hard cap).

**What this looks for, and why it's scoped this narrowly.** ADR-063 D6's
``RunOutcome.gate``/``RunOutcome.operational`` axes are deliberately
exit-code-free domain values (:class:`~abicheck.policy.outcome.
PolicyGateDecision`/:class:`~abicheck.policy.outcome.OperationalStatus`) --
converting either to a real process exit code or comparing it against a raw
integer is confined to the small set of boundary encoders ADR-063 D6 names:
``policy/outcome.py`` itself (the one place a ``RunOutcome`` axis may be
*decoded* from raw report fields at all), ``workflows/aggregate/gate.py``
(the read-time boundary for a persisted report), and ``cli.py``/
``service.py``/``aggregate.py`` (the write-time boundaries that convert an
already-decoded value to the final exit code/JSON integer).

This is a real, repo-wide AST scan, not a text grep -- but deliberately a
*simple* one (the Phase 7 plan's own acceptance-criteria text: "false
negatives are acceptable for a WARN-level heuristic check, false positives
on legitimate code are not"). It looks for exactly one shape: a direct
attribute read named ``gate`` or ``operational`` (the two ``RunOutcome``
field names -- rare enough elsewhere in this codebase that this is a
precise signal, not a generic ``.exit_code`` glob) appearing alongside a
raw integer literal in a comparison or a ``max(...)`` call. It does **not**
flag every ``.exit_code`` comparison in the codebase -- `fold.py`'s own
``max(t.gate.exit_code for t in gated ...)`` reads the ``.exit_code``
*output* of ``gate.py``'s already-decoded ``GateInfo``, not a
``PolicyGateDecision``/``OperationalStatus`` value, and is exactly the kind
of legitimate cross-target aggregation the Phase 7 plan's own
acceptance-criteria paragraph says this check must not flag.

Pure-stdlib, like its caller, so it can run as the first CI step before
``pip install``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


#: The four boundary encoders ADR-063 D6 names, relative to `abicheck/` --
#: the only modules allowed to compare/fold a `.gate`/`.operational` value
#: against a raw integer literal.
ALLOWED_RELATIVE_PATHS = frozenset(
    {
        "policy/outcome.py",
        "workflows/aggregate/gate.py",
        "service.py",
        "cli.py",
        "aggregate.py",
    }
)

#: The two `RunOutcome` field names this scan watches for.
_WATCHED_ATTRS = frozenset({"gate", "operational"})


def _is_int_literal(node: ast.AST) -> bool:
    """Whether *node* is a raw, non-bool integer literal (``bool`` is an
    ``int`` subclass in Python, and ``True``/``False`` are never the raw
    exit-code literals this check looks for)."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    )


def _is_watched_attr(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr in _WATCHED_ATTRS


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_Compare(self, node: ast.Compare) -> None:
        operands: list[ast.expr] = [node.left, *node.comparators]
        if any(_is_watched_attr(o) for o in operands) and any(
            _is_int_literal(o) for o in operands
        ):
            self.hits.append(
                (
                    node.lineno,
                    "compares a `.gate`/`.operational` RunOutcome axis against a raw integer literal",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "max":
            args = list(node.args)
            if any(_is_watched_attr(a) for a in args) and any(
                _is_int_literal(a) for a in args
            ):
                self.hits.append(
                    (
                        node.lineno,
                        "folds a `.gate`/`.operational` RunOutcome axis via max() against a raw integer literal",
                    )
                )
        self.generic_visit(node)


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Every hit in *path*, as ``(lineno, reason)`` pairs."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def check_no_inline_gate_computation(f: Findings) -> None:
    """The ``no-inline-gate-computation`` check (WARN)."""
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel in ALLOWED_RELATIVE_PATHS:
            continue
        for lineno, reason in scan_file(path):
            f.warn(
                "no-inline-gate-computation",
                f"abicheck/{rel}:{lineno}: {reason} -- confine this to "
                "policy/outcome.py and the per-front-end encoders "
                "(workflows/aggregate/gate.py, service.py, cli.py, aggregate.py)",
            )

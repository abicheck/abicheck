#!/usr/bin/env python3
"""Real, repo-wide scan for a direct `==`/`!=` comparison between two
`Fact[T]`-typed values (ADR-063 Phase 0, docs/contribute/plans/
one-semantic-pipeline.md).

A leaf module imported by ``check_ai_readiness.py``, mirroring
``fact_field_readers.py``'s own extraction/registration pattern (itself
mirroring ``engine_cli_boundary.py`` -- ``check_ai_readiness.py`` is
already past the 2000-line hard cap and only stays green through
``LARGE_FILE_ALLOWLIST``, which is not a license to keep growing it).

**Why this exists.** `abicheck/model/fact.py`'s own `Fact` class docstring
already states the rule this check enforces, in the present tense, as if
it already existed: "A detector reads a `Fact[...]`-typed field only by
inspecting `.status` ... The guard against comparing two `Fact[...]`
values directly inside detector logic (rather than unwrapping first) is a
static check, not a runtime one -- see `scripts/check_ai_readiness.py`'s
`fact-detector-misuse` check." No such check existed anywhere in the
repository when this module was written (`grep -rn
"fact-detector-misuse"` before this change matched nothing but that one
docstring sentence) -- this module is what makes that sentence true rather
than a stale promise. `Fact[T]` deliberately does not override `__eq__`
(see that class's own docstring: overriding it would poison the
*containing* dataclass's generated equality the moment comparison reaches
a `Fact`-typed field), so `x.bases_fact == y.bases_fact` does not raise --
it silently falls back to ordinary structural dataclass equality, which
compares `status`/`value`/`diagnostics` together. That is exactly the kind
of comparison a detector must never write: it can be True or False without
ever asking whether either side is even `PRESENT`, quietly reintroducing
the "confirmed absent vs. not collected" ambiguity `Fact[T]` exists to
make unrepresentable, and it does so *silently* -- no exception, no
warning, just a plausible-looking boolean answering the wrong question.

**No type inference**, the identical stance `fact_field_readers.py`
already states for its own scan: this matches the five *known*
`Fact[T]`-bridged field names (`*_fact` attributes on `RecordType`/`Param`)
directly, plus a `Fact(...)`/`Fact.<classmethod>(...)` constructor call on
either side of the comparison -- not "any expression whose static type is
`Fact[T]`," which would need a real type checker this pre-`pip install`
script cannot use. Verified empirically (by running exactly this scan
against the whole package before writing this module) to have zero
existing violations under `abicheck/` today -- every real hit found by a
plain grep for this pattern lives in `tests/` (asserting a constructed
`Fact` equals an expected one, e.g. `assert merged.vptr_offset_bits_fact
== Fact.partial(0)`), which is legitimate test-assertion code, not
detector logic, and is out of this scan's scope (`PKG = ROOT /
"abicheck"`, matching `fact_field_readers.py`'s own scope). So this check
ships with **no baseline at all** -- unlike `fact_field_readers.py`'s
allowlist-and-shrink `KNOWN_UNMIGRATED_READERS`, there is nothing here to
grandfather; any match is a hard, unconditional ERROR.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"

#: The `Fact[T]`-bridged field names known today (ADR-063 Phase 0's Scope
#: section: `RecordType.bases`/`virtual_bases`/`vtable`/`vptr_offset_bits`,
#: `Param.is_va_list` -- the same five `fact_field_readers.FACT_BRIDGED_
#: ATTRS` names, each with a `<name>_fact` sibling attribute). Not imported
#: from that module: the two checks are independent AST scans matching
#: different node shapes (attribute *reads* there, `==`/`!=` *comparisons*
#: here), and a shared source-of-truth constant would couple two leaf
#: modules that otherwise have no reason to depend on each other. Adding a
#: sixth `Fact[T]`-bridged field anywhere in the codebase means updating
#: both lists -- each documents that expectation in its own docstring.
FACT_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "bases_fact",
        "virtual_bases_fact",
        "vtable_fact",
        "vptr_offset_bits_fact",
        "is_va_list_fact",
    }
)


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _is_fact_typed_expr(node: ast.expr) -> bool:
    """True if *node* is recognizable, by name alone, as a `Fact[T]` value.

    Two shapes: a `<expr>.bases_fact`-style attribute access naming one of
    the five known `Fact[T]`-bridged fields, or a constructor call --
    `Fact(...)`, `Fact.present(...)`, `Fact.not_collected(...)`, etc. (any
    attribute call on the bare name `Fact`, matching every classmethod
    `model/fact.py` defines without hard-coding each one by name -- a
    future constructor added there is covered automatically).

    Does **not** resolve a bare `ast.Name` -- that is :func:`_fact_aliases`'
    job, since answering it needs scope (which function a name belongs to),
    which a single node can't supply on its own.
    """
    if isinstance(node, ast.Attribute) and node.attr in FACT_FIELD_NAMES:
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Fact":
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id == "Fact"
    return False


def _is_fact_typed_annotation(annotation: ast.expr | None) -> bool:
    """True for a `Fact[...]` (or bare `Fact`) type annotation, by name
    alone -- `def f(old_fact: Fact[list[str]], ...)` is exactly as
    Fact-typed as `old.bases_fact` is, and a parameter carrying one is a
    real, common alias source (Codex review, fresh evidence): the caller
    already unwrapped nothing, so comparing two such parameters directly is
    the identical misuse this whole check exists to catch.
    """
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id == "Fact"
    if isinstance(annotation, ast.Subscript):
        return _is_fact_typed_annotation(annotation.value)
    return False


def _enclosing_qualnames(tree: ast.Module) -> dict[int, str]:
    """Map every line number in *tree* to its innermost enclosing
    function's qualified name (``Class.method`` for a method), or
    ``"<module>"`` for a line outside any function.

    Deliberately a standalone copy of `fact_field_readers.py`'s identical
    helper rather than a shared import -- see `FACT_FIELD_NAMES`'s own
    docstring for why these two leaf modules stay decoupled.
    """
    ranges: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                end = getattr(child, "end_lineno", child.lineno)
                ranges.append((child.lineno, end, qualname))
                visit(child, qualname + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    ranges.sort(key=lambda r: r[1] - r[0], reverse=True)
    by_line: dict[int, str] = {}
    for start, end, qualname in ranges:
        for lineno in range(start, end + 1):
            by_line[lineno] = qualname
    return by_line


def _fact_aliases(tree: ast.Module, qualnames: dict[int, str]) -> dict[str, set[str]]:
    """Map each function's qualname to the local names, within that
    function, known to hold a `Fact[T]` value (Codex review, fresh
    evidence): `old_fact = old.bases_fact` followed by `old_fact ==
    new_fact` has two bare `ast.Name` operands, invisible to
    :func:`_is_fact_typed_expr` alone -- the exact misuse this check
    exists to catch, laundered through an ordinary local-variable
    refactor.

    Two sources, both name-only (no type inference, same stance as the
    rest of this module): a simple single-target assignment (`name =
    <fact-typed-expr>`) scoped to its own enclosing function, and a
    function parameter whose own annotation is `Fact[...]`/bare `Fact`
    (see :func:`_is_fact_typed_annotation`), scoped to *that* function
    (its own qualname, not its enclosing one -- a parameter belongs to the
    function it's declared on). Deliberately conservative in the
    over-approximating direction: an aliased name is trusted for the
    *whole* function once assigned anywhere in it, not narrowed to the
    lines after the assignment -- this check has no control-flow analysis,
    and a false positive here (flagging a comparison that happens to occur
    before the alias is actually set) is far cheaper than the false
    negative it prevents (missing the aliased comparison this exists to
    catch at all).
    """
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _is_fact_typed_expr(node.value)
        ):
            qualname = qualnames.get(node.lineno, "<module>")
            aliases.setdefault(qualname, set()).add(node.targets[0].id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = qualnames.get(node.lineno, "<module>")
            all_args = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
            for arg in all_args:
                if _is_fact_typed_annotation(arg.annotation):
                    aliases.setdefault(qualname, set()).add(arg.arg)
    return aliases


def fact_equality_misuse_sites(tree: ast.Module, rel: str) -> list[tuple[int, int]]:
    """Return one ``(lineno, col_offset)`` per `==`/`!=` comparison in *tree*
    where at least one side is recognizably `Fact[T]`-typed (see
    :func:`_is_fact_typed_expr`), including through a same-function local
    alias or an annotated parameter (see :func:`_fact_aliases`).

    A chained comparison (`a == b == c`) is walked pairwise -- `ast.Compare`
    stores `left`, `ops`, and `comparators` separately, not as a flat list
    of operands, so each adjacent pair is checked independently and a
    single chain can report more than one site.
    """
    qualnames = _enclosing_qualnames(tree)
    aliases = _fact_aliases(tree, qualnames)

    def is_fact_typed(node: ast.expr, qualname: str) -> bool:
        if _is_fact_typed_expr(node):
            return True
        return isinstance(node, ast.Name) and node.id in aliases.get(qualname, ())

    sites: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        qualname = qualnames.get(node.lineno, "<module>")
        operands = [node.left, *node.comparators]
        for op, left, right in zip(node.ops, operands, operands[1:]):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            if is_fact_typed(left, qualname) or is_fact_typed(right, qualname):
                sites.append((node.lineno, node.col_offset))
    return sites


def check_fact_detector_misuse(f: Findings) -> None:
    """ERROR on any `==`/`!=` comparison of a `Fact[T]`-typed value found
    under `abicheck/` -- see this module's own docstring for why this has
    no baseline: verified, by running this exact scan, to have zero
    existing hits in `abicheck/` today."""
    for path in sorted(PKG.rglob("*.py")):
        rel = _rel(path)
        source = _read(path)
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        for lineno, col in fact_equality_misuse_sites(tree, rel):
            f.err(
                "fact-detector-misuse",
                f"{rel}:{lineno}:{col}: `==`/`!=` compares a `Fact[T]` value "
                "directly -- unwrap via `.status` (and `.value` once "
                "`.is_present`) instead of comparing the wrapper itself "
                "(model/fact.py's own docstring; ADR-063 Phase 0)",
            )

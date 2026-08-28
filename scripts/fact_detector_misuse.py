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


def _imported_fact_aliases(tree: ast.Module) -> frozenset[str]:
    """Return every local name *tree* binds `Fact` under -- always includes
    the bare `"Fact"` itself, plus any `from ... import Fact as F` (Codex
    review, fresh evidence: `from abicheck.model.fact import Fact as F`
    then `F.present(a) == F.present(b)` is the identical misuse as `Fact.
    present(a) == Fact.present(b)`, invisible to a check that only
    recognizes the literal bare name `Fact`). Matched by imported name
    alone, not by source module -- the same name-only stance
    `_imported_class_aliases`/`_builtins_getattr_aliases` in `fact_field_
    readers.py` already take for their own import-alias resolution.
    """
    names = {"Fact"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "Fact" and alias.asname:
                    names.add(alias.asname)
    return frozenset(names)


def _is_fact_typed_expr(node: ast.expr, fact_names: frozenset[str]) -> bool:
    """True if *node* is recognizable, by name alone, as a `Fact[T]` value.

    Two shapes: a `<expr>.bases_fact`-style attribute access naming one of
    the five known `Fact[T]`-bridged fields, or a constructor call --
    `Fact(...)`, `Fact.present(...)`, `Fact.not_collected(...)`, etc. (any
    attribute call on a name in *fact_names* -- see
    :func:`_imported_fact_aliases` -- matching every classmethod
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
        if isinstance(func, ast.Name) and func.id in fact_names:
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id in fact_names
    return False


def _is_fact_typed_annotation(
    annotation: ast.expr | None, fact_names: frozenset[str]
) -> bool:
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
        return annotation.id in fact_names
    if isinstance(annotation, ast.Subscript):
        return _is_fact_typed_annotation(annotation.value, fact_names)
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


def _lexical_function_parents(tree: ast.Module) -> dict[str, str]:
    """Map each function's qualname to its nearest *enclosing function's*
    qualname -- skipping any intervening class scope -- or `"<module>"` if
    it has none. This is Python's real closure-scope chain: a method
    cannot close over its own class body's locals, but it (and any
    function nested inside it, class layers included) can still close
    over an enclosing *function's* locals right through an intervening
    class definition (Codex review, fresh evidence: `fact = rec.
    bases_fact` in an outer function, then `class C: def method(self):
    return fact == other`, still closes over `fact`).

    `_enclosing_qualnames`'s own dotted qualname is the wrong source for
    this: it includes a class layer as its own dot-segment (`"outer.C.
    method"`), so a purely string-based `rsplit` on it lands on the
    synthetic scope `"outer.C"` -- a scope no function actually owns, so
    it never gets processed or seeded and the chain silently breaks
    there. This walks the tree itself instead, tracking the *nearest
    enclosing function* separately from the dotted-name prefix.
    """
    parents: dict[str, str] = {}

    def visit(node: ast.AST, prefix: str, nearest_func: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                parents[qualname] = nearest_func
                visit(child, qualname + ".", qualname)
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", nearest_func)
            else:
                visit(child, prefix, nearest_func)

    visit(tree, "", "<module>")
    return parents


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

    **Chained aliases are resolved to a fixed point (Codex review, fresh
    evidence).** `first = rec.bases_fact; second = first; second == other`
    launders the misuse through a *second* ordinary assignment: `second`'s
    own RHS is the bare `ast.Name` `first`, which `_is_fact_typed_expr`
    doesn't recognize (it only recognizes an attribute access or a
    constructor call), so a single pass over assignments alone stops at
    `first` and never learns that `second` is an alias too. Fixed by
    collecting every simple single-target assignment as a `(name, value)`
    candidate per function first, then repeatedly resolving any candidate
    whose value is either directly Fact-typed *or* is itself a `Name`
    already known as an alias in that same function -- until a pass adds
    nothing new. Bounded by construction (each pass either adds at least
    one alias or the loop stops, and there are only finitely many
    candidates), so this always terminates.

    **An annotated assignment is a candidate too (Codex review, fresh
    evidence).** `old_fact: Fact[list[str]] = old.bases_fact` is an
    `ast.AnnAssign`, not an `ast.Assign` -- a distinct node type the
    original candidate collection didn't match at all. Its own annotation
    is also an unconditional signal on its own (mirroring the function-
    parameter case): `old_fact: Fact[list[str]]` with no meaningful value
    is Fact-typed regardless of what (if anything) resolves on the RHS.

    **Aliases propagate from an enclosing function into a nested one
    (Codex review, two rounds, fresh evidence both times).** `fact =
    rec.bases_fact` in an outer function, then `def inner(): return fact
    == other` -- `inner`'s own qualname (e.g. `"f.inner"`) has no
    assignment of its own establishing `fact`, so a lookup scoped
    strictly to that exact qualname misses it, even though `fact` is a
    real, visible closure variable there. Resolved by seeding each
    function's known-alias set with its lexical parent's *already-
    resolved* set before running the fixed point over its own candidates,
    processed in order of increasing scope-nesting depth so a parent is
    always resolved before its children consult it -- so a nested
    function's own reassignment of an inherited alias is caught too, not
    just a bare read of the outer name.

    A first version of this fix derived "lexical parent" from the dotted
    qualname alone (`rsplit(".", 1)`), which a second review round found
    wrong for a class *nested inside* a function: `fact = rec.bases_fact`
    in an outer function, then `class C: def method(self): return fact ==
    other` -- Python still closes `method` over `fact` right through the
    intervening class body, but the dotted qualname `"f.C.method"` splits
    to the synthetic parent `"f.C"`, a scope no real function owns, so it
    is never itself processed or seeded and the chain silently breaks
    there. Fixed by computing the true lexical parent from the tree
    directly (:func:`_lexical_function_parents`, which tracks the
    nearest *enclosing function*, skipping class layers, separately from
    the dotted-name prefix `_enclosing_qualnames` builds) instead of
    trying to reconstruct it from the qualname string.
    """
    fact_names = _imported_fact_aliases(tree)
    aliases: dict[str, set[str]] = {}
    candidates: dict[str, list[tuple[str, ast.expr]]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            qualname = qualnames.get(node.lineno, "<module>")
            candidates.setdefault(qualname, []).append((node.targets[0].id, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            qualname = qualnames.get(node.lineno, "<module>")
            if _is_fact_typed_annotation(node.annotation, fact_names):
                aliases.setdefault(qualname, set()).add(node.target.id)
            elif node.value is not None:
                candidates.setdefault(qualname, []).append((node.target.id, node.value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = qualnames.get(node.lineno, "<module>")
            all_args = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
            for arg in all_args:
                if _is_fact_typed_annotation(arg.annotation, fact_names):
                    aliases.setdefault(qualname, set()).add(arg.arg)

    lexical_parents = _lexical_function_parents(tree)

    def _scope_depth(qualname: str) -> int:
        depth = 0
        current = qualname
        while current in lexical_parents:
            current = lexical_parents[current]
            depth += 1
        return depth

    # Every scope actually in the tree, not just ones with a candidate or a
    # directly-annotated alias of their own -- otherwise a scope with
    # nothing but an inherited alias (e.g. `inner` in the closure example
    # above) never gets processed at all, and its lookup below silently
    # sees no entry rather than its parent's set.
    all_qualnames = (
        set(candidates) | set(aliases) | set(qualnames.values()) | {"<module>"}
    )
    for qualname in sorted(all_qualnames, key=_scope_depth):
        known = aliases.setdefault(qualname, set())
        parent = lexical_parents.get(qualname)
        if parent is not None:
            known |= aliases.get(parent, set())
        changed = True
        while changed:
            changed = False
            for name, value in candidates.get(qualname, []):
                if name in known:
                    continue
                if _is_fact_typed_expr(value, fact_names) or (
                    isinstance(value, ast.Name) and value.id in known
                ):
                    known.add(name)
                    changed = True
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
    fact_names = _imported_fact_aliases(tree)

    def is_fact_typed(node: ast.expr, qualname: str) -> bool:
        if _is_fact_typed_expr(node, fact_names):
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

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
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

# This script's own directory, so the sibling `fact_detector_misuse_scope`
# module below imports whether this file is run directly (Python adds its
# own directory automatically) or loaded as `scripts.fact_detector_misuse`
# by a test that never imported `check_ai_readiness.py` first (the only
# other thing in this tree that already inserts this same directory) --
# mirroring `check_ai_readiness.py`'s own identical sys.path guard for the
# identical reason.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from fact_detector_misuse_scope import (  # noqa: E402
    _bound_names,
    _def_containing_qualnames,
    _enclosing_qualnames,
    _lexical_function_parents,
    _match_pattern_names,
    _paired_unpacking_candidates,
    _qualname_at,
    _QualnameSpans,
)

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

    **Two known, documented gaps, both accepted rather than chased further
    (Codex review, fresh evidence, both after this project's own stated
    review-convergence point on this PR was reached -- see PR #929's
    convergence comment).** (1) This collects an alias *module-wide*
    regardless of which function the `ImportFrom` sits inside, so a
    (highly unusual) per-function `from ... import Fact as F` leaks its
    alias name into every other function in the module -- an unrelated
    sibling binding that happens to reuse the same short name with its own
    `.present()`-returning value, compared via `==`, would misfire. Fixing
    this needs `_imported_fact_aliases` threaded through the same
    per-scope machinery `_fact_aliases`/`_lexical_function_parents`
    already build for ordinary aliases, not a follow-up to this function.
    (2) This only ever recognizes `from ... import Fact as F` -- a
    module-qualified constructor call (`import abicheck.model.fact as
    fact_model; fact_model.Fact.present(...)`) is invisible, since
    `_is_fact_typed_expr`'s constructor-call branch assumes `func.value`
    is a bare `ast.Name`, not an arbitrary `ast.Attribute` chain. Both are
    the same "no type inference, match by import spelling" residual this
    module's own module docstring already accepts as inherent to a
    pure-AST heuristic, not a specific miss worth chasing indefinitely.
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

    An assignment expression (`(fact := rec.bases_fact) == other`,
    `ast.NamedExpr`) unwraps to its own `.value` -- the walrus expression
    itself evaluates to whatever its RHS does, so it is exactly as
    Fact-typed as that RHS, the same as unwrapping isn't needed for a
    plain `Attribute`/`Call` reaching this function directly (Codex
    review, fresh evidence: `_fact_aliases`'s own alias tracking is what
    used to be the only way this misuse was caught, and only when the
    walrus assignment was a full statement on its own -- an *inline*
    walrus used directly as a comparison operand had no assignment
    statement for that tracking to see at all).

    A conditional expression (`(old.bases_fact if condition else new.
    bases_fact) == other`, `ast.IfExp`) is recognized too, but only when
    *both* branches independently resolve as Fact-typed (Codex review,
    fresh evidence) -- unlike `NamedExpr`, which always evaluates to
    exactly one definite value, an `IfExp` genuinely produces one of two
    *different* values depending on `condition`, so only the case where
    both are guaranteed Fact-typed regardless of which branch actually
    runs is a real, unconditional Fact-typed result; `old.bases_fact if
    condition else some_other_call()` must NOT be recognized (`other`
    isn't reliably a Fact), the identical every-branch-must-agree
    principle the loop-target literal-collection fix elsewhere in this
    module already applies to a tuple's elements.
    """
    if isinstance(node, ast.Attribute) and node.attr in FACT_FIELD_NAMES:
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in fact_names:
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id in fact_names
    if isinstance(node, ast.NamedExpr):
        return _is_fact_typed_expr(node.value, fact_names)
    if isinstance(node, ast.IfExp):
        return _is_fact_typed_expr(node.body, fact_names) and _is_fact_typed_expr(
            node.orelse, fact_names
        )
    if isinstance(node, ast.BoolOp):
        # `old.bases_fact or new.bases_fact` -- Python's `and`/`or` always
        # return one of their own operands verbatim (never a synthesized
        # `True`/`False`, only ever short-circuiting to whichever operand
        # its own truthiness picks), so if *every* operand is guaranteed
        # Fact-typed, the result is too, regardless of which one is
        # actually selected at runtime -- the identical every-operand-
        # must-agree principle the `IfExp` branch above already applies
        # to its two branches, generalized to `BoolOp.values`' arbitrary
        # operand count (`a or b or c` chains to one `BoolOp` node with
        # three values, not two nested ones).
        return all(_is_fact_typed_expr(value, fact_names) for value in node.values)
    return False


def _candidate_resolves_to_fact(
    value: ast.expr, fact_names: frozenset[str], known: set[str]
) -> bool:
    """True if *value* is provably Fact-typed given *known* -- the
    already-resolved aliases for the scope this candidate belongs to.

    The scope-independent structural check alone
    (:func:`_is_fact_typed_expr`) can't answer this on its own for a
    conditional expression whose branches are themselves bare aliases
    (Codex review, fresh evidence): `old_fact = old.bases_fact; new_fact
    = new.bases_fact; fact = old_fact if cond else new_fact; fact ==
    other` is a real misuse -- both branches are aliases already
    confirmed Fact-typed by the surrounding fixed point -- but
    `_is_fact_typed_expr`'s own `IfExp` branch recurses only through
    itself, and it deliberately never resolves a bare `ast.Name` (that
    needs `known`, which it has no access to). This is the fixed-point-
    aware sibling every ordinary `candidates` entry already gets via its
    own inline `isinstance(value, ast.Name) and value.id in known` check
    -- generalized to recurse through an `IfExp`'s own two branches too,
    each independently required to resolve (structurally, or as an
    already-known alias, or itself another nested `IfExp`) before the
    conditional expression as a whole is trusted -- the identical
    every-branch-must-agree principle `tuple_loop_candidates`'s own
    conjunctive resolution already applies to a tuple's elements.
    """
    if _is_fact_typed_expr(value, fact_names):
        return True
    if isinstance(value, ast.Name):
        return value.id in known
    if isinstance(value, ast.IfExp):
        return _candidate_resolves_to_fact(
            value.body, fact_names, known
        ) and _candidate_resolves_to_fact(value.orelse, fact_names, known)
    if isinstance(value, ast.NamedExpr):
        return _candidate_resolves_to_fact(value.value, fact_names, known)
    if isinstance(value, ast.BoolOp):
        return all(
            _candidate_resolves_to_fact(operand, fact_names, known)
            for operand in value.values
        )
    return False


def _annotation_head_name(expr: ast.expr) -> str | None:
    """The bare identifier naming a subscript's own generic -- `Optional`
    for both `Optional[...]` and the module-qualified `typing.
    Optional[...]` spelling. Only used to recognize the `Optional`/`Union`
    wrapper shapes themselves; resolving `Fact` stays keyed on
    *fact_names*, as everywhere else in this module."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _is_fact_typed_annotation(
    annotation: ast.expr | None, fact_names: frozenset[str]
) -> bool:
    """True for a `Fact[...]` (or bare `Fact`) type annotation, by name
    alone -- `def f(old_fact: Fact[list[str]], ...)` is exactly as
    Fact-typed as `old.bases_fact` is, and a parameter carrying one is a
    real, common alias source (Codex review, fresh evidence): the caller
    already unwrapped nothing, so comparing two such parameters directly is
    the identical misuse this whole check exists to catch.

    Also recognizes `Fact[T]` wrapped in `Optional[...]`, `Union[...,
    None]`, or the `X | None` PEP 604 union spelling -- a detector helper
    commonly declares an optional Fact-typed parameter exactly this way,
    and the wrapper must not hide the misuse underneath it (Codex review,
    fresh evidence: `def f(value: Fact[int] | None, other): return value
    == other` produced no finding at all, since only a bare `Fact` or a
    subscript whose own value was `Fact` was ever recognized).
    """
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id in fact_names
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        # `Fact[int] | None` -- PEP 604 union syntax.
        return _is_fact_typed_annotation(
            annotation.left, fact_names
        ) or _is_fact_typed_annotation(annotation.right, fact_names)
    if isinstance(annotation, ast.Subscript):
        head = _annotation_head_name(annotation.value)
        if head == "Optional":
            return _is_fact_typed_annotation(annotation.slice, fact_names)
        if head == "Union":
            members = (
                annotation.slice.elts
                if isinstance(annotation.slice, ast.Tuple)
                else [annotation.slice]
            )
            return any(
                _is_fact_typed_annotation(member, fact_names) for member in members
            )
        if head == "Annotated":
            # `Annotated[Fact[int], metadata]` -- only the *first* slice
            # element is the real type; every following element is
            # arbitrary metadata (PEP 593), never itself a type to check.
            first = (
                annotation.slice.elts[0]
                if isinstance(annotation.slice, ast.Tuple)
                else annotation.slice
            )
            return _is_fact_typed_annotation(first, fact_names)
        return _is_fact_typed_annotation(annotation.value, fact_names)
    return False


def _fact_aliases(tree: ast.Module, qualnames: _QualnameSpans) -> dict[str, set[str]]:
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

    **A local rebinding shadows an inherited alias -- a real false
    *positive*, not a missed detection (Codex review, fresh evidence).**
    `fact = rec.bases_fact` in an outer function, then `def inner(fact,
    other): return fact == other` -- `inner`'s own `fact` parameter is an
    ordinary, unrelated local that merely reuses the outer name; Python's
    scoping rule makes a name bound *anywhere* in a function (a parameter,
    or an assignment target, regardless of line order) local to the
    *whole* function, shadowing an outer one throughout. Unconditionally
    inheriting the parent's alias set flagged this valid code as a hard
    CI error -- the one direction this module's "false positive is
    cheaper than false negative" stance does *not* cover, since a false
    positive here isn't a harmless extra review, it's rejecting correct
    code. Fixed by collecting every name each function binds on its own
    (every parameter, Fact-typed or not, and every simple assignment
    target) and excluding those from what it inherits from its lexical
    parent -- a genuine shadow is no longer treated as an alias, while an
    outer alias a function does *not* rebind still propagates in
    unchanged.
    """
    fact_names = _imported_fact_aliases(tree)
    # Computed up front (not after the walk below, as in an earlier
    # revision) so the walrus-target branch inside that same walk can
    # already use it to hop a comprehension-scoped walrus target out to
    # its real PEP 572 binding scope -- see that branch's own comment.
    lexical_parents = _lexical_function_parents(tree)
    # Position-keyed, not qualname-keyed -- see `_def_containing_qualnames`'s
    # own docstring for why a def/class statement's own start position can't
    # be resolved via `_qualname_at` the way every other binding site below
    # is (that position is always inside the node's *own* span first).
    def_containing = _def_containing_qualnames(tree)
    aliases: dict[str, set[str]] = {}
    candidates: dict[str, list[tuple[str, ast.expr]]] = {}
    # A `for`/comprehension loop target bound to every element of a
    # literal `Tuple`/`List` display needs a *conjunctive* fixed point --
    # the target is only reliably Fact-typed if EVERY element is, unlike
    # `candidates` above's ordinary *disjunctive* resolution (a name is
    # known the moment ANY one of its recorded values resolves). A bare
    # `ast.Name` element referencing an already-known alias (`old_fact =
    # old.bases_fact` outer, then `for fact in (old_fact,): ...`) can't be
    # confirmed Fact-typed at collection time -- `_is_fact_typed_expr()`
    # deliberately never resolves a bare name, since answering that needs
    # the very `known` set this fixed point builds (Codex review, fresh
    # evidence) -- so each such loop is recorded here, elements and all,
    # and re-checked every fixed-point pass alongside `candidates` below.
    #
    # **Each element carries its own resolution qualname, not just the
    # entry's (Codex review, fresh evidence).** For a plain `for` loop
    # this is always the same as the entry's own qualname (a `for`
    # statement introduces no scope of its own) -- but a comprehension's
    # *first* generator iterable evaluates in the *parent* scope, while
    # the resolved target name must still become known in the
    # *comprehension's own* scope (where the actual read happens). One
    # shared qualname per entry can't express both at once: `fact = rec.
    # bases_fact; [fact == other for fact in (fact,)]` needs the tuple
    # element `fact` checked against the *parent's* known aliases, but
    # the comprehension's own target `fact` becomes known in the
    # *comprehension's* own scope -- see the comprehension collection
    # branch's own docstring for the full reasoning.
    tuple_loop_candidates: dict[str, list[tuple[str, list[tuple[ast.expr, str]]]]] = {}
    # A candidate whose *target* binds in one scope but whose *value*
    # must resolve against a DIFFERENT scope -- so far, only a
    # comprehension-scope-hopping walrus (Codex review, fresh evidence):
    # `[(captured := fact) for fact in (rec.bases_fact,)]` binds
    # `captured` at the *enclosing* scope PEP 572 hops it out to (see the
    # `NamedExpr` collection branch's own docstring), but its RHS `fact`
    # is the comprehension's own loop target, only ever known within the
    # comprehension's own scope -- never the scope `captured` binds in.
    # Entries here are keyed by the *binding* qualname (the same outer
    # key `candidates` uses), each carrying its own separate value-
    # resolution qualname alongside the ordinary `(name, value)` pair --
    # the identical "one shared qualname per entry can't express both"
    # problem `tuple_loop_candidates`' own per-element qualname above
    # already solves, applied to a single scalar value instead of a
    # tuple's elements.
    cross_scope_candidates: dict[str, list[tuple[str, ast.expr, str]]] = {}
    # Every name *this* function binds on its own -- every parameter
    # (Fact-typed or not) and every simple assignment target -- used below
    # to stop an inherited alias from shadowing a genuine local rebinding
    # (Codex review, fresh evidence): `fact = rec.bases_fact` in an outer
    # function, then `def inner(fact, other): return fact == other`, where
    # `inner`'s own `fact` parameter is an ordinary, unrelated local that
    # merely reuses the name -- unconditionally inheriting the parent's
    # alias set would flag valid code as a false positive. Python's own
    # scoping rule is that *any* binding of a name anywhere in a function
    # (a parameter, or an assignment target, regardless of order) makes
    # that name local to the *whole* function, shadowing an outer one for
    # every line in it -- not narrowed to after the rebinding, the same
    # over-approximating-is-safe direction used everywhere else in this
    # module, just applied here to exclude a name rather than include one.
    locally_bound: dict[str, set[str]] = {}
    # Every name a function declares `global`/`nonlocal` -- excluded from
    # `locally_bound`'s shadowing subtraction below, since these are the
    # one real exception to Python's own "assignment anywhere makes a
    # name local to the whole function" rule (Codex review, fresh
    # evidence): `nonlocal fact` (or `global fact`) explicitly says this
    # name is *not* a new local at all, it's the identical outer/global
    # variable -- so a reassignment to it later in the same function
    # (`fact = 1`) does not shadow an inherited outer alias the way an
    # ordinary local rebinding would; a use anywhere in the function
    # (before or after that reassignment) can still genuinely see the
    # outer Fact-typed value. Not narrowed to "only before the
    # reassignment" -- the same over-approximating-is-safe direction this
    # whole module already takes, just applied in the opposite direction
    # from the shadowing fix's own (a false positive on a *later*, real
    # reassignment to a non-Fact value is the accepted cost, matching how
    # a shadow is never narrowed to "only after the rebinding" either).
    nonlocal_or_global: dict[str, set[str]] = {}
    # The `global`-declared subset of the above, tracked separately
    # (Codex review, fresh evidence): `nonlocal` and `global` both exempt a
    # name from ordinary shadowing (the set above), but they resolve
    # through completely different scope chains once exempted. `nonlocal
    # fact` genuinely means "the nearest *enclosing function's* own `fact`"
    # -- exactly what `lexical_parents[qualname]` already gives every other
    # inherited name, so ordinary inheritance is already correct for it.
    # `global fact` means "*module*-scope `fact`, full stop" -- it must
    # bypass every intervening function layer's own inheritance entirely,
    # even one that happens to have an unrelated alias of the identical
    # bare name (a first version of this fix routed `global` through the
    # same ordinary-inheritance path `nonlocal` uses, which is wrong in
    # both directions: a genuinely Fact-typed module-level `fact` shadowed
    # by an intervening function's own unrelated, non-Fact `fact` local
    # would be silently missed, and the reverse -- an intervening
    # function's own genuinely Fact-typed `fact` -- would be wrongly
    # attributed to an unrelated module-level name). Resolved directly
    # against `aliases["<module>"]` in the outer fixed-point loop below,
    # independent of `lexical_parents` altogether.
    global_declared: dict[str, set[str]] = {}
    # `(qualname, arg_name, default_expr)` -- a parameter default whose
    # Fact-typedness can't be decided during this same walk, since it
    # must be checked against its *enclosing* scope's alias set (where a
    # default expression is genuinely evaluated -- Python's own binding
    # rule), not the function's own (which has already had this exact
    # name excluded via the shadowing subtraction, since the parameter
    # itself is always in that scope's own `locally_bound`). Resolved in
    # a dedicated pass after the fixed point below has already stabilized
    # every scope's own alias set -- see that pass's own comment.
    pending_defaults: list[tuple[str, str, tuple[int, int], ast.expr]] = []
    # `id()` of every `ast.NamedExpr` found inside a parameter's own
    # default-value expression -- these are handled explicitly by the
    # `FunctionDef`/`AsyncFunctionDef`/`Lambda` branch below (registered
    # in the *enclosing* scope, matching Python's real default-evaluation
    # rule) and must be skipped by the generic, position-based `NamedExpr`
    # branch, which would otherwise misattribute one to the function being
    # *defined* -- see both branches' own comments for why.
    default_walrus_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for target in node.targets:
                for name in _bound_names(target):
                    locally_bound.setdefault(qualname, set()).add(name)
            # The alias-*candidate* pool (a specific value attributed to a
            # specific name, fed through the fixed point below) covers
            # every plain-`Name` target, not only a lone one -- a chained
            # assignment (`first = second = rec.bases_fact`) gives every
            # target the identical RHS value, unlike a tuple-unpacking
            # target (`a, b = pair`), which has no single value to
            # attribute to `a` alone (Codex review, fresh evidence: the
            # single-target restriction wrongly excluded this ordinary,
            # unrelated shape too, letting `first == other`/`second ==
            # other` both bypass the gate). A tuple/list target among
            # `node.targets` still contributes nothing here -- only
            # `locally_bound`, via `_bound_names` above -- since it has no
            # single value of its own either -- *unless* the RHS is
            # itself a literal `Tuple`/`List` display of the identical
            # length, in which case each element genuinely does have its
            # own value (Codex review, fresh evidence: `old_fact, new_fact
            # = old.bases_fact, new.bases_fact` then `old_fact ==
            # new_fact` is an ordinary detector refactor of two
            # independent Fact-typed values, bypassing the gate entirely
            # under the plain-`Name`-only restriction) -- see
            # `_paired_unpacking_candidates()`'s own docstring for exactly
            # which shapes this covers.
            for target in node.targets:
                if isinstance(target, ast.Name):
                    candidates.setdefault(qualname, []).append((target.id, node.value))
                elif isinstance(target, (ast.Tuple, ast.List)):
                    candidates.setdefault(qualname, []).extend(
                        _paired_unpacking_candidates(target, node.value)
                    )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            locally_bound.setdefault(qualname, set()).add(node.target.id)
            if _is_fact_typed_annotation(node.annotation, fact_names):
                aliases.setdefault(qualname, set()).add(node.target.id)
            elif node.value is not None:
                candidates.setdefault(qualname, []).append((node.target.id, node.value))
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            if id(node) in default_walrus_ids:
                # Already handled by the `FunctionDef`/`AsyncFunctionDef`/
                # `Lambda` branch below, registered in the *enclosing*
                # scope -- this generic, position-based path would
                # otherwise misattribute it to the function being defined
                # (see that branch's own comment for why).
                continue
            # `(fact := rec.bases_fact)` -- a real alias binding too, not
            # merely an inline-recognizable expression (see `_is_fact_
            # typed_expr`'s own NamedExpr branch for that half): `fact`
            # itself becomes usable later in the same scope, e.g. `if
            # (fact := rec.bases_fact) is not None: return fact == other`
            # (Codex review, fresh evidence).
            #
            # **Bound to the scope PEP 572 actually assigns it to, not
            # simply wherever it's lexically written (Codex review, fresh
            # evidence, second round on this same branch).** Outside a
            # comprehension, a walrus target binds to its immediately
            # enclosing scope exactly like an ordinary assignment does --
            # the first revision of this branch treated *every* walrus as
            # exempt from `locally_bound`, on the theory that its PEP 572
            # scope-hopping rule always applies; that rule is real, but it
            # only fires when the walrus sits *directly inside a
            # comprehension*, and skipping `locally_bound` unconditionally
            # meant a nested function's own `(fact := 1)` -- an ordinary,
            # unrelated local rebinding, no comprehension involved at all
            # -- failed to shadow a genuine outer alias of the same name,
            # a real false positive. Fixed by hopping the *comprehension*
            # case out to its real binding scope (the nearest enclosing
            # non-comprehension scope, walking `lexical_parents` -- a
            # walrus can sit inside several nested comprehensions at once,
            # and PEP 572 hops out of all of them, not just the innermost)
            # while treating every other case as an ordinary local
            # binding, added to `locally_bound` the same as any other
            # assignment target.
            walrus_qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            binding_qualname = walrus_qualname
            while binding_qualname.rsplit(".", 1)[-1].startswith("<comp>#"):
                binding_qualname = lexical_parents.get(binding_qualname, "<module>")
            # A genuine local binding at `binding_qualname` either way --
            # whether that's the walrus's own lexical scope (no hop) or the
            # scope PEP 572 actually hopped it out to (Codex review, fresh
            # evidence: the `binding_qualname == walrus_qualname` guard here
            # wrongly skipped this mark whenever a real hop occurred, even
            # though this branch's own comment above already states the
            # intent -- "every other case" gets the identical `locally_
            # bound` treatment. `[(fact := x) for x in values]` directly
            # inside `inner` binds `fact` as an ordinary local *of `inner`*
            # under PEP 572, shadowing an outer `fact` alias for a later,
            # real `fact == other` read in `inner` -- but with the mark
            # skipped, `inner`'s own inheritance step never learned this,
            # and the outer alias leaked straight through instead).
            locally_bound.setdefault(binding_qualname, set()).add(node.target.id)
            # No hop -- the walrus's own value resolves in the identical
            # scope it binds in, the ordinary `candidates` case. A real
            # hop means the value must still resolve against the
            # comprehension's own `walrus_qualname` (where it's actually
            # written and where any comprehension-local alias, e.g. its
            # own `for` target, becomes known), even though the target
            # name itself becomes known at `binding_qualname` instead
            # (Codex review, fresh evidence: `[(captured := fact) for
            # fact in (rec.bases_fact,)]; captured == other` was missed,
            # since the pre-fix code resolved `fact` against
            # `binding_qualname`'s own aliases, where the comprehension's
            # `for`-bound `fact` was never recorded at all).
            if binding_qualname == walrus_qualname:
                candidates.setdefault(binding_qualname, []).append(
                    (node.target.id, node.value)
                )
            else:
                cross_scope_candidates.setdefault(binding_qualname, []).append(
                    (node.target.id, node.value, walrus_qualname)
                )
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            # `for fact, other in pairs:` -- the same tuple-unpacking
            # binding as `ast.Assign`, just via a loop target instead
            # (Codex review, fresh evidence: "the other Python binding
            # forms" alongside unpacking).
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for name in _bound_names(node.target):
                locally_bound.setdefault(qualname, set()).add(name)
            # `for fact in (old.bases_fact, new.bases_fact): return fact ==
            # other` -- a single loop target bound, one iteration at a
            # time, to every element of a literal `Tuple`/`List` display
            # (Codex review, fresh evidence). Unlike `_paired_unpacking_
            # candidates` (which pairs *distinct* targets to distinct RHS
            # elements in one assignment), every iteration reuses the
            # *same* target name -- so the alias only holds if *every*
            # element is definitively Fact-typed, not merely one of them
            # (`for x in (rec.bases_fact, some_other_call()):` must stay
            # unflagged, since `x` is only sometimes a Fact).
            #
            # **A bare-`Name` element referencing an already-known alias
            # is a real Fact-typed element too, resolved through the
            # fixed point below rather than at collection time (Codex
            # review, fresh evidence).** `old_fact = old.bases_fact`
            # outer, then `for fact in (old_fact,): fact == other` --
            # `_is_fact_typed_expr()` deliberately never resolves a bare
            # name (that needs `known`, which doesn't exist yet during
            # this single collection pass), so an all-elements check
            # gated on it alone rejected this ordinary alias refactor.
            # Every element satisfying either check (structurally
            # Fact-typed, or a bare name at all -- whether it actually
            # resolves is `tuple_loop_candidates`' own job at fixed-point
            # time, below) is enough to register the whole loop; an
            # element that is neither still disqualifies it outright, the
            # identical conservative behavior as before.
            if (
                isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List))
                and node.iter.elts
                and all(
                    _is_fact_typed_expr(elt, fact_names) or isinstance(elt, ast.Name)
                    for elt in node.iter.elts
                )
            ):
                tuple_loop_candidates.setdefault(qualname, []).append(
                    (node.target.id, [(elt, qualname) for elt in node.iter.elts])
                )
            # `for fact, tag in ((old.bases_fact, "old"), (new.bases_fact,
            # "new")): return fact == other` -- the tuple-*unpacking*
            # sibling of the case just above: the loop target is itself a
            # `Tuple`/`List` display, destructured one iteration at a
            # time against each element of the iterable, which must
            # *also* be a literal `Tuple`/`List` display for any single
            # sub-value to be identifiable at all (Codex review, fresh
            # evidence). Reuses `_paired_unpacking_candidates()` -- the
            # identical elementwise pairing `ast.Assign`'s own unpacking
            # handling already relies on -- once per iteration element,
            # since each is exactly the "one assignment's worth" of value
            # that function already knows how to pair against the
            # (unchanging, one per loop) target shape; a starred target,
            # a length mismatch, or an iteration element that isn't
            # itself a literal display makes that call return `[]` for
            # that element, and any single failure disqualifies the
            # *whole* loop (bailing via `all_iterations_paired`) rather
            # than silently pairing only some iterations -- the identical
            # "no candidates at all over a partial, best-effort pairing"
            # principle `_paired_unpacking_candidates()`'s own docstring
            # already states, extended across iterations instead of
            # within one. Once every iteration pairs successfully, each
            # target name's own per-iteration values are collected and
            # registered together, subject to the identical
            # every-element-Fact-typed-or-deferred-name conjunctive
            # requirement the simple-target case above already applies.
            elif isinstance(node.target, (ast.Tuple, ast.List)) and isinstance(
                node.iter, (ast.Tuple, ast.List)
            ):
                per_name_elements: dict[str, list[ast.expr]] = {}
                all_iterations_paired = bool(node.iter.elts)
                for iteration_elt in node.iter.elts:
                    pairs = _paired_unpacking_candidates(node.target, iteration_elt)
                    if not pairs:
                        all_iterations_paired = False
                        break
                    for name, value in pairs:
                        per_name_elements.setdefault(name, []).append(value)
                if all_iterations_paired:
                    for name, raw_elts in per_name_elements.items():
                        if all(
                            _is_fact_typed_expr(elt, fact_names)
                            or isinstance(elt, ast.Name)
                            for elt in raw_elts
                        ):
                            tuple_loop_candidates.setdefault(qualname, []).append(
                                (name, [(elt, qualname) for elt in raw_elts])
                            )
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            # `with ctx() as fact:` -- likewise a real local binding.
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _bound_names(item.optional_vars):
                        locally_bound.setdefault(qualname, set()).add(name)
        elif isinstance(node, ast.ExceptHandler):
            # `except SomeError as fact:` -- `node.name` is a bare `str`,
            # not an `ast.Name` (Python's own grammar for this one binding
            # form), so it doesn't go through `_bound_names`.
            if node.name is not None:
                qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
                locally_bound.setdefault(qualname, set()).add(node.name)
        elif isinstance(node, ast.Match):
            # `case fact:`/`case [*rest]:`/`case {**rest}:` -- a real
            # local binding too, the same as any other capture form above
            # (Codex review, fresh evidence). `match`/`case` introduces no
            # scope of its own in Python, so every case's own captures are
            # attributed to the `match` statement's own position.
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for case in node.cases:
                for name in _match_pattern_names(case.pattern):
                    locally_bound.setdefault(qualname, set()).add(name)
                # `case fact:` (a bare capture, matching -- and binding --
                # the *entire* subject unconditionally) and `case
                # SomeClass() as fact:` (an `as`-pattern, binding the
                # entire subject whenever its own sub-pattern matches)
                # both make `fact` a real alias of `node.subject` itself,
                # not merely an arbitrary local shadow (Codex review,
                # fresh evidence): `match rec.bases_fact: case fact:
                # return fact == other` is a real misuse. Both shapes are
                # exactly `case.pattern` itself being an `ast.MatchAs`
                # with a real `name` -- Python's own grammar for a
                # top-level capture/`as`-pattern -- as opposed to a
                # *nested* capture inside a larger structural pattern
                # (`case [x, y as fact]:`), which only captures a
                # *sub*-part of the subject, not the whole thing, and is
                # deliberately not treated as an alias of `node.subject`
                # here.
                if (
                    isinstance(case.pattern, ast.MatchAs)
                    and case.pattern.name is not None
                ):
                    candidates.setdefault(qualname, []).append(
                        (case.pattern.name, node.subject)
                    )
                elif isinstance(case.pattern, ast.MatchOr) and case.pattern.patterns:
                    # `case (C() as fact) | (D() as fact): fact == other`
                    # -- an OR pattern where *every* alternative is itself
                    # a top-level `MatchAs` capturing the whole subject
                    # under the identical name (Codex review, fresh
                    # evidence). Python requires every alternative of an
                    # OR pattern to bind the same set of names (a
                    # SyntaxError otherwise), but not the same *shape* of
                    # binding -- `case (C(x=fact)) | (D() as fact):`
                    # legally binds the name `fact` in both branches, but
                    # only the second branch binds it to the *whole*
                    # subject, so this alone is not safe to trust as an
                    # alias. Only when every single alternative is itself
                    # exactly `MatchAs` with the same name is `fact`
                    # guaranteed to be the raw subject regardless of which
                    # alternative matched.
                    alternatives = case.pattern.patterns
                    first_alt = alternatives[0]
                    if (
                        isinstance(first_alt, ast.MatchAs)
                        and first_alt.name is not None
                        and all(
                            isinstance(alt, ast.MatchAs) and alt.name == first_alt.name
                            for alt in alternatives
                        )
                    ):
                        candidates.setdefault(qualname, []).append(
                            (first_alt.name, node.subject)
                        )
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # `import json as fact` / `from pkg import item as fact` --
            # a real local binding too, the identical shadowing shape as
            # any other assignment form above (Codex review, fresh
            # evidence: this collector had no branch for either import
            # statement at all, so a nested function's own import-bound
            # `fact` never shadowed an outer Fact alias). A bare `import
            # a.b.c` (no `as`) binds only the top-level package name `a`
            # in the importing scope -- Python's own import-binding rule
            # -- so an unaliased dotted name is split on its first `.`
            # rather than used whole.
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", 1)[0]
                locally_bound.setdefault(qualname, set()).add(bound_name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            nonlocal_or_global.setdefault(qualname, set()).update(node.names)
            if isinstance(node, ast.Global):
                global_declared.setdefault(qualname, set()).update(node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A `def`/`class` statement binds its own *name* in the
            # containing scope, exactly like an ordinary assignment target
            # (Codex review, fresh evidence): `def fact(): ...` then `fact
            # == other` in the same scope resolves `fact` to the function
            # object just defined, an ordinary local -- Python's own
            # `LOAD_FAST`/`LOAD_NAME` semantics, not the outer Fact alias.
            # The branch below already records this node's own *nested*
            # scope (its parameters, in `qualname`'s own `locally_bound`
            # entry) -- but never the definition's *name* in the scope
            # that contains it, so an outer alias of the same name was
            # never shadowed, a real false positive. A lambda has no name
            # of its own to bind (it's an expression, not a statement), so
            # it's excluded from this specific registration -- unlike the
            # branch below, which still applies to it identically.
            containing_qualname = def_containing.get(
                (node.lineno, node.col_offset), "<module>"
            )
            locally_bound.setdefault(containing_qualname, set()).add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # `ast.Lambda` shares the identical `.args: ast.arguments`
            # shape a `def` has (Codex review, fresh evidence -- see
            # `_enclosing_qualnames`'s own docstring): a lambda parameter
            # can never carry an annotation (`arg.annotation` is always
            # `None`), so `_is_fact_typed_annotation` correctly never
            # matches one -- only the `locally_bound` recording actually
            # matters for a lambda, but sharing this branch rather than
            # duplicating it costs nothing.
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            all_args = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *((node.args.vararg,) if node.args.vararg else ()),
                *((node.args.kwarg,) if node.args.kwarg else ()),
            )
            for arg in all_args:
                locally_bound.setdefault(qualname, set()).add(arg.arg)
                if _is_fact_typed_annotation(arg.annotation, fact_names):
                    aliases.setdefault(qualname, set()).add(arg.arg)
            # A parameter's own *default value* -- evaluated once, in the
            # enclosing scope, at `def`/lambda time -- can itself be
            # Fact-typed (Codex review, fresh evidence): `fact = rec.
            # bases_fact; def inner(fact=fact): return fact == other` --
            # calling `inner()` with no override genuinely runs the
            # comparison against that outer Fact value, but the parameter
            # is already unconditionally excluded from the inherited
            # alias set (`locally_bound`, just above) regardless of what
            # its own default is. Positional defaults right-align against
            # `posonlyargs + args` (the last `len(defaults)` of them);
            # `kw_defaults` pairs positionally with `kwonlyargs`, `None`
            # for a keyword-only parameter with no default at all.
            positional = (*node.args.posonlyargs, *node.args.args)
            offset = len(positional) - len(node.args.defaults)
            for arg, default in zip(positional[offset:], node.args.defaults):
                pending_defaults.append(
                    (qualname, arg.arg, (node.lineno, node.col_offset), default)
                )
            for arg, kw_default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if kw_default is not None:
                    pending_defaults.append(
                        (
                            qualname,
                            arg.arg,
                            (node.lineno, node.col_offset),
                            kw_default,
                        )
                    )
            # A walrus *inside* a default expression -- `def inner(x=(fact
            # := rec.bases_fact)):` -- binds `fact` in the scope the
            # default is evaluated in too, which is the scope that
            # directly, syntactically contains the `def`/lambda (Python's
            # own default-evaluation rule, the same one the pending-
            # defaults handling above already relies on), not `inner`'s
            # own body scope (Codex review, fresh evidence): the generic,
            # position-based `NamedExpr` branch would otherwise attribute
            # it to `inner` -- the smallest span containing the walrus's
            # own position, since a default expression is textually part
            # of the `def`/lambda's own span -- silently losing an alias a
            # later, genuinely outer `fact == other` needs. Registered
            # directly in the containing scope (`def_containing`, computed
            # once up front for exactly this kind of use) as an ordinary
            # local binding, and excluded from the generic branch via
            # `default_walrus_ids` so it isn't also (mis)processed there.
            #
            # **`def_containing`, not `lexical_parents` (Codex review,
            # fresh evidence).** A *method's* own default-embedded walrus
            # is evaluated while its containing *class body* executes,
            # ordinary class-body code, not a closure lookup --
            # `lexical_parents` intentionally skips that class layer for
            # the different question of a method *body*'s own free-
            # variable lookup, the identical class-skipping issue the
            # sibling `pending_defaults`/`_default_and_annotation_scope_
            # overrides()` fixes already had to make for the same reason.
            # `class C: fact = 1; def f(self, x=(fact := rec.bases_fact)):
            # ...` must publish `fact` to `C`'s own class-body scope, not
            # skip past it to whatever encloses `C`.
            #
            # **Stops at a nested scope boundary, via `_iter_default_
            # subtree()` (Codex review, fresh evidence).** A default
            # containing its own lambda/comprehension -- `def configure(cb
            # =lambda: (fact := rec.bases_fact)): ...` -- only *creates*
            # the lambda object at def-time in the enclosing scope; the
            # walrus inside its body binds `fact` in the *lambda's own*
            # scope when the lambda is later called, never the enclosing
            # one, the identical distinction `_default_and_annotation_
            # scope_overrides()` already draws for a `Compare` found the
            # same way. An unrestricted `ast.walk(default_expr)` crossed
            # that boundary too, wrongly publishing the lambda-local walrus
            # target as an alias of the *enclosing* (here, module) scope.
            #
            # **A parameter annotation or return annotation is walked the
            # identical way (CodeRabbit review, fresh evidence).** This
            # loop only ever walked `node.args.defaults`/`kw_defaults` --
            # but a walrus in a parameter's own annotation or the `->`
            # return annotation binds at the identical def-time, in the
            # identical containing scope, absent `from __future__ import
            # annotations` (which this module's own sibling override
            # function, `_default_and_annotation_scope_overrides()`,
            # already treats every annotation as evaluated under
            # regardless of postponed-evaluation status -- unconditionally
            # walking here too matches that established, deliberately
            # conservative choice rather than adding a second, narrower
            # rule: a walrus that in fact never executes under postponed
            # evaluation registering a spurious alias is a false positive,
            # the safe direction this whole module already accepts
            # throughout). `subtrees` mirrors that sibling function's own
            # construction exactly, so the two can't independently drift.
            enclosing = def_containing.get((node.lineno, node.col_offset), "<module>")
            walrus_subtrees = [*node.args.defaults, *node.args.kw_defaults]
            for arg in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *((node.args.vararg,) if node.args.vararg else ()),
                *((node.args.kwarg,) if node.args.kwarg else ()),
            ):
                if arg.annotation is not None:
                    walrus_subtrees.append(arg.annotation)
            walrus_returns = getattr(node, "returns", None)
            if walrus_returns is not None:
                walrus_subtrees.append(walrus_returns)
            for default_expr in walrus_subtrees:
                if default_expr is None:
                    continue
                for walrus in _iter_default_subtree(default_expr):
                    if isinstance(walrus, ast.NamedExpr) and isinstance(
                        walrus.target, ast.Name
                    ):
                        default_walrus_ids.add(id(walrus))
                        locally_bound.setdefault(enclosing, set()).add(walrus.target.id)
                        candidates.setdefault(enclosing, []).append(
                            (walrus.target.id, walrus.value)
                        )
        elif isinstance(node, ast.ClassDef):
            # A walrus inside a class base or metaclass keyword --
            # `class C(make_base(fact := rec.vtable_fact)): ...` -- binds
            # `fact` in the scope containing the `class` statement, the
            # identical PEP 572-independent evaluation-time rule the
            # `FunctionDef`/`AsyncFunctionDef`/`Lambda` branch above
            # already applies to a default expression (Codex review,
            # fresh evidence): a class header executes while the scope
            # *containing* the class statement is active, before the
            # new class's own body scope even exists, so the generic,
            # position-based `NamedExpr` branch's `class-body` attribution
            # -- correct for a walrus inside the body itself -- is wrong
            # here, silently losing an alias a later, genuinely outer
            # `fact == other` needs. `_default_and_annotation_scope_
            # overrides()` already draws this identical distinction for
            # the *read* side (`class Outer: fact = rec.bases_fact; class
            # Inner(make_base(fact == other)): ...`); this is its sibling
            # fix for a *binding* found the same way. Uses the same
            # `def_containing`/`_iter_default_subtree` machinery -- a
            # base/keyword containing its own lambda/comprehension stops
            # at that boundary identically, and the walrus is excluded
            # from the generic branch via `default_walrus_ids` so it
            # isn't also (mis)processed there.
            enclosing = def_containing.get((node.lineno, node.col_offset), "<module>")
            for base_or_keyword in (*node.bases, *(kw.value for kw in node.keywords)):
                for walrus in _iter_default_subtree(base_or_keyword):
                    if isinstance(walrus, ast.NamedExpr) and isinstance(
                        walrus.target, ast.Name
                    ):
                        default_walrus_ids.add(id(walrus))
                        locally_bound.setdefault(enclosing, set()).add(walrus.target.id)
                        candidates.setdefault(enclosing, []).append(
                            (walrus.target.id, walrus.value)
                        )
        elif isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            # A comprehension's own `for` target(s) -- real local bindings
            # scoped to the comprehension itself, the identical shape a
            # `for` loop's own target already gets above (Codex review,
            # fresh evidence -- see `_enclosing_qualnames`'s own
            # docstring).
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for gen_index, generator in enumerate(node.generators):
                for name in _bound_names(generator.target):
                    locally_bound.setdefault(qualname, set()).add(name)
                # The comprehension equivalent of the `for`-loop literal-
                # collection case above -- `[fact == other for fact in
                # (old.bases_fact, new.bases_fact)]` -- the identical
                # every-element-Fact-typed-or-a-deferred-alias requirement
                # (see the `for`/`AsyncFor` branch's own docstring for the
                # bare-`Name`-element reasoning).
                #
                # **Each element resolves against the scope the iterable
                # actually evaluates in, distinct from the scope the
                # resolved target name itself becomes known in (Codex
                # review, fresh evidence).** The target always becomes
                # known in the comprehension's own scope (`qualname`) --
                # that's where the actual read happens -- but only the
                # *first* generator's own iterable evaluates in the scope
                # directly containing the comprehension; every other
                # generator's iterable, like the element expression, runs
                # inside the comprehension's own new scope, where the
                # target(s) from every earlier generator are already
                # locally bound (see `_enclosing_qualnames`'s own
                # docstring for the identical distinction). `fact = rec.
                # bases_fact; [fact == other for fact in (fact,)]` -- the
                # tuple element `fact` names the *outer* alias, but the
                # comprehension's own target is *also* `fact`, shadowing
                # it in the comprehension's own scope -- checking that
                # element against the comprehension's own (shadowed)
                # scope would check the name against itself and never
                # resolve, while registering the *entry* under the
                # parent instead would make the target known in the wrong
                # scope for the actual read to see. `tuple_loop_
                # candidates`' own per-element `(expr, qualname)` pairing
                # (see its own declaration comment) exists for exactly
                # this split. `_qualname_at()` on the first generator's
                # own iterable position picks up the narrower override
                # span `_enclosing_qualnames()` already registers for
                # exactly this iterable, tagged with the comprehension's
                # own *incoming* (parent) qualname.
                elt_qualname = (
                    _qualname_at(
                        (generator.iter.lineno, generator.iter.col_offset), qualnames
                    )
                    if gen_index == 0
                    else qualname
                )
                if (
                    isinstance(generator.target, ast.Name)
                    and isinstance(generator.iter, (ast.Tuple, ast.List))
                    and generator.iter.elts
                    and all(
                        _is_fact_typed_expr(elt, fact_names)
                        or isinstance(elt, ast.Name)
                        for elt in generator.iter.elts
                    )
                ):
                    tuple_loop_candidates.setdefault(qualname, []).append(
                        (
                            generator.target.id,
                            [(elt, elt_qualname) for elt in generator.iter.elts],
                        )
                    )
                # The tuple-*unpacking* sibling of the case just above --
                # the identical shape the `for`/`AsyncFor` branch already
                # handles for a plain `for` loop, applied to a
                # comprehension's own generator (Codex review, fresh
                # evidence): `[fact == other for fact, tag in
                # ((old.bases_fact, "old"),)]` was invisible, since this
                # branch only ever matched a bare `ast.Name` target.
                elif isinstance(generator.target, (ast.Tuple, ast.List)) and isinstance(
                    generator.iter, (ast.Tuple, ast.List)
                ):
                    comp_per_name_elements: dict[str, list[ast.expr]] = {}
                    all_iterations_paired = bool(generator.iter.elts)
                    for iteration_elt in generator.iter.elts:
                        pairs = _paired_unpacking_candidates(
                            generator.target, iteration_elt
                        )
                        if not pairs:
                            all_iterations_paired = False
                            break
                        for pname, pvalue in pairs:
                            comp_per_name_elements.setdefault(pname, []).append(pvalue)
                    if all_iterations_paired:
                        for pname, pelts in comp_per_name_elements.items():
                            if all(
                                _is_fact_typed_expr(pelt, fact_names)
                                or isinstance(pelt, ast.Name)
                                for pelt in pelts
                            ):
                                tuple_loop_candidates.setdefault(qualname, []).append(
                                    (
                                        pname,
                                        [(pelt, elt_qualname) for pelt in pelts],
                                    )
                                )

    # A `global`/`nonlocal`-declared name is never a real local rebinding
    # -- exclude it from the shadowing subtraction now that every
    # ordinary binding has been collected (see `nonlocal_or_global`'s own
    # declaration above for why).
    for qualname, declared in nonlocal_or_global.items():
        if qualname in locally_bound:
            locally_bound[qualname] -= declared

    def _declared_target_scope(qualname: str, name: str) -> str:
        """Where an *assignment* to `name`, written inside `qualname`,
        actually writes -- `<module>` for a `global`-declared name, the
        nearest enclosing function for a `nonlocal`-declared one, or
        `qualname` itself for an ordinary local (Codex review, fresh
        evidence): a function that declares `global fact` and then
        assigns `fact = rec.bases_fact` genuinely writes *module*-scope
        `fact`, not a local of its own -- symmetric to the read-side fix
        the two `global`/`nonlocal` findings above already made (a
        declared name's own *read* correctly resolves through module/
        enclosing-function scope now), but the *write* side was still
        missing: every candidate/alias this assignment produces was still
        being recorded under the writer's own qualname, so a sibling
        function reading the identical module/enclosing-function name
        (through ordinary inheritance, not its own `global`/`nonlocal`
        declaration) never saw it as Fact-typed at all.
        """
        if name in global_declared.get(qualname, ()):
            return "<module>"
        if name in nonlocal_or_global.get(qualname, ()):
            # `nonlocal` can skip *multiple* enclosing functions, not just
            # the immediate lexical parent (Codex review, fresh evidence):
            # Python resolves it to the nearest enclosing function scope
            # that actually binds the name itself, walking outward past
            # any intervening function that doesn't -- `outer` binds
            # `fact`, `middle` (nested in `outer`) never touches it at
            # all, `setter` (nested in `middle`) does `nonlocal fact;
            # fact = rec.bases_fact` -- this genuinely writes `outer`'s
            # `fact`, skipping `middle` entirely, but the previous,
            # immediate-parent-only routing published the write to
            # `middle` instead, where nothing reads it. `locally_bound`
            # is exactly the right test at each step: it already excludes
            # a name an ancestor itself only holds via its *own*
            # `nonlocal`/`global` declaration (the shadowing-exemption
            # subtraction above), so this walk naturally continues past
            # an ancestor whose own binding of the name is itself
            # borrowed from further out, the identical case Python's own
            # resolution skips.
            candidate = lexical_parents.get(qualname, "<module>")
            while candidate != "<module>" and name not in locally_bound.get(
                candidate, ()
            ):
                candidate = lexical_parents.get(candidate, "<module>")
            return candidate
        return qualname

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
        set(candidates)
        | set(aliases)
        | {q for _start, _end, q in qualnames}
        | {"<module>"}
    )
    # Both passes below -- depth-ordered parent inheritance (plus each
    # scope's own candidate fixed point) and pending-default resolution
    # -- are wrapped in one further, *outer* fixed point (Codex review,
    # fresh evidence): `def inner(fact=rec.bases_fact): def nested():
    # return fact == other` needs `nested` to inherit `fact` from
    # `inner`, but `inner` only gains `fact` from resolving its own
    # *default* -- and the default-resolution pass deliberately runs
    # *after* the depth-ordered inheritance pass (it needs each scope's
    # alias set already final before checking a default against it), so
    # a single top-to-bottom run processes `nested`'s inheritance before
    # `inner`'s own default has been resolved at all, silently missing
    # the propagation. Re-running both passes together until neither
    # changes anything converges correctly: the second pass through
    # inheritance sees `inner`'s now-resolved `fact` and propagates it to
    # `nested` exactly the same way an ordinary parent alias already
    # would. Guaranteed to terminate -- every alias set only ever grows,
    # over a finite universe of (qualname, name) pairs.
    outer_changed = True
    while outer_changed:
        outer_changed = False
        for qualname in sorted(all_qualnames, key=_scope_depth):
            known = aliases.setdefault(qualname, set())
            parent = lexical_parents.get(qualname)
            global_names = global_declared.get(qualname, set())
            if parent is not None:
                # A class body's own top-level statements use `LOAD_NAME`/
                # `STORE_NAME`, not `LOAD_FAST` -- resolved dynamically at
                # each statement against whatever the class namespace holds
                # *so far*, not statically pre-determined by "is this name
                # assigned anywhere in this class body" the way a function
                # body's `LOAD_FAST` is (Codex review, fresh evidence):
                # `fact = rec.bases_fact` outer, then `class C: hit = fact
                # == other; fact = 1` -- the later `fact = 1` reassignment
                # is a real class-body-local rebinding, but the READ on the
                # line before it still resolves to the outer alias in real
                # Python, since the class namespace has nothing under
                # `fact` yet at that point. This module has no statement-
                # order-aware lookup (every other scope's shadowing is
                # correctly whole-scope, per `locally_bound`'s own
                # docstring -- only a class body's `LOAD_NAME` genuinely
                # differs), so the conservative, over-approximating-is-safe
                # answer here is to never let a class body's own local
                # rebinding shadow what it inherits at all -- the identical
                # direction `locally_bound`'s own docstring already argues
                # for the opposite case (nonlocal/global): a false positive
                # on a class body that reassigns `fact` to something
                # ordinary *before* using it is the accepted cost, exactly
                # matching how a shadow is never narrowed to "only after
                # the rebinding" for a function either.
                shadowed = (
                    set()
                    if qualname.endswith("<class-body>")
                    else locally_bound.get(qualname, set())
                )
                inherited = (aliases.get(parent, set()) - shadowed) - global_names
                if not inherited <= known:
                    outer_changed = True
                known |= inherited
            if global_names:
                # `global fact` bypasses `lexical_parents` entirely --
                # Python's own rule is "the identical module-scope `fact`,
                # regardless of what any enclosing function does with the
                # same bare name" (see `global_declared`'s own declaration
                # comment for the two-directional bug this closes).
                module_inherited = aliases.get("<module>", set()) & global_names
                if not module_inherited <= known:
                    outer_changed = True
                known |= module_inherited
            changed = True
            while changed:
                changed = False
                for name, value in candidates.get(qualname, []):
                    if name in known:
                        continue
                    if _candidate_resolves_to_fact(value, fact_names, known):
                        known.add(name)
                        changed = True
                        outer_changed = True
                # `cross_scope_candidates`' own value/binding scope split
                # -- the value is checked against *its own* recorded
                # qualname's converged aliases (`aliases.get(value_
                # qualname, set())`), never `known` (this loop's current
                # binding-scope set), since the two can genuinely differ
                # (see the comprehension-scope-hopping walrus collection
                # branch's own docstring).
                for name, value, value_qualname in cross_scope_candidates.get(
                    qualname, []
                ):
                    if name in known:
                        continue
                    if _candidate_resolves_to_fact(
                        value, fact_names, aliases.get(value_qualname, set())
                    ):
                        known.add(name)
                        changed = True
                        outer_changed = True
                # `tuple_loop_candidates`' own conjunctive resolution --
                # unlike `candidates` above (known the moment ANY one
                # recorded value resolves), a loop-target entry here needs
                # EVERY element to resolve before the target itself does
                # (Codex review, fresh evidence -- see the `for`/`AsyncFor`
                # collection branch's own docstring). Each element
                # resolves through `_candidate_resolves_to_fact()` --
                # the identical per-value check `candidates` above uses,
                # so a nested `IfExp` element is covered here too --
                # against *its own* recorded qualname's `known` set
                # (`aliases.get(elt_qualname, set())`), not necessarily
                # the current `qualname`: a comprehension's first-
                # generator element resolves against the parent scope's
                # aliases while the target itself still becomes known in
                # `qualname` (see the comprehension collection branch's
                # own docstring for why the two differ). `aliases` is
                # this same fixed point's own accumulator, read live --
                # scopes are visited in `_scope_depth` order each outer
                # pass, so a parent's aliases are already available by
                # the time its child is processed, and any later addition
                # still converges on a subsequent `outer_changed` pass.
                # This loop naturally participates in the surrounding
                # `while changed:` fixed point either way, converging
                # once every element (structural, alias, or nested
                # conditional) is confirmed.
                for name, elts in tuple_loop_candidates.get(qualname, []):
                    if name in known:
                        continue
                    if all(
                        _candidate_resolves_to_fact(
                            elt, fact_names, aliases.get(elt_qualname, set())
                        )
                        for elt, elt_qualname in elts
                    ):
                        known.add(name)
                        changed = True
                        outer_changed = True
            # Propagate a `global`/`nonlocal`-declared name, confirmed
            # Fact-typed *within this writer's own scope* (via `known`
            # just above -- direct annotation, or the inner fixed point
            # resolving it through a same-scope local like `local = rec.
            # bases_fact; fact = local`), into the scope it actually
            # writes to (Codex review, fresh evidence, second round on
            # this same write-side routing fix). The first revision
            # instead *moved* each declared assignment's raw `(name,
            # value)` candidate straight into the target scope's own
            # candidate list -- but `value` can itself be a bare `Name`
            # referencing a *third*, same-writer-scope local (`local` in
            # the example above), and once moved, the inner fixed point
            # for the *target* scope checks that name against the
            # *target*'s own `known` set, where a name local only to the
            # writer was never going to appear -- silently breaking
            # exactly the RHS indirection this whole write-side fix exists
            # to close. Checked here instead, each outer iteration, only
            # after `known` reflects everything resolvable within the
            # writer's own scope for *this* iteration -- so `local`
            # resolves in `seed`'s own scope first, then `fact` (now
            # confirmed via `local`) propagates to `<module>` the same
            # iteration, with no separate resolution context to get out of
            # sync with the writer's own.
            for declared_name in nonlocal_or_global.get(qualname, ()):
                if declared_name not in known:
                    continue
                target_scope = _declared_target_scope(qualname, declared_name)
                if target_scope == qualname:
                    continue
                target_known = aliases.setdefault(target_scope, set())
                if declared_name not in target_known:
                    target_known.add(declared_name)
                    outer_changed = True

        # Resolve every pending parameter default against each scope's
        # own alias set as it stands *this* iteration -- a default
        # expression is evaluated in the scope that directly,
        # syntactically contains the `def`/lambda (Python's own binding
        # rule for a default value, unlike the parameter itself), so a
        # bare-name default is checked against *that* scope's alias set,
        # not the function's own (which excludes the parameter's name
        # entirely, by construction -- see the collection site's own
        # comment). Deliberately `def_containing`, not `lexical_parents`
        # (Codex review, fresh evidence): a method's own default is
        # evaluated while its *containing class body* executes -- ordinary
        # class-body code, not a closure lookup -- but `lexical_parents`
        # intentionally skips the class layer for the (different) question
        # of a method *body*'s own free-variable lookup. `class C: fact =
        # rec.bases_fact; def f(self, value=fact): return value == other`
        # needs the class body's own alias set here, exactly the way
        # `_default_and_annotation_scope_overrides` needs it for a
        # comparison found directly inside the default expression itself.
        for qualname, arg_name, def_pos, default in pending_defaults:
            parent = def_containing.get(def_pos, "<module>")
            default_target_aliases = aliases.setdefault(qualname, set())
            if arg_name in default_target_aliases:
                continue
            if _candidate_resolves_to_fact(
                default, fact_names, aliases.get(parent, set())
            ):
                default_target_aliases.add(arg_name)
                outer_changed = True
    return aliases


_SCOPE_INTRODUCING_NODE_TYPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _iter_default_subtree(node: ast.AST) -> Iterator[ast.AST]:
    """Yield *node* and every descendant that still evaluates in *node*'s
    own (def-time) scope -- stopping before descending into any node that
    introduces its own scope (a nested lambda/comprehension/def), whose
    body evaluates later, in that nested scope, not at def-time here
    (Codex review, fresh evidence): with an outer `fact = rec.bases_fact`,
    `def f(cb=lambda fact: fact == other): ...` -- the lambda parameter
    `fact` genuinely shadows the outer alias inside the lambda's own body,
    so force-attributing everything inside that default expression
    (including the lambda's own body) to the *enclosing* scope wrongly
    treated the lambda's own local `fact` as the outer alias instead of
    leaving it to ordinary position-based resolution, which already
    handles a nested scope correctly (`_enclosing_qualnames`/`_qualname_at`
    give the lambda its own real span and qualname). A boundary node
    (the `Lambda`/`FunctionDef`/comprehension itself) is still yielded --
    harmless, since only an `ast.Compare` id is ever looked up in the
    resulting map -- just never expanded past.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, _SCOPE_INTRODUCING_NODE_TYPES):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _default_and_annotation_scope_overrides(
    tree: ast.Module,
    def_containing: dict[tuple[int, int], str],
) -> dict[int, str]:
    """Map `id()` of every node found inside a parameter's own default
    value or annotation expression to the qualname it actually *evaluates*
    in -- the `def`'s own directly-enclosing syntactic scope, not the
    function's own body scope (Codex review, fresh evidence): `fact = rec.
    bases_fact; def inner(fact=(fact == other)): ...` -- Python evaluates a
    default (and, absent `from __future__ import annotations`, an
    annotation -- including a `-> ...` return annotation, evaluated the
    identical way) at `def`-time, in the scope that directly, syntactically
    contains the `def` statement, the identical binding-timing rule `_fact_
    aliases()`'s own default-embedded-walrus handling already relies on.
    But `fact_equality_misuse_sites()`'s own site-to-qualname resolution is
    purely position-based (`_qualname_at`), and a default/annotation
    expression is textually *inside* the `def`'s own span -- so the
    comparison above was resolved against `inner`'s own alias set, where
    `inner`'s own parameter `fact` has already removed the inherited
    alias (a real local, shadowing it for the whole function body) --
    silently missing a comparison that, at the point it actually runs,
    still reads the outer alias.

    **Uses `_def_containing_qualnames`, not `_lexical_function_parents`
    (Codex review, fresh evidence).** A method's own defaults are
    evaluated while its *containing class body* executes -- ordinary
    `LOAD_NAME` lookup, ordinary class-body code -- not while some
    enclosing *function* scope executes: `class C: fact = rec.bases_fact;
    def f(self, value=fact): return value == other` must see `fact` as the
    class body's own alias, but `_lexical_function_parents` intentionally
    skips the class layer entirely (it answers a method *body*'s own
    free-variable/closure lookup, a different, narrower question -- a
    method cannot close over its class body, but its *default value*,
    evaluated at `def`-time as ordinary class-body code, is not a closure
    lookup at all). `_def_containing_qualnames` answers the syntactic
    question this needs instead: the scope that directly contains the
    `def` statement, class layers included.

    Consulted by `fact_equality_misuse_sites()` only -- `_fact_aliases()`
    itself already resolves a *binding* found inside a default/annotation
    correctly (the walrus case), this is the sibling fix for a *read* (an
    `==`/`!=` comparison) found there instead.

    **Also overrides a `ClassDef`'s own base/keyword expressions to its
    containing scope (Codex review, fresh evidence).** A base class or
    metaclass keyword (`class Inner(make_base(fact == other)): ...`) is
    evaluated while the *class statement itself* executes -- in whatever
    scope directly, syntactically contains that `class` statement -- never
    inside the new class's own body, even though `_enclosing_qualnames`
    assigns the entire `ClassDef` span, bases and keywords included, to
    the inner class-body scope (correct for the body's own statements,
    wrong for the header that precedes them). `class Outer: fact = rec.
    bases_fact; class Inner(make_base(fact == other)): ...` was silently
    missed: position-based resolution attributed the comparison to
    `Outer.Inner<class-body>`, whose own alias set has no relationship to
    `Outer<class-body>`'s (a class body's own locals give no visibility to
    a *nested* class the way an enclosing function's would -- `_lexical_
    function_parents` never produces a class-body-derived key at all).
    """
    overrides: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            enclosing = def_containing.get((node.lineno, node.col_offset), "<module>")
            for base_or_keyword in (*node.bases, *(kw.value for kw in node.keywords)):
                for descendant in _iter_default_subtree(base_or_keyword):
                    overrides[id(descendant)] = enclosing
            continue
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        enclosing = def_containing.get((node.lineno, node.col_offset), "<module>")
        subtrees = [*node.args.defaults, *node.args.kw_defaults]
        for arg in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *((node.args.vararg,) if node.args.vararg else ()),
            *((node.args.kwarg,) if node.args.kwarg else ()),
        ):
            if arg.annotation is not None:
                subtrees.append(arg.annotation)
        returns = getattr(node, "returns", None)
        if returns is not None:
            subtrees.append(returns)
        for subtree in subtrees:
            if subtree is None:
                continue
            for descendant in _iter_default_subtree(subtree):
                overrides[id(descendant)] = enclosing
    return overrides


def fact_equality_misuse_sites(tree: ast.Module, rel: str) -> list[tuple[int, int]]:
    """Return one ``(lineno, col_offset)`` per `==`/`!=` comparison in *tree*
    where at least one side is recognizably `Fact[T]`-typed (see
    :func:`_is_fact_typed_expr`), including through a same-function local
    alias or an annotated parameter (see :func:`_fact_aliases`).

    A chained comparison (`a == b == c`) is walked pairwise -- `ast.Compare`
    stores `left`, `ops`, and `comparators` separately, not as a flat list
    of operands, so each adjacent pair is checked independently and a
    single chain can report more than one site.

    **A comparison found inside a parameter's own default or annotation
    resolves against the function's lexical *parent*, not its own body
    (Codex review, fresh evidence).** See `_default_and_annotation_scope_
    overrides()`'s own docstring for why: a default/annotation expression
    is textually inside the `def`'s own span but evaluates in the
    enclosing scope, and this function's own site-to-qualname resolution
    is otherwise purely position-based.
    """
    qualnames = _enclosing_qualnames(tree)
    aliases = _fact_aliases(tree, qualnames)
    def_containing = _def_containing_qualnames(tree)
    scope_overrides = _default_and_annotation_scope_overrides(tree, def_containing)
    fact_names = _imported_fact_aliases(tree)

    def is_fact_typed(node: ast.expr, qualname: str) -> bool:
        if _is_fact_typed_expr(node, fact_names):
            return True
        if isinstance(node, ast.Name):
            return node.id in aliases.get(qualname, ())
        if isinstance(node, ast.IfExp):
            return is_fact_typed(node.body, qualname) and is_fact_typed(
                node.orelse, qualname
            )
        if isinstance(node, ast.NamedExpr):
            return is_fact_typed(node.value, qualname)
        if isinstance(node, ast.BoolOp):
            return all(is_fact_typed(operand, qualname) for operand in node.values)
        return False

    sites: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        qualname = scope_overrides.get(
            id(node), _qualname_at((node.lineno, node.col_offset), qualnames)
        )
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

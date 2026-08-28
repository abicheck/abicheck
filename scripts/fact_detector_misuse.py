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
from fact_detector_misuse_aliases import (  # noqa: E402
    FACT_FIELD_NAMES as FACT_FIELD_NAMES,  # re-exported for existing callers
    _fact_aliases,
    _imported_fact_aliases,
    _is_fact_typed_expr,
    _static_subscript_element,
)
from fact_detector_misuse_scope import (  # noqa: E402
    _constructor_alias_names,
    _constructor_method_alias_names,
    _def_containing_qualnames,
    _enclosing_qualnames,
    _global_declared_names,
    _iter_default_subtree,
    _lexical_function_parents,
    _locally_bound_constructor_shadow_names,
    _qualname_at,
    _resolve_effective_fact_names,
)

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


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


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

    **Also overrides a `def`/`class`'s own decorator expressions to their
    containing scope, the identical way defaults/annotations/bases already
    are (Codex review, fresh evidence).** A decorator (`@deco(fact ==
    other)`) is evaluated while the decorated statement itself executes --
    before the function/class it decorates even exists -- in whatever
    scope directly, syntactically contains that statement, never inside
    the new function's/class's own body. `_lexical_function_parents`'s and
    `_def_containing_qualnames`'s own `def_time_subtrees()`/`dispatch()`
    helpers already dispatch a `def`'s or `class`'s `decorator_list` under
    the *incoming* (enclosing) qualname, exactly the def-time treatment
    this function already gives every other def-time subtree -- but this
    function's own subtree collection had no `decorator_list` entry at
    all, so `fact = rec.bases_fact; @deco([x for x in (fact == other,)])
    def f(fact): ...` was silently missed: `_enclosing_qualnames` assigns
    the whole `FunctionDef`'s decorator-adjacent lines to `f`'s own body
    scope, where the parameter `fact` has already shadowed the alias.
    Fixed by adding `decorator_list` to both the function/lambda branch's
    `subtrees` (via `getattr`, since `ast.Lambda` has none) and the
    `ClassDef` branch's own base/keyword loop.
    """
    overrides: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            enclosing = def_containing.get((node.lineno, node.col_offset), "<module>")
            for base_keyword_or_deco in (
                *node.bases,
                *(kw.value for kw in node.keywords),
                *node.decorator_list,
            ):
                for descendant in _iter_default_subtree(base_keyword_or_deco):
                    overrides[id(descendant)] = enclosing
            continue
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        enclosing = def_containing.get((node.lineno, node.col_offset), "<module>")
        subtrees = [
            *node.args.defaults,
            *node.args.kw_defaults,
            *getattr(node, "decorator_list", ()),
        ]
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


def _module_has_deferred_annotations(tree: ast.Module) -> bool:
    """True if *tree* has `from __future__ import annotations` (PEP 563)
    at module level -- under it every annotation (parameter, return, or
    variable) is stored as source text and never evaluated at runtime,
    the repository-mandated convention this module's own scan target
    (`abicheck/`) follows throughout (AGENTS.md: "Python: 3.10+ syntax,
    type annotations, `from __future__ import annotations`")."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _deferred_annotation_compare_ids(tree: ast.Module) -> frozenset[int]:
    """`id()` of every `ast.Compare` node embedded inside a parameter,
    return, or variable annotation's own subtree, when *tree*'s module
    defers annotations (Codex review, fresh evidence): `def f(x:
    Annotated[int, Fact.present(1) == sentinel]): ...` stores that
    comparison as inert annotation *text*, never actually executed, so
    the unconditional `ast.Compare` walk below was flagging dead code as
    a live misuse. Empty when the future import is absent -- an
    annotation without it genuinely does evaluate at def-time, in which
    case the embedded comparison is a real site, not a false one.

    Deliberately independent of `_default_and_annotation_scope_
    overrides()`'s own subtree collection just above, even though both
    walk the identical `FunctionDef`/`AsyncFunctionDef`/`Lambda`
    parameter-annotation/`returns` shape: that function's own `overrides`
    dict conflates default-value, decorator, and class-base/keyword
    subtrees together with annotation subtrees into one undifferentiated
    id set, and only annotations are ever deferred -- a default value,
    decorator, or class base always evaluates eagerly at def/class-time
    regardless of this future import, so reusing that dict's keys here
    would wrongly exclude a genuine comparison inside one of those other
    subtrees too.
    """
    if not _module_has_deferred_annotations(tree):
        return frozenset()
    ids: set[int] = set()
    for node in ast.walk(tree):
        annotations: list[ast.expr] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for arg in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *((node.args.vararg,) if node.args.vararg else ()),
                *((node.args.kwarg,) if node.args.kwarg else ()),
            ):
                if arg.annotation is not None:
                    annotations.append(arg.annotation)
            returns = getattr(node, "returns", None)
            if returns is not None:
                annotations.append(returns)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            annotations.append(node.annotation)
        for annotation in annotations:
            for descendant in ast.walk(annotation):
                if isinstance(descendant, ast.Compare):
                    ids.add(id(descendant))
    return frozenset(ids)


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
    deferred_annotation_compare_ids = _deferred_annotation_compare_ids(tree)
    fact_names = _imported_fact_aliases(tree)
    locally_bound_shadows = _locally_bound_constructor_shadow_names(tree, qualnames)
    lexical_parents = _lexical_function_parents(tree)
    global_names = _global_declared_names(tree, qualnames)
    constructor_aliases = _constructor_alias_names(
        tree,
        qualnames,
        fact_names,
        locally_bound_shadows,
        lexical_parents,
        global_names,
    )
    constructor_method_aliases = _constructor_method_alias_names(
        tree,
        qualnames,
        fact_names,
        locally_bound_shadows,
        lexical_parents,
        global_names,
    )

    def is_fact_typed(node: ast.expr, qualname: str) -> bool:
        # `def f(Fact, other): return Fact(1) == other` -- an ordinary
        # parameter reusing the constructor's own name shadows it for
        # the whole function (Codex review, fresh evidence): `_is_fact_
        # typed_expr()`'s constructor-call recognition is a pure,
        # scope-blind lookup against the single, whole-tree `fact_names`
        # set, so a locally-shadowed `Fact` was still treated as the
        # real constructor. `F = Fact` is the opposite direction of the
        # same gap -- a real local alias of the constructor itself
        # (`_constructor_alias_names()`), not merely a shadow -- so it's
        # *added* into the effective set the same way a shadow is
        # subtracted from it. `_resolve_effective_fact_names()` resolves
        # both in one combined, nearest-scope-wins walk rather than two
        # independent `_scope_chain_union()` calls -- a real bug the
        # independent-walk version had (Codex review, fresh evidence):
        # `F = Fact; def f(F, other): return F(1) == other` -- `f`'s own
        # parameter `F` shadows the outer alias, but an unconditional
        # alias-union re-added it from the ancestor scope regardless.
        effective_fact_names = _resolve_effective_fact_names(
            qualname,
            fact_names,
            locally_bound_shadows,
            constructor_aliases,
            lexical_parents,
            global_names,
        )
        if _is_fact_typed_expr(node, effective_fact_names):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            in _resolve_effective_fact_names(
                qualname,
                frozenset(),
                locally_bound_shadows,
                constructor_method_aliases,
                lexical_parents,
                global_names,
            )
        ):
            # `make_fact = Fact.present; make_fact(1) == other` -- a
            # direct call through a local name bound to an *unbound
            # classmethod reference*, not the constructor name itself
            # (`_constructor_method_alias_names()`'s own docstring: this
            # is why it's a separate set, never folded into
            # `effective_fact_names`, which participates in the general
            # `Attribute`-call recognition too). Resolved through the
            # identical nearest-scope-wins machinery as the bare-alias
            # case above -- an unconditional union had the same bug here
            # (Codex review, fresh evidence, verified as a sibling of the
            # reported finding): `make_fact = Fact.present; def f(make_
            # fact, other): return make_fact(1) == other` -- `f`'s own
            # parameter shadows the outer classmethod alias too. `frozenset()`
            # as the base set, since there's no separate "fact_names"
            # concept here -- only ever asking "is this exact name a live
            # classmethod alias at this scope."
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
        if isinstance(node, ast.Subscript):
            # `fact = rec.bases_fact; (fact,)[0] == other` -- the
            # identical bare-alias-inside-a-static-display gap the
            # `Name`/`IfExp`/`NamedExpr`/`BoolOp` branches above already
            # close for their own composed shapes (Codex review, fresh
            # evidence): `_is_fact_typed_expr()`'s own Subscript branch
            # only ever recurses into itself, structurally, so a selected
            # element that's a bare alias name was never resolved against
            # this scope's own `aliases`. Recurses through `is_fact_
            # typed()` itself (not `_is_fact_typed_expr()`), the
            # identical "route through the alias-aware resolver, not the
            # purely structural one" fix `_candidate_resolves_to_fact()`'s
            # own new Subscript branch already applies at fixed-point
            # time.
            element = _static_subscript_element(node)
            if element is not None:
                return is_fact_typed(element, qualname)
        return False

    sites: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if id(node) in deferred_annotation_compare_ids:
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

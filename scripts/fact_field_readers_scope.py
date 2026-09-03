#!/usr/bin/env python3
"""Lexical-scope resolution primitives for ``fact_field_readers.py``'s
`fact-field-readers` AI-readiness check (ADR-063 Phase 0,
docs/contribute/plans/one-semantic-pipeline.md).

Split out of ``fact_field_readers.py`` once that module approached the
AI-readiness gate's own 2000-line hard cap (``file-size`` check) --
mechanical extraction, not a redesign: every function here is unchanged
from its original home, moved as a contiguous, self-contained block
(``_enclosing_qualnames``, ``_parent_map``,
``_TRANSPARENT_EXPR_WRAPPER_TYPES``, ``_outermost_containing_expr``,
``_locally_bound_names``, ``_lexical_function_parents``) -- the shared
"resolve a position, a call site, or a name to its enclosing lexical
scope" building blocks the rest of that module's own alias/shadowing
machinery (``unmigrated_fact_reader_sites``'s own ``_shadowed()``
closure, every ``*_aliases()`` helper) builds on. Deliberately does
**not** share these with ``fact_detector_misuse.py``'s own,
differently-shaped same-named helpers -- see that module's own docstring
for why the two checks keep independent copies rather than a shared
abstraction (this module's own qualname model is deliberately coarser,
line-keyed rather than span-keyed, and ``_locally_bound_names``/
``_lexical_function_parents`` cover only the narrower set of binding
forms `fact_field_readers.py` has needed evidence for so far -- see each
function's own docstring).

A second, later block was moved here the identical way, once the same
file crossed the cap a second time (Codex review round adding the
``operator.attrgetter``/``operator.itemgetter`` dotted-path and
constructor-recognition fixes): ``_operator_attrgetter_aliases``,
``_is_attrgetter_constructor_call``, ``_attrgetter_matched_name``,
``_is_itemgetter_constructor_call``, ``_itemgetter_matched_name`` -- the
`operator.attrgetter`/`operator.itemgetter` alias-resolution and
constructor-recognition primitives, unchanged from their original home,
with no dependency on anything in this module's first block beyond bare
`ast`.

Imported directly by ``fact_field_readers.py``; not meant to be used
standalone.
"""

from __future__ import annotations

import ast


def _enclosing_qualnames(tree: ast.Module) -> dict[int, str]:
    """Map every line number in *tree* to its innermost enclosing
    function's qualified name (``Class.method`` for a method, tracked
    through nested ``def``s the way `__qualname__` is), or ``"<module>"``
    for a line outside any function.

    A plain line-range lookup rather than tracking a live scope stack
    during the attribute walk: `ast.walk` doesn't expose parent/ancestor
    context, and re-deriving it with a hand-rolled visitor for every call
    site would duplicate this same walk. One pass building this map,
    consulted by line number, is simpler and gives the identical answer.

    **A parameter default value/annotation is textually part of the
    function's own signature, but evaluates at *def-time*, in whatever
    scope directly, syntactically contains the `def` statement -- not the
    function's own body scope this map would otherwise attribute its
    whole line range to (Codex review, fresh evidence).** `def f(getattr,
    x=getattr(rec, "bases")): ...` -- the default `x=getattr(rec,
    "bases")` evaluates *before* `f`'s own parameters exist, so this call
    genuinely reads the real builtin, but the function's own `[child.
    lineno, end]` range covers its own signature line too, so `_shadowed()`
    saw `f`'s own (not-yet-bound) parameter `getattr` and wrongly excluded
    a real read. A decorator does *not* need this treatment: it sits on a
    line strictly *before* `child.lineno`, already outside the function's
    own range by construction. Fixed by registering each default/
    annotation's own `[lineno, end_lineno]` range under the *current*
    (enclosing, pre-function) qualname -- narrower than the function's own
    range in the ordinary case, so the existing smallest-range-wins
    tie-break below lets it correctly override the function's own broader
    range for just those lines. A default/annotation sharing a line with
    genuine function-*body* code (a one-liner `def f(x=getattr(rec,
    "bases")): return x`) is a real, accepted residual this line-based
    model can't distinguish further -- the same granularity limit this
    function's own docstring already accepts throughout.
    """
    ranges: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str, qualname: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_qualname = f"{prefix}{child.name}"
                end = getattr(child, "end_lineno", child.lineno)
                ranges.append((child.lineno, end, child_qualname))
                all_args = (
                    *child.args.posonlyargs,
                    *child.args.args,
                    *child.args.kwonlyargs,
                    *((child.args.vararg,) if child.args.vararg else ()),
                    *((child.args.kwarg,) if child.args.kwarg else ()),
                )
                def_time_subtrees = [
                    *child.args.defaults,
                    *(d for d in child.args.kw_defaults if d is not None),
                    *(a.annotation for a in all_args if a.annotation is not None),
                ]
                returns = getattr(child, "returns", None)
                if returns is not None:
                    def_time_subtrees.append(returns)
                for subtree in def_time_subtrees:
                    sub_end = getattr(subtree, "end_lineno", subtree.lineno)
                    ranges.append((subtree.lineno, sub_end, qualname))
                visit(child, child_qualname + ".", child_qualname)
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", qualname)
            else:
                visit(child, prefix, qualname)

    visit(tree, "", "<module>")
    # Innermost enclosing range wins: sort by ascending span size so a
    # later, narrower match overwrites the wider one already recorded.
    ranges.sort(key=lambda r: r[1] - r[0], reverse=True)
    by_line: dict[int, str] = {}
    for start, end, qualname in ranges:
        for lineno in range(start, end + 1):
            by_line[lineno] = qualname
    return by_line


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    """Map ``id(child)`` to its immediate AST parent, for every node in
    *tree*. `ast.walk`/`ast.iter_child_nodes` expose no ancestor link on
    their own, so this is one pass building the reverse edge, consulted by
    :func:`_outermost_containing_expr`.
    """
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


#: Non-`ast.expr` AST node kinds this module still climbs *through* when
#: finding a read's outermost containing expression -- each always sits
#: directly between an expression and its own real expression parent, so
#: stopping at one of these (as the original `isinstance(..., ast.expr)`
#: check alone did) understates the containing expression instead of
#: reaching it (Codex review, fresh evidence): a `keyword`-argument value
#: (`old(value=rec.bases)`) and a comprehension's own `for`/`if` clause
#: (`[x for x in rec.bases]`'s `iter`, or an `if` filter) are both
#: genuinely part of a real enclosing expression (a `Call`, a
#: `ListComp`/`SetComp`/`DictComp`/`GeneratorExp`) one hop further up --
#: `old(value=rec.bases)`/`keep(value=rec.bases)` collapsed to the
#: identical `outer_text = "rec.bases"` (differing only by occurrence
#: rank) before this fix, the exact same-key collision this whole
#: mechanism exists to prevent.
_TRANSPARENT_EXPR_WRAPPER_TYPES = (ast.keyword, ast.comprehension)


def _outermost_containing_expr(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST:
    """Walk *node*'s ancestors (via *parents*, from :func:`_parent_map`) up
    through every enclosing `ast.expr` -- and every transparent wrapper in
    `_TRANSPARENT_EXPR_WRAPPER_TYPES` above, climbed straight through
    rather than counted as the boundary -- stopping at the outermost real
    expression: the whole `old_decision(rec.bases)` call, or the whole
    `not p_old.is_va_list and p_new.is_va_list` boolean test, but never
    further: the next ancestor up is always a *statement*
    (`Expr`/`If`/`Return`/...), whose own body/orelse this must not pull
    in.

    Deliberately narrower than the *enclosing statement* an earlier
    revision of this function used -- see :func:`unmigrated_fact_reader_
    sites`'s own docstring for why that was wrong (a compound statement's
    body dwarfs and destabilizes the key for no benefit the expression
    boundary doesn't already give).
    """
    current = node
    while id(current) in parents:
        parent = parents[id(current)]
        if isinstance(parent, ast.expr) or isinstance(
            parent, _TRANSPARENT_EXPR_WRAPPER_TYPES
        ):
            current = parent
        else:
            break
    return current


def _target_bound_names(target: ast.expr) -> list[str]:
    """Every plain name *target* binds -- a bare `ast.Name`, or every
    name nested inside a `Tuple`/`List`/`Starred` unpacking target
    (`for getattr, _ in pairs:` binds `getattr` exactly like a bare
    `for getattr in ...:` does). Module-level (not nested inside
    `_locally_bound_names()`) so `fact_field_readers.py`'s own
    `_shadowed()` can reuse it for a comprehension's own `for` target --
    see that function's own docstring for why a comprehension's target
    is handled there, via a real AST-ancestor check, rather than here."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_bound_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_bound_names(elt))
        return names
    return []


def _locally_bound_names(
    tree: ast.Module,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Map each function's qualname (the identical key `_enclosing_
    qualnames` uses) to every *parameter* name it declares -- not an
    ordinary assignment target, and not a name bound inside a *nested*
    function's own body -- as the first of two returned dicts; the second
    maps each qualname to every name recognized there as a genuine
    alias-source import (see the "Carved out" section below) instead of
    an ordinary shadow.

    **The second dict exists to stop `_shadowed()`'s own outward closure
    walk at the scope a recognized alias resolves, rather than letting it
    keep walking past that scope entirely (Codex review, fresh
    evidence).** `from helper import ag` at module scope, then `from
    operator import attrgetter as ag` inside `f`, then `ag("bases")(rec)`
    inside `f` -- a real field read, since `f`'s own import genuinely
    resolves `ag` to `operator.attrgetter`. The recognized-import
    exclusion below correctly keeps `f`'s own import out of the first
    dict (it is not a shadow), but that alone left `_shadowed()`'s walk
    with nothing to stop it at `f` -- it kept walking outward to
    `<module>`, found the *unrelated* `ag` import recorded there, and
    wrongly treated that completely different binding as a shadow of the
    call inside `f`. Recording the recognized name in this second dict
    lets `_shadowed()` stop -- unshadowed -- the moment it passes through
    the scope where the alias was actually recognized, instead of
    continuing to search outer scopes for an unrelated same-named binding
    that has nothing to do with the resolved alias.

    **Used to exclude a shadowed name from builtin recognition (Codex
    review, fresh evidence).** `def f(getattr, rec): return getattr(rec,
    "bases")` shadows the real `getattr` builtin with an ordinary,
    unrelated local parameter of the identical name -- but the
    builtin-recognition branch in `unmigrated_fact_reader_sites()` had no
    notion of local shadowing at all, unconditionally treating the bare
    name `getattr` as the real builtin regardless of what the enclosing
    function actually bound it to. Reported call sites in an unrelated,
    valid change would then either falsely fail the ERROR-level gate or
    force a misleading baseline entry for a read that was never really a
    builtin call.

    **Deliberately parameters only, not an ordinary assignment target
    (Codex review, fresh evidence: a first revision of this helper also
    covered `ast.Assign`/`ast.AnnAssign` targets, and immediately broke six
    existing tests).** `read_attr = getattr; read_attr(rec, "bases")` is
    exactly `_builtins_getattr_aliases()`'s own alias-resolution mechanism
    -- `read_attr` IS a genuine local assignment target, but treating that
    as "shadowing" is backwards: it's *how* an alias becomes trustworthy,
    not a reason to distrust it. Telling a real shadow (`getattr =
    some_unrelated_value`) apart from a real alias assignment (`read_attr =
    getattr`) needs per-assignment tracing of what each target's own value
    resolves to (exactly what `_builtins_getattr_aliases()`'s internal
    `assign_candidates` already does, but scoped per-function and exposed,
    neither of which it currently is) -- a real, if narrow, follow-up this
    revision does not attempt. A parameter can never be an alias source in
    that same sense (nothing in a function signature assigns FROM
    `getattr`), so restricting to parameters closes the reported false
    positive with no risk of this same conflict.

    Deliberately narrower than the exhaustive binding-form coverage
    `fact_detector_misuse.py`'s own `locally_bound` machinery has grown
    into over many review rounds (comprehension/lambda/match/walrus
    scoping, closures into a *nested* function, global/nonlocal routing) --
    only for the immediate enclosing function of the call site being
    checked, since no evidence has reported any of those more exotic
    shapes shadowing `getattr`/`builtins` specifically (or the identical
    risk for `attrgetter`/`operator`, which shares this same unhandled gap
    -- deliberately not extended there either without reported evidence).
    Extend this the same incremental way if one is ever found, matching
    this module's own established practice of only building the
    generality an actual review finding demonstrates.

    **Known gap, confirmed with a concrete repro rather than left purely
    theoretical (Codex review, fresh evidence): a shadowing parameter that
    is later *rebound* to a genuine alias source is still treated as
    shadowed for the call.** `def f(getattr, rec): getattr =
    builtins.getattr; return getattr(rec, "bases")` -- a real, unremarkable
    read of a bridged field through a locally-rebound name -- currently
    reports no site at all (the sibling `attrgetter`/`operator` shape has
    the identical gap: `def f(operator, rec): import operator; return
    operator.attrgetter("bases")(rec)`). This is exactly the follow-up the
    paragraph above already named as not attempted, now with a real
    example rather than a hypothetical one: correctly distinguishing it
    from a genuine shadow (`getattr = some_unrelated_value`) needs
    *order-aware* per-function tracing -- which assignment to the name is
    the one actually in effect at the call's own position, not merely
    whether *some* recognized-alias assignment exists anywhere in the
    scope. The latter, simpler check is unsound in the other direction:
    `def f(getattr, rec): result = getattr(rec, "bases"); getattr =
    builtins.getattr` calls `getattr` *before* the rebind, while still
    holding the arbitrary parameter value, so an order-blind "was this
    name ever reassigned to a recognized alias" check would wrongly
    exclude a real shadow the same way `_builtins_getattr_aliases()`'s own
    docstring already warns a naive treatment could. Building genuine
    per-position dataflow into this module -- rather than its current
    presence/absence-only model -- is a materially larger change than the
    guard conditions this module has added incrementally so far (it took
    `fact_detector_misuse.py`'s own alias-resolution machinery upwards of
    twenty review rounds to reach exactly this kind of order-sensitivity
    for its own, structurally similar problem), so it is recorded here as
    an accepted, deliberately unfixed gap rather than attempted under
    review pressure. This is a false *negative* (a real dynamic read
    silently passes the gate), the direction this module's own established
    "a false positive is far cheaper than the false negative it closes"
    trade-off argues hardest against accepting -- but an incorrect,
    order-blind attempt at closing it risks trading this false negative
    for a new false positive on a genuine shadow, which is not obviously
    an improvement. Revisit with real per-position tracing if this shape
    is found in practice, not with a heuristic that cannot tell the two
    cases apart.

    **A `def`/`class` statement's own *name* is a locally-bound name too,
    not just a parameter (Codex review, fresh evidence).** `def
    getattr(obj, name): return None` followed by `getattr(rec, "bases")`
    -- an ordinary, unrelated function definition that happens to share
    the builtin-looking name `getattr` -- was still unconditionally
    treated as the real builtin, since only parameters were ever recorded
    as locally bound; a `def`/`class` statement's own binding target
    (Python's ordinary `STORE_NAME`/`STORE_FAST` rule for a def/class
    statement, the identical rule `fact_detector_misuse.py`'s own
    `_def_containing_qualnames` already models) was invisible here. Fixed
    by also recording each `def`/`class`'s own `name` against whichever
    scope directly, syntactically contains it.

    **That "directly, syntactically contains it" scope is NOT simply the
    nearest enclosing *function*, unlike the closure-parent concept
    `_lexical_function_parents` tracks (Codex review, fresh evidence, a
    real regression in the first version of this same fix).** A first
    version tracked a `nearest_func` parameter, skipping class layers the
    same way `_lexical_function_parents` deliberately does for closure
    purposes -- but a method's own *name* does not bind into its
    enclosing function/module namespace at all; it becomes a class
    attribute (`C.getattr`), invisible to an ordinary bare-name lookup
    anywhere outside the class body. Recording it against the skip-class
    `nearest_func` anyway meant `class C: def getattr(self, name): ...`
    made an *unrelated* function elsewhere in the same module -- one with
    no textual relationship to `C` at all -- read as if it had a local
    `getattr` binding, silently excluding its own, genuine
    `getattr(rec, "bases")` call. Fixed by tracking a separate `binding_
    scope: str | None` -- the scope a *bare name binds into*, as opposed
    to `nearest_func`'s "scope a closure looks up through" -- `None`
    while directly inside a class body (nothing recorded there at all,
    matching how `_shadowed()` never queries a class-body scope either,
    since none of this module's qualname machinery models one), and the
    function's own qualname once recursed into a function body (an
    ordinary nested function's own name genuinely does bind into its
    immediately enclosing function, unlike a method's into its class).

    **An import statement binds its target name too, exactly like a
    parameter or a `def`/`class` statement's own name (Codex review,
    fresh evidence).** `from helper import getattr` then `getattr(rec,
    "bases")` -- an ordinary import of an unrelated module's own
    `getattr` symbol, reusing the builtin-looking bare name -- was
    unconditionally treated as the real builtin, since neither `ast.
    Import` nor `ast.ImportFrom` was ever visited here at all. Fixed by
    recording each imported name (`alias.asname` if given, else the
    plain name -- an unaliased dotted `import a.b.c` binds only the
    top-level package `a`, Python's own import-binding rule, the
    identical split `fact_detector_misuse.py`'s own import branch
    already applies) against whichever scope directly contains the
    import statement.

    **Carved out: an import this module already recognizes as a genuine
    alias *source* for a builtin/`operator` symbol must NOT be treated as
    a shadow of itself.** `from builtins import getattr` (bare, no
    `as`), `from builtins import object`/`type`/`vars`/`dict`, `from
    operator import attrgetter`/`getitem`/`itemgetter`, and a bare
    `import builtins`/`import operator` are all already resolved
    elsewhere in this module (`_builtins_getattr_aliases()`,
    `_unbound_getattribute_receiver_aliases()`, `_builtins_symbol_
    aliases()` -- covering both `vars` and `dict`, its own
    reused-elsewhere generality -- `_operator_attrgetter_aliases()`,
    which resolves `itemgetter` the identical way it already resolves
    `attrgetter`/`getitem`) as
    evidence that the bound name genuinely *is* the real builtin/operator
    symbol -- recording that same binding here too would make
    `_shadowed()` see it as a local shadow and wrongly exclude the very
    call it was imported to enable
    (e.g. `from operator import attrgetter; attrgetter("bases")(rec)`
    would stop being recognized at all, a real regression, not merely an
    incomplete fix). Every *other* import -- including an aliased
    `from builtins import getattr as g` recognized under the alias `g`,
    which is excluded the identical way -- still binds and shadows
    normally.

    **A `for` target, a `with ... as` target, an `except ... as` name, and
    a `match` capture are all real lexical bindings too -- the identical
    class of gap as the `def`/`class`/import bindings above, just for four
    more binding *forms* rather than a fifth binding *site* (Codex review,
    fresh evidence).** `for getattr in funcs: return getattr(rec,
    "bases")` -- an ordinary, unrelated loop variable reusing the
    builtin-looking name -- was still unconditionally treated as the real
    builtin, since none of `ast.For`/`ast.AsyncFor`/`ast.With`/
    `ast.AsyncWith`/`ast.ExceptHandler`/`ast.Match` was ever specially
    recognized here; the fallback walk only ever recurses into each of
    these, it never records what they themselves bind. The identical
    false-positive shape reproduces for `with cm() as getattr:`, `except
    Exception as getattr:`, and `case getattr: return getattr(rec,
    "bases")`.

    Modeled as one generalized shadowing class rather than four
    independently-hand-rolled ones, per the review finding's own
    suggestion: `_target_bound_names()` (module-level, shared with
    `fact_field_readers.py`'s own `_shadowed()`) extracts every plain name
    a `for`/`with` target binds (recursing through `Tuple`/`List`/
    `Starred` nesting -- `for getattr, _ in pairs:` binds `getattr`
    exactly like a bare `for getattr in ...:` does), and
    `_match_pattern_captures()` extracts every capture a `match` pattern
    binds (`ast.walk` over the pattern subtree, since a capture can nest
    arbitrarily deep inside a class/sequence/mapping/OR pattern, and
    Python already requires every alternative of an OR pattern to bind the
    identical name set, so walking the whole pattern once is sound
    regardless of which alternative a real match would take). None of
    these four forms introduces its own new *scope* the way a `def` does
    for its *body* (a `for`/`with`/`except`/`match` binds directly into
    whatever function scope already contains it) -- so every one of these
    bindings is recorded against the current `binding_scope` directly, the
    same target every parameter already uses, never a new one.

    **Deliberately excludes a comprehension's own `for` target, unlike the
    four forms above -- a real regression in an earlier revision of this
    same fix, caught by review (Codex review, fresh evidence).** A
    comprehension genuinely *does* introduce its own new scope in Python
    3 (unlike a plain `for`/`with`/`except`/`match`, none of which are
    block-scoped), so its target must shadow calls *inside* the
    comprehension (its `elt`, its filters, its later generators) without
    leaking into the rest of the enclosing function -- `[x for getattr in
    funcs]` followed by a genuine, unrelated `getattr(rec, "bases")` later
    in the same function must still be flagged. Recording the target
    against `binding_scope` (this module's coarser, line-based/function-
    only qualname model, with no comprehension-specific scope of its own)
    got this backwards: it correctly shadowed calls *inside* the
    comprehension, but also wrongly shadowed every *unrelated* call
    anywhere later in the whole enclosing function. Handled instead by
    `fact_field_readers.py`'s own `_shadowed()`, via a real AST-ancestor
    check against the call's true position -- exact by construction, so it
    can never leak outside the comprehension the way this qualname-keyed
    dict would -- mirroring how that function already handles a `lambda`
    parameter's identical "not a scope this module's coarser qualname
    model tracks" shape (see its own docstring).
    """
    bound: dict[str, set[str]] = {}
    recognized_aliases: dict[str, set[str]] = {}

    def _match_pattern_captures(pattern: ast.pattern) -> list[str]:
        names: list[str] = []
        for node in ast.walk(pattern):
            if isinstance(node, ast.MatchAs) and node.name is not None:
                names.append(node.name)
            elif isinstance(node, ast.MatchStar) and node.name is not None:
                names.append(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest is not None:
                names.append(node.rest)
        return names

    def visit(node: ast.AST, prefix: str, binding_scope: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.AsyncFor)):
                if binding_scope is not None:
                    bound.setdefault(binding_scope, set()).update(
                        _target_bound_names(child.target)
                    )
                visit(child, prefix, binding_scope)
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                if binding_scope is not None:
                    for item in child.items:
                        if item.optional_vars is not None:
                            bound.setdefault(binding_scope, set()).update(
                                _target_bound_names(item.optional_vars)
                            )
                visit(child, prefix, binding_scope)
            elif isinstance(child, ast.ExceptHandler):
                if binding_scope is not None and child.name is not None:
                    bound.setdefault(binding_scope, set()).add(child.name)
                visit(child, prefix, binding_scope)
            elif isinstance(child, ast.Match):
                if binding_scope is not None:
                    for case in child.cases:
                        bound.setdefault(binding_scope, set()).update(
                            _match_pattern_captures(case.pattern)
                        )
                visit(child, prefix, binding_scope)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    if isinstance(child, ast.Import):
                        bound_name = alias.asname or alias.name.split(".", 1)[0]
                        recognized = alias.name in ("builtins", "operator")
                    else:
                        bound_name = alias.asname or alias.name
                        recognized = (
                            child.module == "builtins"
                            and alias.name
                            in ("getattr", "object", "type", "vars", "dict")
                        ) or (
                            child.module == "operator"
                            and alias.name in ("attrgetter", "getitem", "itemgetter")
                        )
                    if binding_scope is None:
                        continue
                    if recognized:
                        recognized_aliases.setdefault(binding_scope, set()).add(
                            bound_name
                        )
                        continue
                    bound.setdefault(binding_scope, set()).add(bound_name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_qualname = f"{prefix}{child.name}"
                if binding_scope is not None:
                    bound.setdefault(binding_scope, set()).add(child.name)
                all_args = (
                    *child.args.posonlyargs,
                    *child.args.args,
                    *child.args.kwonlyargs,
                    *((child.args.vararg,) if child.args.vararg else ()),
                    *((child.args.kwarg,) if child.args.kwarg else ()),
                )
                for arg in all_args:
                    bound.setdefault(child_qualname, set()).add(arg.arg)
                visit(child, child_qualname + ".", child_qualname)
            elif isinstance(child, ast.ClassDef):
                if binding_scope is not None:
                    bound.setdefault(binding_scope, set()).add(child.name)
                visit(child, f"{prefix}{child.name}.", None)
            else:
                visit(child, prefix, binding_scope)

    visit(tree, "", "<module>")
    return bound, recognized_aliases


def _lexical_function_parents(tree: ast.Module) -> dict[str, str]:
    """Map each function's qualname (the identical key `_enclosing_
    qualnames`/`_locally_bound_names` use) to its nearest *enclosing
    function's* qualname -- skipping any intervening class scope -- or
    `"<module>"` if it has none.

    Used to widen `_shadowed()`'s shadowing check to a call's *entire*
    lexical scope chain, not just its own innermost function (Codex
    review, fresh evidence): `def outer(getattr): def inner(rec): return
    getattr(rec, "bases")` -- `getattr` is an arbitrary callable captured
    from `outer`'s own parameter via Python's ordinary closure rule, but
    `inner` binds no parameter of that name itself, so a check restricted
    to `inner`'s own `locally_bound` entry never saw it, falsely treating
    the closed-over parameter as the real `getattr` builtin.

    A standalone copy of `fact_detector_misuse.py`'s identical-purpose
    helper (see `FACT_FIELD_NAMES`'s own docstring for why these two leaf
    modules stay decoupled), simplified to this module's own coarser,
    dot-joined qualname scheme (no `#lineno` disambiguator -- an
    `@overload` stub colliding with its real implementation is an
    existing, accepted characteristic of this module's qualnames already,
    not a new risk this helper introduces).
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


def _operator_attrgetter_aliases(
    tree: ast.Module,
) -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]:
    """Return `(attrgetter_names, operator_module_names, getitem_names,
    itemgetter_names)`: every local name *tree* binds to the real
    `operator.attrgetter` callable (the bare `"attrgetter"` itself only
    once a real `from operator import attrgetter` is found -- see this
    docstring's own "Seeded only from a verified import" paragraph below
    for why -- plus any `... as X` alias), every local name bound to the
    `operator` module itself (the bare `"operator"` only once a real
    `import operator` is found, plus any `import operator as X`), every
    local name bound to the real `operator.getitem` callable (the
    identical `attrgetter`-shaped resolution -- import-seeded, chained,
    qualified -- applied to `getitem` instead), and every local name bound
    to the real `operator.itemgetter` callable (the identical resolution
    again, applied to `itemgetter` -- Codex review, fresh evidence:
    `operator.itemgetter("bases")(vars(rec))` constructs a getter the
    same way `attrgetter` does, just for subscript access rather than
    attribute access, and neither the bare nor the qualified spelling was
    tracked at all before this).

    **`getitem_names` closes the identical import-alias gap this
    function's own `attrgetter_names` already covers, for `operator.
    getitem` instead (Codex review, fresh evidence).** `from operator
    import getitem as gi; gi(vars(rec), "bases")` reads the exact same
    normalized legacy value the unaliased `operator.getitem(...)` form
    already catches -- but the call-matching branch there requires an
    `ast.Attribute` callee (`X.getitem(...)`), and `getitem` was never
    tracked as its own alias family at all. Resolved identically to
    `attrgetter_names`, sharing this same function's `operator_names`/
    `assign_candidates` collection so the two families can't
    independently drift on what counts as a resolved `operator` alias.

    An ordinary `import operator as op` or `from operator import attrgetter
    as ag` reads the identical legacy field as the unaliased spellings
    (Codex review, fresh evidence): `op.attrgetter("bases")(rec)`/
    `ag("bases")(rec)` are real, unremarkable Python, and the caller's own
    exact-name matching (`"operator"`/`"attrgetter"` only) missed both --
    the identical gap `_builtins_getattr_aliases()` above closes for
    `getattr`/`builtins`, applied to this pair instead.

    **A plain-assignment alias of either name is resolved too (Codex
    review, fresh evidence, second round on this same helper).** `import
    operator as op; op2 = op; op2.attrgetter("bases")(rec)` and `from
    operator import attrgetter as ag; ag2 = ag; ag2("bases")(rec)` are the
    identical dynamic reads as the unaliased/singly-aliased spellings --
    this function's own first revision claimed a `Call`-typed value (the
    *result* of `attrgetter(...)`) has no simple assignment shape to chain
    through, which is true but irrelevant: `op`/`ag`/`attrgetter` are
    themselves ordinary references (a module object, a builtin callable)
    *before* being called, and a plain `ast.Name`-valued assignment of
    either chains exactly the way `_builtins_getattr_aliases()`'s own
    `getattr`/`builtins` resolution already does. Fixed by reusing the
    identical fixed-point assignment-chaining pattern -- every plain-`Name`
    target of an `ast.Assign` (including every target of a chained
    assignment, `op2 = op3 = op`) or `ast.AnnAssign` is collected as a
    candidate, then repeatedly folded into either name set until a pass
    adds nothing new. The `_is_attrgetter_constructor_call()`'s own
    docstring wording about `attrgetter` getting "no local-alias
    resolution" refers to a *different* thing -- a value ASSIGNED FROM a
    *constructed getter* (`getter = attrgetter(...); getter(rec)`, still
    correctly out of scope, see that function's own docstring) -- not the
    module/callable references resolved here.

    **A *qualified* assignment (`ag = op.attrgetter`, given a resolved
    `operator` alias `op`) is resolved too (Codex review, fresh
    evidence, third round on this same helper).** `import operator as
    op; ag = op.attrgetter; ag("bases")(rec)` -- the identical dynamic
    read as every other alias shape this function already covers -- was
    invisible, since `_add_candidate()` only ever recognized a plain
    `ast.Name` RHS, never an `ast.Attribute` one, mirroring the identical
    gap `_builtins_getattr_aliases()`'s own `qualified_candidates`
    mechanism already closes for `read_attr = builtins.getattr`. Fixed
    the same way: a qualified `X.attrgetter` assignment is collected
    separately (`qualified_candidates`) during the same walk, then
    resolved once *after* the walk finishes -- since, unlike the plain-
    name chain, this needs the *complete* `operator_names` set to know
    whether `X` really is a resolved `operator` alias, exactly the
    reason `_builtins_getattr_aliases()`'s own qualified resolution runs
    after its own import-collection walk too.

    **Seeded only from a verified import, not unconditionally the way
    `_builtins_getattr_aliases()` seeds bare `"getattr"` (Codex review,
    fresh evidence).** `getattr` is a real Python builtin, always in
    scope with no import required, so seeding it unconditionally is
    correct -- but `attrgetter`/`operator` are not builtins; they mean
    nothing until a real `import operator`/`from operator import
    attrgetter` actually happens. Unconditionally seeding the bare
    spellings anyway meant a module-level `def attrgetter(name): ...` (an
    ordinary, unrelated local function reusing the name, no `operator`
    import anywhere in the file) followed by `attrgetter("bases")(rec)`,
    or `operator = SomeUnrelatedHelper()` followed by `operator.
    attrgetter("bases")(rec)`, both read as the real standard-library
    callable -- and neither is a *parameter* shadow, the only shadow
    shape `_shadowed()` ever checks, so nothing excluded either. Fixed by
    starting both sets empty and relying entirely on the import-detection
    walk below (which already adds the exact bare spelling whenever a
    real, unaliased `import operator`/`from operator import attrgetter`
    is found, via its own `alias.asname or alias.name` fallback) --
    exactly the same "no import, no identity" contract `_builtins_
    getattr_aliases()` already applies to the bare `"builtins"` module
    name.
    """
    attrgetter_names: set[str] = set()
    operator_names: set[str] = set()
    getitem_names: set[str] = set()
    itemgetter_names: set[str] = set()
    assign_candidates: list[tuple[str, str]] = []
    # `local = <module-name>.attrgetter` -- resolved once, after the walk
    # below has finished collecting every `import operator` occurrence,
    # since (unlike the plain-name candidates) this needs the *complete*
    # `operator_names` set to know whether `<module-name>` really is one
    # (mirroring `_builtins_getattr_aliases()`'s own identical two-phase
    # resolution for `read_attr = builtins.getattr`).
    qualified_candidates: list[tuple[str, str]] = []
    # `local = <module-name>.getitem` -- the identical two-phase
    # resolution as `qualified_candidates` above, kept as a separate list
    # since it seeds a different name family (`getitem_names`, not
    # `attrgetter_names`).
    qualified_getitem_candidates: list[tuple[str, str]] = []
    # `local = <module-name>.itemgetter` -- the identical two-phase
    # resolution again, seeding `itemgetter_names` instead.
    qualified_itemgetter_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
        elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            if value.attr == "attrgetter":
                qualified_candidates.append((target, value.value.id))
            elif value.attr == "getitem":
                qualified_getitem_candidates.append((target, value.value.id))
            elif value.attr == "itemgetter":
                qualified_itemgetter_candidates.append((target, value.value.id))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "operator":
            for alias in node.names:
                if alias.name == "attrgetter":
                    attrgetter_names.add(alias.asname or alias.name)
                elif alias.name == "getitem":
                    getitem_names.add(alias.asname or alias.name)
                elif alias.name == "itemgetter":
                    itemgetter_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "operator":
                    operator_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in operator_names:
                continue
            if ref in operator_names:
                operator_names.add(local)
                changed = True
    for local, base in qualified_candidates:
        if base in operator_names:
            attrgetter_names.add(local)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in attrgetter_names:
                continue
            if ref in attrgetter_names:
                attrgetter_names.add(local)
                changed = True
    for local, base in qualified_getitem_candidates:
        if base in operator_names:
            getitem_names.add(local)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in getitem_names:
                continue
            if ref in getitem_names:
                getitem_names.add(local)
                changed = True
    for local, base in qualified_itemgetter_candidates:
        if base in operator_names:
            itemgetter_names.add(local)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in itemgetter_names:
                continue
            if ref in itemgetter_names:
                itemgetter_names.add(local)
                changed = True
    return (
        frozenset(attrgetter_names),
        frozenset(operator_names),
        frozenset(getitem_names),
        frozenset(itemgetter_names),
    )


def _is_attrgetter_constructor_call(
    node: ast.expr, attrgetter_names: frozenset[str], operator_names: frozenset[str]
) -> bool:
    """True for a `Call` node constructing an `operator.attrgetter(...)`
    getter -- the qualified spelling through any resolved alias of the
    `operator` module (`operator_names`), or the bare spelling through any
    resolved alias of `attrgetter` itself (`attrgetter_names`, always
    covering `from operator import attrgetter` and its own `as` alias, see
    `_operator_attrgetter_aliases`) -- with at least one positional
    argument (the attribute name(s) to read). The caller is responsible for
    checking that the requested arguments are literal, single-name strings
    matching a recognized field -- this helper only recognizes the
    *constructor*, not the field(s) it will read. Matched wherever the
    constructor call itself occurs -- immediately invoked
    (`attrgetter("bases")(rec)`), assigned to an intermediate variable
    before being called (`getter = attrgetter("bases"); getter(rec)`), or
    handed to another function as a callback (`sorted(records,
    key=attrgetter("bases"))`) -- since the field will be read on whatever
    the constructed getter is eventually called with, regardless of how
    that call happens (Codex review, fresh evidence: matching only an
    immediate outer call missed the equally common callback spelling
    entirely; see `unmigrated_fact_reader_sites()`'s own attrgetter branch
    for the full reasoning). This is a genuine improvement over needing
    dedicated alias tracking for the constructed *getter object* itself
    (no `x = attrgetter(...)` equivalent of `_builtins_getattr_aliases()`'s
    own alias-chain tracking is needed here, since the constructor call is
    matched directly rather than needing to be traced through to wherever
    it's eventually called).
    """
    return (
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "attrgetter"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in operator_names
            )
            or (isinstance(node.func, ast.Name) and node.func.id in attrgetter_names)
        )
        and len(node.args) >= 1
    )


def _attrgetter_matched_name(node: ast.Call) -> str:
    """Return the bare local name that made `_is_attrgetter_constructor_
    call()` return `True` for *node* -- the qualifying module name for the
    `operator.attrgetter(...)` spelling, or the callable name itself for
    the bare `attrgetter(...)` spelling. Used to check that name against
    `_locally_bound_names()` for shadowing (Codex review, fresh evidence:
    an unrelated local parameter named `operator`/`attrgetter` shadows the
    real module/callable exactly the way one named `getattr` can, but this
    call site never consulted the shadowing check at all). The caller is
    responsible for confirming `_is_attrgetter_constructor_call()` already
    returned `True` for *node* -- this only re-derives which of its two
    recognized shapes actually matched, narrowly typed so the caller
    doesn't need its own unchecked `ast.Attribute`/`ast.Name` assumption.
    """
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return node.func.value.id
    assert isinstance(node.func, ast.Name)
    return node.func.id


def _is_itemgetter_constructor_call(
    node: ast.expr, itemgetter_names: frozenset[str], operator_names: frozenset[str]
) -> bool:
    """True for a `Call` node constructing an `operator.itemgetter(...)`
    getter -- the `attrgetter`-shaped sibling of
    `_is_attrgetter_constructor_call()`, for subscript access instead of
    attribute access (Codex review, fresh evidence). Unlike the attrgetter
    case, this predicate alone is not treated as a match at the point of
    *construction* -- `unmigrated_fact_reader_sites()`'s own itemgetter
    branch (for this *unassigned* shape specifically -- a getter assigned
    to a variable first is tracked separately, by `_itemgetter_alias_
    keys()`, and does not go through this same call site again) requires
    the constructed getter to be called immediately, on a real mapping
    receiver (`operator.itemgetter("bases")(vars(rec))`), mirroring the
    `_is_mapping_receiver()`-gated shape every other subscript-reading
    form in this module already uses (`dict.get`, `operator.getitem`,
    ...) rather than the attrgetter branch's own wider "match wherever
    constructed, regardless of how the getter is later used" stance. The
    two behave differently for a structural reason: `attrgetter("bases")
    (x)` reads `x.bases` for *any* `x` -- there's no narrower receiver
    shape to require -- while `itemgetter("bases")(x)` reads `x["bases"]`,
    which is exactly as legitimate for an ordinary, unrelated mapping as
    for an instance's own `vars()`/`__dict__`, so requiring a real
    mapping receiver keeps this form no noisier than its already-shipped
    `dict.get`/`operator.getitem` siblings.

    At least one positional argument, not exactly one (Codex review, fresh
    evidence, second round): `operator.itemgetter("foo", "bases")(x)`
    returns a getter reading *both* keys as a tuple -- real, documented
    `itemgetter` behavior -- so the caller must inspect every constructor
    argument for a bridged name, the identical multi-key handling
    `_is_attrgetter_constructor_call()` already applies.
    """
    return (
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "itemgetter"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in operator_names
            )
            or (isinstance(node.func, ast.Name) and node.func.id in itemgetter_names)
        )
        and len(node.args) >= 1
    )


def _itemgetter_matched_name(node: ast.Call) -> str:
    """The `_attrgetter_matched_name()` sibling for an
    `_is_itemgetter_constructor_call()`-matched node -- same contract,
    same caller responsibility, for `itemgetter`/`operator` instead of
    `attrgetter`/`operator`.
    """
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return node.func.value.id
    assert isinstance(node.func, ast.Name)
    return node.func.id


def _itemgetter_alias_keys(
    tree: ast.Module, itemgetter_names: frozenset[str], operator_names: frozenset[str]
) -> dict[str, list[str]]:
    """Return every local name *tree* binds, via a plain `ast.Assign`, an
    `ast.AnnAssign`, or an `ast.NamedExpr` (the same three binding forms
    `_mapping_receiver_aliases()` already treats uniformly), to the
    *result* of constructing an `operator.itemgetter(...)` getter --
    mapped to that constructor call's own literal, string-constant
    positional arguments (Codex review, fresh evidence). `get = operator.
    itemgetter("bases"); get(vars(rec))` reads the identical subscript
    value as the already-recognized immediate-call spelling
    (`operator.itemgetter("bases")(vars(rec))`), but storing the
    constructed getter in a variable before calling it is ordinary,
    common Python -- unlike a constructed-but-never-called getter with no
    assignment at all, which `_is_itemgetter_constructor_call()`'s own
    docstring already accepts as out of scope for the *unassigned* case
    only. The identical getter can also be bound via `get: object =
    operator.itemgetter("bases")` (an annotated assignment) or
    `(get := operator.itemgetter("bases"))(vars(rec))` (a named
    expression/walrus) -- both are covered here too (Codex review, fresh
    evidence), not just the plain `ast.Assign` form.

    Deliberately narrower than `_operator_attrgetter_aliases()`'s own
    name-only alias tracking: a bare `Name`-valued alias of `itemgetter`
    itself (`ig = itemgetter; ig("bases")(...)`) is already covered by
    `itemgetter_names` resolving `ig` the ordinary way -- what's tracked
    *here* is specifically the *result* of calling that (already-
    resolved) constructor, so the RHS must itself satisfy
    `_is_itemgetter_constructor_call()`, not merely reference a name in
    `itemgetter_names`.

    **Not chased through a further plain-name alias
    (`get = operator.itemgetter("bases"); get2 = get; get2(vars(rec))`)
    -- the identical "no type inference beyond one hop" limit this
    module already accepts for a comparable case.** A getter constructed
    but assigned to a *second* name before being called is a real gap,
    left open here rather than adding a second fixed-point chain for a
    narrower and rarer pattern than the one this fix targets.

    A name assigned more than once anywhere in *tree* (any RHS shape, not
    just a repeated itemgetter-constructor assignment) is dropped
    entirely rather than keeping either assignment's own key list -- the
    alias is ambiguous by the second assignment, and guessing which one a
    later call actually used would risk fabricating a false positive, the
    same "no partial/best-effort attribution" principle
    `_paired_unpacking_candidates()`'s own docstring already states for a
    structurally different reason.
    """
    assign_counts: dict[str, int] = {}
    candidate_keys: dict[str, list[str]] = {}

    def _record(target: str, value: ast.expr | None) -> None:
        if value is None:
            return
        assign_counts[target] = assign_counts.get(target, 0) + 1
        if _is_itemgetter_constructor_call(value, itemgetter_names, operator_names):
            assert isinstance(value, ast.Call)
            candidate_keys[target] = [
                arg.value
                for arg in value.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]

    for node in ast.walk(tree):
        # Every plain-name binding form this module's other alias
        # collectors already treat uniformly (`_mapping_receiver_
        # aliases()`'s own identical three-branch walk), not just
        # `ast.Assign` (Codex review, fresh evidence): `get: object =
        # operator.itemgetter("bases")` (an annotated assignment) and
        # `(get := operator.itemgetter("bases"))(vars(rec))` (a named
        # expression) construct and bind the identical getter, just
        # through a different Python binding statement.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _record(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _record(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _record(node.target.id, node.value)
    return {
        name: keys for name, keys in candidate_keys.items() if assign_counts[name] == 1
    }

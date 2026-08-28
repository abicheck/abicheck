#!/usr/bin/env python3
"""Lexical-scope resolution primitives for ``fact_detector_misuse.py``'s
`fact-detector-misuse` AI-readiness check (ADR-063 Phase 0,
docs/contribute/plans/one-semantic-pipeline.md).

Split out of ``fact_detector_misuse.py`` once that module crossed the
AI-readiness gate's own 2000-line hard cap (``file-size`` check) --
mechanical extraction, not a redesign: every function here is unchanged
from its original home, moved as a contiguous, self-contained block
(``_enclosing_qualnames``/``_qualname_at``/``_QualnameSpans``,
``_lexical_function_parents``, ``_def_containing_qualnames``,
``_bound_names``, ``_paired_unpacking_candidates``,
``_match_pattern_names``) -- the shared "resolve a position or a name to
its enclosing lexical scope" building blocks the rest of that module's
own alias-resolution machinery (``_fact_aliases``,
``_default_and_annotation_scope_overrides``,
``fact_equality_misuse_sites``) builds on. Deliberately does **not**
share these with ``fact_field_readers.py``'s own, differently-shaped
same-named helpers -- see that module's own docstring for why the two
checks keep independent copies rather than a shared abstraction.

No public entry point of its own; imported by ``fact_detector_misuse.py``
only. Pure stdlib, importable before ``pip install -e .``, matching every
other AI-readiness leaf module's own constraint.
"""

from __future__ import annotations

import ast


def _enclosing_qualnames(tree: ast.Module) -> _QualnameSpans:
    """Return every named scope's exact `((start_line, start_col),
    (end_line, end_col), qualname)` span in *tree* -- resolve a query
    position to its innermost enclosing scope's qualname via
    :func:`_qualname_at`, not by indexing this list directly.

    Deliberately a standalone copy of `fact_field_readers.py`'s identical
    helper rather than a shared import -- see `FACT_FIELD_NAMES`'s own
    docstring for why these two leaf modules stay decoupled.

    **Each `def`'s own line number is folded into its qualname (Codex
    review, fresh evidence): a `@typing.overload`-decorated stub and its
    real implementation share one bare name (`"f"`) -- two genuinely
    separate function objects/scopes, exactly as distinct as any other two
    same-named functions in different modules, but this scan's own
    dotted-name qualname collapsed them to one key. `_fact_aliases` then
    merged their alias/candidate data: a stub parameter annotated
    `Fact[...]` fed the real implementation's own unrelated `x` local, so
    a valid `x == other` comparison in the implementation was reported as
    a hard error. Unlike `fact_field_readers.py`'s identical-looking
    helper, nothing here persists a qualname in an external baseline key
    (`fact_equality_misuse_sites` returns only `(lineno, col_offset)`), so
    there is no format to keep stable -- appending `#<lineno>` unique to
    each `def` costs nothing outside this module and gives every function
    definition its own real scope identity while `_lexical_function_
    parents` (below) keeps the identical lineage relationship.

    **A class body's own top-level statements now get their own scope
    range too, distinct from whatever function encloses the class (Codex
    review, fresh evidence).** Previously a `ClassDef` contributed no
    range of its own -- only a nested `FunctionDef`/method did -- so a
    class-body-level assignment (`fact = rec.bases_fact` written directly
    in the class body, a real, if unusual, pattern) fell through to
    whichever *function* range happened to enclose the class and was
    treated as if it were that function's own local, when a class body is
    actually its own separate namespace (an ordinary name assigned there
    is a class attribute, visible only as `self.x`/`Class.x`, never as a
    bare name to a method the way a real enclosing function's local would
    be -- the same rule `_lexical_function_parents` already encodes for
    the opposite direction). Fixed by adding one range per `ClassDef`
    (`f"{prefix}{child.name}#{child.lineno}<class-body>"`) the identical
    way a `FunctionDef` already gets one -- a nested method's own,
    narrower range still overrides it for the method's own lines (the
    same size-sorted-ranges mechanism that already lets a nested `def`
    override its enclosing one), so only genuinely class-body-level code
    (not inside any method) lands on this new scope. Nothing ever looks
    this qualname up as a lexical *parent* (`_lexical_function_parents`
    only ever produces function-def-derived keys), so a class-body
    scope's own aliases simply go unused by anything else -- exactly the
    outcome wanted, since real Python gives them no visibility outside
    the class body itself either.

    **A lambda and a comprehension (`list`/`set`/`dict`/generator) each
    get their own scope range too, the identical way a `def` already does
    (Codex review, fresh evidence).** Both are real Python closures over
    their enclosing scope, with their own local bindings (a lambda's
    parameters; a comprehension's `for` targets) -- `fact = rec.
    bases_fact` outer, then `(lambda fact: fact == other)(1)` or
    `[fact == other for fact in values]`, each shadow the outer alias
    with an unrelated local exactly the way a nested `def`'s own parameter
    or `for`-loop target already does, but previously neither introduced
    a scope of its own at all: every line inside either was silently
    attributed to whatever *function* enclosed it, so the lambda
    parameter/comprehension target was never recorded as a local
    binding there, and the outer alias leaked straight through. Fixed the
    same way as a `def` -- a dedicated range (keyed
    `f"{prefix}<lambda>#{child.lineno}:{child.col_offset}"`/
    `f"{prefix}<comp>#{child.lineno}:{child.col_offset}"`, disambiguating
    by column too since several could otherwise share one line) --
    wired into `_lexical_function_parents` identically below, and into
    `_fact_aliases`'s own binding collection (see that function's own
    docstring for the parameter/target-recording half of this fix).
    **The comprehension's *outermost* iterable is a documented exception,
    resolved to the enclosing scope rather than the comprehension's own
    (Codex review, fresh evidence -- this paragraph originally predicted
    this exact case as "vanishingly rare, and not the shape any review
    round has found," then a later round found it).** Only the element
    expression, any `if` filters, and every non-first `for`'s iterable
    actually run inside the comprehension's own scope in real Python; the
    *first* generator's iterable evaluates before the comprehension's
    implicit function is even called, in whatever scope directly contains
    the comprehension. `fact = rec.bases_fact; [x for fact in (fact ==
    other,)]` -- the comparison inside the first iterable reads the
    *outer* `fact`, since the comprehension-local `fact` target doesn't
    exist yet at that point, but attributing the whole comprehension
    (outermost iterable included) to its own scope wrongly treated it as
    already shadowed. `visit()` now registers a narrower override span for
    just the outermost iterable's own range, tagged with the *incoming*
    qualname (the scope active before entering the comprehension) rather
    than the comprehension's own -- `_qualname_at`'s smallest-span-first
    resolution picks this override for any position inside that one
    iterable, while everything else in the comprehension still resolves to
    the comprehension's own broader span.

    **Resolved by *position* (line and column), not by line alone (Codex
    review, fresh evidence).** A line-keyed map -- `dict[int, str]`, one
    qualname per physical line -- was fine while only a `def`/`class`
    (each normally its own multi-line block) introduced a scope, but a
    lambda or comprehension is routinely a small piece of a much larger
    expression sharing its line with code that is NOT part of it at all:
    `fact = rec.bases_fact` outer, then `(lambda fact: fact == other)(1);
    return fact == other` -- both statements sit on the very next line,
    so the line-keyed map could only record ONE qualname for that whole
    line, and the lambda's own (later-processed, narrower-in-line-count)
    range unconditionally won, silently reattributing the *second*,
    unrelated `fact == other` to the lambda's shadowing scope too --
    hiding a real misuse, not merely producing a spurious one. Fixed by
    returning a `list` of exact `((start_line, start_col), (end_line,
    end_col), qualname)` spans instead of a `dict[int, str]`, resolved by
    :func:`_qualname_at` -- the smallest span whose `(start, end)`
    lexicographically brackets a query `(lineno, col_offset)` position,
    not merely the smallest span sharing its *line*. Every caller that
    previously did `qualnames.get(node.lineno, "<module>")` now does
    `_qualname_at((node.lineno, node.col_offset), qualnames)`.
    """
    spans: list[tuple[tuple[int, int], tuple[int, int], str]] = []

    def _span(child: ast.stmt | ast.expr) -> tuple[tuple[int, int], tuple[int, int]]:
        start = (child.lineno, child.col_offset)
        end = (
            child.end_lineno if child.end_lineno is not None else child.lineno,
            child.end_col_offset
            if child.end_col_offset is not None
            else child.col_offset,
        )
        return start, end

    def dispatch(node: ast.AST, prefix: str, qualname: str) -> None:
        """Match *node* against every scope-introducing shape this
        function recognizes, exactly the way `visit()`'s own loop used to
        match each of `node`'s *children* inline -- factored out so a
        *specific* node (not "every child of a container") can be treated
        as a candidate in its own right (Codex review, fresh evidence: the
        comprehension branch below needs to dispatch its own `elt`/
        `generators[0].iter`/etc. individually, and `visit(node, ...)`
        only ever tests `node`'s children, so calling it on a bare
        expression that might itself be a `Lambda`/comprehension --
        `[lambda: x for x in y]`'s own `elt`, for instance -- would
        silently skip matching that expression itself).
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            child_qualname = f"{prefix}{node.name}#{node.lineno}"
            start, end = _span(node)
            spans.append((start, end, child_qualname))
            visit(node, child_qualname + ".", child_qualname)
        elif isinstance(node, ast.Lambda):
            child_qualname = f"{prefix}<lambda>#{node.lineno}:{node.col_offset}"
            start, end = _span(node)
            spans.append((start, end, child_qualname))
            visit(node, child_qualname + ".", child_qualname)
        elif isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            child_qualname = f"{prefix}<comp>#{node.lineno}:{node.col_offset}"
            start, end = _span(node)
            spans.append((start, end, child_qualname))
            # The OUTERMOST generator's own iterable evaluates in the
            # ENCLOSING scope -- before the comprehension's implicit
            # function is even called -- not inside the comprehension's
            # own new scope (Codex review, fresh evidence for the exact
            # case this function's own docstring above already named as
            # "vanishingly rare, and not the shape any review round has
            # found": `fact = rec.bases_fact; [x for fact in (fact ==
            # other,)]` -- the comparison inside the first iterable
            # reads the *outer* `fact`, not the comprehension's own
            # shadowing target).
            #
            # **A first version of this fix dispatched the outermost
            # iterable once under the old scope, then still re-walked the
            # *whole* comprehension afterward -- a further round found
            # this reaches the same iterable a second time, silently
            # re-registering any closure inside it under the wrong scope
            # (Codex review, fresh evidence): `[x for fact in (lambda
            # y=fact: (y == other,))()]` -- the lambda's own default is
            # correctly dispatched once, under the outer scope, but the
            # blanket re-walk then reached the identical lambda again and
            # re-registered it under the comprehension's own scope
            # instead. Harmless for *this* function's own `spans` list
            # (both entries share the identical span, and `_qualname_at`'s
            # smallest-span-first tie-break never lets a same-size later
            # entry replace an earlier one) -- but the identical
            # duplicate-walk shape in `_lexical_function_parents`/
            # `_def_containing_qualnames` below uses a plain `dict`, where
            # a same-key second write unconditionally *overwrites* the
            # first.** Fixed uniformly across all three functions: the
            # outermost iterable is dispatched exactly once, under the old
            # scope, and everything else in the comprehension is now
            # dispatched explicitly, field by field, rather than through a
            # blanket walk that would reach the same iterable again.
            if node.generators:
                first_iter = node.generators[0].iter
                i_start, i_end = _span(first_iter)
                spans.append((i_start, i_end, qualname))
                dispatch(first_iter, prefix, qualname)
            if isinstance(node, ast.DictComp):
                dispatch(node.key, child_qualname + ".", child_qualname)
                dispatch(node.value, child_qualname + ".", child_qualname)
            else:
                dispatch(node.elt, child_qualname + ".", child_qualname)
            for index, generator in enumerate(node.generators):
                dispatch(generator.target, child_qualname + ".", child_qualname)
                if index > 0:
                    dispatch(generator.iter, child_qualname + ".", child_qualname)
                for cond in generator.ifs:
                    dispatch(cond, child_qualname + ".", child_qualname)
        elif isinstance(node, ast.ClassDef):
            class_qualname = f"{prefix}{node.name}#{node.lineno}<class-body>"
            start, end = _span(node)
            spans.append((start, end, class_qualname))
            visit(node, f"{prefix}{node.name}.", class_qualname)
        else:
            visit(node, prefix, qualname)

    def visit(node: ast.AST, prefix: str, qualname: str) -> None:
        for child in ast.iter_child_nodes(node):
            dispatch(child, prefix, qualname)

    visit(tree, "", "<module>")
    return spans


#: A parsed source span, as returned by :func:`_enclosing_qualnames` and
#: consumed by :func:`_qualname_at`: `(start, end, qualname)`, each of
#: `start`/`end` a `(line, col)` position.
_QualnameSpans = list[tuple[tuple[int, int], tuple[int, int], str]]


def _qualname_at(pos: tuple[int, int], spans: _QualnameSpans) -> str:
    """Return the innermost scope's qualname whose span contains *pos*
    (a `(lineno, col_offset)` position), or `"<module>"` if none does.

    "Innermost" means the smallest containing span, not merely the first
    one found -- every span this module ever produces nests strictly
    inside its lexical parent's own span (a laminar family, since each is
    built from `ast.iter_child_nodes`'s own parent/child structure), so
    comparing by `(end_line - start_line, end_col - start_col)` -- line
    span first, column span only as a tiebreaker for two spans starting
    and ending on the same lines -- always picks the correctly-nested one
    among every span that contains *pos*.
    """
    best: str | None = None
    best_size: tuple[int, int] | None = None
    for start, end, qualname in spans:
        if start <= pos <= end:
            size = (end[0] - start[0], end[1] - start[1])
            if best_size is None or size < best_size:
                best = qualname
                best_size = size
    return best if best is not None else "<module>"


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

    **A `def`/`lambda`'s own default values, parameter annotations, return
    annotation, and decorators are visited with the *old* `nearest_func`,
    not the new one (Codex review, fresh evidence).** All of these
    evaluate at def/lambda-creation time, in whatever scope was active
    *before* the def/lambda's own qualname takes over -- the identical
    binding-timing rule `_fact_aliases()`'s/`_default_and_annotation_
    scope_overrides()`'s own default/annotation handling already relies
    on. But the blanket `visit(child, qualname + ".", qualname)` this
    function used for a `FunctionDef`/`Lambda` recursed into *every* one
    of its children -- default values included -- with the *new* qualname
    already in effect, so a lambda or comprehension found inside a
    default was wrongly parented under the function it's a default *of*,
    not the scope that actually surrounds the `def`/`lambda` statement:
    `fact = rec.bases_fact` in `f`, then `def g(fact, cb=[fact == other
    for _ in xs]): ...` -- the comprehension executes while `g` is being
    *defined* (i.e. in `f`'s own scope, before `g`'s own parameter `fact`
    even exists to shadow anything) and genuinely closes over `f`'s alias,
    but was parented under `g` instead, where `g`'s own same-named
    parameter incorrectly shadowed it. Fixed by splitting the dispatch: a
    def-time subtree (`def_time_subtrees()`, mirroring `_default_and_
    annotation_scope_overrides()`'s own `subtrees` construction) is
    re-dispatched with the *old* `nearest_func`, while only the function's
    real body executes with the new one.
    """
    parents: dict[str, str] = {}

    def def_time_subtrees(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> list[ast.AST]:
        args = node.args
        # `kw_defaults` pairs positionally with `kwonlyargs`, `None` for a
        # keyword-only parameter with no default at all -- a real,
        # ordinary element of this list, not a bug, so it must be filtered
        # before re-dispatching (matching `_default_and_annotation_scope_
        # overrides()`'s own identical `if subtree is None: continue`).
        subtrees: list[ast.AST] = [
            *args.defaults,
            *(d for d in args.kw_defaults if d is not None),
        ]
        for arg in (
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
            *((args.vararg,) if args.vararg else ()),
            *((args.kwarg,) if args.kwarg else ()),
        ):
            if arg.annotation is not None:
                subtrees.append(arg.annotation)
        returns = getattr(node, "returns", None)
        if returns is not None:
            subtrees.append(returns)
        decorator_list = getattr(node, "decorator_list", None)
        if decorator_list:
            subtrees.extend(decorator_list)
        return subtrees

    def dispatch(child: ast.AST, prefix: str, nearest_func: str) -> None:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # `#<lineno>` disambiguator, matching `_enclosing_
            # qualnames`'s own scheme exactly (Codex review, fresh
            # evidence -- see that function's own docstring): two
            # same-named `def`s (e.g. an `@overload` stub and its real
            # implementation) must resolve to two distinct scopes here
            # too, not just in the line-number map, or this function's
            # own `parents` dict would still let one collapse onto the
            # other's already-processed entry.
            qualname = f"{prefix}{child.name}#{child.lineno}"
            parents[qualname] = nearest_func
            for subtree in def_time_subtrees(child):
                dispatch(subtree, qualname + ".", nearest_func)
            for stmt in child.body:
                dispatch(stmt, qualname + ".", qualname)
        elif isinstance(child, ast.Lambda):
            # A lambda is a real closure scope too, the identical
            # shape as a `def` (Codex review, fresh evidence -- see
            # `_enclosing_qualnames`'s own docstring): it both closes
            # over its enclosing function's locals *and* can itself be
            # closed over by anything nested inside it, so it becomes
            # the new `nearest_func` for its own body exactly like a
            # named function does -- but (see this function's own
            # docstring) not for its own default values.
            qualname = f"{prefix}<lambda>#{child.lineno}:{child.col_offset}"
            parents[qualname] = nearest_func
            for subtree in def_time_subtrees(child):
                dispatch(subtree, qualname + ".", nearest_func)
            dispatch(child.body, qualname + ".", qualname)
        elif isinstance(
            child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            # A comprehension is a real closure scope too, in Python 3
            # -- same reasoning and same treatment as a lambda. Unlike a
            # def/lambda, a comprehension has no default-value/annotation
            # concept of its own -- but its *outermost* generator's own
            # iterable is a real exception, evaluating in the enclosing
            # scope before the comprehension's implicit function is even
            # called (Codex review, fresh evidence -- mirroring
            # `_enclosing_qualnames`'s own identical fix and docstring for
            # the exact reasoning): re-dispatched with the *old*
            # `nearest_func` so a closure found there (not merely a bare
            # read, which `_enclosing_qualnames`'s own span override
            # already handles) still resolves against the outer scope.
            #
            # **Dispatched exactly once, not also reachable through a
            # later blanket walk (Codex review, fresh evidence, mirroring
            # `_enclosing_qualnames`'s own identical follow-up fix and
            # docstring for the full reasoning).** A first version of this
            # fix still finished with a blanket `visit(child, qualname +
            # ".", qualname)`, which reaches the same outermost iterable a
            # *second* time and re-registers any closure inside it under
            # the wrong (comprehension's own) scope -- and since `parents`
            # is a plain `dict`, that second, wrong write unconditionally
            # overwrites the first, correct one (no smallest-span
            # tie-break to protect it the way `_enclosing_qualnames`'s own
            # `spans` list has). Fixed by never re-walking the outermost
            # iterable at all: every other part of the comprehension is
            # now dispatched explicitly, field by field, instead of
            # through a blanket walk.
            qualname = f"{prefix}<comp>#{child.lineno}:{child.col_offset}"
            parents[qualname] = nearest_func
            if child.generators:
                dispatch(child.generators[0].iter, prefix, nearest_func)
            if isinstance(child, ast.DictComp):
                dispatch(child.key, qualname + ".", qualname)
                dispatch(child.value, qualname + ".", qualname)
            else:
                dispatch(child.elt, qualname + ".", qualname)
            for index, generator in enumerate(child.generators):
                dispatch(generator.target, qualname + ".", qualname)
                if index > 0:
                    dispatch(generator.iter, qualname + ".", qualname)
                for cond in generator.ifs:
                    dispatch(cond, qualname + ".", qualname)
        elif isinstance(child, ast.ClassDef):
            # The class body's *own* scope inherits from whatever
            # encloses the `class` statement (ordinary Python LEGB
            # lookup -- a class body is executed once, as ordinary
            # code, and can read a bare name from its enclosing
            # function/module scope exactly like any other nested
            # block) -- but a *method* inside it must still skip past
            # this class layer entirely when computing its own
            # nearest enclosing function (Codex review, fresh
            # evidence: `fact = rec.bases_fact` outer, then `class C:
            # result = fact == other`, directly in the class body, not
            # inside a method -- the class-body scope this module
            # already gives its own qualname had no parent recorded
            # at all, so it could never inherit anything). Both are
            # true at once: record the class-body qualname's own
            # parent as `nearest_func` (using the identical
            # `<class-body>`-suffixed key `_enclosing_qualnames`
            # already produces for it), while recursing into the
            # class's own children with `nearest_func` *unchanged* --
            # a nested method's own qualname is a different key
            # entirely (built by the `FunctionDef` branch above), so
            # this class-body entry is never consulted when computing
            # a method's own parent.
            class_qualname = f"{prefix}{child.name}#{child.lineno}<class-body>"
            parents[class_qualname] = nearest_func
            visit(child, f"{prefix}{child.name}.", nearest_func)
        else:
            visit(child, prefix, nearest_func)

    def visit(node: ast.AST, prefix: str, nearest_func: str) -> None:
        for child in ast.iter_child_nodes(node):
            dispatch(child, prefix, nearest_func)

    visit(tree, "", "<module>")
    return parents


def _def_containing_qualnames(tree: ast.Module) -> dict[tuple[int, int], str]:
    """Map each `def`/`class` statement's own `(lineno, col_offset)` to the
    qualname of the scope that *directly, syntactically* contains it --
    unlike `_lexical_function_parents` above, this does NOT skip an
    intervening class layer (Codex review, fresh evidence): a `def`/`class`
    statement's own *name* binds into whatever namespace textually
    contains it, class body included -- Python's own `STORE_NAME`/
    `STORE_FAST` rule for a def/class statement, a different question from
    the closure-scope chain `_lexical_function_parents` answers (which
    intentionally treats a class body as invisible to a nested method's own
    free-variable lookup, but a `def`/`class` statement's binding target is
    never about free-variable lookup at all).

    Keyed by position rather than qualname string (unlike `_lexical_
    function_parents`'s `dict[str, str]`): the caller already has the
    node's own `(lineno, col_offset)` in hand at the point it needs this
    answer, and `_qualname_at((node.lineno, node.col_offset), qualnames)`
    is the wrong tool for this specific question -- a scope-introducing
    node's own span always starts at that exact position, so it is always
    that node's *own* smallest containing span, not its parent's; this
    dedicated walk tracks the containing scope explicitly instead of
    relying on span containment for a position that is, by construction,
    inside the very span whose *container* is being asked for.

    **A `Lambda`'s own containing scope is recorded too (Codex review,
    fresh evidence).** A lambda has no *name* to bind -- unlike a `def`/
    `class` statement, its introduction is an expression, not a statement
    -- but its own default values still evaluate at lambda-creation time
    in whatever scope directly contains it, the identical rule a method's
    default already relies on this function for. `fact = rec.bases_fact;
    cb = lambda x=fact: x == other` inside a function has no entry to
    resolve against without this, so both the pending-default alias
    resolution and the comparison-scope override below silently fell back
    to `"<module>"` regardless of the lambda's real containing scope.

    **A nested `def`/`lambda`'s own def-time subtrees are dispatched under
    the *old* scope, not the new one -- the identical fix
    `_lexical_function_parents` needed for the identical reason (Codex
    review, fresh evidence).** `fact = rec.bases_fact; def g(fact, cb=
    lambda x=fact: x == other): ...` -- the inner lambda's own `x=fact`
    default evaluates while `g` is being *defined*, in `f`'s own scope
    (before `g`'s own parameter `fact` even exists to shadow anything),
    so the lambda's real containing scope is `f`, not `g`. The previous
    unconditional `visit(child, qualname + ".", qualname)` for a `def`/
    `lambda` recursed into *every* child -- default values included --
    already under the new qualname, so the lambda's own entry in
    `containing` recorded `g` instead of `f`: `g`'s own same-named
    parameter `fact` then incorrectly appeared to shadow the lambda's
    `x=fact` default, silently missing the misuse. Fixed the same way
    `_lexical_function_parents` was: split the dispatch so a def-time
    subtree (`def_time_subtrees()`, an identical helper, duplicated here
    rather than shared -- see this module's own docstring for why a
    scoping helper is not shared across these two functions) is
    re-visited with the *old* `scope_qualname`, while only the real body
    executes under the new one.
    """
    containing: dict[tuple[int, int], str] = {}

    def def_time_subtrees(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> list[ast.AST]:
        args = node.args
        # `kw_defaults` pairs positionally with `kwonlyargs`, `None` for a
        # keyword-only parameter with no default at all -- a real,
        # ordinary element of this list, matching `_lexical_function_
        # parents`'s own identical filter.
        subtrees: list[ast.AST] = [
            *args.defaults,
            *(d for d in args.kw_defaults if d is not None),
        ]
        for arg in (
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
            *((args.vararg,) if args.vararg else ()),
            *((args.kwarg,) if args.kwarg else ()),
        ):
            if arg.annotation is not None:
                subtrees.append(arg.annotation)
        returns = getattr(node, "returns", None)
        if returns is not None:
            subtrees.append(returns)
        decorator_list = getattr(node, "decorator_list", None)
        if decorator_list:
            subtrees.extend(decorator_list)
        return subtrees

    def dispatch(child: ast.AST, prefix: str, scope_qualname: str) -> None:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{child.name}#{child.lineno}"
            containing[child.lineno, child.col_offset] = scope_qualname
            for subtree in def_time_subtrees(child):
                dispatch(subtree, qualname + ".", scope_qualname)
            for stmt in child.body:
                dispatch(stmt, qualname + ".", qualname)
        elif isinstance(child, ast.Lambda):
            qualname = f"{prefix}<lambda>#{child.lineno}:{child.col_offset}"
            containing[child.lineno, child.col_offset] = scope_qualname
            for subtree in def_time_subtrees(child):
                dispatch(subtree, qualname + ".", scope_qualname)
            dispatch(child.body, qualname + ".", qualname)
        elif isinstance(
            child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            qualname = f"{prefix}<comp>#{child.lineno}:{child.col_offset}"
            # The outermost generator's own iterable evaluates in the
            # enclosing scope, before the comprehension's implicit
            # function is even called (Codex review, fresh evidence --
            # mirroring `_lexical_function_parents`'s/`_enclosing_
            # qualnames`'s own identical fix): a `def`/`lambda`/`class`
            # found there is still contained by *this* scope
            # (`scope_qualname`), not the comprehension's own.
            #
            # Dispatched exactly once, not also reachable through a later
            # blanket walk (Codex review, fresh evidence, mirroring
            # `_lexical_function_parents`'s own identical follow-up fix
            # and docstring for the full reasoning): a blanket `visit(
            # child, qualname + ".", qualname)` here would reach the same
            # outermost iterable a second time and overwrite its correct
            # `containing[]` entry (and any closure found inside it) with
            # the comprehension's own, wrong scope -- `containing` is a
            # plain `dict`, so a second write always wins. Every other
            # part of the comprehension is dispatched explicitly instead.
            if child.generators:
                dispatch(child.generators[0].iter, prefix, scope_qualname)
            if isinstance(child, ast.DictComp):
                dispatch(child.key, qualname + ".", qualname)
                dispatch(child.value, qualname + ".", qualname)
            else:
                dispatch(child.elt, qualname + ".", qualname)
            for index, generator in enumerate(child.generators):
                dispatch(generator.target, qualname + ".", qualname)
                if index > 0:
                    dispatch(generator.iter, qualname + ".", qualname)
                for cond in generator.ifs:
                    dispatch(cond, qualname + ".", qualname)
        elif isinstance(child, ast.ClassDef):
            class_qualname = f"{prefix}{child.name}#{child.lineno}<class-body>"
            containing[child.lineno, child.col_offset] = scope_qualname
            # A base class, a keyword argument (e.g. a metaclass), and a
            # decorator all evaluate while the `class` *statement itself*
            # executes -- in whatever scope directly, syntactically
            # contains that statement -- never inside the new class's own
            # body (Codex review, fresh evidence): only the body's own
            # statements are actually contained by `class_qualname`.
            for base_or_keyword in (
                *child.bases,
                *(kw.value for kw in child.keywords),
            ):
                dispatch(base_or_keyword, f"{prefix}{child.name}.", scope_qualname)
            for deco in child.decorator_list:
                dispatch(deco, f"{prefix}{child.name}.", scope_qualname)
            for stmt in child.body:
                dispatch(stmt, f"{prefix}{child.name}.", class_qualname)
        else:
            visit(child, prefix, scope_qualname)

    def visit(node: ast.AST, prefix: str, scope_qualname: str) -> None:
        for child in ast.iter_child_nodes(node):
            dispatch(child, prefix, scope_qualname)

    visit(tree, "", "<module>")
    return containing


def _bound_names(target: ast.expr) -> list[str]:
    """Yield every plain name a single assignment/binding *target* binds,
    recursively unpacking `ast.Tuple`/`ast.List`/`ast.Starred` targets
    (Codex review, fresh evidence): `fact, other = pair` binds `fact` as
    an ordinary tuple-unpacking element, which a check restricted to a
    bare `ast.Name` target never saw -- so `fact` still read as the
    *outer* alias for the whole function it appears in, the same
    "unconditionally inherit the parent's alias set" false positive the
    shadowing fix already closed for a plain `fact = ...`/parameter
    binding, just reached through a different target shape. An
    `ast.Attribute`/`ast.Subscript` target (`obj.fact = x`, `d["fact"] =
    x`) binds no new *name* at all -- correctly ignored here, the same as
    the shadowing fix's existing exclusion.
    """
    names: list[str] = []
    if isinstance(target, ast.Name):
        names.append(target.id)
    elif isinstance(target, ast.Starred):
        names.extend(_bound_names(target.value))
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            names.extend(_bound_names(elt))
    return names


def _paired_unpacking_candidates(
    target: ast.expr, value: ast.expr
) -> list[tuple[str, ast.expr]]:
    """Recursively pair a tuple/list-unpacking *target* against a
    structurally matching *value*, returning a `(name, expr)` candidate
    for each plain-`Name` element that lines up with its own identifiable
    RHS sub-expression (Codex review, fresh evidence): `old_fact, new_fact
    = old.bases_fact, new.bases_fact` is an ordinary detector refactor of
    two independent Fact-typed values -- each target element genuinely has
    its own single value, exactly the way a chained assignment's every
    target does, but the *general* tuple-unpacking case (`a, b = pair`,
    where `pair` is one opaque value with no per-element sub-expression at
    all) has no such value to attribute, which is why the ordinary
    candidate-collection loop above deliberately skips every tuple/list
    target. Requires both sides to be literal `Tuple`/`List` displays, with
    no `Starred` element anywhere in *value* -- a starred value element
    (`x = (*a, b)`) is a genuine dynamic expansion of unknown length, so
    there is no way to know which value ends up at which target position,
    and the whole pairing stays untrustworthy regardless of the target's
    own shape.

    **A single `Starred` *target* element is handled, not rejected
    outright (Codex review, fresh evidence).** `fact, *rest = old.
    bases_fact, tag` still leaves `fact` definitively paired with `old.
    bases_fact` -- a starred target captures a runtime-length slice with
    no single corresponding value of its own, but the *fixed*-position
    elements before and after it still line up unambiguously against the
    value display's own (starless, so fixed-length) elements. Split via
    the identical before/after-the-star positional pairing
    :func:`_paired_match_sequence_candidates` already uses for a
    structural sequence pattern's own `MatchStar`, with the star element
    itself never producing a candidate (mirroring that function's
    identical treatment of `MatchStar`'s own captured name) -- only ever
    an ordinary local shadow via `_bound_names`, not a Fact-typed alias
    source. More than one `Starred` target element (invalid Python
    regardless) still returns no candidates at all, matching the
    length-mismatch/no-value-side-star cases: a partial pairing anywhere
    it can't be established risks attributing the wrong sub-expression to
    the wrong name. Nests through a further tuple/list target (`(a, (b,
    c)) = (x, (y, z))`, or a further-nested star, `(a, (b, *c)) = (x, (y,
    z, w))`) the identical way `_bound_names` already does.
    """
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if not isinstance(target, (ast.Tuple, ast.List)) or not isinstance(
        value, (ast.Tuple, ast.List)
    ):
        return []
    if any(isinstance(elt, ast.Starred) for elt in value.elts):
        # A starred *value* element (`x = (*a, b)`) is a genuine dynamic
        # expansion of unknown length -- there is no way to know, from the
        # display alone, which value ends up at which target position, so
        # the whole pairing stays untrustworthy regardless of the target's
        # own shape.
        return []
    star_positions = [
        i for i, elt in enumerate(target.elts) if isinstance(elt, ast.Starred)
    ]
    if len(star_positions) > 1:
        return []
    if not star_positions:
        if len(target.elts) != len(value.elts):
            return []
        pairs = list(zip(target.elts, value.elts))
    else:
        # `fact, *rest = rec.bases_fact, tag` -- a starred *target*
        # element captures a runtime-length slice with no single
        # corresponding value, but the *fixed*-position elements before
        # and after it still line up unambiguously against the value
        # display's own (starless, so fixed-length) elements, the
        # identical before/after-the-star split
        # `_paired_match_sequence_candidates()` already uses for a
        # structural sequence pattern (Codex review, fresh evidence: the
        # previous blanket "any Starred anywhere disqualifies everything"
        # rule discarded the `fact, *rest = old.bases_fact, tag` shape
        # entirely, even though `fact` is still definitively Fact-typed).
        star_index = star_positions[0]
        before, after = target.elts[:star_index], target.elts[star_index + 1 :]
        if len(before) + len(after) > len(value.elts):
            return []
        pairs = list(zip(before, value.elts[: len(before)]))
        if after:
            pairs += list(zip(after, value.elts[-len(after) :]))
    candidates: list[tuple[str, ast.expr]] = []
    for target_elt, value_elt in pairs:
        candidates.extend(_paired_unpacking_candidates(target_elt, value_elt))
    return candidates


def _paired_match_sequence_candidates(
    pattern: ast.MatchSequence, subject: ast.expr
) -> list[tuple[str, ast.expr]]:
    """Recursively pair a structural sequence pattern's own captures
    against a statically-known `Tuple`/`List` *subject*'s elements,
    positionally -- the `match`/`case` sibling of
    :func:`_paired_unpacking_candidates` (Codex review, fresh evidence):
    `match (rec.bases_fact, tag): case (fact, _): return fact == other`
    -- `fact` is definitively the subject tuple's first, Fact-typed
    element, but the existing whole-subject-capture handling only ever
    recognized `case.pattern` itself being a bare `ast.MatchAs`, never a
    *structural* pattern capturing a sub-part of the subject.

    Unlike `_paired_unpacking_candidates`'s own all-or-nothing stance (a
    length mismatch or a starred element anywhere disqualifies the whole
    pairing), a non-capturing sub-pattern at one position -- a wildcard
    `_`, a literal `MatchValue`, a `MatchClass` -- does *not* disqualify
    a real capture found at another position: a structural pattern
    routinely mixes captures with non-capturing sub-patterns, and that is
    completely ordinary, not a sign the pairing itself is unreliable. Only
    a length mismatch (accounting for at most one `MatchStar`, matching
    Python's own sequence-pattern grammar) makes the *whole* pairing
    untrustworthy, since then no position can be confidently attributed to
    the right subject element at all. A `MatchStar`'s own captured name
    (`case (fact, *rest):`) binds a runtime list, not a single value, and
    is deliberately not treated as a candidate here -- `_match_pattern_
    names()` already records it as an ordinary local bound name (a real
    shadow), just not as a Fact-typed alias source.

    Nests through a further sequence pattern matched against a further
    `Tuple`/`List` subject element (`case ((fact, _), tag):` against
    `((rec.bases_fact, "x"), tag)`) the identical way `_paired_unpacking_
    candidates()` already nests through a further tuple/list target.
    """
    if not isinstance(subject, (ast.Tuple, ast.List)):
        return []
    subject_elts = subject.elts
    patterns = pattern.patterns
    star_positions = [i for i, p in enumerate(patterns) if isinstance(p, ast.MatchStar)]
    if len(star_positions) > 1:
        return []
    if not star_positions:
        if len(patterns) != len(subject_elts):
            return []
        pairs = list(zip(patterns, subject_elts))
    else:
        star_index = star_positions[0]
        before, after = patterns[:star_index], patterns[star_index + 1 :]
        if len(before) + len(after) > len(subject_elts):
            return []
        pairs = list(zip(before, subject_elts[: len(before)]))
        if after:
            pairs += list(zip(after, subject_elts[-len(after) :]))
    candidates: list[tuple[str, ast.expr]] = []
    for sub_pattern, sub_subject in pairs:
        if isinstance(sub_pattern, ast.MatchAs) and sub_pattern.name is not None:
            candidates.append((sub_pattern.name, sub_subject))
        elif isinstance(sub_pattern, ast.MatchSequence):
            candidates.extend(
                _paired_match_sequence_candidates(sub_pattern, sub_subject)
            )
        elif isinstance(sub_pattern, ast.MatchMapping):
            candidates.extend(
                _paired_match_mapping_candidates(sub_pattern, sub_subject)
            )
    return candidates


def _paired_match_mapping_candidates(
    pattern: ast.MatchMapping, subject: ast.expr
) -> list[tuple[str, ast.expr]]:
    """Pair a structural mapping pattern's own captures against a
    statically-known `Dict` *subject*'s entries, by literal key -- the
    `MatchMapping` sibling of :func:`_paired_match_sequence_candidates`
    (Codex review, fresh evidence): `match {"fact": rec.bases_fact}: case
    {"fact": fact}: return fact == other` -- `fact` is definitively the
    subject dict's `"fact"` entry, but the sequence-only pairing above
    left every mapping pattern to the whole-subject-capture handling,
    which only ever recognizes `case.pattern` itself being a bare
    `MatchAs`/`MatchOr`-of-`MatchAs`, never a *structural* pattern
    capturing a sub-part of the subject.

    Unlike sequence pairing (positional, so a length/star mismatch can
    make the *whole* pairing untrustworthy), mapping pairing is by literal
    key: each of *pattern*'s own `keys` is looked up directly against
    *subject*'s own literal keys, independently of every other key, so a
    key present in the pattern but absent from (or unresolvable in) the
    subject simply contributes no candidate for that one key rather than
    disqualifying the others. Requires `subject` to be a literal `Dict`
    with every key a literal `ast.Constant` and no `**expansion` entry (a
    `None` key) -- a non-literal or dynamically-expanded subject key
    cannot be matched against a pattern's own literal key without runtime
    evaluation. A pattern's own non-literal key (Python's grammar allows
    only a literal or a dotted `value` pattern here) is symmetrically
    skipped rather than disqualifying the rest, for the same
    independent-per-key reason. `pattern.rest` (`case {**rest}:`) captures
    an arbitrary sub-mapping, not a single value, and -- mirroring
    `_paired_match_sequence_candidates()`'s identical treatment of
    `MatchStar`'s own captured name -- is deliberately not treated as a
    candidate here; `_match_pattern_names()` already records it as an
    ordinary local bound name. Nests through a further sequence/mapping
    pattern matched against a further literal subject entry the identical
    way sequence pairing already does.
    """
    if not isinstance(subject, ast.Dict) or any(k is None for k in subject.keys):
        return []
    subject_by_key: dict[object, ast.expr] = {}
    for key_expr, value_expr in zip(subject.keys, subject.values):
        if not isinstance(key_expr, ast.Constant):
            return []
        subject_by_key[key_expr.value] = value_expr
    candidates: list[tuple[str, ast.expr]] = []
    for key_expr, sub_pattern in zip(pattern.keys, pattern.patterns):
        if not isinstance(key_expr, ast.Constant):
            continue
        sub_subject = subject_by_key.get(key_expr.value)
        if sub_subject is None:
            continue
        if isinstance(sub_pattern, ast.MatchAs) and sub_pattern.name is not None:
            candidates.append((sub_pattern.name, sub_subject))
        elif isinstance(sub_pattern, ast.MatchSequence):
            candidates.extend(
                _paired_match_sequence_candidates(sub_pattern, sub_subject)
            )
        elif isinstance(sub_pattern, ast.MatchMapping):
            candidates.extend(
                _paired_match_mapping_candidates(sub_pattern, sub_subject)
            )
    return candidates


def _match_pattern_names(pattern: ast.pattern) -> list[str]:
    """Recursively collect every name a structural-pattern-matching
    `pattern` binds -- `case fact:` (a bare capture, `ast.MatchAs` with a
    `name`), `case [*rest]:` (`ast.MatchStar`), `case {**rest}:`
    (`ast.MatchMapping`'s own `rest`), and any of these nested inside a
    `MatchSequence`/`MatchMapping`/`MatchClass`/`MatchOr` (Codex review,
    fresh evidence): each is a real local binding, exactly like a `for`
    loop target or an `except ... as name:` handler, but nothing recorded
    it as one -- a nested function's own `case fact:` failed to shadow an
    outer `fact = rec.bases_fact` alias, flagging a valid `fact == other`
    against the captured (arbitrary) matched value. `MatchValue`/
    `MatchSingleton` bind nothing and are correctly ignored.
    """
    names: list[str] = []
    if isinstance(pattern, ast.MatchAs):
        if pattern.name is not None:
            names.append(pattern.name)
        if pattern.pattern is not None:
            names.extend(_match_pattern_names(pattern.pattern))
    elif isinstance(pattern, ast.MatchStar):
        if pattern.name is not None:
            names.append(pattern.name)
    elif isinstance(pattern, ast.MatchMapping):
        if pattern.rest is not None:
            names.append(pattern.rest)
        for sub in pattern.patterns:
            names.extend(_match_pattern_names(sub))
    elif isinstance(pattern, ast.MatchSequence):
        for sub in pattern.patterns:
            names.extend(_match_pattern_names(sub))
    elif isinstance(pattern, ast.MatchClass):
        for sub in (*pattern.patterns, *pattern.kwd_patterns):
            names.extend(_match_pattern_names(sub))
    elif isinstance(pattern, ast.MatchOr):
        for sub in pattern.patterns:
            names.extend(_match_pattern_names(sub))
    return names

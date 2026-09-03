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
and ``fact_detector_misuse_aliases.py`` (added once that second sibling
module was itself split out of ``fact_detector_misuse.py`` -- both need
``_iter_default_subtree``, so it moved here rather than staying a
private helper only one of the two could see). Pure stdlib, importable
before ``pip install -e .``, matching every other AI-readiness leaf
module's own constraint.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator


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
    candidates()` already nests through a further tuple/list target --
    delegated to `_paired_sub_pattern_candidates()`, which also unwinds a
    chained `MatchAs` (`case (fact as alias,):`) into every name along
    the chain and recurses into a structural sub-pattern *wrapped* by
    `MatchAs` (`case ((fact, _) as alias,):`), not just a bare structural
    sub-pattern at a position.

    Rejects a *starred* subject element (`match (*extras, rec.bases_fact):`)
    outright, the same all-or-nothing rule `_paired_unpacking_candidates()`
    already applies to a starred value display -- a dynamic expansion of
    unknown length means no position can be confidently attributed to a
    known subject element.
    """
    if not isinstance(subject, (ast.Tuple, ast.List)):
        return []
    subject_elts = subject.elts
    if any(isinstance(elt, ast.Starred) for elt in subject_elts):
        # A starred *subject* element (`match (*extras, rec.bases_fact):`)
        # is a dynamic expansion of unknown length, so no pattern position
        # can be confidently attributed to a known subject element -- the
        # identical rule `_paired_unpacking_candidates()` already applies
        # to a starred value display (Codex review, fresh evidence: this
        # guard existed on the assignment-unpacking sibling but not here).
        return []
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
        candidates.extend(_paired_sub_pattern_candidates(sub_pattern, sub_subject))
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
    way sequence pairing already does -- delegated to `_paired_sub_
    pattern_candidates()`, the same shared per-position helper
    `_paired_match_sequence_candidates()` uses, which also unwinds a
    chained `MatchAs` (`case {"fact": fact as alias}:`) into every name
    along the chain and recurses into a structural sub-pattern wrapped by
    `MatchAs`.
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
        candidates.extend(_paired_sub_pattern_candidates(sub_pattern, sub_subject))
    return candidates


def _matchas_chain_names(pattern: ast.pattern) -> list[str]:
    """Collect every name along a chain of nested `MatchAs` nodes, each
    binding the *identical* value -- whatever the chain's own innermost
    wrapped sub-pattern matches -- since Python's `as`-pattern binds its
    own name to the same subject its wrapped pattern was matched against
    (Codex review, fresh evidence): `case fact as alias:` parses as
    `MatchAs(pattern=MatchAs(name="fact"), name="alias")` -- a *nested*
    `MatchAs`, not a structural sub-pattern -- so both `alias` (the outer
    capture) and `fact` (the inner one) are equally real whole-subject
    aliases, but the caller previously only ever registered the outer
    `case.pattern.name`, leaving `fact` to `_match_pattern_names()`'s own
    ordinary-local-shadow treatment.

    Returns `[]` once the chain bottoms out -- at a bare wildcard capture
    (`pattern=None`), or at a non-`MatchAs` sub-pattern (a real structural
    pattern, e.g. `case SomeClass() as fact:`'s own wrapped `MatchClass`,
    whose *own* captures are correctly left to their existing, separate
    sub-part-capture handling, not treated as whole-subject aliases here)
    -- so a plain single-level `case fact:`/`case SomeClass() as fact:`
    still returns exactly the one name it always did, and this helper is
    a strict generalization, not a behavior change for either.
    """
    if not isinstance(pattern, ast.MatchAs):
        return []
    inner = _matchas_chain_names(pattern.pattern) if pattern.pattern else []
    return [pattern.name, *inner] if pattern.name is not None else inner


def _trusted_matchor_chain_names(pattern: ast.MatchOr) -> list[str]:
    """Return the names an OR pattern's *every* alternative is guaranteed
    to bind to the identical value regardless of which alternative
    matched, or `[]` if that can't be established (Codex review, fresh
    evidence): `case (C() as fact) | (D() as fact):` -- every alternative
    is itself a top-level `MatchAs` capturing the *whole* value it's
    matched against under the identical name. Python requires every
    alternative of an OR pattern to bind the same set of names (a
    `SyntaxError` otherwise), but not the same *shape* of binding --
    `case (C(x=fact)) | (D() as fact):` legally binds the name `fact` in
    both branches, but only the second branch binds it to the *whole*
    matched value, so a bare per-alternative name check is not safe to
    trust as an alias. Only when every single alternative is itself
    exactly `MatchAs` with the *identical full nested chain* of names (via
    `_matchas_chain_names()`, so a chained `case (fact as alias) | (other
    as alias):` is correctly rejected -- only `alias` is guaranteed
    identical, not `fact`/`other`) is every one of those names guaranteed
    safe.

    Shared by two callers matching this identical OR-pattern shape at two
    different levels: `fact_detector_misuse.py`'s whole-`case.pattern`
    capture collection (`case (C() as fact) | (D() as fact): fact ==
    other`), and `_paired_sub_pattern_candidates()`'s per-position
    structural pairing (`case ((C() as fact) | (D() as fact),):` -- the
    identical OR-pattern shape nested one level inside a sequence/mapping
    position, previously unrecognized there since this function's own
    predecessor logic was inlined only at the whole-subject level).
    """
    alternatives = pattern.patterns
    if not alternatives:
        return []
    first_alt = alternatives[0]
    first_chain = (
        _matchas_chain_names(first_alt)
        if isinstance(first_alt, ast.MatchAs) and first_alt.name is not None
        else []
    )
    if first_chain and all(
        isinstance(alt, ast.MatchAs)
        and alt.name is not None
        and _matchas_chain_names(alt) == first_chain
        for alt in alternatives
    ):
        return first_chain
    return []


def _paired_sub_pattern_candidates(
    sub_pattern: ast.pattern, sub_subject: ast.expr
) -> list[tuple[str, ast.expr]]:
    """Pair one structural-pattern position's own *sub_pattern* against
    its statically-known *sub_subject*, the shared per-position step
    `_paired_match_sequence_candidates()`/`_paired_match_mapping_
    candidates()` both delegate to (Codex review, fresh evidence): the
    previous inline handling recognized only a bare `MatchAs(name=...)`
    at a position, missing three real shapes. (1) A *chained* `MatchAs`
    (`case (fact as alias,):`, parsing as `MatchAs(pattern=MatchAs(
    name="fact"), name="alias")`) recorded only the outer `alias`,
    leaving `fact` -- an equally real whole-subject alias, per
    `_matchas_chain_names()`'s own docstring -- unrecorded. (2) A
    structural sub-pattern *wrapped* by `MatchAs` (`case ((fact, _) as
    alias,):`) fell through every branch untouched, since the position's
    own top-level node is `MatchAs`, not `MatchSequence`/`MatchMapping`
    directly, even though its wrapped pattern is exactly one of those.
    (3) A `MatchOr` at a position (`case ((C() as fact) | (D() as
    fact),):`) fell through untouched too -- the identical OR-pattern
    shape `_trusted_matchor_chain_names()` already recognizes at the
    whole-`case.pattern` level, just unreached at the per-position level
    (Codex review, fresh evidence).

    Unwinds the full nested-`MatchAs` chain via `_matchas_chain_names()`
    first (recording every name along it against the identical
    *sub_subject*, since Python's `as`-pattern always binds its own name
    to the same value its wrapped pattern matched against), then finds
    the chain's innermost non-`MatchAs` wrapped pattern -- skipping past
    every already-recorded `MatchAs` layer, so a name is never registered
    twice -- and recurses into it structurally when it is itself a
    `MatchSequence`/`MatchMapping`. A `MatchOr` position delegates to
    `_trusted_matchor_chain_names()`, the identical "every alternative is
    exactly `MatchAs` with the same full nested chain" trust rule the
    whole-subject level already applies -- deliberately not recursed into
    further (an alternative's own wrapped structural sub-pattern is left
    unpaired, matching the whole-subject branch's identical restriction).
    A non-`MatchAs`, non-structural, non-`MatchOr` *sub_pattern* (a
    wildcard `_`, a literal `MatchValue`, a bare `MatchClass`) contributes
    no candidate, matching the pre-existing behavior for those shapes.
    """
    if isinstance(sub_pattern, ast.MatchAs):
        candidates = [(name, sub_subject) for name in _matchas_chain_names(sub_pattern)]
        inner = sub_pattern.pattern
        while isinstance(inner, ast.MatchAs):
            inner = inner.pattern
        if inner is not None:
            candidates.extend(_paired_sub_pattern_candidates(inner, sub_subject))
        return candidates
    if isinstance(sub_pattern, ast.MatchSequence):
        return _paired_match_sequence_candidates(sub_pattern, sub_subject)
    if isinstance(sub_pattern, ast.MatchMapping):
        return _paired_match_mapping_candidates(sub_pattern, sub_subject)
    if isinstance(sub_pattern, ast.MatchOr):
        return [
            (name, sub_subject) for name in _trusted_matchor_chain_names(sub_pattern)
        ]
    return []


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


def _locally_bound_constructor_shadow_names(
    tree: ast.Module, qualnames: _QualnameSpans
) -> dict[str, set[str]]:
    """Map each scope's own qualname to the names bound *within* it that
    shadow a `Fact[T]` constructor spelling (`Fact`, or an imported
    alias of it) -- a function parameter (Codex review, fresh evidence:
    `def f(Fact, other): return Fact(1) == other`), an ordinary
    assignment/annotated-assignment/walrus target, a `for`/comprehension
    loop target, or an import that binds the name to something *other*
    than the real constructor -- since `_is_fact_typed_expr()`'s
    constructor-call recognition is a pure, scope-blind lookup against a
    single whole-tree name set with no notion of shadowing at all.

    **A first version of this collector covered only parameters,
    deliberately, because reusing `_fact_aliases()`'s own broader
    internal `locally_bound` set (which also records every `ast.Import`/
    `ast.ImportFrom` binding) treated a genuine `from abicheck.model.fact
    import Fact` -- the ordinary, correct way to bring the real
    constructor into scope at all -- as though it shadowed `Fact`,
    confirmed by direct reproduction before that mistake was reverted.
    This version closes the gap that narrowing left open (Codex review,
    fresh evidence: `Fact = lambda x: x`, `for Fact in factories:`, and a
    comprehension target named `Fact` were all still read as the real
    constructor) by widening to every binding form *except* the one that
    genuinely introduces it.** For `ast.ImportFrom`, a name is recorded
    as a shadow unless the import's own `alias.name` is literally
    `"Fact"` -- the identical structural test `_imported_fact_aliases()`
    itself uses to decide a name belongs in `fact_names` in the first
    place, so the two functions cannot disagree about which import
    establishes the real constructor. This means `from x import Fact`/
    `from x import Fact as F` are correctly *not* shadows (whatever `x`
    is -- this module's own established "match by name alone, not by
    source module" stance), while `from x import SomethingElse as Fact`
    (renaming an unrelated import to the exact spelling `Fact`) *is* a
    real shadow, the same as any other rebinding: `_imported_fact_
    aliases()` never adds a name to `fact_names` for that shape (its own
    `alias.name == "Fact"` check requires the *original* imported name to
    be `"Fact"`, not the local alias), so without this exclusion a bare
    module-level `fact_names` lookup would still treat the shadowed name
    as the real constructor. `ast.Import` (`import x.y.z as Fact`) has no
    such carve-out at all -- `_imported_fact_aliases()` only ever
    recognizes `ImportFrom`, so a bare `Import` binding can never
    legitimately be the real constructor.

    **Deliberately narrower than `_fact_aliases()`'s own general-binding
    walk in one respect: `match`/`case` pattern captures, `with`/`except
    ... as` targets, and a walrus used inside a comprehension's own
    scope-hopping position are not collected here.** Each of those needs
    the identical hardening `_fact_aliases()`'s own multi-round history
    already applied to its *general* binding collection (this module's
    own several "Codex review, N rounds" notes throughout that function)
    before it could be trusted for this purpose too -- reusing that
    already-hardened logic outright risks re-coupling this narrower,
    independent collector to `_fact_aliases()`'s own alias-tracking
    semantics the way the reverted first attempt already showed is
    dangerous. Left as an accepted, narrower residual matching the
    reported finding's own three shapes (assignment, `for`-loop target,
    comprehension target) plus parameters, rather than a same-round
    reimplementation of that entire hardened machinery for a second,
    independent purpose.
    """
    shadows: dict[str, set[str]] = {}
    def_containing = _def_containing_qualnames(tree)

    def _add(qualname: str, name: str) -> None:
        shadows.setdefault(qualname, set()).add(name)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A nested `def Fact(...):`/`class Fact:` binds `Fact` as a
            # local in whatever scope directly *contains* it, not within
            # its own new scope -- the identical `STORE_NAME`/`STORE_FAST`
            # rule `_def_containing_qualnames()`'s own docstring states
            # (Codex review, fresh evidence: `def outer(other): def
            # Fact(x): return x; return Fact(1) == other` was still read
            # as the real constructor, since nothing recorded the nested
            # def's own name as a shadow, only its parameters).
            containing = def_containing.get((node.lineno, node.col_offset))
            if containing is not None:
                _add(containing, node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            all_args = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *((node.args.vararg,) if node.args.vararg else ()),
                *((node.args.kwarg,) if node.args.kwarg else ()),
            )
            for arg in all_args:
                _add(qualname, arg.arg)
        elif isinstance(node, ast.Assign):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for target in node.targets:
                for name in _bound_names(target):
                    _add(qualname, name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            _add(qualname, node.target.id)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            _add(qualname, node.target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for name in _bound_names(node.target):
                _add(qualname, name)
        elif isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for generator in node.generators:
                for name in _bound_names(generator.target):
                    _add(qualname, name)
        elif isinstance(node, ast.ImportFrom):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for alias in node.names:
                if alias.name == "Fact":
                    continue
                _add(qualname, alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            for alias in node.names:
                _add(qualname, alias.asname or alias.name.split(".", 1)[0])
    return shadows


def _global_declared_names(
    tree: ast.Module, qualnames: _QualnameSpans
) -> dict[str, set[str]]:
    """Map each function's own qualname to the names it declares via a
    direct `global` statement in its own body (not a further-nested
    `def`'s -- that one gets its own, independent qualname entry).

    Exists to let `_scope_chain_union()` route a `global`-declared name
    straight to module scope instead of through the ordinary closure
    chain (Codex review, fresh evidence): `def outer(Fact): def
    inner(other): global Fact; return Fact.present(1) == other` --
    `inner`'s own `global Fact` statement makes *every* reference to
    `Fact` inside `inner` resolve to the module-level name, completely
    bypassing `outer`'s own parameter, which would otherwise shadow it
    for any ordinary (non-`global`) nested closure. A plain unconditional
    lexical-parent walk can't tell the two cases apart, since Python's
    `global` statement is a per-function-scope override of the normal
    closure rule, not a binding form the rest of this module's
    collectors have any notion of.
    """
    declared: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            declared.setdefault(qualname, set()).update(node.names)
    return declared


def _scope_chain_union(
    qualname: str,
    per_scope: dict[str, set[str]],
    lexical_parents: dict[str, str],
    global_names: dict[str, set[str]] | None = None,
) -> set[str]:
    """Union *per_scope*'s own entry for *qualname* with every one of its
    lexical-function ancestors' entries, walking `lexical_parents` (the
    real closure-scope chain, skipping class layers) until it bottoms
    out at `"<module>"` -- the shared "does this name resolve to
    something bound anywhere in my own closure chain" walk this module's
    several independent per-scope dicts (a shadowed constructor name, a
    constructor alias, a constructor-method alias) all need identically
    (Codex review, fresh evidence: `def outer(Fact): def inner(other):
    return Fact(1) == other` -- `inner`'s own entry is empty, since
    `Fact` is `outer`'s binding, not `inner`'s own, but `inner` still
    genuinely sees it through the closure). One shared walk instead of
    the identical loop hand-rolled once per dict.

    **`global_names` (optional) carries the exception to the walk**: any
    name *qualname* itself declares `global` (`_global_declared_names()`)
    is excluded from every non-module ancestor's own contribution --
    `global` routes resolution straight to module scope, so an enclosing
    function's own shadow/alias binding of that name must not leak in
    just because it sits between *qualname* and `"<module>"` in the
    chain. The name still receives whatever `"<module>"`'s own entry
    says, since the walk reaches it as the chain's terminal scope
    regardless. Omitted (the default) reproduces the original,
    unconditional-union behavior exactly, for a caller with no `global`
    awareness of its own.
    """
    result: set[str] = set()
    seen: set[str] = set()
    routed_to_module = global_names.get(qualname, set()) if global_names else set()
    current: str | None = qualname
    while current is not None and current not in seen:
        seen.add(current)
        contribution = per_scope.get(current, set())
        if routed_to_module and current != "<module>":
            contribution = contribution - routed_to_module
        result |= contribution
        current = lexical_parents.get(current)
    return result


def _resolve_effective_fact_names(
    qualname: str,
    fact_names: frozenset[str],
    locally_bound_shadows: dict[str, set[str]],
    constructor_aliases: dict[str, set[str]],
    lexical_parents: dict[str, str],
    global_names: dict[str, set[str]] | None = None,
) -> frozenset[str]:
    """The combined "what does the bare identifier `Fact` (or a
    constructor alias of it) mean at *qualname*" resolution both the
    constructor-call path and the annotation-recognition path need --
    replaces treating the shadow subtraction and the alias addition as
    two *independent* `_scope_chain_union()` walks, which was itself a
    real bug (Codex review, fresh evidence): `F = Fact; def f(F, other):
    return F(1) == other` -- `f`'s own parameter `F` is recorded as a
    shadow at `f`'s own scope, but the old alias-union walk unioned in
    *every* ancestor's own `constructor_aliases` unconditionally,
    re-adding `outer`'s `F` alias regardless of `f`'s own nearer shadow,
    so a call through the unrelated local parameter was still flagged.

    A single combined walk, nearest-scope-wins *per name*: starting at
    *qualname* and climbing `lexical_parents`, the *first* scope that
    mentions a given name -- as either a shadow or a constructor alias
    -- decides what that name means for every scope in between (a
    farther ancestor's mention of the same name is never consulted).
    Within one scope, a constructor-alias mention is checked before a
    shadow mention, so `F = Fact` (which the shadow collector also
    records as an ordinary local binding, since it collects *every*
    assignment target unconditionally) still resolves to "alias" at its
    *own* defining scope, not "shadow" -- the more specific answer wins
    the same-scope tie. `global_names` is the identical bypass exception
    `_scope_chain_union()` already documents: a name declared `global`
    routes straight to module scope, skipping every intervening
    ancestor's shadow *and* alias mentions alike.
    """
    routed_to_module = global_names.get(qualname, set()) if global_names else set()
    decided: dict[str, bool] = {}
    seen: set[str] = set()
    current: str | None = qualname
    while current is not None and current not in seen:
        seen.add(current)
        is_module = current == "<module>"
        for name in constructor_aliases.get(current, set()):
            if not is_module and name in routed_to_module:
                continue
            decided.setdefault(name, True)
        for name in locally_bound_shadows.get(current, set()):
            if not is_module and name in routed_to_module:
                continue
            decided.setdefault(name, False)
        current = lexical_parents.get(current)
    result = set(fact_names)
    for name, is_alias in decided.items():
        if is_alias:
            result.add(name)
        else:
            result.discard(name)
    return frozenset(result)


def _single_target_binding(
    node: ast.Assign | ast.AnnAssign,
) -> tuple[ast.expr, ast.expr] | None:
    """Return `(target, value)` for a single-target `ast.Assign` or a
    *valued* `ast.AnnAssign` with a bare-`Name`/`Attribute`/`Subscript`
    target -- the two node shapes a detector-style "name = <constructor
    expression>" alias binding is actually written as (Codex review,
    fresh evidence): `make_fact: Callable[..., Fact[int]] = Fact.present`
    is an `ast.AnnAssign`, invisible to an `ast.Assign`-only walk.
    `None` for every other shape (a tuple-unpacking `Assign`, a bare
    `AnnAssign` with no value, or anything else) -- both callers below
    already require the *target* itself to be a bare `ast.Name`, so this
    helper doesn't narrow that further.
    """
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        return node.targets[0], node.value
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return node.target, node.value
    return None


def _unwrap_generic_receiver(value: ast.expr) -> ast.expr:
    """Unwrap a generic-specialized receiver's own `ast.Subscript` --
    `Fact[int]`/`Fact[int].present` -- down to the bare expression a
    specialization wraps, the identical single-unwrap rule `_is_fact_
    typed_expr()`'s own constructor-call recognition already applies
    (Codex review, fresh evidence): `F = Fact[int]`/`make_fact =
    Fact[int].present` went unrecognized as aliases, even though the
    *direct*-call form (`Fact[int](...)`/`Fact[int].present(...)`) was
    already resolved, since subscripting a class produces a
    `_GenericAlias` whose own `__call__`/attribute access delegates
    straight through to the real class -- a no-op for anything but a
    literal subscript receiver.
    """
    if isinstance(value, ast.Subscript):
        return value.value
    return value


def _constructor_alias_names(
    tree: ast.Module,
    qualnames: _QualnameSpans,
    fact_names: frozenset[str],
    locally_bound_shadows: dict[str, set[str]],
    lexical_parents: dict[str, str],
    global_names: dict[str, set[str]] | None = None,
) -> dict[str, set[str]]:
    """Map each scope's own qualname to local names bound, via a plain
    single-target assignment, directly to the `Fact` constructor itself
    (or an already-recognized alias of it) -- `F = Fact` -- as opposed to
    a `Fact[T]` *value* (`_fact_aliases()`'s own, differently-shaped
    concern) (Codex review, fresh evidence): `F(1) == other`/`F.
    present(1) == other` are exactly as real a misuse as `Fact(1) ==
    other`/`Fact.present(1) == other`, since `F` is the identical
    constructor under a local name, but `_is_fact_typed_expr()`'s own
    constructor-call recognition is a scope-blind lookup against the
    single, whole-tree `fact_names` set, with no notion of a *local*
    rebinding extending it. Deliberately narrow, no type inference: only
    `target = <bare Name in fact_names>`, a single target, is recognized
    -- a further, transitive rename (`G = F`) is a real, if narrower,
    sibling gap left uncollected, the identical "one hop only" limit
    already accepted elsewhere in this module's own alias tracking.

    **`locally_bound_shadows`/`lexical_parents` guard a real false
    positive found during this fix's own proactive sibling verification,
    not a reported finding**: `def f(Fact, other): F = Fact; return F(1)
    == other` has a parameter named `Fact` shadowing the real constructor
    for the whole function, so `F = Fact` binds `F` to that *parameter's*
    runtime value, not to the real `Fact` constructor -- registering `F`
    as a constructor alias here would fabricate a misuse site out of an
    unrelated local rebinding. Skipped whenever the RHS name is itself
    shadowed anywhere in its own closure-scope chain
    (`_scope_chain_union`), the identical check `is_fact_typed()` already
    applies to a bare constructor reference. `global_names` is that same
    shadow check's own `global`-bypass exception (`_global_declared_
    names()`/`_scope_chain_union()`'s own docstrings): `def outer(Fact):
    def inner(other): global Fact; F = Fact; return F(1) == other` must
    still register `F` as a real alias, since `inner`'s own `global Fact`
    routes its `Fact` reference to module scope, not `outer`'s parameter.

    **Also recognizes an `ast.AnnAssign` binding and a generic-
    specialized (`Fact[int]`) receiver (Codex review, fresh evidence,
    both findings against the same commit)**: `F: type[Fact[int]] =
    Fact[int]` combines both extensions at once -- see
    `_single_target_binding()`/`_unwrap_generic_receiver()`'s own
    docstrings for exactly what each covers.
    """
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        pair = _single_target_binding(node)
        if pair is None:
            continue
        target, value = pair
        value = _unwrap_generic_receiver(value)
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Name)
            and value.id in fact_names
        ):
            qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
            if value.id in _scope_chain_union(
                qualname, locally_bound_shadows, lexical_parents, global_names
            ):
                continue
            aliases.setdefault(qualname, set()).add(target.id)
    return aliases


#: The `Fact` classmethods that actually construct and return a new
#: `Fact[T]` -- `abicheck/model/fact.py`'s own six `@classmethod`s, each
#: literally returning `cls(...)`. Deliberately excludes `Fact`'s other
#: two public members: `value_or` (an *instance* method that unwraps and
#: returns the bare `T`, never a `Fact`) and `is_present` (a `@property`
#: returning `bool`) -- treating either as a constructor was a real false
#: positive (Codex review, fresh evidence: `Fact.value_or(fact, 0) ==
#: expected` is an ordinary, correct unwrap-then-compare, and has nothing
#: to do with the misuse this whole check exists to catch, since neither
#: operand is `Fact`-typed after the call). No type inference, the
#: identical "match by name alone" stance `FACT_FIELD_NAMES` already
#: takes -- a name outside this set is never treated as a constructor,
#: including a future classmethod this list hasn't been updated for yet.
_FACT_CONSTRUCTOR_METHOD_NAMES = frozenset(
    {
        "present",
        "partial",
        "not_collected",
        "unsupported",
        "failed",
        "not_applicable",
    }
)


def _constructor_method_alias_names(
    tree: ast.Module,
    qualnames: _QualnameSpans,
    fact_names: frozenset[str],
    locally_bound_shadows: dict[str, set[str]],
    lexical_parents: dict[str, str],
    global_names: dict[str, set[str]] | None = None,
) -> dict[str, set[str]]:
    """Map each scope's own qualname to local names bound, via a plain
    single-target assignment, to an *unbound reference* of one of the
    `Fact` constructor's own classmethods -- `make_fact = Fact.present`
    (Codex review, fresh evidence): a later *direct* call `make_fact(1)`
    is exactly `Fact.present(1)`, but nothing recognized `make_fact` as
    anything at all. Unlike `_constructor_alias_names()` (where the bound
    name behaves exactly like `Fact` itself, in both call and further-
    attribute-access position), a classmethod reference is only ever
    meaningfully *called directly* -- `make_fact.present(...)` would be
    nonsensical -- so this is tracked as its own, separate set rather
    than folded into the constructor-alias one, and the caller must
    check it only against a direct `ast.Call` whose own `func` is a bare
    `ast.Name`, never composed into the general `fact_names` substitution
    the constructor-alias set participates in.

    **`locally_bound_shadows`/`lexical_parents` guard the identical
    shadowing hazard `_constructor_alias_names()`'s own docstring
    describes** -- `def f(Fact, other): make_fact = Fact.present; ...`
    with `Fact` itself a shadowing parameter would otherwise fabricate a
    classmethod alias off the parameter's own runtime value.
    `global_names` is the identical `global`-bypass exception described
    there too.

    **Also recognizes an `ast.AnnAssign` binding and a generic-
    specialized (`Fact[int]`) receiver (Codex review, fresh evidence,
    both findings against the same commit)**: `make_fact: Callable[...,
    Fact[int]] = Fact[int].present` combines both extensions at once --
    see `_single_target_binding()`/`_unwrap_generic_receiver()`'s own
    docstrings for exactly what each covers.
    """
    aliases: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        pair = _single_target_binding(node)
        if pair is None:
            continue
        target, value = pair
        if not (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Attribute)
            and value.attr in _FACT_CONSTRUCTOR_METHOD_NAMES
        ):
            continue
        receiver = _unwrap_generic_receiver(value.value)
        if not (isinstance(receiver, ast.Name) and receiver.id in fact_names):
            continue
        qualname = _qualname_at((node.lineno, node.col_offset), qualnames)
        if receiver.id in _scope_chain_union(
            qualname, locally_bound_shadows, lexical_parents, global_names
        ):
            continue
        aliases.setdefault(qualname, set()).add(target.id)
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

    **A comprehension's own *outermost* generator's iterable is the one
    exception to that stop rule (Codex review, fresh evidence): `fact =
    rec.bases_fact; def g(fact, cb=[x for x in (fact == other,)]): ...`
    was silently missed.** A comprehension's outermost iterable evaluates
    in whatever scope encloses the comprehension itself -- here, the same
    def-time scope this whole function exists to attribute defaults to --
    before the comprehension's own implicit function is even called
    (`g`'s own parameter `fact` does not yet exist to shadow anything at
    that point). Treating the comprehension as an opaque scope boundary
    the moment it is reached (the general rule above) stopped before ever
    descending into that iterable, so the comparison nested inside it kept
    reading as `g`'s own body scope instead of the enclosing one -- the
    identical "outermost iterable is not really part of the new scope"
    exception `_enclosing_qualnames`'s and `_lexical_function_parents`'s
    own comprehension handling already carve out, applied here too.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(
            current, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            if current.generators:
                stack.append(current.generators[0].iter)
            continue
        if isinstance(current, _SCOPE_INTRODUCING_NODE_TYPES):
            continue
        stack.extend(ast.iter_child_nodes(current))

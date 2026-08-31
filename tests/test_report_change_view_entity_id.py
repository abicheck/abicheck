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

"""``_ReportChangeView`` must keep pace with every attribute
``finding_identity.resolve_change_identity`` reads on a ``Change``.

Split out of ``tests/test_aggregate_findings.py`` (a debt-tracked, no-growth
test module -- see ``architecture/debt.yaml``) rather than added there, so
this regression didn't need to fight that file's frozen line budget.

ADR-063 Phase 2 gave ``Change`` an ``entity_id`` field and later taught
``resolve_change_identity`` to read it unconditionally
(``change.entity_id``, gated only on the pre-existing ``is_batch`` check).
``abicheck.workflows.aggregate.reconcile._ReportChangeView`` -- the
read-back adapter ``resolve_report_change_identity`` builds from a report's
JSON and passes to that same function -- was never given a matching field,
so any report round trip of a ``Change`` that carried a live ``entity_id``
raised ``AttributeError``. This is a real regression class, not one input:
whenever ``resolve_change_identity`` starts reading a new ``Change``
attribute, ``_ReportChangeView`` must be updated in lockstep or every
report-based aggregation call breaks the same way, unconditionally, for any
finding at all -- which is exactly what happened here (105 tests failed on
``main`` from this one gap).

``test_report_change_view_covers_every_attribute_resolve_change_identity_reads``
below is the generalized half (Codex review, fresh evidence): the concrete
``entity_id`` round trip above only pins the one attribute that was
actually missing, so it would stay green if some *other* attribute went
missing next. The structural test instead statically discovers every
``change.<attr>`` read reachable from ``resolve_change_identity`` (walking
into any same-module helper it calls with the same object) and asserts
``_ReportChangeView``'s own field set is a superset -- registered as this
PR's ``adapter.duck_typed_view_attribute_drift`` bug class in
``tests/regressions/manifest.py``.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from collections.abc import Iterator

from abicheck import finding_identity
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model.identity import EntityId, EntityKind
from abicheck.reporter import _change_to_dict
from abicheck.workflows.aggregate.reconcile import (
    _ReportChangeView,
    resolve_report_change_identity,
)

_KIND = ChangeKind.FUNC_REMOVED
_SYMBOL = "_ZN3lib3addEii"
_DESCRIPTION = "Function removed"


def test_a_live_changes_entity_id_survives_the_report_round_trip() -> None:
    """A real `Change` carrying an `entity_id` must round-trip through a
    report without raising, and `entity_id` (never serialized by
    `_change_to_dict`) must not change the round-tripped identity relative
    to an otherwise-identical finding that never had one."""
    eid = EntityId(scope=(), kind=EntityKind.FUNCTION, leaf_name="add")
    with_change = Change(
        kind=_KIND, symbol=_SYMBOL, description=_DESCRIPTION, entity_id=eid
    )
    without_change = Change(kind=_KIND, symbol=_SYMBOL, description=_DESCRIPTION)
    with_id = resolve_report_change_identity(_change_to_dict(with_change))
    without_id = resolve_report_change_identity(_change_to_dict(without_change))
    assert with_id.primary_id == without_id.primary_id
    assert with_id.tier == without_id.tier


def _group_funcs_by_name(tree: ast.AST) -> dict[str, list[ast.FunctionDef]]:
    """Every ``ast.FunctionDef`` in *tree*, grouped by name -- a *list*
    per name rather than the single-function ``dict[str, FunctionDef]``
    this module used through the fourteenth round (Codex review, fresh
    evidence, fifteenth round): two functions sharing a name in unrelated
    scopes (a module-level helper and a same-named nested function
    defined somewhere else entirely) silently collapsed onto whichever
    ``ast.walk`` visited last, so a real read living in the DISCARDED
    function could vanish from this guard entirely. Following every
    same-named candidate and unioning their results trades a small
    chance of also following an unrelated function of the same name (a
    spurious extra field demand) for never silently dropping the real
    one -- this module's established favor-false-positives-over-false-
    negatives choice, extended to name resolution itself.
    """
    grouped: dict[str, list[ast.FunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            grouped.setdefault(node.name, []).append(node)
    return grouped


def _own_param_names(args: ast.arguments) -> frozenset[str]:
    """Every name *args* binds as its own parameter -- positional-only,
    regular, keyword-only, ``*args``, and ``**kwargs`` alike."""
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return frozenset(names)


def _bind_target(target: ast.expr, names: set[str]) -> None:
    """Record every ``Name`` *target* binds, unpacking tuple/list/starred
    assignment targets recursively (``a, (b, *c) = ...``)."""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _bind_target(elt, names)
    elif isinstance(target, ast.Starred):
        _bind_target(target.value, names)


def _unwrap_transparent(expr: ast.expr) -> ast.expr:
    """Unwrap a ``typing.cast(<type>, <value>)`` call, or a walrus
    (``:=``) assignment expression, to the value each forwards unchanged
    at runtime -- looping so the two compose in either order (``cast("Change",
    (x := change))`` or ``(x := cast("Change", change))``).

    The ``cast`` case (Codex review, fresh evidence, eleventh round):
    ``helper(cast("Change", change))`` forwards ``change`` as-is, but the
    AST shows an ``ast.Call`` to ``cast``, not a bare ``ast.Name``, so the
    existing call-argument match missed it entirely. Recognizes both the
    bare ``cast(...)`` and qualified ``typing.cast(...)`` call forms.

    The walrus case (Codex review, fresh evidence, thirteenth round):
    ``(candidate := change).future_field`` accesses the attribute on the
    walrus expression's *value*, an ``ast.NamedExpr`` node, not a bare
    ``ast.Name`` -- every existing name-match check missed this
    immediate-use shape the same way it missed ``cast(...)``.

    Any other expression is returned unchanged.
    """
    while True:
        if isinstance(expr, ast.NamedExpr):
            expr = expr.value
            continue
        if (
            isinstance(expr, ast.Call)
            and len(expr.args) == 2
            and (
                (isinstance(expr.func, ast.Name) and expr.func.id == "cast")
                or (isinstance(expr.func, ast.Attribute) and expr.func.attr == "cast")
            )
        ):
            expr = expr.args[1]
            continue
        return expr


def _locally_bound_names(scope_node: ast.AST) -> frozenset[str]:
    """Every name Python's own scoping rules make LOCAL to *scope_node*
    (a nested function/async function/lambda) by ordinary body-level
    binding -- assignment (including tuple/list/starred unpacking),
    augmented/annotated assignment, a ``for``/``async for`` target, a
    ``with``/``async with ... as`` target, an ``except ... as`` name, an
    ``import``/``from ... import ... as`` alias, a walrus (``:=``)
    target, a ``global`` declaration (which severs the name from *every*
    enclosing function scope, this one included), or a nested ``def
    NAME(): ...``/``class NAME: ...`` statement (which binds ``NAME`` in
    *this* scope exactly like any other statement, even though the
    definition's own body is a further, separate scope this walk must
    not descend into -- Codex review, fresh evidence, tenth round: ``def
    nested(): def change(): ...; return change.future_field`` was missed
    entirely, since binding a name and opening a new scope are two
    different facts and only the second was modeled before this round).
    Never descends into a further-nested function/async function/lambda/
    class's own body (each computes its own local set independently).

    A ``nonlocal`` declaration is deliberately excluded from the returned
    set (Codex review, fresh evidence, tenth round, second finding on the
    same commit): it explicitly means the name resolves to the nearest
    enclosing function scope instead of binding locally in *this* one --
    exactly the genuine-capture case this whole module exists to still
    catch -- even when that name is *also* the target of an ordinary
    ``Assign`` later in the same body (``nonlocal change; change =
    get_new_change()`` still rebinds the ENCLOSING scope's ``change``,
    the whole point of declaring it ``nonlocal``, not a new local one
    here; the first attempt at this exclusion only skipped collecting the
    name from the ``Nonlocal`` statement itself, but never stopped the
    sibling ``Assign`` from adding it back in). ``Global``/``Nonlocal``
    declared names are tracked in two disjoint sets and only subtracted
    from the local set at the very end, so declaration order relative to
    the assignment it covers never matters (Codex review, fresh evidence,
    ninth round: the seventh/eighth rounds' shadow set covered only
    *parameters* -- ``def nested(): change = object(); return
    change.future_field`` rebinds ``change`` as a local via ordinary
    assignment, no parameter involved at all, and was still misattributed
    as an outer read).

    A plain ``Assign``/``AnnAssign`` whose value (after unwrapping a
    transparent ``cast(...)``) is a bare ``Name`` is deliberately NOT
    added here at all (Codex review, fresh evidence, twelfth round): the
    ninth round's blanket rule made ``def nested(): candidate = change;
    return candidate.future_field`` shadow ``candidate`` in ``nested``'s
    own scope, discarding the exact alias `_change_attrs_read`'s
    fixed-point pass had *just* established -- the two mechanisms were
    fighting each other. Deferring an alias-*shaped* assignment to that
    fixed-point pass instead accepts a narrower, safer trade-off: a
    genuinely unrelated rebind through a bare name the fixed-point pass
    never tracks (``change = some_other_local``) is no longer shadowed
    either, so a subsequent read is over-reported as an outer one rather
    than silently dropped -- a spurious extra field demand is a nuisance,
    a missed real read is the actual bug class this module exists to
    catch, and this module has consistently favored the former over the
    latter. ``AugAssign`` is excluded from this exemption unconditionally
    (``change += 1`` combines with something else by construction, never
    a pure alias).

    The exemption applies to a name for its *whole* scope, not just the
    specific alias-shaped binding (Codex review, fresh evidence,
    fifteenth round): ``candidate = change; value = candidate.
    future_field; candidate = object()`` still leaves ``candidate``
    unshadowed even after that later, non-alias rebind, since this
    module has no real per-statement control-flow ordering to know a
    given read happened before or after it. Widening the exemption this
    way is the same favor-false-positives trade-off stated above, taken
    one step further rather than reverted -- a name that was ever a
    genuine alias in this scope never risks hiding a real outer read
    here again, at the cost of also over-reporting its later, truly
    unrelated uses.
    """
    names: set[str] = set()
    alias_shaped_names: set[str] = set()
    nonlocal_names: set[str] = set()
    stack: list[ast.AST] = [scope_node]
    is_root = True
    while stack:
        current = stack.pop()
        if not is_root and isinstance(
            current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            # A `def NAME(): ...`/`class NAME: ...` statement binds NAME in
            # THIS scope (like any other statement) even though its own
            # body is a separate scope this walk must not descend into
            # (Codex review, fresh evidence, tenth round: `def nested():
            # def change(): ...; return change.future_field` was missed
            # entirely -- the name binding and the scope boundary are two
            # different facts, and only the second was modeled before).
            names.add(current.name)
            continue
        if not is_root and isinstance(current, ast.Lambda):
            continue
        is_root = False
        if isinstance(current, ast.Assign):
            for assign_target in current.targets:
                _bind_target(assign_target, names)
            if isinstance(_unwrap_transparent(current.value), ast.Name):
                for assign_target in current.targets:
                    _bind_target(assign_target, alias_shaped_names)
        elif isinstance(current, ast.AugAssign):
            _bind_target(current.target, names)
        elif isinstance(current, ast.AnnAssign):
            _bind_target(current.target, names)
            if current.value is not None and isinstance(
                _unwrap_transparent(current.value), ast.Name
            ):
                _bind_target(current.target, alias_shaped_names)
        elif isinstance(current, (ast.For, ast.AsyncFor)):
            _bind_target(current.target, names)
        elif isinstance(current, (ast.With, ast.AsyncWith)):
            for item in current.items:
                if item.optional_vars is not None:
                    _bind_target(item.optional_vars, names)
        elif isinstance(current, ast.ExceptHandler) and current.name:
            names.add(current.name)
        elif isinstance(current, (ast.Import, ast.ImportFrom)):
            for alias in current.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(current, ast.NamedExpr) and isinstance(
            current.target, ast.Name
        ):
            names.add(current.target.id)
            if isinstance(_unwrap_transparent(current.value), ast.Name):
                alias_shaped_names.add(current.target.id)
        elif isinstance(current, ast.Global):
            names.update(current.names)
        elif isinstance(current, ast.Nonlocal):
            # A name declared `nonlocal` still gets an `Assign`/`AugAssign`
            # node recorded above when it's reassigned (that's the whole
            # point of declaring it), but such an assignment rebinds the
            # ENCLOSING scope's variable, not a new local one here (Codex
            # review, fresh evidence, tenth round) -- excluded below.
            nonlocal_names.update(current.names)
        for child in ast.iter_child_nodes(current):
            stack.append(child)
    return frozenset(names - nonlocal_names - alias_shaped_names)


def _iter_scope_aware(node: ast.AST) -> Iterator[tuple[ast.AST, frozenset[str]]]:
    """Like ``ast.walk``, but each yielded node also carries the set of
    tracked names *shadowed* at that point -- names a nested function,
    async function, or lambda strictly inside *node* makes local to
    itself, whether via its own parameter list (Codex review, fresh
    evidence, seventh round: the sixth round's fix skipped an entire
    nested scope outright, which also threw away a genuine *closure*
    read of the outer name, e.g. ``def nested(): return change.
    future_field`` -- ``nested`` never rebinds ``change``, so that read
    really is of the outer parameter and must still count) or via an
    ordinary body-level binding (Codex review, fresh evidence, ninth
    round: ``def nested(): change = object(); return change.
    future_field`` rebinds ``change`` with no parameter involved at all
    -- see :func:`_locally_bound_names` for the full list of binding
    forms recognized). Only a name a nested scope actually makes local
    to itself is shadowed within that scope; every other name stays
    visible, the same way Python's own closure lookup works.

    A nested scope's ``args`` (parameter defaults/annotations) and, for a
    real function, its ``decorator_list``/``returns`` are walked with the
    *enclosing* scope's shadow set, not the nested one (Codex review,
    fresh evidence, eighth round): Python evaluates defaults, decorators,
    and the return annotation in the enclosing scope at definition time,
    before the nested scope even exists, so ``def nested(change=change.
    future_field): ...`` reads the *outer* ``change`` in its default value
    even though ``nested`` itself redeclares that name as its own
    parameter. Only ``body`` runs inside the new, shadowed scope.

    A nested class body is walked with the *same* shadow set as its
    surroundings (classes have no parameter list to rebind a name via) --
    a name reassigned inside it is a rarer, unmodeled edge matching this
    helper's existing "small, targeted, not general-purpose" scope.

    A list/set/dict comprehension or generator expression also opens its
    own scope for its ``for`` targets in real Python 3 (Codex review,
    fresh evidence, eleventh round): ``[change.future_field for change in
    candidates]`` reads only the comprehension-local ``change``, never
    the outer parameter, so its targets are shadowed the same way a
    nested function's parameters are -- except the *first* ``for``
    clause's own iterable expression, which Python evaluates in the
    *enclosing* scope (mirroring the eighth round's default-value
    treatment), so a genuine outer read there still counts.
    """
    stack: list[tuple[ast.AST, frozenset[str]]] = [(node, frozenset())]
    while stack:
        current, shadowed = stack.pop()
        yield current, shadowed
        if current is not node and isinstance(
            current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            own_shadowed = (
                shadowed
                | _own_param_names(current.args)
                | _locally_bound_names(current)
            )
            stack.append((current.args, shadowed))
            if isinstance(current, ast.Lambda):
                stack.append((current.body, own_shadowed))
            else:
                for decorator in current.decorator_list:
                    stack.append((decorator, shadowed))
                if current.returns is not None:
                    stack.append((current.returns, shadowed))
                for stmt in current.body:
                    stack.append((stmt, own_shadowed))
            continue
        if isinstance(
            current, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
        ):
            comp_names: set[str] = set()
            for generator in current.generators:
                _bind_target(generator.target, comp_names)
            comp_shadowed = shadowed | comp_names
            for i, generator in enumerate(current.generators):
                stack.append((generator.iter, shadowed if i == 0 else comp_shadowed))
                for if_expr in generator.ifs:
                    stack.append((if_expr, comp_shadowed))
            if isinstance(current, ast.DictComp):
                stack.append((current.key, comp_shadowed))
                stack.append((current.value, comp_shadowed))
            else:
                stack.append((current.elt, comp_shadowed))
            continue
        for child in ast.iter_child_nodes(current):
            stack.append((child, shadowed))


def _change_attrs_read(
    func_name: str,
    param_name: str,
    funcs_by_name: dict[str, list[ast.FunctionDef]],
    visited: set[tuple[int, str]],
) -> set[str]:
    """Every ``<param_name>.<attr>`` read reachable from *any* function
    named ``func_name`` -- unioned across every same-named candidate
    ``funcs_by_name`` groups under that key (Codex review, fresh
    evidence, fifteenth round: see :func:`_group_funcs_by_name`'s own
    docstring for why a name can legitimately resolve to more than one
    function here, and why following all of them is the safe choice).
    ``visited`` is keyed on ``(id(function), param_name)`` rather than
    ``(name, param_name)`` so two distinct same-named functions are each
    still explored independently.
    """
    attrs: set[str] = set()
    for func in funcs_by_name.get(func_name, ()):
        key = (id(func), param_name)
        if key in visited:
            continue
        visited.add(key)
        attrs |= _change_attrs_read_in_function(
            func, param_name, funcs_by_name, visited
        )
    return attrs


def _change_attrs_read_in_function(
    func: ast.FunctionDef,
    param_name: str,
    funcs_by_name: dict[str, list[ast.FunctionDef]],
    visited: set[tuple[int, str]],
) -> set[str]:
    """Every ``<param_name>.<attr>`` read reachable from *this specific*
    ``func``, following calls (by position or keyword) into other
    functions defined in the same module that receive the same object
    under a new name --
    positional binding counts a callee's positional-only parameters ahead
    of its regular ones (Codex review, fresh evidence, fifth round: a
    callee declaring the forwarded object positional-only, e.g. ``def
    helper(candidate, /)``, was invisible to a walk that only read
    ``args.args``, the same way it excludes that parameter at runtime) --
    and following simple local-variable aliases of ``param_name`` within the
    same function body -- both a plain ``candidate = change`` (Codex
    review, fresh evidence, third round: a parameter-only walk stays green
    for a read written after a refactor like ``candidate = change;
    candidate.future_field``, or a helper call forwarding the alias
    instead of the original name) and an annotated ``candidate: Change =
    change`` (Codex review, fresh evidence, fourth round: an ``AnnAssign``
    is a distinct AST node from ``Assign`` and was not recognized at all).
    Also catches an *indirect* read via the ``getattr(<param_name>, "attr", ...)`` builtin
    (Codex review, fresh evidence, second round: a literal-only walk stays
    green if a future reader switches to this form) -- a non-literal
    attribute name (computed at runtime) is out of reach for a static
    check like this one and is not attempted.

    Deliberately module-scoped and name-based (no real type inference) --
    a small, targeted static check for this one adapter/consumer pair, not
    a general-purpose analyzer. Visited-tracking against infinite
    recursion is the caller's (:func:`_change_attrs_read`'s) job, keyed
    on this specific function object rather than its name. Alias
    tracking runs to a fixed point rather than a single pass (Codex
    review, fresh evidence, seventh round: ``_iter_scope_aware``'s
    stack-based traversal does not yield nodes in source order, so a
    multi-hop chain like ``first = change; second = first`` could reach
    ``second = first`` before ``first`` was itself tracked -- a fixed
    point makes the result independent of traversal order entirely). Also
    tracks a ``match``/``case`` bare capture pattern (Codex review, fresh
    evidence, fourteenth round: ``match change: case candidate: return
    candidate.future_field`` binds ``candidate`` to the whole subject
    value -- a distinct binding form from ``Assign``/``AnnAssign``/
    ``NamedExpr``, unconditional since a bare-name capture pattern always
    matches) -- only the simple top-level ``case NAME:`` shape is
    recognized, not a capture nested inside a class/sequence/mapping
    pattern, matching this helper's existing "small, targeted" scope. A
    conditional reassignment or an alias shadowed by a later unrelated
    rebinding is still not modeled either.
    """
    tracked = {param_name}
    changed = True
    while changed:
        changed = False
        for node, shadowed in _iter_scope_aware(func):
            if isinstance(node, ast.Assign):
                assign_value = _unwrap_transparent(node.value)
                if (
                    isinstance(assign_value, ast.Name)
                    and assign_value.id in tracked
                    and assign_value.id not in shadowed
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id not in tracked:
                            tracked.add(target.id)
                            changed = True
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                ann_value = node.value and _unwrap_transparent(node.value)
                if (
                    isinstance(ann_value, ast.Name)
                    and ann_value.id in tracked
                    and ann_value.id not in shadowed
                    and node.target.id not in tracked
                ):
                    tracked.add(node.target.id)
                    changed = True
            elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                walrus_value = _unwrap_transparent(node.value)
                if (
                    isinstance(walrus_value, ast.Name)
                    and walrus_value.id in tracked
                    and walrus_value.id not in shadowed
                    and node.target.id not in tracked
                ):
                    tracked.add(node.target.id)
                    changed = True
            elif isinstance(node, ast.Match):
                match_subject = _unwrap_transparent(node.subject)
                if (
                    isinstance(match_subject, ast.Name)
                    and match_subject.id in tracked
                    and match_subject.id not in shadowed
                ):
                    for case in node.cases:
                        pattern = case.pattern
                        if (
                            isinstance(pattern, ast.MatchAs)
                            and pattern.pattern is None
                            and pattern.name is not None
                            and pattern.name not in tracked
                        ):
                            tracked.add(pattern.name)
                            changed = True
    attrs: set[str] = set()
    for node, shadowed in _iter_scope_aware(func):
        if isinstance(node, ast.Attribute):
            attr_value = _unwrap_transparent(node.value)
            if (
                isinstance(attr_value, ast.Name)
                and attr_value.id in tracked
                and attr_value.id not in shadowed
            ):
                attrs.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(
                getattr_target := _unwrap_transparent(node.args[0]), ast.Name
            )
            and getattr_target.id in tracked
            and getattr_target.id not in shadowed
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            attrs.add(node.args[1].value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in funcs_by_name
        ):
            # Every same-named candidate is followed independently (Codex
            # review, fresh evidence, fifteenth round) -- their parameter
            # lists can differ, so position `i` may map to a different
            # name per candidate; `_change_attrs_read` itself fans back
            # out over all candidates again for that mapped name, which
            # is redundant but not incorrect.
            for callee in funcs_by_name[node.func.id]:
                arg_names = [
                    a.arg for a in (*callee.args.posonlyargs, *callee.args.args)
                ]
                for i, raw_arg in enumerate(node.args):
                    arg = _unwrap_transparent(raw_arg)
                    if (
                        isinstance(arg, ast.Name)
                        and arg.id in tracked
                        and arg.id not in shadowed
                        and i < len(arg_names)
                    ):
                        attrs |= _change_attrs_read(
                            node.func.id, arg_names[i], funcs_by_name, visited
                        )
                for kw in node.keywords:
                    kw_value = _unwrap_transparent(kw.value)
                    if (
                        isinstance(kw_value, ast.Name)
                        and kw_value.id in tracked
                        and kw_value.id not in shadowed
                        and kw.arg
                    ):
                        attrs |= _change_attrs_read(
                            node.func.id, kw.arg, funcs_by_name, visited
                        )
    return attrs


def test_report_change_view_covers_every_attribute_resolve_change_identity_reads() -> (
    None
):
    """Structural enforcement of this module's own stated bug class: parse
    `finding_identity.py`'s real source (not a hand-copied attribute list
    that could itself go stale) and fail if `resolve_change_identity` --
    transitively, through its own same-module helpers -- reads any `Change`
    attribute `_ReportChangeView` does not declare."""
    tree = ast.parse(inspect.getsource(finding_identity))
    funcs_by_name = _group_funcs_by_name(tree)
    attrs_read = _change_attrs_read(
        "resolve_change_identity", "change", funcs_by_name, set()
    )
    assert attrs_read, (
        "AST walk found no change.<attr> reads -- the walker itself is broken"
    )
    view_fields = {f.name for f in dataclasses.fields(_ReportChangeView)}
    missing = attrs_read - view_fields
    assert not missing, (
        f"resolve_change_identity reads Change.{sorted(missing)} but "
        "_ReportChangeView has no matching field(s) -- add them (see "
        "adapter.duck_typed_view_attribute_drift in tests/regressions/manifest.py)"
    )


def test_change_attrs_read_catches_an_indirect_getattr_field_read() -> None:
    """Codex review, fresh evidence, second round: an unconditional read
    written as `getattr(change, "future_field")` -- literally, not through
    a runtime-computed name, which no static check can follow -- must be
    found too, including through a followed helper call, or the structural
    guard above would stay green while `_ReportChangeView` silently lacks
    the field a future `resolve_change_identity` revision starts reading
    this way instead of via `change.future_field`."""
    source = (
        "def outer(change):\n"
        "    return helper(change)\n"
        "def helper(c):\n"
        "    return getattr(c, 'future_field', None)\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_catches_a_direct_local_alias_read() -> None:
    """Codex review, fresh evidence, third round: a local alias of the
    tracked parameter (`candidate = change`) followed by a direct read
    off the alias (`candidate.future_field`) must be found too, or the
    structural guard above would stay green while `_ReportChangeView`
    silently lacks a field a future refactor reads only through a
    renamed local variable."""
    source = (
        "def outer(change):\n"
        "    candidate = change\n"
        "    return candidate.future_field\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_catches_a_local_alias_forwarded_to_a_helper() -> None:
    """Same alias-tracking gap as above, but the alias is forwarded to a
    helper call (`helper(candidate)`) instead of read directly -- the
    walker must resolve the alias to the tracked parameter *before*
    matching it against a call argument, not just before matching a bare
    attribute access."""
    source = (
        "def outer(change):\n"
        "    candidate = change\n"
        "    return helper(candidate)\n"
        "def helper(c):\n"
        "    return c.future_field2\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field2"
    }


def test_change_attrs_read_catches_an_annotated_local_alias_read() -> None:
    """Codex review, fresh evidence, fourth round: an *annotated* alias
    assignment (`candidate: Change = change`) is a distinct `ast.AnnAssign`
    node, not `ast.Assign` -- a walker that only recognizes the latter
    stays green for this equally common, equally real refactor shape."""
    source = (
        "def outer(change):\n"
        "    candidate: object = change\n"
        "    return candidate.future_field\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_follows_a_positional_only_helper_parameter() -> None:
    """Codex review, fresh evidence, fifth round: a followed helper that
    declares the forwarded object positional-only (`def helper(candidate,
    /)`) puts `candidate` in `args.posonlyargs`, not `args.args` -- a
    walker that only reads the latter can never resolve the binding for
    a call like `helper(change)`, the same way that parameter is excluded
    from `args.args` at runtime."""
    source = (
        "def outer(change):\n"
        "    return helper(change)\n"
        "def helper(candidate, /):\n"
        "    return candidate.future_field\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_ignores_a_nested_functions_shadowed_parameter() -> None:
    """Codex review, fresh evidence, sixth round: a nested function that
    redeclares a parameter with the same spelling as the tracked one
    (`def nested(change): return change.future_field`, defined inside the
    very function being walked) opens its own separate scope -- a plain
    `ast.walk` would misattribute that read to the *outer* `change`,
    demanding a field `_ReportChangeView` never actually needs to carry
    and failing a refactor that never touches the real parameter."""
    source = (
        "def outer(change):\n"
        "    def nested(change):\n"
        "        return change.future_field\n"
        "    return nested(None)\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == set()


def test_change_attrs_read_follows_a_multi_hop_alias_chain() -> None:
    """Codex review, fresh evidence, seventh round: `_iter_scope_aware`'s
    stack-based traversal does not yield nodes in source order, so a
    single forward pass over `first = change; second = first` could reach
    `second = first` before `first` was itself tracked, silently dropping
    the second hop. The fixed-point loop must resolve this regardless of
    traversal order."""
    source = (
        "def outer(change):\n"
        "    first = change\n"
        "    second = first\n"
        "    return second.future_field\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_counts_a_genuine_closure_capture() -> None:
    """Codex review, fresh evidence, seventh round: the sixth round's fix
    skipped an entire nested scope outright to handle *shadowing*, but a
    nested function that never rebinds the tracked name as its own
    parameter is a genuine Python closure over the outer one (`def
    nested(): return change.future_field` -- `nested` declares no
    `change` parameter at all). That read really does reach the outer
    `Change`, so it must still be found -- skipping it would let a field
    `resolve_change_identity` actually reads slip past this guard
    entirely."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        return change.future_field\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_evaluates_a_nested_default_in_the_enclosing_scope() -> None:
    """Codex review, fresh evidence, eighth round: Python evaluates a
    nested function's parameter defaults in the *enclosing* scope, at
    definition time, before that function's own scope exists -- so `def
    nested(change=change.future_field): ...` reads the *outer* `change`
    in its default value even though `nested` redeclares that same name
    as its own parameter. Applying the nested scope's shadow set to its
    own `args` (as the seventh round's fix did uniformly) would wrongly
    hide this read."""
    source = (
        "def outer(change):\n"
        "    def nested(change=change.future_field):\n"
        "        return change\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_ignores_a_nested_functions_own_local_assignment() -> None:
    """Codex review, fresh evidence, ninth round: the seventh/eighth
    rounds' shadow set only ever covered a nested scope's own
    *parameters* -- but Python also makes any name assigned inside a
    function body local to that function, no parameter involved at all.
    `def nested(): change = object(); return change.future_field` reads
    `nested`'s own freshly-assigned local, never the outer `change`
    parameter, and must not be misattributed as an outer read."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        change = object()\n"
        "        return change.future_field\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == set()


def test_change_attrs_read_treats_nonlocal_as_a_genuine_capture() -> None:
    """`nonlocal change` explicitly means `change` inside `nested`
    resolves to the *enclosing* function's binding rather than a new
    local one -- unlike an ordinary assignment, this must NOT be
    shadowed, or a real read through a `nonlocal`-declared name would be
    wrongly hidden the same way plain closure capture almost was in the
    seventh round."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        nonlocal change\n"
        "        return change.future_field\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_treats_a_nested_def_name_as_a_local_binding() -> None:
    """Codex review, fresh evidence, tenth round: `def NAME(): ...` binds
    `NAME` in the CONTAINING scope, exactly like an assignment would --
    `def nested(): def change(): pass; return change.future_field` reads
    `nested`'s own locally-defined `change` function, never the outer
    `change` parameter, and must not be misattributed as an outer read."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        def change():\n"
        "            pass\n"
        "        return change.future_field\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == set()


def test_change_attrs_read_keeps_a_nonlocal_capture_after_reassignment() -> None:
    """Codex review, fresh evidence, tenth round, second finding on the
    same commit: `nonlocal change` followed by an ordinary `Assign` to
    `change` (the normal reason to declare it `nonlocal` at all) still
    rebinds the *enclosing* scope's `change`, not a new local one here --
    a later `change.future_field` genuinely reads the outer `Change` and
    must still be found, even though the reassignment alone would
    otherwise look exactly like the ninth round's local-shadowing case."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        nonlocal change\n"
        "        change = get_new_change()\n"
        "        return change.future_field\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_ignores_a_comprehension_local_target() -> None:
    """Codex review, fresh evidence, eleventh round: a comprehension opens
    its own scope for its `for` target in real Python 3 -- `[change.
    future_field for change in candidates]` reads only the comprehension-
    local `change`, never the outer parameter, and must not be
    misattributed as an outer read."""
    source = (
        "def outer(change):\n"
        "    return [change.future_field for change in candidates]\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == set()


def test_change_attrs_read_unwraps_a_cast_wrapped_helper_argument() -> None:
    """Codex review, fresh evidence, eleventh round: `helper(cast("Change",
    change))` forwards `change` unchanged at runtime, but the AST shows an
    `ast.Call` to `cast`, not a bare `ast.Name`, so the existing call-
    argument match missed it entirely -- must still be found through the
    followed helper."""
    source = (
        "def outer(change):\n"
        "    return helper(cast('Change', change))\n"
        "def helper(c):\n"
        "    return c.future_field\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_keeps_a_closure_local_alias_of_the_tracked_object() -> None:
    """Codex review, fresh evidence, twelfth round: `def nested(): candidate
    = change; return candidate.future_field` aliases the *captured* outer
    `change` to a name local to `nested`'s own scope -- the ninth round's
    blanket local-shadowing rule was discarding this exact alias the
    fixed-point pass had just established, fighting its own alias-tracking
    mechanism. The read must still be found."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        candidate = change\n"
        "        return candidate.future_field\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_still_shadows_an_unrelated_local_via_augassign() -> None:
    """`AugAssign` is deliberately excluded from the alias exemption above
    -- `change += 1` always combines with something else, never a pure
    alias, and must still be shadowed the way the ninth round established
    for ordinary local rebinding."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        change = get()\n"
        "        change += 1\n"
        "        return change.future_field\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == set()


def test_change_attrs_read_unwraps_a_walrus_wrapped_direct_attribute() -> None:
    """Codex review, fresh evidence, thirteenth round: `(candidate :=
    change).future_field` accesses the attribute directly on the walrus
    expression's value -- an `ast.NamedExpr` node, not a bare `ast.Name` --
    which every existing name-match check missed the same way it missed
    `cast(...)`."""
    source = "def outer(change):\n    return (candidate := change).future_field\n"
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_tracks_a_walrus_alias_for_a_later_separate_read() -> None:
    """A walrus target used to introduce an alias for a *later*, separate
    read (`if (candidate := change): pass; return candidate.future_field`)
    needs the alias itself tracked, not just the immediate-use shape above
    -- covers a nested closure too, where `_locally_bound_names` must not
    shadow a walrus target whose value is itself a tracked alias, mirroring
    the twelfth round's fix for ordinary `Assign`/`AnnAssign`."""
    top_level_source = (
        "def outer(change):\n"
        "    if (candidate := change):\n"
        "        pass\n"
        "    return candidate.future_field\n"
    )
    top_level_funcs = _group_funcs_by_name(ast.parse(top_level_source))
    assert _change_attrs_read("outer", "change", top_level_funcs, set()) == {
        "future_field"
    }
    nested_source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        if (candidate := change):\n"
        "            pass\n"
        "        return candidate.future_field\n"
        "    return nested()\n"
    )
    nested_funcs = _group_funcs_by_name(ast.parse(nested_source))
    assert _change_attrs_read("outer", "change", nested_funcs, set()) == {
        "future_field"
    }


def test_change_attrs_read_tracks_a_match_case_capture_alias() -> None:
    """Codex review, fresh evidence, fourteenth round: `match change: case
    candidate: return candidate.future_field` binds `candidate` to the
    whole subject value via a bare capture pattern -- a distinct binding
    form the alias-tracking fixed-point pass didn't recognize at all
    (only `Assign`/`AnnAssign`/`NamedExpr`), so this read was missed
    entirely."""
    source = (
        "def outer(change):\n"
        "    match change:\n"
        "        case candidate:\n"
        "            return candidate.future_field\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_reads_an_alias_before_a_later_unrelated_rebind() -> None:
    """Codex review, fresh evidence, fifteenth round: `candidate = change;
    value = candidate.future_field; candidate = object()` reads `candidate`
    as the tracked alias BEFORE it is later rebound to something unrelated
    -- this module has no per-statement control-flow ordering, so the
    alias exemption must cover the name for its whole scope rather than
    silently dropping this genuine earlier read."""
    source = (
        "def outer(change):\n"
        "    def nested():\n"
        "        candidate = change\n"
        "        value = candidate.future_field\n"
        "        candidate = object()\n"
        "        return value\n"
        "    return nested()\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }


def test_change_attrs_read_follows_every_same_named_helper_candidate() -> None:
    """Codex review, fresh evidence, fifteenth round: a name collision
    between a real helper and an unrelated, differently-scoped function
    sharing that name must not silently discard the real one --
    `_group_funcs_by_name` groups every candidate, and `_change_attrs_read`
    must follow all of them, unioning their results."""
    source = (
        "def outer(change):\n"
        "    return helper(change)\n"
        "def helper(c):\n"
        "    return c.future_field\n"
        "def unrelated():\n"
        "    def helper(x):\n"
        "        return None\n"
    )
    funcs_by_name = _group_funcs_by_name(ast.parse(source))
    assert len(funcs_by_name["helper"]) == 2
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }

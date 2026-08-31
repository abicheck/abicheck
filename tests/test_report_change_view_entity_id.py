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
    """
    names: set[str] = set()
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
        elif isinstance(current, (ast.AugAssign, ast.AnnAssign)):
            _bind_target(current.target, names)
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
    return frozenset(names - nonlocal_names)


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
        for child in ast.iter_child_nodes(current):
            stack.append((child, shadowed))


def _change_attrs_read(
    func_name: str,
    param_name: str,
    funcs_by_name: dict[str, ast.FunctionDef],
    visited: set[tuple[str, str]],
) -> set[str]:
    """Every ``<param_name>.<attr>`` read reachable from ``func_name``,
    following calls (by position or keyword) into other functions defined
    in the same module that receive the same object under a new name --
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
    a general-purpose analyzer. ``visited`` is keyed on (function, param
    name) so the same helper reached under two different bindings is each
    still explored once, without infinite-looping on recursion. Alias
    tracking runs to a fixed point rather than a single pass (Codex
    review, fresh evidence, seventh round: ``_iter_scope_aware``'s
    stack-based traversal does not yield nodes in source order, so a
    multi-hop chain like ``first = change; second = first`` could reach
    ``second = first`` before ``first`` was itself tracked -- a fixed
    point makes the result independent of traversal order entirely) -- a
    conditional reassignment or an alias shadowed by a later unrelated
    rebinding is still not modeled, matching this helper's existing
    "small, targeted" scope.
    """
    key = (func_name, param_name)
    if key in visited:
        return set()
    visited.add(key)
    func = funcs_by_name.get(func_name)
    if func is None:
        return set()
    tracked = {param_name}
    changed = True
    while changed:
        changed = False
        for node, shadowed in _iter_scope_aware(func):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Name)
                and node.value.id in tracked
                and node.value.id not in shadowed
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in tracked:
                        tracked.add(target.id)
                        changed = True
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Name)
                and node.value.id in tracked
                and node.value.id not in shadowed
                and node.target.id not in tracked
            ):
                tracked.add(node.target.id)
                changed = True
    attrs: set[str] = set()
    for node, shadowed in _iter_scope_aware(func):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in tracked
            and node.value.id not in shadowed
        ):
            attrs.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in tracked
            and node.args[0].id not in shadowed
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            attrs.add(node.args[1].value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in funcs_by_name
        ):
            callee = funcs_by_name[node.func.id]
            arg_names = [a.arg for a in (*callee.args.posonlyargs, *callee.args.args)]
            for i, arg in enumerate(node.args):
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
                if (
                    isinstance(kw.value, ast.Name)
                    and kw.value.id in tracked
                    and kw.value.id not in shadowed
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
    funcs_by_name = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
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
    funcs_by_name = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
    assert _change_attrs_read("outer", "change", funcs_by_name, set()) == {
        "future_field"
    }

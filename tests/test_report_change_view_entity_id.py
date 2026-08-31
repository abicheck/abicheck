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


_SCOPE_BOUNDARY_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
)


def _walk_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Like ``ast.walk``, but never descends into a nested function,
    async function, lambda, or class body (Codex review, fresh evidence,
    sixth round): a nested scope can declare its own parameter/attribute
    of the same spelling as the one being tracked (``def nested(change):
    return change.future_field`` inside the very function being walked),
    and a plain ``ast.walk`` would misattribute that separately-scoped
    read to the outer name. ``node`` itself is always walked regardless
    of its own kind -- only nodes strictly *inside* it that open a new
    scope are excluded, along with everything inside them.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if child is not node and isinstance(child, _SCOPE_BOUNDARY_NODES):
                continue
            stack.append(child)


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
    tracking is a single flat pass over the function body's direct
    ``name = <tracked>`` assignments (in source order) -- an alias of an
    alias is followed because the newly-discovered name is added to the
    same tracked set before the body is re-scanned for reads, but a
    conditional reassignment or an alias shadowed by a later unrelated
    rebinding is not modeled, matching this helper's existing "small,
    targeted" scope.
    """
    key = (func_name, param_name)
    if key in visited:
        return set()
    visited.add(key)
    func = funcs_by_name.get(func_name)
    if func is None:
        return set()
    tracked = {param_name}
    for node in _walk_own_scope(func):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id in tracked
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    tracked.add(target.id)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Name)
            and node.value.id in tracked
        ):
            tracked.add(node.target.id)
    attrs: set[str] = set()
    for node in _walk_own_scope(func):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in tracked
        ):
            attrs.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in tracked
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
                    and i < len(arg_names)
                ):
                    attrs |= _change_attrs_read(
                        node.func.id, arg_names[i], funcs_by_name, visited
                    )
            for kw in node.keywords:
                if isinstance(kw.value, ast.Name) and kw.value.id in tracked and kw.arg:
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

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

"""Self-enforcing gate: a detector that ignores ``new`` is declared ``one_sided``.

A registered detector that never reads its second (``new``) snapshot is a
single-snapshot hygiene check, not an OLD->NEW comparison. ``compare
--no-baseline`` runs no comparison detectors at all, so such a check only
reaches the audit if it is registered ``one_sided=True``
(``DetectorRegistry.run_one_sided``). ``visibility_leak`` was not, and its
findings silently vanished from every no-baseline audit (known-gaps:
"``compare --no-baseline`` silently drops one-sided detectors' findings").

This test walks every detector in the real registry, decides from the
source whether it reads ``new`` -- following calls into module-level
helpers the ``new`` argument is forwarded to, since ``visibility_leak``'s
registered wrapper forwards ``new`` to a helper that ``del``s it -- and
requires the ``one_sided`` declaration to agree in both directions.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable
from typing import Any

import pytest

from abicheck.detector_registry import DetectorRegistry, registry


def _function_def(fn: Callable[..., Any]) -> ast.FunctionDef | None:
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return None
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == fn.__name__:
            return node
    return None


def _param_is_ignored(
    fn: Callable[..., Any], index: int, _seen: frozenset[int] = frozenset()
) -> bool:
    """Whether positional parameter *index* of *fn* is never really read.

    A read that only forwards the parameter positionally to another
    module-level function which itself ignores that position does not
    count. Anything the analysis cannot resolve counts as a read, so the
    answer errs toward "reads ``new``" -- never toward a false ``one_sided``.
    """
    fn = inspect.unwrap(fn)
    node = _function_def(fn)
    if node is None or len(node.args.args) <= index:
        return False
    param = node.args.args[index].arg
    loads = {
        id(n)
        for n in ast.walk(node)
        if isinstance(n, ast.Name) and n.id == param and isinstance(n.ctx, ast.Load)
    }
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        if not isinstance(call.func, ast.Name):
            continue
        callee = fn.__globals__.get(call.func.id)
        if not inspect.isfunction(callee) or id(callee) in _seen:
            continue
        for pos, arg in enumerate(call.args):
            if (
                isinstance(arg, ast.Name)
                and arg.id == param
                and _param_is_ignored(callee, pos, _seen | {id(fn)})
            ):
                loads.discard(id(arg))
    return not loads


def _all_entries() -> list[Any]:
    registry.ensure_loaded()
    return sorted(registry._detectors, key=lambda e: e.order)


def test_every_detector_ignoring_new_is_declared_one_sided() -> None:
    undeclared = [
        e.name for e in _all_entries() if not e.one_sided and _param_is_ignored(e.fn, 1)
    ]
    assert not undeclared, (
        "these detectors never read their `new` snapshot, so they are "
        "single-snapshot hygiene checks: register them "
        "`@registry.detector(..., one_sided=True)` or `compare --no-baseline` "
        f"silently drops their findings: {undeclared}"
    )


def test_every_one_sided_detector_really_ignores_new() -> None:
    misdeclared = [
        e.name for e in _all_entries() if e.one_sided and not _param_is_ignored(e.fn, 1)
    ]
    assert not misdeclared, (
        "a `one_sided` detector runs with the candidate as both arguments "
        f"under --no-baseline, so it must not read `new`: {misdeclared}"
    )


def test_the_known_population_is_classified() -> None:
    """The population enumerated when the gap was fixed (step 1 of its fix shape)."""
    assert "visibility_leak" in registry.one_sided_detector_names


# --- the analyser itself must not be vacuous -------------------------------


def _helper_ignores_second(a: Any, b: Any) -> list[Any]:
    del b
    return [a]


def _helper_reads_second(a: Any, b: Any) -> list[Any]:
    return [a, b]


def _det_del(old: Any, new: Any) -> list[Any]:
    del new
    return [old]


def _det_unused(old: Any, new: Any) -> list[Any]:
    return [old]


def _det_forward_to_ignorer(old: Any, new: Any) -> list[Any]:
    return _helper_ignores_second(old, new)


def _det_forward_to_reader(old: Any, new: Any) -> list[Any]:
    return _helper_reads_second(old, new)


def _det_reads(old: Any, new: Any) -> list[Any]:
    return [old, new.functions]


def _det_forward_and_read(old: Any, new: Any) -> list[Any]:
    return _helper_ignores_second(old, new) + [new]


def _det_unknown_callee(old: Any, new: Any) -> list[Any]:
    return list(map(str, (old, new)))


@pytest.mark.parametrize(
    ("fn", "ignored"),
    [
        (_det_del, True),
        (_det_unused, True),
        (_det_forward_to_ignorer, True),
        (_det_forward_to_reader, False),
        (_det_reads, False),
        (_det_forward_and_read, False),
        (_det_unknown_callee, False),
    ],
)
def test_analyser_classifies_new_usage(fn: Callable[..., Any], ignored: bool) -> None:
    assert _param_is_ignored(fn, 1) is ignored


def test_run_one_sided_runs_only_declared_detectors_and_marks_them() -> None:
    reg = DetectorRegistry()
    calls: list[str] = []

    from abicheck.model.change import Change
    from abicheck.model.change_catalog.kinds import ChangeKind

    def _mk(tag: str) -> Change:
        return Change(kind=ChangeKind.VISIBILITY_LEAK, symbol=tag, description=tag)

    @reg.detector("hygiene", one_sided=True)
    def _h(old: Any, new: Any) -> list[Change]:
        del new
        calls.append("hygiene")
        return [_mk("h")]

    @reg.detector("comparison")
    def _c(old: Any, new: Any) -> list[Change]:
        calls.append("comparison")
        return [_mk("c")]

    snap = object()
    changes, results = reg.run_one_sided(snap)  # type: ignore[arg-type]
    assert calls == ["hygiene"]
    assert [c.symbol for c in changes] == ["h"]
    assert all(c.candidate_side_enrichment for c in changes)
    assert [r.name for r in results] == ["hygiene"]

    calls.clear()
    two_sided, _ = reg.run_all(snap, snap)  # type: ignore[arg-type]
    assert calls == ["hygiene", "comparison"]
    # Two-sided runs keep the finding unmarked: the marker is applied only by
    # the no-baseline entry point, so ordinary compare reports are unchanged.
    assert not any(c.candidate_side_enrichment for c in two_sided)

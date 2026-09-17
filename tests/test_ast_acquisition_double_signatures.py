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

"""A test double of an AST-acquisition entry point may not be narrower.

Adding ``group`` to ``run_ast_acquisition``/``AstAcquisitionScope.run``
broke five test modules' doubles at once, and the way it broke is the
point: every wrapped call raised ``TypeError``, so the observations those
tests were built on came back *empty* rather than wrong. One assertion --
``assert len(cold_keys) >= 12`` -- caught it, in the ``integration`` lane,
which the default fast command skips. Nothing in a fast run said a word.

So this module states the invariant mechanically and in the fast lane: any
callable a test installs over one of these entry points must accept every
parameter the real one has. It reads the *real* signature with
``inspect.signature`` rather than restating it, so a future parameter is
covered the day it is added, with no list here to keep in step.

Registry: ``test_double.narrower_than_the_real_signature`` in
``tests/regressions/manifest_performance.py``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from abicheck.dumper_cache import AstAcquisitionScope, run_ast_acquisition

_TESTS = Path(__file__).resolve().parent

# attribute name as monkeypatched -> the real callable it replaces, whether
# the double is installed on the class (so it also takes `self`), and the
# owner text its patch target must mention. That last field is load-bearing:
# `"run"` alone also matches `monkeypatch.setattr(subprocess, "run", ...)`,
# which has nothing to do with AST acquisition, and the first version of
# this module reported exactly those as violations.
_GUARDED: dict[str, tuple[object, bool, str | None]] = {
    "run_ast_acquisition": (run_ast_acquisition, False, None),
    "run": (AstAcquisitionScope.run, True, "AstAcquisitionScope"),
}


def _required_names(real: object, *, method: bool) -> list[str]:
    """Parameter names a faithful double must accept."""
    params = list(inspect.signature(real).parameters)
    if method and params and params[0] == "self":
        params = params[1:]
    return params


def _setattr_target(call: ast.Call) -> str | None:
    """The guarded attribute a ``monkeypatch.setattr(obj, "name", x)`` targets.

    ``None`` unless the name is guarded *and* the patched object matches
    that entry's required owner text, so an unrelated ``setattr(mod,
    "run", ...)`` is not mistaken for an acquisition double.
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "setattr":
        return None
    if len(call.args) < 3 or not isinstance(call.args[1], ast.Constant):
        return None
    name = call.args[1].value
    if not isinstance(name, str) or name not in _GUARDED:
        return None
    _real, _method, owner = _GUARDED[name]
    if owner is not None and owner not in ast.unparse(call.args[0]):
        return None
    return name


def _replacement_name(call: ast.Call) -> str | None:
    """The identifier of the callable being installed, when it is a name."""
    third = call.args[2]
    return third.id if isinstance(third, ast.Name) else None


def _functions_by_name(tree: ast.AST) -> dict[str, ast.FunctionDef]:
    """Every ``def`` in the module, at any nesting depth, keyed by name."""
    out: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out[node.name] = node  # type: ignore[assignment]
    return out


def _accepted(fn: ast.FunctionDef) -> tuple[set[str], bool, bool]:
    """(named parameters, has ``*args``, has ``**kwargs``) for *fn*."""
    a = fn.args
    named = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    return named, a.vararg is not None, a.kwarg is not None


def _installed_doubles() -> list[tuple[Path, str, ast.FunctionDef, bool]]:
    """Every same-file double installed over a guarded entry point."""
    found: list[tuple[Path, str, ast.FunctionDef, bool]] = []
    for path in sorted(_TESTS.rglob("test_*.py")):
        if path == Path(__file__).resolve():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - unreadable file
            continue
        defs = _functions_by_name(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _setattr_target(node)
            if target is None:
                continue
            replacement = _replacement_name(node)
            fn = defs.get(replacement) if replacement else None
            if fn is None:
                continue
            found.append((path, target, fn, _GUARDED[target][1]))
    return found


class TestDoublesMatchTheRealSignature:
    """The invariant, over whatever doubles the suite currently installs."""

    def test_at_least_one_double_is_inspected(self) -> None:
        """Vacuity guard: an empty scan would satisfy the check below.

        The discovery is structural, so a renamed helper or a changed
        patch spelling would silently empty it -- which is the same
        failure mode this module exists to catch.
        """
        assert len(_installed_doubles()) >= 3

    def test_no_double_is_narrower_than_what_it_replaces(self) -> None:
        narrower: list[str] = []
        for path, target, fn, is_method in _installed_doubles():
            real, _method, _owner = _GUARDED[target]
            required = _required_names(real, method=is_method)
            named, has_varargs, has_kwargs = _accepted(fn)
            for param in required:
                if param in named or has_kwargs or has_varargs:
                    continue
                narrower.append(
                    f"{path.relative_to(_TESTS)}::{fn.name} replaces "
                    f"{target!r} but cannot accept {param!r}"
                )
        assert not narrower, "\n".join(narrower)

    @pytest.mark.parametrize("target", sorted(_GUARDED))
    def test_the_real_signature_is_readable(self, target: str) -> None:
        """The oracle itself must not silently degrade to an empty list."""
        real, is_method, _owner = _GUARDED[target]
        assert _required_names(real, method=is_method), target

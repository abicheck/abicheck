# Copyright 2026 Nikolay Petrov
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

"""Template inner-type deep analysis detectors (PR #89, issues #38 / #73).

Split from ``diff_platform.py`` to keep that module under the AI-readiness
file-size soft cap. Re-exported from ``diff_platform`` for back-compat with
``abicheck.checker`` and the test suite.
"""
from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .diff_symbols import _public_functions
from .model import AbiSnapshot


def _split_top_level_args(inner: str) -> list[str]:
    """Split a template argument string on top-level commas.

    Respects nested ``<>``, ``()``, ``[]``, and ``{}`` delimiters so that
    types like ``std::function<void(int, double)>`` are not split incorrectly.
    """
    _OPEN = {"<": 0, "(": 1, "[": 2, "{": 3}  # pylint: disable=invalid-name
    _CLOSE = {">": 0, ")": 1, "]": 2, "}": 3}  # pylint: disable=invalid-name

    args: list[str] = []
    current: list[str] = []
    nesting = [0, 0, 0, 0]  # angle, paren, bracket, brace

    for c in inner:
        if c in _OPEN:
            nesting[_OPEN[c]] += 1
            current.append(c)
        elif c == ">" and nesting[0] > 0:
            # Angle depth always unwinds when a '>' is seen — even inside an
            # open '('/'['/'{' — so types like
            # ``Foo<void (*)(std::vector<int>), double>`` parse correctly.
            nesting[0] -= 1
            current.append(c)
        elif c in _CLOSE and c != ">":
            nesting[_CLOSE[c]] -= 1
            current.append(c)
        elif c == "," and all(n == 0 for n in nesting):
            args.append("".join(current).strip())
            current = []
        else:
            current.append(c)
    if current:
        args.append("".join(current).strip())
    return args


def _extract_template_args(type_str: str) -> list[str] | None:
    """Extract template argument string(s) from a type like ``vector<int>``.

    Returns a list of top-level template arguments (splitting on ``,`` while
    respecting nested ``<>``), or ``None`` if the type is not a template.

    Examples::

        "std::vector<int>"         → ["int"]
        "std::map<int, double>"    → ["int", "double"]
        "Foo<Bar<int>, double>"    → ["Bar<int>", "double"]
        "int"                      → None
        "std::vector<>"            → []
    """
    lt = type_str.find("<")
    if lt == -1:
        return None
    # Find the matching closing >
    depth = 0
    for i, ch in enumerate(type_str[lt:], start=lt):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0:
                inner = type_str[lt + 1 : i].strip()
                if not inner:
                    return []
                return _split_top_level_args(inner)
    return None  # unbalanced brackets — skip


def _template_outer(type_str: str) -> str:
    """Return the outer template name, e.g. ``std::vector`` from ``std::vector<int>``."""
    lt = type_str.find("<")
    return type_str[:lt].rstrip() if lt != -1 else type_str


@registry.detector("template_inner_types")
def _diff_template_inner_types(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect ABI-relevant template inner-type changes in function signatures.

    Compares param types and return types for functions present in both snapshots.
    When both old and new have a template specialization (e.g. ``std::vector<T>``)
    with the *same outer template name* but *different type arguments*, this is an
    ABI break: the instantiation's layout, size, and ABI fingerprint all differ.

    This detector fires in addition to FUNC_PARAMS_CHANGED / FUNC_RETURN_CHANGED
    to provide a more specific, actionable description of the inner-type change.

    Example::

        void process(std::vector<int> v)   →   void process(std::vector<double> v)
        # → TEMPLATE_PARAM_TYPE_CHANGED: "std::vector" inner type int → double

    NOTE on mangling: Under the Itanium C++ ABI, parameter types ARE included in the
    mangled symbol name, so a real ``std::vector<int>`` → ``std::vector<double>`` param
    change produces different mangled names (FUNC_REMOVED + FUNC_ADDED, not an intersection
    hit). This detector therefore only fires for:
      1. Return type template changes (return type is NOT in Itanium mangling for
         non-template functions, so the mangled name stays the same).
      2. Cases where the snapshot was produced with simplified/un-mangled names (e.g.
         from header-only analysis without a compiled .so).
    For production ELF-based snapshots, FUNC_PARAMS_CHANGED is the primary signal.
    """
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled in set(old_map) & set(new_map):
        f_old = old_map[mangled]
        f_new = new_map[mangled]

        # --- Return type template inner change ---
        old_ret_args = _extract_template_args(f_old.return_type)
        new_ret_args = _extract_template_args(f_new.return_type)
        if (
            old_ret_args is not None
            and new_ret_args is not None
            and old_ret_args != new_ret_args
            and _template_outer(f_old.return_type) == _template_outer(f_new.return_type)
        ):
            changes.append(make_change(
                ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED,
                symbol=mangled,
                name=f_old.name,
                old=f_old.return_type,
                new=f_new.return_type,
            ))

        # --- Param template inner change ---
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            old_args = _extract_template_args(p_old.type)
            new_args = _extract_template_args(p_new.type)
            if (
                old_args is not None
                and new_args is not None
                and old_args != new_args
                and _template_outer(p_old.type) == _template_outer(p_new.type)
            ):
                param_label = p_old.name or str(i)
                changes.append(make_change(
                    ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
                    symbol=mangled,
                    name=f_old.name,
                    detail=param_label,
                    old=p_old.type,
                    new=p_new.type,
                ))

    return changes

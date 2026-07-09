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

"""Python-level API diff for CPython extension modules (G23).

Diffs two :class:`~abicheck.python_api.PythonApiSurface` objects — the
Python-visible API recovered from ``.pyi`` type stubs — and emits Python-level
``ChangeKind``s that the native C-ABI check (G14) cannot see. A build can be
byte-for-byte C-ABI compatible yet break every ``import`` caller by renaming a
keyword argument or dropping a default; that break lives in the signatures, not
in the export table.

All findings are **source-level** breaks (``API_BREAK``) or behavioural
**risks** (``COMPATIBLE_WITH_RISK``): a Python module has no separate binary-ABI
layer at this level, so a signature change never corrupts an already-loaded
caller — it breaks *re-import / re-call at the source*. Additions are
``COMPATIBLE``.

Registered via ``@registry.detector("python_api")`` and skipped automatically
when the new snapshot carries no recovered Python surface (no stub shipped), so
the check degrades honestly rather than reporting a phantom mass removal.
"""

from __future__ import annotations

from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot
from .python_api import (
    KEYWORD_ONLY,
    POSITIONAL_OR_KEYWORD,
    VAR_KEYWORD,
    VAR_POSITIONAL,
    PyClass,
    PyFunction,
    PyParameter,
    PythonApiSurface,
)


def _has_kind(fn: PyFunction, kind: str) -> bool:
    """True when *fn* declares a parameter of the given *kind*."""
    return any(p.kind == kind for p in fn.parameters)


def _param_of_kind(fn: PyFunction, kind: str) -> PyParameter | None:
    """The parameter of *kind* (e.g. the ``*args`` collector), or ``None``."""
    for p in fn.parameters:
        if p.kind == kind:
            return p
    return None


def _is_keyword_capable(p: PyParameter) -> bool:
    """True when *p* can be passed by keyword (not positional-only / variadic)."""
    return p.kind in (POSITIONAL_OR_KEYWORD, KEYWORD_ONLY)


def _kind_narrowing_detail(name: str, op: PyParameter, np: PyParameter) -> str | None:
    """Describe a *narrowing* pass-kind change on a same-named parameter, else ``None``.

    Narrowing removes a way the argument could be passed and so breaks existing
    call sites; widening (gaining a way) is compatible and returns ``None``.
    """
    if op.is_positional and np.kind == KEYWORD_ONLY:
        return f"{name}: {op.kind} → {np.kind} (can no longer be passed positionally)"
    if _is_keyword_capable(op) and not _is_keyword_capable(np):
        return f"{name}: {op.kind} → {np.kind} (can no longer be passed by keyword)"
    return None


def _module_prefix(new: PythonApiSurface, old: PythonApiSurface) -> str:
    """A stable ``python:<module>`` identifier prefix for findings."""
    name = new.module_name or old.module_name
    return f"python:{name}" if name else "python:<extension>"


def _named_param_map(fn: PyFunction) -> dict[str, PyParameter]:
    """Map of argument-naming parameters (excludes ``*args`` / ``**kwargs``)."""
    return {p.name: p for p in fn.named_parameters}


def _positional_names(fn: PyFunction) -> list[str]:
    """Ordered names of parameters that can be passed positionally."""
    return [p.name for p in fn.parameters if p.is_positional]


def _diff_signature(
    old_fn: PyFunction, new_fn: PyFunction, symbol: str, qualified: str
) -> list[Change]:
    """Diff two function/method signatures with the same qualified name.

    Emits parameter-level findings (removed / added-required / renamed /
    default-removed / type-changed / binding-changed) plus a return-annotation
    change. The binding checks compare the *ordered* parameter list and each
    parameter's kind — not just the set of names — so a call-shape break with
    unchanged names (positional→keyword-only, a reorder, or an optional
    parameter inserted mid-list) is not missed.
    """
    changes: list[Change] = []
    old_params = _named_param_map(old_fn)
    new_params = _named_param_map(new_fn)

    common = [n for n in old_params if n in new_params]
    removed = [n for n in old_params if n not in new_params]
    added = [n for n in new_params if n not in old_params]

    # Exactly one dropped + one gained named parameter reads as a rename
    # (the fixture case: `encoding` → `codec`). The rename is folded into the
    # positional-binding comparison below (via ``rename_map``) so a same-position
    # rename is not double-reported as a reorder. But a rename is only a *break*
    # when the old parameter could be passed **by keyword**: renaming a
    # positional-only parameter (`def f(a, /)` → `def f(b, /)`) is invisible to
    # callers (they pass by position, and the name was never a valid keyword), so
    # it is recorded in ``rename_map`` (to suppress a false positional-order
    # finding) but emits nothing.
    rename_map: dict[str, str] = {}
    if len(removed) == 1 and len(added) == 1:
        old_name, new_name = removed[0], added[0]
        rename_map[old_name] = new_name
        if _is_keyword_capable(old_params[old_name]):
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_RENAMED,
                    symbol=symbol,
                    name=qualified,
                    old=old_name,
                    new=new_name,
                    detail=qualified,
                )
            )
    else:
        for n in removed:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_REMOVED,
                    symbol=symbol,
                    name=qualified,
                    detail=n,
                )
            )
        for n in added:
            p = new_params[n]
            # A newly *required* parameter (no default) breaks every existing
            # caller. A new optional parameter is backward compatible on its
            # own; if it is inserted *before* an existing positional parameter
            # it shifts bindings — that is caught by the positional check below.
            if not p.has_default:
                changes.append(
                    make_change(
                        ChangeKind.PYTHON_API_PARAMETER_ADDED,
                        symbol=symbol,
                        name=qualified,
                        detail=n,
                    )
                )

    for n in common:
        op, np = old_params[n], new_params[n]
        # Dropping a default makes a previously optional argument mandatory —
        # callers relying on the default now raise a missing-argument TypeError.
        if op.has_default and not np.has_default:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_DEFAULT_REMOVED,
                    symbol=symbol,
                    name=qualified,
                    detail=n,
                )
            )
        # An annotation change is a type-checker / behavioural risk, not a hard
        # runtime break — only flagged when both sides declare a type and they
        # differ (adding or removing an annotation is not reported).
        if op.annotation and np.annotation and op.annotation != np.annotation:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED,
                    symbol=symbol,
                    name=qualified,
                    old=op.annotation,
                    new=np.annotation,
                    detail=n,
                )
            )
        # Pass-kind narrowing on a same-named parameter: losing the ability to
        # pass it positionally (…→keyword-only) breaks positional callers;
        # losing the ability to pass it by keyword (…→positional-only) breaks
        # keyword callers. Widening (positional-only→positional-or-keyword, or
        # keyword-only→positional-or-keyword) is compatible and not reported.
        detail = _kind_narrowing_detail(n, op, np)
        if detail is not None:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED,
                    symbol=symbol,
                    name=qualified,
                    detail=detail,
                )
            )

    # Positional-binding compatibility: an existing positional caller passing
    # k arguments requires that the new positional sequence keep the old one
    # (modulo an at-most-one rename) as an order-preserving prefix — you may
    # only *append* positional parameters. Any earlier divergence rebinds a
    # positional argument (a reorder, or an optional parameter inserted before
    # an existing one). Trailing positional removals are already reported as
    # PARAMETER_REMOVED, so comparing only the shared prefix length avoids
    # double-reporting them here.
    old_pos = [rename_map.get(n, n) for n in _positional_names(old_fn)]
    new_pos = _positional_names(new_fn)
    limit = min(len(old_pos), len(new_pos))
    if old_pos[:limit] != new_pos[:limit]:
        i = next(k for k in range(limit) if old_pos[k] != new_pos[k])
        changes.append(
            make_change(
                ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED,
                symbol=symbol,
                name=qualified,
                detail=(
                    f"positional order changed at position {i}: "
                    f"'{old_pos[i]}' → '{new_pos[i]}'"
                ),
            )
        )

    # ``*args`` / ``**kwargs`` collectors. Dropping one breaks callers that
    # passed extra positional / keyword arguments (they now raise ``TypeError``);
    # adding one is more permissive and compatible. When the collector survives
    # but its *annotation* changed, that is the same type-contract RISK the named
    # parameters get (only flagged when both sides declare a type and differ).
    for var_kind, label in ((VAR_POSITIONAL, "*args"), (VAR_KEYWORD, "**kwargs")):
        ov, nv = _param_of_kind(old_fn, var_kind), _param_of_kind(new_fn, var_kind)
        if ov is not None and nv is None:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_REMOVED,
                    symbol=symbol,
                    name=qualified,
                    detail=label,
                )
            )
        elif (
            ov is not None
            and nv is not None
            and ov.annotation
            and nv.annotation
            and ov.annotation != nv.annotation
        ):
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED,
                    symbol=symbol,
                    name=qualified,
                    old=ov.annotation,
                    new=nv.annotation,
                    detail=label,
                )
            )

    if (
        old_fn.return_annotation
        and new_fn.return_annotation
        and old_fn.return_annotation != new_fn.return_annotation
    ):
        changes.append(
            make_change(
                ChangeKind.PYTHON_API_RETURN_TYPE_CHANGED,
                symbol=symbol,
                name=qualified,
                old=old_fn.return_annotation,
                new=new_fn.return_annotation,
                detail=qualified,
            )
        )

    return changes


def _callable_kind_detail(old_fn: PyFunction, new_fn: PyFunction) -> str | None:
    """Describe an async / descriptor protocol change, else ``None``."""
    parts: list[str] = []
    if old_fn.is_async != new_fn.is_async:
        parts.append("async def → def" if old_fn.is_async else "def → async def")
    if old_fn.descriptor != new_fn.descriptor:
        parts.append(f"{old_fn.descriptor} → {new_fn.descriptor}")
    return "; ".join(parts) if parts else None


def _overload_identity(fn: PyFunction) -> tuple[Any, ...]:
    """A hashable identity for one overload variant — parameters + protocol.

    Identity is the variant's **required input call shape**: the name, kind, and
    input type of each parameter that has *no* default, plus ``async`` /
    descriptor kind and whether it accepts ``*args`` / ``**kwargs``. Excluded on
    purpose:

    * the **return annotation** — it does not distinguish overloads, so a
      return-only change on a matched variant is a return-type-change RISK, not
      a spurious removal (mirrors the non-overloaded path);
    * **optional (defaulted) parameters** — adding one compatibly *widens* a
      variant, so it must still match the old one rather than read as a removed
      overload plus a new one.

    ``*args`` / ``**kwargs`` presence stays in the identity because dropping a
    collector rejects extra arguments (a real removal).
    """
    return (
        fn.is_async,
        fn.descriptor,
        tuple(
            (p.name, p.kind, p.annotation)
            for p in fn.named_parameters
            if not p.has_default
        ),
        _has_kind(fn, VAR_POSITIONAL),
        _has_kind(fn, VAR_KEYWORD),
    )


def _render_signature(fn: PyFunction) -> str:
    """A short human rendering of one overload variant for a finding detail."""
    args = ", ".join(p.annotation or p.name for p in fn.named_parameters)
    return f"({args}) -> {fn.return_annotation or '?'}"


def _overload_variants(fn: PyFunction) -> list[PyFunction]:
    """All signature variants of *fn* (its ``overloads`` list, or itself)."""
    return fn.overloads or [fn]


def _diff_callable(
    old_fn: PyFunction, new_fn: PyFunction, symbol: str, qualified: str
) -> list[Change]:
    """Diff two callables of the same qualified name.

    Handles the callable *protocol* (async / descriptor kind), then either the
    overload set (when either side is ``@overload``-ed) or the single signature.
    """
    changes: list[Change] = []
    detail = _callable_kind_detail(old_fn, new_fn)
    if detail is not None:
        changes.append(
            make_change(
                ChangeKind.PYTHON_API_CALLABLE_KIND_CHANGED,
                symbol=symbol,
                name=qualified,
                detail=detail,
            )
        )

    if old_fn.overloads or new_fn.overloads:
        new_by_id: dict[tuple[Any, ...], PyFunction] = {}
        for v in _overload_variants(new_fn):
            new_by_id.setdefault(_overload_identity(v), v)
        for variant in _overload_variants(old_fn):
            match = new_by_id.get(_overload_identity(variant))
            if match is None:
                # The variant's call shape is gone. Added variants are
                # compatible and not reported.
                changes.append(
                    make_change(
                        ChangeKind.PYTHON_API_OVERLOAD_REMOVED,
                        symbol=symbol,
                        name=qualified,
                        detail=_render_signature(variant),
                    )
                )
            elif (
                variant.return_annotation
                and match.return_annotation
                and variant.return_annotation != match.return_annotation
            ):
                # Same call shape, different return: a return-type change (RISK),
                # not a removed overload — mirrors the non-overloaded path.
                changes.append(
                    make_change(
                        ChangeKind.PYTHON_API_RETURN_TYPE_CHANGED,
                        symbol=symbol,
                        name=qualified,
                        old=variant.return_annotation,
                        new=match.return_annotation,
                        detail=_render_signature(variant),
                    )
                )
    else:
        changes.extend(_diff_signature(old_fn, new_fn, symbol, qualified))
    return changes


def _diff_methods(old_cls: PyClass, new_cls: PyClass, prefix: str) -> list[Change]:
    """Diff the public methods of a class present on both sides."""
    changes: list[Change] = []
    for name, old_m in old_cls.methods.items():
        qualified = f"{old_cls.name}.{name}"
        symbol = f"{prefix}.{qualified}"
        new_m = new_cls.methods.get(name)
        if new_m is None:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_METHOD_REMOVED,
                    symbol=symbol,
                    name=qualified,
                    detail=qualified,
                )
            )
        else:
            changes.extend(_diff_callable(old_m, new_m, symbol, qualified))
    for name, new_m in new_cls.methods.items():
        if name not in old_cls.methods:
            qualified = f"{new_cls.name}.{name}"
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_METHOD_ADDED,
                    symbol=f"{prefix}.{qualified}",
                    name=qualified,
                    detail=qualified,
                )
            )
    return changes


def _diff_functions(
    old: PythonApiSurface, new: PythonApiSurface, prefix: str
) -> list[Change]:
    """Diff top-level functions between two surfaces."""
    changes: list[Change] = []
    for name, old_fn in old.functions.items():
        symbol = f"{prefix}.{name}"
        new_fn = new.functions.get(name)
        if new_fn is None:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_FUNCTION_REMOVED,
                    symbol=symbol,
                    name=name,
                    detail=name,
                )
            )
        else:
            changes.extend(_diff_callable(old_fn, new_fn, symbol, name))
    for name in new.functions:
        if name not in old.functions:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_FUNCTION_ADDED,
                    symbol=f"{prefix}.{name}",
                    name=name,
                    detail=name,
                )
            )
    return changes


def _diff_classes(
    old: PythonApiSurface, new: PythonApiSurface, prefix: str
) -> list[Change]:
    """Diff top-level classes (and their methods) between two surfaces."""
    changes: list[Change] = []
    for name, old_cls in old.classes.items():
        symbol = f"{prefix}.{name}"
        new_cls = new.classes.get(name)
        if new_cls is None:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_CLASS_REMOVED,
                    symbol=symbol,
                    name=name,
                    detail=name,
                )
            )
        else:
            changes.extend(_diff_methods(old_cls, new_cls, prefix))
    for name in new.classes:
        if name not in old.classes:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_CLASS_ADDED,
                    symbol=f"{prefix}.{name}",
                    name=name,
                    detail=name,
                )
            )
    return changes


@registry.detector(
    "python_api",
    requires_support=lambda o, n: (
        n.python_api is not None,
        "missing Python API surface (no .pyi stub recovered)",
    ),
)
def _diff_python_api(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Python-level API compatibility detector (G23).

    Compares the Python-visible surface (top-level functions, classes, methods,
    and their signatures) recovered from ``.pyi`` stubs. Complements — does not
    replace — the G14 native-ABI check: a single ``compare`` surfaces both.

    A missing old surface (a freshly stubbed module, or an old build that shipped
    no stub) is treated as an empty baseline, so everything in the new surface
    reads as an addition rather than a spurious break.
    """
    n = new.python_api
    assert n is not None  # guaranteed by requires_support
    o = old.python_api if old.python_api is not None else PythonApiSurface()

    prefix = _module_prefix(n, o)
    changes: list[Change] = []
    changes.extend(_diff_functions(o, n, prefix))
    changes.extend(_diff_classes(o, n, prefix))
    return changes

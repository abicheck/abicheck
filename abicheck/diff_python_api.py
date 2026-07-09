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
    POSITIONAL_ONLY,
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

    # Exactly one dropped + one gained named parameter *at the same ordinal
    # position* reads as a rename of that slot (the fixture case: `encoding` →
    # `codec`, both the 2nd named parameter). Requiring positional alignment
    # distinguishes a rename from an unrelated shuffle — `f(a, b)` → `f(b, c)`
    # drops `a` and adds `c` at different positions, so it is reported as a
    # removal + addition (and a reorder), not a phantom `a`→`c` rename. The
    # rename is folded into the positional-binding comparison below (via
    # ``rename_map``) so a same-position rename is not double-reported as a
    # reorder. A rename is only a *break* when the old parameter could be passed
    # **by keyword**: renaming a positional-only parameter (`def f(a, /)` →
    # `def f(b, /)`) is invisible to callers (they pass by position, and the name
    # was never a valid keyword), so it is recorded in ``rename_map`` (to
    # suppress a false positional-order finding) but emits nothing.
    old_named = list(old_params)
    new_named = list(new_params)
    rename_map: dict[str, str] = {}
    if (
        len(removed) == 1
        and len(added) == 1
        and old_named.index(removed[0]) == new_named.index(added[0])
    ):
        old_name, new_name = removed[0], added[0]
        op, np = old_params[old_name], new_params[new_name]
        rename_map[old_name] = new_name
        if _is_keyword_capable(op):
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
        # The renamed slot is position-bound: still compare its default and
        # annotation, so a positional-only rename that ALSO drops a default
        # (`def f(a=1, /)` → `def f(b, /)`, breaking no-arg callers) or changes
        # a type is not silently lost.
        if op.has_default and not np.has_default:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_DEFAULT_REMOVED,
                    symbol=symbol,
                    name=qualified,
                    detail=new_name,
                )
            )
        if op.annotation and np.annotation and op.annotation != np.annotation:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_TYPE_CHANGED,
                    symbol=symbol,
                    name=qualified,
                    old=op.annotation,
                    new=np.annotation,
                    detail=new_name,
                )
            )
        # The renamed slot may also change *binding kind* — a positional-only
        # slot replaced by a keyword-only one (`def f(a, /)` → `def f(*, b)`)
        # breaks positional callers (`f(1)` now raises TypeError) even though no
        # keyword-renamed name existed. Because this branch bypasses the
        # removed/added and positional-prefix checks below, the narrowing would
        # otherwise be swallowed, so compare the kinds here directly.
        kind_detail = _kind_narrowing_detail(new_name, op, np)
        if kind_detail is not None:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_API_PARAMETER_KIND_CHANGED,
                    symbol=symbol,
                    name=qualified,
                    detail=kind_detail,
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


def _param_key(p: PyParameter, pos_only_index: int) -> tuple[Any, ...]:
    """Overload-identity key for a named parameter.

    Positional-only parameters are keyed by their *slot* (callers bind them by
    position; the source name is invisible and freely renameable), so
    ``def f(a: int, /)`` and ``def f(b: int, /)`` share a key.  Keyword-capable
    parameters are keyed by name, since callers may pass them by keyword.  The
    annotation is part of the identity either way — two ``@overload`` variants
    that differ only by input type are genuinely distinct overloads.
    """
    slot: Any = pos_only_index if p.kind == POSITIONAL_ONLY else p.name
    return (slot, p.kind, p.annotation)


def _required_shape(fn: PyFunction) -> tuple[Any, ...]:
    """Ordered (slot, kind, input-type) keys of each *required* parameter."""
    keys: list[tuple[Any, ...]] = []
    pos_only = 0
    for p in fn.named_parameters:
        if p.has_default:
            continue
        keys.append(_param_key(p, pos_only))
        if p.kind == POSITIONAL_ONLY:
            pos_only += 1
    return tuple(keys)


def _optional_shape(fn: PyFunction) -> frozenset[tuple[Any, ...]]:
    """The (slot, kind, input-type) set of each *optional* (defaulted) parameter."""
    keys: list[tuple[Any, ...]] = []
    pos_only = 0
    for p in fn.named_parameters:
        if p.kind == POSITIONAL_ONLY:
            if p.has_default:
                keys.append(_param_key(p, pos_only))
            pos_only += 1
        elif p.has_default:
            keys.append(_param_key(p, pos_only))
    return frozenset(keys)


def _covers_required_shape(
    old_req: tuple[Any, ...],
    new_req: tuple[Any, ...],
    new_opt: frozenset[tuple[Any, ...]],
) -> bool:
    """True when ``new_v``'s required params still accept ``old_v``'s minimal call.

    ``new_req`` must be ``old_req`` with zero or more parameters *widened* to
    optional — an order-preserving subsequence where every dropped key reappears
    in ``new_opt``. This keeps a required→optional change
    (``@overload def f(x: int)`` → ``def f(x: int = ...)``) covered — every old
    call that supplied ``x`` is still accepted — while still rejecting a *new*
    required parameter (breaks the minimal call) or a reordering of the retained
    required parameters (rebinds positional callers).
    """
    i = 0
    for key in old_req:
        if i < len(new_req) and new_req[i] == key:
            i += 1  # still required in new_v, same position
        elif key in new_opt:
            continue  # widened to optional in new_v — the old call still binds
        else:
            return False  # required param dropped, or new_v added/reordered one
    return i == len(new_req)  # no extra required parameter in new_v


def _overload_covers(new_v: PyFunction, old_v: PyFunction) -> bool:
    """True when new variant *new_v* still accepts every call *old_v* accepted.

    Overload matching is **directional**, not a symmetric identity: adding an
    optional parameter to a variant (or widening a required one to optional) is a
    compatible *widening* (still matches), but *removing* one drops a supported
    call shape (a real removal), so the two must not collapse to the same key.
    ``new_v`` covers ``old_v`` when they share the same protocol (async /
    descriptor), ``new_v``'s required shape is ``old_v``'s with zero or more
    parameters widened to optional (see ``_covers_required_shape``), and
    ``new_v``'s optional-parameter set is a **superset** of ``old_v``'s (so every
    optional call ``old_v`` accepted, ``new_v`` also accepts); and ``new_v`` must
    keep any ``*args`` / ``**kwargs`` collector ``old_v`` had (dropping one
    rejects extra arguments). The return annotation is excluded — a return-only
    change on a covered variant is a RISK, not a removal.
    """
    if new_v.is_async != old_v.is_async or new_v.descriptor != old_v.descriptor:
        return False
    if not _covers_required_shape(
        _required_shape(old_v), _required_shape(new_v), _optional_shape(new_v)
    ):
        return False
    if not (_optional_shape(new_v) >= _optional_shape(old_v)):
        return False
    if _has_kind(old_v, VAR_POSITIONAL) and not _has_kind(new_v, VAR_POSITIONAL):
        return False
    if _has_kind(old_v, VAR_KEYWORD) and not _has_kind(new_v, VAR_KEYWORD):
        return False
    return True


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
        new_variants = _overload_variants(new_fn)
        for variant in _overload_variants(old_fn):
            match = next(
                (nv for nv in new_variants if _overload_covers(nv, variant)), None
            )
            if match is None:
                # No new variant still accepts this call shape. Added variants
                # (that cover nothing old) are compatible and not reported.
                changes.append(
                    make_change(
                        ChangeKind.PYTHON_API_OVERLOAD_REMOVED,
                        symbol=symbol,
                        name=qualified,
                        detail=_render_signature(variant),
                    )
                )
            else:
                # A new variant still accepts this call shape; run the full
                # matched-variant signature diff so within-shape changes the
                # ``covers`` relation intentionally tolerates are still surfaced:
                # an optional parameter inserted before an existing one (a
                # positional-binding shift), a ``*args`` / ``**kwargs`` collector
                # whose element type changed, or a return-type change — each a
                # RISK, not a removed overload. Mirrors the non-overloaded path.
                changes.extend(_diff_signature(variant, match, symbol, qualified))
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

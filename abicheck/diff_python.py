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

"""CPython extension-module ABI diff (G14).

Compares the CPython C-API **import** surface of two versions of a stable-ABI
(``abi3`` / ``Py_LIMITED_API``) extension module — the contract the export
table cannot see. Fires only for modules that are actually ``abi3`` builds; a
version-specific (``cpython-311``) module is *expected* to use private CPython
API and rebuild per interpreter, so applying these checks to it would be a false
positive. The gate is therefore strict on the abi3 tag.

Two findings:

* :data:`ChangeKind.PYTHON_STABLE_ABI_VIOLATION` — an ``abi3`` build gained an
  import outside the Stable ABI (a private ``_Py*`` symbol or a ``PyUnstable_*``
  symbol). Membership in the authoritative stable set decides; no floor needed.
* :data:`ChangeKind.PYTHON_ABI3_DROPPED` — the module was an ``abi3`` build and
  the new build is version-specific, dropping every other interpreter it
  supported.

Interpreter-*floor* conformance (a stable symbol newer than the declared
``Py_LIMITED_API``) is intentionally NOT diffed here — see
:func:`_diff_python_ext` for why — it is checked by ``abicheck scan --abi3``,
where the user supplies the target floor. :func:`audit_stable_abi_imports` is the
shared single-artifact audit that command drives.

Registered via ``@registry.detector("python_ext")`` and skipped automatically
when either snapshot lacks extension metadata.
"""

from __future__ import annotations

from . import stable_abi
from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot
from .python_ext import PythonExtMetadata
from .stable_abi import StableAbiStatus


def audit_stable_abi_imports(
    meta: PythonExtMetadata, abi3_floor: tuple[int, int] | None
) -> list[Change]:
    """Single-artifact stable-ABI audit of a module's CPython imports.

    Classifies every imported CPython symbol against the authoritative Stable-ABI
    set for the target ``abi3_floor`` and returns aggregated
    :data:`ChangeKind.PYTHON_STABLE_ABI_VIOLATION` findings — one row each for
    private/unstable imports, imports newer than the floor, and public ``Py*``
    symbols absent from the vendored set (all outside the Limited API). This is
    the shared engine behind ``abicheck scan --abi3`` (a single-binary audit,
    distinct from the two-snapshot :func:`_diff_python_ext` detector).

    Without a floor only the floor-independent violations (private/unstable and
    unknown-public imports) are caught — stable symbols cannot be judged
    above-floor — so callers should require a floor before certifying a module.

    The artifact's own ``cpXY-abi3`` tag is itself a hard floor: a ``cp39-abi3``
    wheel is installed on 3.9/3.10 regardless of the ``--abi3`` the caller
    passes. So when the tag declares a floor **lower** than the requested one,
    the audit certifies against the tag's floor — otherwise ``--abi3 3.12`` on a
    ``cp39-abi3`` build would wave through a 3.11-only symbol that breaks on the
    3.9 the tag still advertises. Only a genuine abi3 build carries a
    Limited-API floor here; a version-specific module's declared minor is the
    interpreter, not an abi3 promise (it is flagged separately below), so its
    tag floor is not folded in.
    """
    declared = meta.declared_abi3 if not meta.is_version_specific else None
    effective_floor = abi3_floor
    if declared is not None and (abi3_floor is None or declared < abi3_floor):
        effective_floor = declared

    private: list[str] = []
    above_floor: list[str] = []
    unknown: list[str] = []
    for name in meta.cpython_imports:
        status, added = stable_abi.classify(name, effective_floor)
        if status is StableAbiStatus.PRIVATE:
            private.append(name)
        elif status is StableAbiStatus.ABOVE_FLOOR:
            above_floor.append(
                f"{name} (added {stable_abi.format_version(added)})"
                if added is not None
                else name
            )
        elif status is StableAbiStatus.UNKNOWN:
            unknown.append(name)

    module_name = meta.module_name or meta.init_symbol or "<extension>"
    findings: list[Change] = []
    # The artifact's own SOABI tag can pin it to one interpreter regardless of
    # its imports: a version-specific `foo.cpython-311.so` cannot satisfy the
    # requested abi3 floor no matter how stable its symbol names are. Auditing it
    # as abi3 is a contradiction — surface it rather than certify a build the tag
    # itself restricts to one minor.
    if meta.is_version_specific:
        findings.append(
            make_change(
                ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                symbol=f"python:{module_name}",
                name=module_name,
                detail=(
                    f"artifact SOABI tag '{meta.soabi_tag}' is version-specific — "
                    "not an abi3 build; it loads only on that one interpreter, so "
                    "it cannot satisfy a Py_LIMITED_API floor"
                ),
                new_value=[meta.soabi_tag],
            )
        )
    for group in (private, unknown):
        if group:
            findings.append(
                make_change(
                    ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                    symbol=f"python:{module_name}",
                    name=module_name,
                    detail=", ".join(sorted(group)),
                    new_value=sorted(group),
                )
            )
    # State the floor the above-floor group was judged against — especially when
    # it was pulled down to the artifact's declared `cpXY-abi3` tag, so a caller
    # who passed a higher `--abi3` understands why a symbol newer than *their*
    # floor (but older than the tag's) is flagged.
    if above_floor:
        floor_note = (
            f"declared abi3 floor {stable_abi.format_version(effective_floor)} "
            "(from the artifact tag)"
            if effective_floor != abi3_floor and effective_floor is not None
            else f"abi3 floor {stable_abi.format_version(effective_floor)}"
            if effective_floor is not None
            else "abi3 floor"
        )
        findings.append(
            make_change(
                ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                symbol=f"python:{module_name}",
                name=module_name,
                detail=(
                    f"stable symbols newer than the {floor_note}: "
                    + ", ".join(sorted(above_floor))
                ),
                new_value=sorted(above_floor),
            )
        )
    # Windows: an abi3 module must link the version-neutral `python3.dll`; a
    # version-specific `pythonXY.dll` ties it to one interpreter minor, so it
    # fails to load elsewhere no matter how stable its imported symbol *names*
    # are. Flag it even when every symbol is in the stable set.
    versioned = meta.version_specific_python_dlls
    if versioned:
        findings.append(
            make_change(
                ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                symbol=f"python:{module_name}",
                name=module_name,
                detail=(
                    "links version-specific "
                    + ", ".join(versioned)
                    + " (abi3 requires python3.dll)"
                ),
                new_value=versioned,
            )
        )
    return findings


def _is_abi3(meta: PythonExtMetadata) -> bool:
    """True when a module presents as a stable-ABI (``abi3``) build.

    Only the ``abi3`` suffix / ``limited_api`` flag counts. ``declared_abi3`` is
    NOT a signal: for a version-specific (``cpython-311``) module it records the
    interpreter minor, not a Limited-API promise, so keying on it would wrongly
    subject a normal per-version extension to the stable-ABI contract.
    """
    return meta.limited_api or meta.soabi_tag == "abi3"


def _module_symbol(new: PythonExtMetadata, old: PythonExtMetadata) -> str:
    """A stable identifier for the module in findings."""
    for meta in (new, old):
        if meta.module_name:
            return f"python:{meta.module_name}"
        if meta.init_symbol:
            return f"python:{meta.init_symbol}"
    return "python:<extension>"


def _diff_stable_abi_violations(
    old: PythonExtMetadata, new: PythonExtMetadata
) -> list[Change]:
    """Non-stable imports that violate the NEW build's abi3 promise.

    A "non-stable" import is any CPython symbol absent from the authoritative
    Stable-ABI set — an internal ``_Py*``/``PyUnstable_*`` symbol OR a public
    ``Py*`` function that was never added to the Limited API (e.g.
    ``PyUnicode_AsUTF8``). For an ``abi3`` module all of these break the
    cross-interpreter promise.

    When the old build was *also* abi3, only *newly-gained* non-stable imports
    are a change this version introduced. But when the old build was
    version-specific and the new one is retagged to abi3
    (``foo.cpython-311.so`` → ``foo.abi3.so``), the promise is brand new, so
    **every** non-stable import in the new build is now a violation — even one
    carried over unchanged. The baseline is therefore the old imports only if the
    old build already made the abi3 promise, else empty.
    """
    baseline = set(old.cpython_imports) if _is_abi3(old) else set()
    gained = sorted(
        s
        for s in new.cpython_imports
        if s not in baseline and stable_abi.is_nonstable_cpython_import(s)
    )
    module = _module_symbol(new, old)
    name = new.module_name or old.module_name or "<extension>"
    changes: list[Change] = []
    if gained:
        changes.append(
            make_change(
                ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                symbol=module,
                name=name,
                detail=", ".join(gained),
                new_value=gained,
            )
        )
    # When the NEW build carries an explicit declared floor (a `cpXY-abi3` tag),
    # a newly-gained *stable* symbol newer than that floor is also a violation:
    # the artifact advertises a floor it can no longer meet (a `cp39-abi3` build
    # importing `PyType_GetName`, stable since 3.11, fails to load on 3.9/3.10).
    # This is exact — the floor is the declared tag, not a min-of-imports guess —
    # so it complements `_diff_abi3_floor_raised` (which needs the floor to have
    # *risen*). Baselined like the imports: only symbols not already present on
    # an abi3 old build count as this version's change.
    floor = new.declared_abi3
    if floor is not None:
        above_floor: list[str] = []
        for sym in new.cpython_imports:
            if sym in baseline:
                continue
            status, added = stable_abi.classify(sym, floor)
            if status is StableAbiStatus.ABOVE_FLOOR:
                above_floor.append(
                    f"{sym} (added {stable_abi.format_version(added)})"
                    if added is not None
                    else sym
                )
        if above_floor:
            changes.append(
                make_change(
                    ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                    symbol=module,
                    name=name,
                    detail=(
                        "stable symbols newer than the declared abi3 floor "
                        f"{stable_abi.format_version(floor)}: "
                        + ", ".join(sorted(above_floor))
                    ),
                    new_value=sorted(above_floor),
                )
            )
    # Windows: the abi3 build must link the version-neutral `python3.dll`. A
    # newly version-specific `pythonXY.dll` breaks the cross-interpreter promise
    # even if every imported symbol name is stable. Baselined like the imports:
    # only flag DLLs not already present on an abi3 old build.
    dll_baseline = (
        set(old.version_specific_python_dlls) if _is_abi3(old) else set()
    )
    gained_dlls = sorted(
        d for d in new.version_specific_python_dlls if d not in dll_baseline
    )
    if gained_dlls:
        changes.append(
            make_change(
                ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                symbol=module,
                name=name,
                detail=(
                    "links version-specific "
                    + ", ".join(gained_dlls)
                    + " (abi3 requires python3.dll)"
                ),
                new_value=gained_dlls,
            )
        )
    return changes


@registry.detector(
    "python_ext",
    requires_support=lambda o, n: (
        n.python_ext is not None,
        "missing CPython extension metadata",
    ),
)
def _diff_python_ext(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """CPython extension-module compatibility detector (G14).

    Emits :data:`ChangeKind.PYTHON_STABLE_ABI_VIOLATION` when a stable-ABI
    (abi3) module gains a CPython *private* (``_Py*``) import — the
    always-correct, floor-independent signal. A raised *interpreter floor* is
    deliberately NOT diffed here: proving that a supported interpreter was
    dropped needs the module's declared ``Py_LIMITED_API`` floor, which a bare
    ``.abi3.so`` does not carry, so comparing the min-of-imports across versions
    would false-positive (e.g. a ``cp39-abi3`` build adding a 3.5 symbol drops no
    3.9+ user). Floor conformance is checked by ``abicheck scan --abi3 <floor>``,
    where the user supplies the target floor.
    """
    n = new.python_ext
    assert n is not None  # guaranteed by requires_support
    # The old side need not be an extension at all: a module freshly introduced
    # or retagged as abi3 has no (or a non-extension) baseline. Treat a missing
    # old extension as an empty baseline so every private import in the new abi3
    # build is flagged.
    o = old.python_ext if old.python_ext is not None else PythonExtMetadata()

    changes: list[Change] = []
    # GIL ↔ free-threaded (PEP 703) ABI switch is independent of the abi3 promise
    # and applies to any two real extension builds: report it whenever the module
    # gained or lost the free-threaded `t` ABI. Skipped when the old side is not a
    # genuine extension (a freshly introduced module changes nothing).
    if old.python_ext is not None and old.python_ext.is_extension:
        changes.extend(_diff_gil_abi(o, n))

    # The contract is the NEW artifact's: only a stable-ABI (abi3) new build
    # makes the cross-interpreter promise a private import breaks. The old build
    # need not be abi3 — a retag from a version-specific build to abi3 newly
    # subjects its imports to the promise (handled in _diff_stable_abi_violations).
    if not _is_abi3(n):
        # New build is version-specific. If the OLD build was abi3, the module
        # just dropped its Limited-API promise: it used to load on every
        # interpreter at/above its floor, now only on one. That is a deployment
        # regression even though the new build uses private API legitimately.
        if _is_abi3(o):
            changes.extend(_diff_abi3_dropped(o, n))
        return changes

    changes.extend(_diff_stable_abi_violations(o, n))
    changes.extend(_diff_abi3_floor_raised(o, n))
    return changes


def _diff_abi3_floor_raised(
    old: PythonExtMetadata, new: PythonExtMetadata
) -> list[Change]:
    """Both builds are abi3 but the new declared cpXY-abi3 floor is higher.

    Uses the floor read from the explicit ``cpXY-abi3`` tag on *both* sides
    (``declared_abi3``), so it is exact — no min-of-imports inference (the
    false-positive trap that kept interpreter-floor drift out of ``compare``).
    Requires both sides to be abi3 with a declared floor; a bare ``.abi3.so``
    (floor ``None``) carries no tag to compare, so it is skipped.
    """
    if not _is_abi3(old):
        return []
    old_floor, new_floor = old.declared_abi3, new.declared_abi3
    if old_floor is None or new_floor is None or new_floor <= old_floor:
        return []
    module = _module_symbol(new, old)
    name = new.module_name or old.module_name or "<extension>"
    return [
        make_change(
            ChangeKind.PYTHON_ABI3_FLOOR_RAISED,
            symbol=module,
            name=name,
            old=stable_abi.format_version(old_floor),
            new=stable_abi.format_version(new_floor),
        )
    ]


def _diff_gil_abi(old: PythonExtMetadata, new: PythonExtMetadata) -> list[Change]:
    """The module switched between the regular (GIL) and free-threaded ABI."""
    if old.free_threaded == new.free_threaded:
        return []
    module = _module_symbol(new, old)
    name = new.module_name or old.module_name or "<extension>"
    old_tag = "free-threaded" if old.free_threaded else "gil"
    new_tag = "free-threaded" if new.free_threaded else "gil"
    return [
        make_change(
            ChangeKind.PYTHON_GIL_ABI_CHANGED,
            symbol=module,
            name=name,
            old=old_tag,
            new=new_tag,
        )
    ]


def _diff_abi3_dropped(old: PythonExtMetadata, new: PythonExtMetadata) -> list[Change]:
    """The module was abi3 and the new build is version-specific → promise lost."""
    module = _module_symbol(new, old)
    name = new.module_name or old.module_name or "<extension>"
    old_tag = old.soabi_tag or "abi3"
    new_tag = new.soabi_tag or "version-specific"
    return [
        make_change(
            ChangeKind.PYTHON_ABI3_DROPPED,
            symbol=module,
            name=name,
            old=old_tag,
            new=new_tag,
        )
    ]

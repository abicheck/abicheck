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
:func:`_diff_python_ext` for why — it is checked by the ``stable-abi`` command,
where the user supplies the target floor via ``--abi3``.

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
    if not gained:
        return []
    module = _module_symbol(new, old)
    name = new.module_name or old.module_name or "<extension>"
    detail = ", ".join(gained)
    return [
        make_change(
            ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
            symbol=module,
            name=name,
            detail=detail,
            new_value=gained,
        )
    ]


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
    3.9+ user). Floor conformance is checked in the ``stable-abi`` command, where
    the user supplies the target floor via ``--abi3``.
    """
    n = new.python_ext
    assert n is not None  # guaranteed by requires_support
    # The old side need not be an extension at all: a module freshly introduced
    # or retagged as abi3 has no (or a non-extension) baseline. Treat a missing
    # old extension as an empty baseline so every private import in the new abi3
    # build is flagged.
    o = old.python_ext if old.python_ext is not None else PythonExtMetadata()

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
            return _diff_abi3_dropped(o, n)
        return []

    return _diff_stable_abi_violations(o, n)


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

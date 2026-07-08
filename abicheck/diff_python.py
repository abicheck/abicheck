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

One finding:

* :data:`ChangeKind.PYTHON_STABLE_ABI_VIOLATION` — the new build gained an
  import of a CPython *private* symbol (``_Py*``), which is outside the Limited
  API. This is the always-correct signal (no allowlist, no floor needed).

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
    """Private (``_Py*``) imports that violate the NEW build's abi3 promise.

    When the old build was *also* abi3, only *newly-gained* private imports are a
    change this version introduced. But when the old build was version-specific
    and the new one is retagged to abi3 (``foo.cpython-311.so`` → ``foo.abi3.so``),
    the cross-interpreter promise is brand new, so **every** private import in the
    new build is now a violation — even one carried over unchanged. The baseline
    to diff against is therefore the old imports only if the old build already
    made the abi3 promise, else empty.
    """
    baseline = set(old.cpython_imports) if _is_abi3(old) else set()
    gained_private = sorted(
        s
        for s in new.cpython_imports
        if s not in baseline and stable_abi.is_private_symbol(s)
    )
    if not gained_private:
        return []
    module = _module_symbol(new, old)
    name = new.module_name or old.module_name or "<extension>"
    detail = ", ".join(gained_private)
    return [
        make_change(
            ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
            symbol=module,
            name=name,
            detail=detail,
            new_value=gained_private,
        )
    ]


@registry.detector(
    "python_ext",
    requires_support=lambda o, n: (
        o.python_ext is not None and n.python_ext is not None,
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
    o = old.python_ext
    n = new.python_ext
    assert o is not None and n is not None  # guaranteed by requires_support

    # The contract is the NEW artifact's: only a stable-ABI (abi3) new build
    # makes the cross-interpreter promise a private import breaks. The old build
    # need not be abi3 — a retag from a version-specific build to abi3 newly
    # subjects its imports to the promise (handled in _diff_stable_abi_violations).
    # A version-specific new build legitimately uses private CPython API and is
    # rebuilt per interpreter, so it has no such promise.
    if not _is_abi3(n):
        return []

    return _diff_stable_abi_violations(o, n)

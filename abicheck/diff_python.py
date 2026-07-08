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

* :data:`ChangeKind.PYTHON_STABLE_ABI_VIOLATION` — the new build gained an
  import of a CPython *private* symbol (``_Py*``), which is outside the Limited
  API. This is the always-correct signal (no allowlist needed).
* :data:`ChangeKind.PYTHON_ABI_FLOOR_RAISED` — the minimum interpreter version
  implied by the new build's recognised stable imports is higher than the old
  build's, so the module drops interpreters it used to load on.

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
    """Newly-imported CPython *private* (``_Py*``) symbols in an abi3 module."""
    old_imports = set(old.cpython_imports)
    gained_private = sorted(
        s
        for s in new.cpython_imports
        if s not in old_imports and stable_abi.is_private_symbol(s)
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


def _diff_abi_floor(old: PythonExtMetadata, new: PythonExtMetadata) -> list[Change]:
    """A rise in the module's minimum interpreter floor across recognised imports."""
    old_floor = old.min_required_abi3()
    new_floor = new.min_required_abi3()
    if old_floor is None or new_floor is None or new_floor <= old_floor:
        return []
    module = _module_symbol(new, old)
    name = new.module_name or old.module_name or "<extension>"
    # Which recognised stable imports are responsible for the raised floor.
    old_imports = set(old.cpython_imports)
    raisers = sorted(
        s
        for s in new.cpython_imports
        if s not in old_imports
        and (v := stable_abi.added_version(s)) is not None
        and v > old_floor
    )
    return [
        make_change(
            ChangeKind.PYTHON_ABI_FLOOR_RAISED,
            symbol=module,
            name=name,
            old=stable_abi.format_version(old_floor),
            new=stable_abi.format_version(new_floor),
            detail=", ".join(raisers),
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
    """CPython extension-module compatibility detector (G14)."""
    o = old.python_ext
    n = new.python_ext
    assert o is not None and n is not None  # guaranteed by requires_support

    # Only stable-ABI (abi3) modules carry these contracts. A version-specific
    # extension legitimately uses private CPython API and is rebuilt per
    # interpreter, so it has no cross-interpreter import promise to break.
    if not (_is_abi3(n) and _is_abi3(o)):
        return []

    changes: list[Change] = []
    changes.extend(_diff_stable_abi_violations(o, n))
    changes.extend(_diff_abi_floor(o, n))
    return changes

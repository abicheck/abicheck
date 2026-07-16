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

"""NumPy C-API compatibility-envelope ChangeKind registry entries (G26).

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap (same reason ``change_registry_coverage.py``
exists). These entries are spliced into the single ``REGISTRY`` at import
time — declaring a kind here is exactly equivalent to declaring it in
``change_registry.py``. See ``abicheck/numpy_capi.py`` and
``abicheck/diff_numpy_capi.py`` for the evidence extraction and detectors
that emit these kinds, and ``docs/development/plans/g26-numpy-capi-envelope.md``
for the full design.
"""

from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

NUMPY_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "numpy_capi_consumption_added",
        _R,
        impact="The module started consuming the NumPy C-API "
        "(_ARRAY_API/_UFUNC_API, populated by import_array()/"
        "import_ufunc()) — a runtime dependency ordinary symbol-table "
        "diffing cannot see, since the API is consumed through an "
        "indirect function-pointer capsule table, not ordinary dynamic "
        "symbol imports. Verify the wheel/package metadata now "
        "declares a numpy runtime dependency; if it doesn't, users "
        "without numpy installed get an ImportError this diff never "
        "flagged.",
        description_template="Module now consumes the NumPy C-API: {detail}",
    ),
    _E(
        "numpy_capi_consumption_removed",
        _C,
        impact="The module stopped consuming the NumPy C-API. A dependency "
        "reduction; existing consumers with numpy installed are "
        "unaffected.",
        description_template="Module no longer consumes the NumPy C-API",
    ),
    _E(
        "numpy_target_floor_raised",
        _R,
        impact="The module's compiled-in NumPy C-API usage now targets a "
        "newer minimum NumPy release (NPY_TARGET_VERSION, recovered "
        "from the module's own import_array() failure-message string) "
        "than the previous build. Runtimes with the old, lower NumPy "
        "that worked before can now fail to import this module.",
        description_template="NumPy C-API target floor raised: {old} → {new}",
    ),
    _E(
        "numpy_metadata_understates_required_version",
        _R,
        impact="The wheel/package's declared numpy requirement is looser "
        "than (or absent relative to) the binary's own NumPy C-API "
        "target version recovered from binary evidence. A user who "
        "installs the oldest numpy the metadata nominally allows gets "
        "a NumPy C-API version mismatch at import time despite pip "
        "reporting a satisfied dependency.",
        description_template="Declared numpy requirement ({old}) understates the binary's own NumPy C-API target ({new})",
    ),
    _E(
        "numpy_abi_major_incompatible",
        _B,
        impact="The binary's NumPy C-API target crosses the NumPy 1.x/2.x "
        "ABI boundary (NumPy 2.0 changed the ABI: a module built "
        "against it does not load against a NumPy 1.x runtime), but "
        "the declared numpy requirement still allows a NumPy 1.x "
        "runtime. This is not just a stale metadata claim — installing "
        "the declared floor produces a hard import crash, not merely a "
        "missing API surface.",
        description_template="NumPy C-API target ({new}) requires NumPy >= 2.0, but declared requirement ({old}) still allows NumPy 1.x",
    ),
]

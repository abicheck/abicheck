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

"""NumPy C-API compatibility-envelope evidence (G26).

Extracts, from a compiled extension's binary evidence alone (no header or
source needed — same ELF_ONLY evidence tier as G10's platform-baseline
check), whether the module consumes the NumPy C-API (the ``_ARRAY_API``/
``_UFUNC_API`` capsule tables, populated by ``import_array()``/
``import_ufunc()``) and, when it does, the NumPy release its compiled-in
usage targets.

Ordinary symbol-table diffing sees nothing here: the NumPy C-API is
consumed through an indirect function-pointer table populated at runtime
via ``PyObject_GetAttrString(numpy, "_ARRAY_API")``, not ordinary dynamic
symbol imports — exactly the gap this module closes.

Binary-evidence design, verified empirically against a real compiled NumPy
2.4 extension (see the G26 plan and PR discussion for the full derivation):
NumPy's generated ``_import_array()``/``_import_umath()`` shims embed
literal, stable C string constants that a plain byte-substring scan
recovers reliably, independent of optimisation level and surviving
``strip`` (string literals used at runtime live in ``.rodata``/``.rdata``/
``__TEXT,__cstring``, sections symbol-stripping never touches):

* ``"_ARRAY_API is NULL pointer"`` / ``"_ARRAY_API is not PyCapsule
  object"`` — compiled in only when ``import_array()`` was called; presence
  is a reliable "consumes the array C-API" signal.
* The ``_UFUNC_API`` counterparts for ``import_ufunc()``.
* ``"...compiled against NumPy C-API version 0x<hex> (NumPy X.Y)..."`` — the
  exact ``NPY_TARGET_VERSION`` (or NumPy's own default when unset) as a
  human-readable release string, via NumPy's ``NPY_FEATURE_VERSION_STRING``
  macro, string-concatenated directly into the ``_import_array()``/
  ``_import_umath()`` shim's error message at compile time. This is the
  *minimum* NumPy runtime the module's C-API usage requires — the
  practically important compatibility-envelope question. The scan anchors on
  the full ``"compiled against NumPy C-API version 0x... (NumPy X.Y)"``
  phrase rather than a bare ``"(NumPy X.Y)"``, so an unrelated parenthesized
  version string elsewhere in ``.rodata`` (a docstring, a log message) can't
  be mistaken for the shim's own floor (Codex review).

What is deliberately NOT extracted: the raw ``NPY_ABI_VERSION``/
``NPY_API_VERSION`` hex constants. Those are passed as ``PyErr_Format``
varargs via a compiler-emitted immediate load, not a string literal, so
recovering them needs disassembly — a new heavy dependency this project's
"no heavy deps without strong justification" policy rules out (the same
reasoning that keeps G4's libclang frontend out of scope). The target-
version string above already answers the practically important "what's the
minimum NumPy this extension needs" question without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Hard cap on how much of a binary is read into memory to scan. Real NumPy
#: C-API extensions are KB-to-low-single-digit-MB; this only bounds the
#: worst case for a caller pointing us at something unexpectedly huge.
_MAX_SCAN_SIZE = 512 * 1024 * 1024

_ARRAY_API_MARKERS = (
    b"_ARRAY_API is NULL pointer",
    b"_ARRAY_API is not PyCapsule object",
)
_UFUNC_API_MARKERS = (
    b"_UFUNC_API is NULL pointer",
    b"_UFUNC_API is not PyCapsule object",
)
#: NumPy's NPY_FEATURE_VERSION_STRING, string-concatenated verbatim into the
#: import_array()/import_umath() shim's "module was compiled against NumPy
#: C-API version 0x%x (NumPy X.Y) but the running NumPy has ..." message.
#: Anchored to the full "compiled against ... 0x<hex> (NumPy X.Y)" phrase
#: (not a bare "(NumPy X.Y)") so an unrelated parenthesized version string
#: elsewhere in .rodata can't be mistaken for the shim's own floor.
_TARGET_VERSION_RE = re.compile(
    rb"compiled against NumPy C-API version 0x[0-9a-fA-F]+ \(NumPy (\d+\.\d+)\)"
)


@dataclass
class NumPyCapiSurface:
    """One library's NumPy C-API consumption, from binary evidence alone."""

    consumes_array_api: bool = False
    consumes_ufunc_api: bool = False
    #: The minimum NumPy release this module's compiled-in C-API usage
    #: requires (NPY_TARGET_VERSION, or NumPy's own default when the build
    #: didn't set one), e.g. ``"1.23"``. ``None`` when the target-version
    #: string wasn't recoverable (a degraded-coverage case, not "no floor").
    capi_target_version: str | None = None


def extract_numpy_capi_surface(binary_path: Path) -> NumPyCapiSurface | None:
    """Scan *binary_path* for NumPy C-API consumption evidence (G26).

    Returns ``None`` only when the binary could not be scanned at all
    (missing, empty, unreadable, or over ``_MAX_SCAN_SIZE``) — "no evidence
    captured", the same state a snapshot predating this field's introduction
    deserializes to. An ordinary, successfully-scanned non-NumPy library
    returns a real :class:`NumPyCapiSurface` with both flags ``False``
    ("confirmed not consuming"), not ``None`` — collapsing "confirmed absent"
    and "never scanned" into the same ``None`` would make
    :func:`abicheck.diff_numpy_capi.diff_numpy_capi_surfaces` unable to tell
    a genuine new-consumption transition from a comparison against a legacy
    snapshot (Codex review).

    Best-effort and format-agnostic: reads up to ``_MAX_SCAN_SIZE`` raw
    bytes and scans directly for the literal marker strings, rather than
    parsing ELF/PE/Mach-O section structure — the strings above are
    themselves the evidence, so nothing is gained by attributing them to a
    specific section, and this way the same scan works identically across
    all three binary formats.
    """
    try:
        if not binary_path.is_file():
            return None
        with binary_path.open("rb") as f:
            data = f.read(_MAX_SCAN_SIZE + 1)
    except OSError:
        return None
    if not data or len(data) > _MAX_SCAN_SIZE:
        return None

    consumes_array = any(marker in data for marker in _ARRAY_API_MARKERS)
    consumes_ufunc = any(marker in data for marker in _UFUNC_API_MARKERS)

    target_version = None
    if consumes_array or consumes_ufunc:
        match = _TARGET_VERSION_RE.search(data)
        target_version = match.group(1).decode("ascii") if match else None

    return NumPyCapiSurface(
        consumes_array_api=consumes_array,
        consumes_ufunc_api=consumes_ufunc,
        capi_target_version=target_version,
    )

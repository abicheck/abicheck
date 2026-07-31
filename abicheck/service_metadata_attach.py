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

"""Opportunistic per-ecosystem metadata attachment for a dumped snapshot.

Four best-effort enrichment steps ``service.run_dump`` applies after the
core dump: SYCL plugin metadata, Python extension-module metadata, the NumPy
C-API surface, and the recovered Python API surface. Each detects its own
ecosystem cheaply, attaches nothing when it does not apply, and swallows its
own failures -- an enrichment step must never fail a dump.

Split out of ``service.py`` (which sits at the AI-readiness file-size hard
cap -- see ``AGENTS.md``) as a leaf module, the same pattern
``service_render``/``service_scan`` already follow. ``service.py``
re-exports all four names, so ``from abicheck.service import
_try_attach_python_api_surface`` keeps working.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .model import AbiSnapshot

# Deliberately the *parent* module's logger name, not this module's:
# these functions logged under "abicheck.service" before the split and
# callers (and tests) capture that name.
_logger = logging.getLogger("abicheck.service")


def _try_attach_sycl_metadata(snap: AbiSnapshot, lib_path: Path) -> None:
    """Auto-detect SYCL distribution and attach plugin metadata.

    Runs only when ``lib_path`` lives in a directory that looks like a
    SYCL runtime distribution (contains ``libsycl.so`` or ``libacpp-rt.so``).
    Cost for non-SYCL libraries: one ``_detect_sycl_implementation()`` call
    which is a few ``Path.exists()`` checks — effectively zero overhead.
    """
    from .sycl_metadata import parse_sycl_metadata

    try:
        # `resolve()` is inside the handler, not above it: it can raise, and
        # a path this function cannot resolve must skip metadata extraction
        # rather than abort the whole dump (CodeRabbit review). Measured
        # across interpreters, since the triggers differ: a relative path
        # whose cwd has been deleted raises `FileNotFoundError` on every
        # supported version, while a *symlink loop* raises `RuntimeError`
        # only on 3.10-3.12 -- 3.13 switched to non-strict `realpath` and
        # returns the looping path unchanged. A merely missing path resolves
        # fine everywhere.
        sycl = parse_sycl_metadata(lib_path.resolve().parent)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("SYCL metadata extraction skipped: %s", exc)
        return
    if sycl is not None:
        snap.sycl = sycl
        _logger.info(
            "SYCL metadata attached: implementation=%s, %d plugin(s)",
            sycl.implementation,
            len(sycl.plugins),
        )


def _try_attach_python_ext_metadata(snap: AbiSnapshot) -> None:
    """Recognise a CPython extension module and attach its metadata (G14).

    Cheap and side-effect-free: inspects the snapshot's already-parsed export
    and import tables (plus the filename SOABI tag) for a ``PyInit_*`` export or
    ``Py*`` imports. A plain C/C++ library has neither, so ``python_ext`` stays
    ``None`` and nothing downstream changes.
    """
    from .python_ext import detect_python_extension

    try:
        python_ext = detect_python_extension(snap)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Python extension detection skipped: %s", exc)
        return
    if python_ext is not None:
        snap.python_ext = python_ext
        _logger.info(
            "CPython extension detected: module=%s, abi3=%s, %d CPython import(s)",
            python_ext.module_name,
            python_ext.limited_api,
            len(python_ext.cpython_imports),
        )


def _try_attach_numpy_capi_surface(snap: AbiSnapshot, lib_path: Path) -> None:
    """Scan for NumPy C-API consumption evidence and attach it (G26).

    Cheap: a bounded read of the binary plus a handful of substring/regex
    scans. A successfully-scanned library that doesn't consume the NumPy
    C-API gets a real ``NumPyCapiSurface`` with both flags ``False``
    ("confirmed not consuming"); ``numpy_capi`` stays ``None`` only when the
    binary could not be scanned at all. Keeping those two apart is
    deliberate — see :func:`~abicheck.numpy_capi.extract_numpy_capi_surface`,
    which owns the distinction (CodeRabbit review caught this docstring,
    carried over verbatim from ``service.py``, claiming the opposite).
    """
    from .numpy_capi import extract_numpy_capi_surface
    try:
        numpy_capi = extract_numpy_capi_surface(lib_path)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("NumPy C-API surface extraction skipped: %s", exc)
        return
    if numpy_capi is not None:
        snap.numpy_capi = numpy_capi
        if numpy_capi.consumes_array_api or numpy_capi.consumes_ufunc_api:
            _logger.info(
                "NumPy C-API consumption detected: array_api=%s, ufunc_api=%s, target=%s",
                numpy_capi.consumes_array_api,
                numpy_capi.consumes_ufunc_api,
                numpy_capi.capi_target_version,
            )


def _try_attach_python_api_surface(snap: AbiSnapshot) -> None:
    """Recover an extension module's Python-visible API surface (G23).

    Looks for a ``.pyi`` type stub alongside the snapshot's ``source_path`` and,
    if found, statically parses the top-level functions/classes/methods and
    their signatures into ``python_api``. Never imports or executes the module.
    A no-op (leaves ``python_api`` as ``None``) when no stub is present — the
    common case for a plain C/C++ library or a stubless extension.
    """
    from .python_api import detect_python_api
    try:
        python_api = detect_python_api(snap)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Python API surface recovery skipped: %s", exc)
        return
    if python_api is not None:
        snap.python_api = python_api
        _logger.info(
            "Python API surface recovered: module=%s, %d function(s), %d class(es)",
            python_api.module_name,
            len(python_api.functions),
            len(python_api.classes),
        )

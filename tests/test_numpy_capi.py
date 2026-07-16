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

"""Tests for NumPy C-API compatibility-envelope binary evidence (G26).

The marker strings scanned for here are NumPy's own generated
``_import_array()``/``_import_umath()`` shim literals — verified empirically
against a real compiled NumPy 2.4 extension during development (see the PR
discussion for the derivation). These tests use synthetic byte fixtures
reproducing those literals so they run in the default fast lane without
needing a real numpy install/compiler.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.model import AbiSnapshot
from abicheck.numpy_capi import NumPyCapiSurface, extract_numpy_capi_surface
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

# A representative slice of what NumPy's generated import_array() shim
# actually compiles into .rodata (see abicheck/numpy_capi.py's module
# docstring for the full derivation).
_ARRAY_API_BLOB = (
    b"_ARRAY_API is NULL pointer\x00"
    b"_ARRAY_API is not PyCapsule object\x00"
    b"module was compiled against NumPy C-API version 0x10 "
    b"(NumPy 1.23) but the running NumPy has C-API version 0x15.\x00"
)
_UFUNC_API_BLOB = (
    b"_UFUNC_API is NULL pointer\x00_UFUNC_API is not PyCapsule object\x00"
)


class TestExtractNumPyCapiSurface:
    def test_no_numpy_signature_returns_confirmed_absent_surface(
        self, tmp_path: Path
    ) -> None:
        # A successfully-scanned binary with no NumPy markers is "confirmed
        # not consuming" -- a real NumPyCapiSurface(False, False, None), NOT
        # None. None is reserved for "couldn't scan at all" (missing/empty/
        # oversized/unreadable) so it stays distinguishable from a legacy
        # snapshot that predates this field (Codex review).
        binary = tmp_path / "plain.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 100 + b"some ordinary string data")
        surf = extract_numpy_capi_surface(binary)
        assert surf == NumPyCapiSurface(
            consumes_array_api=False, consumes_ufunc_api=False, capi_target_version=None
        )

    def test_nonexistent_file_returns_none(self, tmp_path: Path) -> None:
        assert extract_numpy_capi_surface(tmp_path / "missing.so") is None

    def test_array_api_consumption_and_target_version_detected(
        self, tmp_path: Path
    ) -> None:
        binary = tmp_path / "mod.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 50 + _ARRAY_API_BLOB)
        surf = extract_numpy_capi_surface(binary)
        assert surf is not None
        assert surf.consumes_array_api is True
        assert surf.consumes_ufunc_api is False
        assert surf.capi_target_version == "1.23"

    def test_ufunc_api_consumption_detected_without_target_string(
        self, tmp_path: Path
    ) -> None:
        # A module that only calls import_ufunc() (no import_array()) has the
        # _UFUNC_API markers but not the array-API's "(NumPy X.Y)" string.
        binary = tmp_path / "mod.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 50 + _UFUNC_API_BLOB)
        surf = extract_numpy_capi_surface(binary)
        assert surf is not None
        assert surf.consumes_array_api is False
        assert surf.consumes_ufunc_api is True
        assert surf.capi_target_version is None

    def test_both_apis_consumed(self, tmp_path: Path) -> None:
        binary = tmp_path / "mod.so"
        binary.write_bytes(
            b"\x7fELF" + b"\x00" * 50 + _ARRAY_API_BLOB + _UFUNC_API_BLOB
        )
        surf = extract_numpy_capi_surface(binary)
        assert surf is not None
        assert surf.consumes_array_api is True
        assert surf.consumes_ufunc_api is True
        assert surf.capi_target_version == "1.23"

    def test_decoy_parenthesized_version_string_is_not_matched(
        self, tmp_path: Path
    ) -> None:
        # An unrelated "(NumPy X.Y)" string elsewhere in .rodata (e.g. a
        # docstring or log message) must not be mistaken for the real shim
        # message's floor -- the scan is anchored to the full "compiled
        # against NumPy C-API version 0x... (NumPy X.Y)" phrase, not a bare
        # "(NumPy X.Y)" (Codex review).
        binary = tmp_path / "mod.so"
        binary.write_bytes(
            b"\x7fELF"
            + b"\x00" * 50
            + b"some unrelated docstring mentioning (NumPy 1.19) in passing\x00"
            + _ARRAY_API_BLOB
        )
        surf = extract_numpy_capi_surface(binary)
        assert surf is not None
        assert surf.capi_target_version == "1.23"

    def test_survives_symbol_stripping(self, tmp_path: Path) -> None:
        # Symbol-table stripping never touches .rodata string literals used
        # at runtime — a stripped binary carries these strings unchanged.
        # Simulated here by simply not adding any symbol-table-shaped bytes;
        # the extractor never looks at symbol tables at all.
        binary = tmp_path / "stripped.so"
        binary.write_bytes(_ARRAY_API_BLOB)
        surf = extract_numpy_capi_surface(binary)
        assert surf is not None
        assert surf.capi_target_version == "1.23"

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        binary = tmp_path / "empty.so"
        binary.write_bytes(b"")
        assert extract_numpy_capi_surface(binary) is None

    def test_oversized_file_degrades_to_none(self, tmp_path: Path, monkeypatch) -> None:
        import abicheck.numpy_capi as numpy_capi_mod

        monkeypatch.setattr(numpy_capi_mod, "_MAX_SCAN_SIZE", 8)
        binary = tmp_path / "big.so"
        binary.write_bytes(_ARRAY_API_BLOB)  # far bigger than the 8-byte cap
        assert extract_numpy_capi_surface(binary) is None


class TestNumPyCapiSurfaceSerializationRoundTrip:
    def test_round_trips_through_dict(self) -> None:
        snap = AbiSnapshot(
            library="mod.so",
            version="1.0",
            numpy_capi=NumPyCapiSurface(
                consumes_array_api=True,
                consumes_ufunc_api=True,
                capi_target_version="1.25",
            ),
        )
        d = snapshot_to_dict(snap)
        assert d["numpy_capi"] == {
            "consumes_array_api": True,
            "consumes_ufunc_api": True,
            "capi_target_version": "1.25",
        }
        restored = snapshot_from_dict(d)
        assert restored.numpy_capi == snap.numpy_capi

    def test_none_round_trips_to_none(self) -> None:
        snap = AbiSnapshot(library="mod.so", version="1.0")
        d = snapshot_to_dict(snap)
        assert d.get("numpy_capi") is None
        restored = snapshot_from_dict(d)
        assert restored.numpy_capi is None

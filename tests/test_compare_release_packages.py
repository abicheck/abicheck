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

"""Unit tests for ``_extract_if_package`` (release operand preparation).

Split out of ``test_compare_release.py``, which is at its recorded
``architecture/debt.yaml`` no-growth baseline: package extraction is its own
subject -- how a release operand is *prepared* (directory vs. archive, debug
and devel side packages, extractor detection), not how two prepared operands
compare -- so it is a cohesive class to own separately rather than a slice
trimmed to fit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from abicheck.cli_compare_release import _extract_if_package
from abicheck.package import ExtractResult


def _make_mock_extractor(
    lib_dir: Path, debug_dir: Path | None = None, header_dir: Path | None = None
) -> MagicMock:
    """Return a mock PackageExtractor whose extract() returns the given paths."""
    result = ExtractResult(lib_dir=lib_dir, debug_dir=debug_dir, header_dir=header_dir)
    extractor = MagicMock()
    extractor.extract.return_value = result
    return extractor


class TestExtractIfPackage:
    """Unit tests for _extract_if_package covering the directory-input bug fix."""

    def _make_temp_dir(self, tmp_path: Path) -> Path:
        """Simple make_temp_dir stub that returns a subdirectory."""
        d = tmp_path / f"tmp_{id(self)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_directory_input_no_side_pkgs_returns_path_unchanged(
        self, tmp_path: Path
    ) -> None:
        """Plain directory with no side packages: lib_dir == input, debug/header None."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()

        lib_out, debug_out, header_out, _symbols_out, _whole = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=None,
            devel_pkg=None,
            make_temp_dir=lambda p: tmp_path / p,
            is_package=lambda _: False,
            detect_extractor=lambda _: None,
        )

        assert lib_out == lib_dir
        assert debug_out is None
        assert header_out is None

    def test_directory_input_with_debug_pkg_yields_debug_dir(
        self, tmp_path: Path
    ) -> None:
        """Directory input + standalone debug package: debug_dir must be non-None."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()
        extracted_debug = tmp_path / "extracted_debug"
        extracted_debug.mkdir()

        counter = [0]

        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        dbg_extractor = _make_mock_extractor(
            lib_dir=extracted_debug, debug_dir=extracted_debug
        )

        lib_out, debug_out, header_out, _symbols_out, _whole = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=dbg_pkg,
            devel_pkg=None,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,  # dir is not a package; debug pkg is
            detect_extractor=lambda p: dbg_extractor if p == dbg_pkg else None,
        )

        assert lib_out == lib_dir
        assert debug_out == extracted_debug  # debug_dir from ExtractResult
        assert header_out is None

    def test_directory_input_with_devel_pkg_yields_header_dir(
        self, tmp_path: Path
    ) -> None:
        """Directory input + standalone devel package: header_dir must be non-None."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dev_pkg = tmp_path / "devel.rpm"
        dev_pkg.touch()
        extracted_headers = tmp_path / "extracted_headers"
        extracted_headers.mkdir()

        counter = [0]

        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        dev_extractor = _make_mock_extractor(
            lib_dir=extracted_headers, header_dir=extracted_headers
        )

        lib_out, debug_out, header_out, _symbols_out, _whole = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=None,
            devel_pkg=dev_pkg,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,
            detect_extractor=lambda p: dev_extractor if p == dev_pkg else None,
        )

        assert lib_out == lib_dir
        assert debug_out is None
        assert header_out == extracted_headers  # header_dir from ExtractResult

    def test_directory_input_with_both_side_pkgs(self, tmp_path: Path) -> None:
        """Directory input + both debug and devel packages: both are returned."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()
        dev_pkg = tmp_path / "devel.rpm"
        dev_pkg.touch()
        extracted_debug = tmp_path / "dbg"
        extracted_debug.mkdir()
        extracted_headers = tmp_path / "hdr"
        extracted_headers.mkdir()

        counter = [0]

        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        dbg_extractor = _make_mock_extractor(
            lib_dir=extracted_debug, debug_dir=extracted_debug
        )
        dev_extractor = _make_mock_extractor(
            lib_dir=extracted_headers, header_dir=extracted_headers
        )

        def _detect(p: Path) -> MagicMock | None:
            if p == dbg_pkg:
                return dbg_extractor
            if p == dev_pkg:
                return dev_extractor
            return None

        lib_out, debug_out, header_out, _symbols_out, _whole = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=dbg_pkg,
            devel_pkg=dev_pkg,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,
            detect_extractor=_detect,
        )

        assert lib_out == lib_dir
        assert debug_out == extracted_debug
        assert header_out == extracted_headers

    def test_debug_pkg_fallback_to_lib_dir_when_no_debug_dir(
        self, tmp_path: Path
    ) -> None:
        """When ExtractResult.debug_dir is None, fall back to lib_dir."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()
        extracted = tmp_path / "extracted"
        extracted.mkdir()

        counter = [0]

        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        # debug_dir=None in ExtractResult: fallback must use lib_dir
        dbg_extractor = _make_mock_extractor(lib_dir=extracted, debug_dir=None)

        lib_out, debug_out, header_out, _symbols_out, _whole = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=dbg_pkg,
            devel_pkg=None,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,
            detect_extractor=lambda p: dbg_extractor if p == dbg_pkg else None,
        )

        assert lib_out == lib_dir
        assert debug_out == extracted  # fallback to lib_dir

    def test_package_input_uses_result_debug_dir_not_lib_dir(
        self, tmp_path: Path
    ) -> None:
        """When input is a package, side-pkg debug_dir uses .debug_dir, not .lib_dir."""
        pkg = tmp_path / "main.rpm"
        pkg.touch()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()

        main_lib = tmp_path / "main_lib"
        main_lib.mkdir()
        extracted_debug = tmp_path / "real_debug"
        extracted_debug.mkdir()
        extracted_lib_in_dbg = tmp_path / "dbg_lib"
        extracted_lib_in_dbg.mkdir()

        counter = [0]

        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        main_extractor = _make_mock_extractor(lib_dir=main_lib)
        # debug_dir differs from lib_dir — must pick debug_dir
        dbg_extractor = _make_mock_extractor(
            lib_dir=extracted_lib_in_dbg, debug_dir=extracted_debug
        )

        def _detect(p: Path) -> MagicMock | None:
            if p == pkg:
                return main_extractor
            if p == dbg_pkg:
                return dbg_extractor
            return None

        lib_out, debug_out, header_out, _symbols_out, _whole = _extract_if_package(
            input_path=pkg,
            debug_pkg=dbg_pkg,
            devel_pkg=None,
            make_temp_dir=_make_temp,
            is_package=lambda _: True,
            detect_extractor=_detect,
        )

        assert lib_out == main_lib
        assert debug_out == extracted_debug  # .debug_dir preferred over .lib_dir
        assert header_out is None

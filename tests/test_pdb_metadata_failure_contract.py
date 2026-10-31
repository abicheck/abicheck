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

"""``parse_pdb_debug_info``'s failure contract (ADR-062 D15): a PDB with no
TPI stream, or one whose extraction phase crashes, raises ``PdbParseError``
instead of returning empty or partial metadata. Moved from
``test_platform_coverage_gaps.py`` when the contract changed."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from abicheck.pdb_metadata import PdbParseError, parse_pdb_debug_info


class TestPdbMetadataNoTpi:
    """Cover lines 87-88: pdb.types is None."""

    def test_no_tpi_stream(self, tmp_path):
        pdb_path = tmp_path / "empty.pdb"
        pdb_path.write_bytes(b"fake")

        mock_pdb = MagicMock()
        mock_pdb.types = None
        mock_pdb.dbi = None

        # A PDB with no TPI stream is malformed: failed, never empty metadata.
        with (
            patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb),
            pytest.raises(PdbParseError, match="no TPI stream"),
        ):
            parse_pdb_debug_info(pdb_path)


class TestPdbMetadataPhaseExceptions:
    """A crashed extraction phase fails the whole PDB (``PdbParseError``) rather
    than returning metadata with that phase's facts silently missing."""

    def _make_mock_pdb(self):
        mock_types = MagicMock()
        mock_types.parse_failures = []  # every TPI record parsed
        mock_pdb = MagicMock()
        mock_pdb.types = mock_types
        mock_pdb.dbi = None
        return mock_pdb

    def test_struct_extraction_fails(self, tmp_path):
        """Lines 95-96: _extract_struct_layouts raises."""
        pdb_path = tmp_path / "test.pdb"
        pdb_path.write_bytes(b"fake")
        mock_pdb = self._make_mock_pdb()

        with (
            patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb),
            patch(
                "abicheck.pdb_metadata._extract_struct_layouts",
                side_effect=RuntimeError("boom"),
            ),
            patch("abicheck.pdb_metadata._extract_enums"),
            patch("abicheck.pdb_metadata._extract_toolchain_info"),
        ):
            with pytest.raises(PdbParseError, match="extraction failed: boom") as err:
                parse_pdb_debug_info(pdb_path)
        assert isinstance(err.value.__cause__, RuntimeError)

    def test_enum_extraction_fails(self, tmp_path):
        """Lines 100-101: _extract_enums raises."""
        pdb_path = tmp_path / "test.pdb"
        pdb_path.write_bytes(b"fake")
        mock_pdb = self._make_mock_pdb()

        with (
            patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb),
            patch("abicheck.pdb_metadata._extract_struct_layouts"),
            patch(
                "abicheck.pdb_metadata._extract_enums", side_effect=RuntimeError("boom")
            ),
            patch("abicheck.pdb_metadata._extract_toolchain_info"),
        ):
            with pytest.raises(PdbParseError, match="extraction failed: boom") as err:
                parse_pdb_debug_info(pdb_path)
        assert isinstance(err.value.__cause__, RuntimeError)

    def test_toolchain_info_extraction_fails(self, tmp_path):
        """Lines 110-111: _extract_toolchain_info raises."""
        pdb_path = tmp_path / "test.pdb"
        pdb_path.write_bytes(b"fake")
        mock_pdb = self._make_mock_pdb()

        with (
            patch("abicheck.pdb_metadata.parse_pdb", return_value=mock_pdb),
            patch("abicheck.pdb_metadata._extract_struct_layouts"),
            patch("abicheck.pdb_metadata._extract_enums"),
            patch(
                "abicheck.pdb_metadata._extract_toolchain_info",
                side_effect=RuntimeError("boom"),
            ),
        ):
            with pytest.raises(PdbParseError, match="extraction failed: boom") as err:
                parse_pdb_debug_info(pdb_path)
        assert isinstance(err.value.__cause__, RuntimeError)

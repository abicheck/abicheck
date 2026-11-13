# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Header fields a mocked ``pefile.PE`` needs to read as a whole image.

``parse_pe_metadata`` checks that the headers, sections and export
directory a ``pefile.PE`` describes are held by the file
(``pe_metadata._note_truncated_image``). A bare ``MagicMock`` answers those
fields with more mocks, so a test standing in for pefile states them here.

A non-``test_`` helper: import it, do not copy it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def structurally_whole(mock_pe: MagicMock, *, named_exports: int = 0) -> MagicMock:
    """Give *mock_pe* the header fields of an image its file holds entirely,
    so the truncation checks record nothing; returns *mock_pe*."""
    mock_pe.__data__ = bytes(0x1000)
    mock_pe.DOS_HEADER.e_lfanew = 0x80
    mock_pe.FILE_HEADER.SizeOfOptionalHeader = 0xF0
    mock_pe.FILE_HEADER.NumberOfSections = 0
    mock_pe.sections = []
    mock_pe.OPTIONAL_HEADER.Magic = 0x20B
    va = 0x2000 if named_exports else 0
    mock_pe.OPTIONAL_HEADER.DATA_DIRECTORY = [
        SimpleNamespace(VirtualAddress=va, Size=0x40)
    ]
    mock_pe.get_offset_from_rva = MagicMock(return_value=0x400)
    if named_exports:
        mock_pe.DIRECTORY_ENTRY_EXPORT.struct = SimpleNamespace(
            AddressOfFunctions=0x2040,
            NumberOfFunctions=named_exports,
            AddressOfNames=0x2060,
            NumberOfNames=named_exports,
            AddressOfNameOrdinals=0x2080,
        )
    return mock_pe

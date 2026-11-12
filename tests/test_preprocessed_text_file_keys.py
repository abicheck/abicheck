"""``PreprocessedText`` finds a file however its path is spelled, on any host.

``file_key`` is ``os.path.normpath``, so on Windows it turns ``/x/a.h`` into
``\\x\\a.h``. The table used to key only its *lookups*: a table built from, or
read back with, POSIX spellings missed every file on Windows, and a missed
file reads as one whose code spells no alignment specifier, so castxml
declared a natural alignment ``PRESENT`` where a specifier could apply
(``PARTIAL``). Each case here runs under both normalisers, with
``ntpath.normpath`` standing in for a Windows host.
"""

from __future__ import annotations

import ntpath
import posixpath
from xml.etree.ElementTree import Element

import pytest

from abicheck.extract.headers.castxml import preprocessed_text as pt

NORMALISERS = {"posix": posixpath.normpath, "windows": ntpath.normpath}


@pytest.fixture(params=sorted(NORMALISERS))
def host(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(pt, "file_key", NORMALISERS[request.param])
    return str(request.param)


# Spellings of the same file each normaliser treats as equal.
_SAME = {
    "posix": ["/x/inc/a.h", "/x/inc/./a.h", "/x/src/../inc/a.h", "/x//inc/a.h"],
    "windows": ["/x/inc/a.h", r"\x\inc\a.h", "/x/inc/./a.h", r"\x\src\..\inc\a.h"],
}


@pytest.mark.parametrize("stored", range(4))
def test_a_recorded_file_is_found_in_every_equivalent_spelling(
    host: str, stored: int
) -> None:
    spellings = _SAME[host]
    table = pt.PreprocessedText(
        {spellings[stored]: {3: "int x;"}}, "", frozenset({spellings[stored]})
    )
    for name in spellings:
        assert table.spells_alignment(name), (host, spellings[stored], name)
        assert table.source_lines(name) == ["", "", "int x;"], name
    # Another file is still not recorded: the fix widens nothing.
    assert not table.spells_alignment("/x/inc/b.h")
    assert table.source_lines("/x/inc/b.h") is None


def test_the_document_round_trip_keeps_finding_the_file(host: str) -> None:
    root = Element("CastXML")
    pt.attach_preprocessed_text(
        root,
        pt.PreprocessedText(
            {"/x/a.h": {1: "_Alignas(8) int v;"}}, "", frozenset({"/x/a.h"})
        ),
    )
    back = pt.read_preprocessed_text(root)
    assert back is not None
    assert back.spells_alignment("/x/a.h")
    other_spelling = {"posix": "/x/./a.h", "windows": r"\x\a.h"}[host]
    assert back.spells_alignment(other_spelling)
    assert not back.spells_alignment("/x/b.h")


def test_an_unrecorded_table_still_assumes_any_file_may_spell_one(host: str) -> None:
    table = pt.PreprocessedText({}, "", None)
    assert table.alignment_files is None
    assert table.spells_alignment("/x/anything.h")

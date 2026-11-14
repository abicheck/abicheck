"""CastXML-derived Itanium data size (dsize) never contradicts the compilers.

The oracle is the compilers' own placement of a derived class's member
(``tests/_data_size_matrix.py``). ``data_size.data_size_bits`` may answer
``None`` (unknown), but a value must equal every compiler's; a record the
compilers disagree on must be ``None``; and the shapes the derivation is
built for must not stay unknown.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.data_size import data_size_bits
from tests._data_size_matrix import CASES, COMPILERS, compiler_dsizes, header_text

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("std", ["c++17", "c++20"])
def test_data_size_agrees_with_compilers(tmp_path: Path, std: str) -> None:
    for tool in (*COMPILERS, "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    header = tmp_path / "records.hpp"
    header.write_text(header_text())
    xml = tmp_path / "records.xml"
    subprocess.run(
        ["castxml", "--castxml-output=1", "--castxml-cc-gnu", "g++", f"-std={std}",
         "-o", str(xml), str(header)],
        check=True,
    )  # fmt: skip
    ctx = CastxmlParserContext(parse(xml).getroot(), set(), set())
    ctx.build_id_map()
    derived = {
        el.get("name"): data_size_bits(ctx, el)
        for el in ctx.record_els
        if el.get("name") in CASES
    }
    compiled = compiler_dsizes(tmp_path, header)
    for name, case in CASES.items():
        got, truth = derived[name], compiled[name]
        if len(truth) > 1:
            assert got is None, (name, got, truth)  # compilers disagree
            continue
        if got is not None:
            assert {got} == truth, (name, got, truth)
        if case.castxml_known:
            assert got is not None, (name, truth)

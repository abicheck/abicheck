"""CastXML-derived Itanium data size (dsize) never contradicts the compilers.

The oracle is the compilers' own placement of a derived class's member
(``tests/_data_size_matrix.py``). ``data_size.data_size_bits`` may answer
``None`` (unknown), but a value must equal every compiler's; a record the
compilers disagree on must be ``None``; and the shapes the derivation is
built for must not stay unknown.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse

from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.data_size import data_size_bits
from abicheck.extract.headers.castxml.tail_padding_rule import (
    ms_bitfields,
    tail_padding_rule,
)
from tests._data_size_matrix import (
    CASES,
    COMPILERS,
    compiler_dsizes,
    header_text,
    target_triple,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("std", ["c++17", "c++20"])
def test_data_size_agrees_with_compilers(tmp_path: Path, std: str) -> None:
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    # castxml parses as g++'s target; only a compiler whose target follows the
    # same tail-padding rule is an oracle (a clang++ targeting MSVC lays
    # records out by Microsoft's rules; Apple arm64 uses C++11 POD).
    castxml_triple = target_triple("g++")
    rule = tail_padding_rule(castxml_triple)
    if rule is None:
        pytest.skip(f"g++ target {castxml_triple!r} has no modeled Itanium rule")
    oracles = tuple(
        cc
        for cc in COMPILERS
        if shutil.which(cc) is not None and tail_padding_rule(target_triple(cc)) is rule
    )
    if not oracles:
        pytest.skip(f"no compiler follows castxml's {rule} tail-padding rule")
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
    ctx.target_triple = castxml_triple
    derived = {
        el.get("name"): data_size_bits(ctx, el)
        for el in ctx.record_els
        if el.get("name") in CASES
    }
    compiled = compiler_dsizes(tmp_path, header, oracles)
    for name, case in CASES.items():
        got, truth = derived[name], compiled[name]
        if len(truth) > 1:
            assert got is None, (name, got, truth)  # compilers disagree
            continue
        if got is not None:
            assert {got} == truth, (name, got, truth)
        # MinGW lays bit-fields out by Microsoft's rules, which the
        # derivation leaves unknown rather than guess.
        bitfield_unmodelled = ms_bitfields(ctx) and re.search(r":\s*\d+\s*;", case.decl)
        if case.castxml_known and not bitfield_unmodelled:
            assert got is not None, (name, truth)

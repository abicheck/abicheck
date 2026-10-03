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

"""L4 source replay on a real library tree (the eval suite's zstd row).

Three defects, each stated as the contract it broke rather than as the one
input that exposed it:

* public-root matching must not depend on how a header path is *spelled*:
  zstd reaches its public ``zstd.h`` as ``lib/common/../zstd.h``, and the
  clang extractor's private exact-file matcher compared un-normalized
  segments, so every declaration was classified non-public and L4 was empty;
* one compile unit an extractor cannot parse is that unit's failure, never the
  whole replay's (castxml handed malformed output raised a bare ``ParseError``
  that aborted ``dump``);
* replay never selects an assembler unit, which declares no C/C++ ABI, under
  any scope -- and never drops a C-family one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _strict_process import StrictProcessRunner, proc

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit, Target
from abicheck.buildsource.source_extractors import CastxmlSourceExtractor
from abicheck.buildsource.source_extractors.base import SourceExtractionError
from abicheck.buildsource.source_extractors.clang import _ClassifyContext
from abicheck.buildsource.source_replay import (
    REPLAY_SCOPES,
    _extract_one,
    select_compile_units,
)

# ── public-root matching is spelling-independent ─────────────────────────────

ROOT = "/repo/lib"
PUBLIC = f"{ROOT}/zstd.h"

#: Spellings of a path, each of which names the same file as *path* lexically.
#: The oracle below is os.path.normpath, not anything the extractor uses.
_SPELLINGS = (
    lambda p: p,
    lambda p: p.replace("/lib/", "/lib/common/../"),
    lambda p: p.replace("/lib/", "/lib/./"),
    lambda p: p.replace("/lib/", "/lib//"),
    lambda p: p.replace("/lib/", "/lib/compress/./../"),
    lambda p: p.replace("/repo/", "/repo/x/../"),
)

#: Headers the run may meet: the public one, a sibling, and one outside lib/.
_HEADERS = (PUBLIC, f"{ROOT}/zstd_errors.h", "/repo/programs/zstd.h")


def _oracle_public(header: str, *, file_root: str | None, dir_root: str | None) -> bool:
    norm = os.path.normpath(header)
    if file_root is not None and norm == os.path.normpath(file_root):
        return True
    return dir_root is not None and norm.startswith(os.path.normpath(dir_root) + os.sep)


@pytest.mark.parametrize("spell", range(len(_SPELLINGS)))
@pytest.mark.parametrize("header", _HEADERS)
@pytest.mark.parametrize("root_kind", ["file", "dir"])
def test_public_classification_ignores_path_spelling(
    tmp_path: Path, spell: int, header: str, root_kind: str
) -> None:
    # A directory root is recognised as one by existing on disk (or a trailing
    # slash); a trailing slash keeps this test independent of the filesystem.
    file_root = PUBLIC if root_kind == "file" else None
    dir_root = ROOT if root_kind == "dir" else None
    ctx = _ClassifyContext([file_root] if file_root else [dir_root + "/"])
    spelled = _SPELLINGS[spell](header)
    is_public = ctx.classify(spelled)[2]
    assert is_public == _oracle_public(
        spelled, file_root=file_root, dir_root=dir_root
    ), (
        spelled,
        root_kind,
    )


def test_the_matrix_is_not_vacuous() -> None:
    """Both outcomes occur, and the spelling actually changes the text."""
    outcomes = {
        _oracle_public(s(h), file_root=PUBLIC, dir_root=None)
        for s in _SPELLINGS
        for h in _HEADERS
    }
    assert outcomes == {True, False}
    assert len({s(PUBLIC) for s in _SPELLINGS}) == len(_SPELLINGS)


# ── a malformed extractor output is one TU's failure ─────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "\x00\x01garbage",
        "<GCC_XML><File id='f1'",  # truncated
        "\t.text\n\t.globl HUF_decompress4X1_usingDTable_internal_fast_asm_loop\n",
        "not xml at all <<<",
        # defusedxml refuses entity declarations with EntitiesForbidden, which
        # is not a ParseError subclass.
        '<!DOCTYPE x [<!ENTITY a "b">]><GCC_XML>&a;</GCC_XML>',
    ],
)
def test_malformed_castxml_output_is_a_per_unit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    from abicheck.buildsource.source_extractors import castxml as castxml_mod

    extractor = CastxmlSourceExtractor()
    monkeypatch.setattr(extractor, "available", lambda: True)

    def _castxml_writes_payload(argv: tuple[str, ...]) -> bool:
        # The one castxml call writes its XML to the -o path; the payload is
        # what this unit's real castxml run would have left there.
        if argv[0] != extractor.castxml_bin or "-o" not in argv:
            return False
        Path(argv[argv.index("-o") + 1]).write_text(payload)
        return True

    runner = StrictProcessRunner()
    for _ in range(2):  # extractor.extract, then the replay worker's call
        runner.expect(argv_matches=_castxml_writes_payload, returns=proc())
    runner.install(monkeypatch, castxml_mod.deadline, "run_bounded")
    cu = CompileUnit(
        id="cu://x", source="src/x.c", directory=str(tmp_path), language="C"
    )
    with pytest.raises(SourceExtractionError):
        extractor.extract(cu, public_header_roots=["x.h"])
    # ...and the replay worker turns it into a diagnostic rather than raising.
    tu, diag = _extract_one(extractor, ["x.h"], cu)
    assert tu is None and diag and "src/x.c" in diag
    runner.assert_exhausted()


# ── replay selection never includes an assembler unit ────────────────────────

#: Independent of the implementation's own suffix tuple on purpose.
_ASM = (".s", ".S", ".sx", ".asm", ".ASM", ".Asm", ".SX")
_C_FAMILY = (".c", ".C", ".cc", ".cpp", ".cxx", ".m", ".mm", ".cu")


def _build() -> BuildEvidence:
    units = [
        CompileUnit(id=f"cu://{i}", source=f"src/u{i}{ext}", target_id="target://lib")
        for i, ext in enumerate(_ASM + _C_FAMILY)
    ]
    return BuildEvidence(
        targets=[Target(id="target://lib", public_headers=["include/lib.h"])],
        compile_units=units,
    )


@pytest.mark.parametrize("scope", REPLAY_SCOPES)
def test_no_scope_selects_an_assembler_unit(scope: str) -> None:
    build = _build()
    changed = [cu.source for cu in build.compile_units]
    # One target, so the `target` scope's "every unit attached to a target"
    # (#1469 dropped the per-target selector) is that target's units.
    picked = select_compile_units(build, scope=scope, changed_paths=changed)
    assert not [cu.source for cu in picked if cu.source.endswith(_ASM)], scope
    assert build.compile_units[0].source.endswith(_ASM), (
        "assembler units must sort first"
    )
    if scope in ("full", "target", "changed"):
        # These scopes select every unit of the target / every changed source,
        # so the C-family units must all survive the filter.
        kept = {cu.source for cu in picked}
        assert {
            cu.source for cu in build.compile_units if cu.source.endswith(_C_FAMILY)
        } <= kept


@pytest.mark.parametrize("with_include_map", [False, True])
def test_headers_only_keeps_a_c_family_representative(with_include_map: bool) -> None:
    """Assembler units are dropped *before* a representative is chosen.

    ``_build()`` orders the assembler units first by id, so a heuristic that
    picks a target's first unit (or a set cover that picks an assembler unit to
    cover a header) and filters afterwards would leave the target with no unit
    at all. Oracle: the scope must still select at least one C-family unit,
    and every header a C-family unit includes stays covered.
    """
    build = _build()
    include_map = None
    if with_include_map:
        include_map = {cu.id: ["include/lib.h"] for cu in build.compile_units}
    picked = select_compile_units(
        build,
        scope="headers-only",
        include_map=include_map,
        public_header_roots=["include"],
    )
    assert picked, "headers-only lost the target's only C-family representatives"
    assert all(cu.source.endswith(_C_FAMILY) for cu in picked)

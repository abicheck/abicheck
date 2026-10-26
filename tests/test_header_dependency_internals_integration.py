"""End to end: a header-only dependency's internals stay out of the contract
on a comparison done with headers, stored or live.

The corpus shape ``header_only_dependency_internals`` (an intel/xvec-shaped
fake dependency whose vector's ABI tag, hidden friends, inline tables and
member typedefs follow the target ISA) is built twice, once per side, and
compared through the CLI. Two library shapes:

* the corpus shape itself: the library's own surface does not change, so
  every dependency finding is recorded out of contract and nothing gates;
* a derived shape where the vector's storage follows the ISA and the library
  exports a function taking it by value: the library's own break (``State``'s
  size and layout, the vector the library names, the re-mangled export) stays
  in contract and gates, and the dependency's internals still do not.

The oracle is the shape's ground truth (which entities are the library's and
which are the dependency's), written from the sources, not the classifier.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _field_shapes import CORPUS, materialize, replace_once, shape_sources
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    # PE records no coalesced (COMDAT) export, so on Windows the dependency's
    # exported instances stay in contract: ADR-089's PE limit.
    pytest.mark.skipif(
        sys.platform not in ("linux", "darwin"),
        reason="needs an export table that records weak definitions (ELF, Mach-O)",
    ),
    pytest.mark.skipif(
        shutil.which("g++") is None or shutil.which("castxml") is None,
        reason="needs g++ and castxml",
    ),
]

SHAPE = "header_only_dependency_internals"
DEP = CORPUS / SHAPE / "dep"
REASON = "dependency_internal_unreachable"

#: Kinds whose subject in this shape is always one of the dependency's.
DEPENDENCY_KINDS = {
    "hidden_friend_added",
    "hidden_friend_removed",
    "var_added",
    "type_added",
    "type_removed",
    "typedef_removed",
    "typedef_base_changed",
    "tag_type_renamed",
    "overload_set_rerouted",
    "internal_type_leaks_via_public_api",
    "func_added_elf_only",
    "func_removed_elf_only",
}


def _sources(side: str, *, library_break: bool) -> dict[str, str]:
    sources = shape_sources(SHAPE, side)
    if not library_break:
        return sources
    header = replace_once(
        sources["api.h"],
        "#include <fakesimd/simd.h>",
        "#define FAKESIMD_STORAGE FAKESIMD_LANES\n#include <fakesimd/simd.h>",
    )
    header = replace_once(
        header,
        "float sum(const State* s);",
        "float sum(const State* s);\nV scale(V v, float f);",
    )
    source = (
        sources["api.cpp"]
        + "namespace lib {\nV scale(V v, float f) { return v * f; }\n}\n"
    )
    return {"api.h": header, "api.cpp": source}


def _build(side: str, tmp: Path, *, library_break: bool) -> tuple[Path, Path]:
    src = materialize(
        SHAPE, side, tmp / side, _sources(side, library_break=library_break)
    )
    lib = src / "libapi.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-shared",
            "-fPIC",
            f"-I{src}",
            f"-I{DEP}",
            str(src / "api.cpp"),
            "-o",
            str(lib),
            # One install name for both sides: Mach-O otherwise records each
            # side's own output path, a soname change no source made.
            *(
                ["-Wl,-install_name,@rpath/libapi.so"]
                if sys.platform == "darwin"
                else []
            ),
        ],
        check=True,
        capture_output=True,
    )
    return src, lib


def _cli(args: list[str]) -> None:
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code in (0, 1, 2, 4), result.output[-3000:]


def _compare(tmp: Path, *, library_break: bool, live: bool) -> dict:
    (old_src, old_lib), (new_src, new_lib) = (
        _build(side, tmp, library_break=library_break) for side in ("v1", "v2")
    )
    report = tmp / "report.json"
    if live:
        _cli(
            [
                "compare", str(old_lib), str(new_lib),
                "-H", f"old={old_src / 'api.h'}", "-I", f"old={old_src}", "-I", f"old={DEP}",
                "-H", f"new={new_src / 'api.h'}", "-I", f"new={new_src}", "-I", f"new={DEP}",
                "-o", f"json={report}",
            ]
        )  # fmt: skip
    else:
        snaps = []
        for src, lib in ((old_src, old_lib), (new_src, new_lib)):
            snap = src / "snap.json"
            _cli(
                [
                    "dump",
                    str(lib),
                    "-H",
                    str(src / "api.h"),
                    "-I",
                    str(src),
                    "-I",
                    str(DEP),
                    "-o",
                    str(snap),
                ]
            )
            snaps.append(str(snap))
        _cli(["compare", *snaps, "--depth", "headers", "-o", f"json={report}"])
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.fixture(
    params=[(False, False), (False, True), (True, False), (True, True)],
    ids=[
        "unchanged-stored",
        "unchanged-live",
        "library-break-stored",
        "library-break-live",
    ],
)
def compared(request, tmp_path: Path) -> tuple[dict, bool]:
    library_break, live = request.param
    return _compare(tmp_path, library_break=library_break, live=live), library_break


def test_dependency_internals_are_recorded_out_of_contract(compared) -> None:
    report, _ = compared
    dependency = [c for c in report["changes"] if c["kind"] in DEPENDENCY_KINDS]
    assert {c["kind"] for c in dependency} >= {
        "hidden_friend_added",
        "hidden_friend_removed",
        "var_added",
        "type_added",
    }
    wrong = [
        (c["kind"], c["symbol"], c["contract_relevance"], c["contract_reason_code"])
        for c in dependency
        if c["contract_relevance"] != "PROVEN_OUT_OF_CONTRACT"
        or c["contract_reason_code"] != REASON
    ]
    assert wrong == []
    assert all(c["gate_contribution"] == 0 for c in dependency)
    reasons = {
        e["reason_code"]: e["matched_count"]
        for e in report["disposition_audit"]["scope_reasons"]
    }
    assert reasons[REASON] >= len(dependency)


def test_the_library_break_alone_gates(compared) -> None:
    report, library_break = compared
    gating = {
        (c["kind"], c["symbol"]) for c in report["changes"] if c["gate_contribution"]
    }
    if not library_break:
        assert gating == set()
        assert report["exit"]["code"] == 0
        return
    assert report["verdict"] == "BREAKING"
    # Exactly the library's own break: State and the vector it holds, and the
    # re-mangled lib::scale(V, float), one parameter change on the old (avx2)
    # export since V's resolved type changed under the same declaration.
    assert gating == {
        ("type_size_changed", "State"),
        ("type_field_offset_changed", "State"),
        ("type_size_changed", "basic_vec<float>"),
        ("type_field_type_changed", "basic_vec<float>"),
        (
            "func_params_changed",
            "_ZN3lib5scaleEN8fakesimd9basic_vecIfNS0_14fixed_size_abiILi8ENS0_12x86_avx2_tagEEEEEf",
        ),
    }
    for change in report["changes"]:
        if (change["kind"], change["symbol"]) in gating:
            assert change["contract_relevance"] == "IN_CONTRACT", change["kind"]

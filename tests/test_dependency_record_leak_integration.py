"""End to end: a leak on a dependency record a release starts (or stops)
naming publicly stays in contract.

Derived from the corpus shape ``header_only_dependency_internals`` at one ISA
on both sides, so the dependency's own instances do not change. The
dependency's ``detail::vec_traits`` gains a field whose size the library's
header picks. On one side nothing public names ``vec_traits`` (a dependency
internal, unreachable); on the other a public function takes it by pointer
(reached). Its size changes between the sides, which raises
``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` on it.

Truth, from the sources: the record is reached on one side, so its leak is
the library's promise on that side and must stay in contract
(``IN_CONTRACT``), in both directions (old internal / new reached, and the
mirror). Compared through the CLI, stored and live.
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
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
    pytest.mark.skipif(
        shutil.which("g++") is None or shutil.which("castxml") is None,
        reason="needs g++ and castxml",
    ),
]

SHAPE = "header_only_dependency_internals"
TRAITS = "vec_traits<fakesimd::x86_avx2_tag, float, 8>"


def _dependency(tmp: Path) -> Path:
    """The shape's dependency with a ``vec_traits`` field sized by the
    includer."""
    dep = tmp / "dep"
    shutil.copytree(CORPUS / SHAPE / "dep", dep)
    header = dep / "fakesimd" / "simd.h"
    text = replace_once(
        header.read_text(encoding="utf-8"),
        "  using element_type = T;\n",
        "  using element_type = T;\n  char pad[FAKESIMD_TRAITS_PAD];\n",
    )
    header.write_text(text, encoding="utf-8")
    return dep


def _sources(*, pad: int, reached: bool) -> dict[str, str]:
    # v1 on both sides: one ISA, so only vec_traits' size changes.
    sources = shape_sources(SHAPE, "v1")
    header = replace_once(
        sources["api.h"],
        "#include <fakesimd/simd.h>",
        f"#define FAKESIMD_TRAITS_PAD {pad}\n#include <fakesimd/simd.h>",
    )
    source = sources["api.cpp"]
    if reached:
        header = replace_once(
            header,
            "float sum(const State* s);",
            "float sum(const State* s);\n"
            f"int inspect(const fakesimd::detail::{TRAITS}* t);",
        )
        source += (
            "namespace lib {\n"
            f"int inspect(const fakesimd::detail::{TRAITS}* t) "
            "{ return t->pad[0]; }\n}\n"
        )
    return {"api.h": header, "api.cpp": source}


def _build(src: Path, dep: Path, sources: dict[str, str]) -> Path:
    materialize(SHAPE, "v1", src, sources)
    lib = src / "libapi.so"
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", f"-I{src}", f"-I{dep}",
         str(src / "api.cpp"), "-o", str(lib)],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return lib


def _cli(args: list[str]) -> None:
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code in (0, 1, 2, 4), result.output[-3000:]


def _compare(tmp: Path, *, reached_side: str, live: bool) -> dict:
    dep = _dependency(tmp)
    sides = {}
    for side, pad in (("old", 1), ("new", 2)):
        src = tmp / side
        lib = _build(src, dep, _sources(pad=pad, reached=side == reached_side))
        sides[side] = (src, lib)
    report = tmp / "report.json"
    if live:
        args = ["compare", str(sides["old"][1]), str(sides["new"][1])]
        for side, (src, _) in sides.items():
            args += ["-H", f"{side}={src / 'api.h'}", "-I", f"{side}={src}",
                     "-I", f"{side}={dep}"]  # fmt: skip
        _cli([*args, "-o", f"json={report}"])
    else:
        snaps = []
        for src, lib in sides.values():
            snap = src / "snap.json"
            _cli(["dump", str(lib), "-H", str(src / "api.h"), "-I", str(src),
                  "-I", str(dep), "-o", str(snap)])  # fmt: skip
            snaps.append(str(snap))
        _cli(["compare", *snaps, "--depth", "headers", "-o", f"json={report}"])
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize("live", [False, True], ids=["stored", "live"])
@pytest.mark.parametrize(
    "reached_side",
    ["new", "old"],
    ids=["old-internal-new-reached", "old-reached-new-internal"],
)
def test_a_leak_on_a_record_reached_on_one_side_stays_in_contract(
    tmp_path: Path, reached_side: str, live: bool
) -> None:
    report = _compare(tmp_path, reached_side=reached_side, live=live)
    leaks = [
        c
        for c in report["changes"]
        if c["kind"] == "internal_type_leaks_via_public_api" and c["symbol"] == TRAITS
    ]
    assert len(leaks) == 1, [(c["kind"], c["symbol"]) for c in report["changes"]]
    leak = leaks[0]
    assert leak["contract_relevance"] == "IN_CONTRACT"
    assert leak["contract_reason_code"] != "dependency_internal_unreachable"
    assert leak["gate_contribution"] != 0

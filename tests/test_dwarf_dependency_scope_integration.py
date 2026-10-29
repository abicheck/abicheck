"""End to end: a header-only dependency's internal instantiations are kept out
of the contract on a binary + DWARF compare, without headers.

A library built against a self-contained fake header-only SIMD dependency
(templates instantiated internally, ISA-dependent tag) is compiled twice, with
``-mavx2`` and ``-mavx512f``. The dependency's tag structs and the scratch
template it uses internally change between the builds, so the compare reports
``TYPE_ADDED``/``TYPE_REMOVED`` for them. None of those names is reached by the
library's public declarations, so they must read ``out of contract`` with the
``dependency_internal_unreachable`` reason, counted in the disposition audit.
The vector type the library's own state holds is reached, so it stays in
contract.

Two shapes are covered. With an exported function taking the vector by value,
it is reached through that parameter. Without one, it is reached only through
the library's own struct field, and the field spells a typedef alias the
DWARF-only path cannot resolve; that exercises the size bound on the alias.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
    pytest.mark.skipif(
        platform.machine().lower() not in ("x86_64", "amd64"),
        reason="builds with -mavx2/-mavx512f, which need an x86 compiler target",
    ),
    pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not found"),
]

FAKE_SIMD_H = """\
#pragma once
// Fake header-only SIMD dependency: templates instantiated internally.
namespace fakevec {
namespace simd {
struct avx2_tag {};
struct avx512_tag {};
#if defined(__AVX512F__)
using native_tag = avx512_tag;
#else
using native_tag = avx2_tag;
#endif
template <typename T, typename Tag> struct basic_vec {
  T data[8];
  T get() const { return data[0]; }
};
template <typename T, typename Tag>
basic_vec<T, Tag> operator*(basic_vec<T, Tag> a, T f) {
  for (auto& d : a.data) d *= f;
  return a;
}
template <typename T, typename Tag> T reduce(const basic_vec<T, Tag>& v) {
  T s{};
  for (auto d : v.data) s += d;
  return s;
}
// Internal-only instantiation helper: never named by the library's API.
template <typename T> struct scratch_pad { T buf[4]; };
template <typename T> T internal_helper(T x) {
  scratch_pad<T> p{};
  p.buf[0] = x;
  return p.buf[0];
}
}  // namespace simd
template <typename T> using vec = simd::basic_vec<T, simd::native_tag>;
}  // namespace fakevec
"""

API_H = """\
#pragma once
#include "fakevec/simd.h"
namespace lib {
using V = fakevec::vec<float>;
struct State { V acc; int n; };
{declarations}
}
"""

LIB_CPP = """\
#include "api.h"
namespace lib {{
{definitions}
}}
"""

SCALE_DECL = "V scale(V, float);"
SUM_DECL = "float sum(const State&);"
SCALE_DEF = "V scale(V x, float f) { return x * f; }"
SUM_DEF = (
    "float sum(const State& s) { return fakevec::simd::reduce(s.acc) + s.n"
    " + fakevec::simd::internal_helper(1.0f) * 0; }"
)


def _write_tree(root: Path, *, with_scale: bool) -> Path:
    (root / "dep" / "fakevec").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "dep" / "fakevec" / "simd.h").write_text(FAKE_SIMD_H)
    declarations = [SUM_DECL] + ([SCALE_DECL] if with_scale else [])
    definitions = [SUM_DEF] + ([SCALE_DEF] if with_scale else [])
    (root / "lib" / "api.h").write_text(
        API_H.replace("{declarations}", "\n".join(declarations))
    )
    (root / "lib" / "lib.cpp").write_text(
        LIB_CPP.format(definitions="\n".join(definitions))
    )
    return root


def _build_so(root: Path, isa_flag: str, out: Path) -> Path:
    subprocess.run(
        [
            "g++",
            "-std=c++20",
            "-O1",
            "-g",
            "-shared",
            "-fPIC",
            isa_flag,
            f"-I{root / 'dep'}",
            f"-I{root / 'lib'}",
            str(root / "lib" / "lib.cpp"),
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _compare(old: Path, new: Path, report: Path) -> dict:
    subprocess.run(
        abicheck_command("compare",
            str(old),
            str(new),
            "-o",
            f"json={report}",
        ),
        check=False,
        capture_output=True,
        timeout=600,
    )
    return json.loads(report.read_text())


@pytest.fixture(params=[True, False], ids=["direct-param", "field-alias-only"])
def compared(request, tmp_path: Path) -> tuple[dict, bool]:
    with_scale = request.param
    root = _write_tree(tmp_path / "src", with_scale=with_scale)
    old = _build_so(root, "-mavx2", tmp_path / "lib_avx2.so")
    new = _build_so(root, "-mavx512f", tmp_path / "lib_avx512f.so")
    return _compare(old, new, tmp_path / "report.json"), with_scale


def _presence(report: dict) -> dict[str, dict]:
    return {
        c["symbol"]: c
        for c in report["changes"]
        if c["kind"] in ("type_added", "type_removed")
    }


def test_dependency_tags_are_out_of_contract_and_counted(compared) -> None:
    report, _ = compared
    presence = _presence(report)
    tags = {"fakevec::simd::avx2_tag", "fakevec::simd::avx512_tag"}
    assert tags <= presence.keys()
    for name in tags:
        assert presence[name]["contract_relevance"] == "PROVEN_OUT_OF_CONTRACT"
        assert presence[name]["contract_reason_code"] == (
            "dependency_internal_unreachable"
        )
    reasons = {
        entry["reason_code"]: entry["matched_count"]
        for entry in report["disposition_audit"]["scope_reasons"]
    }
    assert reasons["dependency_internal_unreachable"] >= len(tags)


@pytest.mark.parametrize(
    "name",
    [
        "fakevec::simd::basic_vec<float, fakevec::simd::avx2_tag>",
        "fakevec::simd::basic_vec<float, fakevec::simd::avx512_tag>",
    ],
)
def test_reached_vector_type_stays_in_contract(compared, name: str) -> None:
    report, _ = compared
    presence = _presence(report)
    assert name in presence, (
        "the vector type the library's State holds must be reported"
    )
    assert presence[name]["contract_relevance"] == "IN_CONTRACT"
    assert presence[name]["contract_reason_code"] != "dependency_internal_unreachable"


def test_library_break_still_gates(compared) -> None:
    # With an exported parameter of the vector type, its mangled name changes
    # (a real break). Without one, the public surface and State's layout are
    # unchanged; the vector-type presence changes remain as risk, not a gate.
    report, with_scale = compared
    expected = "BREAKING" if with_scale else "COMPATIBLE_WITH_RISK"
    assert report["verdict"] == expected

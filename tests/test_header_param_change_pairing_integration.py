"""Header-parsed comparisons report a same-declaration parameter retype as one
``FUNC_PARAMS_CHANGED`` and a changed written signature as a removal.

Each shape is a real shared library built with g++ from a header, dumped
through ``abicheck dump -H`` and compared through the loader users reach, so
castxml's spellings, the resolved identities, the typedef maps and the record
layouts are the real ones. The shapes are siblings of one bug class: the
function's mangled name changes in every one of them, and only the header
declaration tells a parameter change from a removal.

Oracle: the expected finding is fixed per shape from what its header edit is
(same declaration with a retargeted type -> parameter change; old signature
gone from the header -> removal), not read from the implementation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.cli import main
from abicheck.serialization import load_snapshot

pytestmark = [pytest.mark.integration]

_VEC = "template <class T, int N = LIB_WIDTH> struct basic_vec { T d[N]; };\n"
_XVEC_LIKE = (
    "namespace lib { using V = basic_vec<float>; struct State { V acc; int n; };"
    " V scale(V v, float f); void init(State* s); }\n"
)

# name -> (v1 header, v2 header, implementation, function whose fate is checked,
#          expected kind for its old symbol)
_SHAPES: dict[str, tuple[str, str, str, str, ChangeKind]] = {
    "typedef_retarget_record": (
        "template <int N> struct vec { float d[N]; };\n"
        "namespace lib { using V = vec<8>; V scale(V v, float f); }\n",
        "template <int N> struct vec { float d[N]; };\n"
        "namespace lib { using V = vec<16>; V scale(V v, float f); }\n",
        "namespace lib { V scale(V v, float) { return v; } }",
        "scale",
        ChangeKind.FUNC_PARAMS_CHANGED,
    ),
    "typedef_retarget_builtin": (
        "namespace lib { using T = int; void put(T v, float f); }\n",
        "namespace lib { using T = long; void put(T v, float f); }\n",
        "namespace lib { void put(T, float) {} }",
        "put",
        ChangeKind.FUNC_PARAMS_CHANGED,
    ),
    # The xvec shape: the spelling and the typedef target stay
    # ``basic_vec<float>``; a default template argument picks the width, and
    # ``State`` completes the instantiation, so the header records its layout.
    "default_template_argument": (
        "#define LIB_WIDTH 8\n" + _VEC + _XVEC_LIKE,
        "#define LIB_WIDTH 16\n" + _VEC + _XVEC_LIKE,
        "namespace lib { V scale(V v, float) { return v; } void init(State* s) { s->n = 0; } }",
        "scale",
        ChangeKind.FUNC_PARAMS_CHANGED,
    ),
    # The same edit with nothing completing the instantiation: castxml marks
    # ``basic_vec<float>`` incomplete, so no header fact shows that the type
    # behind the spelling changed, and the pass makes no claim.
    "default_template_argument_never_completed": (
        "#define LIB_WIDTH 8\n" + _VEC + "namespace lib { using V = basic_vec<float>;"
        " V scale(V v, float f); }\n",
        "#define LIB_WIDTH 16\n" + _VEC + "namespace lib { using V = basic_vec<float>;"
        " V scale(V v, float f); }\n",
        "namespace lib { V scale(V v, float) { return v; } }",
        "scale",
        ChangeKind.FUNC_REMOVED,
    ),
    "retargeted_overload_beside_unchanged_one": (
        "template <int N> struct vec { float d[N]; };\n"
        "namespace lib { using V = vec<8>; V scale(V v, float f); int scale(int x); }\n",
        "template <int N> struct vec { float d[N]; };\n"
        "namespace lib { using V = vec<16>; V scale(V v, float f); int scale(int x); }\n",
        "namespace lib { V scale(V v, float) { return v; } int scale(int x) { return x; } }",
        "scale",
        ChangeKind.FUNC_PARAMS_CHANGED,
    ),
    "direct_public_retype": (
        "namespace lib { void put(int v, float f); }\n",
        "namespace lib { void put(long v, float f); }\n",
        "",
        "put",
        ChangeKind.FUNC_REMOVED,
    ),
    "internal_retype_case189": (
        "namespace demo { struct Meta { int m; }; void configure(void* o, Meta i); }\n",
        "namespace demo { namespace detail { struct Options { int y; }; }\n"
        "struct Meta { int m; }; void configure(detail::Options* o, Meta i); }\n",
        "",
        "configure",
        ChangeKind.FUNC_REMOVED,
    ),
}

_IMPLS_PER_SIDE = {
    "direct_public_retype": (
        "namespace lib { void put(int, float) {} }",
        "namespace lib { void put(long, float) {} }",
    ),
    "internal_retype_case189": (
        "namespace demo { void configure(void*, Meta) {} }",
        "namespace demo { void configure(detail::Options*, Meta) {} }",
    ),
}


def _need_toolchain() -> None:
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    if shutil.which("castxml") is None:
        pytest.skip("needs castxml")


def _build_and_dump(case: Path, header: str, impl: str) -> Path:
    case.mkdir(parents=True)
    (case / "lib.h").write_text("#pragma once\n" + header)
    (case / "lib.cpp").write_text(f'#include "lib.h"\n{impl}\n')
    so = case / "libt.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-shared",
            "-fPIC",
            "-g",
            "-o",
            str(so),
            str(case / "lib.cpp"),
        ],
        check=True,
    )
    out = case / "snap.json"
    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so),
            "-H",
            str(case / "lib.h"),
            "--label",
            case.name,
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    return out


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list]:
    _need_toolchain()
    root = tmp_path_factory.mktemp("header_param_change")
    out: dict[str, list] = {}
    for name, (v1, v2, impl, _fn, _kind) in _SHAPES.items():
        impl_old, impl_new = _IMPLS_PER_SIDE.get(name, (impl, impl))
        old = load_snapshot(_build_and_dump(root / name / "v1", v1, impl_old))
        new = load_snapshot(_build_and_dump(root / name / "v2", v2, impl_new))
        out[name] = [old, new, compare(old, new)]
    return out


def _function_findings(result, symbols: set[str]) -> list[tuple[ChangeKind, str]]:
    kinds = {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_ADDED,
        ChangeKind.FUNC_PARAMS_CHANGED,
    }
    return [
        (c.kind, c.symbol)
        for c in result.changes
        if c.kind in kinds and c.symbol in symbols
    ]


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_shape_reports_its_expected_function_finding(
    results: dict[str, list], shape: str
) -> None:
    old, new, result = results[shape]
    fn_name, expected = _SHAPES[shape][3], _SHAPES[shape][4]
    old_symbols = {f.mangled for f in old.declarations.functions if f.name == fn_name}
    new_symbols = {f.mangled for f in new.declarations.functions if f.name == fn_name}
    (old_key,) = old_symbols - new_symbols
    (new_key,) = new_symbols - old_symbols
    findings = _function_findings(result, old_symbols | new_symbols)
    if expected is ChangeKind.FUNC_PARAMS_CHANGED:
        assert findings == [(ChangeKind.FUNC_PARAMS_CHANGED, old_key)], findings
    else:
        assert sorted(findings) == sorted(
            [(ChangeKind.FUNC_REMOVED, old_key), (ChangeKind.FUNC_ADDED, new_key)]
        ), findings
    assert result.verdict.value == "BREAKING"

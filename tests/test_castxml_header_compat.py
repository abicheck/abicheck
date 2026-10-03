"""castxml compatibility preamble (`extract/castxml_header_compat.py`).

Bug class: castxml emulates g++ >= 13, so glibc assumes a builtin
``_Float128`` in C++; castxml supplies one only where Clang has
``__float128`` (x86-64). On every target whose ``long double`` is binary128
(AArch64, s390x, RISC-V, LoongArch) each C++ header reaching ``<cwchar>`` /
``<cstdlib>`` failed to parse (examples nightly: case80/case89/case126 ERROR on
AArch64 for 81 days). The invariants stated here:

* each guard activates exactly under its documented condition, over the whole
  small domain of (language, emulated GCC, castxml predefine, long double
  format) -- evaluated by the real preprocessor, judged by an independent
  predicate;
* nothing synthetic is ever attributed to a library -- the stand-in and every
  member castxml synthesizes for it are filtered, and a use reads as the
  fundamental ``_Float128``, exactly as on x86-64;
* where it is not needed it is inert: a host-target dump is identical with
  and without it;
* where it is needed, libstdc++'s float-touching headers parse, on a real
  AArch64-target castxml run.
"""

from __future__ import annotations

import itertools
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree.ElementTree import Element

import pytest

from abicheck.extract import castxml_header_compat as compat
from abicheck.extract.headers.castxml.location import is_builtin_element
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_snapshot

# -- Guard semantics, exhaustively, under the real preprocessor -------------

_LANGS = ("c", "c++")
_GNUC = (None, 8, 9, 12, 13, 14)
_PREDEFINED = (False, True)
_LDBL_MANT = (None, 64, 113)
_AARCH64 = (False, True)
_CASTXML_RUN = (False, True)


def _float128_active(lang, gnuc, predefined, mant, aarch64, castxml) -> bool:
    """The documented ``_Float128`` condition, restated independently."""
    return (
        castxml
        and lang == "c++"
        and gnuc is not None
        and gnuc >= 13
        and not predefined
        and mant == 113
    )


def _vector_active(lang, gnuc, predefined, mant, aarch64, castxml) -> bool:
    """The documented AArch64 vector-builtin condition, restated independently."""
    return castxml and aarch64 and gnuc is not None and gnuc >= 9


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("gcc"), reason="needs a C preprocessor")
def test_guards_activate_exactly_under_their_documented_conditions(
    tmp_path: Path,
) -> None:
    pre = tmp_path / compat.PREAMBLE_FILENAME
    pre.write_text(compat.CASTXML_HEADER_PREAMBLE)
    wrong: list[tuple[object, ...]] = []
    domain = list(
        itertools.product(
            _LANGS, _GNUC, _PREDEFINED, _LDBL_MANT, _AARCH64, _CASTXML_RUN
        )
    )
    for case in domain:
        lang, gnuc, predefined, mant, aarch64, castxml = case
        # -undef: no host macros, so each case's macros are exactly these.
        cmd = ["gcc", "-E", "-P", "-undef", "-x", lang]
        if lang == "c++":
            cmd.append("-D__cplusplus=201703L")
        if gnuc is not None:
            cmd.append(f"-D__GNUC__={gnuc}")
        if predefined:
            cmd.append("-D_Float128=__castxml_Float128")
        if mant is not None:
            cmd.append(f"-D__LDBL_MANT_DIG__={mant}")
        if aarch64:
            cmd.append("-D__aarch64__=1")
        if castxml:
            cmd.append("-D__castxml__=1")
        out = subprocess.run(
            [*cmd, str(pre)], capture_output=True, text=True, check=True
        ).stdout
        got = (compat.FLOAT128_STANDIN in out, "__Float32x4_t" in out)
        want = (_float128_active(*case), _vector_active(*case))
        if got != want:
            wrong.append((case, got, want))
    # Neither oracle is constant over the domain, so the comparison is not vacuous.
    for oracle in (_float128_active, _vector_active):
        hits = sum(oracle(*c) for c in domain)
        assert 0 < hits < len(domain)
    assert not wrong, wrong


# -- Nothing synthetic reaches a snapshot ------------------------------------


class _Ctx:
    def __init__(self, files: dict[str, str]) -> None:
        self.id_map = {
            fid: Element("File", {"id": fid, "name": name})
            for fid, name in files.items()
        }


@pytest.mark.parametrize(
    ("file_name", "synthetic"),
    [
        ("<builtin>", True),
        ("<command-line>", True),
        (f"/tmp/abicheck_castxml_x/{compat.PREAMBLE_FILENAME}", True),
        (f"C:\\Temp\\abicheck_castxml_x\\{compat.PREAMBLE_FILENAME}", True),
        (compat.PREAMBLE_FILENAME, True),
        (f"/src/include/my{compat.PREAMBLE_FILENAME}", False),  # exact basename only
        (f"/src/{compat.PREAMBLE_FILENAME}/real.h", False),
        ("/src/include/foo.h", False),
    ],
)
def test_preamble_file_is_synthetic_and_nothing_else_is(
    file_name: str, synthetic: bool
) -> None:
    ctx = _Ctx({"f1": file_name})
    assert is_builtin_element(ctx, Element("Function", {"file": "f1"})) is synthetic


# -- Real castxml runs --------------------------------------------------------

_CASTXML = shutil.which("castxml")
_CROSS = shutil.which("aarch64-linux-gnu-g++") and shutil.which("aarch64-linux-gnu-gcc")
_FLOAT_HEADERS = (
    "string",
    "cwchar",
    "cstdlib",
    "cmath",
    "memory",
    "complex",
    "map",
    "limits",
    "charconv",
)


def _dump(
    tmp: Path, compiler_dir: Path | None, header: Path, lib: Path, monkeypatch
) -> AbiSnapshot:
    if compiler_dir is not None:
        monkeypatch.setenv("PATH", f"{compiler_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp / "cache"))
    out = tmp / f"{lib.stem}.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "dump",
            str(lib),
            "-H",
            str(header),
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    return load_snapshot(out)


def _write_case(tmp: Path) -> tuple[Path, Path]:
    header = tmp / "api.h"
    header.write_text(
        "#pragma once\n"
        + "".join(f"#include <{h}>\n" for h in _FLOAT_HEADERS)
        + "struct S { std::string s; long double ld; };\n"
        + "int f(const S&);\n"
        + "_Float128 q(_Float128);\n"
    )
    src = tmp / "api.cpp"
    src.write_text(
        '#include "api.h"\nint f(const S& s) { return (int)s.s.size(); }\n_Float128 q(_Float128 x) { return x; }\n'
    )
    return header, src


def _assert_clean(snap) -> None:
    text = repr([t.name for t in snap.declarations.types]) + repr(
        [f.name for f in snap.declarations.functions]
    )
    assert compat.FLOAT128_STANDIN not in text
    q = next(f for f in snap.declarations.functions if f.name == "q")
    assert q.return_type == compat.FLOAT128_SPELLING
    assert [p.type for p in q.params] == [compat.FLOAT128_SPELLING]
    assert any(f.name == "f" for f in snap.declarations.functions)


@pytest.mark.integration
@pytest.mark.skipif(
    not (_CASTXML and _CROSS),
    reason="needs castxml and an aarch64-linux-gnu cross toolchain",
)
def test_aarch64_target_parses_libstdcxx_float_headers(
    tmp_path: Path, monkeypatch
) -> None:
    shim = tmp_path / "shim"
    shim.mkdir()
    for tool in ("g++", "gcc"):
        (shim / tool).symlink_to(shutil.which(f"aarch64-linux-gnu-{tool}"))
    header, src = _write_case(tmp_path)
    lib = tmp_path / "libapi_arm.so"
    subprocess.run(
        [str(shim / "g++"), "-shared", "-fPIC", "-g", str(src), "-o", str(lib)],
        check=True,
    )
    _assert_clean(_dump(tmp_path, shim, header, lib, monkeypatch))
    # The same toolchain in C mode (no preamble there) must keep parsing glibc.
    c_header = tmp_path / "capi.h"
    c_header.write_text(
        "#pragma once\n#include <math.h>\n#include <wchar.h>\n#include <stdlib.h>\nint g(double);\n"
    )
    c_src = tmp_path / "capi.c"
    c_src.write_text('#include "capi.h"\nint g(double d) { return (int)sqrt(d); }\n')
    c_lib = tmp_path / "libcapi_arm.so"
    subprocess.run(
        [
            str(shim / "gcc"),
            "-shared",
            "-fPIC",
            "-g",
            str(c_src),
            "-o",
            str(c_lib),
            "-lm",
        ],
        check=True,
    )
    c_payload = _dump(tmp_path, shim, c_header, c_lib, monkeypatch)
    assert any(f.name == "g" for f in c_payload.declarations.functions)


def _host_gxx_accepts_float128() -> bool:
    """The host case below compiles ``_Float128`` with g++; Apple clang and
    MSVC-targeting toolchains reject it, and those hosts have no glibc
    ``_Float128`` problem for the preamble to be inert about."""
    if not shutil.which("g++"):
        return False
    r = subprocess.run(
        ["g++", "-x", "c++", "-fsyntax-only", "-"],
        input="_Float128 q(_Float128 x) { return x; }\n",
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


@pytest.mark.integration
@pytest.mark.skipif(
    not (_CASTXML and shutil.which("g++")), reason="needs castxml and g++"
)
@pytest.mark.skipif(
    not _host_gxx_accepts_float128(), reason="host g++ has no _Float128"
)
def test_preamble_is_inert_on_the_host_target(tmp_path: Path, monkeypatch) -> None:
    """With and without the preamble, a host dump is byte-identical."""
    header, src = _write_case(tmp_path)
    lib = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", str(src), "-o", str(lib)], check=True
    )
    with_preamble = _dump(tmp_path / "a", None, header, lib, monkeypatch)
    # The same dump with the preamble emptied, in a fresh process and cache.
    (tmp_path / "b").mkdir()
    sitecustom = tmp_path / "b" / "sitecustomize.py"
    marker = tmp_path / "b" / "preamble-removed"
    sitecustom.write_text(
        "import abicheck.extract.castxml_header_compat as c, pathlib\n"
        "c.CASTXML_HEADER_PREAMBLE = ''\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
    )
    monkeypatch.setenv(
        "PYTHONPATH", f"{tmp_path / 'b'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"
    )
    without = _dump(tmp_path / "b", None, header, lib, monkeypatch)
    # Differential test: prove the second configuration really ran without the
    # preamble (separate caches are given by the per-run XDG_CACHE_HOME).
    assert marker.exists(), "sitecustomize did not run; the comparison would be vacuous"
    if platform.machine().lower() in ("x86_64", "amd64", "i686"):
        assert (
            with_preamble.declarations.functions,
            with_preamble.declarations.types,
        ) == (
            without.declarations.functions,
            without.declarations.types,
        )
    _assert_clean(with_preamble)

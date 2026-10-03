"""Debian/Ubuntu cross-toolchain sysroot recognition (`extract/system_header_layout.py`).

Bug class: the default dependency exclusion (``dumper_scoping``, via
``provenance.is_system_header``) recognized a target's system headers only at
``/usr/include`` and under ``lib/gcc/<triple>/<ver>/include``. A cross
toolchain installs the target's glibc and libstdc++ at
``/usr/<triple>/include`` (which castxml reports as
``/usr/lib/gcc-cross/<triple>/<ver>/../../../../<triple>/include``), so a
cross-target dump kept every libc/libstdc++ declaration: 7,707 functions and
59 MB for a header whose host-target dump has 7 functions and 5 MB (measured,
aarch64-linux-gnu g++ 13). The invariants:

* every real cross triple's sysroot is a system root, at any depth below it
  and through the ``..`` spelling the compiler reports;
* a hyphenated project directory in the same position never is;
* exactly as for ``/usr/include``: the bare root and libstdc++'s ``c++`` tree
  are bare system directories, a project subdirectory under the root is not
  (so an explicit ``-I /usr/<triple>/include/mylib`` stays a project dir);
* end to end, a cross-target dump keeps exactly what the host-target dump
  keeps.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from abicheck.provenance import _is_bare_system_dir, _segments, is_system_header

#: Real Debian cross-toolchain triples (``gcc-<ver>-<triple>`` package names).
_DEBIAN_CROSS_TRIPLES = (
    "aarch64-linux-gnu",
    "arm-linux-gnueabihf",
    "arm-linux-gnueabi",
    "i686-linux-gnu",
    "powerpc64le-linux-gnu",
    "s390x-linux-gnu",
    "riscv64-linux-gnu",
    "mips64el-linux-gnuabi64",
    "loongarch64-linux-gnu",
    "x86_64-linux-gnux32",
    "x86_64-w64-mingw32",
    "i686-w64-mingw32",
    "aarch64-linux-musl",
)
#: Hyphenated names a project could plausibly use for /usr/<name>/include.
_PROJECT_NAMES = (
    "my-lib",
    "foo-bar",
    "acme-sdk-2",
    "lib-core",
    "x86-tools",
    "arm-utils",
)

_TAIL = st.lists(
    st.sampled_from(["mylib", "bits", "sys", "api.h", "x.hpp", "v2"]), max_size=4
)
_LEAD = st.lists(st.sampled_from(["opt", "sysroot", "cross", "home"]), max_size=2)


@given(triple=st.sampled_from(_DEBIAN_CROSS_TRIPLES), lead=_LEAD, tail=_TAIL)
def test_cross_sysroot_headers_are_system_at_any_depth(triple, lead, tail) -> None:
    path = "/" + "/".join([*lead, "usr", triple, "include", *tail, "h.h"])
    assert is_system_header(path)


@given(triple=st.sampled_from(_DEBIAN_CROSS_TRIPLES), tail=_TAIL)
def test_compiler_reported_dotdot_spelling_is_recognized(triple, tail) -> None:
    path = "/".join(
        [
            "/usr/lib/gcc-cross",
            triple,
            "13",
            "..",
            "..",
            "..",
            "..",
            triple,
            "include",
            *tail,
            "h.h",
        ]
    )
    assert is_system_header(path)


@given(name=st.sampled_from(_PROJECT_NAMES), tail=_TAIL)
def test_hyphenated_project_directory_is_never_a_cross_sysroot(name, tail) -> None:
    path = "/" + "/".join(["usr", name, "include", *tail, "h.h"])
    assert not is_system_header(path)
    assert not _is_bare_system_dir(_segments("/" + "/".join(["usr", name, "include"])))


@given(
    triple=st.sampled_from(_DEBIAN_CROSS_TRIPLES),
    version=st.sampled_from(["12", "13", "14.2.0"]),
)
def test_bare_system_dir_matches_usr_include_semantics(triple, version) -> None:
    for root, native in ((f"/usr/{triple}/include", "/usr/include"),):
        # The bare root, and libstdc++'s tree under it, are system directories ...
        assert (
            _is_bare_system_dir(_segments(root))
            is _is_bare_system_dir(_segments(native))
            is True
        )
        assert _is_bare_system_dir(_segments(f"{root}/c++/{version}"))
        assert _is_bare_system_dir(_segments(f"{native}/c++/{version}"))
        # ... while a project subdirectory under either stays a project directory.
        assert not _is_bare_system_dir(_segments(f"{root}/mylib"))
        assert not _is_bare_system_dir(_segments(f"{native}/mylib"))


_CROSS = shutil.which("aarch64-linux-gnu-g++")


@pytest.mark.integration
@pytest.mark.skipif(
    not (_CROSS and shutil.which("castxml") and shutil.which("g++")),
    reason="needs castxml, g++ and aarch64-linux-gnu-g++",
)
def test_cross_target_dump_keeps_what_the_host_dump_keeps(tmp_path: Path) -> None:
    header = tmp_path / "s.h"
    header.write_text(
        "#pragma once\n#include <string>\n#include <vector>\nstruct S { std::string s; std::vector<int> v; };\nint f(const S&);\n"
    )
    src = tmp_path / "s.cpp"
    src.write_text('#include "s.h"\nint f(const S& s) { return (int)s.s.size(); }\n')
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "g++").symlink_to(_CROSS)
    (shim / "gcc").symlink_to(shutil.which("aarch64-linux-gnu-gcc") or _CROSS)

    def dump(tag: str, compiler: str, path_prefix: str | None) -> set[str]:
        lib = tmp_path / f"lib{tag}.so"
        subprocess.run(
            [compiler, "-shared", "-fPIC", str(src), "-o", str(lib)], check=True
        )
        env = {**os.environ, "XDG_CACHE_HOME": str(tmp_path / f"cache-{tag}")}
        if path_prefix:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
        out = tmp_path / f"{tag}.json"
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
            env=env,
        )
        payload = json.loads(out.read_text())["sections"]["declarations"]["payload"]
        return {fn["name"] for fn in payload["functions"]}

    host = dump("host", "g++", None)
    cross = dump("cross", str(shim / "g++"), str(shim))
    assert "f" in host
    assert cross == host

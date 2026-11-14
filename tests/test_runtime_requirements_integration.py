"""SANITIZER_RUNTIME_REQUIRED on real compiled libraries (gcc and clang)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.dumper import dump

# ELF-only: DT_NEEDED, versioned imports and -Wl,-soname.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="ELF shared-library facts"
    ),
]

#: The named optional feature being unavailable: the sanitizer flag refused
#: for this target, or its runtime library not installed. Any other failure
#: of a configured compiler is a test failure.
_UNAVAILABLE_SANITIZER = re.compile(
    r"unsupported option '-fsanitize=[^']*'"
    r"|unrecognized argument to '-fsanitize=' option"
    r"|cannot find -l(?:asan|ubsan|tsan|hwasan|lsan|msan)\b"
    r"|cannot find (?:\S*/)?lib(?:asan|ubsan|tsan|hwasan|lsan|msan)[\w.]*"
    r"|cannot find (?:\S*/)?libclang_rt\.\w+[\w.-]*"
)

_SRC = "int f(int *p, int n) { return p[n] + n * 3; }\n"


def _build(tmp: Path, cc: str, flags: list[str], name: str) -> Path:
    src = tmp / f"{name}.c"
    src.write_text(_SRC, encoding="utf-8")
    so = tmp / name / "libx.so"
    so.parent.mkdir()
    proc = subprocess.run(
        [
            cc,
            "-g",
            "-fPIC",
            "-shared",
            *flags,
            "-Wl,-soname,libx.so",
            "-o",
            str(so),
            str(src),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 and any(f.startswith("-fsanitize") for f in flags):
        unavailable = _UNAVAILABLE_SANITIZER.search(proc.stderr)
        if unavailable:
            pytest.skip(f"{cc}: sanitizer unavailable here ({unavailable.group(0)})")
    assert proc.returncode == 0, (
        f"{' '.join(proc.args)} failed (exit {proc.returncode})\n"
        f"--- source ---\n{_SRC}--- stderr ---\n{proc.stderr}"
    )
    return so


def _kinds(tmp: Path, cc: str, new_flags: list[str]) -> tuple[Verdict, set[str]]:
    if shutil.which(cc) is None:
        pytest.skip(f"{cc} is not installed")
    old = dump(_build(tmp, cc, [], "old"), [], version="1")
    new = dump(_build(tmp, cc, new_flags, "new"), [], version="2")
    result = compare(old, new)
    return result.verdict, {c.kind.value for c in result.changes}


@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_an_asan_build_requires_its_runtime(tmp_path: Path, cc: str) -> None:
    # gcc links libasan.so (a preload runtime); clang links no runtime into a
    # -shared library and leaves __asan_* imports nothing provides.
    verdict, kinds = _kinds(tmp_path, cc, ["-fsanitize=address"])
    assert "sanitizer_runtime_required" in kinds
    assert verdict == Verdict.BREAKING


def test_clang_ubsan_shared_library_has_unprovided_runtime_imports(
    tmp_path: Path,
) -> None:
    verdict, kinds = _kinds(tmp_path, "clang", ["-fsanitize=undefined"])
    assert "sanitizer_runtime_required" in kinds
    assert verdict == Verdict.BREAKING


def test_gcc_ubsan_links_a_runtime_that_loads_anywhere(tmp_path: Path) -> None:
    verdict, kinds = _kinds(tmp_path, "gcc", ["-fsanitize=undefined"])
    assert "sanitizer_runtime_required" not in kinds
    assert verdict != Verdict.BREAKING


def test_a_self_contained_library_gaining_an_import_nothing_provides(
    tmp_path: Path,
) -> None:
    from abicheck.dependency_info import populate_dependency_info

    if shutil.which("gcc") is None:
        pytest.skip("gcc is not installed")
    sides = {
        "old": "int f(int x) { return x + 1; }\n",
        # -shared links with undefined references by default: the new build
        # links fine, and every process that loads it fails.
        "new": "int missing_helper(int);\nint f(int x) { return missing_helper(x); }\n",
    }
    snaps = {}
    for name, text in sides.items():
        src = tmp_path / f"{name}.c"
        src.write_text(text, encoding="utf-8")
        so = tmp_path / name / "libx.so"
        so.parent.mkdir()
        subprocess.run(
            ["gcc", "-fPIC", "-shared", "-Wl,-soname,libx.so", "-o", str(so), str(src)],
            check=True,
        )
        snap = dump(so, [], version=name)
        populate_dependency_info(snap, so, [so.parent], None, "")
        snaps[name] = snap
    result = compare(snaps["old"], snaps["new"])
    kinds = {c.kind.value for c in result.changes}
    assert "imported_symbol_unprovided" in kinds
    assert result.verdict == Verdict.BREAKING


@pytest.mark.parametrize(
    ("stderr", "unavailable"),
    [
        ("clang: error: unsupported option '-fsanitize=address' for target 'x'", True),
        ("/usr/bin/ld: cannot find -lasan: No such file or directory", True),
        ("/usr/bin/ld: cannot find libasan_preinit.o: No such file or directory", True),
        ("ld: cannot find /usr/lib/clang/18/lib/libclang_rt.asan-x86_64.a", True),
        # unrelated failures that merely mention sanitize / cannot find
        ("error: 'sanitize' attribute ignored", False),
        ("/usr/bin/ld: cannot find -lfoo: No such file or directory", False),
        ("x.c:1: error: expected ';' before 'sanitize_me'", False),
    ],
)
def test_only_a_named_unavailable_sanitizer_is_a_skip(stderr, unavailable) -> None:
    assert bool(_UNAVAILABLE_SANITIZER.search(stderr)) is unavailable

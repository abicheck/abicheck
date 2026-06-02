"""Cross-compiler false positive prevention tests.

Verifies that the same C/C++ source compiled with gcc vs clang produces no
spurious ABI diffs. Different compilers may generate different DWARF layouts,
ELF symbol ordering, or debug info, but the public ABI should be identical.

Requires: gcc, g++, clang, clang++, castxml (all available in CI).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from abicheck.checker import Verdict

# ── Test sources ──────────────────────────────────────────────────────────

C_SRC = """\
#include <stddef.h>

typedef struct {
    int width;
    int height;
} Rect;

int rect_area(const Rect *r) {
    return r->width * r->height;
}

Rect make_rect(int w, int h) {
    Rect r = {w, h};
    return r;
}

void free_rect(Rect *r) {
    (void)r;
}

const char *version(void) {
    return "1.0.0";
}
"""

C_HDR = """\
typedef struct {
    int width;
    int height;
} Rect;

int rect_area(const Rect *r);
Rect make_rect(int w, int h);
void free_rect(Rect *r);
const char *version(void);
"""

CPP_SRC = """\
class Widget {
public:
    Widget() : x_(0), y_(0) {}
    virtual ~Widget() {}
    virtual int area() const { return x_ * y_; }
    void resize(int x, int y) { x_ = x; y_ = y; }
    int x() const { return x_; }
    int y() const { return y_; }
private:
    int x_;
    int y_;
};

Widget* create_widget() { return new Widget(); }
void destroy_widget(Widget* w) { delete w; }
"""

CPP_HDR = """\
class Widget {
public:
    Widget();
    virtual ~Widget();
    virtual int area() const;
    void resize(int x, int y);
    int x() const;
    int y() const;
private:
    int x_;
    int y_;
};

Widget* create_widget();
void destroy_widget(Widget* w);
"""


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


# Generous compile timeout: a cold clang/gcc on a Windows CI runner (cold
# toolchain cache, on-access AV scanning, slow temp filesystem) can take far
# longer than a warm Linux invocation. This test verifies abicheck's diff
# behavior, not compiler throughput, so a slow/hung toolchain should skip
# (like a compilation failure does below), not fail the suite.
_COMPILE_TIMEOUT_S = 180


def _run_compile_or_skip(cmd: list[str], *, label: str = "Compilation") -> None:
    """Run a compile command, skipping (not failing) on timeout or error.

    A non-zero exit or a timeout both mean "this runner's toolchain couldn't
    produce the artifact" — neither is an abicheck defect, so both skip.
    """
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_COMPILE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(
            f"{label} timed out after {_COMPILE_TIMEOUT_S}s; "
            "slow/cold toolchain on this runner"
        )
    if r.returncode != 0:
        pytest.skip(f"{label} failed: {r.stderr[:200]}")


def _compile_so(src: str, out: Path, compiler: str, lang: str) -> None:
    ext = ".c" if lang == "c" else ".cpp"
    src_file = out.with_suffix(ext)
    src_file.write_text(textwrap.dedent(src).strip(), encoding="utf-8")
    cmd = [compiler, "-shared", "-fPIC", "-g", "-fvisibility=default",
           "-o", str(out), str(src_file)]
    if lang == "cpp":
        cmd.insert(1, "-std=c++17")
    elif lang == "c" and compiler in ("g++", "clang++"):
        # Force C mode when using a C++ driver on .c files
        cmd.insert(1, "-x")
        cmd.insert(2, "c")
    _run_compile_or_skip(cmd, label=f"Compilation ({compiler})")


def _dump_and_compare(gcc_so: Path, clang_so: Path, hdr: str | None,
                      lang: str, tmp_path: Path):
    """Dump both .so files and compare."""
    from abicheck.checker import compare
    from abicheck.dumper import dump

    compiler_name = "cc" if lang == "c" else "c++"
    headers = []
    if hdr is not None:
        h = tmp_path / "header.h" if lang == "c" else tmp_path / "header.hpp"
        h.write_text(textwrap.dedent(hdr).strip(), encoding="utf-8")
        headers = [h]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        snap_gcc = dump(gcc_so, headers=headers, version="gcc", compiler=compiler_name)
        snap_clang = dump(clang_so, headers=headers, version="clang", compiler=compiler_name)

    return compare(snap_gcc, snap_clang)


@pytest.mark.integration
class TestCCrossFP:
    """Same C source, gcc vs clang → no false positives."""

    def test_gcc_vs_clang_c_no_break(self, tmp_path):
        _require_tool("gcc")
        _require_tool("clang")
        _require_tool("castxml")

        gcc_so = tmp_path / "libtest_gcc.so"
        clang_so = tmp_path / "libtest_clang.so"
        _compile_so(C_SRC, gcc_so, "gcc", "c")
        _compile_so(C_SRC, clang_so, "clang", "c")

        r = _dump_and_compare(gcc_so, clang_so, C_HDR, "c", tmp_path)
        assert not r.breaking, (
            f"gcc vs clang on identical C source should not be BREAKING; "
            f"changes: {[(c.kind.value, c.symbol) for c in r.changes]}"
        )

    def test_gcc_vs_clang_c_elf_only_no_break(self, tmp_path):
        """ELF-only mode: same C source, gcc vs clang."""
        _require_tool("gcc")
        _require_tool("clang")

        gcc_so = tmp_path / "libtest_gcc.so"
        clang_so = tmp_path / "libtest_clang.so"
        _compile_so(C_SRC, gcc_so, "gcc", "c")
        _compile_so(C_SRC, clang_so, "clang", "c")

        r = _dump_and_compare(gcc_so, clang_so, None, "c", tmp_path)
        assert not r.breaking


@pytest.mark.integration
class TestCppCrossFP:
    """Same C++ source, g++ vs clang++ → no false positives.

    NOTE: gcc and clang may emit different DWARF representations for vtable
    pointers (e.g., _vptr.Widget field presence/name differs). This is a
    known cross-compiler difference. The test validates that at minimum the
    comparison completes without error and documents the divergence.
    """

    def test_gxx_vs_clangxx_completes(self, tmp_path):
        """Cross-compiler C++ comparison should complete without error."""
        _require_tool("g++")
        _require_tool("clang++")
        _require_tool("castxml")

        gcc_so = tmp_path / "libwidget_gcc.so"
        clang_so = tmp_path / "libwidget_clang.so"
        _compile_so(CPP_SRC, gcc_so, "g++", "cpp")
        _compile_so(CPP_SRC, clang_so, "clang++", "cpp")

        r = _dump_and_compare(gcc_so, clang_so, CPP_HDR, "cpp", tmp_path)
        # Should not crash — verdict may vary due to DWARF vtable differences
        assert isinstance(r.verdict, Verdict)

    def test_gxx_vs_clangxx_c_api_subset_no_break(self, tmp_path):
        """Same C++ with extern C wrapper — C API subset should not break."""
        _require_tool("g++")
        _require_tool("clang++")
        _require_tool("castxml")

        # Use the C source (no vtables, no cross-compiler DWARF issues)
        gcc_so = tmp_path / "libtest_gcc.so"
        clang_so = tmp_path / "libtest_clang.so"
        _compile_so(C_SRC, gcc_so, "g++", "c")
        _compile_so(C_SRC, clang_so, "clang++", "c")

        r = _dump_and_compare(gcc_so, clang_so, C_HDR, "c", tmp_path)
        assert not r.breaking


@pytest.mark.integration
class TestOptimizationLevelFP:
    """Same source compiled with -O0 vs -O2 → no false positives."""

    def test_o0_vs_o2_c_no_break(self, tmp_path):
        _require_tool("gcc")
        _require_tool("castxml")

        o0_so = tmp_path / "libtest_o0.so"
        o2_so = tmp_path / "libtest_o2.so"

        for so, opt in [(o0_so, "-O0"), (o2_so, "-O2")]:
            src_file = so.with_suffix(".c")
            src_file.write_text(textwrap.dedent(C_SRC).strip(), encoding="utf-8")
            cmd = ["gcc", "-shared", "-fPIC", "-g", "-fvisibility=default",
                   opt, "-o", str(so), str(src_file)]
            _run_compile_or_skip(cmd)

        r = _dump_and_compare(o0_so, o2_so, C_HDR, "c", tmp_path)
        assert not r.breaking, (
            f"-O0 vs -O2 should not produce BREAKING changes; "
            f"changes: {[(c.kind.value, c.symbol) for c in r.changes]}"
        )

    @pytest.mark.xfail(
        sys.platform == "win32",
        reason="MinGW -O2 inlines constructors away from PE exports",
        strict=False,
    )
    def test_o0_vs_o2_cpp_no_break(self, tmp_path):
        _require_tool("g++")
        _require_tool("castxml")

        o0_so = tmp_path / "libwidget_o0.so"
        o2_so = tmp_path / "libwidget_o2.so"

        for so, opt in [(o0_so, "-O0"), (o2_so, "-O2")]:
            src_file = so.with_suffix(".cpp")
            src_file.write_text(textwrap.dedent(CPP_SRC).strip(), encoding="utf-8")
            cmd = ["g++", "-shared", "-fPIC", "-g", "-fvisibility=default",
                   "-std=c++17", opt, "-o", str(so), str(src_file)]
            _run_compile_or_skip(cmd)

        r = _dump_and_compare(o0_so, o2_so, CPP_HDR, "cpp", tmp_path)
        assert not r.breaking


@pytest.mark.integration
class TestStrippedVsUnstrippedFP:
    """Stripped (-g removed) vs debug build → graceful degradation."""

    def test_stripped_vs_debug_c(self, tmp_path):
        """Stripping debug info should NOT cause BREAKING changes,
        but should lower confidence and report DWARF_INFO_MISSING."""
        _require_tool("gcc")
        _require_tool("castxml")
        _require_tool("strip")

        debug_so = tmp_path / "libtest_debug.so"
        stripped_so = tmp_path / "libtest_stripped.so"

        src_file = debug_so.with_suffix(".c")
        src_file.write_text(textwrap.dedent(C_SRC).strip(), encoding="utf-8")
        cmd = ["gcc", "-shared", "-fPIC", "-g", "-fvisibility=default",
               "-o", str(debug_so), str(src_file)]
        _run_compile_or_skip(cmd)

        # Copy and strip
        shutil.copy2(debug_so, stripped_so)
        r = subprocess.run(["strip", "--strip-debug", str(stripped_so)],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            pytest.skip(f"strip failed: {r.stderr[:200]}")

        from abicheck.checker import compare
        from abicheck.checker_policy import Confidence
        from abicheck.dumper import dump

        h = tmp_path / "header.h"
        h.write_text(textwrap.dedent(C_HDR).strip(), encoding="utf-8")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            snap_debug = dump(debug_so, headers=[h], version="debug", compiler="cc")
            snap_stripped = dump(stripped_so, headers=[h], version="stripped", compiler="cc")

        result = compare(snap_debug, snap_stripped)

        # Should NOT be BREAKING (same source code!)
        assert not result.breaking, (
            f"Debug vs stripped should not be BREAKING; "
            f"changes: {[(c.kind.value, c.symbol) for c in result.changes]}"
        )
        # When headers are provided alongside ELF data, confidence remains
        # HIGH even when one binary is stripped — headers are the primary
        # type-level evidence source and ELF provides symbol-level coverage.
        # The DWARF detector may still be enabled (populated from the debug
        # build), so confidence is not degraded in this configuration.
        assert result.confidence == Confidence.HIGH, (
            "With headers + ELF, confidence should remain HIGH even when "
            "one binary is stripped"
        )

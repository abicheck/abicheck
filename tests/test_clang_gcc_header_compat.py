"""Every clang AST pass must parse GCC's own headers.

GCC >= 11 headers (unconditionally GCC's ``omp.h``) use the deallocator form
``__attribute__((__malloc__ (dealloc)))``, which clang rejects as a hard
error. A run that puts a GCC resource directory on the include path -- which
any castxml-compatible setup does -- used to lose the whole semantic header
graph to it, silently. The fix is one define carried by every AST pass, and
never by a macro pass.
"""

from __future__ import annotations

import glob
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck._compiler_options import (
    CLANG_GCC_HEADER_COMPAT_DEFINES,
    clang_ast_dump_tail,
)
from abicheck.buildsource.build_evidence import CompileUnit


def _unit() -> CompileUnit:
    return CompileUnit(id="u", source="a.c", argv=["cc", "-c", "a.c"], language="C")


class TestEveryAstBuilderCarriesTheShim:
    def test_tail(self) -> None:
        tail = clang_ast_dump_tail("x.h")
        assert tail[-1] == "x.h"
        for d in CLANG_GCC_HEADER_COMPAT_DEFINES:
            assert d in tail

    def test_l2_header_command(self) -> None:
        from abicheck.extract.headers.ast_config import _build_clang_header_command

        for force_cpp in (False, True):
            cmd = _build_clang_header_command(
                "clang", "gnu", [], Path("agg.h"), force_cpp=force_cpp
            )
            assert set(CLANG_GCC_HEADER_COMPAT_DEFINES) <= set(cmd)
            assert cmd[-1] == "agg.h"

    def test_l4_ast_command_but_not_macro_command(self) -> None:
        from abicheck.buildsource.source_extractors.clang import (
            build_clang_command,
            build_clang_macro_command,
        )

        ast_cmd = build_clang_command(_unit(), Path("a.c"))
        macro_cmd = build_clang_macro_command(_unit(), Path("a.c"))
        assert set(CLANG_GCC_HEADER_COMPAT_DEFINES) <= set(ast_cmd)
        # A define on a -E -dM/-dD pass would be recorded as the TU's own macro.
        assert not set(CLANG_GCC_HEADER_COMPAT_DEFINES) & set(macro_cmd)

    def test_l5_run_clang_ast_dump(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from abicheck.buildsource import clang_ast_run

        seen: list[list[str]] = []

        def fake_run(cmd, **_k):
            seen.append(list(cmd))
            raise OSError("stop")

        monkeypatch.setattr(clang_ast_run.deadline, "run_bounded", fake_run)
        diags: list[str] = []
        assert (
            clang_ast_run.run_clang_ast_dump(
                "clang", ["a.c"], cwd=None, diagnostics=diags
            )
            is None
        )
        assert len(seen) == 1
        assert set(CLANG_GCC_HEADER_COMPAT_DEFINES) <= set(seen[0])
        assert diags


_GCC_OMP = sorted(glob.glob("/usr/lib/gcc/*/*/include/omp.h"))


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
@pytest.mark.skipif(not _GCC_OMP, reason="no GCC omp.h on this host")
@pytest.mark.parametrize("lang", ["c", "c++"])
def test_real_gcc_omp_h_parses_and_keeps_declarations(
    tmp_path: Path, lang: str
) -> None:
    omp = Path(_GCC_OMP[-1])
    hdr = tmp_path / "t.h"
    hdr.write_text("#include <omp.h>\n#include <stdlib.h>\n")
    base = ["clang", "-x", lang, "-I", str(omp.parent)]
    bare = subprocess.run(
        [*base, "-fsyntax-only", str(hdr)], capture_output=True, text=True
    )
    fixed = subprocess.run(
        [*base, *clang_ast_dump_tail(str(hdr))], capture_output=True, text=True
    )
    # Negative control: when this clang still rejects GCC's deallocator
    # form, that rejection is the only thing the shim changes.
    if bare.returncode != 0:
        assert "__malloc__" in bare.stderr, bare.stderr
    assert fixed.returncode == 0, fixed.stderr
    names: set[str] = set()

    def walk(n: dict) -> None:
        if isinstance(n.get("name"), str):
            names.add(n["name"])
        for c in n.get("inner", []) or []:
            walk(c)

    walk(json.loads(fixed.stdout))
    assert {"omp_alloc", "omp_free", "malloc"} <= names

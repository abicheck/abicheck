# SPDX-License-Identifier: Apache-2.0
"""Behavioral half of the preprocessor_scan gap (plan §3 #8).

``test_engine_primitive_call_sites.py`` proves ``run_preprocessor_scan``
structurally has no caller outside ``scan_engine.py``. This module proves
the positive side concretely: it really does capture a private-header
leak via a real ``clang -E`` invocation. Needs a real ``clang++`` on PATH
(``ClangPreprocessorExtractor``), so it's ``integration``-marked per this
package's own cheapness rule, even though the check itself is otherwise
pure-Python.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.preprocessor_scan import (
    ClangPreprocessorExtractor,
    run_preprocessor_scan,
)

from .gaps import EXPECTED_GAPS
from .runner import invoke_cli


@pytest.mark.integration
def test_run_preprocessor_scan_finds_a_private_header_leak(tmp_path: Path) -> None:
    assert "preprocessor_scan" in EXPECTED_GAPS
    if not ClangPreprocessorExtractor().available():
        pytest.skip("clang++ not on PATH")

    private = tmp_path / "widget_impl.h"
    private.write_text("#define WIDGET_MAGIC 42\n", encoding="utf-8")
    public = tmp_path / "widget.h"
    public.write_text(
        '#include "widget_impl.h"\nint widget_get(void);\n', encoding="utf-8"
    )
    src = tmp_path / "widget.c"
    src.write_text(
        '#include "widget.h"\nint widget_get(void) { return WIDGET_MAGIC; }\n'
    )

    unit = CompileUnit(
        id="tu",
        source=str(src),
        directory=str(tmp_path),
        argv=["cc", "-c", str(src)],
        language="C",
    )
    result = run_preprocessor_scan(
        BuildEvidence(compile_units=[unit]), public_headers=[str(public)]
    )
    assert result.ran, result.skipped_reason
    assert result.leaks, "expected a captured private-header leak"
    assert any(str(private) == leak.leaked_header for leak in result.leaks), (
        result.leaks
    )


def test_compare_has_no_preprocessor_scan_surface() -> None:
    result = invoke_cli("compare", "--help-all")
    assert result.exit_code == 0
    assert "preprocessor-scan" not in result.output.lower()
    assert "preprocessor_scan" not in result.output.lower()

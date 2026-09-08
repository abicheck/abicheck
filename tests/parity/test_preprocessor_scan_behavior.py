# SPDX-License-Identifier: Apache-2.0
"""Behavioral half of the (now closed) preprocessor_scan gap (plan §3 #8,
Phase 2b).

``test_engine_primitive_call_sites.py`` proves ``run_preprocessor_scan``
has exactly two production callers now: ``scan_engine.py`` and
``workflows/pattern_preprocessor_scan.py``. This module proves the positive
side concretely: it really does capture a private-header leak via a real
``clang -E`` invocation. Needs a real ``clang++`` on PATH
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
from .runner import compare_json, invoke_cli


@pytest.mark.integration
def test_run_preprocessor_scan_finds_a_private_header_leak(tmp_path: Path) -> None:
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


def test_preprocessor_scan_no_longer_a_registered_gap() -> None:
    """`gaps.py` is the migration's definition of done: a capability
    `compare` has reached must not still be listed as scan-only."""
    assert "preprocessor_scan" not in EXPECTED_GAPS


def test_compare_has_no_preprocessor_scan_cli_surface() -> None:
    """ADR-068 D4/D5: the stage is automatic, evidence-gated per side --
    never a CLI opt-in flag, even now that `compare` reaches it."""
    result = invoke_cli("compare", "--help-all")
    assert result.exit_code == 0
    assert "preprocessor-scan" not in result.output.lower()
    assert "preprocessor_scan" not in result.output.lower()


def _widget_build_source(
    tmp_path: Path, side: str, *, leaks: bool
) -> tuple[object, Path]:
    """Build one side's `widget.h`/`widget.c` fixture + embedded `BuildSourcePack`.

    `leaks` controls whether the public header pulls in the private one --
    both sides get their own real, independently-preprocessable header/TU
    pair so the fold below has real OLD evidence to compare against,
    rather than the pre-existing side's absence of build evidence alone
    (which would just read `not_evaluated`, per the correctness crux
    `_fold_evolution`'s own docstring names).
    """
    from abicheck.buildsource.pack import BuildSourcePack

    side_dir = tmp_path / side
    side_dir.mkdir()
    private = side_dir / "widget_impl.h"
    private.write_text("#define WIDGET_MAGIC 42\n", encoding="utf-8")
    public = side_dir / "widget.h"
    include = '#include "widget_impl.h"\n' if leaks else ""
    public.write_text(f"{include}int widget_get(void);\n", encoding="utf-8")
    src = side_dir / "widget.c"
    body = "WIDGET_MAGIC" if leaks else "0"
    src.write_text(f'#include "widget.h"\nint widget_get(void) {{ return {body}; }}\n')

    unit = CompileUnit(
        id="tu",
        source=str(src),
        directory=str(side_dir),
        argv=["cc", "-c", str(src)],
        language="C",
    )
    build_source = BuildSourcePack(
        root=side_dir, build_evidence=BuildEvidence(compile_units=[unit])
    )
    return build_source, public


@pytest.mark.integration
def test_compare_surfaces_a_preprocessor_scan_leak_introduced_in_new(
    tmp_path: Path,
) -> None:
    """The same leak the direct `run_preprocessor_scan` test above proves,
    now reached through `compare()`'s own automatic pipeline stage: OLD's
    header does not leak, NEW's does, and both sides carry real, evaluable
    build evidence -- so the fold reads `introduced`, not `not_evaluated`."""
    if not ClangPreprocessorExtractor().available():
        pytest.skip("clang++ not on PATH")

    from abicheck.model import AbiSnapshot, Function, ScopeOrigin
    from abicheck.serialization import snapshot_to_json

    old_build, old_public = _widget_build_source(tmp_path, "old", leaks=False)
    new_build, new_public = _widget_build_source(tmp_path, "new", leaks=True)

    old_fn = Function(
        name="widget_get",
        mangled="widget_get",
        return_type="int",
        source_header=str(old_public),
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    new_fn = Function(
        name="widget_get",
        mangled="widget_get",
        return_type="int",
        source_header=str(new_public),
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    old = AbiSnapshot(
        library="libwidget.so",
        version="1.0",
        functions=[old_fn],
        build_source=old_build,
    )
    new = AbiSnapshot(
        library="libwidget.so",
        version="2.0",
        functions=[new_fn],
        build_source=new_build,
    )

    old_path = tmp_path / "old.abi.json"
    new_path = tmp_path / "new.abi.json"
    old_path.write_text(snapshot_to_json(old), encoding="utf-8")
    new_path.write_text(snapshot_to_json(new), encoding="utf-8")

    report = compare_json(old_path, new_path)
    block = report["pattern_preprocessor_scan"]
    leak_key = f"{new_public}|{new_public.parent / 'widget_impl.h'}"
    assert block["preprocessor"]["header_leak_evolution"].get(leak_key) == "introduced"

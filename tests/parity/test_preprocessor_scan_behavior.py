# SPDX-License-Identifier: Apache-2.0
"""Behavioral half of the preprocessor_scan gap (plan §3 #8), **now closed**
(Phase 2b): ``compare`` reaches the same S2 preprocessor pre-scan ``scan``
always ran, automatically, on both sides, with no new flag.

``test_engine_primitive_call_sites.py`` proves ``run_preprocessor_scan``
structurally has ``workflows/lexical_prescan.py`` as a second, legitimate
caller now. This module proves the positive side concretely: it really does
capture a private-header leak via a real ``clang -E`` invocation, both
directly (the same production function ``scan`` calls) and through
``compare``'s own JSON report. Needs a real ``clang++`` on PATH
(``ClangPreprocessorExtractor``), so the positive-detection tests are
``integration``-marked per this package's own cheapness rule, even though the
check itself is otherwise pure-Python; the evidence-gating (no build info at
all) test needs no compiler and stays unmarked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.preprocessor_scan import (
    ClangPreprocessorExtractor,
    run_preprocessor_scan,
)

from .gaps import EXPECTED_GAPS
from .runner import compare_json, write_snapshot


def test_the_gap_is_closed_and_deregistered() -> None:
    assert "preprocessor_scan" not in EXPECTED_GAPS


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


def _plain_snapshot_path(tmp_path: Path, name: str):
    from abicheck.model import AbiSnapshot

    return write_snapshot(
        AbiSnapshot(library=name, version="1.0"), tmp_path / f"{name}.abi.json"
    )


def _write_widget_tree(root: Path) -> tuple[Path, Path]:
    """The same private-header-leak fixture as the direct-call test above,
    on disk under *root*, plus a ``compile_commands.json`` so ``compare
    --sources`` can auto-discover L3 evidence for it. Returns
    ``(public_header, source_tree_root)``."""
    root.mkdir(parents=True, exist_ok=True)
    private = root / "widget_impl.h"
    private.write_text("#define WIDGET_MAGIC 42\n", encoding="utf-8")
    public = root / "widget.h"
    public.write_text(
        '#include "widget_impl.h"\nint widget_get(void);\n', encoding="utf-8"
    )
    src = root / "widget.c"
    src.write_text(
        '#include "widget.h"\nint widget_get(void) { return WIDGET_MAGIC; }\n'
    )
    compile_db = root / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(root),
                    "command": f"cc -c {src.name}",
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return public, root


def _compile_widget_lib(src: Path, out: Path) -> None:
    """Compile *src* into a real shared library at *out* (skips on failure).

    A real ELF operand is needed here, not a bare stub JSON snapshot: the
    CLI's own inline-source-embed path (``_embed_inline_source_side``) dumps
    the ``--sources`` tree *against the given library operand*, which needs
    something real to dump.
    """
    r = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(out), str(src)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        pytest.skip(f"library compile failed: {r.stderr[:200]}")


@pytest.mark.integration
def test_compare_surfaces_preprocessor_prescan_leak(tmp_path: Path) -> None:
    if not ClangPreprocessorExtractor().available():
        pytest.skip("clang++ not on PATH")

    public, tree = _write_widget_tree(tmp_path / "tree")
    old = tmp_path / "old.so"
    new = tmp_path / "new.so"
    _compile_widget_lib(tree / "widget.c", old)
    _compile_widget_lib(tree / "widget.c", new)

    report = compare_json(
        old, new, "-H", str(public), "--sources", str(tree), "--depth", "build"
    )
    block = report["preprocessor_prescan"]
    for side in ("old", "new"):
        assert block[side]["ran"], block[side].get("skipped_reason")
        leaked = {leak["leaked_header"] for leak in block[side]["leaks"]}
        assert str(tree / "widget_impl.h") in leaked, block[side]


def test_compare_preprocessor_prescan_degrades_honestly_with_no_build_evidence(
    tmp_path: Path,
) -> None:
    """No `--sources`/`--build-info` at all: the block is still present
    (always computed), but each side honestly reports `ran: false` with a
    `skipped_reason` rather than a silently-clean, never-ran scan. A bare
    stub JSON snapshot is fine here -- no inline embed is asked for."""
    old = _plain_snapshot_path(tmp_path, "old")
    new = _plain_snapshot_path(tmp_path, "new")

    report = compare_json(old, new)
    block = report["preprocessor_prescan"]
    for side in ("old", "new"):
        assert block[side]["ran"] is False
        assert block[side]["skipped_reason"]

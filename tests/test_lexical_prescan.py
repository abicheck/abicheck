# SPDX-License-Identifier: Apache-2.0
"""Property-style tests for ``workflows/lexical_prescan.py``'s evidence-gating
contract (docs/contribute/plans/one-comparison-product.md §3 rows 6/8, §6
Phase 2b).

Related bug class: ``tests/regressions/manifest.py``'s
``evidence.silent_degradation_to_clean_verdict`` -- missing evidence must
never silently read as a clean/absent result. This module states that
invariant for the two pre-scans directly, across the evidence matrix
(headers present/absent x sources present/absent x build evidence
present/absent), rather than pinning one fixed-input example: every
combination must produce a populated, honestly-labelled block, and none may
ever raise, touch ``changes``, or move the verdict.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.checker_policy import Verdict
from abicheck.checker_types import DiffResult
from abicheck.model import AbiSnapshot
from abicheck.workflows.lexical_prescan import (
    compute_pattern_prescan_side,
    compute_preprocessor_prescan_side,
    fold_lexical_prescan,
)


def _blank_result() -> DiffResult:
    return DiffResult(old_version="1.0", new_version="1.0", library="libfoo.so")


def _snapshot() -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0")


@pytest.mark.parametrize(
    "has_headers,has_sources",
    list(itertools.product([False, True], repeat=2)),
)
def test_pattern_prescan_side_never_raises_and_reports_honestly(
    tmp_path: Path, has_headers: bool, has_sources: bool
) -> None:
    headers: list[Path] = []
    if has_headers:
        h = tmp_path / "pub.h"
        h.write_text("virtual void f();\n", encoding="utf-8")
        headers = [h]
    sources = None
    if has_sources:
        sources = tmp_path / "src"
        sources.mkdir()
        (sources / "impl.cpp").write_text(
            "template class Widget<int>;\n", encoding="utf-8"
        )

    result = compute_pattern_prescan_side(headers, sources, None, None)
    cov = result.coverage()
    if has_headers or has_sources:
        assert result.files_scanned >= 1
        assert cov.status.value == "present"
    else:
        assert result.files_scanned == 0
        assert cov.status.value == "not_collected"


@pytest.mark.parametrize("has_build_evidence", [False, True])
def test_preprocessor_prescan_side_never_raises_and_reports_honestly(
    has_build_evidence: bool,
) -> None:
    build = None
    if has_build_evidence:
        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

        build = BuildEvidence(
            compile_units=[
                CompileUnit(id="tu", source="x.c", directory="/tmp", argv=[])
            ]
        )
    result = compute_preprocessor_prescan_side(build, [], None)
    if has_build_evidence:
        # No clang on a headless CI runner degrades to `ran=False` with a
        # reason too -- both are honest, neither is silently "clean".
        if result.ran:
            assert result.skipped_reason == ""
        else:
            assert result.skipped_reason
    else:
        assert result.ran is False
        assert "no L3 build evidence" in result.skipped_reason


@pytest.mark.parametrize(
    "has_headers,has_sources,has_changed_paths",
    list(itertools.product([False, True], repeat=3)),
)
def test_fold_lexical_prescan_always_populates_both_blocks(
    tmp_path: Path,
    has_headers: bool,
    has_sources: bool,
    has_changed_paths: bool,
) -> None:
    """Across the whole evidence matrix: the fold never raises, always
    stamps both DiffResult fields with an `old`/`new` pair, and never
    touches `changes`/`verdict` -- these two pre-scans are advisory
    evidence attachments, never a diffed finding (see this module's own
    docstring and workflows/lexical_prescan.py's for why)."""
    headers: list[Path] = []
    if has_headers:
        h = tmp_path / "pub.h"
        h.write_text("virtual void f();\n", encoding="utf-8")
        headers = [h]
    sources = None
    if has_sources:
        sources = tmp_path / "src"
        sources.mkdir()
        (sources / "impl.cpp").write_text("int x;\n", encoding="utf-8")
    changed_paths = ("pub.h",) if has_changed_paths else None

    result = _blank_result()
    original_changes = list(result.changes)
    original_verdict = result.verdict

    fold_lexical_prescan(
        result,
        old_headers=headers,
        new_headers=headers,
        old_sources=sources,
        new_sources=sources,
        old_snapshot=_snapshot(),
        new_snapshot=_snapshot(),
        depth=None,
        changed_paths=changed_paths,
    )

    assert result.pattern_prescan is not None
    assert set(result.pattern_prescan) == {"old", "new"}
    assert result.preprocessor_prescan is not None
    assert set(result.preprocessor_prescan) == {"old", "new"}
    # Advisory only: never folded into the diffed change set or the verdict.
    assert result.changes == original_changes
    assert result.verdict == original_verdict
    assert result.verdict == Verdict.NO_CHANGE


def test_fold_lexical_prescan_is_deterministic_for_identical_inputs(
    tmp_path: Path,
) -> None:
    """Running the fold twice over identical evidence yields identical
    JSON-ready output -- no timestamp/ordering nondeterminism leaking into
    a report field."""
    h = tmp_path / "pub.h"
    h.write_text("virtual void f();\ntemplate class Widget<int>;\n", encoding="utf-8")

    def _run() -> DiffResult:
        r = _blank_result()
        fold_lexical_prescan(
            r,
            old_headers=[h],
            new_headers=[h],
            old_sources=None,
            new_sources=None,
            old_snapshot=_snapshot(),
            new_snapshot=_snapshot(),
            depth=None,
        )
        return r

    first = _run()
    second = _run()
    assert first.pattern_prescan == second.pattern_prescan
    assert first.preprocessor_prescan == second.preprocessor_prescan

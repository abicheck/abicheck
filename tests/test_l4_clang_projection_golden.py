"""Phase 4 projection equivalence over the checked-in L4 clang golden.

Plan ``l4-l2-extraction-convergence`` Phase 4, ADR-063 amendment "L4 clang
payload comparability" (test plan item 1). The current-side (``producer_version``
``"0.13"``) surface of each catalog C++ TU is read from
``tests/golden/l4_clang/surface_0_13.jsonl``, generated from the pre-change
emitter by ``scripts/gen_l4_clang_golden.py``. The projection side is computed
here, live, with the spike's configuration (``scripts/measure_l4_clang_projection.py``).

The expected pinned set is the literal ``EXPECTED_PINNED`` below. It is not
computed from the projection helper. If the measured set disagrees, the test
fails: do not edit the literal to match the code.

The golden's clang version is recorded in its header. The two tests that depend
on clang output skip, with the reason named, when the live clang differs, so a
different toolchain is never reported as drift or as a measurement.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _l4_clang_golden_support import GOLDEN, REPO_ROOT, G, M, golden_header_and_rows

#: Pinned ``(kind, field)`` pairs: status ``equal`` with at least two paired
#: entities. Written by hand from the amendment's candidate set and the rule A3
#: (a field that differs on any TU is dropped). ``typedef.visibility`` and
#: ``typedef.api_relevant`` are equal by construction (``base.entity_from_typedef``
#: hard-codes them), not by evidence; the rule admits them, and the plan's status
#: note says so.
EXPECTED_PINNED: frozenset[tuple[str, str]] = frozenset(
    {
        ("typedef", "qualified_name"),
        ("typedef", "value"),
        ("typedef", "visibility"),
        ("typedef", "api_relevant"),
    }
)

#: A pinned field needs at least this many paired entities; one paired variable
#: establishes nothing (the spike's "Not established" rows).
MIN_PAIRED = 2


def _require_golden_clang() -> None:
    """Skip unless the live clang is the one the golden was generated with."""
    from abicheck.buildsource.source_extractors.clang import ClangSourceExtractor

    if not ClangSourceExtractor().available():
        pytest.skip("clang not installed")
    header, _ = golden_header_and_rows()
    live = G.live_clang_version()
    if live != header["clang_version"]:
        pytest.skip(
            f"golden was generated with {header['clang_version']!r}; "
            f"live clang is {live!r}, so the golden is not comparable here"
        )


def _current_views(row: dict) -> list:
    return [
        M.EntityView(
            kind=e["kind"], qualified_name=e["qualified_name"], fields=e["fields"]
        )
        for e in row["entities"]
    ]


def _projected_views(cpp: Path, extractor) -> list:
    """The spike's projection configuration, applied to one TU's AST."""
    from abicheck.buildsource.source_extractors.clang_projection import (
        PublicSurfaceClangParser,
        project_clang_surface,
    )

    case_dir = cpp.parent
    dump = extractor.dump_for(G.compile_unit_for(cpp))
    try:
        assert dump.ast is not None, f"clang AST dump failed for {cpp}"
        parser = PublicSurfaceClangParser(
            dump.ast,
            set(),
            set(),
            public_header_paths=[],
            public_dir_paths=[str(case_dir)],
            target_triple=None,
            no_binary_evidence=True,
            is_cxx=True,
        )
        projected = project_clang_surface(parser)
    finally:
        dump.release()
    return [M.view_of(e) for e in projected if e.kind in M.CANDIDATE_FIELDS]


def test_golden_header_pins_the_pre_change_producer() -> None:
    from abicheck.buildsource.source_extractors.clang import CLANG_EXTRACTOR_VERSION

    header, rows = golden_header_and_rows()
    assert header["producer_version"] == "0.13"
    # The golden is the pre-change side: the producer must still be 0.13 here.
    # A bump to 0.14 is the b2 change and must regenerate this file first.
    assert CLANG_EXTRACTOR_VERSION == "0.13"
    assert header["tu_count"] == len(rows) == 253


@pytest.mark.integration
@pytest.mark.slow
def test_golden_is_the_output_of_its_generator() -> None:
    """Regenerating from this checkout reproduces the checked-in bytes exactly."""
    _require_golden_clang()
    from abicheck.buildsource.source_extractors.clang import (
        CLANG_EXTRACTOR_VERSION,
    )

    assert CLANG_EXTRACTOR_VERSION == "0.13"
    regenerated = G.build_golden_text(M.corpus_cases(REPO_ROOT / "catalog" / "cases"))
    assert regenerated == GOLDEN.read_text(encoding="utf-8"), (
        "golden is stale: run scripts/gen_l4_clang_golden.py and review the diff"
    )


@pytest.mark.integration
@pytest.mark.slow
def test_pinned_set_matches_the_measured_projection_equivalence() -> None:
    from abicheck.buildsource.source_extractors.clang import ClangSourceExtractor

    _require_golden_clang()
    extractor = ClangSourceExtractor()
    _, rows = golden_header_and_rows()

    comparisons = []
    failures = []
    for row in rows:
        if "error" in row:
            failures.append(row["tu"])
            continue
        cpp = REPO_ROOT / row["compile_unit"]["source"]
        comparisons.append(
            M.compare_views(
                row["tu"], _current_views(row), _projected_views(cpp, extractor)
            )
        )

    assert failures == [], "golden TUs that failed to dump: " + ", ".join(failures)
    assert len(comparisons) == 253

    measured = {
        (r.kind, r.field)
        for r in M.aggregate(comparisons)
        if r.status == "equal" and r.compared >= MIN_PAIRED
    }
    assert measured == set(EXPECTED_PINNED), (
        f"measured pinned set {sorted(measured)} != expected {sorted(EXPECTED_PINNED)}"
    )


@pytest.mark.parametrize(
    "script", ["gen_l4_clang_golden", "measure_l4_clang_projection"]
)
def test_script_import_leaves_sys_path_unchanged(script: str) -> None:
    """Loading a golden-tool script must not mutate the process's import path.

    Oracle: the ``sys.path`` list itself, snapshotted before and after a fresh
    load of the script file, not the shared loader's own behaviour.
    """
    import importlib.util
    import sys

    from _l4_clang_golden_support import SCRIPTS

    before = list(sys.path)
    spec = importlib.util.spec_from_file_location(
        f"_probe_{script}", SCRIPTS / f"{script}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while being built.
    probe = f"_probe_{script}"
    sys.modules[probe] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[probe]
    assert sys.path == before, f"importing {script}.py changed sys.path"

"""Phase 4 projection equivalence over the checked-in L4 clang golden.

Plan ``l4-l2-extraction-convergence`` Phase 4, ADR-063 amendment "L4 clang
payload comparability" (test plan item 1). The current-side (``producer_version``
``"0.13"``) surface of each catalog C++ TU is read from
``tests/golden/l4_clang/surface_0_13.jsonl``, which was generated from the
pre-change emitter by ``scripts/gen_l4_clang_golden.py``. The projection side is
computed here, live, with the spike's configuration
(``scripts/measure_l4_clang_projection.py``). Per TU the two sides are compared
per ``(kind, field)``; a field whose status is ``equal`` on every TU with
evidence is pinned.

The expected pinned set is the literal ``EXPECTED_PINNED`` below. It is not
computed from the projection helper. If the measured set disagrees, the test
fails: do not edit the literal to match the code.

Marked ``integration``: needs clang. Skips when clang is absent.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = REPO_ROOT / "tests" / "golden" / "l4_clang" / "surface_0_13.jsonl"
TOOL = REPO_ROOT / "scripts" / "measure_l4_clang_projection.py"

#: Pinned ``(kind, field)`` pairs: status ``equal`` with at least two paired
#: entities. Written by hand from the amendment's candidate set and the
#: measurement's ``equal`` rows (ADR-063 A3: a field that differs on any TU is
#: dropped). ``typedef.visibility`` and ``typedef.api_relevant`` are equal by
#: construction (``base.entity_from_typedef`` hard-codes them), not by evidence;
#: they are listed because the rule admits them, and the status note says so.
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


def _load_tool():
    spec = importlib.util.spec_from_file_location("measure_l4_clang_projection", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = _load_tool()


def _golden_rows() -> tuple[dict, list[dict]]:
    lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    return header, [json.loads(line) for line in lines[1:]]


def _current_views(row: dict) -> list:
    return [
        M.EntityView(
            kind=e["kind"], qualified_name=e["qualified_name"], fields=e["fields"]
        )
        for e in row["entities"]
    ]


def _projected_views(cpp: Path, extractor) -> list:
    """The spike's projection configuration, applied to one TU's AST."""
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors import base
    from abicheck.dumper_clang import _ClangAstParser

    case_dir = cpp.parent
    roots = [str(case_dir)]
    cu = CompileUnit(
        id=f"cu://{case_dir.name}/{cpp.name}",
        source=str(cpp),
        directory=str(case_dir),
        language="CXX",
        standard="c++17",
        argv=["c++", "-std=c++17", "-fPIC", "-fvisibility=hidden", "-c", str(cpp)],
    )
    dump = extractor.dump_for(cu)
    try:
        assert dump.ast is not None, f"clang AST dump failed for {cpp}"
        parser = _ClangAstParser(
            dump.ast,
            set(),
            set(),
            public_header_paths=[],
            public_dir_paths=roots,
            target_triple=None,
            no_binary_evidence=True,
            is_cxx=True,
        )
        projected = [base.entity_from_function(f) for f in parser.parse_functions()]
        projected += [base.entity_from_variable(v) for v in parser.parse_variables()]
        projected += [base.entity_from_record(r) for r in parser.parse_types()]
        projected += [base.entity_from_enum(e) for e in parser.parse_enums()]
        projected += [
            base.entity_from_typedef(name, target)
            for name, target in parser.parse_typedefs_qualified().items()
        ]
    finally:
        dump.release()
    return [M.view_of(e) for e in projected if e.kind in M.CANDIDATE_FIELDS]


def test_golden_header_pins_the_pre_change_producer() -> None:
    from abicheck.buildsource.source_extractors.clang import CLANG_EXTRACTOR_VERSION

    header, rows = _golden_rows()
    assert header["producer_version"] == "0.13"
    # The golden is the pre-change side: the producer must still be 0.13 here.
    # A bump to 0.14 is the b2 change and must regenerate this file first.
    assert CLANG_EXTRACTOR_VERSION == "0.13"
    assert header["tu_count"] == len(rows) == 253


@pytest.mark.integration
def test_pinned_set_matches_the_measured_projection_equivalence() -> None:
    from abicheck.buildsource.source_extractors.clang import ClangSourceExtractor

    extractor = ClangSourceExtractor()
    if not extractor.available():
        pytest.skip("clang not installed")
    _, rows = _golden_rows()

    comparisons = []
    failures = []
    for row in rows:
        cpp = REPO_ROOT / "catalog" / "cases" / row["tu"]
        if "error" in row:
            failures.append(row["tu"])
            continue
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

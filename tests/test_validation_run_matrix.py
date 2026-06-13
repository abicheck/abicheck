"""Tests for the real-world validation harness helpers.

``logical_name`` now lives in the shared engine (``conda_harness``) that both
``run_matrix.py`` and ``validate.py`` build on; these cases pin its behaviour
on sonames the curated manifest relies on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path("validation/scripts/conda_harness.py")
_RUN_MATRIX_SCRIPT = Path("validation/scripts/run_matrix.py")


def _load_logical_name():
    spec = importlib.util.spec_from_file_location("conda_harness", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.logical_name


def _load_run_matrix():
    spec = importlib.util.spec_from_file_location("run_matrix", _RUN_MATRIX_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_logical_name_handles_standard_soname_suffixes() -> None:
    logical_name = _load_logical_name()

    assert logical_name("/pkg/lib/libprotobuf.so.33.5.0") == "libprotobuf"
    assert logical_name("/pkg/lib/libssl.so.3") == "libssl"


def test_logical_name_strips_version_embedded_before_so_suffix() -> None:
    logical_name = _load_logical_name()

    assert logical_name("/pkg/lib/libcapnp-1.4.0.so") == "libcapnp"
    assert logical_name("/pkg/lib/libkj-async-1.3.0.so") == "libkj-async"


def test_run_matrix_records_source_layer_asymmetry() -> None:
    run_matrix = _load_run_matrix()

    assert run_matrix.side_layers("sym") == ["L0"]
    assert run_matrix.side_layers("dwarf") == ["L0", "L1"]
    assert run_matrix.evidence_asymmetry("sym->sym") == "symmetric"
    assert run_matrix.evidence_asymmetry("dwarf->sym") == "old-rich/new-poor"
    assert run_matrix.evidence_asymmetry("sym->dwarf") == "old-poor/new-rich"


def test_run_matrix_scores_expected_vs_actual_verdicts() -> None:
    run_matrix = _load_run_matrix()

    assert run_matrix.normalize_verdict("COMPATIBLE_WITH_RISK") == "COMPATIBLE"
    assert run_matrix.normalize_verdict("API_BREAK") == "BREAKING"
    assert run_matrix.comparison_status("BREAKING", "API_BREAK") == "MATCH"
    assert run_matrix.comparison_status("COMPATIBLE", "BREAKING") == "ABICHECK_STRICTER"
    assert run_matrix.comparison_status("BREAKING", "NO_CHANGE") == "ABICHECK_WEAKER"


def test_run_matrix_record_has_remeasurement_metadata() -> None:
    run_matrix = _load_run_matrix()
    row = {
        "pair": "TBB_2021_2022",
        "library": "oneTBB",
        "old_ver": "2021.9",
        "new_ver": "2022.0",
        "expectation": "COMPATIBLE_WITH_RISK",
        "note": "known risk pair",
    }

    rec = run_matrix.make_record(
        row,
        logical="libtbb",
        old_path="/old/libtbb.so",
        new_path="/new/libtbb.so",
        mode="dwarf->sym",
        exit_code=0,
        seconds=1.234,
        stderr="",
        data={
            "verdict": "COMPATIBLE_WITH_RISK",
            "summary": {"breaking": 0, "risk": 2},
            "release_recommendation": "manual_review",
            "layer_coverage": [{"layer": "L0"}, {"layer": "L1"}],
        },
    )

    assert rec["schema_version"] == "run_matrix.v2"
    assert rec["component"] == "real-world-matrix"
    assert rec["case_id"] == "TBB_2021_2022__libtbb"
    assert rec["platform"]
    assert rec["source_layers"] == ["L0", "L1"]
    assert rec["old_source_layers"] == ["L0", "L1"]
    assert rec["new_source_layers"] == ["L0"]
    assert rec["evidence_asymmetry"] == "old-rich/new-poor"
    assert rec["seconds"] == 1.23
    assert rec["got"] == "COMPATIBLE_WITH_RISK"
    assert rec["normalized_expected"] == "COMPATIBLE"
    assert rec["normalized_got"] == "COMPATIBLE"
    assert rec["comparison_status"] == "MATCH"
    assert rec["counts"] == {"breaking": 0, "risk": 2}
    assert rec["release_recommendation"] == "manual_review"
    assert rec["layer_coverage"] == [{"layer": "L0"}, {"layer": "L1"}]


def test_run_matrix_run_metadata_summarizes_modes() -> None:
    run_matrix = _load_run_matrix()

    meta = run_matrix.make_run_metadata(
        [
            {"mode": "sym->sym", "comparison_status": "MATCH"},
            {"mode": "dwarf->sym", "comparison_status": "ABICHECK_WEAKER"},
        ],
        [{"pair": "A"}, {"pair": "B"}],
    )

    assert meta["schema_version"] == "run_matrix.v2"
    assert meta["runner"] == "validation/scripts/run_matrix.py"
    assert meta["platform"]
    assert meta["manifest_pairs"] == 2
    assert meta["comparisons"] == 2
    assert meta["modes"] == ["dwarf->sym", "sym->sym"]
    assert meta["comparison_status_counts"] == {"ABICHECK_WEAKER": 1, "MATCH": 1}
    assert meta["results_file"] == "validation/data/results.json"

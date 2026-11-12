"""Reproducibility tests for the formal-methods experiments in ``formal/``.

These pin the *results* the experiment report (``formal/EXPERIMENTS.md``)
states, so a change that invalidates one turns a test red instead of leaving
the report silently stale. They are conformance and model-checking runs, not
proofs about the Python implementation.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def test_e7_count_trigger_sound_without_same_call_additions() -> None:
    chk = _load(FORMAL / "tla" / "check_disposition.py")
    states, trace = chk.check(5, 3, adds=False)
    assert trace is None
    assert states > 1000  # the space was actually explored


def test_e7_count_trigger_misses_drop_plus_bucket_addition() -> None:
    """Shortest counterexample: a step drops finding 1 and puts a *new*
    object 2 into a bucket in the same call; the counts balance, so the
    trigger does not fire and finding 1 is neither visible, bucketed nor
    recorded."""
    chk = _load(FORMAL / "tla" / "check_disposition.py")
    _, trace = chk.check(4, 2, adds=True)
    assert trace is not None and len(trace) == 2
    (c0, b0, l0, s0, *_), (c1, b1, l1, s1, *_) = trace
    vanished = s1 - (c1 | b1 | l1)
    assert vanished and vanished <= c0
    assert len(c1) + len(b1 - b0) == len(c0)  # why the count trigger stays silent


def test_lean_layout_table_covers_declared_domain() -> None:
    rows = json.loads(
        (FORMAL / "lean" / "layout_table.json").read_text(encoding="utf-8")
    )["rows"]
    types = {"char", "short", "int", "double"}
    bases = {tuple(r["base"]) for r in rows}
    assert len(bases) == 4 + 16 + 64
    for r in rows:
        assert set(r["base"]) <= types and r["inserted"] in types
        assert 0 <= r["insert_at"] <= len(r["base"])
        assert len(r["new_offsets"]) == len(r["old_offsets"]) + 1
    assert len(rows) == sum((len(b) + 1) * 4 for b in bases)


@pytest.mark.integration
def test_e5_e6_layout_model_extraction_and_compare_agree(tmp_path: Path) -> None:
    if shutil.which("gcc") is None or shutil.which("castxml") is None:
        pytest.skip("gcc and castxml required")
    exp = _load(FORMAL / "experiments" / "layout_experiments.py")
    assert exp.main(tmp_path) == 0
    s = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    n = 2 * s["cases"]
    assert s["E6a_model_vs_gcc"] == {"agree": n}
    assert s["E6b_extraction_vs_gcc"] == {
        "castxml_headers": {"agree": n},
        "dwarf_only": {"agree": n},
    }
    mismatches = {
        k: v
        for k, v in s["E5_compare_vs_model"].items()
        if "pred=True:found=False" in k or "pred=False:found=True" in k
    }
    assert mismatches == {}
    # E4: the edit class alone does not determine the outcome
    assert s["E4_class_outcomes"]["insert_before_field:NOT moved"] > 0
    assert s["E4_class_outcomes"]["append:size SAME"] > 0

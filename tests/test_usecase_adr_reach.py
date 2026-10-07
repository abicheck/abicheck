# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``usecase_paths.py adr-reach`` / ``ratchet`` (``scripts/usecase_adr_reach.py``).

ADR reach is checked over hand-built recordings whose expected status per
ADR is written out by hand from the definitions -- one ADR per status, plus
sibling cases at each boundary -- not recomputed with the module's helpers.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import usecase_adr_reach as ar  # noqa: E402
import usecase_paths as up  # noqa: E402


def _recording(
    runs: dict[str, tuple[str | None, list[str]]], sources=("scenarios",)
) -> dict:
    return {
        "schema_version": up.SCHEMA_VERSION,
        "sources": list(sources),
        "revision": "abc",
        "failures": {},
        "inventory": {
            "abicheck/a.py": ["fa", "ga"],
            "abicheck/b.py": ["fb"],
            "abicheck/c.py": ["fc"],
        },
        "runs": {
            rid: {"use_case": uc, "functions": fns} for rid, (uc, fns) in runs.items()
        },
    }


ADRS = {
    "001-own-path.md": "Implemented in `abicheck/a.py`.",
    "002-other-path.md": "See abicheck/b.py and abicheck/gone.py.",
    "003-built-unexercised.md": "Lives in abicheck/c.py.",
    "004-moved.md": "Was abicheck/old/thing.py.",
    "005-process.md": "We decided to write tests first.",
    "006-uncited-but-run.md": "abicheck/a.py",
}
USE_CASES = [
    {"id": "UC-A", "docs": ["docs/contribute/adr/001-own-path.md"]},
    {"id": "UC-B", "note": "Follows ADR-002 and ADR-0045 (not ADR-004)."},
]


def test_every_status_is_assigned_from_its_definition() -> None:
    rec = _recording(
        {
            "SC-1": ("UC-A", ["abicheck/a.py::fa"]),
            "SC-2": ("UC-C", ["abicheck/b.py::fb"]),
        }
    )
    rows = {r.adr: r for r in ar.adr_reach(rec, ADRS, USE_CASES)}
    assert {k: r.status for k, r in rows.items()} == {
        "001-own-path.md": "reached",  # cited by UC-A, and UC-A runs a.py
        "002-other-path.md": "reached-elsewhere",  # cited by UC-B; only UC-C runs b.py
        "003-built-unexercised.md": "not-reached",  # c.py exists, nothing runs it
        "004-moved.md": "untraced",  # its only named file is gone
        "005-process.md": "no-code-refs",
        "006-uncited-but-run.md": "reached-elsewhere",  # runs, but no use case cites it
    }
    assert rows["002-other-path.md"].missing_files == ["abicheck/gone.py"]
    assert rows["002-other-path.md"].use_cases == ["UC-B"]
    assert (
        rows["001-own-path.md"].functions_reached,
        rows["001-own-path.md"].functions_total,
    ) == (1, 2)


def test_citation_matches_the_adr_number_exactly() -> None:
    cites = ar.adr_citations(USE_CASES, sorted(ADRS))
    assert cites["002-other-path.md"] == {"UC-B"}
    # "ADR-0045" must not cite 004; "ADR-004" in prose does
    assert cites["004-moved.md"] == {"UC-B"}
    assert "005-process.md" not in cites


def test_a_run_of_the_citing_use_case_outside_the_named_files_does_not_reach() -> None:
    rec = _recording({"SC-1": ("UC-A", ["abicheck/b.py::fb"])})
    rows = {r.adr: r.status for r in ar.adr_reach(rec, ADRS, USE_CASES)}
    assert rows["001-own-path.md"] == "not-reached"


def test_code_refs_are_deduplicated_and_ignore_non_python_paths() -> None:
    text = "abicheck/x.py, abicheck/x.py, abicheck/schemas/y.json, `abicheck/z/w.py`"
    assert ar.adr_code_refs(text) == ["abicheck/x.py", "abicheck/z/w.py"]


def test_real_adr_tree_classifies_every_adr() -> None:
    adrs = ar.adr_files(ROOT)
    assert adrs and all(re.match(r"^\d{3}", n) for n in adrs)
    rec = _recording({})
    rows = ar.adr_reach(rec, adrs, ar.load_registry_use_cases(ROOT))
    assert len(rows) == len(adrs)
    assert {r.status for r in rows} <= set(ar.STATUSES)


# -- ratchet ---------------------------------------------------------------


def test_ratchet_reports_growth_and_shrinkage() -> None:
    baseline = ar.unreached_baseline_payload({"x::a", "x::b"}, ["scenarios"], None)
    result = ar.ratchet_unreached({"x::b", "x::c"}, baseline, ["scenarios"])
    assert (result.grown, result.shrunk, result.ok) == (["x::c"], ["x::a"], False)
    assert ar.ratchet_unreached({"x::a"}, baseline, ["scenarios"]).ok


def test_ratchet_refuses_a_baseline_from_other_sources() -> None:
    baseline = ar.unreached_baseline_payload(set(), ["scenarios"], None)
    with pytest.raises(ValueError, match="not comparable"):
        ar.ratchet_unreached(set(), baseline, ["flows", "scenarios"])


def test_ratchet_cli_writes_then_gates(tmp_path: Path) -> None:
    rec_path = tmp_path / "rec.json"
    base_path = tmp_path / "base.json"
    rec_path.write_text(json.dumps(_recording({"SC": ("UC-A", ["abicheck/a.py::fa"])})))
    assert (
        up.main(["ratchet", str(rec_path), "--baseline", str(base_path), "--write"])
        == 0
    )
    written = json.loads(base_path.read_text())
    assert written["unreached"] == [
        "abicheck/a.py::ga",
        "abicheck/b.py::fb",
        "abicheck/c.py::fc",
    ]
    assert (
        up.main(["ratchet", str(rec_path), "--baseline", str(base_path), "--strict"])
        == 0
    )
    # the run stops reaching fa: growth, a failure only under --strict
    rec_path.write_text(json.dumps(_recording({})))
    assert up.main(["ratchet", str(rec_path), "--baseline", str(base_path)]) == 0
    assert (
        up.main(["ratchet", str(rec_path), "--baseline", str(base_path), "--strict"])
        == 1
    )


def test_ratchet_cli_refuses_a_recording_with_failed_runs(tmp_path: Path) -> None:
    rec = _recording({})
    rec["failures"] = {"SC-X": "boom"}
    path = tmp_path / "rec.json"
    path.write_text(json.dumps(rec))
    assert up.main(["ratchet", str(path), "--baseline", str(tmp_path / "b.json")]) == 1


def test_committed_unreached_baseline_is_well_formed() -> None:
    doc = ar.load_unreached_baseline()
    assert doc["sources"] == ["scenarios"]
    assert doc["count"] == len(doc["unreached"]) > 0
    assert doc["unreached"] == sorted(set(doc["unreached"]))
    assert all(fid.startswith("abicheck/") and "::" in fid for fid in doc["unreached"])


def test_the_ratchet_runs_weekly_and_never_on_a_pull_request() -> None:
    text = (ROOT / ".github/workflows/usecase-paths.yml").read_text(encoding="utf-8")
    pr_job, full_job = text.split("\n  full:\n")
    assert "ratchet" not in pr_job.split("\njobs:\n", 1)[1]
    assert "github.event_name != 'pull_request'" in full_job
    assert "ratchet usecase-paths-scenarios.json --strict --step-summary" in full_job
    assert "record --source scenarios --out usecase-paths-scenarios.json" in full_job
    assert "adr-reach usecase-paths.json" in full_job

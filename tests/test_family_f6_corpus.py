# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Family F6 (H6): offline tests of the real-library corpus gate.

The network half runs in ``.github/workflows/real-library-corpus.yml``; this
file checks the pure gate over fabricated per-pair results, the corpus file's
schema and provenance, and replays historical false-positive shapes (the
``*mutant*`` tests) to prove the gate would have caught them.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VALID = REPO / "skills-src" / "evaluation" / "validation"
_spec = importlib.util.spec_from_file_location(
    "run_compat_corpus", VALID / "scripts" / "run_compat_corpus.py"
)
assert _spec and _spec.loader
rcc = importlib.util.module_from_spec(_spec)
sys.modules["run_compat_corpus"] = rcc
_spec.loader.exec_module(rcc)


def _entry(
    pair: str, expected: str = "COMPATIBLE", kinds: list[str] | None = None
) -> dict:
    return {
        "pair": pair,
        "library": "lib",
        "pkg": "lib",
        "old_ver": "1.0.0",
        "new_ver": "1.0.1",
        "old_file": "a.conda",
        "new_file": "b.conda",
        "expected": expected,
        "expected_break_kinds": kinds or [],
        "ground_truth_source": "test",
    }


def _result(**libs: dict[str, int]) -> dict:
    return {
        "libraries": {
            name: {"verdict": "X", "counts_by_kind": c} for name, c in libs.items()
        }
    }


# ---------------------------------------------------------------- gate logic


@pytest.mark.parametrize("kind", ["func_removed", "var_removed", "type_size_changed"])
def test_compatible_pair_with_a_break_fails(kind: str) -> None:
    gate = rcc.evaluate_gate([_entry("p")], {"p": _result(libx={kind: 1})}, None)
    assert not gate.passed
    assert "known-compatible" in gate.failures[0]


def test_compatible_pair_with_api_break_fails() -> None:
    from abicheck.checker_policy import API_BREAK_KINDS

    kind = sorted(k.value for k in API_BREAK_KINDS)[0]
    gate = rcc.evaluate_gate([_entry("p")], {"p": _result(libx={kind: 1})}, None)
    assert not gate.passed


def test_clean_compatible_pair_passes_without_baseline_report_only() -> None:
    res = {"p": _result(libx={"func_added": 400, "exported_not_public": 900})}
    gate = rcc.evaluate_gate([_entry("p")], res, None)
    assert gate.passed, gate.failures
    assert gate.baseline_present is False


def test_drift_beyond_tolerance_fails() -> None:
    res = {"p": _result(libx={"func_added": 40})}
    gate = rcc.evaluate_gate([_entry("p")], res, {"p": {"func_added": 10}})
    assert not gate.passed
    assert "drift" in gate.failures[0]


def test_new_kind_appearing_beyond_absolute_tolerance_fails() -> None:
    res = {"p": _result(libx={"func_added": 10, "visibility_leak": 6})}
    gate = rcc.evaluate_gate(
        [_entry("p")], res, {"p": {"func_added": 10}}, abs_tolerance=5
    )
    assert not gate.passed


@pytest.mark.parametrize(
    ("old", "new"), [(10, 15), (100, 110), (100, 90), (0, 5), (3, 0)]
)
def test_within_tolerance_passes(old: int, new: int) -> None:
    res = {"p": _result(libx={"func_added": new})}
    gate = rcc.evaluate_gate(
        [_entry("p")],
        res,
        {"p": {"func_added": old}},
        rel_tolerance=0.10,
        abs_tolerance=5,
    )
    assert gate.passed, gate.failures


def test_drift_oracle_matches_documented_rule_on_small_domain() -> None:
    """Exhaustive small-domain check against the documented rule, restated
    independently: fail iff |new-old| exceeds both 5 and 10% of old."""
    for old in range(0, 120, 7):
        for new in range(0, 140, 3):
            gate = rcc.evaluate_gate(
                [_entry("p")],
                {"p": _result(l={"func_added": new})},
                {"p": {"func_added": old}},
                rel_tolerance=0.10,
                abs_tolerance=5,
            )
            expect_fail = abs(new - old) > 5 and abs(new - old) > old / 10
            assert gate.passed is not expect_fail, (old, new)


def test_missing_baseline_entry_is_report_only() -> None:
    gate = rcc.evaluate_gate([_entry("p")], {"p": _result(libx={"func_added": 9})}, {})
    assert gate.passed
    assert gate.drift_notes


def test_unevaluated_pair_fails() -> None:
    for res in (
        {},
        {"p": {"error": "fetch failed", "libraries": {}}},
        {"p": {"libraries": {}}},
    ):
        assert not rcc.evaluate_gate([_entry("p")], res, None).passed


def test_incompatible_pair_requires_a_break_and_documented_kinds() -> None:
    corpus = [_entry("b", "BREAKING", ["func_removed"])]
    assert not rcc.evaluate_gate(
        corpus, {"b": _result(l={"func_added": 1})}, None
    ).passed
    assert not rcc.evaluate_gate(
        corpus, {"b": _result(l={"var_removed": 1})}, None
    ).passed
    assert rcc.evaluate_gate(corpus, {"b": _result(l={"func_removed": 2})}, None).passed


def test_baseline_round_trip_passes_its_own_results(tmp_path: Path) -> None:
    res = {"p": _result(a={"func_added": 3}, b={"visibility_leak": 2, "func_added": 1})}
    path = tmp_path / "bl.json"
    path.write_text(json.dumps(rcc.baseline_from_results(res)))
    baseline = rcc.load_baseline(path)
    assert baseline == {"p": {"func_added": 4, "visibility_leak": 2}}
    assert rcc.evaluate_gate([_entry("p")], res, baseline).passed


def test_main_gates_stored_results_and_exits_nonzero(tmp_path: Path) -> None:
    corpus = tmp_path / "c.json"
    corpus.write_text(json.dumps({"schema": rcc.CORPUS_SCHEMA, "pairs": [_entry("p")]}))
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "p.json").write_text(json.dumps(_result(l={"func_removed": 1})))
    args = [
        "--corpus",
        str(corpus),
        "--baseline",
        str(tmp_path / "none.json"),
        "--out-dir",
        str(tmp_path / "o"),
        "--results-dir",
        str(rdir),
    ]
    assert rcc.main(args) == 1
    gate = json.loads((tmp_path / "o" / "gate.json").read_text())
    assert gate["passed"] is False and gate["baseline_present"] is False
    (rdir / "p.json").write_text(json.dumps(_result(l={"func_added": 1})))
    assert rcc.main(args) == 0


# ------------------------------------------------------------ corpus schema


def _corpus_doc() -> dict:
    return json.loads(
        (VALID / "data" / "compat_corpus.json").read_text(encoding="utf-8")
    )


def test_corpus_file_is_valid_and_every_entry_cites_ground_truth() -> None:
    doc = _corpus_doc()
    assert rcc.validate_corpus(doc) == []
    for p in doc["pairs"]:
        assert p["ground_truth_source"].startswith("data/manifest.json")


def test_corpus_ground_truth_matches_cited_manifest_entry() -> None:
    manifest = {
        e["pair"]: e
        for e in json.loads(
            (VALID / "data" / "manifest.json").read_text(encoding="utf-8")
        )
    }
    for p in _corpus_doc()["pairs"]:
        src = manifest[p["pair"]]
        assert p["expected"] == src["expectation"], p["pair"]
        for key in ("pkg", "old_ver", "new_ver", "old_file", "new_file"):
            assert p[key] == src[key], (p["pair"], key)


def test_corpus_has_both_expectations() -> None:
    assert {p["expected"] for p in _corpus_doc()["pairs"]} == set(rcc.EXPECTATIONS)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("ground_truth_source"),
        lambda p: p.update(ground_truth_source="  "),
        lambda p: p.update(expected="MAYBE"),
        lambda p: p.update(expected_break_kinds=["func_removed"]),
        lambda p: p.pop("new_file"),
    ],
)
def test_schema_validator_rejects_malformed_entries(mutate) -> None:
    entry = _entry("p")
    mutate(entry)
    assert rcc.validate_corpus({"schema": rcc.CORPUS_SCHEMA, "pairs": [entry]})


def test_schema_validator_rejects_duplicates_and_empty() -> None:
    assert rcc.validate_corpus({"schema": rcc.CORPUS_SCHEMA, "pairs": []})
    assert rcc.validate_corpus(
        {"schema": rcc.CORPUS_SCHEMA, "pairs": [_entry("p"), _entry("p")]}
    )


def test_checked_in_baseline_if_present_covers_known_pairs() -> None:
    path = VALID / "data" / "compat_corpus_baseline.json"
    if not path.is_file():
        pytest.skip("no baseline recorded yet (drift half is report-only)")
    baseline = rcc.load_baseline(path)
    assert baseline is not None
    assert set(baseline) <= {p["pair"] for p in _corpus_doc()["pairs"]}


# ------------------------------------------------- historical-FP mutants


def test_mutant_1283_mkl_false_removals_fail_the_gate() -> None:
    """#1283: a compatible MKL release reported mass false symbol removals.
    Injecting that shape into a known-compatible pair must fail the gate."""
    res = {
        "mkl": _result(
            libmkl_core={
                "func_removed": 180,
                "func_removed_elf_only": 40,
                "func_added": 12,
            }
        )
    }
    gate = rcc.evaluate_gate([_entry("mkl")], res, {"mkl": {"func_added": 12}})
    assert not gate.passed
    assert any("known-compatible" in f for f in gate.failures)


def test_mutant_1411_exported_not_public_flood_fails_on_drift() -> None:
    """#1411: a flood of false ``exported_not_public`` findings. None is
    BREAKING, so only the baseline drift half can see it -- it must."""
    baseline = {"lib": {"exported_not_public": 3, "func_added": 20}}
    res = {"lib": _result(libx={"exported_not_public": 503, "func_added": 20})}
    gate = rcc.evaluate_gate([_entry("lib")], res, baseline)
    assert not gate.passed
    assert any("exported_not_public" in f for f in gate.failures)


def test_mutant_incompatible_pair_silenced_fails() -> None:
    """A regression that hides a documented SONAME-bump break (verdict
    reduced to additions only) must fail on a known-incompatible pair."""
    res = {"b": _result(libx={"func_added": 30, "soname_bump_recommended": 1})}
    assert not rcc.evaluate_gate([_entry("b", "BREAKING")], res, None).passed

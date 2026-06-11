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

"""Offline tests for the unified validation harness source adapters.

Exercises ``manifest_pairs``/``tracker_pairs`` normalisation only — no network,
conda, or abicheck. The fetch/extract/compare engine is covered separately in
``test_conda_harness.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path("validation/scripts/validate.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("validate", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_manifest_pairs_normalises_curated_entries() -> None:
    # The committed manifest must map cleanly to normalised pairs with the
    # expectation carried as expected_verdict and exact builds pinned.
    mod = _load_module()
    pairs = mod.manifest_pairs(None, "linux-64")

    assert pairs, "manifest should yield at least one pair"
    sample = pairs[0]
    assert set(sample) >= {
        "pair",
        "library",
        "pkg",
        "old_ver",
        "new_ver",
        "expected_verdict",
        "subdir",
    }
    # expectation -> expected_verdict, and exact build files are pinned.
    assert all(p["expected_verdict"] in {"COMPATIBLE", "BREAKING"} for p in pairs)
    assert all(p["old_file"] and p["new_file"] for p in pairs)
    assert sample["subdir"] == "linux-64"


def test_manifest_pairs_filters_by_library() -> None:
    mod = _load_module()
    everything = mod.manifest_pairs(None, "linux-64")
    libs = {p["library"] for p in everything}
    target = next(iter(libs))

    filtered = mod.manifest_pairs(target, "linux-64")
    assert filtered
    assert {p["library"] for p in filtered} == {target}
    assert len(filtered) <= len(everything)


def test_tracker_pairs_reads_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tracker_oracle/*.json is gitignored, so build a tiny oracle on the fly and
    # point the adapter at it.
    mod = _load_module()
    oracle = {
        "library": "demo",
        "pairs": [
            {
                "pair": "demo_1.0_to_1.1",
                "old_ver": "1.0",
                "new_ver": "1.1",
                "expected_verdict": "BREAKING",
            }
        ],
    }
    (tmp_path / "demo.json").write_text(json.dumps(oracle))
    monkeypatch.setattr(mod, "ORACLE_DIR", tmp_path)

    pairs = mod.tracker_pairs("demo", pkg="libdemo", subdir="linux-64")
    assert len(pairs) == 1
    p = pairs[0]
    assert p["pair"] == "demo_1.0_to_1.1"
    assert p["pkg"] == "libdemo"  # --pkg override applied
    assert p["expected_verdict"] == "BREAKING"
    assert p["subdir"] == "linux-64"


def test_tracker_pairs_missing_oracle_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "ORACLE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        mod.tracker_pairs("nope", pkg=None, subdir="linux-64")


def test_is_evidence_limited() -> None:
    # A type-level-only oracle break (removed_symbols == 0) on a binary with no
    # usable type evidence that abicheck didn't flag is an evidence limit, not a
    # false negative.
    mod = _load_module()
    base = {"expected_verdict": "BREAKING", "removed_symbols": 0}

    assert mod._is_evidence_limited(base, "COMPATIBLE", {"has_type_evidence": False})
    # had usable DWARF coverage -> abicheck could see types, so a miss WOULD be a real FN
    assert not mod._is_evidence_limited(base, "COMPATIBLE", {"has_type_evidence": True})
    # symbols were removed -> visible without DWARF, so not evidence-limited
    assert not mod._is_evidence_limited(
        {"expected_verdict": "BREAKING", "removed_symbols": 2},
        "COMPATIBLE",
        {"has_type_evidence": False},
    )
    # abicheck already flagged it breaking -> not a miss at all
    assert not mod._is_evidence_limited(base, "BREAKING", {"has_type_evidence": False})
    # oracle says compatible -> nothing to excuse
    assert not mod._is_evidence_limited(
        {"expected_verdict": "COMPATIBLE", "removed_symbols": 0},
        "COMPATIBLE",
        {"has_type_evidence": False},
    )


def test_run_validation_max_pairs_reports_only_attempted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With --max-pairs the loop stops early; pairs it never reached must not be
    # reported as UNCOMPARABLE (which would make a smoke run look broken).
    mod = _load_module()
    monkeypatch.setattr(mod, "PARITY_DIR", tmp_path)
    monkeypatch.setattr(mod, "query_conda", lambda pkg: {"files": []})
    monkeypatch.setattr(
        mod,
        "evaluate_pair",
        lambda pair, api, subdir, tmp, idx, evidence=None: "COMPATIBLE",
    )

    pairs = [
        {
            "pair": f"p{i}",
            "pkg": "x",
            "old_ver": "1",
            "new_ver": "2",
            "expected_verdict": "COMPATIBLE",
            "subdir": "linux-64",
        }
        for i in range(5)
    ]
    report = mod.run_validation(pairs, max_pairs=2, label="t")

    assert report["ran_pairs"] == 2
    assert len(report["rows"]) == 2  # only the 2 attempted, not all 5
    assert {r["pair"] for r in report["rows"]} == {"p0", "p1"}
    assert all(r["status"] != "UNCOMPARABLE" for r in report["rows"])


def test_tracker_pairs_carry_scope_corroboration_fields(tmp_path, monkeypatch) -> None:
    # tracker_pairs must thread the oracle's public-surface counts through so the
    # scope-divergence gate can corroborate against them.
    mod = _load_module()
    oracle = {
        "library": "libtiff",
        "pairs": [
            {
                "pair": "libtiff_4.0.6_to_4.0.7",
                "old_ver": "4.0.6",
                "new_ver": "4.0.7",
                "expected_verdict": "COMPATIBLE",
                "removed_symbols": 0,
                "backward_compat_pct": 100.0,
                "soname_changed": False,
            }
        ],
    }
    odir = tmp_path / "tracker_oracle"
    odir.mkdir()
    (odir / "libtiff.json").write_text(json.dumps(oracle))
    monkeypatch.setattr(mod, "ORACLE_DIR", odir)
    pairs = mod.tracker_pairs("libtiff", None, "linux-64")
    assert pairs[0]["backward_compat_pct"] == 100.0
    assert pairs[0]["removed_symbols"] == 0
    assert pairs[0]["soname_changed"] is False


def test_is_scope_divergence_true_for_internal_only_break() -> None:
    mod = _load_module()
    pair = {
        "expected_verdict": "COMPATIBLE",
        "removed_symbols": 0,
        "backward_compat_pct": 100.0,
    }
    ev = {"scope_divergent": True}
    assert mod._is_scope_divergence(pair, "BREAKING", ev) is True


def test_is_scope_divergence_requires_oracle_corroboration() -> None:
    # Even if the engine flags every breaking finding scope-sensitive, the oracle
    # must independently confirm no public symbol was removed.
    mod = _load_module()
    ev = {"scope_divergent": True}
    # oracle saw a public removal -> not a scope divergence
    pair = {
        "expected_verdict": "COMPATIBLE",
        "removed_symbols": 2,
        "backward_compat_pct": 98.0,
    }
    assert mod._is_scope_divergence(pair, "BREAKING", ev) is False


def test_is_scope_divergence_false_without_engine_flag() -> None:
    mod = _load_module()
    pair = {
        "expected_verdict": "COMPATIBLE",
        "removed_symbols": 0,
        "backward_compat_pct": 100.0,
    }
    assert (
        mod._is_scope_divergence(pair, "BREAKING", {"scope_divergent": False}) is False
    )


def test_is_scope_divergence_false_when_oracle_also_breaking() -> None:
    mod = _load_module()
    pair = {
        "expected_verdict": "BREAKING",
        "removed_symbols": 0,
        "backward_compat_pct": 100.0,
    }
    assert (
        mod._is_scope_divergence(pair, "BREAKING", {"scope_divergent": True}) is False
    )

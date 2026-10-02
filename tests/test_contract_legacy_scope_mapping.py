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

"""The legacy scope flags and their ``--contract`` replacements, through the CLI.

One-comparison-product Phase 9 replaces ``--no-scope-public-headers`` with
``--contract all`` and ``--scope-public-headers`` with ``--contract public``.
Before either flag can be deleted, the mapping has to hold on real input, so
this runs every case of the labelled FP-rate corpus
(``scripts/check_fp_rate.py``) through the public ``compare`` CLI under each
spelling. The oracle is the corpus's own ground-truth label (internal noise
vs. real break), not the code under test.

Measuring this found a defect: ``--contract all`` alone left the legacy
header-origin filter running at its *default* value, ahead of the evaluator,
so ten internal-change cases that ``--no-scope-public-headers`` reported as
breaking (exit 4) came out clean (exit 0) under the domain that was meant to
evaluate everything.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.serialization import snapshot_to_json

_GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_fp_rate.py"
_spec = importlib.util.spec_from_file_location("check_fp_rate_mapping", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
fp_gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fp_gate
_spec.loader.exec_module(fp_gate)

CORPUS = fp_gate.CORPUS

#: Real breaks `--contract public` reports only as the coverage floor (exit 1)
#: rather than as a break: the spelling-only same-leaf shape, `public`'s one
#: explained unresolved loss (docs/contribute/known-gaps.md). Never exit 0.
PUBLIC_COVERAGE_ONLY = frozenset({"ambiguous_namespaced_leaf_spelling_only"})

_SPELLINGS = {
    "no_scope": ["--no-scope-public-headers"],
    "scope": ["--scope-public-headers"],
    "contract_all": ["--contract", "all"],
    "contract_public": ["--contract", "public"],
}


@pytest.fixture(scope="module")
def exits(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, int]]:
    root = tmp_path_factory.mktemp("mapping")
    runner = CliRunner()
    out: dict[str, dict[str, int]] = {}
    for case in CORPUS:
        old, new = case.build()
        old_p, new_p = root / f"{case.name}.old.json", root / f"{case.name}.new.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        out[case.name] = {
            name: runner.invoke(
                main,
                [
                    "compare",
                    str(old_p),
                    str(new_p),
                    *args,
                    "-o",
                    f"json={root / 'r.json'}",
                ],
            ).exit_code
            for name, args in _SPELLINGS.items()
        }
    return out


def test_corpus_has_both_labels() -> None:
    """Vacuity guard: the oracle distinguishes something."""
    labels = {case.internal_noise for case in CORPUS}
    assert labels == {True, False}


def test_contract_all_is_the_exact_no_scope_alias(
    exits: dict[str, dict[str, int]],
) -> None:
    diffs = {name: e for name, e in exits.items() if e["contract_all"] != e["no_scope"]}
    assert not diffs, (
        f"--contract all disagrees with --no-scope-public-headers: {diffs}"
    )


def test_contract_all_reports_internal_breaks(exits: dict[str, dict[str, int]]) -> None:
    """The regression itself: the opt-out domain still sees internal changes."""
    surfaced = [
        c.name
        for c in CORPUS
        if c.internal_noise and exits[c.name]["contract_all"] == 4
    ]
    assert surfaced, "no internal-noise case breaks under --contract all"


def test_contract_public_never_loses_a_real_break(
    exits: dict[str, dict[str, int]],
) -> None:
    lost = {}
    for case in CORPUS:
        if case.internal_noise:
            continue
        e = exits[case.name]
        assert e["scope"] in (2, 4), f"legacy scoping lost {case.name}: {e}"
        expected = 1 if case.name in PUBLIC_COVERAGE_ONLY else e["scope"]
        if e["contract_public"] != expected:
            lost[case.name] = e
    assert not lost, f"--contract public changed a real break's outcome: {lost}"


def test_contract_public_fabricates_no_break_on_internal_noise(
    exits: dict[str, dict[str, int]],
) -> None:
    bad = {
        c.name: exits[c.name]
        for c in CORPUS
        if c.internal_noise
        and (
            exits[c.name]["scope"] != 0
            or exits[c.name]["contract_public"] not in (0, 1)
        )
    }
    assert not bad, f"internal noise scored as a break: {bad}"


def test_the_coverage_only_set_is_still_real() -> None:
    names = {c.name for c in CORPUS if not c.internal_noise}
    assert PUBLIC_COVERAGE_ONLY <= names

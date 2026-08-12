# Copyright 2026 Nikolay Petrov
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

"""Fast-lane validation of the L3/L4/L5 build/source-only example corpus.

Cases 152-162 and 194-195 (see ``scripts/gen_l3l4l5_examples.py``) demonstrate ABI/API
failures that *only* build context (L3), source-replay surfaces (L4), or the
derived source graph (L5) can see. They do not fit the ``v1``/``v2`` binary-diff
shape: each ships a hand-built pair of evidence-model fixtures
(``old.json`` + ``new.json``) so the corpus is validated here **compiler-free**.

Each case's ``ground_truth.json`` entry declares ``expected_kinds`` (the diff
findings it must produce) and ``min_evidence`` (which routes it to the L3 build
diff, the L4 source diff, or the L5 graph diff). This module asserts the emitted
kinds and keeps the committed fixtures in sync with their generator.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from abicheck.buildsource.build_diff import diff_build_evidence
from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.source_abi import SourceAbiSurface
from abicheck.buildsource.source_diff import diff_source_abi
from abicheck.buildsource.source_graph import (
    SourceGraphSummary,
    diff_source_graph_findings,
)

_REPO = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO / "examples"
_GT = json.loads((_EXAMPLES / "ground_truth.json").read_text())["verdicts"]

#: The L3/L4/L5 corpus: cases shipping an old.json/new.json fixture pair.
_CASES = sorted(
    name
    for name, info in _GT.items()
    if info.get("fixtures") == ["old.json", "new.json"]
)


def _load(case_name: str, side: str) -> dict:
    return json.loads((_EXAMPLES / case_name / f"{side}.json").read_text())


def _emitted_kinds(case_name: str) -> list[str]:
    """Run the diff selected by the case's evidence tier and return kind values."""
    tier = _GT[case_name]["min_evidence"]
    old, new = _load(case_name, "old"), _load(case_name, "new")
    if tier == "L3":
        changes = diff_build_evidence(
            BuildEvidence.from_dict(old), BuildEvidence.from_dict(new)
        )
    elif tier == "L4":
        changes = diff_source_abi(
            SourceAbiSurface.from_dict(old), SourceAbiSurface.from_dict(new)
        )
    elif tier == "L5":
        changes = diff_source_graph_findings(
            SourceGraphSummary.from_dict(old), SourceGraphSummary.from_dict(new)
        )
    else:  # pragma: no cover - guards a mis-tagged fixture
        raise AssertionError(f"{case_name}: unexpected min_evidence {tier!r}")
    return [c.kind.value for c in changes]


def test_corpus_is_non_empty() -> None:
    # Guards against the discovery query silently matching nothing (which would
    # turn every parametrized assertion below into a vacuous pass).
    assert len(_CASES) == 13, f"expected the 13 L3/L4/L5 cases, found {_CASES}"


@pytest.mark.parametrize("case_name", _CASES)
def test_case_emits_expected_kinds(case_name: str) -> None:
    info = _GT[case_name]
    want = set(info["expected_kinds"])
    assert want, f"{case_name}: no expected_kinds declared"
    got = set(_emitted_kinds(case_name))
    missing = want - got
    assert not missing, (
        f"{case_name}: expected kinds not emitted: {sorted(missing)}; got {sorted(got)}"
    )


@pytest.mark.parametrize("case_name", _CASES)
def test_case_fixtures_exist(case_name: str) -> None:
    for side in ("old", "new"):
        path = _EXAMPLES / case_name / f"{side}.json"
        assert path.is_file(), f"{case_name}: missing committed fixture {path.name}"


def _load_generator():
    path = _REPO / "scripts" / "gen_l3l4l5_examples.py"
    spec = importlib.util.spec_from_file_location("gen_l3l4l5_examples", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixtures_match_generator() -> None:
    """The committed fixtures must equal what the writer produces (no drift)."""
    gen = _load_generator()
    for case_name, (_layer, old, new) in gen.build_cases().items():
        assert _load(case_name, "old") == old, f"{case_name}: old.json drifted"
        assert _load(case_name, "new") == new, f"{case_name}: new.json drifted"

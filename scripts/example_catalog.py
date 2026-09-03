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

"""Single source of truth for resolving an example-catalog case id to its
on-disk directory -- Phase 3 of the examples/catalog split
(docs/contribute/plans/examples-catalog-split.md).

Before this module, roughly a dozen consumers (`scripts/gen_examples_docs.py`,
`scripts/benchmark_comparison.py`, `scripts/check_ai_readiness.py`, every
`validation/scripts/run_*_examples.py` runner, and the various
`tests/test_*_examples.py` / `tests/validate_examples.py` fast-lane tests)
each independently derived `EXAMPLES_DIR = <repo root> / "examples"` and then
joined a case id onto it by hand (`EXAMPLES_DIR / case_name`). That's fine
while every case lives directly under `examples/`, but Phase 4's planned
physical split (`examples/caseNN_*` -> one of
`catalog/{rules,patterns,case-studies,capabilities}/...`) would otherwise
require editing every one of those call sites in lockstep, in one
un-reviewable diff.

Routing through `case_dir(case_id)` instead means Phase 4 only ever changes
*this* module -- once cases physically move, this resolves each id via
`ground_truth.json["taxonomy"]`'s `entity`/`scenario_kind` fields (already
computed by Phase 1/2, see `gen_catalog_taxonomy.py`) to pick the right
`catalog/` subtree, and every caller below is unaffected. Until Phase 4
lands, this is a thin, behavior-preserving wrapper: every path it returns is
byte-identical to what the hand-rolled `EXAMPLES_DIR / case_name` joins
already produced.

Pure stdlib, importable before `pip install -e .` (mirrors
`check_ai_readiness.py`'s own constraint -- it's one of this module's
consumers and is itself the first CI step).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = ROOT / "examples"
GROUND_TRUTH_PATH = EXAMPLES_DIR / "ground_truth.json"


def case_dir(case_id: str) -> Path:
    """Resolve a case id (e.g. ``"case01_symbol_removal"``) to its
    directory. Today this is always ``examples/<case_id>``; Phase 4 is the
    only change this module is meant to absorb without touching callers.
    """
    return EXAMPLES_DIR / case_id


def load_ground_truth() -> dict[str, object]:
    """The parsed contents of ``examples/ground_truth.json``."""
    return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))


def all_case_ids(ground_truth: dict[str, object] | None = None) -> list[str]:
    """Every case id in ``ground_truth.json["verdicts"]``, in file order --
    the same order every existing hand-rolled iteration already relies on
    (`ground_truth.json` is never key-sorted, see `gen_catalog_taxonomy.py`'s
    own note on this).
    """
    gt = ground_truth if ground_truth is not None else load_ground_truth()
    verdicts: dict[str, object] = gt["verdicts"]  # type: ignore[assignment]
    return list(verdicts.keys())


def iter_case_dirs(
    ground_truth: dict[str, object] | None = None,
) -> list[tuple[str, Path]]:
    """``(case_id, case_dir(case_id))`` for every case, in file order."""
    ids = all_case_ids(ground_truth)
    return [(case_id, case_dir(case_id)) for case_id in ids]

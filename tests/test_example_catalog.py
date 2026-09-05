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

"""scripts/example_catalog.py -- Phase 3 of the examples/catalog split
(docs/contribute/plans/examples-catalog-split.md). Mirrors
test_platform_matrix.py's load-by-path pattern for a scripts/ leaf module."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _load(name: str):
    path = REPO_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _catalog():
    return _load("example_catalog")


def test_examples_dir_matches_real_repo_layout():
    catalog = _catalog()
    assert catalog.EXAMPLES_DIR == REPO_DIR / "examples"
    assert catalog.EXAMPLES_DIR.is_dir()
    assert catalog.CATALOG_DIR == REPO_DIR / "catalog"
    assert catalog.CASES_DIR == REPO_DIR / "catalog" / "cases"
    assert catalog.CASES_DIR.is_dir()
    assert catalog.GROUND_TRUTH_PATH == REPO_DIR / "catalog" / "ground_truth.json"
    assert catalog.GROUND_TRUTH_PATH.is_file()


def test_case_dir_is_byte_identical_to_the_hand_rolled_join():
    catalog = _catalog()
    for case_id in ("case01_symbol_removal", "case197_header_graph_identity_reconciled"):
        assert catalog.case_dir(case_id) == catalog.CASES_DIR / case_id


def test_all_case_ids_matches_ground_truth_verdicts_order():
    catalog = _catalog()
    gt = json.loads(catalog.GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    assert catalog.all_case_ids() == list(gt["verdicts"].keys())
    assert catalog.all_case_ids(gt) == list(gt["verdicts"].keys())


def test_all_case_ids_directories_exist():
    catalog = _catalog()
    for case_id in catalog.all_case_ids():
        assert catalog.case_dir(case_id).is_dir(), (
            f"{case_id}: ground_truth.json references it but "
            f"{catalog.case_dir(case_id)} doesn't exist"
        )


def test_iter_case_dirs_pairs_ids_with_their_resolved_directory():
    catalog = _catalog()
    pairs = catalog.iter_case_dirs()
    assert [case_id for case_id, _ in pairs] == catalog.all_case_ids()
    for case_id, path in pairs:
        assert path == catalog.case_dir(case_id)


def test_load_ground_truth_round_trips_the_real_file():
    catalog = _catalog()
    gt = catalog.load_ground_truth()
    assert "verdicts" in gt
    assert "taxonomy" in gt

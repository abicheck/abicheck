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

"""clang-latest.yml's path filters must select every file the lane runs.

The lane's test list lives in ``scripts/verify_clang_lane.py``; the filters
that decide whether a PR starts the lane live in the workflow. A module in
the list that no filter matches is only ever run by the weekly schedule
(CodeRabbit on #1921: ``test_header_backend_parity_probes_integration.py``
matched neither ``tests/*clang*`` nor any explicit entry). The set is derived
from the list itself, so adding a module there without a filter fails here.
"""

from __future__ import annotations

import sys

import _yaml_fast
import pytest
from _workflow_files import REPO_ROOT, read_repo_text
from test_workflow_path_filter_closure import _covers

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from verify_clang_lane import CLANG_SENSITIVE_TESTS  # noqa: E402

_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "clang-latest.yml"


def _filters() -> dict[str, list[str]]:
    doc = _yaml_fast.safe_load(read_repo_text(_WORKFLOW))
    on = doc.get("on", doc.get(True))
    return {event: list(on[event]["paths"]) for event in ("push", "pull_request")}


# The lane's own inputs beyond the test list: the catalog runner and the
# fixtures it builds, and the clang header backend those tests exercise.
_LANE_INPUTS = (
    *CLANG_SENSITIVE_TESTS,
    "tests/validate_examples.py",
    "tests/_validate_examples_cli.py",
    "catalog/ground_truth.json",
    "abicheck/extract/headers/clang/records.py",
    "abicheck/dumper_clang.py",
    "scripts/verify_clang_lane.py",
)


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize("path", _LANE_INPUTS)
def test_every_lane_input_starts_the_lane(event: str, path: str) -> None:
    assert (REPO_ROOT / path).is_file(), f"{path} no longer exists"
    assert _covers(_filters()[event], path), (
        f"{path} is run by the clang-latest lane but its {event} paths filter "
        "does not select it"
    )


def test_unrelated_paths_do_not_start_the_lane() -> None:
    """Negative control: the filter is not a catch-all."""
    for event, globs in _filters().items():
        for path in ("docs/index.md", "abicheck/policy/__init__.py", "README.md"):
            assert not _covers(globs, path), (event, path)

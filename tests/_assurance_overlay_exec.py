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

"""Shared execution helpers for ``actions/check-target/action.yml``'s
"Generate assurance-overlay config" step, split out of
``tests/test_reusable_workflows_require_complete_analysis.py`` so that
file's own sibling test module
(``tests/test_reusable_workflows_assurance_overlay_compile_context.py``,
PR #1222 fourth round) can share the identical execution machinery instead
of redefining it -- the same "move responsibility, don't trim to fit"
convention the root ``AGENTS.md`` states, mirroring how
``tests/_check_target_exec.py`` already holds the shared execution
machinery for the ``validate-inputs.sh``/``run.sh`` tests.

Not a `test_*` module itself: pytest never collects this file, and every
consumer imports these names directly rather than redefining them. Names
are unchanged from their original home so every pre-existing call site
keeps working unmodified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from _workflow_exec import run_step

_REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_PROJECT = _REPO_ROOT / ".github" / "workflows" / "check-project.yml"
CHECK_TARGET_ACTION = _REPO_ROOT / "actions" / "check-target" / "action.yml"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _overlay_step() -> dict[str, Any]:
    data = _load(CHECK_TARGET_ACTION)
    return next(s for s in data["runs"]["steps"] if s.get("id") == "assurance_overlay")


def _run_overlay(workspace: Path, env: dict[str, str]) -> Any:
    """``run_step`` against the overlay step, defaulting ``SOURCES_ROOT``/
    ``SOURCES_PAIRWISE``/``SOURCES_MERGE_COMPILE`` to empty (single-sided,
    single-document-exclusive ``compile:``) for every test that isn't
    specifically exercising PR #1222 Finding 2's ``build.compile_db``-
    resolution behavior, or (``SOURCES_PAIRWISE``/``SOURCES_MERGE_COMPILE``)
    PR #1222 fourth round's single-sided-vs-ambiguous compile:/source:/
    debug: promotion and its own compile:-specific MERGE-vs-REPLACE
    distinction (mode: compare vs. mode: scan -- see
    ``apply_sources_root_config_blocks``'s own ``merge_compile`` parameter
    docstring).

    ``_workflow_exec.run_step`` leaves any step ``env:`` key the caller
    doesn't override at its literal, un-evaluated ``${{ ... }}`` GitHub
    Actions expression text (see its own docstring) -- without this
    default, every pre-existing test in this module (none of which name
    ``SOURCES_ROOT``/``SOURCES_PAIRWISE``/``SOURCES_MERGE_COMPILE`` at all)
    would suddenly see that literal expression string as the shell's
    actual ``$SOURCES_ROOT``/``$SOURCES_PAIRWISE``/
    ``$SOURCES_MERGE_COMPILE`` value the moment the step gained that env
    key, rather than the empty string these tests intend.
    """
    merged = {
        "SOURCES_ROOT": "",
        "SOURCES_PAIRWISE": "",
        "SOURCES_MERGE_COMPILE": "",
        **env,
    }
    return run_step(_overlay_step(), workspace=workspace, env=merged)


def _written_overlay(result: Any) -> Any:
    """Load the YAML the overlay step actually wrote.

    Security (Codex review, PR #1222): the step no longer writes to a fixed,
    in-checkout-relative name -- it writes to a private, `mktemp`-created
    file under `$RUNNER_TEMP` and reports that ABSOLUTE path as its own
    `config-path` $GITHUB_OUTPUT record, exactly what the real "Run
    analysis" step reads to build its `--config`. Reading the file back via
    that same output (never a path this test re-derives on its own) is what
    proves the two agree.
    """
    config_path = Path(result.outputs["config-path"])
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))

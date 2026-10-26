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

"""Render a stored ``BundleDiffResult`` the way the stored CLI does.

The stored compare builds one release evaluation from its result and renders
every format from it (``frontends/cli/commands/compare_bundle_facts``). Tests
that exercise a render must build the same evaluation, so this helper is the
one place that does, rather than each test re-deriving it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from abicheck.frontends.cli.commands.compare_bundle_facts import _render
from abicheck.frontends.cli.commands.compare_bundle_facts_release import (
    stored_document_details,
    stored_release_evaluation,
)
from abicheck.report.comparison_scope import comparison_scope_terms
from abicheck.report.release_document import stored_reported_verdict
from abicheck.workflows.gate import resolve_scope_decision


def render_stored(
    result: Any,
    fmt: str,
    *,
    old_facts_path: Path,
    new_dir: Path,
    new_is_stored: bool = False,
) -> str:
    """The text of *result* rendered as *fmt* by the stored compare."""
    release = stored_release_evaluation(
        result,
        worst_verdict=stored_reported_verdict(result),
        old_facts_path=old_facts_path,
        new_dir=new_dir,
        scope_terms=comparison_scope_terms(resolve_scope_decision(None, None)),
        assurance_decision=None,
        require_complete_analysis=False,
        effective_config=("sha256:test", {"policy.base": "strict_abi"}),
    )
    details = stored_document_details(result, new_is_stored=new_is_stored)
    return _render(fmt, release, details)

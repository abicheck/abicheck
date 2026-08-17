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

"""``$GITHUB_STEP_SUMMARY`` writer for a single-comparison annotation pass.

Split out of ``annotations.py`` (CLI cleanup phase two, PR E): that module
is now imported by ``reporter_contract_blocks.add_annotations`` (the
``annotations`` JSON report field, schema 2.43) so a real ``DiffResult``
comparison can persist its classified findings alongside the rest of the
report. ``annotations.py`` importing ``reporter`` -- as this one function
alone did, for ``reporter.to_markdown`` -- would close
``reporter -> reporter_contract_blocks -> annotations -> reporter`` into an
import cycle the AI-readiness ``import-cycle-growth`` gate correctly
rejects (CLAUDE.md "M1-3": prefer a leaf module over extending the
allowlist). This module has no callers that need ``annotations.py``'s own
classification logic, so it stays a genuine leaf with respect to
``reporter``, and ``annotations.py`` no longer reaches ``reporter`` at all.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .checker import DiffResult

if TYPE_CHECKING:
    from .severity import SeverityConfig


def emit_github_step_summary(
    diff_result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
) -> str | None:
    """Write a Markdown job summary to $GITHUB_STEP_SUMMARY if available.

    *severity_config*, when given, is forwarded to :func:`abicheck.reporter.to_markdown`
    so the step summary carries the same "Severity Configuration" section the
    inline annotations are already gated on — without it, `severity.addition: error` (for example) could fail the annotations/exit code while the step
    summary still rendered the legacy compatible report with no severity gate
    section, contradicting the actual gate on the same PR.

    Returns the summary path if written, None otherwise.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return None

    from .reporter import to_markdown

    md = to_markdown(diff_result, severity_config=severity_config)
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(md)
        f.write("\n")
    return summary_path

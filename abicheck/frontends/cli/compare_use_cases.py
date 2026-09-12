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

"""``compare --use-cases`` validation against the set of formats this run
actually renders.

Split out of ``cli_compare_helpers.run_compare`` (ADR-068 D4/Phase 5) rather
than folded into a sibling helper module, since both ``cli_helpers_compare.py``
and ``cli_compare_options.py`` are already at their own ``architecture/
debt.yaml`` ``no_growth`` ceilings -- a fresh, single-purpose leaf module is
the "move responsibility to a properly-owned module" ADR-061 asks for,
rather than trading one file's growth for another's.
"""

from __future__ import annotations

from typing import Any

import click


def reject_use_cases_without_carrying_output(
    *,
    fmt: str,
    secondary_fmts: list[str],
    use_cases_manifest: Any,
) -> None:
    """Reject ``--use-cases`` when nothing this run renders would carry it.

    A manifest resolved and then dropped is the same failure --use-cases is
    rejected for set inputs to avoid -- sarif/junit/html never read
    ``DiffResult.use_case_impact``, and the one-line format (``--format
    oneline``) has no room for it either. Asked across *every* rendered
    output, primary or any ``-o`` (repeatable per ADR-068 D4/Phase 5 --
    "one output carrying it" is satisfied by the primary render OR any
    secondary write); only when none does is the manifest genuinely resolved
    for nothing (Codex review).
    """
    from ...cli_compare_fold import format_carries_use_case_impact

    if use_cases_manifest is None or (
        format_carries_use_case_impact(fmt)
        or any(format_carries_use_case_impact(f) for f in secondary_fmts)
    ):
        return
    writes_desc = "".join(f" --write {f}=..." for f in secondary_fmts)
    rendered = f"--format {fmt}" + (f" and{writes_desc}" if secondary_fmts else "")
    detail = (
        f"no output this run renders ({rendered}) carries use-case "
        "attribution, so the manifest would be resolved and its result "
        "dropped. Use -o json=.../markdown/review, or add --write "
        "json=PATH to get one output that carries it alongside the "
        f"{fmt} report."
    )
    raise click.UsageError(f"--use-cases is not supported here: {detail}")

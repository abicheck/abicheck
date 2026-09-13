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

"""The report-mode vocabulary and the one check that enforces it.

A leaf of `report/` (no first-party imports beyond ``abicheck.errors``,
a public root surface) because *every* public rendering entry point has to
share it: ``service_render.render_output``, ``reporter.to_json`` and
``report.dispatch_markdown.to_markdown`` are three separately documented
ways into the same set of documents, and a retirement enforced at one of
them is not a retirement -- a caller reaching either of the others keeps
rendering and silently receives a different document shape.

That is not hypothetical: plan slice 7o first enforced the ``leaf``
retirement in `service_render` alone, and two tests in this repository went
on calling ``to_markdown(..., report_mode="leaf")`` and passing, each
quietly rendering a *full* report while asserting against the leaf view
(CodeRabbit review, PR #1284, which read those call sites as failures --
they were worse, they were green).
"""

from __future__ import annotations

from ..errors import ValidationError

#: Report modes this codebase renders.
SUPPORTED_REPORT_MODES: frozenset[str] = frozenset({"full", "impact", "root-cause"})

#: Retired mode -> what to use instead. Named rather than merely absent, so
#: the error tells a caller where the capability went.
RETIRED_REPORT_MODES: dict[str, str] = {
    "leaf": (
        "use report_mode='root-cause': measured over 129 real library pairs, "
        "the two exposed the identical finding set in every one of the 93 "
        "with findings, and 'leaf' rendered an empty headline section in 40 "
        "of them"
    ),
}


def reject_unsupported_report_mode(report_mode: str) -> None:
    """Reject a retired or unknown ``report_mode`` at a public boundary.

    Retiring ``leaf`` from the Click parser does not retire the *documented
    Python* rendering paths. A caller passing ``report_mode="leaf"`` would
    otherwise fall through to a full report -- a silently different document
    shape, strictly worse than an error, since the caller keeps rendering
    and never learns the mode is gone. ``abicheck/AGENTS.md`` treats the
    typed API as public surface, so the retirement is enforced where that
    surface is, not only where Click is.
    """
    if report_mode in RETIRED_REPORT_MODES:
        raise ValidationError(
            f"report_mode={report_mode!r} was retired (plan slice 7o): "
            f"{RETIRED_REPORT_MODES[report_mode]}"
        )
    if report_mode not in SUPPORTED_REPORT_MODES:
        raise ValidationError(
            f"Unsupported report mode: {report_mode!r} "
            f"(expected one of {sorted(SUPPORTED_REPORT_MODES)})"
        )

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

"""``output.view show=`` filtering of a release's release-global sections.

Bundle (cross-library) findings and the build-matrix result are not tied to
one member's ``DiffResult``; the release document filters them here, once,
for every release format.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..checker import (
    Change,
    DiffResult,
)
from .policy_facts import kind_sets_for
from .view import (
    apply_show_only,
    show_only_matches,
)

if TYPE_CHECKING:
    from ..bundle_models import BundleDiffResult


def filter_release_bundle_findings(
    findings: Sequence[Any],
    show_only: str,
    policy: str = "strict_abi",
    policy_file: object | None = None,
) -> list[Any]:
    """Filter ``compare-release`` bundle (cross-library) findings via --show-only.

    Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): bundle findings are release-global, not tied to
    one library's own ``DiffResult``, so :func:`apply_show_only` (which
    expects a real :class:`Change`) cannot be called on them directly.
    Each finding is lowered through its own ``to_change()`` projection --
    the identical lowering
    ``workflows.release_exit_fold.fold_release_global_severity`` already
    uses to resolve a bundle finding's severity for the release's exit
    code -- so this display filter can never disagree with what that exit
    code already computed for the same finding. *kind_sets* is deliberately
    not accepted: bundle findings carry canonical, already-partitioned
    ``ChangeKind``s, matching that same existing severity fold's own
    omission of it.

    *findings* is typed ``Sequence[Any]`` rather than
    ``Sequence[BundleFinding]`` to avoid this leaf module importing
    :mod:`abicheck.bundle_models` at runtime for a type-only reference; any
    object exposing ``to_change() -> Change`` (every real
    :class:`~abicheck.bundle_models.BundleFinding` does) satisfies it.
    """
    return [
        f
        for f in findings
        if show_only_matches(
            show_only, f.to_change(), policy=policy, policy_file=policy_file
        )
    ]


def release_bundle_findings_for_view(
    bundle_result: BundleDiffResult, show_only: str | None
) -> list[Any]:
    """Return *bundle_result*'s findings, ``output.view show=``-filtered when active.

    Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): shared by the release fan-out's JSON
    (``report.render_release_report.render_release_json``) and Markdown
    (``report.render_release_report.render_release_markdown_report``) bundle
    sections so the two formats can never disagree about which bundle
    findings a given ``show_only`` selection keeps. Both call sites live in ``report/release_document.py`` (a ``report`` module, so it may import this module directly) as of Codex review, fresh
    evidence, PR #1154 follow-up ("Move release filtering out of the
    Markdown renderer") -- ``report/render_release_markdown.py``'s own
    Markdown section renderer used to call this function directly, which
    `report/AGENTS.md`'s renderer contract forbids (a renderer may only
    format an already-computed projection, never filter one itself); it now
    takes the pre-filtered result as a parameter instead. A no-op (returns
    every finding) when *show_only* is falsy.
    """
    if not show_only:
        return list(bundle_result.bundle_findings)
    return filter_release_bundle_findings(
        bundle_result.bundle_findings,
        show_only,
        policy=bundle_result.policy,
        policy_file=bundle_result.policy_file,
    )


def release_matrix_changes_for_view(
    matrix_result: DiffResult, show_only: str | None
) -> list[Change]:
    """Return *matrix_result*'s changes, ``output.view show=``-filtered when active.

    Shared by the release fan-out's JSON and Markdown release-global matrix
    (build-configuration) sections, both called from ``report/release_document.py`` for the identical reason as
    :func:`release_bundle_findings_for_view` -- see that function's own
    docstring. Unlike a bundle finding, a matrix result is a real
    :class:`DiffResult`, so it is filtered the identical way a per-library
    one is (``kind_sets``/``policy_file`` included, so the severity
    dimension resolves consistently). A no-op (returns every change) when
    *show_only* is falsy.
    """
    if not show_only:
        return list(matrix_result.changes)
    return apply_show_only(
        matrix_result.changes,
        show_only,
        policy=matrix_result.policy or "strict_abi",
        kind_sets=kind_sets_for(matrix_result),
        policy_file=matrix_result.policy_file,
    )

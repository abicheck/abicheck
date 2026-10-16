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

"""The release fan-out's frontend adapter to the one release report builder.

:func:`render_release_summary` is the only thing the CLI's directory/package
``compare`` calls to render a release report, for the primary ``--format``
and every ``-o`` export alike. It translates CLI-shaped arguments into a
:class:`~abicheck.report.release_document.ReleaseEvaluation` -- resolving the
two things only a frontend can: the default scope/assurance decision for a
caller that passes none, and the effective-config receipt (which reloads
``--policy-file``/``--suppress``, so it is computed only for a format that
prints it) -- then hands it to
:func:`~abicheck.report.release_document.build_release_report` and
:func:`~abicheck.report.render_release_report.render_release_text`. It
renders nothing itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...report.comparison_scope import ComparisonScopeTerms, comparison_scope_terms
from ...report.release_assurance_section import (
    ReleaseAssuranceTerms,
    release_assurance_terms,
)
from ...report.release_document import ReleaseEvaluation, build_release_report
from ...report.render_release_report import render_release_text
from ...workflows.gate import (
    resolve_release_assurance_decision,
    resolve_scope_decision,
)
from .options.params import DEFAULT_POLICY_PROFILE

if TYPE_CHECKING:
    from ...bundle_models import BundleDiffResult
    from ...checker_types import DiffResult
    from ...report.junit_inventory import JunitOldOperand
    from ...report.release_public_surface_section import ReleasePublicSurfaceTerms
    from ...workflows.gate import SeverityConfig

__all__ = ["render_release_summary"]

#: Formats whose rendering states the effective-config receipt.
_RECEIPT_FORMATS = frozenset({"json", "html"})


def render_release_summary(
    fmt: str,
    worst_verdict: str,
    old_dir: Path,
    new_dir: Path,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: Mapping[str, Path],
    new_map: Mapping[str, Path],
    warning_msgs: list[str],
    diff_pairs: Sequence[tuple[DiffResult, JunitOldOperand]] | None = None,
    bundle_result: BundleDiffResult | None = None,
    matrix_result: DiffResult | None = None,
    severity_config: SeverityConfig | None = None,
    severity_exit_code: int | None = None,
    contract_coverage_exit_contribution: int = 0,
    contract_coverage_failure_count: int = 0,
    fail_on_removed: bool = False,
    policy: str = DEFAULT_POLICY_PROFILE,
    policy_file_path: Path | None = None,
    suppress: Path | None = None,
    pack_application: Any = None,
    scope_public_headers: bool = True,
    scope_terms: ComparisonScopeTerms | None = None,
    assurance_terms: ReleaseAssuranceTerms | None = None,
    demangle: bool = False,
    show_only: str | None = None,
    env_matrix_source_sha256: str | None = None,
    require_complete_analysis: bool = False,
    excluded_header_patterns: str = "",
    public_surface: ReleasePublicSurfaceTerms | None = None,
) -> str:
    """Render the release report as *fmt* (``json``/``markdown``/``junit``/
    ``html``/``oneline``).

    *show_only* is the ``--view show=`` selection this render applies to its
    own projection; *library_results* is never mutated, so the same list can
    feed several renders. *scope_terms*/*assurance_terms* default to an
    empty, setting-off decision for a direct (unit-test) caller.
    """
    terms = (
        scope_terms
        if scope_terms is not None
        else comparison_scope_terms(resolve_scope_decision(None, None))
    )
    a_terms = (
        assurance_terms
        if assurance_terms is not None
        else release_assurance_terms(
            resolve_release_assurance_decision((), require_complete=False)
        )
    )
    effective_config = None
    if fmt in _RECEIPT_FORMATS:
        # Shared with the `--output-dir` sidecar (`release_summary.py`), so
        # the two summary documents cannot drift.
        from ...cli_compare_receipt import _release_summary_effective_config_block

        effective_config = _release_summary_effective_config_block(
            severity_config,
            policy=policy,
            policy_file_path=policy_file_path,
            suppress=suppress,
            pack_application=pack_application,
            scope_public_headers=scope_public_headers,
            on_incomplete_scope=terms.policy,
            fail_on_removed_library=fail_on_removed,
            env_matrix_source_sha256=env_matrix_source_sha256,
            require_complete_analysis=require_complete_analysis,
            excluded_header_patterns=excluded_header_patterns,
        )
    evaluation = ReleaseEvaluation(
        worst_verdict=worst_verdict,
        old_dir=old_dir,
        new_dir=new_dir,
        library_results=library_results,
        scope_terms=terms,
        assurance_terms=a_terms,
        removed_keys=removed_keys,
        added_keys=added_keys,
        old_map=old_map,
        new_map=new_map,
        warning_msgs=warning_msgs,
        diff_pairs=diff_pairs,
        bundle_result=bundle_result,
        matrix_result=matrix_result,
        severity_config=severity_config,
        severity_exit_code=severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
        contract_coverage_failure_count=contract_coverage_failure_count,
        fail_on_removed=fail_on_removed,
        env_matrix_source_sha256=env_matrix_source_sha256,
        require_complete_analysis=require_complete_analysis,
        public_surface=public_surface,
        effective_config=effective_config,
    )
    report = build_release_report(evaluation, show_only=show_only)
    return render_release_text(fmt, report, demangle=demangle)

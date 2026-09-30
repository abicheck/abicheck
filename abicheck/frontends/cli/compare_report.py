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

"""A cohesive slice of ``compare``'s post-comparison phase: scoped gating,
report rendering, suppression-audit attachment, and the set-input flag
rejection shared by the single-pair and directory/package paths.

Split out of :mod:`abicheck.cli_compare_helpers` (2026-09-11, ai-readiness
`file-size` gate) for the usual reason this package exists -- that module
sits at its ``architecture/debt.yaml`` ``no_growth`` baseline (2000 lines),
so a new, cohesive slice belongs in its own file rather than pushing that
one further over the hard cap. ``run_compare`` (and its own
``_resolve_evaluation_config``/``_attach_use_case_impact``/
``_report_compare_result`` helpers, which stay in
:mod:`abicheck.cli_compare_helpers`) import these functions back rather than
being moved themselves: two of those three lazily import genuinely
unclassified legacy modules (``compatibility_evaluation_resolver.py``,
``impact/``), which a real *migrated* ``frontends`` package file may not do
(``scripts/check_architecture.py``'s ``unclassified-import`` check applies
only to files physically under a layer's own package path, not to a flat
legacy file merely classified into that layer via ``legacy_paths`` --
``cli_compare_helpers.py`` is the latter). Moving only the callees with no
such dependency, not the call site, keeps every existing
``cli_compare_helpers.<name>`` reference (docstrings, comments) resolving
exactly as before, since Python looks a bare name up in the *calling*
module's globals at call time, not in the module that originally defined it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ...cli_compare_fold import (
    _fold_scoped_compat_into_text,
    _fold_suppression_audit_into_text,
    _fold_use_case_impact_into_text,
)
from ...cli_compare_options import (
    _reject_set_input_flags,
)
from ...cli_helpers_compare import (
    _apply_required_symbol_scoping,
    _apply_used_by_scoping,
)
from ...cli_resolve import (
    _reject_evidence_flags_for_set_inputs,
)
from .runtime import _render_output

if TYPE_CHECKING:
    from ...model.consumer_spec import ConsumerAppInput
    from ...workflows.policy_file import PolicyFile


def _apply_scoped_gating(
    result: Any,
    old: Any,
    new: Any,
    policy: str,
    pf: PolicyFile | None,
    *,
    used_by_apps: tuple[ConsumerAppInput, ...],
    required_symbols: tuple[str, ...],
    used_by_old_input: Path,
    used_by_new_input: Path,
    exit_code_scheme: str,
    sev_config: Any,
    suppression: Any,
) -> int | None:
    """Apply whichever ADR-043 scoped gate this run selected, if any.

    ``--used-by`` and ``--required-symbol`` are mutually exclusive (rejected
    earlier), so at most one applies. Returns the scoped exit code, or ``None``
    when the run is unscoped and the full-library verdict gates instead.
    """
    if used_by_apps:
        return _apply_used_by_scoping(
            result,
            used_by_apps,
            used_by_old_input,
            used_by_new_input,
            old,
            new,
            policy,
            pf,
            exit_code_scheme=exit_code_scheme,
            sev_config=sev_config,
            suppression=suppression,
        )
    if required_symbols:
        return _apply_required_symbol_scoping(
            result,
            required_symbols,
            old,
            new,
            policy,
            pf,
            exit_code_scheme=exit_code_scheme,
            sev_config=sev_config,
            suppression=suppression,
        )
    return None


def _render_compare_report(
    result: Any,
    old: Any,
    new: Any,
    *,
    fmt: str,
    follow_deps: bool,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    severity_config: Any,
    demangle: bool,
    contract_evaluation: bool,
    require_complete_analysis: bool = False,
) -> str:
    """Render one compare report and fold every post-render section into it.

    The primary (``--format``) and secondary (``-o``) renders
    run the identical pipeline and differ only in their arguments, so
    they share this one function rather than keeping two copies that can drift.

    No ``stat``/``recommend`` parameters (CLI cleanup phase two, PR 1): see
    ``_render_output``/``service_render.render_output`` for where the
    one-line format and the unconditional recommendation now live.

    ADR-061 Phase 2 item 5: this used to be a four-fold pipeline -- the
    fourth step, ``_fold_evidence_depth_into_json``, re-parsed the JSON text
    this function was about to return to splice in
    ``old_evidence_depth``/``new_evidence_depth``. Both are now resolved
    once by the caller and attached onto ``result`` before this function
    ever runs (see ``checker_types.DiffResult.old_evidence_depth``'s own
    docstring), so ``_render_output``'s own ``to_json`` call already emits
    them and no fourth fold-in is needed here any more.
    """
    text = _render_output(
        fmt,
        result,
        old,
        new,
        follow_deps=follow_deps,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
        demangle=demangle,
        contract_evaluation=contract_evaluation,
        require_complete_analysis=require_complete_analysis,
    )
    text = _fold_scoped_compat_into_text(
        text,
        fmt,
        result,
        severity_config=severity_config,
        show_only=show_only,
        report_mode=report_mode,
        contract_evaluation=contract_evaluation,
        demangle=demangle,
    )
    # Plan slice 7o: unconditional. `result.suppression_audit` is attached
    # whenever suppression was given, and every machine format has always
    # emitted it; `--view suppressions` gated only whether the markdown/
    # text/review render echoed it. ADR-067's record-before-disposing rule
    # says a suppressed finding may not be invisible because a token was
    # not typed, so the fold now runs on the field itself -- a no-op, as
    # before, on a run with no `--suppress`.
    text = _fold_suppression_audit_into_text(
        text,
        fmt,
        result.suppression_audit,
        demangle=demangle,
    )
    return _fold_use_case_impact_into_text(
        text, fmt, result, show_only, demangle=demangle
    )


def _reject_flags_unsupported_for_set_inputs(
    ctx: click.Context,
    *,
    used_by_apps: tuple[ConsumerAppInput, ...],
    required_symbols: tuple[str, ...],
    diagnostic_comparison: bool,
    include_labels: dict[Path, str] | None,
    use_cases_manifest: Path | None = None,
    suppress: Path | None = None,
    budget: str | None = None,
    pdb_path: Path | None = None,
) -> str | None:
    """Reject the single-pair-only flags on a directory/package compare.

    The per-library fan-out (``compare-release`` backend) consumes the
    resolved scheme from config but has no public CLI support for these
    flags on set inputs -- reject them loudly (ADR-037 D12). Validated ahead
    of the ``--dry-run`` emit so a dry run can't report "ok" for a flag
    combination the real run would then reject.

    ``--pack`` is not rejected here: the caller resolves it separately right
    after this call. ``-o`` (``secondary_writes``, repeatable per
    ADR-068 D4/Phase 5) is not rejected either -- the release engine
    supports it directly (at most one write; see ``_dispatch_release_
    compare``'s own rejection of a second one), so it is simply forwarded.

    Returns the ``--depth`` value the caller should forward to the fan-out
    -- any rung of the public ladder, or ``None``. No rung is rejected; a
    shortfall is ADR-064's exit-7 axis, per member (see
    :func:`~abicheck.cli_compare_options._resolve_depth_for_set_inputs`).
    """
    _reject_set_input_flags(
        used_by_apps=used_by_apps,
        required_symbols=required_symbols,
        use_cases_manifest=use_cases_manifest,
        diagnostic_comparison=diagnostic_comparison,
        suppress=suppress,
        include_labels=include_labels,
        budget=budget,
        pdb_path=pdb_path,
    )
    return _reject_evidence_flags_for_set_inputs(ctx)

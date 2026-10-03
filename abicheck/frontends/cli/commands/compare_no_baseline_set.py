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

"""``abicheck compare --no-baseline DIR`` -- the CLI-layer half of the
N-library audit (one-comparison-product F-23, ADR-068 D2).

Reached from :func:`.compare_no_baseline.maybe_dispatch_no_baseline_compare`
when the operand classifies as a directory or package. The invocation is
validated and resolved by the *same* :mod:`.no_baseline_invocation` the
scalar audit uses, each member is audited by the same
``workflows.no_baseline_compare.audit_no_baseline_candidate``, and member
discovery, the acquisition record and the per-member loop live in
:mod:`abicheck.workflows.no_baseline_set` -- so this module only translates:
CLI values in, typed operand errors to Click errors, the ``audit_set``
report out.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ....report.no_baseline_set import (
    NO_BASELINE_SET_SUPPORTED_FORMATS,
    NO_BASELINE_SET_UNSUPPORTED_FORMATS,
    render_no_baseline_set,
)
from ....workflows.no_baseline_set import (
    NoBaselineSetPlan,
    NoBaselineSetResult,
    cleanup_no_baseline_set_plan,
    resolve_no_baseline_set_plan,
    run_no_baseline_set,
)
from ..runtime import _safe_write_output, _write_or_echo
from .no_baseline_invocation import (
    ResolvedNoBaselineInvocation,
    audit_inputs,
    load_policy_documents,
    resolve_no_baseline_invocation,
    validate_no_baseline_invocation,
)
from .no_baseline_rulings import _set_variant_from_context

if TYPE_CHECKING:
    from ....model.release_selection import ReleaseSelection

__all__ = ["run_no_baseline_set_cmd"]


def _set_format_message(fmt: str) -> str:
    """The usage error for a format an N-library audit does not render."""
    supported = "/".join(sorted(NO_BASELINE_SET_SUPPORTED_FORMATS))
    reason = NO_BASELINE_SET_UNSUPPORTED_FORMATS.get(
        fmt, "it is not a format an audit report has"
    )
    return (
        f"-o {fmt}=... is not available with --no-baseline for a directory or "
        f"package of libraries: {reason}. Use one of {supported} instead."
    )


def _selection(kwargs: dict[str, Any]) -> ReleaseSelection | None:
    """ADR-065 S1's explicit member selection from ``--select``/
    ``--select-required`` -- ``None`` when neither was given."""
    select = tuple(kwargs.get("select") or ())
    select_required = tuple(kwargs.get("select_required") or ())
    if not (select or select_required):
        return None
    from ....model.release_selection import ReleaseSelection

    try:
        return ReleaseSelection.from_lists(required=select_required, optional=select)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


def _resolve_plan(
    candidate: Path,
    kind: str,
    inv: ResolvedNoBaselineInvocation,
    selection: ReleaseSelection | None,
    variant: str | None,
) -> NoBaselineSetPlan:
    """Resolve the member plan, translating the release resolution's typed
    operand errors exactly as the two-sided release boundary does
    (``frontends/cli/release_compare_request.py``): a usage fact exits 64,
    a content fact (nothing readable, an unrecognized archive, an ambiguous
    member match, an unreadable stored-package marker) exits 1."""
    from ....errors import (
        AmbiguousLibraryMatchError,
        ReleaseOperandContentError,
        ReleaseOperandUsageError,
        SnapshotError,
    )

    try:
        return resolve_no_baseline_set_plan(
            candidate,
            operand_kind=kind,
            include_private_dso=inv.scope.include_private_dso,
            dso_only=inv.scope.dso_only,
            selection=selection,
            variant=variant,
        )
    except ReleaseOperandUsageError as exc:
        raise click.UsageError(str(exc)) from exc
    except (
        ReleaseOperandContentError,
        AmbiguousLibraryMatchError,
        SnapshotError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc


def _emit(result: NoBaselineSetResult, inv: ResolvedNoBaselineInvocation) -> int:
    """Render every requested format from the one result; return the exit."""

    def _render(fmt: str) -> tuple[str, int]:
        return render_no_baseline_set(
            result,
            fmt,
            on_incomplete=inv.scope.on_incomplete,
            require_complete_analysis=inv.output.require_complete_analysis,
            audit_gate_enabled=inv.output.audit_gate_enabled,
        )

    text, exit_code = _render(inv.output.fmt)
    _write_or_echo(inv.output.output, text)
    for write_fmt, write_path in inv.output.secondary_writes:
        # Rendered from the *same* result, never a second run (ADR-068 D4).
        _safe_write_output(write_path, _render(write_fmt)[0])
    return exit_code


def run_no_baseline_set_cmd(
    ctx: click.Context, candidate: Path, kind: str, kwargs: dict[str, Any]
) -> None:
    """Validate, resolve, audit every member of *candidate*, and report.

    Exits with the folded exit code (``report.no_baseline_set``): the max of
    every member's own axes, the operational-error axis for a failed member,
    and ADR-065's scope axes -- so zero audited members exits ``1``.
    """
    validate_no_baseline_invocation(
        kwargs,
        supported_formats=NO_BASELINE_SET_SUPPORTED_FORMATS,
        format_message=_set_format_message,
        operand_is_set=True,
    )
    variant = _set_variant_from_context(ctx)
    inv = resolve_no_baseline_invocation(ctx, kwargs)
    selection = _selection(kwargs)

    if inv.output.dry_run:
        from ....dry_run import emit_dry_run
        from ..no_baseline_set_dry_run import build_no_baseline_set_dry_run_result

        # Validation only, exactly as the scalar preview does: a malformed
        # `--suppress` is exit 64 on the real run, so the preview says so too.
        load_policy_documents(inv, kwargs)
        emit_dry_run(
            build_no_baseline_set_dry_run_result(
                candidate=candidate,
                operand_kind=kind,
                selection=selection,
                fmt=inv.output.fmt,
                on_incomplete=inv.scope.on_incomplete,
                depth=inv.evidence.depth,
                contract_mode=kwargs.get("contract_mode"),
            )
        )

    suppression, policy_file_obj = load_policy_documents(inv, kwargs)
    inputs = audit_inputs(
        inv,
        suppression=suppression,
        policy=kwargs.get("policy") or "strict_abi",
        policy_file=policy_file_obj,
    )
    plan = _resolve_plan(candidate, kind, inv, selection, variant)
    try:
        for warning in plan.warnings:
            click.echo(warning, err=True)
        from ....errors import ValidationError

        try:
            result = run_no_baseline_set(
                plan, inputs, notify=lambda msg: click.echo(msg, err=True)
            )
        except ValidationError as exc:
            # Misconfiguration, not a member's own failure: abort as the
            # scalar audit does (exit 64), rather than recording the same
            # usage error against every member.
            raise click.UsageError(str(exc)) from exc
        exit_code = _emit(result, inv)
    finally:
        cleanup_no_baseline_set_plan(plan)
    if exit_code != 0:
        sys.exit(exit_code)

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

"""CLI — the ``aggregate`` multi-target fan-in gate (workflow composition).

``aggregate`` folds the per-target ``compare``/``scan`` JSON reports produced
by a CI build matrix into one gate decision. It is a *workflow-composition*
command (like a report-level ``compare``): unlike the core-analysis commands
(``dump``/``compare``/``scan``/``deps``/``compat``) it does not analyze a
binary — it reconciles already-produced reports against the set of targets the
matrix was supposed to build.

Three axes stay separate (ADR-042): **compatibility** (worst verdict, for
reporting), **gate** (each report's own ``severity`` decision, combined — never
recomputed from the verdict), and **coverage** (did every required target
report?). A required coverage gap fails at exit ``1`` — a build that never ran
is never handed an ABI-break exit ``4``.

Split out of :mod:`abicheck.cli` per the sibling-module pattern; imported for
side-effect at the bottom of :mod:`abicheck.cli` so ``@main.command`` runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .cli import _safe_write_output, _setup_verbosity, main
from .cli_options import output_options, verbose_option
from .report.aggregate import render_aggregate_json, render_aggregate_text
from .workflows.aggregate import (
    DEFAULT_REPORT_PREFIX,
    AggregateError,
    ExpectedTargets,
    aggregate_reports_dir,
)


@main.command("aggregate")
@click.argument(
    "reports_dir",
    type=click.Path(path_type=Path),
)
@click.option(
    "--manifest",
    "manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help='Expected-target manifest (JSON: {"targets": [{"id", "required"}]}). '
    "The single source of truth for which targets the matrix must produce — "
    "generate it in the plan job and feed the same file to both the matrix and "
    "this gate so they never drift.",
)
@click.option(
    "--run-plan",
    "run_plan_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="A project run-plan.json (from `abicheck project plan`), projected "
    "to the expected-target manifest shape internally -- an alternative to "
    "--manifest that skips the separate projection step. Each check's own "
    "check_id becomes the expected target id, matching what `check-target` "
    "writes as every report's target_id.",
)
@click.option(
    "--discovered-only",
    "discovered_only",
    is_flag=True,
    default=False,
    help="Explicitly aggregate whatever reports are present, with NO coverage "
    "gate. Required to run without --manifest/--run-plan — because with no "
    "declared target set the gate cannot tell a missing required target from "
    "an intentionally absent one.",
)
@output_options(
    ["text", "json"],
    default="text",
    format_help="Output format for the aggregated result.",
)
@verbose_option
def aggregate_cmd(
    reports_dir: Path,
    manifest: Path | None,
    run_plan_path: Path | None,
    discovered_only: bool,
    fmt: str,
    output: Path | None,
    verbose: bool,
) -> None:
    """Aggregate per-target ABI reports in REPORTS_DIR into one CI gate verdict.

    REPORTS_DIR holds the per-target ``compare``/``scan`` JSON reports
    downloaded from the build matrix (one ``abi-report-<target>.json`` per
    leg). Provide the expected-target set with ``--manifest`` or
    ``--run-plan`` (recommended for a `project plan`-driven workflow), or opt
    into ``--discovered-only`` to aggregate whatever is present with no
    coverage gate.

    The gate policy for an unavailable required target
    (``missing_required: fail|warn``) or a report outside the expected set
    (``unexpected_target: include|warn|fail|ignore``) is no longer a pair of
    CLI flags -- CLI cleanup phase two, PR 2 folded them into the manifest's
    (or run-plan-projected manifest's) own ``gate`` block, so expectation and
    the consequence of breaking it are one versioned contract instead of two
    independently-typeable inputs. Omitting ``gate`` keeps the same defaults
    this command always had (``fail``/``include``); see
    ``docs/use/aggregate-reports.md`` for the manifest shape.

    Exit code: 0 pass / 1 required-coverage gap, a policy-blocked
    addition-or-quality finding, or a non-verdict per-report failure (e.g. a
    `scan` budget overflow) / 2 a source-API break / 4 an ABI break / 64 usage
    error. Each target's own recorded gate decision is used — the gate is never
    recomputed from the compatibility verdict (ADR-042).
    """
    _setup_verbosity(verbose)

    expected, policy_source_hint = _resolve_expected(
        manifest, run_plan_path, discovered_only
    )

    try:
        result = aggregate_reports_dir(
            reports_dir,
            expected=expected,
            discovered_only=discovered_only,
            policy_source_hint=policy_source_hint,
            prefix=DEFAULT_REPORT_PREFIX,
        )
    except AggregateError as exc:
        raise click.UsageError(str(exc)) from exc

    text = (
        json.dumps(render_aggregate_json(result), indent=2)
        if fmt == "json"
        else render_aggregate_text(result)
    )
    if output is not None:
        _safe_write_output(output, text)
    else:
        click.echo(text)

    sys.exit(result.exit_code())


def _resolve_expected(
    manifest: Path | None,
    run_plan_path: Path | None,
    discovered_only: bool,
) -> tuple[ExpectedTargets | None, str]:
    """Resolve the expected-target set from exactly one source, or usage error.

    Precedence is deliberately *exclusive*, not merging: ``--discovered-only``,
    ``--manifest``, and ``--run-plan`` are three distinct ways to say what the
    target set is, and combining them is ambiguous. (The ad-hoc
    ``--expect``/``--optional`` id lists were a fourth; they are gone --
    an expected-target set is a file the plan job and the gate share, and
    retyping it on the command line was the drift the manifest exists to
    prevent.)

    Returns the expected-target set plus a ``policy_source_hint`` label
    (``"manifest"``/``"run-plan"``) naming which source it came from --
    :func:`~.workflows.aggregate.resolve_gate_policy` reports this back in the
    result's ``effective_policy.source`` whenever that source's own ``gate``
    block actually supplied a value.
    """
    sources_given = sum([manifest is not None, run_plan_path is not None])

    if discovered_only:
        if sources_given:
            raise click.UsageError(
                "--discovered-only cannot be combined with --manifest/--run-plan"
            )
        return None, "default"
    if sources_given > 1:
        raise click.UsageError(
            "--manifest and --run-plan are mutually exclusive expected-target sources"
        )
    if manifest is not None:
        try:
            return ExpectedTargets.from_manifest_file(manifest), "manifest"
        except AggregateError as exc:
            raise click.UsageError(str(exc)) from exc
    if run_plan_path is not None:
        from .buildsource.run_plan import RunPlan, to_aggregate_manifest

        try:
            raw = json.loads(run_plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise click.UsageError(f"cannot read {run_plan_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise click.UsageError(f"{run_plan_path} must contain a JSON object.")
        try:
            plan = RunPlan.from_dict(raw)
        except AggregateError as exc:
            raise click.UsageError(f"{run_plan_path}: {exc}") from exc
        try:
            return (
                ExpectedTargets.from_manifest_data(to_aggregate_manifest(plan)),
                "run-plan",
            )
        except AggregateError as exc:
            raise click.UsageError(
                f"{run_plan_path}: {exc} — an empty run-plan.json has no "
                "targets to aggregate; regenerate it with `abicheck project "
                "plan` (dropping --allow-empty), or aggregate with "
                "--discovered-only instead"
            ) from exc
    raise click.UsageError(
        "no expected-target set: pass --manifest or --run-plan (the targets "
        "the matrix must produce), or --discovered-only to aggregate whatever "
        "is present with no coverage gate"
    )

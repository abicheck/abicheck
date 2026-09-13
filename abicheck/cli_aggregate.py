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
from typing import Any

import click

from .cli import main
from .cli_options import export_options, verbose_option
from .frontends.cli.options.export import ExportSet
from .frontends.cli.runtime import _setup_verbosity, emit_export_set
from .report.aggregate import render_aggregate_json, render_aggregate_text
from .workflows.aggregate import (
    DEFAULT_REPORT_PREFIX,
    AggregateError,
    ExpectedTargets,
    aggregate_reports_dir,
)
from .workflows.aggregate.expected_input import (
    ExpectedInputError,
    ExpectedInputKind,
    classify_expected_input,
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
    help="The set of targets this matrix was supposed to produce. Either "
    "shape is accepted and recognized by its own content: an expected-target "
    'manifest (JSON: {"targets": [{"id", "required"}]}), or an `abicheck '
    "project plan` run-plan.json (declaring schema: abicheck.run-plan/vN), "
    "projected to the manifest shape internally so each check's own check_id "
    "becomes the expected target id — which is what `check-target` writes as "
    "every report's target_id. The single source of truth for the expected "
    "set: generate it in the plan job and feed the same file to both the "
    "matrix and this gate so they never drift.",
)
@click.option(
    "--discovered-only",
    "discovered_only",
    is_flag=True,
    default=False,
    help="Explicitly aggregate whatever reports are present, with NO coverage "
    "gate. Required to run without --manifest — because with no declared "
    "target set the gate cannot tell a missing required target from an "
    "intentionally absent one. Deliberately a flag of its own rather than "
    "something inferred from an absent or empty manifest: it states that the "
    "operator has no expected inventory, which no document's contents can "
    "say for them.",
)
@export_options(["text", "json"], default_format="text")
@verbose_option
def aggregate_cmd(
    reports_dir: Path,
    manifest: Path | None,
    discovered_only: bool,
    exports: ExportSet,
    verbose: bool,
) -> None:
    """Aggregate per-target ABI reports in REPORTS_DIR into one CI gate verdict.

    REPORTS_DIR holds the per-target ``compare``/``scan`` JSON reports
    downloaded from the build matrix (one ``abi-report-<target>.json`` per
    leg). Provide the expected-target set with ``--manifest`` -- either a
    hand-authored expected-target manifest or an ``abicheck project plan``
    run-plan.json, recognized by the document's own schema rather than by
    its filename (plan slice 7q retired the separate ``--run-plan`` flag) --
    or opt into ``--discovered-only`` to aggregate whatever is present with
    no coverage gate.

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

    expected, policy_source_hint = _resolve_expected(manifest, discovered_only)

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

    emit_export_set(
        exports,
        lambda fmt: (
            json.dumps(render_aggregate_json(result), indent=2)
            if fmt == "json"
            else render_aggregate_text(result)
        ),
    )

    sys.exit(result.exit_code())


def _resolve_expected(
    manifest: Path | None,
    discovered_only: bool,
) -> tuple[ExpectedTargets | None, str]:
    """Resolve the expected-target set from *manifest*, or usage error.

    ``--discovered-only`` and ``--manifest`` are two distinct statements
    about the expected set -- "there is no declared inventory" and "here it
    is" -- so combining them stays ambiguous and stays rejected. (The ad-hoc
    ``--expect``/``--optional`` id lists were a third; they are gone -- an
    expected-target set is a file the plan job and the gate share, and
    retyping it on the command line was the drift the manifest exists to
    prevent. ``--run-plan`` was a fourth, retired by plan slice 7q: it named
    a second *schema* for this same input, which the document itself
    already declares.)

    Returns the expected-target set plus a ``policy_source_hint`` label
    (``"manifest"``/``"run-plan"``) naming which shape it came from --
    :func:`~.workflows.aggregate.resolve_gate_policy` reports this back in
    the result's ``effective_policy.source`` whenever that source's own
    ``gate`` block actually supplied a value. Both shapes are read through
    :meth:`ExpectedTargets.from_manifest_data`, so the field cannot tell
    them apart; the classifier can, and says which.
    """
    if discovered_only:
        if manifest is not None:
            raise click.UsageError(
                "--discovered-only cannot be combined with --manifest"
            )
        return None, "default"
    if manifest is None:
        raise click.UsageError(
            "no expected-target set: pass --manifest (the targets the matrix "
            "must produce, as an expected-target manifest or an `abicheck "
            "project plan` run-plan.json), or --discovered-only to aggregate "
            "whatever is present with no coverage gate"
        )

    try:
        kind, document = classify_expected_input(manifest)
    except ExpectedInputError as exc:
        raise click.UsageError(str(exc)) from exc

    if kind is ExpectedInputKind.RUN_PLAN:
        return _expected_from_run_plan(manifest, document)
    try:
        return ExpectedTargets.from_manifest_data(document), "manifest"
    except AggregateError as exc:
        raise click.UsageError(f"{manifest}: {exc}") from exc


def _expected_from_run_plan(
    path: Path, document: dict[str, Any]
) -> tuple[ExpectedTargets | None, str]:
    """Project an already-classified run-plan document to the expected set."""
    from .buildsource.run_plan import RunPlan, to_aggregate_manifest

    try:
        plan = RunPlan.from_dict(document)
    except AggregateError as exc:
        raise click.UsageError(f"{path}: {exc}") from exc
    try:
        return (
            ExpectedTargets.from_manifest_data(to_aggregate_manifest(plan)),
            "run-plan",
        )
    except AggregateError as exc:
        raise click.UsageError(
            f"{path}: {exc} — an empty run-plan.json has no targets to "
            "aggregate; regenerate it with `abicheck project plan` once at "
            "least one check resolves, or aggregate with --discovered-only "
            "instead"
        ) from exc

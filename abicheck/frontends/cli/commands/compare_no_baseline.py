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

"""``abicheck compare --no-baseline NEW`` -- the CLI-layer half (ADR-068 D2,
plan §6 Phase 2e).

Dispatched from :func:`abicheck.frontends.cli.commands.compare.compare_cmd`
*before* any of the two-sided pipeline runs, so a normal `compare OLD NEW`
invocation never touches this module at all (every existing two-sided
invocation stays bit-for-bit unchanged). All the candidate-side work --
resolving NEW, self-diffing it, recording OLD's `declared_absent`
acquisition state -- lives in :mod:`abicheck.workflows.no_baseline_compare`;
the report shape lives in :mod:`abicheck.report.no_baseline`. This module
only translates CLI options in, and the resulting report out.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from ....report.no_baseline import (
    no_baseline_exit_code,
    no_baseline_json_report,
    no_baseline_markdown_report,
)
from ....workflows.no_baseline_compare import (
    resolve_no_baseline_candidate,
    run_no_baseline_compare,
)
from ..options.params import _load_suppression_and_policy
from ..runtime import _write_or_echo

__all__ = ["maybe_dispatch_no_baseline_compare"]

#: Formats this Phase 2e slice can render faithfully. `sarif`/`html`/`junit`/
#: `review` all frame their output around a compatibility verdict, which a
#: `--no-baseline` audit deliberately never has -- rather than force a
#: misleading rendering through machinery built for a two-sided verdict,
#: those stay a declared usage error until a later phase of
#: `docs/contribute/plans/one-comparison-product.md` gives them one.
_SUPPORTED_FORMATS = frozenset({"json", "markdown"})


def maybe_dispatch_no_baseline_compare(
    ctx: click.Context, kwargs: dict[str, Any]
) -> bool:
    """Validate and run a ``--no-baseline`` invocation if *kwargs* asked for
    one; returns ``True`` when it did (the caller must stop -- the whole
    two-sided pipeline below is skipped), ``False`` for an ordinary
    two-operand ``compare OLD NEW``.

    ADR-068 D2: ``--no-baseline`` is an explicit declaration, never inferred
    from arity -- ``compare NEW`` (one operand, no flag) and
    ``compare --no-baseline OLD NEW`` (the flag plus two operands) are both
    usage errors (exit 64), raised here rather than left to Click's own
    argument arity (which cannot express "required unless a flag is set").
    """
    no_baseline = kwargs.pop("no_baseline", False)
    if not no_baseline:
        if kwargs.get("new_input") is None:
            raise click.UsageError("Missing argument 'NEW_INPUT'.")
        return False
    if kwargs.get("new_input") is not None:
        raise click.UsageError(
            "--no-baseline takes exactly one operand (the candidate build); "
            "OLD is declared absent, so a second path is not accepted. Run "
            "`abicheck compare OLD NEW` (without --no-baseline) to compare "
            "against a real baseline."
        )
    candidate = kwargs.pop("old_input")
    kwargs.pop("new_input", None)
    if candidate.is_dir():
        raise click.UsageError(
            "--no-baseline does not support a directory/package operand yet "
            "-- pass a single artifact (a binary or a stored snapshot)."
        )
    _run_no_baseline_compare_cmd(ctx, candidate, **kwargs)
    return True


def _run_no_baseline_compare_cmd(
    ctx: click.Context, candidate: Path, **kwargs: Any
) -> None:
    """Run and report a ``compare --no-baseline`` audit of *candidate*."""
    fmt = kwargs.get("fmt") or "markdown"
    if fmt not in _SUPPORTED_FORMATS:
        raise click.UsageError(
            f"--no-baseline does not support --format {fmt} yet -- only "
            f"{'/'.join(sorted(_SUPPORTED_FORMATS))} are available for an "
            "audit report."
        )
    output = kwargs.get("output")

    headers = list(kwargs.get("headers") or ()) + list(
        kwargs.get("new_headers_only") or ()
    )
    includes = list(kwargs.get("includes") or ()) + list(
        kwargs.get("new_includes_only") or ()
    )
    lang = kwargs.get("lang") or "c++"
    lang_src = ctx.get_parameter_source("lang") if ctx is not None else None
    lang_explicit = lang_src == click.core.ParameterSource.COMMANDLINE
    public_headers = list(kwargs.get("public_headers") or ())
    public_header_dirs = list(kwargs.get("public_header_dirs") or ())

    new_snapshot = resolve_no_baseline_candidate(
        candidate,
        headers=headers,
        includes=includes,
        lang=lang,
        lang_explicit=lang_explicit,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
    )

    suppression, policy_file_obj = _load_suppression_and_policy(
        kwargs.get("suppress"),
        kwargs.get("policy") or "strict_abi",
        kwargs.get("policy_file_path"),
    )

    result = run_no_baseline_compare(
        new_snapshot,
        suppression=suppression,
        policy=kwargs.get("policy") or "strict_abi",
        policy_file=policy_file_obj,
        scope_to_public_surface=bool(kwargs.get("scope_public_headers", True)),
        pattern_verdicts=bool(kwargs.get("pattern_verdicts", False)),
        collapse_versioned_symbols=bool(
            kwargs.get("collapse_versioned_symbols", False)
        ),
    )

    require_complete_analysis = bool(kwargs.get("require_complete_analysis", False))

    text = (
        json.dumps(no_baseline_json_report(result), indent=2)
        if fmt == "json"
        else no_baseline_markdown_report(result)
    )
    _write_or_echo(output, text)

    exit_code = no_baseline_exit_code(
        result, require_complete_analysis=require_complete_analysis
    )
    if exit_code != 0:
        sys.exit(exit_code)

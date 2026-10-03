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
resolving NEW, auditing it, recording OLD's `declared_absent` acquisition
state -- lives in :mod:`abicheck.workflows.no_baseline_compare`; the report
shape lives in :mod:`abicheck.report.no_baseline`. Validating and resolving
the invocation itself lives in :mod:`.no_baseline_invocation`, shared with
the N-library audit (:mod:`.compare_no_baseline_set`) this module routes a
directory/package operand to. This module only dispatches, and translates a
scalar audit's report out.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from ....report.no_baseline import render_no_baseline
from ....workflows.no_baseline_compare import (
    NoBaselineCompareResult,
    audit_no_baseline_candidate,
    candidate_is_live_artifact,
)
from ..runtime import _safe_write_output, _write_or_echo
from .no_baseline_invocation import (
    ResolvedNoBaselineInvocation,
    audit_inputs,
    load_policy_documents,
    resolve_no_baseline_invocation,
    validate_no_baseline_invocation,
)
from .no_baseline_rulings import (
    _INERT_DESTS as _INERT_DESTS,  # noqa: F401  — re-exported: the options test asserts against these tables
    _OLD_ONLY_DESTS as _OLD_ONLY_DESTS,  # noqa: F401
    _SET_ONLY_OPTIONS as _SET_ONLY_OPTIONS,  # noqa: F401
    _SIDED_LABEL_DESTS as _SIDED_LABEL_DESTS,  # noqa: F401
    _SIDED_SINGLE_DESTS as _SIDED_SINGLE_DESTS,  # noqa: F401
    _UNSUPPORTED_OPTIONS as _UNSUPPORTED_OPTIONS,  # noqa: F401
    _VIEW_DEFAULTS as _VIEW_DEFAULTS,  # noqa: F401
    _reject_context_stashed_options,
    _reject_unsupported_options as _reject_unsupported_options,  # noqa: F401
    _reject_view_tokens_for_no_baseline,
    _was_given as _was_given,  # noqa: F401
)

__all__ = ["maybe_dispatch_no_baseline_compare"]

#: The operand kinds (``cli_resolve.classify_compare_operand``) that are a
#: *set* of libraries -- the same two a two-sided ``compare`` fans out over.
_SET_OPERAND_KINDS = frozenset({"directory", "package"})


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

    The operand is classified by the *same* classifier a two-sided
    ``compare`` uses (``cli_resolve.classify_compare_operand``): a directory
    of libraries, a package archive, or a multi-artifact/degraded stored
    ``ProjectSnapshot`` package is a set, audited per member
    (:mod:`.compare_no_baseline_set`); everything else is one artifact. A
    narrower ``is_dir()`` test used to stand here, which sent a ``.deb``/
    ``.rpm``/``.whl``/``.tar.gz`` archive -- and a multi-artifact stored
    package -- to the scalar resolver, auditing a set as if it were one
    complete artifact.
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
    _reject_view_tokens_for_no_baseline(kwargs)
    from ....cli_resolve import classify_compare_operand

    kind = classify_compare_operand(candidate)
    if kind in _SET_OPERAND_KINDS:
        from .compare_no_baseline_set import run_no_baseline_set_cmd

        run_no_baseline_set_cmd(ctx, candidate, kind, kwargs)
        return True
    _run_no_baseline_compare_cmd(ctx, candidate, **kwargs)
    return True


def _emit_no_baseline_report(
    result: NoBaselineCompareResult, inv: ResolvedNoBaselineInvocation
) -> None:
    """Render the audit in every requested format, then exit accordingly."""
    text, exit_code = render_no_baseline(
        result,
        inv.output.fmt,
        require_complete_analysis=inv.output.require_complete_analysis,
        audit_gate_enabled=inv.output.audit_gate_enabled,
    )
    _write_or_echo(inv.output.output, text)
    for write_fmt, write_path in inv.output.secondary_writes:
        # Rendered from the *same* result, never a second run -- ADR-068 D4's
        # "presentation never changes analysis" applies here exactly as it
        # does to the two-sided path, and the exit code is the primary
        # render's, identical by construction since both project one
        # document. Written through the same writer `-o/--output` uses, which
        # creates a missing parent directory and turns a write failure into a
        # clean Click error rather than a traceback on an otherwise-complete
        # run (Codex review, P2).
        rendered, _ = render_no_baseline(
            result,
            write_fmt,
            require_complete_analysis=inv.output.require_complete_analysis,
            audit_gate_enabled=inv.output.audit_gate_enabled,
        )
        _safe_write_output(write_path, rendered)
    if exit_code != 0:
        sys.exit(exit_code)


def _run_no_baseline_compare_cmd(
    ctx: click.Context, candidate: Path, **kwargs: Any
) -> None:
    """Run and report a ``compare --no-baseline`` audit of *candidate*.

    Four phases, one each: refuse what this path cannot honour, resolve what
    it can, run the audit, report it.
    """
    validate_no_baseline_invocation(kwargs)
    _reject_context_stashed_options(ctx)
    inv = resolve_no_baseline_invocation(ctx, kwargs)

    if inv.output.dry_run:
        from ....dry_run import emit_dry_run
        from ....model.macro_definition import define_spellings_from_tokens
        from ..no_baseline_dry_run import build_no_baseline_dry_run_result

        # Load the read-only policy documents *before* previewing. `--dry-run`
        # promises to resolve and validate the invocation, and these are part
        # of it: a malformed `--suppress`/`--policy-file` is exit 64 on the
        # real run, so a preview that exits 0 approves a run that cannot
        # start (Codex review, P2). Cheap and side-effect-free -- reading two
        # config files is not the analysis `--dry-run` exists to skip -- and
        # the result is discarded, since the preview needs the *validation*,
        # not the rules.
        load_policy_documents(inv, kwargs)
        emit_dry_run(
            build_no_baseline_dry_run_result(
                # Read off the resolved context, not the raw --define values,
                # so config-supplied macros are reported too and the receipt
                # cannot drift from the run it previews.
                defines=define_spellings_from_tokens(
                    inv.compile.context.gcc_option_tokens
                    if inv.compile.context is not None
                    else ()
                ),
                candidate=candidate,
                depth=inv.evidence.depth,
                headers=tuple(inv.headers.headers),
                includes=tuple(inv.headers.includes),
                public_header_dirs=tuple(inv.headers.public_header_dirs),
                sources=inv.evidence.sources,
                build_info=inv.evidence.build_info,
                fmt=inv.output.fmt,
                contract_mode=kwargs.get("contract_mode"),
                # The same carve-out the real run applies, from the same
                # helper -- so the preview and the run can never disagree
                # about whether the depth floor bites.
                candidate_is_live=candidate_is_live_artifact(
                    candidate,
                    sources=inv.evidence.sources,
                    build_info=inv.evidence.build_info,
                ),
            )
        )

    # The policy documents are loaded before the candidate is resolved -- the
    # same order an N-library audit needs (one malformed `--suppress` must be
    # one usage error, not N member failures), so both shapes share it.
    suppression, policy_file_obj = load_policy_documents(inv, kwargs)
    inputs = audit_inputs(
        inv,
        suppression=suppression,
        policy=kwargs.get("policy") or "strict_abi",
        policy_file=policy_file_obj,
    )
    _emit_no_baseline_report(_audit_or_fail(candidate, inputs), inv)


def _audit_or_fail(candidate: Path, inputs: Any) -> NoBaselineCompareResult:
    """Audit the candidate, translating a failure at the CLI boundary.

    ``audit_no_baseline_candidate`` raises framework-free errors
    (``SnapshotError`` for an unreadable/unparseable artifact, a header set
    that will not parse, a missing frontend), which is correct for a workflow
    -- but this is the CLI boundary, and without translation the exception
    escapes as a full traceback. The two-sided path already converts the same
    failures (``cli_resolve``'s own ``run_dump`` wrapper), so an identical
    input produced a clean ``Error: ...`` there and a stack trace here (Codex
    review, P2). ``ValidationError`` maps to a usage error (exit 64) and
    ``SnapshotError`` to a plain operational failure, matching that wrapper
    exactly rather than inventing a second mapping.
    """
    from ....errors import SnapshotError, ValidationError

    try:
        return audit_no_baseline_candidate(candidate, inputs)
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc

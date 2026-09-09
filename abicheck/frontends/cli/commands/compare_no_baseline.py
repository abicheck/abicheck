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

import sys
from pathlib import Path
from typing import Any

import click

from ....report.no_baseline import (
    NO_BASELINE_SUPPORTED_FORMATS,
    NO_BASELINE_UNSUPPORTED_FORMATS,
    render_no_baseline,
)
from ....workflows.no_baseline_compare import (
    candidate_is_live_artifact,
    public_header_sets_for_candidate,
    resolve_no_baseline_candidate,
    run_no_baseline_compare,
)
from ..options.contract import resolve_contract_domain, resolve_contract_evaluation
from ..options.params import _load_suppression_and_policy
from ..runtime import _safe_write_output, _write_or_echo

__all__ = ["maybe_dispatch_no_baseline_compare"]

#: Formats a ``--no-baseline`` audit renders, and the two that stay a usage
#: error. Both sets, and the ruling behind the split, are owned by
#: :mod:`abicheck.report.no_baseline` -- the report layer decides what it
#: can project; this module only translates that into a Click error.
_SUPPORTED_FORMATS = NO_BASELINE_SUPPORTED_FORMATS


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
    _reject_view_tokens_for_no_baseline(kwargs)
    _run_no_baseline_compare_cmd(ctx, candidate, **kwargs)
    return True


#: The values `--view` resolves into, and each one's "nothing requested"
#: default -- anything else means a real `--view` token was given.
_VIEW_DEFAULTS: dict[str, object] = {
    "report_mode": "full",
    "show_only": None,
    "demangle": None,
    "explain_patterns": False,
    # Phase 5 additions (Codex review, PR #1180, fresh evidence): a
    # no-baseline audit has no scope/disposition ledger and no suppression
    # audit either -- it reports one hand-built, un-suppressed, always-
    # in-scope empty change set by construction -- so these two are exactly
    # as unsupported as the four above, not a silent no-op.
    "show_filtered": False,
    "audit_suppressions": False,
}


def _reject_view_tokens_for_no_baseline(kwargs: dict[str, Any]) -> None:
    """Reject any non-default ``--view`` token for a ``--no-baseline`` audit.

    Codex review, fresh evidence ("Reject unsupported views for no-baseline
    audits"): a `--no-baseline` report is a self-diff audit against nothing
    (an empty change set, by construction -- see `report/no_baseline.py`'s
    own module docstring) -- it has no root-cause graph for `leaf`/
    `root-cause` to restructure, no per-library `DiffResult` for `impact`
    to summarize, no findings list for `show=...` to filter, no symbol
    table for `demangle` to affect, and no pattern-modulation ledger for
    `patterns` to echo. Silently accepting any of them (`parse_view_tokens`
    resolved them, but this dispatch never reads the result) reads as "your
    selector was honored" when nothing changed at all -- the same class of
    gap `_dispatch_release_compare` already guards against for its own
    unsupported view modes.
    """
    for name, default in _VIEW_DEFAULTS.items():
        value = kwargs.get(name, default)
        if value != default:
            raise click.UsageError(
                "--view is not available together with --no-baseline: a "
                "no-baseline audit has no root-cause graph, findings list, "
                "or pattern-modulation ledger for --view to act on (it "
                "reports an empty change set by construction). Drop --view "
                "for this operand."
            )


#: Evidence dests that only ever hold an explicitly ``old=``-prefixed value
#: (``cli_options.normalize_sided_options`` routes a bare/``both=``
#: ``--header``/``--include`` to its own separate ``headers``/``includes``
#: dest), mapped to the CLI spelling that produced them so a usage error
#: names what the user typed. Any truthy value here is OLD-scoped.
_OLD_ONLY_DESTS: dict[str, str] = {
    "old_headers_only": "--header old=",
    "old_includes_only": "--include old=",
}

#: Evidence dests whose bare/``both=`` value lands on *both* sides
#: (``cli_options._split_sided_single``: a bare path sets ``old`` and ``new``
#: to the same value, an ``old=`` prefix sets only ``old``). So "the user
#: scoped this to OLD" is not "the old dest is set" -- it is "the old dest is
#: set to something the new dest did not also get". Getting this wrong the
#: other way rejects an ordinary bare ``--sources tree/``, which is the
#: single most useful spelling on this path.
_SIDED_SINGLE_DESTS: dict[str, tuple[str, str]] = {
    "old_sources": ("new_sources", "--sources old="),
    "old_build_info": ("new_build_info", "--build-info old="),
    "old_dump_manifest": ("new_dump_manifest", "--dump-manifest old="),
}


def _reject_old_sided_inputs(kwargs: dict[str, Any]) -> None:
    """Reject any explicitly OLD-scoped evidence input for a one-sided audit.

    A ``--no-baseline`` run has no OLD side, so an ``old=``-scoped input asks
    it to feed a side it was told does not exist. Silently ignoring the value
    reads as "honored" when it was dropped -- the same class of guard as
    :func:`_reject_view_tokens_for_no_baseline` above. A bare or ``both=``
    value is *not* rejected: it applies to the candidate, which is the whole
    point of passing it.
    """
    for dest, spelling in _OLD_ONLY_DESTS.items():
        if kwargs.get(dest):
            raise click.UsageError(_old_sided_message(spelling))
    for dest, (new_dest, spelling) in _SIDED_SINGLE_DESTS.items():
        old_value = kwargs.get(dest)
        if old_value is not None and old_value != kwargs.get(new_dest):
            raise click.UsageError(_old_sided_message(spelling))


def _old_sided_message(spelling: str) -> str:
    return (
        f"{spelling} is not available together with --no-baseline: the OLD side "
        "is declared absent, so there is no baseline for this evidence to "
        "describe. Drop the 'old=' prefix to apply it to the candidate build "
        "instead."
    )


#: Every ``compare`` option this audit path does **not** implement, mapped
#: to its CLI spelling and the reason. Passing one is a usage error rather
#: than a silent no-op.
#:
#: This table exists because "accepted but never read" is the single defect
#: this whole module has now produced four separate times -- ``--contract``,
#: ``--sources``/``--build-info``/``--depth``/``--dry-run``, ``--write``,
#: and ``--include-system-declarations`` (see ``docs/contribute/known-gaps.md``).
#: Each was found by reading the code, never by a failing test, because a
#: dropped option produces no output at all. Fixing them one at a time
#: leaves the *class* open: the next option added to ``compare`` inherits
#: the same silence. So the rule is inverted here -- an option this path
#: does not implement must be listed, and
#: ``tests/test_compare_no_baseline_options.py`` fails if any ``compare``
#: parameter is neither read by this module nor named below. A new
#: ``compare`` flag therefore cannot reach ``--no-baseline`` silently; it is
#: either wired or declared.
#:
#: Three reason families, so the message tells the user which it is:
#:
#: * *no baseline to speak of* -- the option describes a comparison
#:   (consumer scoping, variant selection, a stored bundle-facts pair).
#: * *not a single artifact* -- the option is for the directory/package
#:   release fan-out, which ``--no-baseline`` does not accept anyway.
#: * *not implemented yet* -- genuinely applicable to a one-sided audit and
#:   simply not wired. These are the ones worth closing next; they are
#:   listed rather than silently accepted so that is a visible decision.
_UNSUPPORTED_OPTIONS: dict[str, tuple[str, str]] = {
    # -- describes a comparison this run never performs -------------------
    "used_by_apps": (
        "--used-by",
        "consumer scoping answers 'does this change break a consumer', which "
        "needs two versions to compare",
    ),
    "required_symbols_opt": (
        "--required-symbol",
        "an entrypoint contract is checked against what a comparison removed; "
        "with no baseline nothing can have been removed",
    ),
    "used_by_manifests": (
        "--used-by-manifest",
        "a consumer manifest merges into the --used-by pipeline, which needs "
        "two versions to compare",
    ),
    "use_cases_manifest": (
        "--use-cases",
        "use-case attribution maps a comparison's findings to declared use "
        "cases; an audit's findings are not changes",
    ),
    "post_manifest_path": (
        "--post-manifest",
        "a post-manifest overlays contract scope across two sides",
    ),
    "env_matrix_path": (
        "--env-matrix",
        "an environment matrix compares runtime floors across two builds",
    ),
    "diagnostic_comparison": (
        "--diagnostic-comparison",
        "ADR-050's escape hatch downgrades an incomparable-pair failure; a "
        "self-compare is trivially comparable",
    ),
    "old_variant": ("--old-variant", "there is no OLD side to select a variant of"),
    "new_variant": (
        "--new-variant",
        "variant selection picks matching builds from two sides",
    ),
    "bundle_facts_out": (
        "--bundle-facts-out",
        "bundle facts record a two-sided release comparison",
    ),
    "bundle_facts_library_manifest": (
        "--bundle-facts-library-manifest",
        "bundle facts record a two-sided release comparison",
    ),
    "since": (
        "--since",
        "changed-path localization narrows a comparison to what a revision "
        "range touched",
    ),
    "changed_paths_opt": (
        "--changed-path",
        "changed-path localization narrows a comparison to what a revision "
        "range touched",
    ),
    # -- for the directory/package release fan-out ------------------------
    "select": ("--select", "member selection applies to a directory/package operand"),
    "select_required": (
        "--select-required",
        "member selection applies to a directory/package operand",
    ),
    "output_dir": (
        "--output-dir",
        "per-library output applies to the release fan-out; use -o/--output "
        "or --write for a single artifact",
    ),
    # -- applicable, simply not wired yet ---------------------------------
    "abi3": (
        "--abi3",
        "the stable-ABI audit is candidate-side and belongs here, but is not "
        "wired to this path yet",
    ),
    "budget": (
        "--budget",
        "the wall-clock guard is not wired to this path yet",
    ),
    "severity_preset": (
        "--severity-preset",
        "an audit's findings are advisory and never gate, so a severity "
        "preset has nothing to act on yet",
    ),
    "pack_paths": (
        "--pack",
        "pack application is not wired to this path yet",
    ),
    "config": (
        "--config",
        "project-config resolution is not wired to this path yet",
    ),
    "manifest_path": (
        "--instantiation-manifest",
        "template-instantiation evidence is not wired to this path yet",
    ),
    "follow_deps": (
        "--follow-deps",
        "the DT_NEEDED dependency walk is not wired to this path yet",
    ),
    "search_paths": (
        "--search-path",
        "dependency search paths only matter with --follow-deps",
    ),
    "ld_library_path": (
        "--ld-library-path",
        "dependency search paths only matter with --follow-deps",
    ),
    "debug_info": (
        "--debug-info",
        "separate debug-info resolution is not wired to this path yet",
    ),
    "devel_pkg": (
        "--devel-pkg",
        "development-package header discovery is not wired to this path yet",
    ),
}

#: Dests whose "nothing was passed" value is not ``None``/falsey, so a
#: presence test needs the sentinel rather than truthiness.
_UNSET_SENTINELS: tuple[object, ...] = (None, (), "", False)


def _was_given(value: object) -> bool:
    """Whether a Click parameter value represents something the user typed.

    ``compare`` uses a mix of ``None``, ``()``, ``""``, ``False`` and an
    ``UNSET`` sentinel for "not given" (the sentinel exists so a config
    layer can tell an explicit value from a default). Treated uniformly
    here: anything outside :data:`_UNSET_SENTINELS`, and not the sentinel
    itself, was stated.
    """
    if value is None:
        return False
    if type(value).__name__ == "Sentinel" or repr(value).startswith("Sentinel."):
        return False
    return value not in _UNSET_SENTINELS


def _reject_unsupported_options(kwargs: dict[str, Any]) -> None:
    """Reject any option :data:`_UNSUPPORTED_OPTIONS` names, if it was given."""
    for dest, (spelling, reason) in _UNSUPPORTED_OPTIONS.items():
        if _was_given(kwargs.get(dest)):
            raise click.UsageError(
                f"{spelling} is not available with --no-baseline: {reason} "
                "(ADR-068 D2). It is rejected rather than silently ignored so "
                "a CI job never believes it took effect."
            )


def _run_no_baseline_compare_cmd(
    ctx: click.Context, candidate: Path, **kwargs: Any
) -> None:
    """Run and report a ``compare --no-baseline`` audit of *candidate*."""
    fmt = kwargs.get("fmt") or "markdown"
    if fmt not in _SUPPORTED_FORMATS:
        raise click.UsageError(_unsupported_format_message(fmt))
    output = kwargs.get("output")
    dry_run = bool(kwargs.get("dry_run", False))

    from ....dry_run import reject_dry_run_with_output
    from ..options import reject_incoherent_secondary_writes

    # ADR-068 D4: "a complete machine-readable result must always be
    # obtainable without a second run" -- `--write` was accepted and silently
    # dropped on this path (the same silently-inert-flag class as `--contract`
    # and the evidence options above), so a job asking for a JSON artifact
    # alongside a human-readable one got nothing and no warning. The two
    # coherence rules (`--dry-run` writes nothing; a secondary PATH may not
    # collide with `-o`) come from the shared guard both `compare` forms use,
    # not a second copy.
    secondary_writes: tuple[tuple[str, Path], ...] = tuple(
        kwargs.get("secondary_writes") or ()
    )
    reject_dry_run_with_output(dry_run, output)
    reject_incoherent_secondary_writes(
        dry_run=dry_run, output=output, secondary_writes=secondary_writes
    )
    for write_fmt, _path in secondary_writes:
        if write_fmt not in _SUPPORTED_FORMATS:
            raise click.UsageError(
                _unsupported_format_message(write_fmt, flag="--write")
            )
    _reject_old_sided_inputs(kwargs)
    _reject_unsupported_options(kwargs)

    headers = list(kwargs.get("headers") or ()) + list(
        kwargs.get("new_headers_only") or ()
    )
    includes = list(kwargs.get("includes") or ()) + list(
        kwargs.get("new_includes_only") or ()
    )
    lang = kwargs.get("lang") or "c++"
    lang_src = ctx.get_parameter_source("lang") if ctx is not None else None
    lang_explicit = lang_src == click.core.ParameterSource.COMMANDLINE
    public_headers, public_header_dirs = public_header_sets_for_candidate(
        headers,
        list(kwargs.get("public_headers") or ()),
        list(kwargs.get("public_header_dirs") or ()),
    )

    # A bare/`both=` --sources/--build-info lands on *both* per-side dests
    # (`cli_options._split_sided_single`); `_reject_old_sided_inputs` above
    # has already rejected an explicitly OLD-scoped one, so reading the NEW
    # dest here is exactly "the candidate's evidence".
    sources = kwargs.get("new_sources")
    build_info = kwargs.get("new_build_info")
    depth = kwargs.get("depth")

    if dry_run:
        from ....dry_run import emit_dry_run
        from ..no_baseline_dry_run import build_no_baseline_dry_run_result

        emit_dry_run(
            build_no_baseline_dry_run_result(
                candidate=candidate,
                depth=depth,
                headers=tuple(headers),
                includes=tuple(includes),
                public_header_dirs=tuple(public_header_dirs),
                sources=sources,
                build_info=build_info,
                fmt=fmt,
                contract_mode=kwargs.get("contract_mode"),
                # The same carve-out the real run applies below, from the
                # same helper -- so the preview and the run can never
                # disagree about whether the depth floor bites.
                candidate_is_live=candidate_is_live_artifact(candidate),
            )
        )

    new_snapshot = resolve_no_baseline_candidate(
        candidate,
        headers=headers,
        includes=includes,
        lang=lang,
        lang_explicit=lang_explicit,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        sources=sources,
        build_info=build_info,
        build_config=kwargs.get("build_config"),
        depth=depth,
        include_dependencies=bool(kwargs.get("include_dependencies", False)),
    )

    suppression, policy_file_obj = _load_suppression_and_policy(
        kwargs.get("suppress"),
        kwargs.get("policy") or "strict_abi",
        kwargs.get("policy_file_path"),
    )

    # ADR-049: `--contract VALUE` is what activates the evaluator on the CLI,
    # and `auto` maps back to "no explicit domain stated" so D7's lower tiers
    # decide -- resolved through the same two `cli_options` helpers
    # `cli_compare_helpers.run_compare` calls, so an equivalent one-sided and
    # two-sided invocation activate identically. Before this, `--contract`
    # was parsed and documented on this path but never read at all: the
    # contract-coverage ledger never populated, so the flag was a silently
    # inert gate (a CI job relying on it got no warning that it never ran).
    contract_mode_raw = kwargs.get("contract_mode")
    contract_evaluation = resolve_contract_evaluation(contract_mode_raw)
    contract_mode = resolve_contract_domain(contract_mode_raw, ctx)

    result = run_no_baseline_compare(
        new_snapshot,
        suppression=suppression,
        policy=kwargs.get("policy") or "strict_abi",
        policy_file=policy_file_obj,
        scope_to_public_surface=bool(kwargs.get("scope_public_headers", True)),
        # ADR-068 D4/Phase 5: pattern-verdict modulation is unconditional on
        # every `compare` path now (no `--pattern-verdicts` flag exists any
        # more) -- this audit-only path gets the identical treatment.
        pattern_verdicts=True,
        collapse_versioned_symbols=bool(
            kwargs.get("collapse_versioned_symbols", False)
        ),
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        # ADR-064's exit-7 axis: a pinned --depth build/source that this
        # run's evidence did not reach. Recorded by the workflow (after
        # classification, the same point the two-sided native CLI path
        # records it) rather than here, so a front end cannot pick up the
        # audit and forget the orthogonal axis.
        depth=depth,
        candidate_is_live=candidate_is_live_artifact(candidate),
    )

    require_complete = bool(kwargs.get("require_complete_analysis", False))
    text, exit_code = render_no_baseline(
        result, fmt, require_complete_analysis=require_complete
    )
    _write_or_echo(output, text)
    for write_fmt, write_path in secondary_writes:
        # Rendered from the *same* result, never a second run -- ADR-068 D4's
        # "presentation never changes analysis" applies here exactly as it
        # does to the two-sided path, and the exit code is the primary
        # render's, identical by construction since both project one document.
        rendered, _ = render_no_baseline(
            result, write_fmt, require_complete_analysis=require_complete
        )
        # The same writer `-o/--output` goes through, not a bare
        # `Path.write_text` -- it creates a missing parent directory and
        # translates a write failure into a clean Click error, so a
        # `--write json=out/dir/x.json` under a directory that does not
        # exist yet does not end an otherwise-complete analysis in a
        # FileNotFoundError traceback (Codex review, P2).
        _safe_write_output(write_path, rendered)
    if exit_code != 0:
        sys.exit(exit_code)


def _unsupported_format_message(fmt: str, *, flag: str = "--format") -> str:
    """The usage error for a ``--format`` this audit does not render.

    *flag* names the option the rejected value came from (``--format`` or
    ``--write``), so the message quotes what the user typed.

    Names the ruling rather than promising a later phase: ``html`` and
    ``review`` are narrative renderings of a *comparison* (a verdict badge,
    an OLD -> NEW headline, a release recommendation), none of which a
    single-build audit has -- see :data:`abicheck.report.no_baseline.
    NO_BASELINE_UNSUPPORTED_FORMATS` for the full reasoning.
    """
    supported = "/".join(sorted(_SUPPORTED_FORMATS))
    if fmt in NO_BASELINE_UNSUPPORTED_FORMATS:
        return (
            f"{flag} {fmt} is not available with --no-baseline: it renders a "
            "compatibility comparison (verdict, OLD -> NEW counts, release "
            "recommendation), and a single-build audit has none of those "
            f"(ADR-068 D2). Use one of {supported} instead; oneline is the "
            "closest equivalent to a review digest."
        )
    return (
        f"--no-baseline does not support {flag} {fmt} -- only {supported} "
        "are available for an audit report."
    )

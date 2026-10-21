# Copyright 2026 Nikolay Petrov
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

"""``abicheck compare``'s operand routing, shared by the command and its engine.

The directory/package release fan-out dispatch and the inline build-source
embedding a live-binary operand needs before the pair can be resolved. Kept
apart from :mod:`.compare` (which registers the command on the root group) so
the scalar engine in ``cli_compare_helpers`` can call these without importing
the command module back -- that import closed the ``cli`` ->
``commands.compare`` -> ``cli_compare_helpers`` -> ``commands.compare`` cycle.
The nested ``dump`` is looked up on the running command tree for the same
reason, rather than imported.
"""

from __future__ import annotations

from typing import Any

import click

from ....report.report_modes import normalize_report_mode

_RELEASE_FORMATS = frozenset({"json", "markdown", "junit", "oneline", "html"})


def reject_release_incompatible_view_mode(report_mode: str) -> None:
    """Reject a ``--view`` mode/token a directory/package release fan-out
    can't honor: ``leaf``/``root-cause`` restructure a single comparison's
    own root-cause graph, and the release summary is an aggregate report
    across every library with no single such graph to restructure. ``full``
    and ``impact`` (each library already has its own ``DiffResult`` to
    compute an impact table from) are the only accepted report modes here.

    Plan slice 7o removed this predicate's second half. ``--view filtered``
    used to be rejected here too, because the release engine never threaded
    ``show_filtered`` into its own renderer and so would have produced
    output identical to an invocation without the token. The token is gone
    -- the scope/reconciliation ledger is unconditional now -- so there is
    no request left to refuse; what remains is the same missing feature it
    always was (the release renderer still shows no per-library ledger),
    recorded in the plan's 7o section rather than expressed as a usage
    error for a spelling that no longer exists.

    Shared by ``_dispatch_release_compare``'s own check below and
    ``cli_compare_helpers.py``'s pre-``--dry-run`` rejection point (Codex
    review, fresh evidence: without the latter, ``compare --dry-run --view
    leaf`` on a directory/package operand exited 0 while the identical
    non-dry-run invocation exited 64 -- the one thing ``--dry-run`` promises
    not to do, per the sibling checks it already sits next to) -- one
    predicate, so the two call sites cannot silently drift apart.
    """
    if report_mode not in ("full", "impact"):
        raise click.UsageError(
            f"--view {report_mode} is not available when comparing directories "
            "or packages: 'leaf'/'root-cause' restructure a single comparison's "
            "own root-cause graph, and the release summary is an aggregate "
            "report across every library with no single such graph to "
            "restructure. Compare one library at a time (a single old/new "
            f".so pair) to use --view {report_mode}."
        )


def _dispatch_release_compare(ctx: click.Context, **kwargs: Any) -> None:
    """Fan a directory/package `compare` out to the per-library release engine.

    Routes through the same release engine (the unregistered `compare_release_cmd`,
    which fans out per library through the single Tier-2 `service.run_compare`
    chokepoint and writes the two-level summary/per-library output), so a library
    compared here gets the identical verdict it would from a single-pair `compare`
    (ADR-037 D1/D7). The standalone `compare-release` command was removed; this is
    now its only entry point.

    Calls ``compare_release_cmd.callback`` directly rather than
    ``ctx.invoke(compare_release_cmd, ...)`` (CLI-audit P2: "business logic
    depends on Click-to-Click orchestration") -- ``compare_release_cmd`` is
    itself never registered on `main` and exists solely to be called this way
    (see its own module comment), and every one of its ~44 parameters is
    already supplied explicitly by the caller below, so there is no Click
    default-filling for ``ctx.invoke`` to usefully do here; it was only ever
    creating a throwaway sub-``Context`` to call the same plain function.
    ``UsageError``/``BadParameter`` normally get ``e.ctx`` backfilled by
    ``ctx.invoke``'s ``augment_usage_errors`` wrapper for display purposes
    (a "Usage: ..." header on the formatted error) -- replicated by hand here
    so a validation error raised inside the release engine still formats
    identically to before.
    """
    fmt = kwargs.get("fmt", "markdown")
    # --view's derived values: show_only is threaded through the release
    # engine below (cli_compare_release.py). report_mode's "leaf"/
    # "root-cause" restructure a single DiffResult's own root-cause graph --
    # the release report has no such graph to restructure (the same mismatch
    # -o sarif=.../html/review hits below), so those two are rejected.
    # "impact" is threaded through as show_impact, since (unlike leaf/
    # root-cause) an impact summary is naturally per-library: each library
    # already has its own DiffResult, so
    # `project_release_library_results` computes one impact table per
    # library from it. Demangling is no longer a value to thread at all
    # (plan slice 7o): it is resolved per format, from the format alone.
    report_mode = kwargs.pop("report_mode", "full")
    kwargs["show_only"] = kwargs.pop("show_only", None)
    # Already validated ahead of the --dry-run emit (cli_compare_helpers.py's
    # pre-dry-run block) -- re-checked here too since _dispatch_release_
    # compare has its own direct callers/tests and must reject on its own,
    # not merely rely on an upstream caller having done so.
    reject_release_incompatible_view_mode(report_mode)
    # The `impact` -> (`full`, show_impact) fold is shared with every public
    # Python rendering path rather than re-spelled here (Codex review,
    # PR #1284): two private copies of it were why `report_mode="impact"`
    # worked through the CLI and silently did nothing through the typed API.
    report_mode, kwargs["show_impact"] = normalize_report_mode(report_mode)
    if fmt not in _RELEASE_FORMATS:
        raise click.UsageError(
            f"-o {fmt}=... is not available when comparing directories or "
            "packages: sarif/review require a single-pair (non-directory, "
            "non-package) comparison. Choose one of: "
            f"{', '.join(sorted(_RELEASE_FORMATS))}, or compare one library at "
            f"a time (a single old/new .so pair) to export {fmt}."
        )
    # `compare`'s own `-o` accepts sarif/html/review (a single-pair
    # comparison renders all three), and the export set is parsed before
    # this dispatch knows the operands are directories -- so a format this
    # fan-out cannot produce must be rejected here explicitly. Without it,
    # compare_release_cmd's own callback is reached directly (not through
    # Click's arg parsing, so its own decorator-level validation never runs)
    # and render_release_summary's fallback branch would silently render
    # markdown to the requested sarif/html/review destination instead of
    # erroring.
    #
    # Every export is checked, not just the first: the release engine renders
    # each one from the same already-computed result (one analysis, several
    # artifacts), so each must name a format it can actually produce.
    # Destination collisions and stdout exclusivity were already settled
    # while the operand was parsed (`frontends.cli.options.export`); only the
    # *format* restriction below is release-specific.
    secondary_writes = kwargs.get("secondary_writes", ())
    for secondary_fmt, _ in secondary_writes:
        if secondary_fmt not in _RELEASE_FORMATS:
            raise click.UsageError(
                f"-o {secondary_fmt}=... is not available when comparing "
                "directories or packages: sarif/review require a "
                "single-pair (non-directory, non-package) comparison. Choose "
                f"one of: {', '.join(sorted(_RELEASE_FORMATS))}, or compare one "
                "library at a time (a single old/new .so pair) to export "
                f"{secondary_fmt}."
            )
    from ....cli_compare_release import compare_release_cmd

    assert compare_release_cmd.callback is not None
    try:
        compare_release_cmd.callback(**kwargs)
    except click.UsageError as exc:
        if exc.ctx is None:
            exc.ctx = ctx
        raise

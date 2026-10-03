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

"""Which ``compare --no-baseline`` invocations this path refuses, and why.

Every table and predicate that answers "may this option reach the audit?"
lives here rather than beside the dispatch that runs one: they are one
responsibility (ADR-068 D2's rulings, stated as data plus the guards that
apply them), they are what a reader comes looking for when a flag is
rejected, and keeping them here is what holds
``commands/compare_no_baseline.py`` under the 800-line new-file ceiling
without trimming anything to fit.

The rule the tables encode is an inversion of Click's default: an option
this path cannot honour is a **usage error** (exit 64), never a silent
no-op. A dropped flag is how a CI job comes to believe a `--contract`, a
`-o`, or a `--variant` took effect when nothing read it, and every
entry below exists because some option did exactly that.

``tests/test_compare_no_baseline_options.py`` holds the exhaustiveness
contract over these tables: every parameter `compare` declares -- including
each destination ``normalize_sided_options`` generates, and each option
stashed on the context with ``expose_value=False`` -- is read, guarded, or
declared here, and no entry names an option that no longer exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    pass


_VIEW_DEFAULTS: dict[str, object] = {
    "report_mode": "full",
    "show_only": None,
}


def _reject_view_tokens_for_no_baseline(kwargs: dict[str, Any]) -> None:
    """Reject any non-default ``--view`` token for a ``--no-baseline`` audit.

    Codex review, fresh evidence ("Reject unsupported views for no-baseline
    audits"): a `--no-baseline` report is a self-diff audit against nothing
    (an empty change set, by construction -- see `report/no_baseline.py`'s
    own module docstring) -- it has no root-cause graph for `leaf`/
    `root-cause` to restructure, no per-library `DiffResult` for `impact`
    to summarize and no findings list for `show=...` to filter. (Plan slice
    7o retired the other four tokens outright -- demangling, the pattern
    ledger, the scope ledger and the suppression audit are unconditional
    now, and each is self-evidently empty for an audit with no findings, so
    there is nothing left to reject for them.) Silently accepting one of
    the two that remain (`parse_view_tokens`
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
    "debug_roots_old": "--debug-info old=",
    "debug_info1": "--debug-info old=",
    "devel_pkg1": "--header old=",
}

#: Destinations that are inert here *by construction*, with the reason.
#:
#: Empty today. ``old_version`` lived here on the claim that an explicit
#: ``--version old=`` could not be told from the default -- which was wrong,
#: and Codex said so: ``cli_options._split_sided_version`` defaults it to the
#: literal placeholder ``"old"``, so any *other* value was typed. What is
#: genuinely indistinguishable is narrower -- a bare ``--version 1.2`` sets
#: both sides, and that spelling must keep working, since labelling the
#: candidate is its whole point. So the rule is the sided-single one below
#: ("set to something the new dest did not also get"), not inertness, and
#: ``old_version`` moved there. Kept as a named table because a genuinely
#: inert destination is a decision the exhaustiveness test should see stated,
#: not an omission -- the next one goes here with its reason.
_INERT_DESTS: dict[str, str] = {}

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

#: The per-destination *default* for a sided dest that is never ``None``.
#:
#: ``old_version``/``new_version`` are the one such pair: `_split_sided_
#: version` always populates them, with the literal placeholders ``"old"``/
#: ``"new"``, so "the user typed something" is "the value is not the
#: placeholder" -- not "the value is set", which is always true, and not
#: "the two sides differ", which is also always true by default. Both
#: conditions have to hold together: not the default (so it was typed) *and*
#: different from the new side (so it was typed as ``old=`` specifically,
#: rather than as a bare ``--version 1.2`` that labels the candidate too --
#: the spelling this path most wants to keep working).
_SIDED_DEFAULTS: dict[str, str] = {"old_version": "old"}

#: The label half of the same rule. Separate from the evidence family above
#: only because it needs :data:`_SIDED_DEFAULTS`; the guard is one loop over
#: both. ``old_version`` was recorded as *inert* until Codex pointed out that
#: only its default is indistinguishable, not an explicit value.
#:
#: One residual, inherent to where this runs: ``--version old=1.2 --version
#: new=1.2`` arrives identical to the bare form, so it is accepted and the
#: redundant OLD label dropped.
_SIDED_LABEL_DESTS: dict[str, tuple[str, str]] = {
    "old_version": ("new_version", "--version old="),
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
        # `_was_given`, not truthiness: several of these default to Click's
        # `UNSET` sentinel, which is truthy -- guarding on truthiness rejected
        # every invocation, including ones passing nothing at all.
        if _was_given(kwargs.get(dest)):
            raise click.UsageError(_old_sided_message(spelling))
    for dest, (new_dest, spelling) in (
        *_SIDED_SINGLE_DESTS.items(),
        *_SIDED_LABEL_DESTS.items(),
    ):
        old_value = kwargs.get(dest)
        if old_value is None or old_value == _SIDED_DEFAULTS.get(dest):
            continue
        if old_value != kwargs.get(new_dest):
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
#: ``--sources``/``--build-info``/``--depth``/``--dry-run``, ``-o``,
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
#: * *two-sided release only* -- the option shapes the two-sided
#:   directory/package fan-out's output, which an audit does not produce.
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
    "diagnostic_comparison": (
        "--diagnostic-comparison",
        "this escape hatch downgrades an incomparable-pair failure; with "
        "the baseline declared absent there is no second contract to be "
        "incomparable with, so the check never runs",
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
    # -- for the two-sided release fan-out --------------------------------
    "output_dir": (
        "--output <format>=<directory>/",
        "a per-component export directory belongs to the two-sided release "
        "fan-out; name a file (or '-') -- a directory audit's JSON already "
        "carries every member's own report",
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
    "pack_paths": (
        "--pack",
        "pack application is not wired to this path yet",
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
    # Keyed on the destinations `normalize_sided_options` *generates*, not on
    # the raw option names: `compare_cmd` normalizes before dispatching, so a
    # guard keyed on `debug_info`/`header`/`dump_manifest` would check a
    # key that never exists and never fire (Codex review, P1 -- the same hole
    # that let a bare `--dump-manifest` through as a silent no-op).
    "debug_info2": (
        "--debug-info",
        "separate debug-info resolution is not wired to this path yet",
    ),
    # Named as the *transport*, not the bare flag: plain `-H/--header`
    # headers are supported on this path, and only the development-package
    # transport plan Phase 7n folded into it is not. A bare "--header" here
    # would make every documented `--no-baseline -H include/` example read
    # as rejected (`tests/test_docs_no_baseline_flag_examples.py` scans
    # these spellings).
    "devel_pkg2": (
        "--header <development package>",
        "development-package header discovery is not wired to this path yet "
        "(a plain header file/directory is fine -- this is the package "
        "transport plan Phase 7n folded into -H/--header)",
    ),
    "new_dump_manifest": (
        "--dump-manifest",
        "a dump manifest selects a multi-TU header surface and carries its own "
        "comparability contract, neither of which this path resolves yet -- it "
        "was silently ignored before, so even an invalid manifest exited 0 "
        "while the audit analysed a different surface than requested",
    ),
    # Same reason as `devel_pkg2` above: a plain `--build-info` compile
    # context is supported on this path; a probe matrix is not.
    "probe_matrix_old": (
        "--build-info <probe matrix>",
        "a build-configuration matrix is folded across two sides",
    ),
    "probe_matrix_new": (
        "--build-info <probe matrix>",
        "a build-configuration matrix is folded across two sides",
    ),
}

#: Options only a *set* operand -- a directory of libraries, a package
#: archive, a multi-artifact stored package (``compare --no-baseline DIR``,
#: one-comparison-product F-23) -- honours. Rejected for a scalar operand
#: exactly like :data:`_UNSUPPORTED_OPTIONS`, since one artifact has no
#: members to select among; read by ``compare_no_baseline_set.py`` otherwise.
_SET_ONLY_OPTIONS: dict[str, tuple[str, str]] = {
    "select": (
        "--select",
        "member selection applies to a directory/package operand, and a "
        "single artifact has no members to select among",
    ),
    "select_required": (
        "--select-required",
        "member selection applies to a directory/package operand, and a "
        "single artifact has no members to select among",
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


def _reject_unsupported_options(
    kwargs: dict[str, Any], *, operand_is_set: bool = False
) -> None:
    """Reject any option :data:`_UNSUPPORTED_OPTIONS` names, if it was given
    -- and, for a scalar operand, any :data:`_SET_ONLY_OPTIONS` one too."""
    table = (
        _UNSUPPORTED_OPTIONS
        if operand_is_set
        else {**_UNSUPPORTED_OPTIONS, **_SET_ONLY_OPTIONS}
    )
    for dest, (spelling, reason) in table.items():
        if _was_given(kwargs.get(dest)):
            raise click.UsageError(
                f"{spelling} is not available with --no-baseline: {reason}"
                ". It is rejected rather than silently ignored so "
                "a CI job never believes it took effect."
            )


def _reject_context_stashed_options(ctx: click.Context) -> None:
    """Reject an option whose value never reaches ``kwargs`` at all.

    ``--variant`` is declared with ``expose_value=False`` and stashed on the
    context by its own callback, so neither :data:`_UNSUPPORTED_OPTIONS` nor
    the exhaustiveness table can see it the way they see an ordinary
    destination -- it has to be read back the same way
    ``_dispatch_release_compare`` reads it. Without this it was accepted and
    silently dropped: ``compare --no-baseline snap.abi.json --variant v1``
    ran a normal audit and exited 0.

    Rejected for a *scalar* operand because one artifact offers no variant
    to select: a single-artifact stored ``ProjectSnapshot`` package (one
    artifact under one variant) is audited through that artifact. A package
    declaring several variants is a *set* operand, and there ``--variant``
    is wired -- see :func:`_set_variant_from_context`.
    """
    from ..options.release import variant_kwargs_from_context

    if any(variant_kwargs_from_context(ctx).values()):
        raise click.UsageError(
            "--variant is not available with --no-baseline for a single "
            "artifact: it selects among the variants a stored ProjectSnapshot "
            "package declares, and this operand has only one. It is rejected "
            "rather than silently ignored so a CI job never believes it took "
            "effect."
        )


def _set_variant_from_context(ctx: click.Context) -> str | None:
    """The ``--variant`` an N-library audit of a stored package selects.

    A stored ``ProjectSnapshot`` package declaring several variants is a set
    operand (``workflows.release_package.is_multi_artifact_package``), and
    choosing among its variants is exactly what ``--variant`` does for a
    two-sided release's NEW side -- so here it is wired, not rejected: a
    bare ``--variant ID`` or ``--variant new=ID`` selects the candidate's
    variant. An explicitly OLD-scoped value (``--variant old=ID`` without
    the same value for NEW) names a side this run declared absent, and is a
    usage error for the same reason every other ``old=`` input is.
    """
    from ..options.release import variant_kwargs_from_context

    variants = variant_kwargs_from_context(ctx)
    old, new = variants.get("old_variant"), variants.get("new_variant")
    if old is not None and old != new:
        raise click.UsageError(_old_sided_message("--variant old="))
    return new

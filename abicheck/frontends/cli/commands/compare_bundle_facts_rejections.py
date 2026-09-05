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

"""A stored-bundle-facts-OLD_INPUT ``compare``'s unsupported-option guards.

Split out of ``compare_bundle_facts.py`` (which sits at the architecture
no-growth 800-line production cap) purely to make room for the next
Codex-review-found gap without shrinking every existing comment a little
more each round -- this module is validation-only, no different in
behaviour from being inlined at the top of that module's own
``dispatch()``.

:func:`reject_unsupported_options` is pure: it only ever reads *kwargs*
(``compare_cmd``'s already-parsed, already-``normalize_sided_options``-
processed option dict) and either raises ``click.UsageError`` (exit 64) or
returns ``None``. It never mutates *kwargs* and computes nothing
``dispatch()`` needs afterward (``fmt``/``secondary_fmt``/``secondary_
output``/``depth`` are all cheap, side-effect-free re-reads of the same
dict, so ``dispatch()`` just re-derives them itself rather than this
function returning a tuple).

**Every flag `compare_bundle_facts.dispatch()` doesn't explicitly wire
through is rejected outright here** rather than silently ignored -- the
same "reject rather than silently diverge from the request" rule
``--dry-run``/``--contract`` set as this module's precedent. Each check
below carries its own comment explaining exactly why that flag has no
channel into ``compare_release_against_bundle_facts()`` (or, for the rare
flag with a real channel elsewhere -- ``--show-only``/``--report-mode`` on
``reporter.to_json()`` -- why honoring it only here would make that driver
disagree with the live release fan-out's own identical, pre-existing gap).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click


def reject_unsupported_options(kwargs: dict[str, Any], *, new_is_stored: bool = False) -> None:
    """Raise ``click.UsageError`` for any flag a stored-bundle-facts
    OLD_INPUT has no channel to honor. See this module's own docstring for
    the design.

    *new_is_stored* (CLI cleanup phase two, PR I), when true, means
    NEW_INPUT classified as a stored BundleFacts document too -- every
    check below still applies (neither stored side has any of these
    channels), plus the NEW-side-specific extraction options rejected at
    the bottom of this function, which only make sense when NEW_INPUT is a
    *live* directory/package (the default, ``False``, unchanged)."""
    fmt = kwargs.get("fmt", "json")
    if fmt not in ("json", "markdown"):
        raise click.UsageError(
            f"--format {fmt} is not available with a stored-bundle-facts OLD_INPUT: only "
            "json/markdown are supported for a stored-bundle-facts "
            "comparison. Choose one of: json, markdown."
        )
    secondary_fmt = kwargs.get("secondary_fmt")
    secondary_output: Path | None = kwargs.get("secondary_output")
    # dry_run=False: --dry-run is rejected outright for this mode below,
    # regardless of --write, so only the output/secondary-output collision
    # half of this shared check is relevant here.
    from ....frontends.cli.options import reject_incoherent_secondary_output

    reject_incoherent_secondary_output(
        dry_run=False,
        output=kwargs.get("output"),
        secondary_fmt=secondary_fmt,
        secondary_output=secondary_output,
    )
    if secondary_output is not None and secondary_fmt not in ("json", "markdown"):
        # Codex review: --write FORMAT=PATH was accepted (Click's own
        # --write validation allows every format the ordinary compare/
        # compare-release paths render: sarif/html/junit/review too) but
        # this dispatcher only ever renders json/markdown -- a secondary
        # format outside that pair exited successfully without ever writing
        # the promised second artifact. Rejected the same way an
        # unsupported primary --format is, rather than silently skipped.
        raise click.UsageError(
            f"--write {secondary_fmt}=... is not available with "
            "a stored-bundle-facts OLD_INPUT: only json/markdown are supported for a "
            "stored-bundle-facts comparison."
        )
    if kwargs.get("fail_on_removed"):
        raise click.UsageError(
            "--fail-on-removed-library is not supported together with "
            "a stored-bundle-facts OLD_INPUT: answering it would require re-scanning "
            "OLD_FACTS a second time, defeating the point of handing in an "
            "already-loaded facts document. Diff the stored facts' own "
            "per_library_snapshots keys against the release directory "
            "yourself if you need this accounting."
        )
    if kwargs.get("bundle_facts_out") is not None:
        raise click.UsageError(
            "--bundle-facts-out is not supported together with "
            "a stored-bundle-facts OLD_INPUT: the OLD side is already a stored facts "
            "document, so there is nothing new to persist from it."
        )
    if kwargs.get("dry_run"):
        # Codex review: without this, --dry-run silently ran the full
        # comparison anyway (load OLD_FACTS, discover/extract every matching
        # NEW library, diff, render, exit normally) -- the exact cost
        # --dry-run promises to skip, and especially costly for the large
        # bundles this whole flag exists for. Rejected rather than given a
        # real resolve-and-validate-only rendering of its own in this PR.
        raise click.UsageError(
            "--dry-run is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("contract_mode") is not None:
        # Codex review: compare_release_against_bundle_facts() has no
        # contract-evaluation parameter at all, so --contract was silently
        # accepted and ignored -- every per-library comparison ran with
        # contract evaluation off regardless of the requested domain,
        # producing a different finding set/verdict/exit code than asked
        # for with no indication anything was skipped. Rejected rather than
        # silently unscoped.
        raise click.UsageError(
            "--contract is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("severity_preset") is not None or kwargs.get("pack_paths"):
        # Codex review: --severity-preset/--pack drive run_compare's
        # _resolve_compare_config + pack-application path, neither of which
        # this dispatcher calls -- compare_release_against_bundle_facts()
        # has no severity/pack parameter to receive them, so every
        # per-library comparison always exits through the legacy verdict
        # mapping regardless of what was requested. Rejected rather than
        # silently scoring/gating the run differently than asked.
        raise click.UsageError(
            "--severity-preset/--pack are not supported together with "
            "a stored-bundle-facts OLD_INPUT."
        )
    # Codex review: reuse the exact rejection the live release fan-out
    # already applies to a directory/package operand -- this driver's own
    # NEW_INPUT is exactly that kind of operand (now including a package,
    # per the extraction fix in compare_bundle_facts.py), and every one of
    # these flags is equally unconsumed here (no per-library-pair scoping,
    # no single suppression-audit/analysis-assurance result to attach,
    # etc.). Not `_reject_flags_unsupported_for_set_inputs` (which also
    # calls `_reject_evidence_flags_for_set_inputs`): that helper rejects
    # `--depth` unconditionally, but `--depth binary` is a legitimate,
    # supported combination here (see the depth handling below) --
    # `--sources`/`--build-info`/`--dump-manifest` are rejected directly
    # instead, matching that helper's reasoning without its `--depth`
    # overreach.
    from ....cli_compare_options import _reject_set_input_flags

    _reject_set_input_flags(
        bool(kwargs.get("reconcile_build_context", False)),
        kwargs.get("env_matrix_path"),
        used_by_apps=tuple(kwargs.get("used_by_apps") or ()),
        required_symbols=(
            tuple(kwargs.get("required_symbols_opt") or ())
            or (
                ("__file__",) if kwargs.get("required_symbols_file") is not None else ()
            )
        ),
        use_cases_manifest=kwargs.get("use_cases_manifest"),
        diagnostic_comparison=bool(kwargs.get("diagnostic_comparison", False)),
        audit_suppressions=bool(kwargs.get("audit_suppressions", False)),
        include_labels=kwargs.get("include_labels"),
        require_complete_analysis=bool(kwargs.get("require_complete_analysis", False)),
    )
    if any(
        kwargs.get(name) is not None
        for name in (
            "old_sources",
            "new_sources",
            "old_build_info",
            "new_build_info",
            "old_dump_manifest",
            "new_dump_manifest",
        )
    ):
        # Codex review, same root cause as the --depth build/source
        # rejection below: this driver's NEW-side resolution never reads
        # any of these, on either side, so inline build/source evidence (or
        # a dump manifest) would be accepted and silently dropped -- no
        # L3-L5 collected, no manifest-declared includes applied.
        raise click.UsageError(
            "--sources/--build-info/--dump-manifest are not supported "
            "together with a stored-bundle-facts OLD_INPUT: this driver has no channel "
            "for inline build/source evidence on either side."
        )
    if (
        kwargs.get("probe_matrix_old") is not None
        or kwargs.get("probe_matrix_new") is not None
    ):
        # Codex review: the ordinary single-pair/release paths fold
        # --probe-matrix's build-configuration-drift findings (e.g.
        # CXX_STANDARD_FLOOR_RAISED) into the comparison and verdict, but
        # this dispatch never loads or forwards probe_matrix_old/
        # probe_matrix_new -- silently never folded.
        raise click.UsageError(
            "--probe-matrix is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("post_manifest_path") is not None:
        # Codex review: --post-manifest's public_surface_allowlist is
        # applied by passing post_manifest_path through to each
        # service.compare_snapshots() call -- compare_release_against_
        # bundle_facts() has no such parameter, so a private POST symbol
        # the manifest would scope out of the surface stays in the finding
        # set/verdict regardless.
        raise click.UsageError(
            "--post-manifest is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("scope_public_headers") is False:
        # Codex review, same root cause: --no-scope-public-headers has no
        # channel into compare_release_against_bundle_facts() either (the
        # driver always scopes to the public surface via service.
        # compare_snapshots's own default) -- rejected rather than silently
        # ignored.
        raise click.UsageError(
            "--no-scope-public-headers is not supported together with "
            "a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("debug_info2") is not None or kwargs.get("debug_info1") is not None:
        # Codex review, same root cause as the package-extraction fix
        # elsewhere: compare_release_against_bundle_facts() resolves
        # NEW-side ELF/DWARF facts directly from the binary itself (this
        # driver's own docstring: "no debug-info package resolution, no
        # PDB") and has no debug-dir parameter to receive a --debug-info
        # package's extracted contents, so it was silently accepted and
        # ignored. Rejected rather than silently dropped. debug_info1
        # (Codex review, fresh evidence) is the OLD-side scope -- OLD_FACTS
        # is already a resolved, stored snapshot with nothing left to
        # re-extract debug info from either, the same reason old=-scoped
        # --header/--include are rejected.
        raise click.UsageError(
            "--debug-info is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("devel_pkg1") is not None:
        # Codex review, fresh evidence: --devel-pkg new=... is honored (its
        # header_dir/include roots feed the NEW side's header search), but
        # devel_pkg1 (an old=-scoped --devel-pkg) has no OLD-side extraction
        # to feed at all -- OLD_FACTS is already a resolved, stored
        # snapshot -- so it was silently discarded rather than applied.
        raise click.UsageError(
            "--devel-pkg old=... is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if (
        kwargs.get("pdb_path") is not None
        or kwargs.get("old_pdb_path") is not None
        or kwargs.get("new_pdb_path") is not None
    ):
        # Codex review: same root cause as --debug-info just above --
        # compare_release_against_bundle_facts()'s per-library
        # service.resolve_input() call has no pdb_path parameter to receive
        # any of these (this driver's own docstring: "no debug-info package
        # resolution, no PDB"), so a NEW-side PE DLL would always fall back
        # to binary-only extraction regardless of what was given here.
        raise click.UsageError(
            "--pdb-path is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if (
        kwargs.get("follow_deps")
        or kwargs.get("search_paths")
        or kwargs.get("ld_library_path")
    ):
        # Codex review: --follow-deps's DT_NEEDED dependency-graph walk (and
        # its --search-path/--ld-library-path resolution knobs) is computed
        # inside run_compare's own dependency-traversal path --
        # compare_release_against_bundle_facts() has no parameter for any of
        # them, so the requested dependency graph/binding-status/dependency-
        # change section would silently never be produced.
        raise click.UsageError(
            "--follow-deps/--search-path/--ld-library-path are not "
            "supported together with a stored-bundle-facts OLD_INPUT."
        )
    if (
        kwargs.get("debug_format_opt") is not None
        or kwargs.get("debug_format") is not None
        or kwargs.get("dwarf_only") is True
        or kwargs.get("debuginfod") is True
        or kwargs.get("debuginfod_url") is not None
        or kwargs.get("debug_roots")
        or kwargs.get("debug_roots_old")
        or kwargs.get("debug_roots_new")
    ):
        # Codex review: these control which NEW-side ELF/DWARF facts get
        # extracted (--debug-format/--dwarf-only select the debug-info
        # source; --debuginfod/--debuginfod-url and --debug-root locate
        # separate debug files), but compare_release_against_bundle_facts()
        # calls service.resolve_input() with none of them -- always its own
        # defaults, regardless of what was requested here. Rejected rather
        # than silently comparing a different ABI surface than asked for.
        raise click.UsageError(
            "--debug-format/--dwarf-only/--debuginfod/--debuginfod-url/"
            "--debug-root are not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if (
        kwargs.get("pattern_verdicts")
        or kwargs.get("explain_patterns")
        or kwargs.get("surface_metrics")
    ):
        # Codex review: pattern-verdict modulation and surface-metric
        # findings are both computed inside service.compare_snapshots()
        # (ADR-027), but compare_release_against_bundle_facts()'s
        # per-library call never passes pattern_verdicts/surface_metrics --
        # always False, so a requested modulation or metric-drift finding
        # silently never happens even though the CLI accepted the flag.
        raise click.UsageError(
            "--pattern-verdicts/--explain-patterns/--surface-metrics are "
            "not supported together with a stored-bundle-facts OLD_INPUT."
        )
    depth = kwargs.get("depth")
    if depth in ("build", "source"):
        # Codex review: run_compare's own --depth build/source dial collects
        # L3-L5 build/source evidence from --sources/--build-info on either
        # side -- compare_release_against_bundle_facts() has no parameter to
        # receive either, on either side (the OLD side is already a resolved
        # snapshot with no raw sources to replay, and this driver's NEW-side
        # resolution never reads old_sources/new_sources/old_build_info/
        # new_build_info at all), so the requested evidence was silently
        # never collected. Rejected rather than silently downgraded to
        # header-only depth.
        raise click.UsageError(
            f"--depth {depth} is not supported together with "
            "a stored-bundle-facts OLD_INPUT: this driver has no channel for L3-L5 "
            "build/source evidence."
        )
    if kwargs.get("show_only"):
        # Codex review: every nested per-library report here is rendered via
        # reporter.to_json(diff) with no show_only argument, so the filter
        # was accepted but every change stayed in the output regardless.
        # Not implemented here either -- the live release fan-out
        # (cli_compare_release.py) has this identical gap on its own
        # per-library to_json() calls, so threading it through only in this
        # newly-exposed mode would mean this driver's JSON output disagrees
        # with what --show-only already does (nothing) on every other
        # release-shaped comparison path. Rejected rather than partially
        # honored ahead of that pre-existing gap.
        raise click.UsageError(
            "--show-only is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("report_mode") not in (None, "full") or kwargs.get("show_filtered"):
        # Codex review: same root cause as --show-only above -- report_mode
        # has no channel into to_json(diff) here (always "full"), and
        # show_filtered needs the _finalize_compare_result merge step this
        # dispatcher never calls. Same identical pre-existing gap on the
        # live release fan-out's own per-library to_json() calls.
        raise click.UsageError(
            "--report-mode/--show-filtered are not supported together "
            "with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("jobs"):
        # Codex review: compare_release_against_bundle_facts() processes
        # every matched library in a synchronous loop -- an explicit
        # -j/--jobs N request was silently dropped. The silent default (0,
        # "auto-detect") is left alone: unlike every other flag here,
        # --jobs never changes the finding set/verdict/exit code, only
        # wall-clock time, and dispatch() has no way to tell a default 0
        # apart from the flag never having been given at all.
        raise click.UsageError(
            "--jobs is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    if kwargs.get("no_bundle_analysis"):
        # Codex review: compare_release_against_bundle_facts() has no
        # parameter to skip the cross-library BUNDLE_* analysis
        # (compare_bundle_from_facts always runs), so --no-bundle-analysis
        # was silently accepted and ignored -- the run could report a
        # different verdict/exit code than requested (bundle_verdict folds
        # into result.verdict). Rejected rather than silently unscoped.
        raise click.UsageError(
            "--no-bundle-analysis is not supported together with a stored-bundle-facts OLD_INPUT."
        )
    # Codex review: kwargs["config"] is compare.py's own resolved value --
    # an explicit --config, or (since a later review round) the same
    # cwd-upward auto-discovered .abicheck.yml run_compare's own cfg_path
    # falls back to (discover_project_config()) when no --config was given
    # at all. Either way it is consumed only as resolve_compile_context's
    # build_config (compile: block merging) -- the same non-compile
    # settings --severity-preset/--pack/--no-scope-public-headers are
    # rejected for as explicit CLI flags (severity:/exit_code_scheme:/
    # scope:/suppression:/source:) can also be declared in the config file
    # itself, with no CLI flag needed, and were silently unapplied through
    # that channel too. Reject rather than silently diverge, the same bar
    # every other flag/config combination in this dispatcher is held to.
    config_path = kwargs.get("config")
    if config_path is not None:
        from ....workflows.extraction import load_build_config

        try:
            _bc = load_build_config(Path(config_path))
        except ValueError as exc:
            raise click.UsageError(
                f"cannot parse build config {config_path}: {exc}"
            ) from exc
        _unsupported_config_blocks = []
        if _bc.severity_preset is not None or any(
            getattr(_bc, field) is not None
            for field in (
                "severity_abi_breaking",
                "severity_potential_breaking",
                "severity_quality_issues",
                "severity_addition",
            )
        ):
            _unsupported_config_blocks.append("severity:")
        if (
            _bc.scope_public is not None
            or _bc.collapse_versioned_symbols is not None
            or _bc.public_symbols
            or _bc.scope_show_redundant is not None
        ):
            # Codex review, fresh evidence: BuildConfig's scope: block
            # parses public/collapse_versioned_symbols/public_symbols/
            # show_redundant as four independent fields -- a config setting
            # only show_redundant (every other field left at its default)
            # previously passed this check unrejected even though this
            # driver's own JSON rendering never re-merges redundant_changes
            # the way ordinary `compare` does.
            _unsupported_config_blocks.append("scope:")
        if (
            _bc.suppression_strict is not None
            or _bc.suppression_require_justification is not None
        ):
            _unsupported_config_blocks.append("suppression:")
        # exit_code_scheme: no longer exists as a config key at all (CLI
        # cleanup phase two PR G2) -- an unknown top-level key is now a hard
        # ValueError at BuildConfig parse time, so there is nothing left for
        # this dispatcher to detect and reject here.
        if any(
            getattr(_bc, field) is not None
            for field in (
                "debug_format",
                "debug_dwarf_only",
                "debug_debuginfod",
                "debug_debuginfod_url",
            )
        ):
            # Same root cause as the --debug-format/--dwarf-only/
            # --debuginfod CLI-flag rejection above: CompileContext (what
            # this driver actually threads through to service.resolve_input)
            # has no debug-format/dwarf-only/debuginfod fields at all, CLI
            # flag or config alike.
            _unsupported_config_blocks.append("debug:")
        if _bc.source_method is not None:
            # Codex review: source.method (s1-s6) drives run_compare's own
            # _resolve_compare_collect_mode, which this dispatcher never
            # calls -- same root cause as --depth build/source above, this
            # driver has no channel for L3-L5 build/source evidence
            # collection on either side, config-declared or not.
            _unsupported_config_blocks.append("source:")
        if _unsupported_config_blocks:
            raise click.UsageError(
                f"{config_path} declares "
                f"{', '.join(_unsupported_config_blocks)} settings, which "
                "are not supported together with a stored-bundle-facts OLD_INPUT: "
                "compare_release_against_bundle_facts() has no channel to "
                "honor them (same reason --severity-preset/--pack/"
                "--no-scope-public-headers are rejected as explicit "
                "flags). Use a --config that only sets compile: options."
            )

    if kwargs.get("old_headers_only") or kwargs.get("old_includes_only"):
        # Codex review: normalize_sided_options puts an old=-scoped
        # --header/--include into old_headers_only/old_includes_only, but
        # compare_bundle_facts._resolve_new_side_headers_includes only ever
        # reads the new=-scoped/uniform fields (that function's own
        # docstring: "the OLD side has no headers/includes of its own
        # here"). The OLD side is already a resolved, stored snapshot -- it
        # cannot be reparsed with a different header scope at this point --
        # so a requested OLD-side header/include operand was silently
        # discarded rather than applied or rejected.
        raise click.UsageError(
            "--header old=.../--include old=... are not supported together "
            "with a stored-bundle-facts OLD_INPUT: OLD_FACTS is already a resolved, "
            "stored snapshot with no header re-extraction available."
        )
    if kwargs.get("old_header_backend") is not None:
        # Codex review, fresh evidence: normalize_sided_options puts an
        # old=-scoped --ast-frontend into old_header_backend, but
        # compare_bundle_facts.dispatch() only ever reads the new=-scoped/
        # uniform header_backend value (same root cause as the old=-scoped
        # --header/--include rejection just above) -- OLD_FACTS is already
        # resolved and cannot be re-extracted under a different frontend.
        raise click.UsageError(
            "--ast-frontend old=... is not supported together with "
            "a stored-bundle-facts OLD_INPUT: OLD_FACTS is already a resolved, stored "
            "snapshot with no header re-extraction available."
        )
    if kwargs.get("old_version") not in (None, "", "old"):
        # Codex review, fresh evidence: neither compare_release_against_
        # bundle_facts() nor compare_stored_bundle_facts_pair() has an
        # old_version parameter at all -- OLD_INPUT is always a stored,
        # already-resolved document in this dispatcher (regardless of
        # whether NEW_INPUT is too), and its per-library snapshots already
        # carry whatever version they were captured with, so a requested
        # --version old=... was silently discarded rather than applied or
        # rejected, on either side of this shape.
        raise click.UsageError(
            "--version old=... is not supported together with "
            "a stored-bundle-facts OLD_INPUT: OLD_FACTS's own per-library "
            "snapshots already carry whatever version they were captured with."
        )
    if kwargs.get("demangle") is not None:
        # Codex review, fresh evidence: --demangle/--no-demangle is
        # documented to apply to markdown output, but this dispatcher's
        # markdown rendering calls bundle.render_bundle_findings_markdown()
        # directly, which has no demangle parameter at all -- the live
        # release fan-out's own bundle-findings markdown section
        # (cli_compare_release_helpers._release_md_bundle_findings) has
        # this identical pre-existing gap, so implementing it only here
        # would disagree with what that shared renderer already does.
        # Rejected only when the flag is given explicitly, matching the
        # --jobs precedent: the silent default (demangle ON) is left alone
        # since json/the common per-library table never show a mangled
        # symbol at all -- only a rare bundle_* finding on a C++ symbol
        # would show one.
        raise click.UsageError(
            "--demangle/--no-demangle is not supported together with "
            "a stored-bundle-facts OLD_INPUT."
        )
    if new_is_stored:
        _reject_new_side_extraction_options_for_stored_pair(kwargs)


def _reject_new_side_extraction_options_for_stored_pair(kwargs: dict[str, Any]) -> None:
    """PR I stored/stored: the NEW-side-scoped mirror of every OLD-side
    extraction-only rejection above, applied once NEW_INPUT classifies as a
    stored BundleFacts document too -- ``compare_stored_bundle_facts_pair()``
    reads no binaries and parses no header AST on *either* side, so every
    flag that would only ever apply to a *live* NEW_INPUT directory/package
    has no channel to honor here either, the identical reasoning the
    OLD-side checks above already establish for OLD_INPUT."""
    if (
        kwargs.get("new_headers_only")
        or kwargs.get("new_includes_only")
        or kwargs.get("headers")
        or kwargs.get("includes")
    ):
        raise click.UsageError(
            "--header new=.../--include new=... (or the uniform --header/"
            "--include) are not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: neither side has "
            "any header re-extraction available."
        )
    if kwargs.get("new_header_backend") is not None or kwargs.get("header_backend") not in (
        None,
        "auto",
    ):
        # `header_backend` (the uniform/base value) defaults to the literal
        # string "auto", never None (cli_options._split_sided_frontend) --
        # unlike `old_header_backend`/`new_header_backend`, which really do
        # default to None. Checking `is not None` here would reject every
        # ordinary invocation, since the untouched default is always
        # present; only a value that actually differs from that silent
        # default means the flag was really given.
        raise click.UsageError(
            "--ast-frontend is not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: neither side has "
            "any header re-extraction available."
        )
    if kwargs.get("devel_pkg2") is not None:
        raise click.UsageError(
            "--devel-pkg new=... is not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: there is no live "
            "NEW-side package to extract a devel companion package's "
            "headers into."
        )
    if kwargs.get("bundle_facts_library_manifest") is not None:
        raise click.UsageError(
            "--bundle-facts-library-manifest is not supported when both "
            "OLD_INPUT and NEW_INPUT are stored BundleFacts documents: "
            "there is no live NEW-side library set to apply per-library "
            "header/include/compile overrides to."
        )
    if kwargs.get("include_private_dso"):
        raise click.UsageError(
            "--include-private-dso is not supported when both OLD_INPUT "
            "and NEW_INPUT are stored BundleFacts documents: neither side "
            "discovers shared libraries from a live directory/package."
        )
    if kwargs.get("dso_only"):
        # Codex review, PR #1060, round 7: the live release fan-out
        # (cli_compare_release.py's _prepare_compare_release_inputs)
        # explicitly filters both old/new library maps to skip executables
        # for this flag -- a persisted BundleFacts document carries no
        # per-library "was this an executable, not a real .so" fact at all
        # (capture_bundle_facts() only ever stores what
        # bundle_snapshot_from_facts() can reconstruct from an AbiSnapshot),
        # so there is no channel to apply the same selection here. Reject
        # rather than silently compare every intersecting entry including
        # ones a live --dso-only run would have skipped.
        raise click.UsageError(
            "--dso-only is not supported when both OLD_INPUT and NEW_INPUT "
            "are stored BundleFacts documents: a persisted document carries "
            "no per-library executable/library distinction to filter by."
        )
    if kwargs.get("keep_extracted"):
        raise click.UsageError(
            "--keep-extracted is not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: neither side is "
            "ever extracted to a temporary directory."
        )
    if kwargs.get("new_version") not in (None, "", "new"):
        raise click.UsageError(
            "--version new=... is not supported when both OLD_INPUT and "
            "NEW_INPUT are stored BundleFacts documents: NEW_INPUT's own "
            "per-library snapshots already carry whatever version they "
            "were captured with."
        )
    if kwargs.get("include_dependencies"):
        raise click.UsageError(
            "--include-system-declarations is not supported when both "
            "OLD_INPUT and NEW_INPUT are stored BundleFacts documents: "
            "there is no live NEW-side resolution for it to scope."
        )
    # --depth binary/headers are genuinely supported for stored/stored
    # (Codex review, PR #1060, fresh evidence): compare_stored_bundle_
    # facts_pair() enforces the requested depth as a floor
    # (enforce_requested_depth) and then projects both sides via policy.
    # depth_projection.project_snapshot_to_depth() as a ceiling before
    # diffing, the same primitives every other resolved-snapshot
    # comparison path in this codebase already pairs -- an earlier version
    # of this check rejected --depth binary outright on the mistaken
    # premise that no such projection primitive existed. --depth build/
    # source are still rejected, unconditionally, above: this driver has
    # no channel to *collect* L3-L5 evidence on either side, only to
    # enforce and project already-resolved evidence.
    # Codex review, fresh evidence: compare_cmd builds a real CompileContext
    # from these before calling dispatch() (resolve_compile_context), but
    # this stored/stored branch never consumes compile_context at all --
    # neither side does any header-frontend extraction, so every one of
    # these was silently accepted and discarded rather than applied or
    # rejected.
    if (
        kwargs.get("compiler_path") is not None
        or kwargs.get("compiler_prefix") is not None
        or kwargs.get("compiler_option_tokens")
        or kwargs.get("sysroot") is not None
        or kwargs.get("nostdinc")
        or kwargs.get("frontend_context") not in (None, "host")
    ):
        raise click.UsageError(
            "--compiler/--compiler-prefix/--compiler-option/--sysroot/"
            "--nostdinc/--frontend-context are not supported when both "
            "OLD_INPUT and NEW_INPUT are stored BundleFacts documents: "
            "neither side runs any header-frontend extraction for a compile "
            "context to configure."
        )
    if kwargs.get("lang_explicit"):
        raise click.UsageError(
            "--lang is not supported when both OLD_INPUT and NEW_INPUT are "
            "stored BundleFacts documents: neither side's language is "
            "re-detected or re-parsed from headers, so there is nothing "
            "left for it to select."
        )


def reject_explicit_compile_config_for_stored_pair(config_path: Path) -> None:
    """Raise ``click.UsageError`` when *config_path* -- an *explicitly*
    given ``--config`` (Click parameter-source COMMANDLINE, checked by the
    caller, ``compare_bundle_facts.resolve_dispatch_compile_context``)
    -- declares real ``compile:`` settings that a stored/stored comparison
    has no channel to honor (Codex review, PR #1060). An *ambient*,
    auto-discovered ``.abicheck.yml`` is deliberately not checked this way
    at all -- see that function's own docstring for why the two cases
    differ: an explicit ``--config`` is a request the user expects
    honored, while an ambient one silently applying nothing is exactly the
    point of not letting an unrelated project default break a stored/
    stored comparison. Lives here rather than in ``compare_bundle_facts.py``
    purely to keep that file under its own architecture line cap -- this
    module already owns every other stored/stored-specific rejection."""
    from ....workflows.extraction import load_build_config

    try:
        bc = load_build_config(Path(config_path))
    except ValueError as exc:
        raise click.UsageError(f"cannot parse build config {config_path}: {exc}") from exc
    if (
        bc.compile_frontend is not None
        or bc.compile_std is not None
        or bc.compile_include_dirs
        or bc.compile_defines
        or bc.compile_sysroot is not None
        or bc.compile_nostdinc is not None
    ):
        raise click.UsageError(
            f"{config_path} declares compile: settings, which are not "
            "supported when both OLD_INPUT and NEW_INPUT are stored "
            "BundleFacts documents: neither side runs any header-frontend "
            "extraction for them to configure. Omit --config to let an "
            "ambient project config, if any, stay harmlessly unused here "
            "instead, or use a --config that declares no compile: block."
        )


#: (Click parameter dest, CLI flag) pairs for the expose_value=False
#: AST-override flags reject_ast_override_flags_for_stored_pair() checks --
#: shared so the two never drift, since neither name is derivable from the
#: other mechanically (Click's default dest derivation is one-way).
_AST_OVERRIDE_FLAGS: tuple[tuple[str, str], ...] = (
    ("allow_ast_frontend_fallback", "--allow-ast-frontend-fallback"),
    ("allow_unsupported_castxml", "--allow-unsupported-castxml"),
)


def reject_ast_override_flags_for_stored_pair(ctx: click.Context) -> None:
    """Raise ``click.UsageError`` for an explicitly-given
    ``--allow-ast-frontend-fallback``/``--allow-unsupported-castxml`` on a
    stored/stored comparison (Codex review, PR #1060, round 11).

    Both flags are ``expose_value=False`` (``cli_options.
    _scoped_env_flag_callback`` sets a scoped env var as a side effect and
    never adds a ``kwargs`` entry at all), so ``reject_unsupported_
    options()`` -- which reads only ``kwargs`` -- can never see either flag
    to reject it: neither side of a stored/stored comparison runs any
    header-frontend AST extraction for either flag to affect, so both were
    silently accepted and had no effect. ``ctx.get_parameter_source()``
    still answers ``COMMANDLINE`` for an ``expose_value=False`` option --
    Click records the source at parse time regardless of exposure -- so
    this checks the one place that survives instead of ``kwargs``."""
    for dest, flag in _AST_OVERRIDE_FLAGS:
        if ctx.get_parameter_source(dest) == click.core.ParameterSource.COMMANDLINE:
            raise click.UsageError(
                f"{flag} is not supported when both OLD_INPUT and NEW_INPUT "
                "are stored BundleFacts documents: neither side runs any "
                "header-frontend AST extraction for it to affect."
            )


def exit_bundle_facts_not_comparable(
    exc: Exception, *, fmt: str = "markdown", output: Path | None = None
) -> None:
    """Translate a ``ProfileMismatchError``/``ScopeMismatchError`` raised
    from inside a per-library ``compare_snapshots()`` call into a clean CLI
    failure, exit 16 -- the same code native ``compare``'s own ADR-050 D2
    comparability gate uses (``frontends.cli.runtime._EXIT_NOT_COMPARABLE``),
    rather than the generic exit-1 ``click.ClickException`` translation the
    sibling ``except (SnapshotError, TypeError, ValueError, OSError)``
    clause gives every other malformed-input case (Codex review, PR #1060,
    round 12). Neither exception type is a ``ValueError``/``TypeError``/
    ``SnapshotError`` (both are plain ``AbicheckError``), so it previously
    reached neither clause and surfaced as a raw traceback instead. Shared
    by both the stored/stored and stored/live dispatch branches, which both
    diff each matched library through the identical ``compare_snapshots()``
    chokepoint.

    *fmt*/*output* (Codex review, PR #1060, round 14), when ``fmt ==
    "json"``, get a machine-readable refusal document written the same way
    an ordinary successful comparison's JSON would be (``-o`` or stdout) --
    a stored-pair refusal must be consumable by the same scripts/report
    aggregator native compare's own ``_report_not_comparable`` JSON output
    already is; silently leaving the requested report file unwritten (the
    prior stderr-only behavior) was itself a gap this round found. The
    envelope mirrors this dispatcher's own ordinary ``mode: "bundle_facts"``
    shape rather than native compare's schema-versioned ``compare_report.
    schema.json`` document -- the two were never the same schema even for a
    successful comparison here, so inventing schema conformance only for
    the refusal case would be a new, undocumented contract, not a fix.
    Every other format (``markdown``/``html``/``review``, and the default)
    gets only the stderr message, matching native compare's own identical
    "no equivalent document convention" choice for those formats."""
    import sys

    from ....errors import ProfileMismatchError

    kind = "profile_mismatch" if isinstance(exc, ProfileMismatchError) else "scope_mismatch"
    click.echo(
        f"Error: not comparable: {exc}\n"
        "Two matched libraries were not extracted under a comparable "
        "profile/scope contract (ADR-050 D1/D2), so no verdict was "
        "produced for this bundle.",
        err=True,
    )
    if fmt == "json":
        import json as _json

        from ....frontends.cli.runtime import _write_or_echo

        document = {
            "mode": "bundle_facts",
            "verdict": None,
            "not_comparable": True,
            "reason": {"kind": kind, "message": str(exc)},
        }
        _write_or_echo(output, _json.dumps(document, indent=2))
    sys.exit(16)

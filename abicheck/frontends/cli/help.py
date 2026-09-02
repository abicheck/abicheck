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

"""Progressive-disclosure ``--help`` grouping (G21.8 / collapse M1 + M2).

The big commands carry dozens of options (``compare`` ~62, ``dump`` ~39); a flat
list is the dominant source of perceived CLI complexity. rich-click renders the
options in named panels so the everyday inputs lead and the long tail
(per-side overrides, debug-info resolution, L3-L5 evidence, …) is grouped rather
than dumped. This is purely presentational — no option is added, removed, or
renamed.

Keys use rich-click's ``fnmatch`` wildcard form (``"* compare"``) so the panels
render regardless of the program name — ``abicheck compare``, ``python -m
abicheck compare``, or the ``main`` prog click uses under test. Unlisted options
fall through to a default panel, and an unmatched command renders ungrouped — so
this can never break a command, only prettify it.

``compare``, ``dump``, and ``scan`` additionally get a second, orthogonal
disclosure axis (M2): plain ``--help`` on each shows only a curated common
subset (:data:`COMPARE_COMMON_OPTION_NAMES` / :data:`DUMP_COMMON_OPTION_NAMES`
/ :data:`SCAN_COMMON_OPTION_NAMES`), folding the long tail behind
``--help-all``. See :func:`curated_help_options` (the shared factory) and its
three per-command instances, ``compare_help_options``/``dump_help_options``/
``scan_help_options``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TypeVar

import click

F = TypeVar("F", bound=Callable[..., object])

# Root command panels — group the top-level verbs by role in `abicheck --help`
# rather than one flat list, per the ADR-042/ADR-043 framing: the four
# core-analysis verbs, the report-level `aggregate` fan-in (workflow
# composition over already-produced reports, not a binary analysis), and the
# ABICC-compat shim. rich-click keys these by the *exact* root command path,
# which differs per entry point (the console script `abicheck`, `python -m
# abicheck`, `python -m abicheck.cli`, and `main` under CliRunner) — a single
# root token, so the `* <cmd>` wildcard the option panels use does not apply.
# Registering the same panels under each keeps grouping consistent across every
# documented invocation. A verb not listed still shows (rich-click falls it back
# into a default panel), so a new command never silently vanishes.
_ROOT_COMMAND_PANELS: list[dict[str, object]] = [
    {"name": "Core analysis", "commands": ["dump", "compare", "scan", "deps"]},
    {
        "name": "Workflow composition",
        "commands": ["aggregate"],
    },
    {
        "name": "Project integration (advanced)",
        "commands": ["project"],
    },
    {"name": "Legacy compatibility", "commands": ["compat"]},
]
COMMAND_GROUPS: dict[str, list[dict[str, object]]] = {
    root: _ROOT_COMMAND_PANELS
    for root in ("abicheck", "main", "python -m abicheck", "python -m abicheck.cli")
}

# Per-command option panels. Options not listed here land in rich-click's
# default trailing panel, so a new flag never has to be added here to work.
OPTION_GROUPS: dict[str, list[dict[str, object]]] = {
    "* compare": [
        {"name": "Inputs", "options": ["--header", "--include", "--lang"]},
        {
            "name": "Output & reporting",
            "options": [
                "--output",
                "--format",
                "--demangle",
                "--report-mode",
                "--show-only",
                "--config",
                "--exit-code-scheme",
                "--verbose",
            ],
        },
        {
            "name": "Toolchain (header parsing)",
            "options": [
                "--ast-frontend",
                "--compiler",
                "--compiler-prefix",
                "--compiler-option",
                "--sysroot",
                "--nostdinc",
            ],
        },
        {
            "name": "Policy & severity",
            "options": [
                "--policy",
                "--suppress",
                "--severity-preset",
            ],
        },
        {
            "name": "Public-surface scoping",
            "options": [
                "--scope-public-headers",
                "--show-filtered",
            ],
        },
        {
            "name": "Debug info",
            # The format/debuginfod/dwarf-only knobs are demoted to the `debug:`
            # config block (ADR-040 L2) and hidden; only the coarse per-run
            # --debug-root override stays a visible flag.
            "options": ["--debug-root"],
        },
        {
            "name": "Build & source evidence (--depth build/source)",
            "options": [
                "--build-info",
                "--sources",
                "--depth",
            ],
        },
        {
            "name": "Dependencies",
            "options": ["--follow-deps", "--search-path", "--ld-library-path"],
        },
        {
            "name": "Per-side overrides",
            "options": [
                "--version",
                "--pdb-path",
            ],
        },
        {
            "name": "Build-config matrix & idioms",
            "options": [
                "--probe-matrix",
                "--pattern-verdicts",
                "--explain-patterns",
                "--surface-metrics",
            ],
        },
        {
            "name": "Release (directory/package inputs)",
            "options": [
                "--jobs",
                "--dso-only",
                "--output-dir",
                "--fail-on-removed-library",
                "--debug-info",
                "--devel-pkg",
                "--include-private-dso",
                "--keep-extracted",
                "--manifest",
                "--bundle-system-providers",
                "--bundle-cohort",
                "--no-bundle-analysis",
            ],
        },
    ],
    "* dump": [
        {
            "name": "Inputs",
            "options": [
                "--header",
                "--include",
                "--version",
                "--lang",
            ],
        },
        {"name": "Output", "options": ["--output", "--dry-run", "--verbose"]},
        {
            "name": "Toolchain",
            "options": [
                "--ast-frontend",
                "--compiler",
                "--compiler-prefix",
                "--compiler-option",
                "--sysroot",
                "--nostdinc",
            ],
        },
        {
            "name": "Debug info",
            "options": [
                "--dwarf-only",
                "--debug-format",
                "--debug-root",
                "--debuginfod",
                "--debuginfod-url",
                "--pdb-path",
            ],
        },
        {
            "name": "Build & source evidence (--depth build/source)",
            "options": [
                "--depth",
                "--build-info",
                "--sources",
                "--compile-db-filter",
                "--config",
                "--allow-build-query",
            ],
        },
        {
            "name": "Dependencies",
            "options": ["--follow-deps", "--search-path", "--ld-library-path"],
        },
        {
            "name": "Provenance",
            "options": ["--git-tag", "--build-id", "--no-git"],
        },
    ],
    "* scan": [
        {
            "name": "Inputs",
            "options": [
                "--binary",
                "--header",
                "--include",
                "--sources",
                "--build-info",
                "--config",
            ],
        },
        {
            "name": "Baseline & scope",
            "options": [
                "--against",
                "--depth",
                "--since",
                "--changed-path",
                "--budget",
            ],
        },
        {
            "name": "Modes",
            "options": ["--crosscheck", "--risk-rules"],
        },
        {
            "name": "Toolchain (header parsing)",
            "options": [
                "--lang",
                "--ast-frontend",
                "--compiler",
                "--compiler-prefix",
                "--compiler-option",
                "--sysroot",
                "--nostdinc",
            ],
        },
        {
            # Mirrors `compare`'s own panel (CLI audit PR 4/5): these flags are
            # demoted-to-config (hidden=True) the same way compare's already
            # were, so listing them here is what keeps them visible in
            # `scan --help-all` at all (rich-click's OPTION_GROUPS panels
            # render a listed option regardless of `hidden` -- see
            # cli_help.py's module docstring / _make_help_callback).
            "name": "Policy & severity",
            "options": [
                "--policy",
                "--suppress",
                "--severity-preset",
            ],
        },
        {
            "name": "Public-surface scoping",
            "options": [
                "--scope-public-headers",
            ],
        },
        {
            "name": "Output",
            "options": [
                "--format",
                "--output",
                "--dry-run",
                "--verbose",
                "--exit-code-scheme",
            ],
        },
    ],
    # NB: the ABICC drop-in `compat check` (53 single-dash flags) renders with
    # plain Click help — its group is not under the rich-click `main`, so panel
    # config would be inert there. Its flags already carry help; the dialect's
    # flat help is left as-is (ADR-037 non-goal to restyle the ABICC surface).
}


def _disable_cross_panel_dedup(groups: dict[str, list[dict[str, object]]]) -> None:
    """Stamp ``deduplicate: False`` onto every panel dict in *groups*.

    rich-click's ``CommandGroupDict``/``OptionGroupDict`` both default
    ``deduplicate`` to ``True``: when the *same* option/command name appears
    in more than one panel, its resolver (``rich_panel._resolve_panels_from_config``)
    calls ``list.remove()`` **on the panel dict's own ``commands``/``options``
    list object** to drop the later duplicate -- not a copy. None of our
    panels intentionally rely on that (each name here is listed in exactly
    one panel), so this is a no-op for rendered output either way -- but
    leaving the default on is a live landmine: every panel list here is a
    module-level literal, built once and reused for every subsequent
    ``--help`` render in the process (`configure_rich_help()` itself only
    runs once, at :mod:`abicheck.cli` import time). Given enough distinct
    root-help renders in one process (any full test session invoking
    ``CliRunner().invoke(main, ["--help"])`` repeatedly, e.g. under
    pytest-xdist), rich-click's resolver eventually perceives a
    (spurious, cross-call) duplicate and permanently deletes entries from
    these panels for the rest of the process -- observed as CI-only,
    order-dependent panel corruption (`Workflow composition`/`Project
    integration` silently vanishing) that never reproduced in an isolated
    run (CI incident, ADR-054 PR review). Disabling dedup entirely removes
    the mutation path, independent of root-causing rich-click's own caching.
    """
    for panels in groups.values():
        for panel in panels:
            panel["deduplicate"] = False


_disable_cross_panel_dedup(COMMAND_GROUPS)
_disable_cross_panel_dedup(OPTION_GROUPS)


def _ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows, where they otherwise are not.

    A `python -m abicheck.cli --help`/error-path write raises ``UnicodeEncodeError``
    and crashes the process when help/error text carries a non-ASCII character
    (an em dash, an arrow, …, both used throughout this CLI's help strings) and
    the stream isn't a real UTF-8-capable console — e.g. redirected/piped output
    on Windows, which defaults to the legacy ANSI code page rather than UTF-8.
    POSIX terminals already default to UTF-8, so this is a no-op there.
    ``reconfigure`` is a no-op if the stream is already UTF-8, and ``errors="replace"``
    is a last-resort safety net rather than a crash if some other exotic case slips
    through.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def configure_rich_help() -> None:
    """Register the option-group panels with rich-click (idempotent).

    Best-effort: if rich-click is unavailable the CLI still works with click's
    plain help, so the import failure is swallowed rather than aborting startup.
    """
    _ensure_utf8_streams()
    try:
        import rich_click
    except ImportError:  # pragma: no cover - rich-click is a declared dependency
        return
    # rich-click types the values as its OptionGroupDict TypedDict; our plain
    # dict literal is structurally compatible but mypy can't prove it.
    rich_click.rich_click.OPTION_GROUPS.update(OPTION_GROUPS)  # type: ignore[arg-type]
    rich_click.rich_click.COMMAND_GROUPS.update(COMMAND_GROUPS)  # type: ignore[arg-type]
    # Render help monochrome (no ANSI). CI runners set FORCE_COLOR/CI, which
    # would make rich emit colour escapes even into a pipe — env-dependent output
    # that breaks help-substring tests on some platforms but not others. The
    # grouping panels (the actual M1 win) are unaffected; only colour is dropped,
    # so help text is deterministic everywhere.
    rich_click.rich_click.COLOR_SYSTEM = None


# ── `compare --help-all` second-level disclosure (G21.8 / collapse M2) ───────
#
# The panels above (M1) already regroup all ~62 ``compare`` options so the flat
# list isn't the whole story, but a first-time user still sees all ~62 in one
# ``--help`` screen. This is a second axis, orthogonal to panels: a curated
# subset of the everyday options stays on plain ``compare --help``; the long
# tail (per-side toolchain overrides, build-config matrix idioms, release
# knobs, …) is folded behind ``compare --help-all``. Purely presentational —
# every option keeps working unqualified; only its default *visibility* in the
# help screen changes.
#
# Dest names (``click.Option.name``), not flag strings: a few options share
# aliases (``-o``/``--output``) or are on/off pairs (``--demangle``/
# ``--no-demangle``) where only one dest exists either way.
COMPARE_COMMON_OPTION_NAMES: frozenset[str] = frozenset(
    {
        # Inputs
        "header",
        "include",
        "lang",
        # Output & reporting
        "output",
        "fmt",
        "show_only",
        "demangle",
        # Policy & severity
        "config",
        "policy",
        "suppress",
        "severity_preset",
        # Scoped comparison (ADR-043) — headline feature, not a long-tail knob
        "used_by_apps",
        "required_symbols_opt",
        "required_symbols_file",
        # Build & source evidence
        "depth",
        "sources",
        "build_info",
        # Public-surface scoping
        "scope_public_headers",
        # Contract domain (ADR-049 Phase 6/7) -- headline feature per the CLI
        # audit's proposed clean `compare` surface, and the one flag that asks
        # for a contract decision at all (cli_options.resolve_contract_evaluation).
        "contract_mode",
        # Debug info -- only the coarse per-run override stays visible; the
        # format/debuginfod/dwarf-only knobs are demoted to the `debug:`
        # config block (ADR-040 L2) and already hidden regardless of tier.
        "debug_root",
        # Per-side overrides -- version labelling is routine for bare .so
        # inputs; --pdb-path stays in the advanced tier.
        "version",
        # Universal
        "verbose",
        "dry_run",
        # The help options themselves always stay visible
        "help",
        "help_all",
    }
)


def _make_help_callback(
    command_label: str, common_names: frozenset[str]
) -> Callable[[click.Context, click.Parameter, bool], None]:
    """Build the curated ``--help`` callback for one command.

    Factored out of the original ``compare``-only implementation so ``dump``
    and ``scan`` (G21.8 follow-on) get the identical curated/full split
    without a copy-pasted callback per command — the closure just captures
    which command's name to print in the pointer message and which dest-name
    set counts as "common" for it.
    """

    def _help_callback(
        ctx: click.Context, _param: click.Parameter, value: bool
    ) -> None:
        if not value or ctx.resilient_parsing:
            return
        cmd = ctx.command
        original_params = cmd.params
        # Filter the *params list* itself rather than flipping each Option's
        # ``hidden`` flag: rich-click's OPTION_GROUPS panels (M1) resolve their
        # members by name against ``command.get_params(ctx)`` at render time and
        # include them regardless of ``hidden`` — that flag only affects options
        # that fall through to the default catch-all panel, which is nearly none
        # of this command's options once M1 grouped them all. Removing the
        # advanced options from ``params`` for the duration of this render means
        # the named panels simply can't find them, so they resolve to zero rows
        # and rich-click drops the (now-empty) panel entirely. Arguments always
        # stay.
        common = [
            p
            for p in original_params
            if isinstance(p, click.Argument) or p.name in common_names
        ]
        # The footer promises "--help-all shows those N options" -- true only
        # for a folded option that --help-all can actually render. A Click-``hidden``
        # option renders there too *only* if some OPTION_GROUPS panel lists one
        # of its flag strings (same bypass noted above); one that is hidden and
        # unlisted (a deprecated no-op shim like --header-graph) never appears
        # even there (Codex review, PR #757).
        # Counting those in "advanced option(s) hidden" would overstate what
        # --help-all actually recovers, so they're excluded from the count here
        # -- they were never part of this M2 disclosure axis to begin with.
        panel_flags: set[str] = set()
        for panel in OPTION_GROUPS.get(f"* {command_label}", []):
            panel_flags.update(panel.get("options", ()))  # type: ignore[arg-type]
        recoverable_via_help_all = [
            p
            for p in original_params
            if p not in common
            and (not getattr(p, "hidden", False) or set(p.opts) & panel_flags)
        ]
        hidden_count = len(recoverable_via_help_all)
        cmd.params = common
        try:
            help_text = ctx.get_help()
        finally:
            cmd.params = original_params
        click.echo(help_text, color=ctx.color)
        click.echo(
            f"\n{hidden_count} advanced option(s) hidden. "
            f"Run 'abicheck {command_label} --help-all' to see those options."
        )
        ctx.exit()

    return _help_callback


def _help_all_callback(
    ctx: click.Context, _param: click.Parameter, value: bool
) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(ctx.get_help(), color=ctx.color)
    ctx.exit()


def curated_help_options(
    command_label: str, common_names: frozenset[str]
) -> Callable[[F], F]:
    """Replace a command's automatic ``--help`` with a curated/full pair.

    ``command_label`` is the command name as typed on the command line (used
    only in the "advanced option(s) hidden" pointer message); ``common_names``
    is the set of dest names (``click.Option.name``, not flag strings) that
    stay visible on plain ``--help`` — everything else folds behind
    ``--help-all``.

    Declaring our own ``--help`` here (rather than adding a *second* option)
    is deliberate: Click only auto-adds its default help option when no
    existing param already claims the ``--help`` flag string, so this one
    decorator both replaces the default (curated) behaviour and adds the new
    ``--help-all`` (full) escape hatch, with no risk of two competing
    ``--help`` options.
    """

    def _decorator(func: F) -> F:
        func = click.option(
            "--help-all",
            is_flag=True,
            default=False,
            expose_value=False,
            is_eager=True,
            callback=_help_all_callback,
            help="Show every option, including advanced/less-common ones.",
        )(func)
        func = click.option(
            "--help",
            is_flag=True,
            default=False,
            expose_value=False,
            is_eager=True,
            callback=_make_help_callback(command_label, common_names),
            help="Show common options and exit. Use --help-all to see the "
            "remaining advanced options.",
        )(func)
        return func

    return _decorator


# ``compare``'s own curated/full pair (the original G21.8 M2 instance).
compare_help_options: Callable[[F], F] = curated_help_options(
    "compare", COMPARE_COMMON_OPTION_NAMES
)


# ── `dump --help-all` / `scan --help-all` (same disclosure, applied to the
# other two big commands, G21.8 follow-on) ────────────────────────────────
#
# Dest names, mirroring COMPARE_COMMON_OPTION_NAMES above.
DUMP_COMMON_OPTION_NAMES: frozenset[str] = frozenset(
    {
        # Inputs
        "headers",
        "includes",
        "version",
        # Build & source evidence (--depth build/source)
        "depth",
        "sources",
        "build_info",
        "dump_manifest_path",
        # Project config
        "build_config",
        # Output
        "output",
        "snapshot_compression",
        "dry_run",
        "verbose",
        # The help options themselves always stay visible
        "help",
        "help_all",
    }
)

SCAN_COMMON_OPTION_NAMES: frozenset[str] = frozenset(
    {
        # Inputs
        "header_pairs",
        "include_pairs",
        "sources",
        "build_info",
        "build_config",
        # Baseline & scope
        "against",
        "depth",
        "since",
        "changed_paths_opt",
        "budget",
        # Modes
        "crosschecks",
        # Policy & contract
        "policy",
        "suppress",
        # `--contract` is the whole request now -- naming a domain is what
        # turns the ADR-049 evaluator on (cli_options.resolve_contract_evaluation),
        # so there is one option here rather than a switch plus a selector.
        "contract_mode",
        # Output
        "fmt",
        "output",
        "dry_run",
        "verbose",
        # The help options themselves always stay visible
        "help",
        "help_all",
    }
)

dump_help_options: Callable[[F], F] = curated_help_options(
    "dump", DUMP_COMMON_OPTION_NAMES
)
scan_help_options: Callable[[F], F] = curated_help_options(
    "scan", SCAN_COMMON_OPTION_NAMES
)

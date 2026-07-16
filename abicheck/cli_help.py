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

"""Progressive-disclosure ``--help`` grouping (G21.8 / collapse M1).

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
"""

from __future__ import annotations

import sys

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
                "--stat",
                "--report-mode",
                "--show-impact",
                "--recommend",
                "--show-only",
                "--annotate",
                "--annotate-additions",
                "--config",
                "--exit-code-scheme",
                "--verbose",
            ],
        },
        {
            "name": "Toolchain (L2 header AST)",
            "options": [
                "--ast-frontend",
                "--old-ast-frontend",
                "--new-ast-frontend",
                "--gcc-path",
                "--gcc-prefix",
                "--gcc-options",
                "--gcc-option",
                "--sysroot",
                "--nostdinc",
            ],
        },
        {
            "name": "Policy & severity",
            "options": [
                "--policy",
                "--policy-file",
                "--suppress",
                "--strict-suppressions",
                "--require-justification",
                "--severity-preset",
                "--severity-abi-breaking",
                "--severity-potential-breaking",
                "--severity-quality-issues",
                "--severity-addition",
            ],
        },
        {
            "name": "Public-surface scoping",
            "options": [
                "--scope-public-headers",
                "--show-filtered",
                "--show-redundant",
                "--collapse-versioned-symbols",
                "--public-symbol",
                "--public-symbols-list",
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
            "name": "Build/source evidence (L3–L5)",
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
                "--public-header",
                "--public-header-dir",
                "--version",
                "--lang",
            ],
        },
        {"name": "Output", "options": ["--output", "--dry-run", "--verbose"]},
        {
            "name": "Toolchain",
            "options": [
                "--ast-frontend",
                "--gcc-path",
                "--gcc-prefix",
                "--gcc-options",
                "--gcc-option",
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
            "name": "Build/source evidence (L3–L5)",
            "options": [
                "--depth",
                "--build-info",
                "--sources",
                "--build-dir",
                "--compile-db-filter",
                "--build-query",
                "--build-compile-db",
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
                "--public-header-dir",
                "--sources",
                "--build-info",
                "--compile-db",
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
            "name": "Toolchain (L2 header AST)",
            "options": [
                "--lang",
                "--ast-frontend",
                "--gcc-path",
                "--gcc-prefix",
                "--gcc-options",
                "--gcc-option",
                "--sysroot",
                "--nostdinc",
                "--allow-build-query",
            ],
        },
        {"name": "Output", "options": ["--format", "--output", "--dry-run", "--verbose"]},
    ],
    # NB: the ABICC drop-in `compat check` (53 single-dash flags) renders with
    # plain Click help — its group is not under the rich-click `main`, so panel
    # config would be inert there. Its flags already carry help; the dialect's
    # flat help is left as-is (ADR-037 non-goal to restyle the ABICC surface).
}


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
    # Render help monochrome (no ANSI). CI runners set FORCE_COLOR/CI, which
    # would make rich emit colour escapes even into a pipe — env-dependent output
    # that breaks help-substring tests on some platforms but not others. The
    # grouping panels (the actual M1 win) are unaffected; only colour is dropped,
    # so help text is deterministic everywhere.
    rich_click.rich_click.COLOR_SYSTEM = None

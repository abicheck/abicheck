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

# Per-command option panels. Options not listed here land in rich-click's
# default trailing panel, so a new flag never has to be added here to work.
OPTION_GROUPS: dict[str, list[dict[str, object]]] = {
    "* compare": [
        {"name": "Inputs", "options": ["--header", "--include", "--lang"]},
        {
            "name": "Output & reporting",
            "options": [
                "--output", "--format", "--demangle", "--stat", "--report-mode",
                "--show-impact", "--recommend", "--show-only", "--annotate",
                "--annotate-additions", "--config", "--exit-code-scheme", "--verbose",
            ],
        },
        {
            "name": "Toolchain (L2 header AST)",
            "options": [
                "--ast-frontend", "--old-ast-frontend", "--new-ast-frontend",
                "--gcc-path", "--gcc-prefix", "--gcc-options", "--gcc-option",
                "--sysroot", "--nostdinc",
            ],
        },
        {
            "name": "Policy & severity",
            "options": [
                "--policy", "--policy-file", "--suppress", "--strict-suppressions",
                "--require-justification", "--severity-preset",
                "--severity-abi-breaking", "--severity-potential-breaking",
                "--severity-quality-issues", "--severity-addition",
            ],
        },
        {
            "name": "Public-surface scoping",
            "options": [
                "--scope-public-headers", "--show-filtered", "--show-redundant",
                "--collapse-versioned-symbols", "--public-symbol",
                "--public-symbols-list",
            ],
        },
        {
            "name": "Debug info",
            "options": [
                "--dwarf-only", "--debug-format", "--debug-root",
                "--debuginfod", "--debuginfod-url",
            ],
        },
        {
            "name": "Build/source evidence (L3–L5)",
            "options": [
                "--build-info", "--sources", "--depth", "--max",
            ],
        },
        {
            "name": "Dependencies",
            "options": ["--follow-deps", "--search-path", "--ld-library-path"],
        },
        {
            "name": "Per-side overrides",
            "options": [
                "--old-version", "--new-version", "--pdb-path",
            ],
        },
        {
            "name": "Build-config matrix & idioms",
            "options": [
                "--probe-matrix", "--pattern-verdicts",
                "--explain-patterns", "--surface-metrics",
            ],
        },
        {
            "name": "Release (directory/package inputs)",
            "options": [
                "--jobs", "--dso-only", "--output-dir", "--fail-on-removed-library",
                "--debug-info1", "--debug-info2", "--devel-pkg1", "--devel-pkg2",
                "--include-private-dso", "--keep-extracted", "--manifest",
                "--bundle-system-providers", "--bundle-cohort", "--no-bundle-analysis",
            ],
        },
    ],
    "* collect": [
        {
            "name": "Inputs",
            "options": [
                "--binary", "--header", "--source-root", "--build-dir",
                "--compile-db", "-p",
            ],
        },
        {
            "name": "Build-system adapters",
            "options": [
                "--from", "--build-system",
                "--read-compiler-record",
            ],
        },
        {
            "name": "Source-ABI (L4)",
            "options": [
                "--source-abi", "--source-abi-extractor", "--source-abi-scope",
                "--source-abi-target", "--source-abi-cache", "--clang-bin",
                "--android-dump",
            ],
        },
        {
            "name": "Source graph (L5)",
            "options": [
                "--source-graph", "--call-graph", "--include-graph",
                "--kythe-entries", "--codeql-results",
            ],
        },
        {
            "name": "Extractors & collection",
            "options": [
                "--extractor-manifest", "--collection-mode", "--allow-build-query",
                "--changed-path",
            ],
        },
        {"name": "Output", "options": ["--output", "--verbose"]},
    ],
    "* dump": [
        {
            "name": "Inputs",
            "options": [
                "--header", "--include", "--public-header", "--public-header-dir",
                "--version", "--lang",
            ],
        },
        {"name": "Output", "options": ["--output", "--show-data-sources", "--verbose"]},
        {
            "name": "Toolchain",
            "options": [
                "--ast-frontend", "--gcc-path", "--gcc-prefix", "--gcc-options",
                "--gcc-option", "--sysroot", "--nostdinc",
            ],
        },
        {
            "name": "Debug info",
            "options": [
                "--dwarf-only", "--debug-format", "--debug-root", "--debuginfod",
                "--debuginfod-url", "--pdb-path",
            ],
        },
        {
            "name": "Build/source evidence (L3–L5)",
            "options": [
                "--depth", "--max", "--build-info", "--sources",
                "--build-dir", "--compile-db-filter",
                "--build-query", "--build-compile-db", "--config",
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
    # `graph explain` (cli_graph). `graph compare` carries only --format, which
    # the "* compare" key above already groups under "Output & reporting" (the
    # fnmatch key matches the sub-command name) — so only `explain` needs an
    # explicit entry to keep its inputs ahead of the output flag.
    "* explain": [
        {
            "name": "Inputs",
            "options": ["--sources", "--symbol", "--report", "--finding-id"],
        },
        {"name": "Output", "options": ["--format"]},
    ],
    "* scan": [
        {
            "name": "Inputs",
            "options": [
                "--binary", "--header", "--include", "--public-header-dir",
                "--sources", "--build-info", "--compile-db", "--config",
            ],
        },
        {
            "name": "Baseline & scope",
            "options": [
                "--baseline", "--baseline-header", "--baseline-include",
                "--depth", "--since", "--changed-path", "--budget",
            ],
        },
        {
            "name": "Modes",
            "options": ["--audit", "--estimate", "--crosscheck", "--risk-rules"],
        },
        {
            "name": "Toolchain (L2 header AST)",
            "options": [
                "--lang", "--ast-frontend", "--gcc-path", "--gcc-prefix",
                "--gcc-options", "--gcc-option", "--sysroot", "--nostdinc",
                "--allow-build-query",
            ],
        },
        {"name": "Output", "options": ["--format", "--output", "--verbose"]},
    ],
    "* appcompat": [
        {
            "name": "Inputs",
            "options": ["--check-against", "--header", "--include", "--lang"],
        },
        {
            "name": "Per-side overrides",
            "options": [
                "--old-version", "--new-version",
            ],
        },
        {
            "name": "Policy & severity",
            "options": [
                "--policy", "--policy-file", "--suppress", "--severity-preset",
                "--scope-public-headers",
            ],
        },
        {
            "name": "Output & reporting",
            "options": [
                "--format", "--output", "--show-irrelevant",
                "--list-required-symbols", "--verbose",
            ],
        },
    ],
    # NB: the ABICC drop-in `compat check` (53 single-dash flags) renders with
    # plain Click help — its group is not under the rich-click `main`, so panel
    # config would be inert there. Its flags already carry help; the dialect's
    # flat help is left as-is (ADR-037 non-goal to restyle the ABICC surface).
}


def configure_rich_help() -> None:
    """Register the option-group panels with rich-click (idempotent).

    Best-effort: if rich-click is unavailable the CLI still works with click's
    plain help, so the import failure is swallowed rather than aborting startup.
    """
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

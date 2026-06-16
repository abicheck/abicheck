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
                "--annotate-additions",
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
                "--dwarf-only", "--debug-format", "--debug-root", "--debug-root1",
                "--debug-root2", "--debuginfod", "--debuginfod-url",
            ],
        },
        {
            "name": "Build/source evidence (L3–L5)",
            "options": [
                "--old-build-info", "--new-build-info", "--old-sources",
                "--new-sources", "--collect-mode",
            ],
        },
        {
            "name": "Dependencies",
            "options": ["--follow-deps", "--search-path", "--ld-library-path"],
        },
        {
            "name": "Per-side overrides",
            "options": [
                "--old-header", "--new-header", "--old-include", "--new-include",
                "--old-version", "--new-version", "--pdb-path", "--old-pdb-path",
                "--new-pdb-path",
            ],
        },
        {
            "name": "Build-config matrix & idioms",
            "options": [
                "--probe-matrix-old", "--probe-matrix-new", "--pattern-verdicts",
                "--explain-patterns", "--surface-metrics",
            ],
        },
    ],
    "* dump": [
        {
            "name": "Inputs",
            "options": [
                "--header", "--include", "--public-header", "--public-header-dir",
                "--version", "--lang",
            ],
        },
        {"name": "Output", "options": ["--output", "--show-data-sources"]},
        {
            "name": "Toolchain",
            "options": [
                "--gcc-path", "--gcc-prefix", "--gcc-options", "--sysroot",
                "--nostdinc",
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
                "--depth", "--max", "--collect-mode", "--build-info", "--sources",
                "--build-dir", "--compile-db-filter",
                "--build-query", "--build-compile-db", "--build-config",
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
    rich_click.rich_click.OPTION_GROUPS.update(OPTION_GROUPS)
    # Show the legacy --btf/--ctf/--dwarf aliases only when explicitly asked;
    # keep the panels tight. (They are already hidden=True at definition.)
    rich_click.rich_click.STYLE_OPTIONS_PANEL_BORDER = "dim"

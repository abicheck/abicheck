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

"""Frozen option-set snapshot data for ``test_cli_contract.py``.

Split out of that file (a debt.yaml ``no_growth``-tracked module) purely to
keep it under its recorded baseline -- a pure data fixture, not a test
module of its own, mirroring how ``_detector_mutations.py``/
``canonical_identity_contract.py`` hold shared test data separately from the
tests that consume it (see ``tests/CLAUDE.md``'s "Helpers" section).

A diff here in review means a flag was added or dropped from ``compare`` --
update deliberately.
"""

from __future__ import annotations

OPTION_SET_SNAPSHOT: dict[str, tuple[str, ...]] = {
    "compare": (
        "--allow-ast-frontend-fallback",
        "--allow-unsupported-castxml",
        "--ast-frontend",
        "--audit-suppressions",
        "--btf",
        "--bundle-cohort",
        "--bundle-facts-library-manifest",
        "--bundle-facts-out",
        "--bundle-system-providers",
        "--compiler",
        "--compiler-option",
        "--compiler-prefix",
        "--config",
        "--contract",
        "--ctf",
        "--debug-format",
        "--debug-root",
        "--debuginfod",
        "--debuginfod-url",
        "--build-info",
        "--debug-info",
        "--demangle",
        "--depth",
        "--devel-pkg",
        "--diagnostic-comparison",
        "--dry-run",
        "--dso-only",
        "--dump-manifest",
        "--dwarf",
        "--dwarf-only",
        "--env-matrix",
        "--explain-patterns",
        "--fail-on-removed-library",
        "--follow-deps",
        "--format",
        "--frontend-context",
        "--header",
        "--header-graph",
        "--header-graph-includes",
        "--help",
        "--help-all",
        "--include",
        "--include-system-declarations",
        "--include-private-dso",
        "--jobs",
        "--keep-extracted",
        "--lang",
        "--ld-library-path",
        "--manifest",
        "--max-json-object-nodes",
        "--no-bundle-analysis",
        "--no-debuginfod",
        "--no-demangle",
        "--no-dwarf-only",
        "--no-fail-on-removed-library",
        "--no-nostdinc",
        "--no-pattern-verdicts",
        "--no-scope-public-headers",
        "--nostdinc",
        "--output",
        "--output-dir",
        "--pack",
        "--pattern-verdicts",
        "--pdb-path",
        "--policy",
        "--post-manifest",
        "--probe-matrix",
        "--profile",
        "--reconcile-build-context",
        "--report-mode",
        "--require-complete-analysis",
        "--required-symbol",
        "--required-symbols",
        "--scope-public-headers",
        "--search-path",
        "--severity-preset",
        "--show-filtered",
        "--show-only",
        "--sources",
        "--suppress",
        "--surface-metrics",
        "--sysroot",
        "--use-cases",
        "--used-by",
        "--verbose",
        "--write",
        "--version",
        "-H",
        "-I",
        "-j",
        "-o",
        "-v",
    ),
    # `appcompat` folded into `compare --used-by` (ADR-043); it no longer has
    # its own registered command/option-set snapshot.
}

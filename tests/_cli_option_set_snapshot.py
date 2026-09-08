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
        # ADR-068 Phase 2c/2d: changed-path localization + the candidate-side
        # abi3 audit, moved off `scan` (one-comparison-product.md §3 #12/#15).
        # Phase 7 (one-comparison-product.md §4.1, ADR-037 D8.1) deleted 17
        # flags outright, with no CLI spelling left at all:
        # --allow-ast-frontend-fallback, --allow-unsupported-castxml,
        # --ast-frontend, --compiler, --compiler-option, --compiler-prefix,
        # --debug-format, --debuginfod, --debuginfod-url, --dwarf-only,
        # --frontend-context, --lang, --no-debuginfod, --no-dwarf-only,
        # --no-nostdinc, --nostdinc, --sysroot -- .abicheck.yml's compile:/
        # debug: blocks are their only source now.
        "--abi3",
        "--audit-suppressions",
        "--bundle-facts-library-manifest",
        "--bundle-facts-out",
        "--changed-path",
        "--config",
        "--contract",
        "--debug-root",
        "--build-info",
        "--debug-info",
        "--demangle",
        "--depth",
        "--devel-pkg",
        "--diagnostic-comparison",
        "--dry-run",
        "--dso-only",
        "--dump-manifest",
        "--env-matrix",
        "--explain-patterns",
        "--fail-on-removed-library",
        "--follow-deps",
        "--format",
        "--header",
        "--help",
        "--help-all",
        "--include",
        "--include-system-declarations",
        "--include-private-dso",
        "--instantiation-manifest",
        "--jobs",
        "--keep-extracted",
        "--ld-library-path",
        "--max-json-object-nodes",
        "--no-baseline",
        "--no-bundle-analysis",
        "--no-demangle",
        "--no-fail-on-removed-library",
        "--on-incomplete-scope",
        "--no-scope-public-headers",
        "--new-variant",
        "--old-variant",
        "--output",
        "--output-dir",
        "--pack",
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
        "--select",
        "--select-required",
        "--severity-preset",
        "--show-filtered",
        "--show-only",
        "--since",
        "--sources",
        "--support-promise",
        "--suppress",
        "--surface-metrics",
        "--use-cases",
        "--used-by",
        "--used-by-manifest",
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

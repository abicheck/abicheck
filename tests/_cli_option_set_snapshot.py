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
        # Phase 7d deleted 4 more, with no CLI spelling left at all:
        # --dso-only (release.dso_only), --fail-on-removed-library/
        # --no-fail-on-removed-library (gate.fail_on_removed_library),
        # --include-private-dso (release.include_private_dso),
        # --on-incomplete-scope (scope.on_incomplete).
        # ADR-068 D4/Phase 5 collapsed --report-mode/--show-only/--demangle/
        # --no-demangle/--explain-patterns into one repeatable --view option.
        # Phase 7d remainder + 7g deleted 3 more, with no CLI spelling left
        # at all: --keep-extracted/--no-bundle-analysis (no replacement --
        # extraction cleanup and bundle analysis are unconditional now),
        # --max-json-object-nodes (resource_limits.max_bundle_facts_decode_
        # nodes in .abicheck.yml).
        # --instantiation-manifest, --bundle-facts-out, and
        # --bundle-facts-library-manifest stay CLI flags this phase (see
        # frontends/cli/options/release.py and bundle_facts.py's own
        # docstrings for why).
        # Phase 5 + Phase 7i removed six more with no CLI spelling left:
        # --surface-metrics/--show-filtered/--audit-suppressions (AUTO --
        # all three data are computed unconditionally; `--view filtered`/
        # `--view suppressions` render the two that have a rendering, and
        # the metrics are ordinary findings needing no selector),
        # --reconcile-build-context (AUTO -- ADR-039 reconciliation is
        # unconditional), --pdb-path (debug.pdb_path) and
        # --support-promise (release.support_promise).
        # Phase 7j collapsed the last two-sided input still spelled as two
        # flags: --old-variant/--new-variant -> one side-scoped --variant
        # ([old=|new=]VARIANT_ID), the same ADR-040 Lever 1 shape --header/
        # --version already use. No alias: the old pair exits 64.
        "--abi3",
        "--budget",
        "--bundle-facts-library-manifest",
        "--bundle-facts-out",
        "--changed-path",
        "--config",
        "--contract",
        "--debug-root",
        "--build-info",
        "--debug-info",
        "--depth",
        "--devel-pkg",
        "--diagnostic-comparison",
        "--dry-run",
        "--dump-manifest",
        "--follow-deps",
        "--format",
        "--header",
        "--help",
        "--help-all",
        "--include",
        "--include-system-declarations",
        "--instantiation-manifest",
        "--ld-library-path",
        "--max-findings-per-library",
        "--no-baseline",
        "--no-scope-public-headers",
        "--output",
        "--output-dir",
        "--pack",
        "--policy",
        "--post-manifest",
        "--probe-matrix",
        "--require-complete-analysis",
        "--required-symbol",
        "--scope-public-headers",
        "--search-path",
        "--select",
        "--select-required",
        "--severity-preset",
        "--since",
        "--sources",
        "--suppress",
        "--use-cases",
        "--used-by",
        "--used-by-manifest",
        "--variant",
        "--verbose",
        "--view",
        "--write",
        "--version",
        "-H",
        "-I",
        "-o",
        "-v",
    ),
    # `appcompat` folded into `compare --used-by` (ADR-043); it no longer has
    # its own registered command/option-set snapshot.
}

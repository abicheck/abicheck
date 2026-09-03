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

"""Classifies a PR's changed files for ``.github/workflows/performance.yml``.

The Performance workflow's PR-triggered jobs (``scaling``, ``regression``,
``header-graph-perf``, ``header-graph-regression``) only need to run when a PR
actually touches performance-sensitive code -- previously this was a
``pull_request.paths:`` YAML filter on the whole workflow trigger. That has a
real structural cost this module exists to remove: a *trigger-level* path
filter means the workflow itself never registers a check run on a
non-matching PR, and the pattern list lived only in unversioned, untestable
YAML glob strings, hand-maintained by inspection. Both were real, not
theoretical -- two genuinely perf-sensitive changes (Bazel/CMake aquery/
cquery scoping, auto-applied L3 compiler context onto every L2 header parse)
merged with **no** Performance check run at all because the list hadn't been
updated to cover them (see docs/contribute/performance.md "Coverage gaps this
workflow does not close").

The fix is the standard "classifier job" pattern: the workflow trigger itself
drops its ``paths:`` filter (so the workflow -- and its check run -- always
fires on every qualifying PR event), and a cheap first job diffs the PR's
changed files against :data:`PERF_SENSITIVE_PATTERNS` (this module, not YAML)
to decide whether the expensive downstream jobs actually run. The pattern
list moving to a real Python module means it can be unit-tested (see
``tests/test_classify_perf_paths.py``) instead of only exercised by whichever
PR happens to touch a listed path.

Deliberately ONE shared pattern list gating all four downstream jobs
uniformly, not a per-job shard split (despite this file's "choose shards"
framing in earlier design discussion) -- ``check_header_graph_perf.py``
imports ``abicheck.buildsource.header_graph`` (reached through
``service._attach_header_graph``), so even a directory as specifically-named
as ``abicheck/buildsource/`` is relevant to the header-graph jobs, not just
the compare()-scaling jobs the directory's own name suggests. A precise
per-job split needs a verified transitive-import trace from each of
``benchmark_scaling.py``'s and ``check_header_graph_perf.py``'s own entry
points, which is real, separate work -- a *wrong* split would silently
under-cover one shard, exactly the failure mode this module exists to close,
so it is not attempted speculatively here. Tracked as a known follow-up in
docs/contribute/performance.md rather than guessed at.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from collections.abc import Iterable
from pathlib import Path

#: fnmatch-style path patterns (Python's ``fnmatch`` does not treat ``/`` as
#: special, so a single ``*`` -- and therefore ``**`` -- already matches
#: across path separators; no separate "double star" handling is needed).
#: This is the exact file set ``performance.yml``'s ``pull_request.paths:``
#: filter previously enumerated directly in YAML -- ported verbatim, not
#: reconsidered, so introducing the classify job changes *where* this list
#: lives, not what it covers.
PERF_SENSITIVE_PATTERNS: tuple[str, ...] = (
    # Detector core: the compare() pipeline benchmark_scaling.py exercises.
    "abicheck/diff_*.py",
    "abicheck/checker.py",
    "abicheck/checker_policy.py",
    # checker.compare() calls the registry's ensure_loaded()/run_all() for
    # every comparison this workflow benchmarks -- detector discovery,
    # ordering, and dispatch are exactly as load-bearing as checker.py
    # itself (abicheck/CLAUDE.md's own "Classify changes" pipeline stage
    # lists these two alongside checker.py/checker_policy.py), and were
    # missing from both this list and the paths: filter it replaced (Codex
    # review, fresh evidence -- a pre-existing gap this classify job's own
    # PERF_SENSITIVE_PATTERNS ported forward rather than introduced).
    "abicheck/detectors.py",
    "abicheck/detector_registry.py",
    "abicheck/post_processing.py",
    "abicheck/internal_leak.py",
    "abicheck/demangle.py",
    "abicheck/binary_fingerprint.py",
    "abicheck/diff_namespaces.py",
    "abicheck/versioned_symbol_scheme.py",
    "abicheck/surface.py",
    "abicheck/suppression.py",
    "abicheck/html_report.py",
    "abicheck/sarif.py",
    "abicheck/junit_report.py",
    "abicheck/severity.py",
    "abicheck/policy/severity.py",
    "abicheck/serialization.py",
    "abicheck/pe_metadata.py",
    "abicheck/macho_metadata.py",
    "abicheck/cli_scan.py",
    "abicheck/dumper.py",
    "abicheck/dwarf_presence.py",
    "abicheck/service.py",
    "scripts/benchmark_scaling.py",
    "tests/test_performance.py",
    "tests/test_perf_binary_scan.py",
    "tests/test_perf_dump_scaling.py",
    "tests/test_benchmark_scaling.py",
    # Every buildsource module (header/include/call/type graph, L3 build
    # evidence collection, Bazel/CMake/Ninja/Make adapters, S1-S6 source
    # replay, the preprocessor pre-scan, ...) -- a directory glob rather than
    # an enumerated file list, since this tree gained real, previously-
    # unguarded perf-sensitive changes a per-file list missed entirely (P0.2
    # Bazel aquery/cquery scoping, P0.3 auto-applying L3 compiler context to
    # every L2 header parse -- both merged with no Performance check run at
    # all). Also reached by check_header_graph_perf.py (buildsource.
    # header_graph, via service._attach_header_graph) -- not core-only.
    "abicheck/buildsource/**",
    "abicheck/provenance.py",
    "abicheck/dumper_cache.py",
    "abicheck/dumper_ast_config.py",
    "abicheck/dumper_sysinc.py",
    "abicheck/dumper_clang.py",
    "abicheck/dumper_clang_errors.py",
    "abicheck/dumper_toolchain.py",
    "abicheck/dumper_castxml.py",
    "abicheck/compile_context.py",
    "abicheck/castxml_policy.py",
    "abicheck/header_utils.py",
    "action/install-castxml.sh",
    "scripts/check_header_graph_perf.py",
    "tests/test_header_graph_perf_gate.py",
    # scan()/dump()/compare() orchestration, resolution, and caching -- the
    # owners of scan latency a narrower path filter previously missed
    # entirely (no scan-shaped scenario ran under this workflow at all for a
    # change to any of these).
    "abicheck/service_input_resolution.py",
    "abicheck/service_scan.py",
    "abicheck/service_dump_pipeline.py",
    "abicheck/service_compare_pipeline.py",
    "abicheck/service_dump_cache.py",
    "abicheck/scan_engine.py",
    "abicheck/snapshot_cache.py",
    "abicheck/cli_buildsource.py",
    "abicheck/cli_buildsource_helpers.py",
    "abicheck/cli_dump_helpers.py",
    "abicheck/cli_resolve.py",
    # The eval-suite scan-level scalability harness, and the shared
    # measurement/threshold/classification modules the perf-gate scripts (and
    # this workflow's own classify job) import.
    "eval/scan_level_scaling.py",
    "eval/scaling.py",
    "scripts/perf_measurement.py",
    "scripts/perf_baseline.py",
    "scripts/classify_perf_paths.py",
    "tests/test_classify_perf_paths.py",
    ".github/workflows/performance.yml",
)


def changed_files_are_perf_sensitive(changed_files: Iterable[str]) -> bool:
    """True if any *changed_files* path matches `PERF_SENSITIVE_PATTERNS`."""
    files = list(changed_files)
    return any(
        fnmatch.fnmatch(f, pattern)
        for f in files
        for pattern in PERF_SENSITIVE_PATTERNS
    )


def matched_patterns(changed_files: Iterable[str]) -> dict[str, str]:
    """Map each matching changed file to the pattern it matched (first hit).

    Purely for the classify job's own diagnostic log line -- lets a PR author
    see *why* the lane ran (or, absent from this mapping, why it didn't)
    without having to read :data:`PERF_SENSITIVE_PATTERNS` by hand.
    """
    hits: dict[str, str] = {}
    for f in changed_files:
        for pattern in PERF_SENSITIVE_PATTERNS:
            if fnmatch.fnmatch(f, pattern):
                hits[f] = pattern
                break
    return hits


def _write_github_output(name: str, value: str) -> None:
    gh_output = os.environ.get("GITHUB_OUTPUT")
    line = f"{name}={value}\n"
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(line)
    else:
        # Local/manual invocation (no GITHUB_OUTPUT env var set) -- print the
        # same "key=value" shape to stdout so this is still --help-usable and
        # scriptable outside Actions.
        print(line, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--files-from",
        type=Path,
        default=None,
        help="File of changed paths, one per line (default: read stdin)",
    )
    parser.add_argument(
        "--always",
        action="store_true",
        help=(
            "Skip classification and unconditionally report run=true. For "
            "triggers with no base revision to diff against (schedule, "
            "workflow_dispatch) -- pull_request.paths: never applied to "
            "those triggers either, so this preserves that pre-existing "
            "behaviour rather than changing it."
        ),
    )
    parser.add_argument(
        "--force-label",
        default=None,
        help=(
            "The label name that force-runs the lane regardless of changed "
            "paths. If this name is present in --current-labels, run=true "
            "unconditionally -- this is the actual fix for a pre-existing "
            "gap: the workflow's own comment claimed adding a 'performance' "
            "label re-triggers the lane, but no condition ever checked the "
            "label name, so a 'performance' label on a PR touching no "
            "listed path silently did not force a run."
        ),
    )
    parser.add_argument(
        "--current-labels",
        default=None,
        help=(
            "Comma-separated label names currently attached to the PR (e.g. "
            "github.event.pull_request.labels.*.name, joined). Deliberately "
            "the PR's *current* label set, not github.event.label.name -- "
            "the latter is populated only on the one labeled/unlabeled event "
            "that added/removed a label and is empty on every other "
            "pull_request sub-event (opened/reopened/synchronize), so a "
            "later push to an already-'performance'-labeled PR would "
            "silently stop force-running once the triggering event was no "
            "longer the labeling itself (Codex review)."
        ),
    )
    args = parser.parse_args(argv)

    current_labels = {
        label.strip()
        for label in (args.current_labels or "").split(",")
        if label.strip()
    }
    if args.force_label is not None and args.force_label in current_labels:
        run = True
        changed: list[str] = []
    elif args.always:
        run = True
        changed = []
    else:
        text = args.files_from.read_text() if args.files_from else sys.stdin.read()
        changed = [line.strip() for line in text.splitlines() if line.strip()]
        run = changed_files_are_perf_sensitive(changed)

    _write_github_output("run", "true" if run else "false")

    print(
        f"perf-sensitive: {run} ({len(changed)} changed file(s) checked)",
        file=sys.stderr,
    )
    if changed:
        hits = matched_patterns(changed)
        if hits:
            print("matched:", file=sys.stderr)
            for f, pattern in sorted(hits.items()):
                print(f"  {f}  (pattern: {pattern})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

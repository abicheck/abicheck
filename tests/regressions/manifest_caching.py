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

"""Bug classes about caches serving something other than the real answer.

A sibling of `manifest_performance.py`, and deliberately not part of it: the
classes there produce the right answer and the defect is its cost, while a
cache defect produces the *wrong* answer, or none, and is usually silent.
Invalidation, schema drift and "did the cheap path actually run" are one
subject; how often correct work is repeated is another.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["CACHING_BUG_CLASSES"]


CACHING_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="perf.derived_cache_must_match_what_recomputing_would_give",
        invariant=(
            "A cache that serves a *derived* artifact in place of the "
            "expensive input it came from must serve exactly what "
            "recomputing would have produced, and must refuse anything it "
            "cannot prove it understands. The two halves fail differently "
            "and both need their own guard: serving a stale or "
            "wrongly-shaped artifact is silent evidence loss (fewer graph "
            "edges, fewer findings, no error anywhere), while refusing too "
            "eagerly costs only a slow run. The guard is therefore a "
            "result-equality check over several generated inputs PLUS an "
            "observation that the cheap path actually engaged -- an "
            "equality assertion alone passes against an implementation "
            "that ignores the cache and recomputes, which is the behaviour "
            "being removed and which shows up only as memory nobody "
            "measured. Corollary on invalidation: a derived artifact's "
            "validity is its source's validity, so it is named after the "
            "source entry rather than keyed by a second, independently "
            "derived key -- a key that missed one input would serve a "
            "stale artifact, and the inputs are resolved inside the "
            "producer where a copy cannot see them."
        ),
        # The header-graph attach re-parsed an 822 MiB clang AST on every
        # warm run to recompute a 16.5 MiB projection it had already
        # produced identically. The schema is self-describing (the edge
        # dataclasses' own field lists) precisely because a hand-bumped
        # version integer is the kind of registry entry this repository has
        # repeatedly watched go stale.
        fixed_by=(1338, 1339),
        seed_tests=(
            "tests/test_header_graph_projection_cache.py",
            # The same invariant against a *third* producer of the same
            # artifact: a projection streamed from the AST document itself
            # must equal the one built from the whole tree, exactly. Its own
            # module because the thing it can get wrong is different -- not
            # a stale or mis-shaped cache entry, but a walk that loses the
            # state clang's format carries across sibling declarations.
            "tests/test_header_graph_ast_stream.py",
        ),
        # `()` deliberately, per this field's own contract: "cli" needs a
        # real Click/`CliRunner` invocation and "python-api" a call through
        # `abicheck.service`, and every seed test here calls
        # `service_header_graph_attach._attach_header_graph` and
        # `dumper._clang_header_dump` directly. Claiming either would
        # conceal exactly the cross-surface gap this registry exists to
        # surface -- recorded below as a known gap instead (CodeRabbit
        # review, PR #1339).
        public_surfaces=(),
        axes={
            "cache_state": ("cold", "warm", "sidecar-discarded"),
            "schema": ("current", "reordered-fields", "unknown-version", "corrupt"),
            # Earns a real entry per this field's frontend rule: a seed test
            # invokes the live `clang -ast-dump=json` backend, not a
            # hand-built AST fragment fed to an internal parser.
            "frontend": ("clang",),
            # How the projection was produced. Every value must yield the
            # identical artifact, and the axis exists because each is a
            # genuinely different mechanism with its own failure: the whole
            # tree (the reference), the streamed document (loses
            # cross-sibling state), the sidecar (stale or mis-shaped).
            "derivation": ("whole-tree", "streamed", "sidecar"),
            # Every filesystem call either entry point makes, against the
            # faults reachable through it -- the axis the first version of
            # this class had no coverage on at all, which is how an
            # unguarded `UnicodeDecodeError` escaped.
            "io_fault": ("invalid-utf8", "eacces", "enospc", "erofs", "eisdir", "eio"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "No seed test for this class reaches a real public "
                    "surface (a Click invocation or a call through "
                    "`abicheck.service`, per `BugClass.public_surfaces`'s "
                    "own contract). The cold/warm equivalence is proven "
                    "through `_attach_header_graph` and the codec through "
                    "its own entry points -- both internal, however real "
                    "the clang backend driving them is. A silent "
                    "recompute or a stale projection would therefore be "
                    "caught at that layer but is not asserted to survive "
                    "into a real `compare`/`dump` invocation's reported "
                    "output. Not attempted here: the measured evidence for "
                    "this change is a CLI run on oneDAL, which is a "
                    "measurement rather than a gating test (CodeRabbit "
                    "review, PR #1339)."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-4",
            ),
            KnownGap(
                description=(
                    "The equality-plus-mechanism pair is enforced for the "
                    "header-graph projection cache only. The other derived "
                    "caches in this tree (the snapshot cache, the build "
                    "cache, the per-TU source-replay cache) assert their "
                    "own hit/miss behaviour but none of them pairs a "
                    "result-equality check with a count of the expensive "
                    "work actually skipped, so a silent recompute would "
                    "read as a pass there."
                ),
                reference="docs/contribute/measurements/header-graph-attach-memory.md",
            ),
        ),
    ),
)

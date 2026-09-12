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

"""The L2 header-only semantic graph on a directory/package ``compare``.

**What this pins, and why it is executed rather than asserted in prose.**
``cli_resolve.py`` and G31 Phase A both carried a written claim that the L2
header-only graph "is structurally skipped for directory/package (set-input)
compares, since the fan-out never routes through a graph-attaching
single-pair path". That claim was true when written and stopped being true
when the fan-out was migrated onto ``service.run_compare`` — every member
pair now resolves through ``service.run_dump``, whose ``_attach_header_graph``
step is unconditional whenever headers were parsed. Nothing failed when the
claim went stale, because the only thing stating it was a comment.

This is the same bug class as its sibling
``tests/test_cli_compare_release_depth.py``
(``cli_surface.capability_guard_diverged_from_pipeline``): a front-end
belief about what a downstream pipeline does, recorded where no test can
contradict it. So the invariant here is executed against the real attach —
and it is a *parity* invariant, whose oracle is the single-pair compare of
the identical member, rather than a hard-coded node count that would only
pin today's parser output.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

_HEADERS = {
    "foo": """
#ifndef FOO_H
#define FOO_H
struct FooWidget { int a; int b; };
#ifdef __cplusplus
extern "C" {
#endif
int foo_area(struct FooWidget *w);
#ifdef __cplusplus
}
#endif
#endif
""",
    "bar": """
#ifndef BAR_H
#define BAR_H
struct BarGadget { long id; char tag; };
#ifdef __cplusplus
extern "C" {
#endif
long bar_id(struct BarGadget *g);
#ifdef __cplusplus
}
#endif
#endif
""",
}

_SOURCES = {
    "foo": '#include "foo.h"\nint foo_area(struct FooWidget *w){return w->a*w->b;}\n',
    "bar": '#include "bar.h"\nlong bar_id(struct BarGadget *g){return g->id;}\n',
}


def _have(tool: str) -> bool:
    from shutil import which

    return which(tool) is not None


@pytest.fixture
def two_library_release(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two real .so members per side, each with its own public header.

    Two members, not one: the claim under test was about the *fan-out*, so a
    single-member directory would not distinguish "the fan-out attaches the
    graph" from "the fan-out degenerates to the single-pair path".
    """
    if not _have("gcc"):
        pytest.skip("gcc is required to build the release fixture")
    inc = tmp_path / "include"
    inc.mkdir()
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    for side in (old_dir, new_dir):
        side.mkdir()
    for name, text in _HEADERS.items():
        (inc / f"{name}.h").write_text(text, encoding="utf-8")
        src = tmp_path / f"{name}.c"
        src.write_text(_SOURCES[name], encoding="utf-8")
        for side in (old_dir, new_dir):
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    f"-I{inc}",
                    "-o",
                    str(side / f"lib{name}.so"),
                    str(src),
                ],
                check=True,
                capture_output=True,
            )
    return old_dir, new_dir, inc


def _graphs_attached(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> dict[str, int]:
    """Run ``compare`` with *argv*, returning ``{library: graph node count}``.

    Recorded from the real attach step's own output, so a graph that is
    built but empty is distinguishable from one never built at all — the
    distinction the stale claim was about.

    Node **identities**, not a node count: ``GraphNode.id`` is the
    deterministic identity graph comparison itself keys on, so two graphs of
    equal size but different content would compare equal under a count
    (CodeRabbit review). The count is recoverable from the ids, not the
    reverse.
    """
    import abicheck.service_dump_native as native
    from abicheck.cli import main

    real_attach = native._attach_header_graph
    seen: dict[str, tuple[str, ...]] = {}

    def _recording(*args: object, **kwargs: object) -> object:
        snap = real_attach(*args, **kwargs)
        pack = getattr(snap, "build_source", None)
        graph = getattr(pack, "source_graph", None) if pack is not None else None
        if graph is not None:
            ids = tuple(sorted(node.id for node in graph.nodes))
            # A library is attached once per side; keep the richer of the two
            # rather than whichever ran last, matching the previous max().
            if len(ids) >= len(seen.get(snap.library, ())):
                seen[snap.library] = ids
        return snap

    monkeypatch.setattr(native, "_attach_header_graph", _recording)
    CliRunner().invoke(main, argv)
    return seen


@pytest.mark.integration
class TestReleaseFanOutBuildsTheHeaderGraph:
    def test_every_member_gets_a_non_empty_graph(
        self,
        two_library_release: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The negation of the stale claim, stated over every member."""
        old_dir, new_dir, inc = two_library_release
        seen = _graphs_attached(
            monkeypatch,
            ["compare", str(old_dir), str(new_dir), "-H", str(inc), "-o", "markdown=json=-"],
        )
        assert set(seen) == {"libfoo.so", "libbar.so"}, seen
        assert all(ids for ids in seen.values()), seen

    def test_graph_matches_a_single_pair_compare_of_the_same_member(
        self,
        two_library_release: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Parity, not a pinned count: each member's graph through the
        fan-out is the graph that member gets compared on its own — compared
        by node identity, so an equal-sized but different graph fails.

        The oracle is the other public surface, so this keeps holding when
        the parser's output changes — and fails the moment the two paths
        diverge again, in either direction.
        """
        old_dir, new_dir, inc = two_library_release
        via_release = _graphs_attached(
            monkeypatch,
            ["compare", str(old_dir), str(new_dir), "-H", str(inc), "-o", "markdown=json=-"],
        )
        via_single = {}
        for name in ("libfoo.so", "libbar.so"):
            via_single.update(
                _graphs_attached(
                    monkeypatch,
                    [
                        "compare",
                        str(old_dir / name),
                        str(new_dir / name),
                        "-H",
                        str(inc),
                        "-o",
                        "markdown=json=-",
                    ],
                )
            )
        assert via_release == via_single

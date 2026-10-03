"""``graph explain`` (``localize_symbol``) names the static-archive members
that define a symbol.

Bug class: a localization view no command reads. ``defining_members`` was
written for exactly this answer ("``cache_dispatch.o`` in
``libinternal_dispatch.a``") and documented as such, but nothing called it.
The invariant: for every symbol, ``localize_symbol`` reports exactly the
members whose archive index lists it -- all of them when several do, none
when none does. The oracle is the generated archives' own member table,
never the edge walk under test.
"""

from __future__ import annotations

import random

import pytest
from test_archive_graph import _graph_with_archive, build_gnu_archive

from abicheck.buildsource.archive_graph import augment_graph_with_archives
from abicheck.buildsource.source_graph import GraphNode, localize_symbol


@pytest.mark.parametrize("seed", range(12))
def test_graph_explain_names_every_defining_member_and_no_other(tmp_path, seed) -> None:
    rng = random.Random(seed)
    symbols = [f"sym_{i}" for i in range(rng.randint(1, 8))]
    archives: dict[str, dict[str, list[str]]] = {}
    for a in range(rng.randint(1, 3)):
        members = {
            f"m{a}_{m}.o": rng.sample(symbols, rng.randint(0, len(symbols)))
            for m in range(rng.randint(1, 4))
        }
        archives[f"lib{a}.a"] = members
        (tmp_path / f"lib{a}.a").write_bytes(
            build_gnu_archive(
                [
                    (name, b"".join(f"SYM:{s}\n".encode() for s in syms) or b"x")
                    for name, syms in members.items()
                ]
            )
        )
    g = _graph_with_archive(next(iter(archives)), symbols)
    for extra in list(archives)[1:]:
        g.add_node(
            GraphNode(
                id=f"static_library://{extra}",
                kind="static_library",
                label=extra,
                provenance="build_evidence",
                confidence="reduced",
            )
        )
    augment_graph_with_archives(g, search_roots=(tmp_path,))

    for sym in [*symbols, "never_defined"]:
        expected = sorted(
            (arch, member)
            for arch, members in archives.items()
            for member, syms in members.items()
            if sym in syms
        )
        got = localize_symbol(g, sym)["defined_in_archive_members"]
        assert [(d["archive"], d["member"]) for d in got] == expected, sym

"""``header://`` node-id scheme: one owner, pinned to literal strings.

The id is a join key between the L5 source graph and the public-surface graph,
so the expected values below are spelled out literally rather than recomputed
from the constructor.
"""

from __future__ import annotations

import pytest

from abicheck.compare import surface_graph
from abicheck.model import graph_identity
from abicheck.model.graph_identity import header_node_id


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("include/foo.h", "header://include/foo.h"),
        ("foo.h", "header://foo.h"),
        ("/work/build/src/impl.cc", "header:///work/build/src/impl.cc"),
        ("include/a b.h", "header://include/a b.h"),
    ],
)
def test_header_node_id_pins_literal_string(path: str, expected: str) -> None:
    assert header_node_id(path) == expected


def test_header_node_id_is_verbatim_not_normalized() -> None:
    # The producer hands in an already-redacted path; the constructor must not
    # rewrite it, or the L5 and surface sides could disagree on the same header.
    assert (
        header_node_id("include/../include/foo.h")
        == "header://include/../include/foo.h"
    )


def test_surface_graph_uses_the_shared_owner() -> None:
    # Guards against a second copy of the scheme reappearing in compare/.
    assert surface_graph.header_node_id is graph_identity.header_node_id
    assert not hasattr(surface_graph, "_header_node_id")

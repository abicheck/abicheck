# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Unit coverage for the ADR-040 Lever 1 side-aware option normalisation.

Exercises the boundary helpers directly (they are otherwise only reached via
full CLI invocations), including the both-sides fan-out and the empty case.
"""
from __future__ import annotations

from pathlib import Path

from abicheck.cli_options import (
    _split_sided_single,
    normalize_sided_options,
    split_sided_paths,
)
from abicheck.cli_params import SIDED_PATH_PARAM


class TestSidedPathParam:
    def test_bare_value_is_both(self) -> None:
        assert SIDED_PATH_PARAM.convert("inc/foo.h", None, None) == ("both", Path("inc/foo.h"))

    def test_old_and_new_prefixes(self) -> None:
        assert SIDED_PATH_PARAM.convert("old=a.h", None, None) == ("old", Path("a.h"))
        assert SIDED_PATH_PARAM.convert("new=b.h", None, None) == ("new", Path("b.h"))

    def test_both_prefix_is_escape_hatch(self) -> None:
        # a path literally starting 'old=' is expressed with the both= escape
        assert SIDED_PATH_PARAM.convert("both=old=weird.h", None, None) == (
            "both", Path("old=weird.h"),
        )

    def test_metavar(self) -> None:
        assert SIDED_PATH_PARAM.get_metavar(None) == "[old=|new=]PATH"


class TestSplitSidedPaths:
    def test_partitions_by_side(self) -> None:
        pairs = [("both", Path("a")), ("old", Path("o")), ("new", Path("n")), ("both", Path("b"))]
        both, old, new = split_sided_paths(pairs)
        assert both == (Path("a"), Path("b"))
        assert old == (Path("o"),)
        assert new == (Path("n"),)

    def test_empty(self) -> None:
        assert split_sided_paths([]) == ((), (), ())


class TestSplitSidedSingle:
    def test_both_fans_out_to_each_side(self) -> None:
        assert _split_sided_single([("both", Path("s"))]) == (Path("s"), Path("s"))

    def test_per_side_overrides(self) -> None:
        assert _split_sided_single([("old", Path("o")), ("new", Path("n"))]) == (
            Path("o"), Path("n"),
        )

    def test_both_then_side_override(self) -> None:
        # both sets both, a later new= overrides just the new side
        assert _split_sided_single([("both", Path("s")), ("new", Path("n"))]) == (
            Path("s"), Path("n"),
        )

    def test_empty_is_none(self) -> None:
        assert _split_sided_single([]) == (None, None)


class TestNormalizeSidedOptions:
    def test_header_and_include(self) -> None:
        kw: dict[str, object] = {
            "header": (("both", Path("h")), ("old", Path("oh"))),
            "include": (("new", Path("ni")),),
        }
        normalize_sided_options(kw)
        assert kw["headers"] == (Path("h"),)
        assert kw["old_headers_only"] == (Path("oh"),)
        assert kw["new_headers_only"] == ()
        assert kw["includes"] == ()
        assert kw["new_includes_only"] == (Path("ni"),)
        assert "header" not in kw and "include" not in kw

    def test_sources_and_build_info(self) -> None:
        kw: dict[str, object] = {
            "sources": (("both", Path("src")),),
            "build_info": (("old", Path("b1")), ("new", Path("b2"))),
        }
        normalize_sided_options(kw)
        assert kw["old_sources"] == Path("src") and kw["new_sources"] == Path("src")
        assert kw["old_build_info"] == Path("b1") and kw["new_build_info"] == Path("b2")
        assert "sources" not in kw and "build_info" not in kw

    def test_absent_keys_are_untouched(self) -> None:
        kw: dict[str, object] = {"other": 1}
        normalize_sided_options(kw)
        assert kw == {"other": 1}

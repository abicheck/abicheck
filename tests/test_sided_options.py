# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Unit coverage for the ADR-040 Lever 1 side-aware option normalisation.

Exercises the boundary helpers directly (they are otherwise only reached via
full CLI invocations), including the both-sides fan-out and the empty case.
"""
from __future__ import annotations

from pathlib import Path

from abicheck.cli_options import (
    _split_sided_base,
    _split_sided_single,
    _split_sided_version,
    normalize_sided_options,
    split_sided_paths,
)
from abicheck.cli_params import SIDED_PATH_PARAM, SIDED_STR_PARAM


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

    def test_debug_root_pdb_probe_matrix(self) -> None:
        kw: dict[str, object] = {
            "debug_root": (("both", Path("d")), ("old", Path("od"))),
            "pdb": (("both", Path("p")), ("new", Path("np"))),
            "probe_matrix": (("old", Path("mo")), ("new", Path("mn"))),
        }
        normalize_sided_options(kw)
        # debug_root: multi base+per-side
        assert kw["debug_roots"] == (Path("d"),)
        assert kw["debug_roots_old"] == (Path("od"),)
        # pdb: base+per-side single (base kept, not fanned)
        assert kw["pdb_path"] == Path("p")
        assert kw["old_pdb_path"] is None and kw["new_pdb_path"] == Path("np")
        # probe_matrix: per-side single
        assert kw["probe_matrix_old"] == Path("mo") and kw["probe_matrix_new"] == Path("mn")

    def test_debug_info_and_devel_pkg(self) -> None:
        kw: dict[str, object] = {
            "debug_info": (("both", Path("di")),),
            "devel_pkg": (("old", Path("d1")), ("new", Path("d2"))),
        }
        normalize_sided_options(kw)
        # debug_info: both fans out to each side
        assert kw["debug_info1"] == Path("di") and kw["debug_info2"] == Path("di")
        assert kw["devel_pkg1"] == Path("d1") and kw["devel_pkg2"] == Path("d2")

    def test_absent_keys_are_untouched(self) -> None:
        kw: dict[str, object] = {"other": 1}
        normalize_sided_options(kw)
        assert kw == {"other": 1}


class TestSidedStrParam:
    def test_bare_value_is_both(self) -> None:
        assert SIDED_STR_PARAM.convert("1.0", None, None) == ("both", "1.0")

    def test_old_and_new_prefixes(self) -> None:
        assert SIDED_STR_PARAM.convert("old=1.0", None, None) == ("old", "1.0")
        assert SIDED_STR_PARAM.convert("new=2.0", None, None) == ("new", "2.0")

    def test_both_prefix_is_escape_hatch(self) -> None:
        # a label that literally starts 'old=' is expressed with both=
        assert SIDED_STR_PARAM.convert("both=old=weird", None, None) == ("both", "old=weird")

    def test_metavar(self) -> None:
        assert SIDED_STR_PARAM.get_metavar(None) == "[old=|new=]LABEL"


class TestSplitSidedVersion:
    def test_defaults_when_empty(self) -> None:
        # no --version given → the historical per-side defaults
        assert _split_sided_version(()) == ("old", "new")

    def test_both_fans_out_to_each_side(self) -> None:
        assert _split_sided_version([("both", "1.5")]) == ("1.5", "1.5")

    def test_per_side_overrides_keep_other_default(self) -> None:
        assert _split_sided_version([("old", "1.0")]) == ("1.0", "new")
        assert _split_sided_version([("new", "2.0")]) == ("old", "2.0")

    def test_both_then_side_override(self) -> None:
        assert _split_sided_version([("both", "s"), ("new", "2.0")]) == ("s", "2.0")

    def test_normalize_version(self) -> None:
        kw: dict[str, object] = {"version": (("old", "1.0"), ("new", "2.0"))}
        normalize_sided_options(kw)
        assert kw["old_version"] == "1.0" and kw["new_version"] == "2.0"
        assert "version" not in kw

    def test_normalize_version_empty_uses_defaults(self) -> None:
        kw: dict[str, object] = {"version": ()}
        normalize_sided_options(kw)
        assert kw["old_version"] == "old" and kw["new_version"] == "new"


class TestSplitSidedBase:
    def test_both_kept_as_base_not_fanned(self) -> None:
        # base+per-side single: 'both' is its own base value, not copied to sides
        assert _split_sided_base([("both", Path("b"))]) == (Path("b"), None, None)

    def test_per_side_and_last_wins(self) -> None:
        assert _split_sided_base([("old", Path("o1")), ("old", Path("o2")), ("new", Path("n"))]) == (
            None, Path("o2"), Path("n"),
        )

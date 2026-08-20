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

"""Fast, compiler-free unit coverage for
``_dump_compile_args_normalize.split_compile_args`` -- the primitive
``tests/test_dump_cli_typed_api_parity.py`` (an ``integration``-marked,
real-compiler test) relies on to compare two ``ast_compile_args``
sequences for real semantic equivalence rather than literal list/set
equality.

Per this repo's own "Primitive-level property tests" convention
(AGENTS.md): a reusable normalization/comparison primitive gets its
contract pinned directly, with synthetic inputs, decoupled from any one
caller's real-compiler scenario -- not only exercised incidentally
through the integration test that happens to trigger it. Two real Codex
review rounds on the integration test each found a case a plain `set()`
comparison would have silently accepted as "the same" when it was not;
both are pinned here as direct, fast regression cases so a future change
to the normalization rules cannot reintroduce either without this file
failing.
"""

from __future__ import annotations

from _dump_compile_args_normalize import split_compile_args


class TestLastWinsMacroAndDialectFlags:
    def test_conflicting_macro_value_in_different_order_is_not_equal(self) -> None:
        """The case the first `set()`-based comparison would have missed:
        two paths defining the same macro with *different* values, in an
        order that makes the *effective* (last-wins) value differ -- a
        real compiler sees FOO=2 in one case, FOO=1 in the other, so these
        must not compare equal."""
        a = split_compile_args(("-DFOO=1", "-DFOO=2"))
        b = split_compile_args(("-DFOO=2", "-DFOO=1"))
        assert a[0] != b[0]
        assert a[0]["macro:FOO"] == "-DFOO=2"
        assert b[0]["macro:FOO"] == "-DFOO=1"

    def test_conflicting_std_value_in_different_order_is_not_equal(self) -> None:
        a = split_compile_args(("-std=c++17", "-std=c++20"))
        b = split_compile_args(("-std=c++20", "-std=c++17"))
        assert a[0] != b[0]
        assert a[0]["dialect"] == "-std=c++20"
        assert b[0]["dialect"] == "-std=c++17"

    def test_identical_repeated_value_is_tolerated(self) -> None:
        """The CLI dump path's own separately-tracked, harmless
        duplication (the same -std=/-D value repeated) -- this must still
        compare equal to a single occurrence, since the effective
        compiler state is identical either way."""
        duplicated = split_compile_args(("-std=c++17", "-fPIC", "-std=c++17"))
        single = split_compile_args(("-std=c++17", "-fPIC"))
        assert duplicated[0] == single[0]
        assert duplicated[1] == single[1]

    def test_undef_after_define_is_the_effective_state(self) -> None:
        """`-U` shares the same last-wins namespace as `-D` for a given
        macro name -- a later `-UFOO` genuinely undefines an earlier
        `-DFOO=1`, so the *last* token for that name is what must be
        compared, regardless of which of -D/-U spelled it."""
        state, _presence, _includes = split_compile_args(("-DFOO=1", "-UFOO"))
        assert state["macro:FOO"] == "-UFOO"


class TestIncludeSearchFlagsOrderAndMultiplicity:
    def test_reordered_include_dirs_are_not_equal(self) -> None:
        """The case the *second* `set()`-based comparison (of the whole
        arg list) would have missed: two include-search directories in
        opposite order are not interchangeable, since a real compiler
        resolves a colliding header basename from the first match."""
        a = split_compile_args(("-I", "/a", "-I", "/b"))
        b = split_compile_args(("-I", "/b", "-I", "/a"))
        assert a[2] != b[2]

    def test_duplicated_include_dir_is_not_collapsed(self) -> None:
        a = split_compile_args(("-I", "/a", "-I", "/a"))
        b = split_compile_args(
            (
                "-I",
                "/a",
            )
        )
        assert a[2] != b[2]
        assert a[2] == (("-I", "/a"), ("-I", "/a"))

    def test_distinct_include_flag_kinds_are_kept_separate(self) -> None:
        state = split_compile_args(("-I", "/a", "-isystem", "/a"))[2]
        assert state == (("-I", "/a"), ("-isystem", "/a"))


class TestPresenceOnlyFlags:
    def test_reordered_presence_only_flags_are_equal(self) -> None:
        """A boolean-style flag like -fPIC has no meaningful order or
        repeat count -- unlike the two flag families above, a set
        comparison is the *correct* semantics here."""
        a = split_compile_args(("-fPIC", "-pthread"))
        b = split_compile_args(("-pthread", "-fPIC", "-fPIC"))
        assert a[1] == b[1] == frozenset({"-fPIC", "-pthread"})

    def test_split_is_exhaustive_and_partitioned(self) -> None:
        """Every input token lands in exactly one of the three output
        pieces -- a sanity check on the partition itself, independent of
        any equivalence question."""
        args = ("-DFOO=1", "-std=c++17", "-fPIC", "-I", "/a", "-isystem", "/b")
        last_wins, presence, includes = split_compile_args(args)
        assert set(last_wins.values()) == {"-DFOO=1", "-std=c++17"}
        assert presence == frozenset({"-fPIC"})
        assert includes == (("-I", "/a"), ("-isystem", "/b"))

    def test_empty_input_is_empty_everywhere(self) -> None:
        last_wins, presence, includes = split_compile_args(())
        assert last_wins == {}
        assert presence == frozenset()
        assert includes == ()

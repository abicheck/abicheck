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
through the integration test that happens to trigger it. Four real Codex
review rounds on the integration test each found a case a plain ``set()``
(or an insufficiently-defaulted/insufficiently-unified bucket) comparison
would have silently accepted as "the same" when it was not; all four are
pinned here as direct, fast regression cases so a future change to the
normalization rules cannot reintroduce any of them without this file
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
        assert a[0]["-std="] == "-std=c++20"
        assert b[0]["-std="] == "-std=c++17"

    def test_conflicting_target_value_in_different_order_is_not_equal(self) -> None:
        """A third review round found: `--target=` has the identical
        last-wins shape as `-std=`, and an earlier revision's catch-all
        "everything else is presence-only" default silently treated it as
        order-insensitive instead."""
        a = split_compile_args(("--target=a", "--target=b"))
        b = split_compile_args(("--target=b", "--target=a"))
        assert a[0] != b[0]
        assert a[0]["--target="] == "--target=b"
        assert b[0]["--target="] == "--target=a"

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
        state = split_compile_args(("-DFOO=1", "-UFOO"))[0]
        assert state["macro:FOO"] == "-UFOO"

    def test_separate_and_attached_macro_spelling_normalize_identically(self) -> None:
        """GCC/Clang accept `-D FOO=1` (separate argv token) as well as
        `-DFOO=1` (attached) -- the identical attached-vs-separate
        equivalence the ordered-stream pair flags already need, applied
        proactively here before a real dumper output exercises it, rather
        than waiting for a fifth review round to find it the same way the
        fourth found it for -I."""
        separate = split_compile_args(("-D", "FOO=1"))
        attached = split_compile_args(("-DFOO=1",))
        assert separate[0] == attached[0] == {"macro:FOO": "-DFOO=1"}


class TestOrderedStreamPairFlags:
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
        b = split_compile_args(("-I", "/a"))
        assert a[2] != b[2]
        assert a[2] == ("-I=/a", "-I=/a")

    def test_distinct_include_flag_kinds_are_kept_separate(self) -> None:
        stream = split_compile_args(("-I", "/a", "-isystem", "/a"))[2]
        assert stream == ("-I=/a", "-isystem=/a")

    def test_reordered_forced_includes_are_not_equal(self) -> None:
        """The third review round's other named example: `-include a.h`
        then `-include b.h` is not the same compilation as the reverse --
        an earlier forced include's macros are visible to a later one."""
        a = split_compile_args(("-include", "a.h", "-include", "b.h"))
        b = split_compile_args(("-include", "b.h", "-include", "a.h"))
        assert a[2] != b[2]

    def test_attached_and_separate_spellings_normalize_identically(self) -> None:
        """A fourth review round's case: `-I/a` (attached, one argv
        token) and `-I`, `/a` (separate, two argv tokens) name the
        identical compiler behavior and must compare equal."""
        attached = split_compile_args(("-I/a",))
        separate = split_compile_args(("-I", "/a"))
        assert attached[2] == separate[2] == ("-I=/a",)

    def test_attached_form_keeps_its_real_relative_order(self) -> None:
        """The fourth round's actual reported bug: before this fix, an
        attached-form pair and a separate-form pair landed in two
        independently-compared buckets, so their relative order to each
        other was invisible -- these two argv sequences search a
        genuinely different directory *first* and must not compare
        equal."""
        a = split_compile_args(("-I/a", "-I", "/b"))
        b = split_compile_args(("-I", "/b", "-I/a"))
        assert a[2] != b[2]
        assert a[2] == ("-I=/a", "-I=/b")
        assert b[2] == ("-I=/b", "-I=/a")


class TestPresenceOnlyFlags:
    def test_reordered_presence_only_flags_are_equal(self) -> None:
        """A boolean-style flag like -fPIC has no meaningful order or
        repeat count -- unlike the flag families above, a set comparison
        is the *correct* semantics here, not merely a convenient one."""
        a = split_compile_args(("-fPIC", "-pthread"))
        b = split_compile_args(("-pthread", "-fPIC", "-fPIC"))
        assert a[1] == b[1] == frozenset({"-fPIC", "-pthread"})


class TestUnknownTokensShareTheOrderedStream:
    def test_reordered_unrecognized_flags_are_not_equal(self) -> None:
        """The structural fix behind the third review round: a flag this
        module has no specific rule for is *not* silently folded into the
        presence-only set -- it shares the same strict, order-preserving
        stream as the recognized pair flags (see the fourth round's fix,
        which unified what used to be two separately-compared buckets)."""
        a = split_compile_args(("-Wsomething", "-Wother"))
        b = split_compile_args(("-Wother", "-Wsomething"))
        assert a[2] != b[2]
        assert a[2] == ("-Wsomething", "-Wother")

    def test_duplicated_unrecognized_flag_is_not_collapsed(self) -> None:
        a = split_compile_args(("-Wsomething", "-Wsomething"))
        b = split_compile_args(("-Wsomething",))
        assert a[2] != b[2]

    def test_unknown_token_and_pair_flag_interleave_in_real_order(self) -> None:
        """The fourth round's core fix, stated directly: an unrecognized
        token and a recognized pair flag share one stream, so their
        relative order to each other is part of the comparison too, not
        just the internal order within each category separately."""
        a = split_compile_args(("-Wsomething", "-I", "/a"))
        b = split_compile_args(("-I", "/a", "-Wsomething"))
        assert a[2] != b[2]
        assert a[2] == ("-Wsomething", "-I=/a")
        assert b[2] == ("-I=/a", "-Wsomething")


class TestSplitIsExhaustiveAndPartitioned:
    def test_every_token_lands_in_exactly_one_bucket(self) -> None:
        args = (
            "-DFOO=1",
            "-std=c++17",
            "--target=x86_64",
            "-fPIC",
            "-I",
            "/a",
            "-include",
            "/b",
            "-Wsomething",
        )
        last_wins, presence, stream = split_compile_args(args)
        assert set(last_wins.values()) == {"-DFOO=1", "-std=c++17", "--target=x86_64"}
        assert presence == frozenset({"-fPIC"})
        assert stream == ("-I=/a", "-include=/b", "-Wsomething")

    def test_empty_input_is_empty_everywhere(self) -> None:
        last_wins, presence, stream = split_compile_args(())
        assert last_wins == {}
        assert presence == frozenset()
        assert stream == ()

    def test_trailing_pair_flag_with_no_operand_is_not_dropped(self) -> None:
        """A malformed/truncated arg list (a search or forced-include flag
        as the very last token, with no operand) must not silently
        vanish -- it's recorded verbatim in the ordered stream instead of
        being dropped, so a real truncation still shows up as a
        difference rather than disappearing from both sides identically
        by accident."""
        _last_wins, _presence, stream = split_compile_args(("-I",))
        assert stream == ("-I",)

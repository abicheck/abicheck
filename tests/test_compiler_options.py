# SPDX-License-Identifier: Apache-2.0
"""Tests for forwarded compiler dialect option predicates."""

from abicheck._compiler_options import (
    explicit_language_standard,
    has_explicit_cpp_std,
    has_explicit_std,
    language_standard_field,
)


def test_has_explicit_std_accepts_string_and_tokens() -> None:
    assert has_explicit_std("-O2 -std=c17")
    assert has_explicit_std(None, ("/std:c++20",))
    assert not has_explicit_std("-O2", ("-Wall",))


def test_has_explicit_cpp_std_distinguishes_c_and_cpp() -> None:
    assert has_explicit_cpp_std("-O2 -std=gnu++17")
    assert has_explicit_cpp_std(None, ("/std:c++latest",))
    assert not has_explicit_cpp_std("-std=c17")
    assert not has_explicit_cpp_std(None, ("-Wall",))


def test_has_explicit_cpp_std_accepts_long_dash_spelling() -> None:
    """GCC/Clang accept --std=c++17 as a GNU long-option alias for -std=c++17."""
    assert has_explicit_cpp_std(None, ("--std=c++17",))
    assert has_explicit_cpp_std("--std=gnu++20")
    assert not has_explicit_cpp_std(None, ("--std=c17",))


class TestExplicitLanguageStandard:
    """ADR-050 D1's language_standard profile field (Codex review, PR #624
    follow-up): two dumps differing only by -std= must not silently share a
    profile_fingerprint."""

    def test_none_when_nothing_forwarded(self) -> None:
        assert explicit_language_standard(None, ()) is None
        assert explicit_language_standard("-O2", ("-Wall",)) is None

    def test_extracts_value_from_gcc_options_string(self) -> None:
        assert explicit_language_standard("-O2 -std=c++17", ()) == "c++17"

    def test_extracts_value_from_gcc_option_tokens(self) -> None:
        assert explicit_language_standard(None, ("-std=c++20",)) == "c++20"

    def test_last_occurrence_wins_across_both_sources(self) -> None:
        # gcc_option_tokens are appended after gcc_options is shlex-split
        # (dumper.py's own _flag_tokens ordering), so a later token wins --
        # matching real compiler flag precedence.
        assert explicit_language_standard("-std=c++17", ("-std=c++20",)) == "c++20"

    def test_accepts_long_dash_spelling(self) -> None:
        assert explicit_language_standard("--std=c++17", ()) == "c++17"

    def test_accepts_msvc_slash_spelling(self) -> None:
        assert explicit_language_standard("/std:c++20", ()) == "c++20"

    def test_distinct_standards_produce_distinct_values(self) -> None:
        """Pins the exact scenario the reviewer flagged: two dumps that
        differ only by -std= must not fingerprint identically."""
        assert explicit_language_standard(
            "-std=c++17", ()
        ) != explicit_language_standard("-std=c++20", ())


class TestLanguageStandardField:
    """ADR-050 D1's combined language_standard profile field (Codex review,
    PR #624 follow-up): --lang alone (no explicit -std=) must still
    distinguish two extraction profiles."""

    def test_none_when_neither_lang_nor_std_given(self) -> None:
        assert language_standard_field(None, None, ()) is None

    def test_lang_alone_used_when_no_explicit_std(self) -> None:
        assert language_standard_field("c", None, ()) == "c"
        assert language_standard_field("c++", None, ()) == "c++"

    def test_lang_is_case_and_whitespace_normalized(self) -> None:
        assert language_standard_field("C++", None, ()) == "c++"
        assert language_standard_field(" c ", None, ()) == "c"

    def test_explicit_std_alone_matches_bare_helper(self) -> None:
        # No lang given -- output must exactly match explicit_language_standard
        # (backward compatible with the pre-existing language_standard tests).
        assert language_standard_field(None, "-std=gnu11", ()) == "gnu11"

    def test_lang_and_explicit_std_both_present_are_combined(self) -> None:
        assert language_standard_field("c++", "-std=gnu++20", ()) == "c++:gnu++20"

    def test_lang_alone_distinguishes_two_profiles(self) -> None:
        """Pins the exact scenario the reviewer flagged: the same executable,
        no explicit -std=, differing only by --lang c vs --lang c++, must not
        collapse to the same language_standard value."""
        assert language_standard_field("c", None, ()) != language_standard_field(
            "c++", None, ()
        )

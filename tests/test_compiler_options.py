# SPDX-License-Identifier: Apache-2.0
"""Tests for forwarded compiler dialect option predicates."""

import pytest

from abicheck._compiler_options import (
    explicit_language_standard,
    has_explicit_cpp_std,
    has_explicit_std,
    language_standard_field,
    split_gcc_options,
)


class TestSplitGccOptions:
    """Regression coverage for the Windows quoted-value CI failure this
    helper was added to fix (Codex review, PR #774) and the three rounds
    of review findings against earlier revisions -- each pinned input below
    caught a real regression in some prior revision (see the function's own
    docstring for the full history): a hand-rolled ``shlex.shlex`` with
    ``escape=""`` broke both real POSIX escape sequences and comment-
    character handling; reverting to plain ``shlex.split(text, posix=True)``
    fixed those but then corrupted an ordinary unquoted Windows path's
    backslashes. The final hand-rolled tokenizer satisfies every one of
    these simultaneously."""

    def test_quoted_value_with_embedded_space_stays_one_token(self) -> None:
        # The original CI failure: shlex.split(..., posix=False) (the old
        # Windows-only behavior) split this into three malformed tokens
        # instead of two.
        assert split_gcc_options('-DMSG="hello world" -DOK=1') == [
            "-DMSG=hello world",
            "-DOK=1",
        ]

    def test_backslash_escaped_space_is_honored(self) -> None:
        # Codex review (P1): an escape=""-disabled lexer left a real
        # backslash-escaped space as two separate tokens instead of one.
        assert split_gcc_options(r"-DMSG=hello\ world") == ["-DMSG=hello world"]

    def test_backslash_escaped_quote_is_honored(self) -> None:
        assert split_gcc_options(r"-DVERSION=\"1.2\"") == ['-DVERSION="1.2"']

    def test_hash_character_is_not_treated_as_a_comment(self) -> None:
        # Codex review (P2): an escape=""-disabled shlex.shlex left the
        # default #-starts-a-comment behavior active, silently truncating
        # this token and dropping -DOK=1 entirely.
        assert split_gcc_options("-I/build/#generated -DOK=1") == [
            "-I/build/#generated",
            "-DOK=1",
        ]

    def test_malformed_quoting_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            split_gcc_options('-DMSG="unterminated')

    def test_unquoted_windows_path_backslashes_survive(self) -> None:
        # Codex review, third round: plain shlex.split(text, posix=True)
        # treats every unquoted backslash as an escape character, silently
        # corrupting an ordinary Windows include path with no quotes and no
        # escape intent at all -- the single most common real shape this
        # flag carries on Windows.
        assert split_gcc_options(r"-IC:\mypath\include -DFOO=bar") == [
            r"-IC:\mypath\include",
            "-DFOO=bar",
        ]

    def test_quoted_windows_path_still_uses_real_posix_escaping(self) -> None:
        # A quoted value is a deliberate opt-in to POSIX quoting, so
        # backslash-escaping inside quotes follows real shlex rules
        # unconditionally, unlike the unquoted case above.
        assert split_gcc_options(r'-DPATH="C:\a\b"') == [r"-DPATH=C:\a\b"]

    def test_single_quoted_value_is_fully_literal(self) -> None:
        assert split_gcc_options(r"-DMSG='a \ b \" c'") == [r"-DMSG=a \ b \" c"]


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

    def test_malformed_gcc_options_does_not_raise(self) -> None:
        # An unbalanced quote makes shlex.split raise ValueError -- this is
        # the ADR-050 profile-fingerprint path, invoked unconditionally on
        # every header-based dump, so a malformed --gcc-options value must
        # degrade gracefully (no explicit standard detected from the
        # unparseable string) rather than abort the dump, matching the same
        # "must not abort the dump" rule already applied to
        # dumper_contract.py's own shlex.split call.
        assert explicit_language_standard('-DFOO="unterminated', ()) is None
        assert (
            explicit_language_standard('-DFOO="unterminated', ("-std=c++20",))
            == "c++20"
        )


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

    def test_resolved_standard_used_when_no_explicit_std(self) -> None:
        # P0 evidence-provider audit: this is the exact gap the pre-existing
        # docstring flagged as "deferred as a narrower follow-up" -- a
        # heuristic-forced standard (gnu++20 from the requires/concept
        # detector) with no explicit -std= must still be captured.
        assert (
            language_standard_field(None, None, (), resolved_standard="gnu++20")
            == "gnu++20"
        )

    def test_resolved_standard_distinguishes_profiles_with_no_explicit_std(
        self,
    ) -> None:
        """Two dumps of the same --lang, no explicit -std=, whose headers
        triggered the C++20 heuristic on only one side must fingerprint
        differently -- previously both silently resolved to None/lang-only
        and were indistinguishable."""
        assert language_standard_field(
            "c++", None, (), resolved_standard=None
        ) != language_standard_field("c++", None, (), resolved_standard="gnu++20")

    def test_resolved_standard_combined_with_lang(self) -> None:
        assert (
            language_standard_field("c++", None, (), resolved_standard="gnu++20")
            == "c++:gnu++20"
        )

    def test_resolved_standard_takes_priority_over_explicit_std(self) -> None:
        # resolved_standard is the frontend's own final decision -- it wins
        # even when an explicit -std= was also passed (the two should always
        # agree in practice; resolved_standard is authoritative when given).
        assert (
            language_standard_field(
                None, "-std=gnu++11", (), resolved_standard="gnu++20"
            )
            == "gnu++20"
        )

    def test_none_resolved_standard_falls_back_to_explicit(self) -> None:
        # Backward compatible: a caller that hasn't threaded a resolved
        # value through (or a non-header dump) keeps the old behaviour.
        assert (
            language_standard_field(None, "-std=gnu11", (), resolved_standard=None)
            == "gnu11"
        )

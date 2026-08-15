# SPDX-License-Identifier: Apache-2.0
"""Tests for forwarded compiler dialect option predicates."""

import pytest

from abicheck import _compiler_options
from abicheck._compiler_options import (
    _split_gcc_options_windows,
    explicit_language_standard,
    has_explicit_cpp_std,
    has_explicit_std,
    language_standard_field,
    split_gcc_options,
)


class TestSplitGccOptionsDispatch:
    """``split_gcc_options`` itself is a thin platform dispatch (Codex
    review, fourth round -- see the function's own docstring for the full
    four-revision history of why a single grammar can't serve both
    platforms). These tests pin the dispatch, monkeypatching ``os.name``
    so both branches are exercised regardless of which OS actually runs
    the test suite."""

    def test_dispatches_to_windows_tokenizer_when_os_name_is_nt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_compiler_options.os, "name", "nt")
        assert split_gcc_options(
            r"-IC:\mypath\include -DFOO=bar"
        ) == _split_gcc_options_windows(r"-IC:\mypath\include -DFOO=bar")
        # Confirms the branch actually ran (not silently falling through to
        # plain shlex, which would corrupt this input -- see
        # TestSplitGccOptionsPosix.test_unquoted_windows_path_backslashes_are_consumed).
        assert split_gcc_options(r"-IC:\mypath\include") == [r"-IC:\mypath\include"]

    def test_dispatches_to_plain_shlex_on_posix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_compiler_options.os, "name", "posix")
        assert split_gcc_options(r"-DVAR=\$HOME") == ["-DVAR=$HOME"]


class TestSplitGccOptionsWindows:
    """Direct coverage of :func:`_split_gcc_options_windows`, the Windows-
    only tokenizer -- independent of the host OS actually running this
    suite. Each case below caught a real regression in some earlier
    revision of ``split_gcc_options`` back when it applied one grammar to
    every platform (see that function's docstring for the full history):
    a hand-rolled ``shlex.shlex`` with ``escape=""`` broke both real POSIX
    escape sequences and comment-character handling; reverting to plain
    ``shlex.split(text, posix=True)`` fixed those but then corrupted an
    ordinary unquoted Windows path's backslashes. This tokenizer satisfies
    every one of these simultaneously."""

    def test_quoted_value_with_embedded_space_stays_one_token(self) -> None:
        # The original CI failure: shlex.split(..., posix=False) (the old
        # Windows-only behavior) split this into three malformed tokens
        # instead of two.
        assert _split_gcc_options_windows('-DMSG="hello world" -DOK=1') == [
            "-DMSG=hello world",
            "-DOK=1",
        ]

    def test_backslash_before_whitespace_is_not_escaped(self) -> None:
        # Codex review, fifth round: an earlier revision also escaped
        # backslash-before-whitespace (to satisfy real POSIX's
        # -DMSG=hello\ world idiom, even though the separate POSIX branch
        # already handles real POSIX input on its own) -- this created a
        # genuine Windows-specific ambiguity with the far more common shape
        # of a path ending in a trailing directory separator right before
        # the next flag, corrupting it into one token instead of two (see
        # test_trailing_directory_separator_before_next_flag_stays_two_tokens).
        # The backslash and the space are both left untouched here, unlike
        # plain shlex (which would collapse them into one token).
        assert _split_gcc_options_windows(r"-DMSG=hello\ world") == [
            "-DMSG=hello\\",
            "world",
        ]

    def test_trailing_directory_separator_before_next_flag_stays_two_tokens(
        self,
    ) -> None:
        # Codex review, fifth round: the exact regression this class exists
        # to pin -- an unquoted Windows path ending in a trailing directory
        # separator immediately before the next flag must not be merged
        # with it into one corrupted token.
        assert _split_gcc_options_windows(r"-IC:\sdk\ -DOK=1") == [
            "-IC:\\sdk\\",
            "-DOK=1",
        ]

    def test_backslash_escaped_quote_is_honored(self) -> None:
        assert _split_gcc_options_windows(r"-DVERSION=\"1.2\"") == ['-DVERSION="1.2"']

    def test_hash_character_is_not_treated_as_a_comment(self) -> None:
        # Codex review (P2): an escape=""-disabled shlex.shlex left the
        # default #-starts-a-comment behavior active, silently truncating
        # this token and dropping -DOK=1 entirely.
        assert _split_gcc_options_windows("-I/build/#generated -DOK=1") == [
            "-I/build/#generated",
            "-DOK=1",
        ]

    def test_malformed_quoting_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _split_gcc_options_windows('-DMSG="unterminated')

    def test_unquoted_windows_path_backslashes_survive(self) -> None:
        # Codex review, third round: plain shlex.split(text, posix=True)
        # treats every unquoted backslash as an escape character, silently
        # corrupting an ordinary Windows include path with no quotes and no
        # escape intent at all -- the single most common real shape this
        # flag carries on Windows.
        assert _split_gcc_options_windows(r"-IC:\mypath\include -DFOO=bar") == [
            r"-IC:\mypath\include",
            "-DFOO=bar",
        ]

    def test_quoted_windows_path_still_uses_real_posix_escaping(self) -> None:
        # A double-quoted value is a deliberate opt-in to POSIX double-
        # quote semantics, so backslash-escaping inside it follows real
        # shlex rules unconditionally, unlike the unquoted case above.
        assert _split_gcc_options_windows(r'-DPATH="C:\a\b"') == [r"-DPATH=C:\a\b"]

    def test_single_quote_is_never_special(self) -> None:
        # Codex review, sixth round: a bare `'` used to open real POSIX
        # single-quote grouping here, matching plain shlex -- but unlike
        # `"` (illegal in every Windows filename), `'` is an ordinary,
        # legal Windows path/filename character (e.g. a surname like
        # O'Brien) with no shell-special meaning on this platform at all.
        # Treating it as a delimiter either silently dropped real quote
        # characters from a value that needed them (-DCHAR='x') or raised
        # ValueError on an unterminated "quote" that was really just a
        # path containing an apostrophe -- see the two regression tests
        # below. `'` now always falls through to the plain "ordinary
        # character" branch, both inside and outside double quotes.
        assert _split_gcc_options_windows(r"-DMSG='a \ b \" c'") == [
            "-DMSG='a",
            "\\",
            "b",
            '"',
            "c'",
        ]

    def test_apostrophe_in_macro_value_is_preserved(self) -> None:
        # A real, if unusual, C character-constant macro value -- the
        # quotes are semantically part of the value, not POSIX shell
        # quoting to be stripped.
        assert _split_gcc_options_windows("-DCHAR='x'") == ["-DCHAR='x'"]

    def test_quoted_unc_path_prefix_survives(self) -> None:
        # Codex review, seventh round: the double-quote branch previously
        # followed real POSIX double-quote escaping unconditionally, which
        # collapses `\\` -> `\` -- corrupting a quoted UNC path's leading
        # two-backslash prefix into a single, non-UNC backslash the
        # compiler can't resolve.
        assert _split_gcc_options_windows(r'-I"\\server\share path\include"') == [
            r"-I\\server\share path\include"
        ]

    def test_quoted_path_ending_in_trailing_separator_closes_properly(self) -> None:
        # Codex review, eighth round: item #7's separate inside-quotes
        # backslash handling treated the final backslash before a closing
        # quote as always escaping it, regardless of how many backslashes
        # preceded it -- so a quoted path ending in a directory separator
        # (an even, two-backslash run right before the closing quote)
        # consumed the quote as literal instead of closing the quoted
        # region, eventually raising ValueError instead of producing two
        # tokens. The real Windows backslash-run-parity rule (even count ->
        # literal backslashes, quote is a real delimiter) resolves it.
        assert _split_gcc_options_windows('-I"C:\\Program Files\\SDK\\\\" -DOK=1') == [
            "-IC:\\Program Files\\SDK\\",
            "-DOK=1",
        ]

    def test_apostrophe_in_windows_path_does_not_raise(self) -> None:
        # The original bug report shape: a real Windows path/filename
        # containing an apostrophe (a common surname) previously opened
        # unterminated single-quote parsing and raised ValueError,
        # aborting header extraction outright.
        assert _split_gcc_options_windows(r"-IC:\Users\O'Brien\include") == [
            r"-IC:\Users\O'Brien\include"
        ]


class TestSplitGccOptionsPosix:
    """``split_gcc_options`` on POSIX is plain, unmodified
    ``shlex.split(text, posix=True)`` -- identical to every
    ``gcc_options``-consuming call site's historical behavior on
    Linux/macOS. Codex review, fourth round: a revision that applied the
    Windows-only tokenizer everywhere silently changed this real, working
    POSIX behavior (e.g. ``-DVAR=\\$HOME`` keeping its backslash instead of
    resolving to ``-DVAR=$HOME``) even though POSIX was never the platform
    with a bug to fix."""

    def test_quoted_value_with_embedded_space_stays_one_token(self) -> None:
        assert split_gcc_options('-DMSG="hello world" -DOK=1') == [
            "-DMSG=hello world",
            "-DOK=1",
        ]

    def test_hash_character_is_not_treated_as_a_comment(self) -> None:
        # shlex.split's own comments=False default already sets
        # commenters="" -- verifies split_gcc_options doesn't override that.
        assert split_gcc_options("-I/build/#generated -DOK=1") == [
            "-I/build/#generated",
            "-DOK=1",
        ]

    def test_every_backslash_escape_is_honored(self) -> None:
        # Real POSIX shlex semantics: a backslash escapes the character
        # immediately following it, regardless of what that character is.
        assert split_gcc_options(r"-DVAR=\$HOME") == ["-DVAR=$HOME"]
        assert split_gcc_options(r"-DREGEX=a\*b") == ["-DREGEX=a*b"]
        assert split_gcc_options(r"-Ifoo\#bar") == ["-Ifoo#bar"]

    def test_unquoted_windows_path_backslashes_are_consumed(self) -> None:
        # Not a bug on POSIX: this is real shlex.split's own behavior,
        # unchanged from every gcc_options-consuming call site's history on
        # Linux/macOS (the Windows-specific fix lives in
        # _split_gcc_options_windows, dispatched to only when os.name ==
        # "nt" -- see TestSplitGccOptionsDispatch).
        assert split_gcc_options(r"-IC:\mypath\include") == ["-IC:mypathinclude"]

    def test_malformed_quoting_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            split_gcc_options('-DMSG="unterminated')


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

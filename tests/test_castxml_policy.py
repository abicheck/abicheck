# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the CastXML version-gate policy (abicheck/castxml_policy.py).

Pure string/version-parsing logic — no castxml binary needed, runs in the
fast suite.
"""

from __future__ import annotations

from abicheck.castxml_policy import (
    MAX_CASTXML,
    MIN_CASTXML,
    MIN_CASTXML_CLANG_MAJOR,
    REASON_CLANG_MAJOR_BELOW_MINIMUM,
    REASON_VERSION_AT_OR_ABOVE_MAXIMUM,
    REASON_VERSION_BELOW_MINIMUM,
    REASON_VERSION_UNPARSEABLE,
    evaluate_castxml_version,
    parse_castxml_version_output,
)


def _version_text(castxml_version: str, clang_version: str = "18.1.8") -> str:
    return f"castxml version {castxml_version}\nclang version {clang_version}"


class TestParseCastxmlVersionOutput:
    def test_parses_castxml_and_clang_version(self):
        cx, clang = parse_castxml_version_output(_version_text("0.7.0"))
        assert cx == "0.7.0"
        assert clang == (18, 1)

    def test_llvm_spelling_also_matches(self):
        cx, clang = parse_castxml_version_output(
            "castxml version 0.7.0\nLLVM version 18.1.8"
        )
        assert cx == "0.7.0"
        assert clang == (18, 1)

    def test_missing_fields_are_none(self):
        cx, clang = parse_castxml_version_output("not a version string")
        assert cx is None
        assert clang is None

    def test_empty_string(self):
        assert parse_castxml_version_output("") == (None, None)


class TestEvaluateCastxmlVersion:
    def test_supported_build_in_range(self):
        result = evaluate_castxml_version(_version_text("0.7.0"))
        assert result.supported is True
        assert result.reasons == []
        assert result.castxml_version == "0.7.0"

    def test_supported_patch_release_in_range(self):
        result = evaluate_castxml_version(_version_text("0.7.3"))
        assert result.supported is True

    def test_legacy_pypi_version_rejected(self):
        # The legacy PyPI `castxml` distribution's last release.
        result = evaluate_castxml_version(_version_text("0.4.5", "8.0.0"))
        assert result.supported is False
        assert REASON_VERSION_BELOW_MINIMUM in result.reasons
        assert REASON_CLANG_MAJOR_BELOW_MINIMUM in result.reasons

    def test_version_at_or_above_max_rejected(self):
        result = evaluate_castxml_version(_version_text("0.8.0"))
        assert result.supported is False
        assert REASON_VERSION_AT_OR_ABOVE_MAXIMUM in result.reasons

    def test_version_below_min_rejected(self):
        result = evaluate_castxml_version(_version_text("0.6.20260105"))
        assert result.supported is False
        assert REASON_VERSION_BELOW_MINIMUM in result.reasons

    def test_bundled_clang_too_old_rejected_even_if_castxml_version_ok(self):
        result = evaluate_castxml_version(_version_text("0.7.0", "17.0.6"))
        assert result.supported is False
        assert REASON_CLANG_MAJOR_BELOW_MINIMUM in result.reasons
        assert REASON_VERSION_BELOW_MINIMUM not in result.reasons

    def test_git_suffixed_below_min_rejected_on_version_not_unparseable(self):
        """Regression (Codex review): the CastXML Superbuild's own release
        format is ``<version>-g<hash>`` (a bare ``-``, e.g. the pinned
        action/install-castxml.sh build ``0.6.20260105-g9864b1e``) — PEP 440
        only accepts that kind of suffix after a ``+`` separator, so a naive
        ``Version(...)`` call always raised ``InvalidVersion`` regardless of
        whether the numeric release was actually in range. Must be rejected
        for being below the floor, not for being unparseable."""
        result = evaluate_castxml_version(_version_text("0.6.20260105-g9864b1e"))
        assert result.supported is False
        assert REASON_VERSION_BELOW_MINIMUM in result.reasons
        assert REASON_VERSION_UNPARSEABLE not in result.reasons

    def test_git_suffixed_in_range_accepted(self):
        """A future Superbuild release in the supported range must be
        accepted even with its git-describe suffix intact — this is what
        lets action/install-castxml.sh's pin be bumped to a real >=0.7.0
        Superbuild build without also needing a parser change."""
        result = evaluate_castxml_version(_version_text("0.7.0-g9864b1e"))
        assert result.supported is True
        assert result.reasons == []

    def test_git_describe_style_suffix_with_commit_count_accepted(self):
        """The fuller git-describe form (``<tag>-<n>-g<hash>``) must also
        parse — only the *last* hyphen is converted to the PEP 440
        local-version separator; everything before it is left intact."""
        result = evaluate_castxml_version(_version_text("0.7.0-12-g9864b1e"))
        assert result.supported is True

    def test_rc_prerelease_with_git_suffix_stays_below_final_release(self):
        """Regression (Codex review): the documented CastXML version format
        allows an optional ``-rc<n>`` pre-release id before the git suffix
        (e.g. ``0.7.0-rc1-gabc``). Converting the *first* hyphen to ``+``
        (an earlier version of this fallback) folded the "-rc1" marker into
        the opaque local-version string, erasing its pre-release meaning and
        making the build compare as >= the final 0.7.0 release it precedes
        — a release candidate must stay below the floor, not be accepted as
        equivalent to (or newer than) the final release."""
        result = evaluate_castxml_version(_version_text("0.7.0-rc1-gabc"))
        assert result.supported is False
        assert REASON_VERSION_BELOW_MINIMUM in result.reasons
        assert REASON_VERSION_UNPARSEABLE not in result.reasons

    def test_unparseable_version_rejected(self):
        result = evaluate_castxml_version("garbage output, no version here")
        assert result.supported is False
        assert REASON_VERSION_UNPARSEABLE in result.reasons

    def test_empty_output_rejected(self):
        result = evaluate_castxml_version("")
        assert result.supported is False
        assert REASON_VERSION_UNPARSEABLE in result.reasons


class TestCastxmlVersionCheckMessage:
    def test_message_includes_range_and_found_path(self):
        result = evaluate_castxml_version(_version_text("0.4.5", "8.0.0"))
        msg = result.message(found_at="/usr/bin/castxml")
        assert "0.4.5" in msg
        assert "/usr/bin/castxml" in msg
        assert MIN_CASTXML in msg
        assert MAX_CASTXML in msg
        assert "not a supported default scanner setup" in msg

    def test_message_without_found_at(self):
        result = evaluate_castxml_version("garbage")
        msg = result.message()
        assert "CastXML of unknown version." in msg


def test_min_castxml_clang_major_matches_dumper_probe_constant():
    # castxml_policy is the new canonical gate; dumper_castxml_probe's
    # advisory-note floor must stay in sync with it (both express the same
    # "glibc sized-float / __assume__" requirement).
    from abicheck.dumper_castxml_probe import _RECOMMENDED_CLANG_MAJOR

    assert MIN_CASTXML_CLANG_MAJOR == _RECOMMENDED_CLANG_MAJOR

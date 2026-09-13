"""Tests for version-stamped typedef FP suppression.

libpng and similar libraries define typedefs whose names encode the library
version, e.g.::

    typedef char* png_libpng_version_1_6_46;

These are compile-time sentinels — their name changes every release by design
and they are never exported as ELF symbols.  abicheck must NOT report them as
BREAKING when they change between versions.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, _is_version_stamped_typedef, compare
from abicheck.model import AbiSnapshot


def _snap(typedefs: dict[str, str] | None = None, version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        typedefs=typedefs or {},
    )


# ── Unit tests for the pattern matcher ────────────────────────────────────────


class TestVersionStampedTypedefPattern:
    """Unit tests for _is_version_stamped_typedef."""

    @pytest.mark.parametrize(
        "name",
        [
            "png_libpng_version_1_6_46",
            "png_libpng_version_1_6_47",
            "mylib_version_2_0_0",
            "lib_version_10_3_1",
            "mylib_version_1_0_0",
            "MYLIB_VERSION_1_0_0",  # uppercase (re.IGNORECASE)
            "foo_version_12_34_56",
        ],
    )
    def test_matches_version_stamped(self, name: str) -> None:
        assert _is_version_stamped_typedef(name), f"Expected {name!r} to match"

    @pytest.mark.parametrize(
        "name",
        [
            "handler_t",
            "callback_t",
            "png_voidp",
            "size_t",
            "my_version",  # no _\d+_\d+_\d+ suffix
            "version_1_2",  # only two parts (not three)
            "version_1_2_",  # trailing underscore (not digit)
            "_version_1_2_3_extra",  # trailing non-numeric content after triple
        ],
    )
    def test_rejects_non_version_stamped(self, name: str) -> None:
        assert not _is_version_stamped_typedef(name), f"Expected {name!r} NOT to match"


# ── Integration tests: checker verdict ────────────────────────────────────────


class TestVersionStampedTypedefInChecker:
    """Version-stamped typedef removal must NOT produce BREAKING verdict."""

    def test_libpng_style_typedef_not_breaking(self) -> None:
        """png_libpng_version_1_6_46 removal → TYPEDEF_VERSION_SENTINEL, not TYPEDEF_REMOVED."""
        old = _snap({"png_libpng_version_1_6_46": "char*"}, version="1.6.46")
        new = _snap({"png_libpng_version_1_6_47": "char*"}, version="1.6.47")
        result = compare(old, new)
        # The old version sentinel was removed — should be TYPEDEF_VERSION_SENTINEL
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPEDEF_VERSION_SENTINEL in kinds, (
            f"Expected TYPEDEF_VERSION_SENTINEL in {kinds}"
        )
        # Must NOT be TYPEDEF_REMOVED (which is BREAKING)
        assert ChangeKind.TYPEDEF_REMOVED not in kinds
        # Overall verdict must NOT be BREAKING
        assert result.verdict != Verdict.BREAKING, (
            f"Expected non-BREAKING verdict, got {result.verdict}"
        )

    def test_version_sentinel_verdict_is_compatible(self) -> None:
        """Version-stamped typedef change produces COMPATIBLE (or COMPATIBLE_WITH_RISK) verdict."""
        old = _snap({"png_libpng_version_1_6_46": "char*"}, version="1.6.46")
        new = _snap({"png_libpng_version_1_6_47": "char*"}, version="1.6.47")
        result = compare(old, new)
        assert result.verdict in (Verdict.COMPATIBLE, Verdict.NO_CHANGE), (
            f"Expected COMPATIBLE or NO_CHANGE, got {result.verdict}"
        )

    def test_version_sentinel_new_typedef_added(self) -> None:
        """New version typedef is added as TYPE_ADDED (compatible) in typedefs."""
        old = _snap({"png_libpng_version_1_6_46": "char*"}, version="1.6.46")
        new = _snap({"png_libpng_version_1_6_47": "char*"}, version="1.6.47")
        result = compare(old, new)
        # The removal side is TYPEDEF_VERSION_SENTINEL (compatible)
        # The addition side may produce a TYPEDEF_ADDED change — but that's also compatible
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPEDEF_REMOVED not in kinds

    def test_regular_typedef_removal_still_breaking(self) -> None:
        """Normal typedef removal is still BREAKING (guard against over-filtering)."""
        old = _snap({"handler_t": "int(*)(int)"}, version="1.0")
        new = _snap({}, version="2.0")
        result = compare(old, new)
        assert ChangeKind.TYPEDEF_REMOVED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING

    def test_regular_typedef_removal_unchanged_by_fix(self) -> None:
        """Non-version-stamped typedefs continue to be reported as BREAKING."""
        old = _snap({"callback_t": "void(*)(void*)"}, version="1.0")
        new = _snap({}, version="2.0")
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING

    def test_multiple_version_sentinels_all_downgraded(self) -> None:
        """Multiple version-stamped typedefs removed at once — all are COMPATIBLE."""
        old = _snap(
            {
                "libfoo_version_1_0_0": "int",
                "libfoo_version_1_0_1": "int",  # hypothetical extra sentinel
            },
            version="1.0.0",
        )
        new = _snap(
            {
                "libfoo_version_1_1_0": "int",
            },
            version="1.1.0",
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPEDEF_REMOVED not in kinds
        assert result.verdict != Verdict.BREAKING
        # Both removed sentinels must be downgraded — not just one
        sentinel_changes = [
            c for c in result.changes if c.kind == ChangeKind.TYPEDEF_VERSION_SENTINEL
        ]
        assert len(sentinel_changes) == 2, (
            f"Expected 2 TYPEDEF_VERSION_SENTINEL changes, got {len(sentinel_changes)}"
        )

    def test_version_sentinel_mixed_with_real_break(self) -> None:
        """Version sentinel + a real break → overall still BREAKING."""
        old = _snap(
            {
                "png_libpng_version_1_6_46": "char*",
                "real_typedef": "int",
            },
            version="1.6.46",
        )
        new = _snap(
            {
                "png_libpng_version_1_6_47": "char*",
                # real_typedef removed — this IS a real break
            },
            version="1.6.47",
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        # Sentinel should not be TYPEDEF_REMOVED
        assert ChangeKind.TYPEDEF_VERSION_SENTINEL in kinds
        # The real typedef removal should still be BREAKING
        assert ChangeKind.TYPEDEF_REMOVED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_sentinel_without_successor_is_breaking(self) -> None:
        """Version-stamped typedef removed with NO successor → still TYPEDEF_REMOVED."""
        old = _snap({"mylib_version_2_5_3": "unsigned int"}, version="2.5.3")
        new = _snap({}, version="3.0.0")  # no successor at all
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        # No successor → cannot confirm it's a sentinel rotation → report as BREAKING
        assert ChangeKind.TYPEDEF_REMOVED in kinds
        assert ChangeKind.TYPEDEF_VERSION_SENTINEL not in kinds

    def test_empty_prefix_sentinel_not_downgraded(self) -> None:
        """Typedef named _version_1_0_0 (empty family prefix) is not downgraded."""
        # Empty prefix would match ANY version-stamped successor — too broad, reject.
        old = _snap({"_version_1_0_0": "int"}, version="1.0.0")
        new = _snap({"unrelated_version_2_0_0": "int"}, version="2.0.0")
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPEDEF_REMOVED in kinds
        assert ChangeKind.TYPEDEF_VERSION_SENTINEL not in kinds

    def test_version_sentinel_change_description(self) -> None:
        """TYPEDEF_VERSION_SENTINEL change has informative description."""
        old = _snap({"mylib_version_2_5_3": "unsigned int"}, version="2.5.3")
        # Successor must be present so same-family check passes
        new = _snap({"mylib_version_2_6_0": "unsigned int"}, version="2.6.0")
        result = compare(old, new)
        sentinel_changes = [
            c for c in result.changes if c.kind == ChangeKind.TYPEDEF_VERSION_SENTINEL
        ]
        assert len(sentinel_changes) == 1
        c = sentinel_changes[0]
        assert c.symbol == "mylib_version_2_5_3"
        assert "sentinel" in c.description.lower() or "version" in c.description.lower()
        assert c.old_value == "unsigned int"

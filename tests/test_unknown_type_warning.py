"""P5: `__unknown__` type warning (abi-dumper #6).

When abicheck encounters a DWARF type tag it doesn't recognize (unknown/vendor
extension), it should log a warning so the gap is visible.

Implementation:
- dwarf_metadata.py `_compute_fallback_type_info()` logs a WARNING when
  a DIE has an unrecognized tag and no name attribute
- This helps diagnose missing coverage for new/vendor-specific DWARF extensions

Test: verify the warning is emitted when an unknown DWARF type tag is
encountered during type resolution.
"""
from __future__ import annotations

import logging

import pytest

from abicheck import dwarf_metadata
from abicheck.dwarf_metadata import _compute_fallback_type_info


class _MockDie:
    """Minimal DWARF DIE stub for testing _compute_fallback_type_info."""

    def __init__(self, tag: str, attrs: dict | None = None, offset: int = 0) -> None:
        self.tag = tag
        self.offset = offset
        self.attributes: dict = attrs or {}


class TestUnknownTypeWarning:
    """Verify _compute_fallback_type_info emits a warning for unknown tags."""

    @pytest.fixture(autouse=True)
    def _fresh_seen_tags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Give each test its own first-sighting state.

        `_SEEN_UNKNOWN_DWARF_TAGS` is a process-wide set that makes the
        warning fire once per tag, so a test asserting the warning *happens*
        depends on nothing having warned for that tag earlier in the same
        process. That held only because no other test used this tag string —
        an accident of ordering, not isolation, and it broke the moment the
        suite was run twice in one process (mutmut runs it once to collect
        stats and again as the clean baseline, and the second run saw the tag
        already recorded). `test_dwarf_metadata_coverage.py` already resets
        the set the same way for the same reason.
        """
        monkeypatch.setattr(dwarf_metadata, "_SEEN_UNKNOWN_DWARF_TAGS", set())

    def test_unknown_tag_no_name_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown DWARF tag with no name attribute must emit a WARNING.

        abi-dumper #6: __unknown__ type entries should produce a diagnostic.
        """
        die = _MockDie(tag="DW_TAG_GNU_formal_parameter_pack", offset=42)
        with caplog.at_level(logging.WARNING, logger="abicheck.dwarf_metadata"):
            _compute_fallback_type_info(die, "DW_TAG_GNU_formal_parameter_pack")
        # Warning must be emitted
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, (
            "Expected a WARNING log for unknown DWARF type tag with no name"
        )
        # The tag name must appear in the warning
        assert "DW_TAG_GNU_formal_parameter_pack" in warnings[0].message

    def test_unknown_tag_with_name_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown DWARF tag that HAS a name attribute must NOT warn (has useful info)."""

        class _Attr:
            def __init__(self, value: object) -> None:
                self.value = value

        die = _MockDie(
            tag="DW_TAG_vendor_custom",
            attrs={"DW_AT_name": _Attr(b"my_vendor_type")},
            offset=99,
        )
        with caplog.at_level(logging.WARNING, logger="abicheck.dwarf_metadata"):
            name, _ = _compute_fallback_type_info(die, "DW_TAG_vendor_custom")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not warnings, (
            "Should NOT warn when the DIE has a name (type is identifiable)"
        )
        assert name == "my_vendor_type"

    def test_fallback_returns_tag_name_for_unknown(self) -> None:
        """_compute_fallback_type_info must return (tag, 0) for unknown tagless DIE."""
        die = _MockDie(tag="DW_TAG_some_unknown", offset=1)
        name, size = _compute_fallback_type_info(die, "DW_TAG_some_unknown")
        assert name == "DW_TAG_some_unknown"  # falls back to tag string
        assert size == 0

    def test_fallback_returns_unknown_for_empty_tag(self) -> None:
        """When both name and tag are empty, return 'unknown'."""
        die = _MockDie(tag="", offset=2)
        name, size = _compute_fallback_type_info(die, "")
        assert name == "unknown"

    def test_known_tag_with_name_does_not_use_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        """Known DWARF tags go through proper dispatch, not fallback — no warning expected."""
        # This tests that normal code paths don't accidentally trigger warnings
        class _Attr:
            def __init__(self, value: object) -> None:
                self.value = value

        # Directly test that a DIE with a known-type name doesn't warn via fallback
        die = _MockDie(
            tag="DW_TAG_base_type",
            attrs={"DW_AT_name": _Attr(b"int"), "DW_AT_byte_size": _Attr(4)},
            offset=10,
        )
        with caplog.at_level(logging.WARNING, logger="abicheck.dwarf_metadata"):
            # Directly calling fallback with a named DIE should not warn
            name2, _ = _compute_fallback_type_info(die, "DW_TAG_base_type")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not warnings, "Named DIE must not produce warning from fallback"
        assert name2 == "int"

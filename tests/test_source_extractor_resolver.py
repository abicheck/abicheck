# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""Unit tests for the capability-aware source-ABI extractor resolver (ADR-030 D3)."""
from __future__ import annotations

from abicheck.buildsource.source_extractors import (
    ALL_CAPABILITIES,
    PROFILES,
    resolve_source_extractor,
)


def _avail(*names: str):
    """Build an availability probe where only *names* are present."""
    present = set(names)
    return lambda n: n in present


class TestAutoSelection:
    def test_auto_prefers_clang_when_available(self):
        c = resolve_source_extractor("auto", available=_avail("clang", "castxml"))
        assert c.selected == "clang"
        assert not c.fell_back
        assert c.capability_gaps == ()  # clang is fully capable

    def test_auto_picks_castxml_when_clang_absent(self):
        c = resolve_source_extractor("auto", available=_avail("castxml"))
        assert c.selected == "castxml"
        assert any(n == "clang" for n, _ in c.skipped)
        # castxml's documented blind spots must be reported, not silent.
        assert "inline_bodies" in c.capability_gaps
        assert "concepts" in c.capability_gaps
        assert "constructor_mangling" in c.capability_gaps

    def test_auto_none_when_nothing_available(self):
        c = resolve_source_extractor("auto", available=_avail())
        assert c.selected is None
        assert c.capability_gaps == ALL_CAPABILITIES
        assert "disabled" in c.gap_note()


class TestExplicitWithFallback:
    def test_clang_falls_back_to_castxml(self):
        c = resolve_source_extractor("clang", available=_avail("castxml"))
        assert c.selected == "castxml"
        assert c.fell_back
        assert "fell back" in c.reason

    def test_clang_no_fallback_yields_none(self):
        c = resolve_source_extractor("clang", available=_avail("castxml"), fallback=False)
        assert c.selected is None

    def test_castxml_does_not_upgrade_to_clang(self):
        # An explicit castxml request must stay castxml even when clang exists.
        c = resolve_source_extractor("castxml", available=_avail("clang", "castxml"))
        assert c.selected == "castxml"
        assert not c.fell_back

    def test_absent_castxml_does_not_fall_back_to_clang(self):
        # Fallback only degrades to *less* capable backends: castxml must never
        # silently upgrade to clang (that would hide a missing castxml dep).
        c = resolve_source_extractor("castxml", available=_avail("clang"))
        assert c.selected is None
        assert any(n == "castxml" for n, _ in c.skipped)

    def test_explicit_available_request_is_not_fallback(self):
        c = resolve_source_extractor("clang", available=_avail("clang"))
        assert c.selected == "clang"
        assert not c.fell_back
        assert c.reason == "clang (requested)"


class TestAndroid:
    def test_android_explicit_only(self):
        c = resolve_source_extractor("android", available=_avail("android"))
        assert c.selected == "android"

    def test_android_never_auto_selected(self):
        c = resolve_source_extractor("auto", available=_avail("android"))
        assert c.selected is None  # android is not in the auto preference chain


class TestProfilesContract:
    def test_clang_superset_of_castxml(self):
        assert PROFILES["castxml"].capabilities < PROFILES["clang"].capabilities

    def test_rank_orders_clang_above_castxml(self):
        assert PROFILES["clang"].rank > PROFILES["castxml"].rank > PROFILES["android"].rank

    def test_no_probe_assumes_available(self):
        c = resolve_source_extractor("clang")
        assert c.selected == "clang"


class TestGapNote:
    def test_full_capability_backend_reports_no_gap(self):
        c = resolve_source_extractor("clang", available=_avail("clang"))
        note = c.gap_note()
        assert "full source-ABI capability" in note
        assert c.selected == "clang"

    def test_partial_backend_lists_its_blind_spots(self):
        c = resolve_source_extractor("castxml", available=_avail("castxml"))
        note = c.gap_note()
        assert "cannot observe" in note
        # castxml's documented blind spots must appear in the note verbatim.
        assert "inline_bodies" in note
        assert "concepts" in note

    def test_no_backend_note_says_disabled(self):
        c = resolve_source_extractor("auto", available=_avail())
        assert "disabled" in c.gap_note()


class TestAvailabilityProbe:
    def test_probe_that_raises_is_treated_as_available(self):
        # A flaky/raising availability probe must not disable a backend; the
        # resolver defaults to "assumed available" and lets extract() degrade.
        def boom(_name):
            raise RuntimeError("probe blew up")

        c = resolve_source_extractor("clang", available=boom)
        assert c.selected == "clang"


class TestUnavailableAndUnknown:
    def test_android_requested_but_unavailable_yields_none(self):
        c = resolve_source_extractor("android", available=_avail())
        assert c.selected is None
        assert any(n == "android" for n, _ in c.skipped)
        assert "android" in c.reason

    def test_unknown_backend_name_degrades_to_auto(self):
        # An unrecognised backend name is treated as auto (best available), and
        # the request label is recorded in the no-usable-backend reason.
        c = resolve_source_extractor("nonsense", available=_avail("castxml"))
        assert c.selected == "castxml"

    def test_unknown_backend_name_with_nothing_available(self):
        # Unknown names route through the auto chain, so with nothing available
        # the result is None and the reason reflects the (auto) lead.
        c = resolve_source_extractor("nonsense", available=_avail())
        assert c.selected is None
        assert "no usable source-ABI backend" in c.reason

# Copyright (c) 2026 abicheck contributors
# SPDX-License-Identifier: Apache-2.0
"""The bundle-composition v1 -> v2 migration (ADR-065 D8) refuses a
present-but-malformed ``degraded_members`` value instead of silently
replacing it with ``{}`` (Codex review, twenty-ninth round).

A hand-edited v1 ``ProjectSnapshot`` composition carrying
``"degraded_members": null`` (or any falsey non-mapping) used to pass the
truthiness check, migrate to an empty marker, and let the package read as
undegraded -- ``validated_degraded_members`` never saw the value. The
invariant over the whole input domain: only an *absent* key or a genuinely
*empty mapping* is the v1 shape; every other present value is refused with
``ValueError`` (a non-mapping for its type, a non-empty mapping for
requiring v2), and the JSON/archive readers agree.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.storage.dto import (
    BUNDLE_COMPOSITION_SECTION_KIND,
    SectionDTO,
    bundle_composition_from_dto,
)

_BASE = {
    "variant_fingerprint": "x",
    "manifest": None,
    "filesystem_aliases": {},
    "library_filenames": {},
}


def _v1(payload: dict[str, object]) -> SectionDTO:
    return SectionDTO(
        section_kind=BUNDLE_COMPOSITION_SECTION_KIND,
        section_schema_version=1,
        payload=payload,
    )


class TestV1MigrationRefusesMalformedMarkers:
    def test_absent_key_migrates_to_the_empty_marker(self) -> None:
        assert bundle_composition_from_dto(_v1(dict(_BASE)))["degraded_members"] == {}

    def test_empty_mapping_migrates_to_the_empty_marker(self) -> None:
        payload = {**_BASE, "degraded_members": {}}
        assert bundle_composition_from_dto(_v1(payload))["degraded_members"] == {}

    @pytest.mark.parametrize(
        "raw", [None, "", 0, False, [], (), "boom", ["a.so"], 1, {"a.so": "why"}]
    )
    def test_present_non_v1_shape_is_refused(self, raw: object) -> None:
        with pytest.raises(ValueError, match="degraded_members"):
            bundle_composition_from_dto(_v1({**_BASE, "degraded_members": raw}))


_JSONISH = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.text(max_size=8), inner, max_size=3)
    ),
    max_leaves=6,
)


class TestMigrationContractProperty:
    """Property: the migration accepts exactly {absent, {}} and refuses
    every other present value -- the oracle is the value's own shape, not
    the migration's truthiness test."""

    @settings(max_examples=150, deadline=None)
    @given(raw=_JSONISH)
    def test_accepts_exactly_the_v1_shapes(self, raw: object) -> None:
        payload = {**_BASE, "degraded_members": raw}
        is_v1_shape = isinstance(raw, dict) and not raw
        if is_v1_shape:
            assert bundle_composition_from_dto(_v1(payload))["degraded_members"] == {}
        else:
            with pytest.raises(ValueError, match="degraded_members"):
                bundle_composition_from_dto(_v1(payload))

    @settings(max_examples=60, deadline=None)
    @given(raw=_JSONISH)
    def test_json_reader_agrees_on_non_mappings(self, raw: object) -> None:
        """The plain-document reader already refuses every non-mapping;
        the migration must not be laxer than it."""
        from abicheck.storage.bundle_facts_validation import validated_degraded_members

        payload = {**_BASE, "degraded_members": raw}
        if isinstance(raw, dict):
            return
        with pytest.raises(ValueError):
            validated_degraded_members(raw)
        with pytest.raises(ValueError):
            bundle_composition_from_dto(_v1(payload))

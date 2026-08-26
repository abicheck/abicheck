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

"""An enum change reported independently by the L2 header-tier detector
(``diff_types._diff_enums``, keyed by ``EnumType.name`` -- deliberately
bare) and the L1 DWARF-tier detector (``diff_platform._diff_enum_layouts``,
keyed by the DWARF dict's own fully-qualified name) must collapse to one
finding, not two, once both tiers observe the same enum.

Two independent pieces make this work, tested separately (primitive level,
per CLAUDE.md's "Primitive-level property tests" guidance) and together
(through ``compare()``):

* ``diff_filtering._enum_canonical_names``/``_canonicalize_enum_symbol``
  bridge the bare-vs-qualified spelling mismatch so
  ``finding_identity.resolve_change_identity`` resolves the same identity
  for both tiers' findings.
* ``diff_filtering._deduplicate_cross_detector``'s own ``_DEDUP_CATEGORIES``
  gate must actually include the four enum kinds -- reaching a matching
  identity is not enough if the dedup step never even attempts to resolve
  one for these kinds at all, which was the deeper, previously-missing
  wiring gap.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_filtering import (
    _canonicalize_enum_symbol,
    _deduplicate_cross_detector,
    _enrich_source_locations,
    _enum_canonical_names,
)
from abicheck.dwarf_metadata import DwarfMetadata, EnumInfo
from abicheck.finding_identity import resolve_change_identity
from abicheck.model import AbiSnapshot, EnumMember, EnumType


def _enum_type(name: str, qualified: str, members: list[tuple[str, int]]) -> EnumType:
    return EnumType(
        name=name,
        qualified_name=qualified,
        members=[EnumMember(name=n, value=v) for n, v in members],
        underlying_type="int",
    )


def _snap(
    version: str,
    members: list[tuple[str, int]],
    *,
    underlying_byte_size: int = 4,
    name: str = "reduction",
    qualified: str = "ccl::v1::reduction",
) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib.so",
        version=version,
        enums=[_enum_type(name, qualified, members)],
        dwarf=DwarfMetadata(
            has_dwarf=True,
            enums={
                qualified: EnumInfo(
                    name=qualified,
                    underlying_byte_size=underlying_byte_size,
                    members=dict(members),
                )
            },
        ),
    )


# ── Primitive-level: the bare/qualified bridging itself ──────────────────


class TestEnumCanonicalNames:
    def test_bare_and_qualified_both_map_to_the_qualified_form(self) -> None:
        snap = _snap("1", [("SUM", 0)])
        names = _enum_canonical_names(snap)
        assert names["reduction"] == "ccl::v1::reduction"
        assert names["ccl::v1::reduction"] == "ccl::v1::reduction"

    def test_no_qualified_name_registers_nothing(self) -> None:
        """An enum with no recorded qualification carries no bridging
        information -- registering a bare-name-to-itself no-op entry would
        only set ``Change.qualified_name`` to a value identical to
        ``symbol``, which other consumers (e.g. the internal-namespace
        reachability check) read as real evidence on its own. See this
        function's own docstring."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[EnumType(name="Color", members=[EnumMember(name="RED", value=0)])],
        )
        names = _enum_canonical_names(snap)
        assert "Color" not in names

    def test_none_snapshot_is_empty(self) -> None:
        assert _enum_canonical_names(None) == {}

    def test_ambiguous_bare_name_across_two_enums_is_not_registered(self) -> None:
        """Codex review, fresh evidence: ``a::Color`` and ``b::Color`` share
        the bare name ``Color``. A plain ``setdefault`` would pin the bare
        key to whichever enum is iterated first, silently misattributing
        the OTHER enum's own header-tier finding to the wrong qualified
        spelling. The bare name must be dropped entirely rather than
        resolved to an arbitrary winner -- each enum's fully-qualified name
        stays a safe, unambiguous key regardless."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[
                _enum_type("Color", "a::Color", [("RED", 0)]),
                _enum_type("Color", "b::Color", [("BLUE", 1)]),
            ],
        )
        names = _enum_canonical_names(snap)
        assert "Color" not in names
        assert names["a::Color"] == "a::Color"
        assert names["b::Color"] == "b::Color"

    def test_a_global_enum_competing_with_a_namespaced_one_is_not_bridged(self) -> None:
        """Codex review: a genuinely global (unqualified) ``Color`` sharing a
        bare name with a namespaced ``ns::Color`` must not be silently
        bridged to it -- skipping the unqualified enum entirely (found on
        this function's ``RecordType`` sibling, ``record_canonical_names``)
        let it happen anyway."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[
                EnumType(name="Color", members=[EnumMember(name="RED", value=0)]),
                _enum_type("Color", "ns::Color", [("BLUE", 1)]),
            ],
        )
        names = _enum_canonical_names(snap)
        assert "Color" not in names
        assert names["ns::Color"] == "ns::Color"

    def test_a_dwarf_only_global_enum_competing_with_a_namespaced_header_enum_is_not_bridged(
        self,
    ) -> None:
        """Codex review, fresh evidence: an enum DWARF sees but the header
        surface never exposes contributes no ``snap.enums`` entry at all --
        the earlier fix, which only scans ``snap.enums``, still missed this
        exact competing-identity shape (found on this function's
        ``RecordType`` sibling, ``record_canonical_names``)."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[_enum_type("Color", "ns::Color", [("BLUE", 1)])],
            dwarf=DwarfMetadata(
                has_dwarf=True,
                enums={
                    "Color": EnumInfo(
                        name="Color", underlying_byte_size=4, members={"RED": 0}
                    )
                },
            ),
        )
        names = _enum_canonical_names(snap)
        assert "Color" not in names
        assert names["ns::Color"] == "ns::Color"

    def test_unambiguous_bare_name_across_matching_qualified_enums_still_registers(
        self,
    ) -> None:
        """Two *distinct* enums that happen to share both a bare name and a
        fully-qualified spelling (the same enum reported at two different
        member counts, e.g. by two independent detector passes over the
        same identity) are not an ambiguity -- the bare name still resolves
        cleanly since only one qualified target is ever proposed for it."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[
                _enum_type("Color", "a::Color", [("RED", 0)]),
                _enum_type("Color", "a::Color", [("RED", 0), ("BLUE", 1)]),
            ],
        )
        names = _enum_canonical_names(snap)
        assert names["Color"] == "a::Color"


class TestCanonicalizeEnumSymbol:
    def test_bare_member_symbol_resolves_to_qualified(self) -> None:
        names = _enum_canonical_names(_snap("1", [("SUM", 0)]))
        assert (
            _canonicalize_enum_symbol("reduction::SUM", names)
            == "ccl::v1::reduction::SUM"
        )

    def test_qualified_member_symbol_resolves_to_itself(self) -> None:
        names = _enum_canonical_names(_snap("1", [("SUM", 0)]))
        assert (
            _canonicalize_enum_symbol("ccl::v1::reduction::SUM", names)
            == "ccl::v1::reduction::SUM"
        )

    def test_bare_enum_level_symbol_resolves_too(self) -> None:
        """ENUM_UNDERLYING_SIZE_CHANGED/ENUM_BECAME_SCOPED use the bare enum
        name itself as ``symbol``, with no ``::member`` suffix at all."""
        names = _enum_canonical_names(_snap("1", [("SUM", 0)]))
        assert _canonicalize_enum_symbol("reduction", names) == "ccl::v1::reduction"

    def test_unrelated_symbol_resolves_to_none(self) -> None:
        names = _enum_canonical_names(_snap("1", [("SUM", 0)]))
        assert _canonicalize_enum_symbol("unrelated::Thing", names) is None


# ── Primitive-level: resolve_change_identity agrees once bridged ─────────


class TestResolveChangeIdentityAgreesAcrossTiers:
    def test_bare_and_qualified_enum_member_removed_share_an_identity(self) -> None:
        bare = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="reduction::PROD",
            description="Enum member removed: reduction::PROD",
            old_value="3",
            qualified_name="ccl::v1::reduction::PROD",
        )
        qualified = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="ccl::v1::reduction::PROD",
            description="Enum member removed: ccl::v1::reduction::PROD",
            old_value="3",
            qualified_name="ccl::v1::reduction::PROD",
        )
        assert (
            resolve_change_identity(bare).primary_id
            == resolve_change_identity(qualified).primary_id
        )

    def test_without_the_qualified_name_bridge_identities_still_differ(self) -> None:
        """Documents exactly what the bridge closes: with no
        ``qualified_name`` set at all, the two tiers' raw symbols alone
        still resolve to two different identities."""
        bare = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="reduction::PROD",
            description="Enum member removed: reduction::PROD",
            old_value="3",
        )
        qualified = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="ccl::v1::reduction::PROD",
            description="Enum member removed: ccl::v1::reduction::PROD",
            old_value="3",
        )
        assert (
            resolve_change_identity(bare).primary_id
            != resolve_change_identity(qualified).primary_id
        )


# ── _deduplicate_cross_detector must actually attempt it ─────────────────


class TestDeduplicateCrossDetectorCollapsesEnumFindings:
    def test_collapses_given_the_snapshots(self) -> None:
        bare = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="reduction::PROD",
            description="Enum member removed: reduction::PROD",
            old_value="3",
        )
        qualified = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="ccl::v1::reduction::PROD",
            description="Enum member removed: ccl::v1::reduction::PROD",
            old_value="3",
        )
        old = _snap("1", [("SUM", 0), ("PROD", 3)])
        new = _snap("2", [("SUM", 0)])
        result = _deduplicate_cross_detector([bare, qualified], old, new)
        assert len(result) == 1

    def test_without_snapshots_degrades_to_no_dedup(self) -> None:
        """A caller that doesn't pass old/new (e.g. an existing direct-call
        test with no snapshots at hand) must not regress -- this is a
        missed dedup, never an incorrect one."""
        bare = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="reduction::PROD",
            description="d",
            old_value="3",
        )
        qualified = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="ccl::v1::reduction::PROD",
            description="d",
            old_value="3",
        )
        assert len(_deduplicate_cross_detector([bare, qualified])) == 2


# ── End-to-end through compare() ──────────────────────────────────────────


class TestEndToEndOnlyOneFindingSurvives:
    def test_enum_member_removed_across_both_tiers_collapses_to_one(self) -> None:
        old = _snap("1", [("SUM", 0), ("MAX", 1), ("MIN", 2), ("PROD", 3)])
        new = _snap("2", [("SUM", 0), ("MAX", 1), ("MIN", 2)])
        result = compare(old, new)
        removed = [c for c in result.changes if c.kind is ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(removed) == 1
        assert removed[0].qualified_name == "ccl::v1::reduction::PROD"

    def test_enum_member_value_changed_across_both_tiers_collapses_to_one(self) -> None:
        old = _snap("1", [("SUM", 0), ("AVG", 1)])
        new = _snap("2", [("SUM", 0), ("AVG", 5)])
        result = compare(old, new)
        changed = [
            c for c in result.changes if c.kind is ChangeKind.ENUM_MEMBER_VALUE_CHANGED
        ]
        assert len(changed) == 1

    def test_enum_underlying_size_changed_has_no_header_tier_counterpart_to_dedupe(
        self,
    ) -> None:
        """Unlike the member-level kinds above, ``ENUM_UNDERLYING_SIZE_CHANGED``
        is emitted ONLY by the DWARF/L1 tier (`diff_platform._diff_enum_layouts`)
        -- `diff_types._diff_enums` (L2/header) never computes or reports an
        underlying-type byte-size change at all (CodeRabbit review, fresh
        evidence: confirmed by reading `_diff_enums`, which has no
        `ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED` emission site anywhere).
        There is therefore no genuine two-tier duplicate to collapse for this
        one kind -- this test instead pins that the sole DWARF-tier finding
        still survives unharmed (not silently dropped) now that the kind is
        wired into `_DEDUP_CATEGORIES` and its bare/qualified identity is
        resolvable, since a change that is wired for dedup but never actually
        collides must remain exactly as reported."""
        old = _snap("1", [("SUM", 0)], underlying_byte_size=4)
        new = _snap("2", [("SUM", 0)], underlying_byte_size=8)
        result = compare(old, new)
        changed = [
            c
            for c in result.changes
            if c.kind is ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED
        ]
        assert len(changed) == 1

    def test_unrelated_enums_are_not_accidentally_merged(self) -> None:
        """Two distinct, unrelated enums each losing a member must still
        produce two distinct findings -- the bridge must not collapse
        findings that merely share a kind."""
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[
                _enum_type("reduction", "ccl::v1::reduction", [("SUM", 0), ("MAX", 1)]),
                _enum_type("mode", "ccl::v1::mode", [("FAST", 0), ("SLOW", 1)]),
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            enums=[
                _enum_type("reduction", "ccl::v1::reduction", [("SUM", 0)]),
                _enum_type("mode", "ccl::v1::mode", [("FAST", 0)]),
            ],
        )
        result = compare(old, new)
        removed = {
            c.qualified_name or c.symbol
            for c in result.changes
            if c.kind is ChangeKind.ENUM_MEMBER_REMOVED
        }
        assert removed == {"ccl::v1::reduction::MAX", "ccl::v1::mode::SLOW"}


class TestEnumQualificationDoesNotLeakIntoUnrelatedKinds:
    """Codex review, fresh evidence: the bare/qualified enum bridge, applied
    at the top of ``_deduplicate_cross_detector``, was gated on
    ``c.kind in _DEDUP_CATEGORIES`` -- a much broader set covering
    function/variable/version dedup categories too, not just the four enum
    kinds. An unrelated finding whose bare `symbol` happened to coincide
    with a registered enum alias had its `Change.qualified_name` silently
    (and permanently -- the later ``EnrichSourceLocations`` step never
    overwrites an already-set value) rewritten to the enum's qualified
    spelling."""

    def test_a_same_named_function_removal_is_not_qualified_as_the_enum(self) -> None:
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[_enum_type("Color", "detail::Color", [("RED", 0)])],
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="Color",
            description="removed",
        )
        _deduplicate_cross_detector([change], snap, snap)
        assert change.qualified_name is None

    def test_a_same_named_function_removal_is_not_qualified_by_enrichment_either(
        self,
    ) -> None:
        """The identical collision risk existed at a SECOND, independent
        call site: `_enrich_source_locations` (the later pipeline step)
        called `_canonicalize_enum_symbol` unconditionally for any change
        still missing a `qualified_name`, not just the four enum kinds."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            enums=[_enum_type("Color", "detail::Color", [("RED", 0)])],
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="Color",
            description="removed",
        )
        _enrich_source_locations([change], snap, snap)
        assert change.qualified_name is None

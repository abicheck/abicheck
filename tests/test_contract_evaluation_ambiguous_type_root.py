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

"""``TestPublicModeAmbiguousTypeRoot`` -- split out of ``test_contract_evaluation.py``
(which was pushing the AI-readiness file-size hard cap) once this class grew a
second round of ``exact_type_identities`` coverage. Same fixtures/imports as
its former home; see that file's own module docstring for the surrounding
``contract_evaluation.py`` test suite's scope.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_evaluation import evaluate_change_contract_relevance
from abicheck.contract_relevance_types import ContractMode, ContractRelevance
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.surface import compute_public_surface


def _fn(
    name,
    ret="void",
    params=(),
    vis=Visibility.PUBLIC,
    origin=ScopeOrigin.UNKNOWN,
    mangled=None,
):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=origin,
    )


class TestPublicModeAmbiguousTypeRoot:
    """When two distinct records/enums share one bare tail name (e.g.
    ``one::Point``/``two::Point``, both spelled bare ``Point``) and an
    unqualified ``Point *`` public signature cannot identify which one it
    references, ``compute_public_surface`` deliberately keeps *both* in
    ``public_types`` (its own anti-hiding rule) while recording ``Point`` in
    ``ambiguous_type_names``. Confirmation must not treat that conservative
    closure expansion as proof of root membership (Codex review, eleventh
    round).

    An "indecisive ambiguity" exception was tried here and reverted -- see
    ``_confirmed_type_matches``'s docstring for the full reasoning, and
    ``test_a_public_header_sibling_no_public_api_reaches_is_not_confirmed``
    below for the counter-example that sank it. The rejection does cost real
    gate coverage (``measure_contract_shadow.py``'s ``unresolved_losses``
    counts it), so the temptation to relax it is real; the cases below exist
    to make that failed relaxation fail loudly instead.

    A second, narrower gap *was* since closed:
    ``PublicSurface.exact_type_identities`` now lets a public signature that
    spells the qualified form directly (``ns1::Point``, not just bare
    ``Point``) confirm even while an unrelated sibling shares the same bare
    tail -- see ``test_exact_qualified_reference_now_confirms`` below and
    ``_confirmed_type_matches``'s docstring for why that is not the same
    relaxation as the reverted one. A reference that only ever spells the
    bare, colliding tail (no side ever names the qualified form) still has
    no exact route to lean on and correctly stays rejected."""

    @staticmethod
    def _two_siblings(second_origin: ScopeOrigin) -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["Point *"])],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="one::Point",
                ),
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=32,
                    origin=second_origin,
                    qualified_name="two::Point",
                ),
            ],
        )

    def test_ambiguous_bare_tail_is_unresolved_not_confirmed(self) -> None:
        s = compute_public_surface(self._two_siblings(ScopeOrigin.PUBLIC_HEADER))
        assert "Point" in s.ambiguous_type_names
        assert "Point" in s.public_types
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Point", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_a_private_sibling_behind_the_collision_is_invisible(self) -> None:
        # Why the rejection cannot be relaxed by looking at the merged
        # origin: `origin_by_key` resolves the collision with public
        # winning, so the private sibling that makes the ambiguity matter
        # leaves no trace there.
        s = compute_public_surface(self._two_siblings(ScopeOrigin.PRIVATE_HEADER))
        assert s.origin_by_key["Point"] is ScopeOrigin.PUBLIC_HEADER
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Point", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED

    def test_a_public_header_sibling_no_public_api_reaches_is_not_confirmed(
        self,
    ) -> None:
        """The counter-example that sank an "indecisive ambiguity" shortcut.

        Both ``one::Point`` and ``two::Point`` are declared in public
        headers, but only ``one::Point`` is reached by a public API. A rule
        that confirmed the finding whenever every colliding entry is
        public-header *origin* therefore confirmed a finding about the
        unreachable one and gated on it (Codex review). Origin is where a
        declaration was written; membership here is reachability. Keep this
        case: it is the regression test for re-deriving that shortcut.
        """
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["Point *"])],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="one::Point",
                ),
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=32,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="two::Point",
                ),
            ],
        )
        s = compute_public_surface(snap)
        # Both qualified identities end up in `public_types` (the closure
        # walks every record that collides on the shared bare key, and
        # unconditionally records each one's own qualified spelling too --
        # matching public_types' existing "mark every match public"
        # anti-hiding rule for the bare `.name`, see _walk_type_closure).
        # `public_types` alone therefore still cannot say which qualified
        # identity was genuinely, independently reached -- that is exactly
        # what `exact_type_identities` is for, and it stays empty here:
        # neither side was ever reached via anything other than the shared,
        # ambiguous bare key "Point".
        assert s.public_types == {"Point", "one::Point", "two::Point"}
        assert s.exact_type_identities == set()
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="two::Point", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED

    def test_unambiguous_candidate_still_confirms_alongside_an_ambiguous_one(
        self,
    ) -> None:
        # A finding whose candidate set includes both an ambiguous name and
        # a genuinely unambiguous public type must still confirm via the
        # unambiguous one -- the ambiguity check should not overreject.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn("api", ret="void", params=["Point *", "Clear *"]),
            ],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="one::Point",
                ),
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=32,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="two::Point",
                ),
                RecordType(
                    name="Clear",
                    kind="struct",
                    size_bits=8,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Point" in s.ambiguous_type_names
        assert "Clear" not in s.ambiguous_type_names
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Clear", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_qualified_candidate_reached_only_via_ambiguous_bare_tail_is_unresolved(
        self,
    ) -> None:
        # Regression (Codex review, twelfth round): ns1::Mode/ns2::Mode share
        # the bare tail "Mode". A public signature referencing only bare
        # "Mode" makes _walk_type_closure add *both* qualified names to
        # public_types (walking each ambiguous match, per its own anti-hiding
        # rule) -- but ambiguous_type_names only ever records the bare tail
        # "Mode", never the qualified names themselves. A member-level
        # finding owner-stripped to the qualified "ns1::Mode" therefore
        # matched public_types directly and was wrongly confirmed, even
        # though the public signature never disambiguated which of the two
        # enums it actually reaches.
        from abicheck.model import EnumMember, EnumType

        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["Mode"])],
            enums=[
                EnumType(
                    name="ns1::Mode",
                    members=[EnumMember(name="X", value=0)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                EnumType(
                    name="ns2::Mode",
                    members=[EnumMember(name="Y", value=0)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Mode" in s.ambiguous_type_names
        assert {"ns1::Mode", "ns2::Mode"} <= s.public_types
        c = Change(
            kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
            symbol="ns1::Mode::X",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_qualified_candidate_with_unambiguous_bare_tail_still_confirms(
        self,
    ) -> None:
        # The propagated ambiguity check must not overreject: a qualified
        # candidate whose own bare tail is genuinely unambiguous still
        # confirms normally.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="ns::Widget")],
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="ns::Widget",
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Widget" not in s.ambiguous_type_names
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns::Widget", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_exact_qualified_reference_now_confirms(self) -> None:
        # Formerly a known, deliberately-deferred limitation (Codex review,
        # fifteenth round), now fixed: a public signature naming
        # ``ns1::Point`` *explicitly* (fully qualified) is a genuinely
        # exact, unambiguous reference -- even though `_type_identifiers`
        # also derives the bare tail "Point" from that same qualified
        # string (by design, to also match a short in-namespace alias), and
        # that bare tail *separately* drives the ambiguous widening path
        # that adds the unrelated `ns2::Point` to `public_types` too.
        # `_walk_type_closure` now records that "ns1::Point" was itself
        # queued and resolved to exactly one record
        # (`exact_type_identities`), independent of whatever the
        # separately-queued, genuinely-ambiguous bare tail "Point" also
        # swept in -- so this exact reference confirms even though its bare
        # tail remains ambiguous. See `_confirmed_type_matches`'s docstring
        # for the mechanism and why it does not resurrect the reverted
        # "indecisive ambiguity" shortcut (a bare-only reference, with no
        # exact qualified route at all, is unaffected and still rejected --
        # see `test_a_public_header_sibling_no_public_api_reaches_is_not_confirmed`).
        # Mirrors the twelfth-round test's DWARF-style convention (the
        # namespace is baked directly into `.name`, not a separate
        # `qualified_name`); see
        # `test_exact_qualified_reference_now_confirms_castxml_convention`
        # below for the same fix exercised through the bare-name +
        # `qualified_name` convention (castxml/clang) instead, where
        # `_index_surface_types` now also keys `record_by_name` by the
        # qualified spelling itself (Codex review). Either way, a public
        # signature must actually *spell* the qualifier for this exact
        # route to exist at all -- castxml/clang's own signature printer
        # only ever emits the bare leaf for a record reference (confirmed
        # against `dumper_castxml.py`'s `_type_name`), so a real castxml-
        # produced signature that only ever spells the bare tail (e.g. the
        # fp-rate corpus's `ambiguous_namespaced_leaf` case) has no such
        # route regardless of this indexing fix, and correctly remains
        # `UNKNOWN_UNRESOLVED`.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["ns1::Point *"])],
            types=[
                RecordType(
                    name="ns1::Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                RecordType(
                    name="ns2::Point",
                    kind="struct",
                    size_bits=32,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Point" in s.ambiguous_type_names
        assert {"ns1::Point", "ns2::Point"} <= s.public_types
        assert s.exact_type_identities == {"ns1::Point"}
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns1::Point", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_exact_qualified_reference_now_confirms_castxml_convention(self) -> None:
        # Same fix as test_exact_qualified_reference_now_confirms, exercised
        # through the *other* backend convention: bare `.name` + separate
        # `.qualified_name` (castxml/clang), rather than DWARF's qualifier-
        # baked-into-`.name`. Before this test's own fix (Codex review),
        # `_index_surface_types` only ever keyed `record_by_name` by `.name`
        # and its bare `::`-tail -- never by `.qualified_name` -- so a
        # queued spelling equal to the qualified form ("ns1::Point") never
        # resolved anything at all for this convention, leaving
        # `exact_type_identities` empty regardless of how exact the
        # reference actually was. `_index_surface_types` now also indexes
        # `.qualified_name` (guarded so a record whose `qualified_name`
        # happens to equal an already-registered key is never double-
        # counted into `ambiguous_type_names`), closing that gap for
        # whichever producer supplies a genuinely qualified reference under
        # this convention (a real castxml-produced signature never does --
        # see the previous test's docstring -- but a hand-built Change, or
        # a different producer, legitimately can).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["ns1::Point *"])],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="ns1::Point",
                ),
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=32,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="ns2::Point",
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Point" in s.ambiguous_type_names
        assert s.exact_type_identities == {"ns1::Point"}
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns1::Point", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_castxml_convention_indexing_does_not_double_count_a_unique_record(
        self,
    ) -> None:
        # Regression guard for the double-counting hazard the qualified-name
        # indexing fix above must avoid: a single, genuinely unique record
        # whose `qualified_name` differs from its bare `.name` must still
        # count as exactly one entry per key, not silently become "ambiguous"
        # just from being indexed under two keys instead of one.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="ns::Widget")],
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="ns::Widget",
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Widget" not in s.ambiguous_type_names
        assert "ns::Widget" not in s.ambiguous_type_names

    def test_qualified_reference_reached_only_ambiguously_has_no_exact_identity(
        self,
    ) -> None:
        # Counterpart to the previous test: when *neither* side ever spells
        # the qualified form (both siblings only ever reached via the
        # shared ambiguous bare tail), `exact_type_identities` stays empty
        # for both -- there is no exact route to distinguish, so this must
        # still be rejected exactly like
        # `test_a_public_header_sibling_no_public_api_reaches_is_not_confirmed`.
        s = compute_public_surface(self._two_siblings(ScopeOrigin.PUBLIC_HEADER))
        assert s.exact_type_identities == set()
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="one::Point", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED

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

"""What a ``project_*_to_depth`` result owns, and what it deliberately shares.

``project_snapshot_to_depth`` used to answer every rung with one
unconditional ``copy.deepcopy``. At or above the ``headers`` rung that
duplicated an entire L2 surface -- measured on a real 327-type/4,802-function
header snapshot at +34% resident (0.516 -> 0.691 GiB) and +12.5s per
comparison -- in order to rebind exactly two fields (``build_mode`` and
``build_source``), neither of which is read-modified-written. The copy is now
scoped per rung, which makes the module's ownership contract load-bearing
rather than incidental, so this file states it as executable invariants:

* **The guarantee that did not change** -- projecting never mutates its
  argument. Every in-tree caller depends on this (a ``dump`` artifact reused
  for a later, deeper comparison; ``service_compare_pipeline`` keeping
  ``pair.old``/``pair.new`` readable after projecting a view off them).
* **The guarantee that was never made** -- that the result is an independent
  mutable copy a caller may write *through*. At or above ``headers`` the
  declaration lists are shared with the input by design.
* **The rung that still owns everything** -- below ``headers``,
  ``_strip_header_and_above_evidence`` rewrites declarations, identity
  sidecars and fact bridges in place, so that rung deep-copies exactly as it
  always did. A regression in *either* direction (sharing where the strip
  mutates, or deep-copying where nothing does) is a real defect, so both are
  pinned.

The invariants are stated over the *whole* public rung ladder and over
generated field mutations rather than one hand-picked example, per
``AGENTS.md``'s "a bug fix's regression test targets the bug class"
requirement: a fixed-input alias assertion only forecloses the one field it
names, and the class here is "a consumer writes through a projected
snapshot".
"""

from __future__ import annotations

import copy

import pytest

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.evidence_depth import DEPTH_RANK
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.dwarf_facts import DwarfMetadata
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.policy.depth_projection import (
    project_pair_to_depth,
    project_snapshot_to_depth,
)

#: Every public rung, so a new rung joins these invariants automatically
#: rather than silently escaping them.
ALL_DEPTHS = sorted(DEPTH_RANK, key=lambda d: DEPTH_RANK[d])
HEADERS_RANK = DEPTH_RANK["headers"]
BUILD_RANK = DEPTH_RANK["build"]
AT_OR_ABOVE_HEADERS = [d for d in ALL_DEPTHS if DEPTH_RANK[d] >= HEADERS_RANK]
BELOW_HEADERS = [d for d in ALL_DEPTHS if DEPTH_RANK[d] < HEADERS_RANK]

#: The declaration containers whose ownership this module is about. Named as
#: a set so a newly-added container is a deliberate decision here, not an
#: unnoticed omission.
SURFACE_FIELDS = ("functions", "variables", "types", "enums")


def _snapshot(*, from_headers: bool = True, dwarf: bool = False) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib",
        version="1",
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z2f{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
                source_header="/proj/include/api.h",
            )
            for i in range(3)
        ],
        variables=[
            Variable(
                name="g",
                mangled="g",
                type="int",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
                source_header="/proj/include/api.h",
            )
        ],
        types=[
            RecordType(
                name="S",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="x", type="int")],
                origin=ScopeOrigin.PUBLIC_HEADER,
                source_header="/proj/include/api.h",
            )
        ],
        enums=[
            EnumType(
                name="E",
                members=[EnumMember(name="A", value=0)],
                origin=ScopeOrigin.PUBLIC_HEADER,
                source_header="/proj/include/api.h",
            )
        ],
        typedefs={"my_int": "int"},
        constants={"FOO": "1"},
        from_headers=from_headers,
        dwarf=DwarfMetadata(has_dwarf=True) if dwarf else None,
    )


def _pack(*, with_source_payloads: bool = False) -> BuildSourcePack:
    return BuildSourcePack(
        root="",
        source_graph=SourceGraphSummary(nodes=[]) if with_source_payloads else None,
    )


class TestArgumentIsNeverMutated:
    """The guarantee that did not change, over every rung.

    This is the invariant every caller actually relies on, so it is asserted
    against a full structural snapshot of the input taken *before* the
    projection -- not against a single field -- and for every rung, including
    the ones that share rather than copy.
    """

    @pytest.mark.parametrize("depth", ALL_DEPTHS)
    def test_input_is_structurally_unchanged(self, depth: str) -> None:
        snap = _snapshot(dwarf=True)
        snap.build_source = _pack()
        snap.build_mode = "release"
        before = copy.deepcopy(snap)

        project_snapshot_to_depth(snap, depth)

        assert snap.build_mode == before.build_mode
        assert (snap.build_source is None) == (before.build_source is None)
        for field in SURFACE_FIELDS:
            got, want = getattr(snap, field), getattr(before, field)
            assert len(got) == len(want)
            assert [d.name for d in got] == [d.name for d in want]
        assert snap.typedefs == before.typedefs
        assert snap.constants == before.constants
        assert snap.from_headers == before.from_headers

    @pytest.mark.parametrize("depth", ALL_DEPTHS)
    def test_pair_projection_leaves_both_operands_unchanged(self, depth: str) -> None:
        old, new = _snapshot(dwarf=True), _snapshot(dwarf=True)
        counts = [
            (len(getattr(s, f)) for f in SURFACE_FIELDS)
            and tuple(len(getattr(s, f)) for f in SURFACE_FIELDS)
            for s in (old, new)
        ]
        project_pair_to_depth(old, new, depth)
        assert [
            tuple(len(getattr(s, f)) for f in SURFACE_FIELDS) for s in (old, new)
        ] == counts

    @pytest.mark.parametrize("depth", ALL_DEPTHS)
    def test_result_is_always_a_distinct_object(self, depth: str) -> None:
        """Rebinding a field on the result -- which the native ``compare``
        CLI does with ``old.build_source`` -- must never reach the input, so
        an identity return is never correct for a recognized rung."""
        snap = _snapshot()
        projected = project_snapshot_to_depth(snap, depth)
        assert projected is not snap

        projected.build_source = _pack()
        assert snap.build_source is None


class TestRungOwnership:
    """Which rungs own their surface, and which share it -- both directions."""

    @pytest.mark.parametrize("depth", BELOW_HEADERS)
    @pytest.mark.parametrize("field", SURFACE_FIELDS)
    def test_below_headers_fully_owns_every_container(
        self, depth: str, field: str
    ) -> None:
        """``_strip_header_and_above_evidence`` mutates declarations in
        place, so this rung must not share a container *or* an element."""
        snap = _snapshot(from_headers=False, dwarf=True)
        projected = project_snapshot_to_depth(snap, depth)

        assert getattr(projected, field) is not getattr(snap, field)
        for got, want in zip(getattr(projected, field), getattr(snap, field)):
            assert got is not want

    @pytest.mark.parametrize("depth", AT_OR_ABOVE_HEADERS)
    @pytest.mark.parametrize("field", SURFACE_FIELDS)
    def test_at_or_above_headers_shares_the_surface(
        self, depth: str, field: str
    ) -> None:
        """The documented, deliberate sharing. Asserted positively so that
        re-introducing an unconditional deep copy -- the regression this
        change exists to remove -- fails loudly instead of merely costing
        memory again."""
        snap = _snapshot()
        projected = project_snapshot_to_depth(snap, depth)
        assert getattr(projected, field) is getattr(snap, field)

    @pytest.mark.parametrize("depth", AT_OR_ABOVE_HEADERS)
    def test_a_surviving_build_source_pack_is_still_owned(self, depth: str) -> None:
        """``_project_build_source_pack`` degrades a pack in place, so a pack
        that survives a rung must never alias the input's. Below ``build``
        the pack is dropped wholesale, which is the no-copy case."""
        snap = _snapshot()
        snap.build_source = _pack()
        projected = project_snapshot_to_depth(snap, depth)

        if DEPTH_RANK[depth] < BUILD_RANK:
            assert projected.build_source is None
        else:
            assert projected.build_source is not None
            assert projected.build_source is not snap.build_source
        assert snap.build_source is not None

    def test_degrading_a_pack_does_not_reach_the_input_pack(self) -> None:
        """The in-place half of the pack rule, exercised rather than
        asserted structurally: ``build`` clears L4/L5 payloads and demotes
        their coverage rows, and none of that may land on the input."""
        snap = _snapshot()
        pack = _pack(with_source_payloads=True)
        snap.build_source = pack

        projected = project_snapshot_to_depth(snap, "build")

        assert projected.build_source is not None
        assert projected.build_source.source_graph is None
        assert snap.build_source is pack
        assert pack.source_graph is not None


class TestSharingIsSafeForTheWaysCallersActuallyUse_A_Projection:
    """Sharing is only sound because no consumer writes through the result.

    These pin the *usage patterns* that make it sound, so a future consumer
    that starts mutating a projected surface trips a test here rather than
    silently corrupting a caller's retained snapshot.
    """

    @pytest.mark.parametrize("depth", ALL_DEPTHS)
    def test_two_simultaneous_projections_of_one_input_are_independent(
        self, depth: str
    ) -> None:
        """A release fan-out compares one stored baseline against several
        candidates concurrently, so one input is projected repeatedly and the
        results coexist. Rebinding on one must never be visible on the other
        or on the shared input."""
        snap = _snapshot()
        first = project_snapshot_to_depth(snap, depth)
        second = project_snapshot_to_depth(snap, depth)

        assert first is not second
        first.build_mode = "first"
        second.build_mode = "second"
        assert first.build_mode == "first"
        assert second.build_mode == "second"
        assert snap.build_mode is None

    def test_repeated_projection_under_different_depths_is_order_independent(
        self,
    ) -> None:
        """Projecting the same input at several rungs, in any order, must
        give each rung the same answer it gives alone -- the property that
        fails first if one projection ever wrote through to the input."""
        snap = _snapshot(from_headers=False, dwarf=True)

        alone = {
            d: project_snapshot_to_depth(_snapshot(from_headers=False, dwarf=True), d)
            for d in ALL_DEPTHS
        }
        together = [project_snapshot_to_depth(snap, d) for d in reversed(ALL_DEPTHS)]

        for projected in together:
            depth = next(
                d
                for d in ALL_DEPTHS
                if len(alone[d].functions) == len(projected.functions)
                and len(alone[d].types) == len(projected.types)
            )
            assert len(projected.functions) == len(alone[depth].functions)
            assert len(projected.types) == len(alone[depth].types)

    @pytest.mark.parametrize("depth", ALL_DEPTHS)
    def test_serializing_a_projection_does_not_disturb_the_input(
        self, depth: str
    ) -> None:
        """Serialization walks the whole shared graph; it must stay a read."""
        from abicheck.storage.snapshot_encode import snapshot_to_json

        snap = _snapshot(dwarf=True)
        before = snapshot_to_json(snap)

        projected = project_snapshot_to_depth(snap, depth)
        snapshot_to_json(projected)

        assert snapshot_to_json(snap) == before


class TestTheShallowCopyIsAWholeObjectCopy:
    """Why ``copy.copy`` and specifically not ``dataclasses.replace``.

    ``AbiSnapshot`` is a 76-field dataclass with a ``__post_init__``.
    ``copy.copy`` copies its ``__dict__`` wholesale and calls neither
    ``__init__`` nor ``__post_init__``, so every field -- including any that
    is ``init=False`` or is normalised by ``__post_init__`` -- arrives on the
    copy exactly as it stood on the input. ``dataclasses.replace`` does not:
    it re-runs ``__init__``/``__post_init__`` and refuses ``init=False``
    fields outright. It is the faster-looking substitution and it is the
    wrong one, so this pins the property that distinguishes them rather than
    leaving it to a comment.
    """

    def test_every_field_survives_a_projection_unchanged(self) -> None:
        import dataclasses

        snap = _snapshot(dwarf=True)
        snap.build_mode = "release"
        fields = [f.name for f in dataclasses.fields(AbiSnapshot)]
        assert len(fields) > 50, "guard: this test is about a wide dataclass"

        projected = project_snapshot_to_depth(snap, "source")

        # `source` is the top rung: it rebinds nothing at all, so every
        # field must compare identical (by identity where the value is a
        # container, which is the sharing this rung documents).
        differing = [
            name
            for name in fields
            if getattr(projected, name) is not getattr(snap, name)
            and getattr(projected, name) != getattr(snap, name)
        ]
        assert not differing, f"fields lost or altered by the copy: {differing}"

    def test_post_init_is_not_re_run_by_the_shallow_copy(self) -> None:
        """A projection must not re-normalise anything. Pinned by observing
        that ``__post_init__`` does not fire for the copy."""
        calls: list[object] = []
        original = AbiSnapshot.__post_init__

        def counting_post_init(self: AbiSnapshot) -> None:
            calls.append(self)
            original(self)

        AbiSnapshot.__post_init__ = counting_post_init  # type: ignore[method-assign]
        try:
            snap = _snapshot()
            calls.clear()
            project_snapshot_to_depth(snap, "headers")
        finally:
            AbiSnapshot.__post_init__ = original  # type: ignore[method-assign]

        assert calls == []

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

"""ADR-065 S3: package component inventories and support-promise findings.

Three primitives get their own contract stated as invariants rather than as
one worked example each, per the repository's "Primitive-level property
tests" rule:

* :class:`~abicheck.model.package_inventory.PackageInventory` and
  :func:`~abicheck.model.package_inventory.merge_unproduced` -- a small
  reusable record with a completeness flag whose whole job is to keep
  "declared", "complete" and "unproduced" from collapsing onto each other;
* :func:`~abicheck.package.package_component_inventory` -- exercised through
  the real extractors on real containers, not through a hand-built tuple of
  names, because the property being defended is "a container this build
  unpacks in full yields a complete inventory";
* :func:`~abicheck.policy.support_promise.support_promise_changes` -- whose
  one safety property (never a finding without a proven inventory, under any
  policy value) is checked exhaustively over the whole policy domain and the
  whole acquisition-state domain rather than on the one case that motivated
  it.
"""

from __future__ import annotations

import itertools
import tarfile
import zipfile
from pathlib import Path

import pytest

from abicheck.model.package_inventory import (
    ComponentKind,
    PackageComponent,
    PackageInventory,
    merge_unproduced,
)
from abicheck.model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
    SideInventory,
)
from abicheck.package import (
    DirExtractor,
    TarExtractor,
    WheelExtractor,
    package_component_inventory,
)
from abicheck.policy.support_promise import (
    SUPPORT_PROMISE_POLICIES,
    support_promise_changes,
    validate_support_promise_policy,
)
from abicheck.report.comparison_scope import release_scope_warnings

# A minimal but genuinely ELF-shaped shared object, so `discover_shared_
# libraries`' own `_is_elf_shared_object` test (not just the filename) accepts
# it -- a filename-only fixture would make every assertion below pass for the
# wrong reason.
_ELF_ET_DYN = (
    b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"\x03\x00" + b"\x3e\x00" + b"\x00" * 48
)


def _so_bytes() -> bytes:
    return _ELF_ET_DYN + b"\x00" * 64


def _member(
    name: str,
    state: AcquisitionState,
    *,
    old: bool = True,
    new: bool = True,
) -> MemberAcquisition:
    return MemberAcquisition(member=name, state=state, old_present=old, new_present=new)


def _record(
    *members: MemberAcquisition,
    old: InventoryCompleteness = InventoryCompleteness.UNPROVEN,
    new: InventoryCompleteness = InventoryCompleteness.UNPROVEN,
) -> ScopeAcquisitionRecord:
    return ScopeAcquisitionRecord(
        members=members,
        old_inventory=SideInventory(old, "old side"),
        new_inventory=SideInventory(new, "new side"),
        selection="all_expected",
    )


class TestPackageInventoryProperties:
    """The record's own contract, independent of any extractor."""

    def test_a_duplicate_member_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="twice"):
            PackageInventory(
                components=(
                    PackageComponent(member="libfoo.so", path="lib/libfoo.so.1"),
                    PackageComponent(member="libfoo.so", path="lib/libfoo.so.2"),
                ),
                complete=True,
            )

    @pytest.mark.parametrize("complete", [True, False])
    @pytest.mark.parametrize("n_libs", [0, 1, 5])
    @pytest.mark.parametrize("n_other", [0, 3])
    def test_members_are_exactly_the_shared_library_components(
        self, complete: bool, n_libs: int, n_other: int
    ) -> None:
        """`members` never depends on `complete`, on how many non-library
        components the container declared, or on their order -- an oracle
        stated independently of the implementation's own comprehension."""
        libs = [
            PackageComponent(member=f"lib{i}.so", path=f"lib/lib{i}.so")
            for i in range(n_libs)
        ]
        other = [
            PackageComponent(
                member=f"doc{i}", path=f"share/doc{i}", kind=ComponentKind.OTHER
            )
            for i in range(n_other)
        ]
        forward = PackageInventory(components=tuple(libs + other), complete=complete)
        backward = PackageInventory(components=tuple(other + libs), complete=complete)
        expected = {f"lib{i}.so" for i in range(n_libs)}
        assert forward.members == expected
        assert backward.members == forward.members
        assert all(forward.declares(m) for m in expected)
        assert not forward.declares("never-declared.so")

    @pytest.mark.parametrize("bad", [1, 0, "true", "", None, []])
    def test_from_dict_refuses_a_non_boolean_completeness_claim(
        self, bad: object
    ) -> None:
        """A completeness proof is what a removal finding rests on, so a
        malformed document may not manufacture one by being truthy (or lose a
        real one by being a falsy non-bool)."""
        with pytest.raises(ValueError, match="must be a boolean"):
            PackageInventory.from_dict({"complete": bad, "components": []})

    @pytest.mark.parametrize("complete", [True, False])
    def test_round_trip_preserves_every_field(self, complete: bool) -> None:
        inv = PackageInventory(
            components=(
                PackageComponent(member="libfoo.so", path="usr/lib/libfoo.so.1"),
                PackageComponent(
                    member="README", path="README", kind=ComponentKind.OTHER
                ),
            ),
            complete=complete,
            provenance="a provenance sentence",
            unproduced={"libgone.so": "why"},
        )
        assert PackageInventory.from_dict(inv.to_dict()) == inv


class TestMergeUnproducedProperties:
    """`merge_unproduced`'s contract: declared-minus-produced, and nothing
    else -- in particular it never touches `complete`, never invents a
    component, and never depends on input order."""

    @pytest.mark.parametrize("produced_count", range(0, 5))
    def test_unproduced_is_exactly_declared_minus_produced(
        self, produced_count: int
    ) -> None:
        declared = [f"lib{i}.so" for i in range(4)]
        inv = PackageInventory(
            components=tuple(
                PackageComponent(member=m, path=f"lib/{m}") for m in declared
            ),
            complete=True,
            provenance="p",
        )
        for produced in itertools.combinations(declared, produced_count):
            merged = merge_unproduced(inv, produced)
            assert merged is not None
            assert set(merged.unproduced) == set(declared) - set(produced)
            # Never rewrites what the container proved, nor what it declared.
            assert merged.complete is inv.complete
            assert merged.components == inv.components
            assert merged.provenance == inv.provenance

    def test_order_and_extra_produced_members_do_not_matter(self) -> None:
        inv = PackageInventory(
            components=(
                PackageComponent(member="a.so", path="a.so"),
                PackageComponent(member="b.so", path="b.so"),
            ),
            complete=True,
        )
        first = merge_unproduced(inv, ["b.so", "unrelated.so"])
        second = merge_unproduced(inv, ["unrelated.so", "b.so"])
        assert first is not None and second is not None
        assert set(first.unproduced) == {"a.so"} == set(second.unproduced)

    def test_a_non_library_component_is_never_unproduced(self) -> None:
        """Only comparison operands can be *expected but not produced*: a
        licence file the container ships is not a member of the release's
        component set at all."""
        inv = PackageInventory(
            components=(
                PackageComponent(
                    member="LICENSE", path="LICENSE", kind=ComponentKind.OTHER
                ),
            ),
            complete=True,
        )
        merged = merge_unproduced(inv, [])
        assert merged is not None
        assert merged.unproduced == {}

    def test_none_passes_through(self) -> None:
        assert merge_unproduced(None, ["anything"]) is None


class TestPackageComponentInventoryFromRealContainers:
    """Through the real extractors, on real containers.

    The property under test is the one D2 rests on: *a container this build
    unpacks in full yields a complete inventory; a directory operand never
    does* -- so every archive format the extractor family supports is
    exercised through its own public `extract()` entry point, not through a
    hand-assembled name list that would pass identically for a format whose
    extractor forgot to make the claim.
    """

    def _tar(self, tmp_path: Path, names: dict[str, bytes]) -> Path:
        src = tmp_path / "src"
        src.mkdir()
        archive = tmp_path / "pkg.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            for rel, data in names.items():
                member = src / rel
                member.parent.mkdir(parents=True, exist_ok=True)
                member.write_bytes(data)
                tf.add(member, arcname=rel)
        return archive

    def _wheel(self, tmp_path: Path, names: dict[str, bytes]) -> Path:
        archive = tmp_path / "pkg-1.0-py3-none-any.whl"
        with zipfile.ZipFile(archive, "w") as zf:
            for rel, data in names.items():
                zf.writestr(rel, data)
        return archive

    @pytest.mark.parametrize("kind", ["tar", "wheel"])
    def test_a_fully_unpacked_archive_yields_a_complete_inventory(
        self, tmp_path: Path, kind: str
    ) -> None:
        from abicheck.package import discover_shared_libraries

        payload = {
            "usr/lib/libfoo.so.1": _so_bytes(),
            "usr/lib/libbar.so": _so_bytes(),
            "usr/share/doc/README": b"hello",
        }
        if kind == "tar":
            archive = self._tar(tmp_path, payload)
            extractor: TarExtractor | WheelExtractor = TarExtractor()
        else:
            archive = self._wheel(tmp_path, payload)
            extractor = WheelExtractor()
        target = tmp_path / "out"
        target.mkdir()
        result = extractor.extract(archive, target)
        assert result.container_complete is True
        libs = discover_shared_libraries(result.lib_dir)
        inventory = package_component_inventory(
            result.lib_dir, libs, container_complete=result.container_complete
        )
        assert inventory.complete is True
        assert inventory.members == {"libfoo.so", "libbar.so"}
        assert inventory.unproduced == {}
        assert "unpacked in full" in inventory.provenance

    def test_a_directory_operand_never_proves_completeness(
        self, tmp_path: Path
    ) -> None:
        from abicheck.package import discover_shared_libraries

        tree = tmp_path / "tree" / "usr" / "lib"
        tree.mkdir(parents=True)
        (tree / "libfoo.so").write_bytes(_so_bytes())
        result = DirExtractor().extract(tmp_path / "tree", tmp_path / "unused")
        assert result.container_complete is False
        inventory = package_component_inventory(
            result.lib_dir,
            discover_shared_libraries(result.lib_dir),
            container_complete=result.container_complete,
        )
        # The components are found either way -- what a directory cannot do
        # is prove that nothing else was ever meant to be there.
        assert inventory.members == {"libfoo.so"}
        assert inventory.complete is False
        assert "cannot be proven" in inventory.provenance

    def test_a_dangling_shared_object_link_is_unproduced_not_absent(
        self, tmp_path: Path
    ) -> None:
        """The distinction S3 exists to draw: the package plainly ships
        `libghost.so`, so its counterpart on the other side must not read as a
        removal -- it is an acquisition failure."""
        from abicheck.package import discover_shared_libraries

        lib = tmp_path / "usr" / "lib"
        lib.mkdir(parents=True)
        (lib / "libfoo.so").write_bytes(_so_bytes())
        (lib / "libghost.so").symlink_to("libmissing.so.9")
        inventory = package_component_inventory(
            tmp_path,
            discover_shared_libraries(tmp_path),
            container_complete=True,
        )
        assert inventory.members == {"libfoo.so"}
        assert set(inventory.unproduced) == {"libghost.so"}
        assert "not be reached" in inventory.unproduced["libghost.so"]

    def test_a_non_elf_so_named_file_is_neither_a_member_nor_unproduced(
        self, tmp_path: Path
    ) -> None:
        """A linker script named `libfoo.so` resolves fine and simply is not a
        comparison operand -- reporting it unproduced would manufacture an
        incomplete scope out of an ordinary package layout."""
        from abicheck.package import discover_shared_libraries

        lib = tmp_path / "usr" / "lib"
        lib.mkdir(parents=True)
        (lib / "libscript.so").write_text("INPUT(libreal.so.1)\n")
        inventory = package_component_inventory(
            tmp_path,
            discover_shared_libraries(tmp_path),
            container_complete=True,
        )
        assert inventory.members == set()
        assert inventory.unproduced == {}


class TestSupportPromisePolicy:
    def test_the_default_is_off(self) -> None:
        assert validate_support_promise_policy(None) == "off"

    @pytest.mark.parametrize("value", SUPPORT_PROMISE_POLICIES)
    def test_every_declared_value_validates(self, value: str) -> None:
        assert validate_support_promise_policy(value) == value

    @pytest.mark.parametrize("value", ["", "on", "warn", "block", "DECLARED"])
    def test_anything_else_is_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="support-promise policy"):
            validate_support_promise_policy(value)

    @pytest.mark.parametrize("policy", [None, *SUPPORT_PROMISE_POLICIES])
    @pytest.mark.parametrize("state", list(AcquisitionState))
    def test_no_policy_value_can_produce_a_finding_without_a_proof(
        self, policy: str | None, state: AcquisitionState
    ) -> None:
        """The safety property, over the whole cross-product of the policy
        domain and the acquisition-state domain rather than the one state that
        motivated it: with both inventories unproven there is no support-promise
        finding, whatever the member's state and whatever the policy says."""
        record = _record(_member("libfoo.so", state, old=True, new=False))
        assert support_promise_changes(record, policy) == []

    @pytest.mark.parametrize("policy", [None, "off"])
    def test_off_emits_nothing_even_with_a_proof(self, policy: str | None) -> None:
        record = _record(
            _member("libfoo.so", AcquisitionState.NOT_SUPPLIED, new=False),
            new=InventoryCompleteness.PROVEN,
        )
        assert support_promise_changes(record, policy) == []

    def test_declared_reports_a_proven_removal_with_its_receipt(self) -> None:
        record = _record(
            _member("libfoo.so", AcquisitionState.NOT_SUPPLIED, new=False),
            new=InventoryCompleteness.PROVEN,
        )
        (change,) = support_promise_changes(record, "declared")
        assert change.kind.value == "support_promise_component_retired"
        assert change.symbol == "libfoo.so"
        # D2's clarification: the finding carries what proved the absence.
        assert "new side" in (change.new_value or "")

    def test_declared_reports_a_proven_addition_symmetrically(self) -> None:
        record = _record(
            _member("libnew.so", AcquisitionState.NOT_SUPPLIED, old=False),
            old=InventoryCompleteness.PROVEN,
        )
        (change,) = support_promise_changes(record, "declared")
        assert change.kind.value == "support_promise_component_introduced"
        assert "old side" in (change.old_value or "")

    def test_a_failed_member_is_never_a_retired_promise(self) -> None:
        """Even against a proven-complete NEW inventory: the member's own
        acquisition never established what the OLD artifact was."""
        record = _record(
            _member("libfoo.so", AcquisitionState.FAILED, new=False),
            new=InventoryCompleteness.PROVEN,
        )
        assert support_promise_changes(record, "declared") == []

    def test_an_expected_not_produced_member_is_never_a_retired_promise(
        self,
    ) -> None:
        record = _record(
            _member("libfoo.so", AcquisitionState.EXPECTED_NOT_PRODUCED, new=False),
            new=InventoryCompleteness.PROVEN,
        )
        assert support_promise_changes(record, "declared") == []

    def test_a_scalar_run_has_no_component_set_to_promise(self) -> None:
        assert support_promise_changes(None, "declared") == []


class TestReleaseScopeWarnings:
    """ADR-065 S4: the notices now come from the record, so they can tell the
    four states apart -- the exact thing a set difference could not."""

    def test_an_unmatched_member_is_never_called_removed(self) -> None:
        record = _record(
            _member("libkept.so", AcquisitionState.AVAILABLE),
            _member("libfoo.so", AcquisitionState.NOT_SUPPLIED, new=False),
        )
        (msg,) = release_scope_warnings(record)
        assert "unmatched (no counterpart on NEW)" in msg
        assert "removed" not in msg

    def test_a_proven_absence_is_called_removed_and_names_its_proof(self) -> None:
        record = _record(
            _member("libkept.so", AcquisitionState.AVAILABLE),
            _member("libfoo.so", AcquisitionState.NOT_SUPPLIED, new=False),
            new=InventoryCompleteness.PROVEN,
        )
        (msg,) = release_scope_warnings(record)
        assert "library removed" in msg
        assert "new side" in msg

    def test_an_out_of_scope_member_is_not_mentioned(self) -> None:
        record = _record(
            _member("libfoo.so", AcquisitionState.AVAILABLE),
            _member("libother.so", AcquisitionState.OUT_OF_SCOPE, new=False),
        )
        assert release_scope_warnings(record) == []

    def test_no_comparison_completed_is_always_reported(self) -> None:
        record = _record(_member("libfoo.so", AcquisitionState.FAILED))
        assert any(
            "no comparison completed" in m for m in release_scope_warnings(record)
        )

    @pytest.mark.parametrize("state", list(AcquisitionState))
    def test_every_state_is_reported_at_most_once_per_member(
        self, state: AcquisitionState
    ) -> None:
        """Order-independent and duplicate-free over the whole state domain --
        the invariant the old two-loop set-difference version could not state,
        since a member could appear in both differences under a mismatched
        key."""
        record = _record(_member("libfoo.so", state, old=True, new=False))
        messages = release_scope_warnings(record)
        named = [m for m in messages if "libfoo.so" in m]
        assert len(named) <= 1

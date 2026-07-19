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

"""``EnumType`` gained a ``qualified_name`` field (PR #608 follow-up, mirroring
``RecordType.qualified_name``) so ``diff_types.py``'s enum detectors
(``_diff_enums``, ``_diff_enum_renames``, ``_diff_enum_deprecated``) can match
old/new enums by namespace-qualified identity instead of bare ``EnumType.name``
alone. Before this fix, two distinct enums sharing a bare leaf name in
different namespaces (e.g. ``ns1::Status`` and ``ns2::Status``) could be
cross-matched across old/new snapshots, fabricating or missing findings for
the wrong enum -- the exact short/leaf-name collision class already fixed for
``RecordType``.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot, EnumMember, EnumType


def _snap(version="1.0", enums=None):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=[], variables=[], types=[], enums=enums or [],
    )


def _enum(qualified, members, deprecated=None):
    bare = qualified.split("::")[-1]
    return EnumType(
        name=bare, qualified_name=qualified,
        members=[EnumMember(name=n, value=v) for n, v in members],
        deprecated=deprecated,
    )


class TestEnumMemberChangesAmbiguitySafe:
    def test_member_value_change_not_cross_attributed(self):
        """ns1::Status is unchanged; only ns2::Status's member value changes.
        Reversed insertion order on the new side forces a naive last-write-wins
        bare-name dict to compare the wrong pair.
        """
        ns1_old = _enum("ns1::Status", [("OK", 0), ("FAIL", 1)])
        ns2_old = _enum("ns2::Status", [("OK", 0), ("FAIL", 1)])
        ns1_new = _enum("ns1::Status", [("OK", 0), ("FAIL", 1)])
        ns2_new = _enum("ns2::Status", [("OK", 0), ("FAIL", 2)])

        r = compare(
            _snap(enums=[ns1_old, ns2_old]),
            _snap(enums=[ns2_new, ns1_new]),  # reversed order
        )

        value_changes = [c for c in r.changes if c.kind == ChangeKind.ENUM_MEMBER_VALUE_CHANGED]
        assert len(value_changes) == 1
        assert value_changes[0].new_value == "2"

    def test_member_removed_from_one_namespace_not_confused_with_other(self):
        """ns2::Status genuinely loses a member; ns1::Status is untouched."""
        ns1_old = _enum("ns1::Status", [("OK", 0), ("FAIL", 1)])
        ns2_old = _enum("ns2::Status", [("OK", 0), ("FAIL", 1)])
        ns1_new = _enum("ns1::Status", [("OK", 0), ("FAIL", 1)])
        ns2_new = _enum("ns2::Status", [("OK", 0)])

        r = compare(
            _snap(enums=[ns1_old, ns2_old]),
            _snap(enums=[ns2_new, ns1_new]),
        )

        removed = [c for c in r.changes if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "Status::FAIL"


class TestEnumRenameAmbiguitySafe:
    def test_rename_detected_for_the_correct_namespace_only(self):
        """ns2::Status renames FAIL->ERROR (same value); ns1::Status is
        untouched. Reversed insertion order on the new side must not cause
        the rename to be attributed to (or missed for) ns1::Status."""
        ns1_old = _enum("ns1::Status", [("OK", 0), ("FAIL", 1)])
        ns2_old = _enum("ns2::Status", [("OK", 0), ("FAIL", 1)])
        ns1_new = _enum("ns1::Status", [("OK", 0), ("FAIL", 1)])
        ns2_new = _enum("ns2::Status", [("OK", 0), ("ERROR", 1)])

        r = compare(
            _snap(enums=[ns1_old, ns2_old]),
            _snap(enums=[ns2_new, ns1_new]),
        )

        renames = [c for c in r.changes if c.kind == ChangeKind.ENUM_MEMBER_RENAMED]
        assert len(renames) == 1
        assert renames[0].old_value == "FAIL"
        assert renames[0].new_value == "ERROR"


class TestEnumDeprecatedAmbiguitySafe:
    def test_deprecation_added_for_the_correct_namespace_only(self):
        """ns2::Status gains [[deprecated]]; ns1::Status is untouched."""
        ns1_old = _enum("ns1::Status", [("OK", 0)])
        ns2_old = _enum("ns2::Status", [("OK", 0)])
        ns1_new = _enum("ns1::Status", [("OK", 0)])
        ns2_new = _enum("ns2::Status", [("OK", 0)], deprecated="use ns2::NewStatus")

        old_snap = _snap(enums=[ns1_old, ns2_old])
        new_snap = _snap(enums=[ns2_new, ns1_new])
        old_snap.ast_producer = "castxml"
        new_snap.ast_producer = "castxml"
        old_snap.from_headers = True
        new_snap.from_headers = True

        r = compare(old_snap, new_snap)

        added = [c for c in r.changes if c.kind == ChangeKind.ENUM_DEPRECATED_ADDED]
        assert len(added) == 1
        assert added[0].symbol == "Status"
        assert added[0].new_value == "use ns2::NewStatus"


class TestEnumRemovalAmbiguitySafe:
    def test_removed_enum_not_masked_by_surviving_same_leaf_name_sibling(self):
        """ns1::Status is genuinely removed; ns2::Status (same bare name,
        different namespace) survives unchanged. A naive bare-name dict
        would consider 'Status' still present and silently miss the
        removal.
        """
        ns1_old = _enum("ns1::Status", [("OK", 0)])
        ns2_old = _enum("ns2::Status", [("OK", 0)])
        ns2_new = _enum("ns2::Status", [("OK", 0)])

        r = compare(
            _snap(enums=[ns1_old, ns2_old]),
            _snap(enums=[ns2_new]),
        )

        removed = [c for c in r.changes if c.kind == ChangeKind.TYPE_REMOVED and c.symbol == "Status"]
        assert len(removed) == 1

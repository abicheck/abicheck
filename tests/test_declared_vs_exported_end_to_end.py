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

"""The reported two-artifact scenario, end to end through ``compare()``.

Bug class ``evidence.independent_facts_conflated_into_one_signal``. The
quadrant contract itself is covered exhaustively in
``tests/test_declaration_surface_properties.py``; this module is the
whole-workflow half AGENTS.md's "Validate the user-facing result" rule
asks for, plus the two controls the review's acceptance list requires --
a genuine source removal must still be reported, and a public
base/derived vtable change must still be breaking -- so the fix cannot be
shown to work by suppressing real findings.

The scenario: two builds of one library with **byte-identical headers**,
differing only in whether one public inline method is emitted as a
dynamic export. Compared in both directions, and each side compared
against itself.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model import (
    AbiSnapshot,
    Fact,
    Function,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import load_snapshot, save_snapshot

_ADD = "svs::runtime::v0::DynamicVamanaIndex::add"
_ADD_MANGLED = "_ZN3svs7runtime2v019DynamicVamanaIndex3addEmPKf"
_SEARCH = "svs::runtime::v0::DynamicVamanaIndex::search"
_SEARCH_MANGLED = "_ZN3svs7runtime2v019DynamicVamanaIndex6searchEv"
_EXPERIMENTAL = "svs::experimental::tune"
_EXPERIMENTAL_MANGLED = "_ZN3svs12experimental4tuneEv"


def _declared(name: str, mangled: str, *, exported: bool) -> Function:
    """A header-declared function, exported or not -- the only difference
    between the two artifacts under test."""
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC if exported else Visibility.HIDDEN,
        declared_fact=Fact.present(True),
        exported_fact=Fact.present(exported),
    )


def _snapshot(functions: list[Function], types: list[RecordType] | None = None):
    return AbiSnapshot(
        library="libsvs.so",
        version="0.1",
        functions=functions,
        types=types or [],
    )


def _default_build() -> AbiSnapshot:
    return _snapshot(
        [
            _declared(_ADD, _ADD_MANGLED, exported=True),
            _declared(_SEARCH, _SEARCH_MANGLED, exported=True),
            _declared(_EXPERIMENTAL, _EXPERIMENTAL_MANGLED, exported=True),
        ]
    )


def _public_only_build() -> AbiSnapshot:
    """Identical headers; ``add`` is no longer emitted as a dynamic export."""
    return _snapshot(
        [
            _declared(_ADD, _ADD_MANGLED, exported=False),
            _declared(_SEARCH, _SEARCH_MANGLED, exported=True),
            _declared(_EXPERIMENTAL, _EXPERIMENTAL_MANGLED, exported=True),
        ]
    )


_EXPORT_AXIS_KINDS = {
    ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED,
    ChangeKind.VAR_EXPORT_REMOVED_STILL_DECLARED,
}


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


def _symbols_of(result, kind) -> set[str]:
    return {c.symbol for c in result.changes if c.kind == kind}


# --------------------------------------------------------------------------
# The scenario
# --------------------------------------------------------------------------


def test_export_dropped_is_not_reported_as_a_source_removal():
    result = compare(_default_build(), _public_only_build())
    assert ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT not in _kinds(result)
    assert ChangeKind.FUNC_REMOVED not in _kinds(result)


def test_export_dropped_is_reported_on_the_export_axis():
    result = compare(_default_build(), _public_only_build())
    assert _symbols_of(
        result, ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED
    ) == {_ADD_MANGLED}


def test_export_dropped_is_still_an_abi_break():
    """The fix reclassifies the finding; it does not soften the verdict.
    A disappearing dynamic export breaks an already-linked consumer."""
    result = compare(_default_build(), _public_only_build())
    assert result.verdict == Verdict.BREAKING


def test_the_untouched_sibling_export_produces_nothing():
    result = compare(_default_build(), _public_only_build())
    for change in result.changes:
        assert change.symbol != _SEARCH_MANGLED


def test_reverse_direction_reports_no_source_addition_of_the_declaration():
    """The other direction: the export comes back. The declaration was
    never absent, so nothing may claim it was added to the source API."""
    result = compare(_public_only_build(), _default_build())
    assert ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED not in _kinds(result)
    assert ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT not in _kinds(result)


@pytest.mark.parametrize(
    "build", [_default_build, _public_only_build], ids=["default", "public-only"]
)
def test_each_side_compared_against_itself_is_clean(build):
    result = compare(build(), build())
    assert result.changes == []
    # NO_CHANGE, not COMPATIBLE: an identical pair is the strongest clean
    # verdict there is, and it is what a self-comparison must produce.
    assert result.verdict == Verdict.NO_CHANGE


# --------------------------------------------------------------------------
# Controls: the fix must not work by suppressing real findings
# --------------------------------------------------------------------------


def test_a_genuine_source_removal_is_still_reported():
    """Same shape as the scenario above, except the declaration really is
    gone from the new side's headers."""
    new = _snapshot(
        [
            _declared(_SEARCH, _SEARCH_MANGLED, exported=True),
            _declared(_EXPERIMENTAL, _EXPERIMENTAL_MANGLED, exported=True),
        ]
    )
    result = compare(_default_build(), new)
    assert ChangeKind.FUNC_REMOVED in _kinds(result)
    assert _symbols_of(result, ChangeKind.FUNC_REMOVED) == {_ADD_MANGLED}
    assert result.verdict == Verdict.BREAKING


def test_a_genuine_experimental_namespace_removal_is_still_reported():
    new = _snapshot(
        [
            _declared(_ADD, _ADD_MANGLED, exported=True),
            _declared(_SEARCH, _SEARCH_MANGLED, exported=True),
        ]
    )
    result = compare(_default_build(), new)
    assert ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT in _kinds(result)


def _polymorphic_base(*, extra_field: bool) -> RecordType:
    fields = [TypeField(name="a", type="int", offset_bits=64)]
    if extra_field:
        fields.append(TypeField(name="b", type="int", offset_bits=96))
    return RecordType(
        name="svs::Base",
        kind="class",
        size_bits=192 if extra_field else 128,
        fields=fields,
        vtable=["svs::Base::~Base", "svs::Base::run"],
        vptr_offset_bits=0,
    )


def test_a_public_vtable_or_layout_change_is_still_breaking():
    old = _snapshot(
        [_declared(_SEARCH, _SEARCH_MANGLED, exported=True)],
        [_polymorphic_base(extra_field=False)],
    )
    new = _snapshot(
        [_declared(_SEARCH, _SEARCH_MANGLED, exported=True)],
        [_polymorphic_base(extra_field=True)],
    )
    result = compare(old, new)
    assert result.verdict == Verdict.BREAKING
    assert result.changes


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_new_snapshot_round_trips_both_facts(tmp_path):
    path = tmp_path / "snap.json"
    save_snapshot(_public_only_build(), str(path))
    loaded = load_snapshot(str(path))
    by_name = {f.mangled: f for f in loaded.functions}
    assert by_name[_ADD_MANGLED].declared_fact == Fact.present(True)
    assert by_name[_ADD_MANGLED].exported_fact == Fact.present(False)
    assert by_name[_SEARCH_MANGLED].exported_fact == Fact.present(True)


def test_a_pre_bump_snapshot_loads_both_facts_as_unknown_and_is_unchanged(tmp_path):
    """The compatibility half: a snapshot written before schema v46 has
    neither key, so both facts must load as "no evidence" -- never as a
    ``False`` that would fabricate an export loss for every symbol -- and
    the comparison must produce exactly what it produced before the fix.
    """
    import json

    from abicheck.storage.sectioned_document import from_sectioned_document
    from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION

    def _strip(snapshot: AbiSnapshot, path):
        """Write *snapshot*, then rewrite it as a document that predates the
        two new keys -- `from_sectioned_document` first, so this reads the
        real payload rather than indexing a top-level key the ADR-063
        Phase 8 envelope moved (`snapshot_from_dict` accepts either shape,
        so writing the flat form back is a valid pre-v46 document).
        """
        save_snapshot(snapshot, str(path))
        raw = from_sectioned_document(json.loads(path.read_text()))
        for fn in raw.get("functions", []):
            fn.pop("declared_fact", None)
            fn.pop("exported_fact", None)
        raw["schema_version"] = SCHEMA_VERSION - 1
        path.write_text(json.dumps(raw))
        return load_snapshot(str(path))

    old = _strip(_default_build(), tmp_path / "old.json")
    new = _strip(_public_only_build(), tmp_path / "new.json")
    for fn in (*old.functions, *new.functions):
        assert not fn.declared_fact.is_present
        assert not fn.exported_fact.is_present

    result = compare(old, new)
    # Pre-fix behaviour, unchanged: with only the conflated `Visibility` to
    # go on, the export loss reads as a visibility change and the source
    # index still loses the declaration.
    assert ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED not in _kinds(result)
    assert ChangeKind.FUNC_VISIBILITY_CHANGED in _kinds(result)


# --------------------------------------------------------------------------
# Three answers, checked separately
# --------------------------------------------------------------------------


def test_verdict_gate_and_exit_code_each_say_breaking():
    """The semantic verdict, the gate decision and the process exit code
    are three separately configurable answers; a single assertion on one
    of them would not show that the reclassified finding still gates."""
    from abicheck.policy.severity import (
        SeverityConfig,
        SeverityLevel,
        compute_exit_code,
    )

    result = compare(_default_build(), _public_only_build())
    assert result.verdict == Verdict.BREAKING
    # Gate: the finding is scored, not dropped -- an unevaluated finding
    # would silently contribute nothing here.
    assert [c for c in result.changes if c.kind in _EXPORT_AXIS_KINDS]
    # Exit code, computed independently of the verdict, under a severity
    # configuration that treats an ABI break as an error.
    assert (
        compute_exit_code(
            result.changes,
            SeverityConfig(abi_breaking=SeverityLevel.ERROR),
            policy="strict_abi",
        )
        == 4
    )


def test_the_export_axis_kind_dedupes_against_a_plain_removal_of_the_same_symbol():
    """Must-merge: the two kinds describe one event seen with different
    evidence, so a second detector's plain `FUNC_REMOVED` for that very
    symbol must collapse onto it rather than double-report."""
    from abicheck.diff_helpers import make_change
    from abicheck.finding_identity import resolve_change_identity

    export_axis = make_change(
        ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED,
        symbol=_ADD_MANGLED,
        name=_ADD,
        old_value="public",
        new_value="hidden",
    )
    plain = make_change(
        ChangeKind.FUNC_REMOVED,
        symbol=_ADD_MANGLED,
        description=f"Public function removed: {_ADD}",
        old_value=_ADD,
    )
    assert resolve_change_identity(export_axis) == resolve_change_identity(plain)


def test_it_stays_distinct_from_the_same_kind_on_a_different_symbol():
    """Must-not-merge: the other half of the claim above, which
    "collapse everything" would also satisfy."""
    from abicheck.diff_helpers import make_change
    from abicheck.finding_identity import resolve_change_identity

    def _one(symbol: str, name: str):
        return make_change(
            ChangeKind.FUNC_EXPORT_REMOVED_STILL_DECLARED,
            symbol=symbol,
            name=name,
            old_value="public",
            new_value="hidden",
        )

    assert resolve_change_identity(_one(_ADD_MANGLED, _ADD)) != resolve_change_identity(
        _one(_SEARCH_MANGLED, _SEARCH)
    )

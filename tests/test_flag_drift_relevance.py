"""ENUM_SIZE_FLAG_CHANGED relevance to the compared surface (case103)."""

from __future__ import annotations

import pytest

from abicheck.buildsource.flag_drift_relevance import (
    MODULATION_REASON,
    demote_unexposed_enum_size_flag,
)
from abicheck.diff_helpers import make_change
from abicheck.model import AbiSnapshot, EnumMember, EnumType
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict


def _snap(*, headers: bool, enums: int) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib.so",
        version="1",
        enums=[
            EnumType(name=f"E{i}", members=[EnumMember(name="A", value=0)])
            for i in range(enums)
        ],
        from_headers=headers,
    )


def _flip() -> list:
    return [
        make_change(
            ChangeKind.ENUM_SIZE_FLAG_CHANGED, symbol="-fshort-enums", description="d"
        ),
        make_change(
            ChangeKind.CHAR_SIGNEDNESS_CHANGED,
            symbol="-funsigned-char",
            description="d",
        ),
    ]


@pytest.mark.parametrize("old_enums", [0, 1])
@pytest.mark.parametrize("new_enums", [0, 1])
@pytest.mark.parametrize("old_headers", [True, False])
@pytest.mark.parametrize("new_headers", [True, False])
def test_demoted_only_when_both_sides_established_enum_free(
    old_enums, new_enums, old_headers, new_headers
):
    changes = _flip()
    demote_unexposed_enum_size_flag(
        changes,
        _snap(headers=old_headers, enums=old_enums),
        _snap(headers=new_headers, enums=new_enums),
    )
    enum_flip, other = changes
    # Oracle: an enum-free surface needs evidence (headers) AND no enum, on both sides.
    demote = old_headers and new_headers and not old_enums and not new_enums
    assert (enum_flip.effective_verdict is Verdict.COMPATIBLE) is demote
    assert (enum_flip.modulation_reason == MODULATION_REASON) is demote
    # The finding is never dropped, and unrelated flag kinds are untouched.
    assert enum_flip.kind is ChangeKind.ENUM_SIZE_FLAG_CHANGED
    assert other.effective_verdict is None


def test_missing_side_keeps_risk():
    changes = _flip()
    demote_unexposed_enum_size_flag(changes, None, _snap(headers=True, enums=0))
    assert changes[0].effective_verdict is None


def test_inferred_headers_are_not_established_evidence():
    # A legacy document whose from_headers was inferred proves no header AST
    # ran, so its empty enum list is not evidence of no enums.
    changes = _flip()
    old = _snap(headers=True, enums=0)
    new = _snap(headers=True, enums=0)
    new.from_headers_inferred = True
    demote_unexposed_enum_size_flag(changes, old, new)
    assert changes[0].effective_verdict is None


def test_a_policy_verdict_is_not_overridden():
    changes = _flip()
    changes[0].effective_verdict = Verdict.API_BREAK
    demote_unexposed_enum_size_flag(
        changes, _snap(headers=True, enums=0), _snap(headers=True, enums=0)
    )
    assert changes[0].effective_verdict is Verdict.API_BREAK
    assert changes[0].modulation_reason is None


@pytest.mark.parametrize(
    ("dwarf_enums", "demoted"), [({}, True), ({"E": object()}, False)]
)
def test_a_dwarf_enum_vetoes_the_demotion(dwarf_enums, demoted):
    # An enum the headers do not show (in an internal struct) but DWARF does
    # still changes size under -fshort-enums.
    from abicheck.model.dwarf_facts import DwarfMetadata

    changes = _flip()
    old, new = _snap(headers=True, enums=0), _snap(headers=True, enums=0)
    for snap in (old, new):
        snap.declarations.debug_layout = DwarfMetadata(
            has_dwarf=True, enums=dict(dwarf_enums)
        )
    demote_unexposed_enum_size_flag(changes, old, new)
    assert (changes[0].effective_verdict is Verdict.COMPATIBLE) is demoted

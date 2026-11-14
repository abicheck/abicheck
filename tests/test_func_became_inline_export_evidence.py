"""``func_became_inline`` through ``checker.compare`` under each kind of
export evidence for the new side: a read table that still lists the symbol,
a read table that lost it, and no table at all.

Oracle (independent of the detector's wording table): the finding is always
a risk, never the break; a lost export is a break only where a read table
proves it; with no table neither "still exported" nor "removed" is claimed.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol

_SYM = "_Z6helperv"


def _snap(version: str, *, inline: bool, table: tuple[str, ...] | None) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libh.so.1",
        version=version,
        functions=[
            Function(
                name="helper",
                mangled=_SYM,
                return_type="int",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
                is_inline=inline,
            )
        ],
        from_headers=True,
    )
    if table is not None:
        snap.elf = ElfMetadata(
            soname="libh.so.1", symbols=[ElfSymbol(name=n) for n in table]
        )
    return snap


@pytest.mark.parametrize(
    ("old_table", "new_table", "claim", "forbidden"),
    [
        ((_SYM,), (_SYM,), "still exported", ("not assessed", "no longer")),
        (None, None, "not assessed", ("still exported", "no longer")),
        ((_SYM,), None, "not assessed", ("still exported", "no longer")),
    ],
)
def test_became_inline_is_a_risk_and_claims_only_what_the_table_shows(
    old_table, new_table, claim, forbidden
) -> None:
    result = compare(
        _snap("1", inline=False, table=old_table),
        _snap("2", inline=True, table=new_table),
    )
    (finding,) = [c for c in result.changes if c.kind is ChangeKind.FUNC_BECAME_INLINE]
    assert claim in finding.description
    assert not any(f in finding.description for f in forbidden)
    assert result.verdict is Verdict.COMPATIBLE_WITH_RISK


def test_a_read_table_that_lost_the_symbol_is_the_break() -> None:
    result = compare(
        _snap("1", inline=False, table=(_SYM,)),
        _snap("2", inline=True, table=("_Z5otherv",)),
    )
    assert result.verdict is Verdict.BREAKING
    inline = [c for c in result.changes if c.kind is ChangeKind.FUNC_BECAME_INLINE]
    assert all("still exported" not in c.description for c in inline)

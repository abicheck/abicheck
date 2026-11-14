"""The undocumented-export metric counts exports no header declares.

Bug class: a metric counted from the wrong population. A header dump makes no
declaration for an export no header declares, so counting undocumented
exports from the declaration list (``EXPORT_ONLY`` declarations) found none
on every real header-backed snapshot, and ``undocumented_export_ratio_increased``
could never be reported (found by the abiforge ``sources`` variant,
``src_undocumented_export_added``). The metric now accounts the export table.

Oracle: the expected counts are written out per case from the snapshot's own
export table and public declarations, not computed by the accounting helper.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot, Function
from abicheck.model.elf_facts import ElfSymbol, SymbolBinding, SymbolType
from abicheck.model.vocabulary import ScopeOrigin, Visibility
from abicheck.policy.public_surface_query import PublicSurfaceQuery
from abicheck.surface_graph import compute_surface_metrics


def _snap(public: list[str], undeclared: list[str]) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libu.so",
        version="1",
        functions=[
            Function(
                name=n,
                mangled=n,
                return_type="int",
                visibility=Visibility.PUBLIC,
                source_header="u.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
            for n in public
        ],
        from_headers=True,
    )
    snap.elf = ElfMetadata(
        symbols=[
            ElfSymbol(
                name=n,
                binding=SymbolBinding.GLOBAL,
                sym_type=SymbolType.FUNC,
            )
            for n in [*public, *undeclared]
        ]
    )
    return snap


@pytest.mark.parametrize(
    ("public", "undeclared", "expected_undocumented"),
    [
        (["u_open", "u_close"], [], 0),
        (["u_open", "u_close"], ["u_secret"], 1),
        (["u_open"], ["u_secret", "u_hidden_helper", "u_debug"], 3),
        ([], ["u_secret"], None),  # no header provenance: legacy count (0)
    ],
)
def test_undocumented_exports_are_the_undeclared_exports(
    public: list[str], undeclared: list[str], expected_undocumented: int | None
) -> None:
    snap = _snap(public, undeclared)
    m = compute_surface_metrics(
        snap, public_entity_ids=PublicSurfaceQuery.resolve(snap, None)
    )
    if expected_undocumented is None:
        assert m.undocumented_exports == 0
        return
    total = len(public) + len(undeclared)
    assert m.undocumented_exports == expected_undocumented
    assert m.undocumented_export_ratio == pytest.approx(expected_undocumented / total)


def test_a_new_undeclared_export_raises_the_reported_ratio() -> None:
    old = _snap(["u_open", "u_close"], [])
    new = _snap(["u_open", "u_close"], ["u_secret"])
    result = compare(old, new, surface_metrics=True)
    assert "undocumented_export_ratio_increased" in {
        c.kind.value for c in result.changes
    }
    # The reverse direction lowers the share: no increase is reported.
    back = compare(new, old, surface_metrics=True)
    assert "undocumented_export_ratio_increased" not in {
        c.kind.value for c in back.changes
    }

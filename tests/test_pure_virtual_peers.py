"""``PureVirtualPeers``: the new side's pure declaration of an old virtual.

The peer stands in for a declaration the export narrowing dropped (a pure
virtual has no definition, so no symbol, and export evidence says nothing
about it). A declaration the side places outside its public contract is no
peer.
"""

from __future__ import annotations

import pytest

from abicheck.compare.pure_virtual_peers import PureVirtualPeers
from abicheck.extract.surface_fact_producers import debug_info_surface_facts
from abicheck.model import AbiSnapshot, Function, Visibility

M = "_ZNK5Shape4kindEv"


def _fn(
    *,
    virtual: bool = True,
    pure: bool = False,
    vis: Visibility = Visibility.PUBLIC,
    debug_info: bool = False,
) -> Function:
    # A DWARF pure declaration: no symbol, contract membership not established.
    facts = debug_info_surface_facts(exported=False) if debug_info else {}
    return Function(
        name="Shape::kind",
        mangled=M,
        return_type="int",
        is_virtual=virtual,
        is_pure_virtual=pure,
        visibility=vis,
        **facts,
    )


def _side(*functions: Function) -> AbiSnapshot:
    return AbiSnapshot(library="lib", version="2", functions=list(functions))


@pytest.mark.parametrize(
    ("old", "new", "paired"),
    [
        pytest.param(_fn(), _fn(pure=True), True, id="public_pure_peer"),
        # DWARF's in-class declaration: no symbol, contract not established.
        pytest.param(
            _fn(), _fn(pure=True, debug_info=True), True, id="dwarf_pure_peer"
        ),
        pytest.param(
            _fn(),
            _fn(pure=True, vis=Visibility.HIDDEN),
            False,
            id="out_of_contract_peer",
        ),
        pytest.param(_fn(), _fn(), False, id="still_defined"),
        pytest.param(_fn(virtual=False), _fn(pure=True), False, id="old_not_virtual"),
        pytest.param(_fn(), None, False, id="no_declaration"),
    ],
)
def test_a_peer_is_an_in_contract_pure_declaration_of_an_old_virtual(
    old: Function, new: Function | None, paired: bool
) -> None:
    side = _side(*([] if new is None else [new]))
    peer = PureVirtualPeers(side).of(old)
    assert (peer is not None) is paired
    if paired:
        assert peer is side.function_map[M]

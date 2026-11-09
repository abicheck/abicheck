"""``PureVirtualPeers``: the new side's pure declaration of an old virtual.

The peer stands in for a declaration the export narrowing dropped (a pure
virtual has no definition, so no symbol, and export evidence says nothing
about it). A declaration the side places outside its public contract is no
peer.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.compare.pure_virtual_peers import PureVirtualPeers
from abicheck.extract.surface_fact_producers import debug_info_surface_facts
from abicheck.model import AbiSnapshot, Function, Visibility

M = "_ZNK5Shape4kindEv"


def _fn(
    *,
    mangled: str = M,
    virtual: bool = True,
    pure: bool = False,
    vis: Visibility = Visibility.PUBLIC,
    debug_info: bool = False,
) -> Function:
    # A DWARF pure declaration: no symbol, contract membership not established.
    facts = debug_info_surface_facts(exported=False) if debug_info else {}
    return Function(
        name="Shape::kind",
        mangled=mangled,
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
        # A pure declaration under any other name is never the peer: the
        # const overload, another method, the same method of another record.
        *(
            pytest.param(_fn(), _fn(mangled=other, pure=True), False, id=case)
            for case, other in (
                ("const_overload", "_ZN5Shape4kindEv"),
                ("other_method", "_ZNK5Shape4areaEv"),
                ("other_record", "_ZNK6Circle4kindEv"),
            )
        ),
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


def test_a_lookup_answers_the_same_peer_every_time() -> None:
    pure = _fn(pure=True)
    side = _side(_fn(mangled="_ZNK5Shape4areaEv", pure=True), pure)
    peers = PureVirtualPeers(side)
    assert [peers.of(_fn()) for _ in range(3)] == [pure, pure, pure]
    assert peers.of(_fn(mangled="_ZNK5Shape4sizeEv")) is None


# ── the lookup's contract as invariants over generated sides ────────────────

_NAMES = (M, "_ZN5Shape4kindEv", "_ZNK5Shape4areaEv", "_ZNK6Circle4kindEv")
_VIS = (Visibility.PUBLIC, Visibility.HIDDEN)


@st.composite
def _decls(draw: st.DrawFn) -> list[tuple[Function, bool]]:
    """A side: at most one declaration per mangled name (the function map's
    key), each pure or not, public or hidden, with or without the DWARF
    surface facts; paired with whether it is confirmed out of contract."""
    names = draw(st.lists(st.sampled_from(_NAMES), unique=True, max_size=4))
    side = []
    for name in names:
        vis, debug = draw(st.sampled_from(_VIS)), draw(st.booleans())
        fn = _fn(mangled=name, pure=draw(st.booleans()), vis=vis, debug_info=debug)
        # Visibility places a declaration out of contract only where no
        # surface fact speaks; DWARF's facts leave membership unestablished.
        side.append((fn, vis is Visibility.HIDDEN and not debug))
    return side


def _functions(side: list[tuple[Function, bool]]) -> list[Function]:
    return [fn for fn, _ in side]


def _expected(old: Function, side: list[tuple[Function, bool]]) -> Function | None:
    """The oracle, read off the generated side: the one pure declaration
    under *old*'s exact mangled name not placed out of contract, when *old*
    is virtual."""
    if not old.is_virtual:
        return None
    return next(
        (
            f
            for f, out in side
            if f.mangled == old.mangled and f.is_pure_virtual and not out
        ),
        None,
    )


class TestPureVirtualPeerProperties:
    @given(
        side=_decls(),
        name=st.sampled_from(_NAMES),
        virtual=st.booleans(),
    )
    def test_the_peer_is_exactly_the_in_contract_pure_namesake(
        self, side: list[tuple[Function, bool]], name: str, virtual: bool
    ) -> None:
        old = _fn(mangled=name, virtual=virtual)
        peers = PureVirtualPeers(_side(*_functions(side)))
        assert peers.of(old) is _expected(old, side)

    @given(side=_decls(), names=st.lists(st.sampled_from(_NAMES), max_size=6))
    def test_lookups_do_not_depend_on_order_or_repetition(
        self, side: list[tuple[Function, bool]], names: list[str]
    ) -> None:
        # One instance asked in sequence answers as a fresh instance per ask.
        snap = _side(*_functions(side))
        shared = PureVirtualPeers(snap)
        olds = [_fn(mangled=n) for n in names]
        assert [shared.of(o) for o in olds] == [
            PureVirtualPeers(snap).of(o) for o in olds
        ]

    @given(side=_decls(), name=st.sampled_from(_NAMES))
    def test_a_peer_never_carries_another_name(
        self, side: list[tuple[Function, bool]], name: str
    ) -> None:
        peer = PureVirtualPeers(_side(*_functions(side))).of(_fn(mangled=name))
        assert peer is None or (peer.mangled == name and peer.is_pure_virtual)

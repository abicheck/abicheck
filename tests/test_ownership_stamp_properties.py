"""``extract.ownership_stamp.stamp_ownership``: one classification per
declaration, independent of visiting order (ADR-075 D2).

The owner oracle is written here, independently of ``extract.ownership``:
the longest configured root (as a path prefix on whole segments) decides;
no root means ``unresolved``. The generated tree never touches a system
prefix, so the system heuristic cannot interfere.
"""

from __future__ import annotations

import random

from hypothesis import given, settings, strategies as st

from abicheck.extract.ownership_stamp import stamp_ownership
from abicheck.model import AbiSnapshot, Function, RecordType
from abicheck.model.extraction_scope import ownership_of
from abicheck.model.ownership_rules import DependencyRoots, OwnershipRequest, OwnershipRules

_BASE = "/work/proj"
_DIRS = ("inc", "inc/a", "inc/a/vendor", "inc/b", "third", "third/x", "other")


def _oracle_owner(path: str, roots: dict[str, str]) -> str:
    best, best_len = "unresolved", -1
    for root, owner in roots.items():
        root_parts = root.split("/")
        if path.split("/")[: len(root_parts)] == root_parts and len(root_parts) > best_len:
            best, best_len = owner, len(root_parts)
    return best


@st.composite
def _cases(draw):
    chosen = draw(st.lists(st.sampled_from(_DIRS), min_size=1, max_size=4, unique=True))
    roots: dict[str, str] = {}
    for i, d in enumerate(chosen):
        roots[d] = "target" if i == 0 or draw(st.booleans()) else f"dependency:d{i}"
    files = draw(
        st.lists(
            st.tuples(st.sampled_from(_DIRS), st.sampled_from(["h1.h", "h2.hpp"])),
            min_size=1,
            max_size=12,
        )
    )
    seed = draw(st.integers(0, 2**16))
    return roots, [f"{d}/{name}" for d, name in files], seed


def _request(roots: dict[str, str], order: list[str]) -> OwnershipRequest:
    targets = [f"{_BASE}/{r}" for r in order if roots[r] == "target"]
    deps: dict[str, list[str]] = {}
    for r in order:
        if roots[r] != "target":
            deps.setdefault(roots[r].split(":", 1)[1], []).append(f"{_BASE}/{r}")
    return OwnershipRequest(
        OwnershipRules(
            target_roots=tuple(targets),
            dependencies=tuple(DependencyRoots(n, tuple(rs)) for n, rs in deps.items()),
        ),
        project_root=_BASE,
    )


def _snapshot(files: list[str]) -> AbiSnapshot:
    fns = [
        Function(name=f"f{i}", mangled=f"f{i}", return_type="int",
                 source_location=f"{_BASE}/{path}:{i + 1}")
        for i, path in enumerate(files)
    ]
    types = [
        RecordType(name=f"T{i}", kind="struct", source_location=f"{_BASE}/{path}:1")
        for i, path in enumerate(files)
    ]
    return AbiSnapshot(library="l", version="1", functions=fns, types=types, from_headers=True)


@settings(max_examples=150, deadline=None)
@given(_cases())
def test_owner_matches_the_oracle_and_ignores_every_order(case) -> None:
    roots, files, seed = case
    rng = random.Random(seed)
    order_a = list(roots)
    order_b = list(roots)
    rng.shuffle(order_b)

    first = _snapshot(files)
    stamp_ownership(first, _request(roots, order_a))

    second = _snapshot(files)
    second.functions.reverse()
    rng.shuffle(second.types)
    stamp_ownership(second, _request(roots, order_b))

    def by_name(snap: AbiSnapshot) -> dict[str, tuple[str, str]]:
        out = {}
        for d in (*snap.functions, *snap.types):
            decision = ownership_of(d)
            assert decision is not None  # every declaration is classified
            out[d.name] = (decision.owner, decision.contract)
        return out

    a, b = by_name(first), by_name(second)
    assert a == b
    for i, path in enumerate(files):
        expected = _oracle_owner(path, roots)
        assert a[f"f{i}"][0] == expected, (path, roots)
        assert a[f"T{i}"][0] == expected
    assert first.extraction_scope is not None and second.extraction_scope is not None
    assert first.extraction_scope.fingerprint == second.extraction_scope.fingerprint


def test_oracle_generator_reaches_every_outcome() -> None:
    """Vacuity guard: the strategy's shapes produce target, dependency and
    unresolved owners, and a nested root that beats its parent."""
    roots = {"inc": "target", "inc/a/vendor": "dependency:v"}
    assert _oracle_owner("inc/a/vendor/h1.h", roots) == "dependency:v"
    assert _oracle_owner("inc/b/h1.h", roots) == "target"
    assert _oracle_owner("other/h1.h", roots) == "unresolved"


def test_recorded_roots_are_relative_to_the_project_root() -> None:
    snap = _snapshot(["inc/h1.h"])
    stamp_ownership(
        snap,
        OwnershipRequest(
            OwnershipRules(target_roots=(f"{_BASE}/inc/", "/elsewhere/include")),
            project_root=_BASE,
        ),
    )
    assert snap.extraction_scope is not None
    assert snap.extraction_scope.ownership_rules.target_roots == (
        "inc",
        "/elsewhere/include",
    )
    decision = ownership_of(snap.functions[0])
    assert decision is not None and decision.rule_id == "target_root:inc"


def test_builtin_is_toolchain_owned_whatever_file_declares_it() -> None:
    fn = Function(name="__atomic_load_n", mangled="__atomic_load_n", return_type="int",
                  source_location=f"{_BASE}/inc/h1.h:3")
    fn.is_compiler_generated = True
    snap = AbiSnapshot(library="l", version="1", functions=[fn], from_headers=True)
    stamp_ownership(snap, OwnershipRequest(OwnershipRules(target_roots=(f"{_BASE}/inc",))))
    decision = ownership_of(fn)
    assert decision is not None
    assert (decision.owner, decision.rule_id) == ("toolchain", "builtin")


def test_castxml_leaf_name_gets_its_scope_from_the_linker_name() -> None:
    """castxml records ``lib::detail::hidden`` as ``hidden``; the private
    namespace rule must still see ``lib::detail`` (found through a real dump,
    ``tests/test_ownership_dump_integration.py``)."""
    hidden = Function(name="hidden", mangled="_ZN3lib6detail6hiddenEv", return_type="int",
                      source_location=f"{_BASE}/inc/h1.h:1")
    shown = Function(name="shown", mangled="_ZN3lib5shownEv", return_type="int",
                     source_location=f"{_BASE}/inc/h1.h:2")
    c_api = Function(name="c_api", mangled="c_api", return_type="int",
                     source_location=f"{_BASE}/inc/h1.h:3")
    snap = AbiSnapshot(library="l", version="1", functions=[hidden, shown, c_api],
                       from_headers=True)
    stamp_ownership(
        snap,
        OwnershipRequest(
            OwnershipRules(target_roots=(f"{_BASE}/inc",), private_namespaces=("lib::detail",))
        ),
    )
    contracts = {f.name: ownership_of(f).contract for f in snap.functions}  # type: ignore[union-attr]
    assert contracts == {"hidden": "private", "shown": "public", "c_api": "public"}

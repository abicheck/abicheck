"""ADR-093: a declared observable_at must observe every emission it is paired with.

The invariant: if the compare path emits kind K for a run with evidence
configuration C on platform P, then K's ``observable_at`` (the entries whose
condition admits P) must observe C. Emission proves observability, so a
declared value that does not cover an observed emission is wrong.

The oracle is the actual emission of the real ``compare`` path over synthetic
pairs in the shape the dumper produces, not the seeder's decision table.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.dumper import (
    _itanium_export_function,
    _itanium_export_variable,
    _msvc_export_function,
)
from abicheck.model import AbiSnapshot, Function, Param, ScopeOrigin, Visibility
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import UNCLASSIFIED, Platform

PLATFORMS = ["elf", "pe", "macho"]
BINARY = ("B",)
BINARY_AND_HEADERS = ("B", "H")


def _export_snapshot(
    version: str, platform: str, functions: list[str], variables: list[str]
) -> AbiSnapshot:
    """Export-table-only snapshot: the shape the dumper builds in elf_only_mode."""
    make_fn = _msvc_export_function if platform == "pe" else _itanium_export_function
    return AbiSnapshot(
        library="libsynth",
        version=version,
        platform=platform,
        elf_only_mode=True,
        functions=[make_fn(n) for n in functions],
        variables=[_itanium_export_variable(n) for n in variables],
    )


def _header_function(name: str, params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
        return_type="void",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _header_snapshot(
    version: str, platform: str, functions: list[Function]
) -> AbiSnapshot:
    """Header-backed snapshot: declarations parsed from public headers."""
    return AbiSnapshot(
        library="libsynth",
        version=version,
        platform=platform,
        from_headers=True,
        functions=functions,
    )


def _export_pair(platform: str) -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _export_snapshot(
        "1",
        platform,
        ["_Z3foov", "_Z3barv", "_Z3bazi"],
        ["_ZN1v1xE", "_ZN1v1yE"],
    )
    new = _export_snapshot(
        "2",
        platform,
        ["_Z3barv", "_Z3quxv"],
        ["_ZN1v1yE", "_ZN1v1zE"],
    )
    return old, new


def _header_pair(platform: str) -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _header_snapshot(
        "1",
        platform,
        [_header_function("foo"), _header_function("bar", ("int",))],
    )
    new = _header_snapshot(
        "2",
        platform,
        [_header_function("bar", ("long",)), _header_function("qux")],
    )
    return old, new


def _declared(kind: str) -> str:
    obs = REGISTRY.entries[kind].observable_at
    return "UNCLASSIFIED" if obs is UNCLASSIFIED else repr(obs)


def _observes_on(kind: str, platform: str, config: tuple[str, ...]) -> bool:
    """The oracle-free check: some entry whose condition admits *platform* observes *config*."""
    obs = REGISTRY.entries[kind].observable_at
    applicable = [
        o
        for o in obs
        if o.condition is None or o.condition.matches(Platform(platform), None)
    ]
    return any(o.observes(config) for o in applicable)


def _assert_emissions_observed(
    old: AbiSnapshot, new: AbiSnapshot, platform: str, config: tuple[str, ...]
) -> None:
    emitted = {c.kind.value for c in compare(old, new).changes}
    assert emitted, "the pair emitted nothing, so it proves nothing"
    checked = [
        k
        for k in sorted(emitted)
        if REGISTRY.entries[k].observable_at is not UNCLASSIFIED
    ]
    # An all-UNCLASSIFIED emission set would pass vacuously; refuse that.
    assert checked, (
        f"no emitted kind on {platform} carries a declaration: {sorted(emitted)}"
    )
    failures = [
        f"{kind}: emitted on {platform} at {{{','.join(config)}}}, "
        f"declared {_declared(kind)}"
        for kind in checked
        if not _observes_on(kind, platform, config)
    ]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_export_table_only_emissions_are_observed_by_binary_alone(
    platform: str,
) -> None:
    old, new = _export_pair(platform)
    _assert_emissions_observed(old, new, platform, BINARY)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_header_backed_emissions_are_observed_by_binary_and_headers(
    platform: str,
) -> None:
    old, new = _header_pair(platform)
    _assert_emissions_observed(old, new, platform, BINARY_AND_HEADERS)

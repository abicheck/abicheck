"""ADR-093 amendment: the ``arch`` axis of ``ObservabilityCondition``.

A kind whose emission depends on the processor (struct-return and struct-layout
rules are LP64 x86-64 facts) is declared with ``arch`` in its condition. These
tests pin the axis's matching rules and the one x86-64-conditioned entry on
main: an x86-64 kind is not expected on an aarch64 cell.
"""

from __future__ import annotations

import pytest

from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.model.change_catalog.observability import (
    Arch,
    Language,
    ObservabilityCondition,
    Platform,
)


def _expected_on(kind: str, platform: Platform | None, arch: Arch | None) -> bool:
    """Whether some declared entry of *kind* applies to a (platform, arch) cell."""
    return any(
        o.condition is None or o.condition.matches(platform, None, arch)
        for o in REGISTRY.entries[kind].observable_at
    )


def test_arch_axis_admits_its_own_arch_and_refuses_another() -> None:
    cond = ObservabilityCondition(arch=frozenset({Arch.X86_64}))
    assert cond.matches(None, None, Arch.X86_64)
    assert not cond.matches(None, None, Arch.AARCH64)


def test_unknown_arch_matches_so_a_run_without_arch_is_not_hidden() -> None:
    cond = ObservabilityCondition(arch=frozenset({Arch.X86_64}))
    assert cond.matches(None, None, None)


def test_matches_keeps_its_two_argument_spelling_for_platform_and_language() -> None:
    # Existing call sites pass (platform, language); the arch axis defaults open.
    cond = ObservabilityCondition(
        platform=frozenset({Platform.ELF}), language=frozenset({Language.CXX})
    )
    assert cond.matches(Platform.ELF, Language.CXX)
    assert not cond.matches(Platform.PE, Language.CXX)


@pytest.mark.parametrize(
    ("left", "right", "overlap"),
    [
        (Arch.X86_64, Arch.X86_64, True),
        (Arch.X86_64, Arch.AARCH64, False),
    ],
)
def test_arch_conditions_overlap_only_on_a_shared_arch(
    left: Arch, right: Arch, overlap: bool
) -> None:
    a = ObservabilityCondition(arch=frozenset({left}))
    b = ObservabilityCondition(arch=frozenset({right}))
    assert a.overlaps(b) is overlap
    assert a.overlaps(None)


def test_arch_only_condition_is_a_valid_restriction() -> None:
    cond = ObservabilityCondition(arch=frozenset({Arch.AARCH64}))
    assert cond.matches(Platform.ELF, Language.C, Arch.AARCH64)
    assert not cond.matches(Platform.ELF, Language.C, Arch.X86_64)


def test_empty_arch_axis_is_refused() -> None:
    with pytest.raises(ValueError):
        ObservabilityCondition(arch=frozenset())


def test_struct_return_convention_is_expected_on_elf_x86_64_only() -> None:
    kind = "struct_return_convention_changed"
    assert _expected_on(kind, Platform.ELF, Arch.X86_64)
    # The kind is an LP64 x86-64 fact: an aarch64 cell does not expect it.
    assert not _expected_on(kind, Platform.ELF, Arch.AARCH64)
    # A run that did not state its arch is not hidden from the kind.
    assert _expected_on(kind, Platform.ELF, None)

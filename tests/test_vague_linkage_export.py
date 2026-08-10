# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A dropped *weak* export is not the same event as a dropped strong one.

``RecordType``-style absence reasoning applied to symbols: the export table
alone cannot tell a removed strong definition from a removed COMDAT copy the
consumer already carries. The language requires every translation unit using
an inline function, a template instantiation, or an implicit special member
to define it for itself, so when such an export disappears while the new
headers still define it inline, a consumer built against those headers keeps
resolving against its own copy.

Tested at the predicate *and* through ``compare()``: two detectors in two
modules observe this same event (``symbol_linkage.check_removed_function``
and ``diff_platform``'s ELF fallback), and a predicate-level test alone
cannot see one of them re-reporting the symbol at BREAKING and dominating
the verdict — which is exactly what happened on the first attempt.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Verdict, compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, ElfBinding, Function, Visibility
from abicheck.symbol_linkage import vague_linkage_export_dropped

MANGLED = "_ZN2ns1fEv"


def _fn(
    *,
    binding: ElfBinding | None = None,
    inline: bool = False,
    visibility: Visibility = Visibility.PUBLIC,
) -> Function:
    return Function(
        name="ns::f",
        mangled=MANGLED,
        return_type="void",
        visibility=visibility,
        is_inline=inline,
        elf_binding=binding,
    )


def _snap(funcs: list[Function], exported: list[str]) -> AbiSnapshot:
    """A snapshot whose ELF export set is what narrows the public function map.

    Without a populated ``elf`` the narrowing in ``_public_functions`` does not
    run at all, so a snapshot built without one never reaches the removal path
    this module is about.
    """
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=funcs,
        elf=ElfMetadata(
            soname="libx.so.1",
            symbols=[
                ElfSymbol(name=n, binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC)
                for n in exported
            ],
        ),
    )


#: The new side: the export is gone, the header still defines the entity inline.
def _new_side() -> AbiSnapshot:
    return _snap([_fn(inline=True)], ["_Z5otherv"])


class TestThePredicate:
    def test_weak_and_inline_on_both_sides_is_a_carried_copy(self) -> None:
        assert vague_linkage_export_dropped(
            _fn(binding=ElfBinding.WEAK, inline=True), _fn(inline=True)
        )

    def test_an_out_of_line_weak_symbol_is_a_real_break(self) -> None:
        """WEAK does not imply vague linkage. An ordinary out-of-line function
        marked ``__attribute__((weak))`` is WEAK too, and a consumer compiled
        against *that* declaration holds only an undefined dynamic reference --
        no copy of its own. Making it inline on the new side and dropping the
        export breaks that already-compiled consumer at load time, so only the
        old side can establish the demotion (Codex review)."""
        assert not vague_linkage_export_dropped(
            _fn(binding=ElfBinding.WEAK, inline=False), _fn(inline=True)
        )

    def test_a_strong_definition_is_a_real_removal(self) -> None:
        assert not vague_linkage_export_dropped(
            _fn(binding=ElfBinding.GLOBAL), _fn(inline=True)
        )

    def test_unique_is_strong_not_vague(self) -> None:
        """STB_GNU_UNIQUE exists precisely to stop each consumer using its own
        copy, so folding it into WEAK would invert its meaning."""
        assert not vague_linkage_export_dropped(
            _fn(binding=ElfBinding.UNIQUE), _fn(inline=True)
        )

    def test_an_uncaptured_binding_is_never_read_as_weak(self) -> None:
        """None means non-ELF, header-only, or a pre-v21 snapshot. Absence of
        evidence must not demote a real removal."""
        assert not vague_linkage_export_dropped(_fn(), _fn(inline=True))

    def test_gone_from_the_headers_too_is_a_real_removal(self) -> None:
        assert not vague_linkage_export_dropped(_fn(binding=ElfBinding.WEAK), None)

    def test_no_longer_inline_is_a_real_removal(self) -> None:
        """Demoted from inline to an out-of-line definition elsewhere: the
        consumer has no copy of its own to fall back on."""
        assert not vague_linkage_export_dropped(
            _fn(binding=ElfBinding.WEAK), _fn(inline=False)
        )


class TestThroughCompare:
    def test_the_demoted_event_is_reported_once_as_a_risk(self) -> None:
        result = compare(
            _snap([_fn(binding=ElfBinding.WEAK, inline=True)], [MANGLED]), _new_side()
        )
        kinds = sorted(c.kind.value for c in result.changes)
        assert kinds == ["func_export_dropped_inline_available"], kinds
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_the_elf_fallback_detector_does_not_re_report_it(self) -> None:
        """The regression that a predicate-only test could not see: the ELF
        fallback fired BREAKING on the same symbol, both double-reporting it
        and dominating the verdict, undoing the demotion entirely."""
        result = compare(
            _snap([_fn(binding=ElfBinding.WEAK, inline=True)], [MANGLED]), _new_side()
        )
        assert not [
            c for c in result.changes if c.kind.value == "func_deleted_elf_fallback"
        ]

    @pytest.mark.parametrize("binding", [ElfBinding.GLOBAL, ElfBinding.UNIQUE, None])
    def test_every_non_vague_binding_still_breaks(
        self, binding: ElfBinding | None
    ) -> None:
        result = compare(_snap([_fn(binding=binding)], [MANGLED]), _new_side())
        kinds = {c.kind.value for c in result.changes}
        assert "func_removed" in kinds, kinds
        assert "func_export_dropped_inline_available" not in kinds
        assert result.verdict is Verdict.BREAKING

    def test_a_weak_export_that_simply_vanished_still_breaks(self) -> None:
        """No declaration left on the new side at all — nothing says a
        consumer has its own copy."""
        result = compare(
            _snap([_fn(binding=ElfBinding.WEAK, inline=True)], [MANGLED]),
            _snap([], ["_Z5otherv"]),
        )
        assert result.verdict is Verdict.BREAKING


class TestTheEvidenceRoundTrips:
    def test_the_binding_survives_serialization(self) -> None:
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = _snap([_fn(binding=ElfBinding.WEAK, inline=True)], [MANGLED])
        loaded = snapshot_from_dict(snapshot_to_dict(snap))
        assert loaded.functions[0].elf_binding is ElfBinding.WEAK

    def test_a_pre_v21_snapshot_loads_as_not_captured(self) -> None:
        """The field is absent on an older snapshot, which must load as None
        so the demotion stays inert rather than firing on schema evolution."""
        from abicheck.serialization import snapshot_from_dict

        loaded = snapshot_from_dict(
            {
                "library": "libx.so",
                "version": "1",
                "functions": [
                    {"name": "ns::f", "mangled": MANGLED, "return_type": "void"}
                ],
            }
        )
        assert loaded.functions[0].elf_binding is None

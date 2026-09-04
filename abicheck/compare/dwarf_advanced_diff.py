# Copyright 2026 Nikolay Petrov
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

"""Diff logic for :class:`~abicheck.model.dwarf_facts.AdvancedDwarfMetadata`,
split out of ``dwarf_advanced.py`` to keep that module under the architecture
debt-no-growth ceiling (ADR-061), and placed directly under its canonical
owner package: matching two already-parsed sides and identifying a raw
change is a ``compare/`` responsibility, not a flat-root addition
(``dwarf_advanced.py`` stays classified ``extract``, so it may not import
back from here -- ADR-061's dependency direction only allows
``extract -> model, storage`` -- which is why the diff functions moved
rather than staying a re-exported facade in ``dwarf_advanced.py``).

Operates purely on two already-parsed ``AdvancedDwarfMetadata`` objects --
no DWARF-parsing internals -- so it has no dependency on
``dwarf_advanced.py`` at all. ``checker.py`` (``compare`` family) imports
``diff_advanced_dwarf`` directly from here for monkeypatching, and
``tests/test_build_source_pack.py``/``tests/test_changekind_coverage.py``/
``tests/test_sprint4_dwarf_advanced.py`` import the pieces they need the
same way.
"""

from __future__ import annotations

from ..model.dwarf_facts import AdvancedDwarfMetadata


def _diff_calling_conventions(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> tuple[list[tuple[str, str, str, str | None, str | None]], set[str]]:
    """Diff explicit DW_AT_calling_convention. Returns (results, already_reported_cc)."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_cc_keys = set(old_meta.calling_conventions)
    new_cc_keys = set(new_meta.calling_conventions)
    for fname in sorted(old_cc_keys & new_cc_keys):
        old_cc = old_meta.calling_conventions[fname]
        new_cc = new_meta.calling_conventions[fname]
        if old_cc != new_cc:
            results.append(
                (
                    "calling_convention_changed",
                    fname,
                    f"Calling convention changed: {fname} ({old_cc} → {new_cc})",
                    old_cc,
                    new_cc,
                )
            )
    already_reported_cc = {
        fname
        for fname in (old_cc_keys & new_cc_keys)
        if old_meta.calling_conventions[fname] != new_meta.calling_conventions[fname]
    }
    return results, already_reported_cc


def _diff_callee_saved_regs(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
    already_reported_cc: set[str],
) -> tuple[list[tuple[str, str, str, str | None, str | None]], set[str]]:
    """Diff ELF CFI callee-saved fingerprint. Returns (results, updated already_reported_cc)."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_saved_keys = set(old_meta.callee_saved_regs)
    new_saved_keys = set(new_meta.callee_saved_regs)
    already_reported_cc = set(already_reported_cc)
    _MS_ABI_MARKERS = frozenset(("rdi", "rsi"))
    for fname in sorted((old_saved_keys & new_saved_keys) - already_reported_cc):
        old_saved = old_meta.callee_saved_regs[fname]
        new_saved = new_meta.callee_saved_regs[fname]
        if old_saved != new_saved:
            old_has_ms_hint = bool(old_saved & _MS_ABI_MARKERS)
            new_has_ms_hint = bool(new_saved & _MS_ABI_MARKERS)
            if old_has_ms_hint == new_has_ms_hint:
                continue
            results.append(
                (
                    "calling_convention_changed",
                    fname,
                    f"Calling convention changed (ELF CFI fallback): {fname} "
                    f"(saved regs: {sorted(old_saved)} → {sorted(new_saved)}) "
                    f"(ms_abi/sysv_abi drift inferred from CFI saved regs)",
                    ",".join(sorted(old_saved)),
                    ",".join(sorted(new_saved)),
                )
            )
            already_reported_cc.add(fname)
    return results, already_reported_cc


#: SysV AMD64 returns a trivial aggregate in registers only when it fits in two
#: eightbytes (<= 16 bytes); larger aggregates are returned via a hidden pointer
#: regardless of triviality. Used to gate the return-convention classification.
_SYSV_MAX_REGISTER_RETURN_BYTES = 16

#: Architectures whose by-value aggregate-return rules match the SysV AMD64
#: model encoded in ``_returns_in_registers`` (trivial-for-calls AND <= 16
#: bytes AND no unaligned member → registers, else hidden sret pointer). The
#: register<->sret *convention-flip* classification is only sound for these.
#: Other ABIs use different rules — an AArch64 HFA such as ``struct {double
#: a,b,c,d;}`` is returned in vector registers despite being 32 bytes; i386
#: returns every aggregate via memory — so a triviality flip there is just a
#: generic value-ABI change, not a convention flip. An empty/unknown arch is
#: treated as SysV AMD64 to preserve behaviour for arch-less mocks/snapshots.
_SYSV_AMD64_RETURN_ARCHES = frozenset({"x86_64", "x64", ""})


def _sysv_amd64_return_model(old_arch: str, new_arch: str) -> bool:
    """Whether both sides use the SysV-AMD64 aggregate-return model (or unknown)."""
    return (
        old_arch in _SYSV_AMD64_RETURN_ARCHES and new_arch in _SYSV_AMD64_RETURN_ARCHES
    )


def _diff_value_abi_traits(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
    already_reported_cc: set[str],
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff DWARF value-ABI trait fingerprints. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_trait_keys = set(old_meta.value_abi_traits)
    new_trait_keys = set(new_meta.value_abi_traits)
    # The sret-flip classification is only sound for the SysV AMD64 return model.
    sysv_return = _sysv_amd64_return_model(old_meta.target_arch, new_meta.target_arch)
    for fname in sorted((old_trait_keys & new_trait_keys) - already_reported_cc):
        old_trait = old_meta.value_abi_traits[fname]
        new_trait = new_meta.value_abi_traits[fname]
        old_rc = _ret_component(old_trait)
        new_rc = _ret_component(new_trait)
        old_reg = _returns_in_registers(
            old_rc,
            old_meta.return_value_sizes.get(fname),
            fname in old_meta.return_memory_classified,
        )
        new_reg = _returns_in_registers(
            new_rc,
            new_meta.return_value_sizes.get(fname),
            fname in new_meta.return_memory_classified,
        )
        # struct_return_convention_changed only on the SysV AMD64 return model
        # (``sysv_return``) and when BOTH sides return an aggregate by value (both
        # ret components present) AND the register-vs-hidden-sret mechanism
        # actually flipped — this covers a triviality flip, a size crossing the
        # SysV 16-byte threshold (trait unchanged), or a packing change that
        # forces MEMORY. On other ABIs the rules differ, so a changed trait falls
        # through to the generic finding. When the return component is only
        # added/removed (aggregate <-> scalar) the scalar side can still be
        # register-returned, so that is left to the generic return/type findings.
        if (
            sysv_return
            and old_rc is not None
            and new_rc is not None
            and old_reg != new_reg
        ):
            results.append(
                (
                    "struct_return_convention_changed",
                    fname,
                    f"Aggregate return convention changed: {fname} "
                    f"({old_trait} → {new_trait})",
                    old_trait,
                    new_trait,
                )
            )
        elif old_trait != new_trait:
            # Same return mechanism (or a non-return trait change), but the
            # value-ABI fingerprint still changed — a generic value-ABI trait
            # change (parameter passing or copy-semantics).
            results.append(
                (
                    "value_abi_trait_changed",
                    fname,
                    f"DWARF value-ABI trait changed: {fname} ({old_trait} → {new_trait})",
                    old_trait,
                    new_trait,
                )
            )
        # else: identical trait and same return mechanism — nothing to report.
    return results


def _returns_in_registers(
    ret_component: str | None, size: int | None, memory_forced: bool = False
) -> bool:
    """Whether a by-value aggregate return is passed in registers (SysV AMD64).

    A struct is register-returned only when it is **trivial for the purposes of
    calls**, fits in two eightbytes (<= 16 bytes), *and* has no unaligned member.
    A non-trivial aggregate, a large one, or one with an unaligned member (e.g. a
    packed struct, ``memory_forced``) is memory-returned via a hidden sret
    pointer. An unknown size on an otherwise-eligible trivial aggregate is
    treated as register-eligible (stay conservative — preserves the pre-size-
    gating behaviour for snapshots/mocks that carry no size).
    """
    if ret_component is None or memory_forced:
        return False
    # Component is the triviality token: "trivial"/"nontrivial" (or the mock
    # "v(trivial)"/"v(nontrivial)"). It is non-trivial iff it says so.
    if "nontrivial" in ret_component:
        return False
    return size is None or size <= _SYSV_MAX_REGISTER_RETURN_BYTES


def _ret_component(trait: str) -> str | None:
    """Extract the ``ret:`` component of a value-ABI trait fingerprint.

    Trait strings look like ``"ret:trivial|p0:nontrivial"``; returns the value
    after ``ret:`` (e.g. ``"trivial"``) or ``None`` when the function has no
    by-value aggregate return component.
    """
    for part in trait.split("|"):
        if part.startswith("ret:"):
            return part[4:]
    return None


def _diff_struct_packing(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff struct packing attributes. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    both_struct_names = old_meta.all_struct_names & new_meta.all_struct_names
    for name in sorted(
        (old_meta.packed_structs - new_meta.packed_structs) & both_struct_names
    ):
        results.append(
            (
                "struct_packing_changed",
                name,
                f"Struct packing removed: {name} was packed, now standard layout",
                "packed",
                "standard",
            )
        )
    for name in sorted(
        (new_meta.packed_structs - old_meta.packed_structs) & old_meta.all_struct_names
    ):
        results.append(
            (
                "struct_packing_changed",
                name,
                f"Struct packing added: {name} is now __attribute__((packed))",
                "standard",
                "packed",
            )
        )
    return results


def _diff_toolchain_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff ABI-affecting compiler flags. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_flags = old_meta.toolchain.abi_flags
    new_flags = new_meta.toolchain.abi_flags
    removed_flags = old_flags - new_flags
    added_flags = new_flags - old_flags
    if removed_flags or added_flags:
        parts = []
        if added_flags:
            parts.append(f"added: {', '.join(sorted(added_flags))}")
        if removed_flags:
            parts.append(f"removed: {', '.join(sorted(removed_flags))}")
        results.append(
            (
                "toolchain_flag_drift",
                "<toolchain>",
                f"ABI-affecting compiler flags changed: {'; '.join(parts)}",
                ",".join(sorted(old_flags)) or None,
                ",".join(sorted(new_flags)) or None,
            )
        )
    return results


def _diff_vector_abi_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff vector-function (SIMD clone) ABI flags. Returns results list.

    A change in the vector-ABI flag set (-mveclibabi/-fveclib/-vecabi) means
    the vectorized call variants of functions resolve to a different ABI, which
    breaks callers that were compiled against the old vector entry points.
    """
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_flags = old_meta.toolchain.vector_abi_flags
    new_flags = new_meta.toolchain.vector_abi_flags
    if old_flags != new_flags:
        removed_flags = old_flags - new_flags
        added_flags = new_flags - old_flags
        parts = []
        if added_flags:
            parts.append(f"added: {', '.join(sorted(added_flags))}")
        if removed_flags:
            parts.append(f"removed: {', '.join(sorted(removed_flags))}")
        results.append(
            (
                "vector_abi_changed",
                "<vector-abi>",
                f"Vector-function (SIMD clone) ABI flags changed: {'; '.join(parts)}",
                ",".join(sorted(old_flags)) or None,
                ",".join(sorted(new_flags)) or None,
            )
        )
    return results


def _diff_wchar_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff the -fshort-wchar / default wchar_t data-model flag.

    GCC/Clang document that objects built with and without -fshort-wchar are
    not binary compatible: the flag switches wchar_t between the platform
    default (commonly 4-byte signed on Linux/macOS) and a 2-byte unsigned
    type. Any public function or struct field carrying wchar_t changes size
    and signedness with no symbol-level signal, so this flags the compiler-
    flag cause for review.
    """
    old_short = "-fshort-wchar" in old_meta.toolchain.wchar_flags
    new_short = "-fshort-wchar" in new_meta.toolchain.wchar_flags
    if old_short == new_short:
        return []
    old_label = (
        "short (2-byte unsigned, -fshort-wchar)" if old_short else "default wchar_t"
    )
    new_label = (
        "short (2-byte unsigned, -fshort-wchar)" if new_short else "default wchar_t"
    )
    return [
        (
            "wchar_model_changed",
            "<wchar_t>",
            f"wchar_t model changed: {old_label} → {new_label}. Objects built with "
            "and without -fshort-wchar are not binary compatible for any public "
            "wchar_t parameter, field, or return value.",
            old_label,
            new_label,
        )
    ]


def _diff_frame_registers(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff frame/CFA register usage. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_fr_keys = set(old_meta.frame_registers)
    new_fr_keys = set(new_meta.frame_registers)
    for fname in sorted(old_fr_keys & new_fr_keys):
        old_reg = old_meta.frame_registers[fname]
        new_reg = new_meta.frame_registers[fname]
        if old_reg != new_reg:
            results.append(
                (
                    "frame_register_changed",
                    fname,
                    f"Frame/CFA register changed: {fname} ({old_reg} → {new_reg})",
                    old_reg,
                    new_reg,
                )
            )
    return results


def diff_advanced_dwarf(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Return (kind, symbol, description, old_value, new_value) tuples.

    Returns [] gracefully if either side has no DWARF.
    """
    if not old_meta.has_dwarf or not new_meta.has_dwarf:
        return []

    cc_results, already_reported_cc = _diff_calling_conventions(old_meta, new_meta)
    csr_results, already_reported_cc = _diff_callee_saved_regs(
        old_meta, new_meta, already_reported_cc
    )
    trait_results = _diff_value_abi_traits(old_meta, new_meta, already_reported_cc)
    pack_results = _diff_struct_packing(old_meta, new_meta)
    flag_results = _diff_toolchain_flags(old_meta, new_meta)
    vec_results = _diff_vector_abi_flags(old_meta, new_meta)
    wchar_results = _diff_wchar_flags(old_meta, new_meta)
    frame_results = _diff_frame_registers(old_meta, new_meta)

    return (
        cc_results
        + csr_results
        + trait_results
        + pack_results
        + flag_results
        + vec_results
        + wchar_results
        + frame_results
    )

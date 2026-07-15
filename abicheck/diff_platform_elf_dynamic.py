# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""ELF dynamic-section, security-hardening, and dependency-leak diff detectors.

Split from ``diff_platform.py`` to keep that module under the AI-readiness
file-size soft cap. This module is a leaf — it must not import from
``diff_platform``. The helpers/detectors are re-exported back from
``diff_platform`` so existing imports keep working.
"""

from __future__ import annotations

from typing import Any

from .binary_utils import strip_vendor_hash
from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .diff_symbols import _should_filter_transitive_runtime_symbols
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot, Visibility

_INTERNAL_NAME_PATTERNS = (
    "internal",
    "helper",
    "_impl",
    "detail",
    "private",
    "__",
    "_priv",
    "_int_",
    "_do_",
    "_handle_",
)


def _looks_internal(name: str) -> bool:
    """Heuristic: True if symbol name looks like internal implementation detail."""
    lower = name.lower()
    return any(pat in lower for pat in _INTERNAL_NAME_PATTERNS)


def _diff_visibility_leak(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect old-library visibility leaks (ELF-only internal symbols exported)."""
    del new  # detector is intentionally old-library-only
    if not getattr(old, "elf_only_mode", False):
        return []

    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(old)
    leaked = [
        f
        for f in old.functions
        if (
            f.visibility == Visibility.ELF_ONLY
            and is_abi_relevant_elf_symbol(
                f.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
            and _looks_internal(f.name)
        )
    ]
    if not leaked:
        return []

    names = ", ".join(f.name for f in leaked[:5])
    suffix = f" (+{len(leaked) - 5} more)" if len(leaked) > 5 else ""
    return [
        make_change(
            ChangeKind.VISIBILITY_LEAK,
            symbol="<visibility>",
            name=f"{names}{suffix}",
            detail=str(len(leaked)),
            old_value=str(len(leaked)),
        )
    ]


def _diff_leaked_dependency_symbols(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect symbols that were added or removed and appear to originate from a dependency.

    When a symbol exported by this library was detected as likely originating from
    a dependency (libstdc++, libgcc, libc, …), any *addition* or *removal* of that
    symbol gets annotated as ``SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED``.

    Symbols that exist in both old and new with the same origin are intentionally
    **not** re-emitted here — ``_diff_elf_symbol_metadata`` already covers changes
    to the symbol's type/binding/size and emits its own Change records.  Emitting a
    second Change for the same symbol from both detectors would produce contradictory
    messages (one BREAKING, one RISK) for the same event.

    This is a real ABI fact — the library is leaking dependency symbols into its
    public ABI surface — but the verdict is ``COMPATIBLE_WITH_RISK`` rather than
    ``BREAKING``, because direct consumers of this library typically resolve those
    symbols through the dependency directly and are not affected by the leak.

    The risk is that on other systems with a different version of the dependency
    the leaked symbols may differ, causing failures.

    Consider applying ``-fvisibility=hidden`` to prevent this.
    """
    changes: list[Change] = []
    old_syms = old_elf.symbol_map
    new_syms = new_elf.symbol_map

    # Symbols that were *removed* (present in old, absent in new)
    for sym_name, s_old in old_syms.items():
        if sym_name in new_syms:
            # Symbol still exists — skip to avoid double-annotation with
            # _diff_elf_symbol_metadata which handles changed symbols.
            continue
        origin = s_old.origin_lib
        if origin is None:
            continue
        changes.append(
            make_change(
                ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
                symbol=sym_name,
                description=(
                    f"Symbol '{sym_name}' was removed but appears to originate from "
                    f"'{origin}' (a dependency of this library). This is a real ABI "
                    f"change — the library is leaking dependency symbols into its public "
                    f"ABI surface. Consider applying -fvisibility=hidden."
                ),
                old_value=origin,
                new_value=None,
            )
        )

    # Symbols that were *added* (absent in old, present in new with origin_lib)
    for sym_name, s_new in new_syms.items():
        if sym_name in old_syms:
            continue  # Already present in old — not a pure addition
        if s_new.origin_lib is None:
            continue
        changes.append(
            make_change(
                ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
                symbol=sym_name,
                description=(
                    f"Symbol '{sym_name}' was added but appears to originate from "
                    f"'{s_new.origin_lib}' (a dependency of this library). This is a real "
                    f"ABI change — the library is leaking dependency symbols into its public "
                    f"ABI surface. Consider applying -fvisibility=hidden."
                ),
                old_value=None,
                new_value=s_new.origin_lib,
            )
        )

    return changes


def _diff_elf_dynamic_section(old_elf: Any, new_elf: Any) -> list[Change]:
    changes: list[Change] = []
    # Emit SONAME_CHANGED only when old library HAD a SONAME (non-empty) and it
    # changed or was removed. Adding a SONAME (empty/None → value) is a compatible
    # improvement and must not be flagged as breaking.
    #
    # Compare on the vendor-hash-stripped spelling: auditwheel/delocate rewrite
    # a vendored library's own SONAME to match its content-hashed filename on
    # every wheel rebuild (e.g. libfoo-a1b2c3d4.so.1 -> libfoo-9f8e7d6c.so.1),
    # so the raw SONAME differs every build even when the underlying library
    # didn't change. A genuine SONAME bump (e.g. a real major-version change)
    # has no hyphen-hex segment to strip and still compares unequal.
    if old_elf.soname and strip_vendor_hash(old_elf.soname) != strip_vendor_hash(
        new_elf.soname
    ):
        changes.append(
            make_change(
                ChangeKind.SONAME_CHANGED,
                symbol="DT_SONAME",
                description=f"SONAME changed: {old_elf.soname!r} → {new_elf.soname!r}",
                old_value=old_elf.soname,
                new_value=new_elf.soname,
            )
        )
    elif not old_elf.soname and new_elf.soname:
        changes.append(
            make_change(
                ChangeKind.SONAME_MISSING,
                symbol="DT_SONAME",
                new=repr(new_elf.soname),
                old_value="",
                new_value=new_elf.soname,
            )
        )
    # Same vendor-hash normalization as SONAME above: auditwheel/delocate also
    # rewrite a repaired wheel's DT_NEEDED entries to reference the vendored
    # DSO's content-hashed filename, so a dependency can rename across a
    # rebuild (libfoo-a1b2c3d4.so.1 -> libfoo-9f8e7d6c.so.1) with no real
    # dependency change. Compare/report the needed lists on their
    # hash-stripped spelling so this doesn't read as NEEDED_ADDED/REMOVED.
    old_needed_stripped = [strip_vendor_hash(lib) for lib in old_elf.needed]
    new_needed_stripped = [strip_vendor_hash(lib) for lib in new_elf.needed]
    changes.extend(_diff_needed_libraries(old_needed_stripped, new_needed_stripped))
    changes.extend(_diff_needed_order(old_needed_stripped, new_needed_stripped))

    # DT_RPATH ↔ DT_RUNPATH type flip (ld --enable-new-dtags default drift).
    # The two tags carry different lookup semantics (subtree vs direct deps,
    # LD_LIBRARY_PATH precedence), so a flip is reported as its own finding.
    # When the path value is unchanged, the flip *replaces* the two individual
    # value-change findings (they would just re-describe the flip as
    # "path→''" + "''→path" noise); when the value changed too, both report.
    rpath_type_flip = (bool(old_elf.rpath) and not old_elf.runpath
                       and bool(new_elf.runpath) and not new_elf.rpath) or (
                      bool(old_elf.runpath) and not old_elf.rpath
                       and bool(new_elf.rpath) and not new_elf.runpath)
    pure_type_flip = rpath_type_flip and (
        (old_elf.rpath or old_elf.runpath) == (new_elf.rpath or new_elf.runpath)
    )
    if rpath_type_flip:
        old_tag = "DT_RPATH" if old_elf.rpath else "DT_RUNPATH"
        new_tag = "DT_RPATH" if new_elf.rpath else "DT_RUNPATH"
        changes.append(
            make_change(
                ChangeKind.RPATH_TYPE_CHANGED,
                symbol=new_tag,
                old=old_tag,
                new=new_tag,
                old_value=old_elf.rpath or old_elf.runpath,
                new_value=new_elf.rpath or new_elf.runpath,
            )
        )
    if old_elf.rpath != new_elf.rpath and not pure_type_flip:
        changes.append(
            make_change(
                ChangeKind.RPATH_CHANGED,
                symbol="DT_RPATH",
                old=repr(old_elf.rpath),
                new=repr(new_elf.rpath),
                old_value=old_elf.rpath,
                new_value=new_elf.rpath,
            )
        )
    if old_elf.runpath != new_elf.runpath and not pure_type_flip:
        changes.append(
            make_change(
                ChangeKind.RUNPATH_CHANGED,
                symbol="DT_RUNPATH",
                old=repr(old_elf.runpath),
                new=repr(new_elf.runpath),
                old_value=old_elf.runpath,
                new_value=new_elf.runpath,
            )
        )

    # PT_GNU_STACK executable stack detection (security bad practice).
    # Report ONLY the regression direction (stack becomes executable); making
    # the stack non-executable is a hardening improvement, not a finding — and
    # emitting it would let the shipped `security` policy fail an improvement.
    old_exec = getattr(old_elf, "has_executable_stack", False)
    new_exec = getattr(new_elf, "has_executable_stack", False)
    if new_exec and not old_exec:
        changes.append(
            make_change(
                ChangeKind.EXECUTABLE_STACK,
                symbol="PT_GNU_STACK",
                old_value="RW",
                new_value="RWE",
            )
        )
    elif old_exec and not new_exec:
        # Improvement direction — a distinct kind so the `security` policy can
        # gate the regression (executable_stack) without failing this fix.
        changes.append(
            make_change(
                ChangeKind.EXECUTABLE_STACK_REMOVED,
                symbol="PT_GNU_STACK",
                old_value="RWE",
                new_value="RW",
            )
        )

    changes.extend(_diff_security_hardening(old_elf, new_elf))
    changes.extend(_diff_elf_identity(old_elf, new_elf))
    changes.extend(_diff_static_tls(old_elf, new_elf))
    changes.extend(_diff_symbolic_and_textrel(old_elf, new_elf))
    changes.extend(_diff_gnu_property(old_elf, new_elf))
    changes.extend(_diff_dt_relr(old_elf, new_elf))
    changes.extend(_diff_hash_styles(old_elf, new_elf))
    changes.extend(_diff_loader_contract(old_elf, new_elf))
    changes.extend(_diff_kernel_floor(old_elf, new_elf))

    return changes


def _linker_artifact_fields_captured(old_elf: Any, new_elf: Any) -> bool:
    """True only when BOTH snapshots carry the linker-artifact fields.

    ``hash_styles``/``has_dt_relr`` postdate the G23 identity fields, so
    ``_both_captured_elf_identity`` alone cannot prove they were parsed. A
    freshly parsed DSO always has at least one symbol hash section, so a
    non-empty ``hash_styles`` on both sides is the generation marker: a legacy
    JSON snapshot (or an ET_REL ``.o``, where neither DT_RELR nor hash-style
    applies) rehydrates to an empty set and gates these detectors off instead
    of fabricating an "introduced"/"removed" finding.
    """
    return (
        _both_captured_elf_identity(old_elf, new_elf)
        and bool(getattr(old_elf, "hash_styles", frozenset()))
        and bool(getattr(new_elf, "hash_styles", frozenset()))
    )


def dt_relr_introduced(old_elf: Any, new_elf: Any) -> bool:
    """True when the new binary gained DT_RELR (and both sides captured it).

    Shared with the symbol-versioning diff so the synthetic
    ``GLIBC_ABI_DT_RELR`` verneed marker can defer to the dedicated
    ``DT_RELR_INTRODUCED`` finding instead of surfacing as a cryptic
    unparseable-version requirement.
    """
    return (
        _linker_artifact_fields_captured(old_elf, new_elf)
        and not getattr(old_elf, "has_dt_relr", False)
        and bool(getattr(new_elf, "has_dt_relr", False))
    )


def _diff_dt_relr(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect packed-relative-relocation (DT_RELR) drift.

    Introducing DT_RELR (`-z pack-relative-relocs`, a binutils ≥ 2.38 distro
    default) raises the loader floor to glibc ≥ 2.36 → RISK. Dropping it
    broadens loader compatibility → COMPATIBLE.
    """
    if not _linker_artifact_fields_captured(old_elf, new_elf):
        return []
    old_relr = bool(getattr(old_elf, "has_dt_relr", False))
    new_relr = bool(getattr(new_elf, "has_dt_relr", False))
    if old_relr == new_relr:
        return []
    if new_relr:
        return [
            make_change(
                ChangeKind.DT_RELR_INTRODUCED,
                symbol="DT_RELR",
                old_value="rel/rela",
                new_value="relr",
            )
        ]
    return [
        make_change(
            ChangeKind.DT_RELR_REMOVED,
            symbol="DT_RELR",
            old_value="relr",
            new_value="rel/rela",
        )
    ]


def _diff_hash_styles(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect a dropped symbol hash-table style (ld --hash-style drift).

    Only the drop direction reports: gaining a style broadens compatibility.
    Requires the new binary to still have at least one style (a DSO with no
    hash section at all is malformed and is not this finding's story).
    """
    if not _linker_artifact_fields_captured(old_elf, new_elf):
        return []
    old_styles = frozenset(getattr(old_elf, "hash_styles", frozenset()))
    new_styles = frozenset(getattr(new_elf, "hash_styles", frozenset()))
    dropped = old_styles - new_styles
    if not dropped:
        return []
    return [
        make_change(
            ChangeKind.HASH_STYLE_REMOVED,
            symbol=".hash" if "sysv" in dropped else ".gnu.hash",
            old="+".join(sorted(old_styles)),
            new="+".join(sorted(new_styles)),
        )
    ]


def _diff_loader_contract(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect dynamic-loader contract drift: PT_INTERP, BIND_NOW, DT_FLAGS_1
    loading flags, and init/fini presence.

    The tri-state fields (``dynamic_flags``/``has_init``/``has_fini`` = None,
    ``ei_data``/``interpreter`` = "") mean "not captured" on legacy snapshots
    and are skipped rather than compared, so a re-dump against an old baseline
    never fabricates a finding.
    """
    changes: list[Change] = []

    old_interp = getattr(old_elf, "interpreter", "")
    new_interp = getattr(new_elf, "interpreter", "")
    if old_interp and new_interp and old_interp != new_interp:
        changes.append(
            make_change(
                ChangeKind.INTERPRETER_CHANGED,
                symbol="PT_INTERP",
                old=old_interp,
                new=new_interp,
                old_value=old_interp,
                new_value=new_interp,
            )
        )

    # Eager→lazy binding flip. When the drop also lowers the RELRO level
    # (full → partial), _diff_security_hardening already reports RELRO_WEAKENED
    # for the same underlying event — don't double-report.
    if _both_captured_elf_identity(old_elf, new_elf):
        old_bind = getattr(old_elf, "bind_now", False)
        new_bind = getattr(new_elf, "bind_now", False)
        relro_unchanged = getattr(old_elf, "relro", "none") == getattr(
            new_elf, "relro", "none"
        )
        if old_bind and not new_bind and relro_unchanged:
            changes.append(
                make_change(
                    ChangeKind.BIND_NOW_DISABLED,
                    symbol="DT_BIND_NOW",
                    old_value="bind-now",
                    new_value="lazy",
                )
            )

    old_flags = getattr(old_elf, "dynamic_flags", None)
    new_flags = getattr(new_elf, "dynamic_flags", None)
    if old_flags is not None and new_flags is not None and old_flags != new_flags:
        gained = sorted(new_flags - old_flags)
        lost = sorted(old_flags - new_flags)
        detail = ", ".join([f"+{f}" for f in gained] + [f"-{f}" for f in lost])
        changes.append(
            make_change(
                ChangeKind.DYNAMIC_LOADING_FLAGS_CHANGED,
                symbol="DT_FLAGS_1",
                detail=detail,
                old_value=", ".join(sorted(old_flags)) or "(none)",
                new_value=", ".join(sorted(new_flags)) or "(none)",
            )
        )

    for attr, label in (("has_init", "init"), ("has_fini", "fini")):
        old_v = getattr(old_elf, attr, None)
        new_v = getattr(new_elf, attr, None)
        if old_v is None or new_v is None or old_v == new_v:
            continue
        detail = f"{label} code {'added' if new_v else 'removed'}"
        changes.append(
            make_change(
                ChangeKind.ELF_INIT_FINI_CHANGED,
                symbol=f"DT_{label.upper()}",
                detail=detail,
                old_value="present" if old_v else "absent",
                new_value="present" if new_v else "absent",
            )
        )

    return changes


def _kernel_version_tuple(version: str) -> tuple[int, ...] | None:
    """Parse a dotted kernel version ("3.2.0") into a comparable tuple."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _diff_kernel_floor(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect a raised NT_GNU_ABI_TAG minimum-kernel floor.

    Only the raising direction is a finding (consumers on older kernels get
    rejected by the loader); lowering the floor is an improvement.
    """
    old_floor = getattr(old_elf, "min_kernel_version", "")
    new_floor = getattr(new_elf, "min_kernel_version", "")
    if not (old_floor and new_floor) or old_floor == new_floor:
        return []
    old_t = _kernel_version_tuple(old_floor)
    new_t = _kernel_version_tuple(new_floor)
    if old_t is None or new_t is None or new_t <= old_t:
        return []
    return [
        make_change(
            ChangeKind.OS_DEPLOYMENT_FLOOR_RAISED,
            symbol=".note.ABI-tag",
            old=f"Linux {old_floor}",
            new=f"Linux {new_floor}",
            old_value=old_floor,
            new_value=new_floor,
        )
    ]


def _diff_elf_identity(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect ELF header identity drift (G23-A3): machine, class, ABI flags, OS ABI.

    A machine/class/ABI-flags change means the two inputs are
    different-architecture or different-calling-convention images — the
    ELF-side counterpart to PE_MACHINE_CHANGED / MACHO_CPU_TYPE_CHANGED. Empty
    identity (e.g. an in-memory snapshot with no ELF parsed) is skipped so a
    missing-metadata side never fabricates a finding.
    """
    changes: list[Change] = []

    old_machine = getattr(old_elf, "machine", "")
    new_machine = getattr(new_elf, "machine", "")
    # Require BOTH sides to have captured ELF identity before comparing any of
    # it. A real parsed ELF always sets `machine`; a default / header-only /
    # parse-failed `ElfMetadata()` has machine="" but still carries the
    # `elf_class=64` default — comparing that against a real 32-bit ELF would
    # false-positive elf_class_changed. An unknown side is not a change.
    if not (old_machine and new_machine):
        return changes

    if old_machine != new_machine:
        changes.append(
            make_change(
                ChangeKind.ELF_MACHINE_CHANGED,
                symbol="ELF_HEADER",
                old=old_machine,
                new=new_machine,
                old_value=old_machine,
                new_value=new_machine,
            )
        )
        # Machine drift subsumes ABI-flag/class drift (flags are per-arch); a
        # cross-architecture pair has nothing further comparable.
        return changes

    old_class = getattr(old_elf, "elf_class", 0)
    new_class = getattr(new_elf, "elf_class", 0)
    if old_class and new_class and old_class != new_class:
        changes.append(
            make_change(
                ChangeKind.ELF_CLASS_CHANGED,
                symbol="ELF_HEADER",
                old=str(old_class),
                new=str(new_class),
                old_value=str(old_class),
                new_value=str(new_class),
            )
        )

    # EI_DATA byte-order flip — the missing sibling of the class check above.
    # "" = not captured (legacy snapshot); an unknown side is not a change.
    old_data = getattr(old_elf, "ei_data", "")
    new_data = getattr(new_elf, "ei_data", "")
    if old_data and new_data and old_data != new_data:
        changes.append(
            make_change(
                ChangeKind.ELF_ENDIANNESS_CHANGED,
                symbol="ELF_HEADER",
                old=old_data,
                new=new_data,
                old_value=old_data,
                new_value=new_data,
            )
        )

    changes.extend(_diff_abi_flags(old_elf, new_elf, old_machine))

    old_osabi = getattr(old_elf, "osabi", "")
    new_osabi = getattr(new_elf, "osabi", "")
    if (
        old_osabi
        and new_osabi
        and old_osabi != new_osabi
        and not (old_osabi in _BENIGN_OSABI and new_osabi in _BENIGN_OSABI)
    ):
        changes.append(
            make_change(
                ChangeKind.ELF_OSABI_CHANGED,
                symbol="ELF_HEADER",
                old=old_osabi,
                new=new_osabi,
                old_value=old_osabi,
                new_value=new_osabi,
            )
        )

    return changes


#: Architectures whose ABI-selecting e_flags bits are decoded into `abi_flags`
#: by `elf_metadata._decode_abi_flags`. For these the decoded token set is the
#: authoritative ABI signal, so the raw-e_flags fallback must NOT run — the
#: undecoded bits carry ISA-level (`-march`) or feature (RISC-V Ztso, MIPS arch
#: level) changes that are calling-convention-compatible, and diffing them would
#: over-call `elf_abi_flags_changed` (BREAKING) on a compatible rebuild.
_ABI_FLAG_DECODED_MACHINES = frozenset({"EM_ARM", "EM_RISCV", "EM_MIPS"})


def _diff_abi_flags(old_elf: Any, new_elf: Any, machine: str) -> list[Change]:
    """Compare the ABI-selecting e_flags bits (same-machine caller guarantee).

    For architectures the metadata parser knows how to decode (ARM/RISC-V/MIPS)
    the decoded ``abi_flags`` token set is diffed and is authoritative. For any
    other architecture both decoded sets are empty, so fall back to the raw
    ``e_flags`` word — e.g. PPC64 encodes its ELFv1/ELFv2 ABI version there —
    otherwise ABI-selecting drift on undecoded arches would never surface.
    """
    old_abi: frozenset[str] = getattr(old_elf, "abi_flags", frozenset())
    new_abi: frozenset[str] = getattr(new_elf, "abi_flags", frozenset())
    # Back-compat normalization: the pre-fix RISC-V decoder emitted a legacy
    # `rvc` token (compressed-instruction ISA bit, not an ABI selector; #504). A
    # saved `.abi.json` baseline rehydrates `abi_flags` verbatim via
    # `serialization._elf_from_dict`, so an old baseline can carry `rvc` while a
    # freshly-parsed side no longer does. Strip it from both sides before
    # comparing, else `(float-double, rvc)` vs `(float-double)` would falsely
    # report a BREAKING elf_abi_flags_changed on the same ABI.
    if machine == "EM_RISCV":
        old_abi = old_abi - {"rvc"}
        new_abi = new_abi - {"rvc"}
    if old_abi != new_abi:
        return [
            make_change(
                ChangeKind.ELF_ABI_FLAGS_CHANGED,
                symbol="ELF_HEADER",
                old=", ".join(sorted(old_abi)) or "(none)",
                new=", ".join(sorted(new_abi)) or "(none)",
            )
        ]

    # Decoded tokens match. For a decoded arch the token set is authoritative, so
    # stop here: the remaining e_flags bits are ISA-level/feature bits (a MIPS
    # `-march` bump, RISC-V Ztso, …) that are calling-convention-compatible, and
    # diffing them would falsely report a BREAKING abi-flags change on a
    # compatible rebuild. Only fall back to the raw word for arches we don't
    # decode at all (e.g. PPC64 ELFv1/ELFv2), where it is the sole ABI signal.
    if machine in _ABI_FLAG_DECODED_MACHINES:
        return []
    old_ef = getattr(old_elf, "e_flags", 0)
    new_ef = getattr(new_elf, "e_flags", 0)
    if old_ef != new_ef:
        return [
            make_change(
                ChangeKind.ELF_ABI_FLAGS_CHANGED,
                symbol="ELF_HEADER",
                old=hex(old_ef),
                new=hex(new_ef),
            )
        ]
    return []


#: OS-ABI values that are interchangeable on Linux. The GNU toolchain stamps a
#: binary ELFOSABI_GNU/LINUX (3) instead of ELFOSABI_SYSV/NONE (0) as a side
#: effect of using any GNU extension (IFUNC, STB_GNU_UNIQUE, …), so a SYSV↔GNU
#: transition is benign and must not be flagged (it routinely rides along with a
#: compatible change like adding an ifunc). Genuinely different OS ABIs
#: (FreeBSD, Solaris, …) still report.
_BENIGN_OSABI = frozenset({
    "ELFOSABI_SYSV",
    "ELFOSABI_NONE",
    "ELFOSABI_GNU",
    "ELFOSABI_LINUX",
})


def _both_captured_elf_identity(old_elf: Any, new_elf: Any) -> bool:
    """True only when BOTH snapshots were parsed by a G23-aware version.

    Every G23 ELF fact (machine, static-TLS, gnu-property, …) is captured in the
    same parse pass, so a real ELF parsed by current code always has a non-empty
    ``machine``. A legacy snapshot serialized before these fields existed — or a
    header-only / parse-failed side — has machine="" and rehydrates the new
    booleans to their defaults (``has_static_tls=False``, empty gnu_properties),
    which would otherwise read as "the feature was absent" rather than "unknown"
    and fabricate a finding. Gate the new detectors on this so a legacy baseline
    never triggers e.g. a spurious ``static_tls_introduced``.
    """
    return bool(getattr(old_elf, "machine", "") and getattr(new_elf, "machine", ""))


def _diff_static_tls(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect DF_STATIC_TLS drift (G23-A1).

    Only report when the *new* side actually participates in TLS (defines or
    imports an STT_TLS symbol, or carries a PT_TLS segment), so a TLS-free
    library that happens to flip the flag is never flagged. The removal
    (improvement) direction is a distinct COMPATIBLE kind so the security policy
    can gate the regression alone. Skipped entirely unless both snapshots
    captured ELF identity (so a legacy baseline never false-positives).
    """
    if not _both_captured_elf_identity(old_elf, new_elf):
        return []
    old_static = getattr(old_elf, "has_static_tls", False)
    new_static = getattr(new_elf, "has_static_tls", False)
    if old_static == new_static:
        return []
    if new_static and not old_static:
        if not getattr(new_elf, "has_tls_symbols", False):
            return []
        return [
            make_change(
                ChangeKind.STATIC_TLS_INTRODUCED,
                symbol="DF_STATIC_TLS",
                old_value="dynamic-tls",
                new_value="static-tls",
            )
        ]
    return [
        make_change(
            ChangeKind.STATIC_TLS_REMOVED,
            symbol="DF_STATIC_TLS",
            old_value="static-tls",
            new_value="dynamic-tls",
        )
    ]


#: GNU-property feature tokens grouped by the kind that reports their drift.
_CET_FEATURES = frozenset({"IBT", "SHSTK"})
_BRANCH_FEATURES = frozenset({"BTI", "PAC"})
#: x86-64 micro-architecture levels from GNU_PROPERTY_X86_ISA_1_NEEDED,
#: ordered weakest → strongest. A raised maximum level means CPUs that could
#: run the old build can no longer run the new one.
_X86_ISA_LEVEL_RANK: dict[str, int] = {
    "x86-64-baseline": 1,
    "x86-64-v2": 2,
    "x86-64-v3": 3,
    "x86-64-v4": 4,
}


def _diff_gnu_property(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect .note.gnu.property control-flow-protection drift (G23-A2).

    x86 CET (IBT/SHSTK) and AArch64 branch-protection (BTI/PAC) are reported
    separately. Both weakening (dropped feature) and improvement (added
    feature) directions are emitted, mirroring the executable-stack pair, so
    the security policy can gate weakening without failing an improvement.
    """
    if not _both_captured_elf_identity(old_elf, new_elf):
        return []
    # CET (IBT/SHSTK) is x86-only and branch-protection (BTI/PAC) is AArch64-only,
    # so the feature sets are not comparable across machines. A machine change is
    # already reported as `elf_machine_changed` by `_diff_elf_identity`; diffing
    # gnu.property across it would fabricate a spurious weakened/improved pair
    # (e.g. x86_64+IBT → aarch64+BTI reads as CET dropped + branch-prot added).
    if getattr(old_elf, "machine", "") != getattr(new_elf, "machine", ""):
        return []
    old_props: frozenset[str] = getattr(old_elf, "gnu_properties", frozenset())
    new_props: frozenset[str] = getattr(new_elf, "gnu_properties", frozenset())
    if old_props == new_props:
        return []

    changes: list[Change] = []
    for feats, weakened, improved in (
        (_CET_FEATURES, ChangeKind.CET_PROTECTION_WEAKENED, ChangeKind.CET_PROTECTION_IMPROVED),
        (_BRANCH_FEATURES, ChangeKind.BRANCH_PROTECTION_WEAKENED, ChangeKind.BRANCH_PROTECTION_IMPROVED),
    ):
        old_f = old_props & feats
        new_f = new_props & feats
        if old_f == new_f:
            continue
        dropped = old_f - new_f
        symbol = ".note.gnu.property"
        if dropped:
            changes.append(
                make_change(
                    weakened,
                    symbol=symbol,
                    old=", ".join(sorted(old_f)) or "(none)",
                    new=", ".join(sorted(new_f)) or "(none)",
                )
            )
        else:
            changes.append(
                make_change(
                    improved,
                    symbol=symbol,
                    old=", ".join(sorted(old_f)) or "(none)",
                    new=", ".join(sorted(new_f)) or "(none)",
                )
            )

    # x86-64 ISA-needed baseline (GNU_PROPERTY_X86_ISA_1_NEEDED). Fires when the
    # NEW side declares a level: both ELFs are captured (gated above), so an
    # absent old note is not "unrecorded" but "no declared micro-architecture
    # floor" = plain x86-64 (baseline). A common baseline → v3 rebuild must
    # therefore report. Only the raising direction is a finding (a lowered floor
    # widens the supported CPU set).
    old_isa = {t for t in old_props if t in _X86_ISA_LEVEL_RANK}
    new_isa = {t for t in new_props if t in _X86_ISA_LEVEL_RANK}
    if new_isa:
        old_max = (
            max(old_isa, key=_X86_ISA_LEVEL_RANK.__getitem__)
            if old_isa
            else "x86-64-baseline"
        )
        new_max = max(new_isa, key=_X86_ISA_LEVEL_RANK.__getitem__)
        if _X86_ISA_LEVEL_RANK[new_max] > _X86_ISA_LEVEL_RANK[old_max]:
            changes.append(
                make_change(
                    ChangeKind.X86_ISA_BASELINE_RAISED,
                    symbol=".note.gnu.property",
                    old=old_max,
                    new=new_max,
                    old_value=old_max,
                    new_value=new_max,
                )
            )
    return changes


#: RELRO levels ordered weakest → strongest, for regression detection.
_RELRO_RANK: dict[str, int] = {"none": 0, "partial": 1, "full": 2}


def _diff_security_hardening(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect checksec-style hardening regressions between two ELF snapshots.

    Only *weakening* transitions are reported (a release that improves
    hardening is not a finding). All kinds are RISK by default; the shipped
    ``security`` policy gates them to break.
    """
    changes: list[Change] = []

    old_relro = getattr(old_elf, "relro", "none")
    new_relro = getattr(new_elf, "relro", "none")
    if _RELRO_RANK.get(new_relro, 0) < _RELRO_RANK.get(old_relro, 0):
        changes.append(
            make_change(
                ChangeKind.RELRO_WEAKENED,
                symbol="GNU_RELRO",
                old=old_relro,
                new=new_relro,
            )
        )

    if getattr(old_elf, "is_pie", False) and not getattr(new_elf, "is_pie", False):
        changes.append(
            make_change(
                ChangeKind.PIE_DISABLED,
                symbol="DF_1_PIE",
                old_value="PIE",
                new_value="no-PIE",
            )
        )

    if getattr(old_elf, "has_stack_canary", False) and not getattr(
        new_elf, "has_stack_canary", False
    ):
        changes.append(
            make_change(
                ChangeKind.STACK_CANARY_REMOVED,
                symbol="__stack_chk_fail",
                old_value="canary",
                new_value="none",
            )
        )

    if getattr(old_elf, "has_fortify_source", False) and not getattr(
        new_elf, "has_fortify_source", False
    ):
        changes.append(
            make_change(
                ChangeKind.FORTIFY_SOURCE_WEAKENED,
                symbol="_FORTIFY_SOURCE",
                old_value="fortified",
                new_value="none",
            )
        )

    if not getattr(old_elf, "has_writable_executable_segment", False) and getattr(
        new_elf, "has_writable_executable_segment", False
    ):
        changes.append(
            make_change(
                ChangeKind.WRITABLE_EXECUTABLE_SEGMENT,
                symbol="PT_LOAD",
                old_value="W^X",
                new_value="W+X",
            )
        )

    return changes


def _diff_needed_libraries(
    old_needed: list[str], new_needed: list[str]
) -> list[Change]:
    changes: list[Change] = []
    old_set = set(old_needed)
    new_set = set(new_needed)
    for lib in sorted(new_set - old_set):
        changes.append(
            make_change(
                ChangeKind.NEEDED_ADDED,
                symbol="DT_NEEDED",
                description=f"New dependency added: {lib}",
                new_value=lib,
            )
        )
    for lib in sorted(old_set - new_set):
        changes.append(
            make_change(
                ChangeKind.NEEDED_REMOVED,
                symbol="DT_NEEDED",
                description=f"Dependency removed: {lib}",
                old_value=lib,
            )
        )
    return changes


def _diff_needed_order(old_needed: list[str], new_needed: list[str]) -> list[Change]:
    """Detect a DT_NEEDED reorder with the dependency set unchanged.

    The System V gABI dynamic linker searches dependencies breadth-first in
    DT_NEEDED order, so a pure reorder can silently change which DSO wins the
    lookup for a non-versioned symbol defined in more than one dependency.
    Only fires when the *set* is identical — an add/remove is already
    reported by ``_diff_needed_libraries`` and reordering on top of that
    would just be noise describing the same underlying change twice.
    """
    if old_needed == new_needed or set(old_needed) != set(new_needed):
        return []
    return [
        make_change(
            ChangeKind.NEEDED_ORDER_CHANGED,
            symbol="DT_NEEDED",
            old=", ".join(old_needed),
            new=", ".join(new_needed),
            old_value=", ".join(old_needed),
            new_value=", ".join(new_needed),
        )
    ]


def _diff_symbolic_and_textrel(old_elf: Any, new_elf: Any) -> list[Change]:
    """Detect DT_SYMBOLIC/DF_SYMBOLIC and DF_TEXTREL/DT_TEXTREL drift.

    DF_SYMBOLIC makes the object resolve its own references against its own
    definitions first, before the global scope — a lookup-precedence change
    that can silently stop honoring an LD_PRELOAD or another library's
    intended interposition. DF_TEXTREL means the loader must write into the
    (nominally read-only, shared) text segment to apply relocations — a
    security-hardening regression, so only the "gained" direction reports;
    dropping it is an improvement. Gated on both sides having captured ELF
    identity so a legacy baseline never fabricates a finding.
    """
    if not _both_captured_elf_identity(old_elf, new_elf):
        return []
    changes: list[Change] = []

    old_sym = getattr(old_elf, "is_symbolic", False)
    new_sym = getattr(new_elf, "is_symbolic", False)
    if old_sym != new_sym:
        changes.append(
            make_change(
                ChangeKind.SYMBOLIC_BINDING_MODE_CHANGED,
                symbol="DT_SYMBOLIC",
                old="symbolic" if old_sym else "direct",
                new="symbolic" if new_sym else "direct",
                old_value="DF_SYMBOLIC set" if old_sym else "(unset)",
                new_value="DF_SYMBOLIC set" if new_sym else "(unset)",
            )
        )

    old_tr = getattr(old_elf, "has_textrel", False)
    new_tr = getattr(new_elf, "has_textrel", False)
    if new_tr and not old_tr:
        changes.append(
            make_change(
                ChangeKind.TEXT_RELOCATION_INTRODUCED,
                symbol="DF_TEXTREL",
                old_value="(none)",
                new_value="DF_TEXTREL set",
            )
        )
    elif old_tr and not new_tr:
        changes.append(
            make_change(
                ChangeKind.TEXT_RELOCATION_REMOVED,
                symbol="DF_TEXTREL",
                old_value="DF_TEXTREL set",
                new_value="(none)",
            )
        )

    return changes

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

"""ELF dynamic-section and symbol facts as data.

The dataclasses an ELF binary's dynamic section and symbol tables are read
*into*. Parsing them is ``abicheck.elf_metadata``'s job; this module holds no
parsing logic and imports nothing outside ``model/`` itself (ADR-063 Phase 5's
``.fact`` bridge is a sibling leaf module, not the extractor), so ``model``
can own the shape of an ELF fact without depending on the extractor that
fills it in (ADR-061 Phase 5 — "``*_metadata.py`` conflate a model dataclass
with its parser").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property

from .fact import Fact, bridge_legacy_and_fact


class SymbolBinding(str, Enum):
    GLOBAL = "global"
    WEAK = "weak"
    LOCAL = "local"
    UNIQUE = "unique"  # STB_GNU_UNIQUE — process-wide uniqueness, inhibits dlclose
    OTHER = "other"


class SymbolType(str, Enum):
    FUNC = "func"
    OBJECT = "object"
    TLS = "tls"
    IFUNC = "ifunc"  # STT_GNU_IFUNC
    COMMON = "common"  # STT_COMMON
    NOTYPE = "notype"
    OTHER = "other"


@dataclass
class ElfSymbol:
    name: str
    binding: SymbolBinding = SymbolBinding.GLOBAL
    sym_type: SymbolType = SymbolType.FUNC
    size: int = 0
    version: str = ""  # version tag from .gnu.version_d/.gnu.version_r
    is_default: bool = True
    visibility: str = "default"  # default / hidden / protected / internal
    origin_lib: str | None = None  # Detected source library, None = native
    # Power-of-two address alignment derived from st_value (capped at the page
    # size, 4096). Segments load page-aligned, so alignment up to a page is
    # preserved at runtime. 0 = unknown (st_value 0, or a legacy snapshot).
    # Used to detect exported-data alignment reductions (copy-reloc hazard).
    value_alignment: int = 0


@dataclass
class ElfImport:
    """An undefined (imported) dynamic symbol — what this DSO requires."""

    name: str
    binding: SymbolBinding = SymbolBinding.GLOBAL  # GLOBAL or WEAK
    sym_type: SymbolType = SymbolType.NOTYPE
    version: str = ""  # required version tag (from .gnu.version + .gnu.version_r)
    is_default: bool = True  # @@default vs @specific
    # Soname of the library that .gnu.version_r names as the provider of this
    # symbol's required version. GNU version labels are scoped per verneed
    # provider (not globally unique), so this disambiguates which DSO satisfies
    # the import when two providers share a label. "" when unversioned.
    version_soname: str = ""


@dataclass
class ElfMetadata:
    """ELF dynamic-section + symbol metadata for one .so.

    NOTE: Do NOT add ``frozen=True`` to this dataclass — ``@cached_property``
    (used by ``symbol_map``) requires a writable instance ``__dict__``.
    """

    soname: str = ""
    needed: list[str] = field(default_factory=list)
    rpath: str = ""
    runpath: str = ""

    # Symbol versions defined by this library (.gnu.version_d)
    versions_defined: list[str] = field(default_factory=list)
    # Symbol versions required from other libraries (.gnu.version_r)
    # dict: library_soname → list of version strings
    versions_required: dict[str, list[str]] = field(default_factory=dict)

    # Exported symbols (.dynsym, GLOBAL/WEAK, not UND, not hidden/internal)
    symbols: list[ElfSymbol] = field(default_factory=list)

    # Imported symbols (.dynsym, SHN_UNDEF, GLOBAL/WEAK)
    imports: list[ElfImport] = field(default_factory=list)

    # ELF interpreter (PT_INTERP, e.g. /lib64/ld-linux-x86-64.so.2)
    interpreter: str = ""
    # ELF data encoding from EI_DATA: "LSB" (little) / "MSB" (big).
    # "" = not captured (legacy snapshot) — detectors must skip, not compare.
    ei_data: str = ""
    # Minimum kernel version from the NT_GNU_ABI_TAG note (.note.ABI-tag),
    # e.g. "3.2.0". "" = note absent or not captured.
    min_kernel_version: str = ""
    # dlopen/dlclose-contract flags decoded from DT_FLAGS/DT_FLAGS_1:
    # subset of {"NODELETE", "NOOPEN", "ORIGIN"}. None = not captured
    # (legacy snapshot); frozenset() = captured, none set.
    dynamic_flags: frozenset[str] | None = None
    # Load/unload-time code presence (DT_INIT/DT_INIT_ARRAY and
    # DT_FINI/DT_FINI_ARRAY). None = not captured (legacy snapshot).
    has_init: bool | None = None
    has_fini: bool | None = None

    # PT_GNU_STACK: True when the ELF has an executable stack (RWE flags).
    # This is a security bad practice (disables NX protection).
    has_executable_stack: bool = False

    # ── checksec-equivalent hardening surface ────────────────────────────
    # These mirror what `checksec`/`hardening-check` report so a release that
    # silently weakens a hardening property can be diffed (see G12).
    #
    # RELRO level: "none" | "partial" | "full".
    #   partial = PT_GNU_RELRO segment present (GOT moved to a read-only page
    #             after relocation), full = partial + BIND_NOW (eager binding,
    #             so the whole GOT is read-only).
    relro: str = "none"
    # BIND_NOW eager binding (DT_BIND_NOW, DF_BIND_NOW, or DF_1_NOW).
    bind_now: bool = False
    # Position-independent executable (ET_DYN + DF_1_PIE). Shared libraries are
    # always position-independent; this flags PIE *executables* specifically.
    is_pie: bool = False
    # Stack-smashing protector: references __stack_chk_fail / __stack_chk_guard.
    has_stack_canary: bool = False
    # _FORTIFY_SOURCE: references at least one fortified libc wrapper (*_chk).
    has_fortify_source: bool = False
    # W^X violation: a loadable segment is simultaneously writable + executable.
    has_writable_executable_segment: bool = False
    # DT_SYMBOLIC/DF_SYMBOLIC: the object resolves its own references against
    # its own definitions before the global scope (lookup-precedence change).
    is_symbolic: bool = False
    # DF_TEXTREL (DT_FLAGS) or the legacy DT_TEXTREL tag: the loader must write
    # into the text segment to apply relocations, defeating W^X / text-segment
    # sharing. Non-PIC code is the common cause.
    has_textrel: bool = False

    # Target pointer width in bytes (4 for ELFCLASS32, 8 for ELFCLASS64).
    # Used by diff_elf_layout.py to turn `_ZTV`/`_ZTI` object sizes into vtable
    # slot counts and inheritance shapes. Defaults to 8 (the common 64-bit case)
    # so in-memory snapshots constructed in tests need not set it explicitly.
    pointer_size: int = 8

    # ── ELF identity (G23-A3) ────────────────────────────────────────────
    # Header fields that define the binary's target contract. A drift here
    # means the two inputs are different-architecture / different-ABI images.
    # ``machine`` is the pyelftools e_machine string (e.g. "EM_X86_64");
    # ``elf_class`` is 32 or 64; ``osabi`` is the EI_OSABI string
    # (e.g. "ELFOSABI_SYSV"). ``e_flags`` is the raw per-arch flag word and
    # ``abi_flags`` a decoded, human-readable subset (float ABI / EABI version)
    # for the architectures we know how to decode (ARM, RISC-V, MIPS).
    machine: str = ""
    elf_class: int = 64
    osabi: str = ""
    e_flags: int = 0
    abi_flags: frozenset[str] = field(default_factory=frozenset)

    # ── Static-TLS drift (G23-A1) ────────────────────────────────────────
    # DF_STATIC_TLS in DT_FLAGS: the library uses the static (initial/local-exec)
    # TLS model and can no longer be reliably dlopen()ed. ``has_tls_symbols`` is
    # True when the library participates in TLS at all — set from *either* a
    # dynamic STT_TLS entry (defined OR an undefined `extern __thread` import) or
    # a PT_TLS program-header segment (which also covers hidden/local __thread
    # variables that never reach .dynsym). Both the import-only and hidden-local
    # cases are just as dlopen-hostile, so the DF_STATIC_TLS suppression guard
    # must consider all of them.
    has_static_tls: bool = False
    has_tls_symbols: bool = False

    # ── GNU-property hardening (G23-A2) ──────────────────────────────────
    # Control-flow protections carried in PT_GNU_PROPERTY / .note.gnu.property.
    # A set of feature tokens drawn from {"IBT", "SHSTK", "BTI", "PAC"}. Dropping
    # a feature between versions weakens the process-wide guarantee (a single
    # non-IBT/BTI DSO disables enforcement for the whole link map).
    gnu_properties: frozenset[str] = field(default_factory=frozenset)

    # ── Linker artifact facts (binutils & glibc skew) ────────────────────
    # DT_RELR packed relative relocations (`-z pack-relative-relocs`). A
    # DT_RELR binary needs glibc ≥ 2.36 (or an equivalent loader) — glibc
    # marks the requirement with a synthetic GLIBC_ABI_DT_RELR verneed.
    has_dt_relr: bool = False
    # Symbol hash-table styles present: subset of {"sysv", "gnu"}
    # (.hash → "sysv", .gnu.hash → "gnu"; ld --hash-style). Dropping a style
    # drops loaders/tools that only support that style.
    hash_styles: frozenset[str] = field(default_factory=frozenset)

    # ADR-063 Phase 5 (seventh batch): Fact[...] siblings for this
    # dataclass's own schema-version-driven case-(b) fields -- the identical
    # "resting value can't distinguish not-captured from confirmed-empty"
    # shape as the four declaration dataclasses' own case-(b) fields, gated
    # by a persisted schema_version rather than a per-run backend choice.
    dynamic_flags_fact: Fact[frozenset[str] | None] | None = field(
        default=None, kw_only=True
    )
    has_init_fact: Fact[bool | None] | None = field(default=None, kw_only=True)
    has_fini_fact: Fact[bool | None] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        self.dynamic_flags, self.dynamic_flags_fact = bridge_legacy_and_fact(
            self.dynamic_flags, self.dynamic_flags_fact, None, None
        )
        self.has_init, self.has_init_fact = bridge_legacy_and_fact(
            self.has_init, self.has_init_fact, None, None
        )
        self.has_fini, self.has_fini_fact = bridge_legacy_and_fact(
            self.has_fini, self.has_fini_fact, None, None
        )

    @cached_property
    def symbol_map(self) -> dict[str, ElfSymbol]:
        """Name → ElfSymbol mapping (built once, cached on first access).

        Thread safety: benign race — both threads compute the same dict;
        the last write wins. Functionally correct for read-only use.
        """
        return {s.name: s for s in self.symbols}

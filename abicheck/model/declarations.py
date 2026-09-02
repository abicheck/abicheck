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

"""Declared entities: a function or variable and the parameters it takes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from .elf_facts import SymbolBinding
from .fact import Fact, _Omitted, bridge_legacy_and_fact
from .identity import EntityId
from .vocabulary import AccessLevel, ElfVisibility, ParamKind, ScopeOrigin, Visibility

# ADR-063 Phase 0: private omission sentinel for Param.is_va_list's Fact[T]
# sibling — see model/fact.py's _Omitted/bridge_legacy_and_fact docstrings.
# bool has only two instances, so a bare True/False cannot double as an
# omission marker the way an empty list can for a list-typed field; this
# sentinel is cast() to bool so the declared field type never widens to
# bool | None (which would be a breaking change to this public dataclass's
# type for every reader, not only the ones migrating to Fact[T]).
_OMITTED_IS_VA_LIST: bool = cast(bool, _Omitted())
# ADR-063 Phase 5 (ninth batch): `Function.deprecated`/`Variable.deprecated`
# share the identical case-(a) shape -- `None` means "not deprecated" as
# much as "not captured" (see Function.deprecated's own comment below), so
# availability is carried by AbiSnapshot.clang_deprecation_facts_reliable,
# never by the value.
_OMITTED_FUNC_DEPRECATED: str | None = cast("str | None", _Omitted())
_OMITTED_VAR_DEPRECATED: str | None = cast("str | None", _Omitted())


@dataclass
class Param:
    name: str
    type: str
    kind: ParamKind = ParamKind.VALUE
    default: str | None = None  # has default value (value not preserved)
    pointer_depth: int = 0  # nesting: T=0, T*=1, T**=2
    is_restrict: bool = False  # restrict-qualified pointer parameter
    # ADR-063 Phase 0: defaults to a private omission sentinel, not False —
    # see is_va_list_fact below and __post_init__.
    is_va_list: bool = (
        _OMITTED_IS_VA_LIST  # parameter is va_list (variadic argument list)
    )
    # Fact[bool] sibling — see RecordType's identical bases_fact/vtable_fact
    # comment in model/entities.py for the full rationale. A detector reads
    # this, never the plain is_va_list field above.
    is_va_list_fact: Fact[bool] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        self.is_va_list, self.is_va_list_fact = bridge_legacy_and_fact(
            self.is_va_list, self.is_va_list_fact, _OMITTED_IS_VA_LIST, False
        )


@dataclass
class Function:
    name: str  # demangled
    mangled: str  # mangled symbol name
    return_type: str
    params: list[Param] = field(default_factory=list)
    visibility: Visibility = Visibility.PUBLIC
    is_virtual: bool = False
    is_noexcept: bool = False
    is_extern_c: bool = False
    vtable_index: int | None = None
    source_location: str | None = None  # "header.h:42"
    is_static: bool = False
    is_const: bool = False  # const qualifier on this
    is_volatile: bool = False  # volatile qualifier on this
    is_pure_virtual: bool = False
    is_deleted: bool = False  # = delete; previously callable → BREAKING
    deleted_from_dwarf: bool = False  # True when is_deleted was set via DW_AT_deleted
    is_inline: bool = False  # inline keyword / attribute in header
    access: AccessLevel = AccessLevel.PUBLIC  # public/protected/private
    return_pointer_depth: int = 0  # T=0, T*=1, T**=2
    elf_visibility: ElfVisibility | None = None  # ELF st_other (populated from .dynsym)
    ref_qualifier: str = ""  # "" (none), "&" (lvalue), "&&" (rvalue)
    # explicit specifier on constructors / conversion operators (DW_AT_explicit /
    # castxml @explicit). Tri-state to keep "unknown" distinct from "implicit":
    # - True  → source has `explicit` (or `explicit(true)`)
    # - False → source does not have `explicit`
    # - None  → snapshot loader does not know (older snapshots, dumpers that
    #           don't capture this attribute). The diff must skip the
    #           detector when either side is None to avoid false API_BREAK
    #           findings from schema evolution.
    is_explicit: bool | None = None
    # Hidden-friend marker (in-class `friend` declaration, often inline).
    # Tri-state to keep "unknown" distinct from "not a friend":
    # - True  → declared as a friend inside some class body (castxml
    #           ``befriending`` attribute on the class points to this fn).
    # - False → not a friend declaration.
    # - None  → dumper/loader could not determine (older snapshots, DWARF-
    #           only path). Diff detectors skip when either side is None.
    is_hidden_friend: bool | None = None
    # Provenance (ADR-015, schema v6). source_header is the defining header
    # (source_location with the line/col stripped); origin classifies it
    # against the provided public-header set. Both are additive: missing on
    # older snapshots and default to None / UNKNOWN.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN
    # C ellipsis (...) — variadic calls use a different convention on common
    # ABIs (%al on SysV x86-64, stack args on Apple AArch64). Tri-state:
    # None = dumper/loader does not know (older snapshots); diff skips then.
    is_variadic: bool | None = None
    # Semantic contract attributes (nonnull, noreturn, format, alloc_size,
    # malloc, returns_nonnull, warn_unused_result, sentinel, ...), normalized
    # spellings. None = not captured (older snapshots / dumpers without
    # attribute support); [] = captured, none present. Diff skips on None.
    contract_attributes: list[str] | None = None
    # Dynamic exception specification spelling ("throw()", "throw(int)", ...).
    # "" = captured, no dynamic spec; None = not captured. `noexcept` is NOT
    # folded in here — it keeps its dedicated is_noexcept field and kinds.
    exception_spec: str | None = None
    # `[[deprecated]]`/`[[deprecated("msg")]]` (or castxml's `deprecation`
    # attribute, which carries the same message text): a non-empty string is
    # the message, "" is a bare `[[deprecated]]` with no message. Unlike most
    # other tri-state fields here, None does NOT unambiguously mean
    # "unsupported" — castxml also reports None for a genuinely
    # non-deprecated declaration (there is no separate "deprecated with no
    # info" state to distinguish it from). So the diff detector gates on
    # header-tier confirmation at the *snapshot* level (mirroring
    # Param.default/param_defaults's own header-tier-only gate), not by
    # skipping a None on either side of a single pair — that would silently
    # miss every real "gained/lost deprecated" transition, since one side of
    # a real transition is always None (not-deprecated) by construction.
    # ADR-063 Phase 5 (ninth batch): private omission sentinel, see
    # deprecated_fact below and _OMITTED_FUNC_DEPRECATED above.
    deprecated: str | None = _OMITTED_FUNC_DEPRECATED
    # Explicit C++11 `override` specifier on a virtual method declaration.
    # Tri-state like is_explicit/is_hidden_friend: True/False = captured;
    # None = dumper/loader does not know (older snapshots, DWARF/symbols-only
    # mode, or a non-virtual/non-method declaration for which the specifier
    # is not applicable). Populated by both header backends since G31 Phase C
    # (castxml's own `attributes` regex; clang's `OverrideAttr` child node).
    is_override: bool | None = None
    # Qualified name of the class whose body declares this friend (the
    # `befriending` owner in castxml terms), e.g. "ns::Foo". None when the
    # function is not a hidden friend, or the owner could not be resolved
    # (older snapshots, DWARF-only path). Surface classification must key
    # demotion off the *owner's* origin (system/private/public header), not
    # just the friend function's own — a hidden friend can never produce an
    # exported symbol by construction, but that is only a reason to skip the
    # not-exported check, not a reason to skip the header-provenance check.
    # Appended after all pre-existing fields (rather than inserted next to
    # is_hidden_friend) so this additive field cannot shift the positional
    # slot of any field that came before it (Codex review: Function is a
    # public, non-keyword-only dataclass, so an insertion mid-list would
    # silently rebind existing positional-constructor arguments instead of
    # failing).
    hidden_friend_owner: str | None = None
    # ELF symbol *linkage* (st_info.bind — GLOBAL/WEAK/LOCAL/UNIQUE/OTHER),
    # populated from .dynsym the same way elf_visibility is (see
    # dumper_elf_symbols._populate_elf_visibility). None on a non-ELF
    # platform, an older snapshot predating this field, or a declaration with
    # no matching exported symbol (e.g. a public header-only inline that
    # never made it into the dynamic symbol table). Distinguishes a WEAK
    # COMDAT definition (e.g. an in-class-defined/`inline` member) from a
    # GLOBAL/STRONG export on an otherwise-identical FUNC_REMOVED finding,
    # which neither `visibility` nor `is_inline` alone can do — but this is
    # PROVIDER-SIDE evidence only, about the *library's own build*, not a
    # guarantee about consumers: a WEAK/COMDAT symbol does not by itself mean
    # every consumer already carries its own copy (a public `extern template`
    # declaration is the documented counterexample — the consumer TU
    # deliberately does *not* instantiate, while the library's own explicit
    # instantiation still emits a WEAK/COMDAT definition, so the consumer can
    # hold an undefined reference to a symbol this field still reports as
    # WEAK). See AGENTS.md's "Linkage-blind removal" entry for why a heavier
    # removal-severity *demotion* keyed off this same fact was attempted and
    # reverted for exactly this reason — this field only makes the fact
    # visible on the model and matchable by a suppression selector, it does
    # not itself change any verdict, and a suppression author must not treat
    # WEAK alone as sufficient justification (see Suppression.binding's own
    # docstring for the full caveat). Known gap, same as elf_visibility: a
    # symbol-versioned bare name with mixed bindings across versions (e.g. a GLOBAL
    # old-ABI @V1 and a WEAK default @@V2) collapses to whichever entry
    # elf.symbol_map's last-write-wins dict happens to keep — see AGENTS.md's
    # dedicated entry for this field.
    elf_binding: SymbolBinding | None = None
    # True when this declaration was never written by the user — a
    # compiler-synthesized implicit special member (default/copy/move
    # constructor, copy/move assignment operator, destructor) the language
    # generates automatically rather than a real declaration in the public
    # header. Tri-state like is_explicit/is_override:
    # - True  → confirmed compiler-generated (castxml's own `artificial="1"`
    #           XML attribute, present on every function-like element it
    #           emits, not just Constructor/Destructor).
    # - False → confirmed user-written (clang's AST walk skips a node
    #           entirely whenever it is `isImplicit`, before it ever
    #           becomes a Function at all — so every Function this backend
    #           produces is structurally guaranteed non-implicit).
    # - None  → dumper/loader does not know (older snapshots, DWARF-only
    #           path — DWARF has no equivalent marker for this).
    # Exists to close a real bug: castxml's compiler-synthesized implicit
    # special members (and a synthesized `operator=`, which castxml gives a
    # real-looking Itanium mangled name) were leaking into the L4
    # source-ABI extractor's "reachable declaration surface" as if they
    # were genuine public API — see
    # `buildsource.source_extractors.base.entity_from_function`'s own
    # `api_relevant` computation, and AGENTS.md's "PR C" known-gaps entry
    # for the full empirical account.
    is_compiler_generated: bool | None = None
    # ADR-063 Phase 2 identity carrier (persisted since schema v28) -- see
    # ``model/entities.py``'s ``RecordType.entity_id`` for the full
    # rationale, including why this is keyword-only, excluded from
    # equality, and not yet readable by any consumer.
    entity_id: EntityId | None = field(default=None, kw_only=True, compare=False)
    # ADR-063 Phase 5 (fifth batch): Fact[...] siblings for this dataclass's
    # own ten case-(b) fields, mirroring Variable's identical fields exactly
    # -- each field's own None already unambiguously means "not captured",
    # so the generic bridge applies directly with no explicit
    # Fact.present(...) construction needed.
    contract_attributes_fact: Fact[list[str] | None] | None = field(
        default=None, kw_only=True
    )
    is_explicit_fact: Fact[bool | None] | None = field(default=None, kw_only=True)
    is_hidden_friend_fact: Fact[bool | None] | None = field(default=None, kw_only=True)
    source_header_fact: Fact[str | None] | None = field(default=None, kw_only=True)
    is_variadic_fact: Fact[bool | None] | None = field(default=None, kw_only=True)
    exception_spec_fact: Fact[str | None] | None = field(default=None, kw_only=True)
    is_override_fact: Fact[bool | None] | None = field(default=None, kw_only=True)
    hidden_friend_owner_fact: Fact[str | None] | None = field(
        default=None, kw_only=True
    )
    elf_binding_fact: Fact[SymbolBinding | None] | None = field(
        default=None, kw_only=True
    )
    # ADR-063 Phase 5 (ninth batch) -- case (a), see the field's own comment.
    deprecated_fact: Fact[str | None] | None = field(default=None, kw_only=True)
    is_compiler_generated_fact: Fact[bool | None] | None = field(
        default=None, kw_only=True
    )

    def __post_init__(self) -> None:
        self.contract_attributes, self.contract_attributes_fact = (
            bridge_legacy_and_fact(
                self.contract_attributes, self.contract_attributes_fact, None, None
            )
        )
        self.is_explicit, self.is_explicit_fact = bridge_legacy_and_fact(
            self.is_explicit, self.is_explicit_fact, None, None
        )
        self.is_hidden_friend, self.is_hidden_friend_fact = bridge_legacy_and_fact(
            self.is_hidden_friend, self.is_hidden_friend_fact, None, None
        )
        self.source_header, self.source_header_fact = bridge_legacy_and_fact(
            self.source_header, self.source_header_fact, None, None
        )
        self.is_variadic, self.is_variadic_fact = bridge_legacy_and_fact(
            self.is_variadic, self.is_variadic_fact, None, None
        )
        self.exception_spec, self.exception_spec_fact = bridge_legacy_and_fact(
            self.exception_spec, self.exception_spec_fact, None, None
        )
        self.is_override, self.is_override_fact = bridge_legacy_and_fact(
            self.is_override, self.is_override_fact, None, None
        )
        self.hidden_friend_owner, self.hidden_friend_owner_fact = (
            bridge_legacy_and_fact(
                self.hidden_friend_owner, self.hidden_friend_owner_fact, None, None
            )
        )
        self.elf_binding, self.elf_binding_fact = bridge_legacy_and_fact(
            self.elf_binding, self.elf_binding_fact, None, None
        )
        self.deprecated, self.deprecated_fact = bridge_legacy_and_fact(
            self.deprecated, self.deprecated_fact, _OMITTED_FUNC_DEPRECATED, None
        )
        self.is_compiler_generated, self.is_compiler_generated_fact = (
            bridge_legacy_and_fact(
                self.is_compiler_generated,
                self.is_compiler_generated_fact,
                None,
                None,
            )
        )


@dataclass
class Variable:
    name: str
    mangled: str
    type: str
    visibility: Visibility = Visibility.PUBLIC
    source_location: str | None = None
    is_const: bool = False  # const-qualified type (write → SIGSEGV)
    value: str | None = None  # initial value (compile-time constant, if known)
    access: AccessLevel = AccessLevel.PUBLIC  # public/protected/private
    elf_visibility: ElfVisibility | None = None  # ELF st_other (populated from .dynsym)
    # Provenance (ADR-015, schema v6) — see Function.source_header.
    source_header: str | None = None
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN
    # Declared alignment in bits: an explicit alignas / __attribute__((aligned))
    # override when present, else the variable's type's natural (computed)
    # alignment when a dumper can resolve it. None = not captured (older
    # snapshots / dumpers without support).
    alignment_bits: int | None = None
    # See Function.deprecated for the message-string convention, and
    # _OMITTED_VAR_DEPRECATED for ADR-063 Phase 5's sentinel rationale.
    deprecated: str | None = _OMITTED_VAR_DEPRECATED
    # See Function.elf_binding for the ELF-linkage rationale; same population
    # path (dumper_elf_symbols._populate_elf_visibility).
    elf_binding: SymbolBinding | None = None
    # ADR-063 Phase 2 identity carrier (persisted since schema v28) -- see
    # ``model/entities.py``'s ``RecordType.entity_id`` for the full
    # rationale, including why this is keyword-only, excluded from
    # equality, and not yet readable by any consumer.
    entity_id: EntityId | None = field(default=None, kw_only=True, compare=False)
    # ADR-063 Phase 5 (fourth batch): Fact[...] siblings for this dataclass's
    # own case-(b) fields, mirroring RecordType.source_header_fact/
    # EnumType.source_header_fact exactly — each field's own None already
    # unambiguously means "not captured", so the generic bridge applies
    # directly with no explicit Fact.present(...) construction needed
    # (unlike qualified_name_fact on the other two dataclasses).
    source_header_fact: Fact[str | None] | None = field(default=None, kw_only=True)
    alignment_bits_fact: Fact[int | None] | None = field(default=None, kw_only=True)
    # ADR-063 Phase 5 (ninth batch) -- case (a), see the field's own comment.
    deprecated_fact: Fact[str | None] | None = field(default=None, kw_only=True)
    elf_binding_fact: Fact[SymbolBinding | None] | None = field(
        default=None, kw_only=True
    )

    def __post_init__(self) -> None:
        self.source_header, self.source_header_fact = bridge_legacy_and_fact(
            self.source_header, self.source_header_fact, None, None
        )
        self.alignment_bits, self.alignment_bits_fact = bridge_legacy_and_fact(
            self.alignment_bits, self.alignment_bits_fact, None, None
        )
        self.elf_binding, self.elf_binding_fact = bridge_legacy_and_fact(
            self.elf_binding, self.elf_binding_fact, None, None
        )
        self.deprecated, self.deprecated_fact = bridge_legacy_and_fact(
            self.deprecated, self.deprecated_fact, _OMITTED_VAR_DEPRECATED, None
        )

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

"""Symbol-level ABI diff detectors (functions, variables, parameters)."""

from __future__ import annotations

import bisect
import re
from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_cxx_rules import (
    old_virtual_signatures,
    owner_class_of,
    virtual_method_addition,
)
from .diff_helpers import (
    TypeMap,
    bool_transition,
    build_type_map,
    diff_by_key,
    lookup_matched_type,
    make_change,
    type_map_key,
)
from .diff_hidden_friends import check_hidden_friend_change, diff_inline_hidden_friends
from .diff_symbols_renames import (  # noqa: F401  (public-surface re-exports)
    _CTOR_DTOR_CODE_RE as _CTOR_DTOR_CODE_RE,
    _FUNC_LIKE_TYPES as _FUNC_LIKE_TYPES,
    _OPERATOR_TOKEN_RE as _OPERATOR_TOKEN_RE,
    _RENAME_MIN_SHARED_AFFIX as _RENAME_MIN_SHARED_AFFIX,
    _after_last_top_level_scope as _after_last_top_level_scope,
    _ctor_dtor_variant as _ctor_dtor_variant,
    _diff_fingerprint_renames as _diff_fingerprint_renames,
    _drop_leading_return_type as _drop_leading_return_type,
    _fingerprints_from_elf as _fingerprints_from_elf,
    _match_declarator_group as _match_declarator_group,
    _param_signature as _param_signature,
    _param_signature_of as _param_signature_of,
    _plausible_rename as _plausible_rename,
    _rename_name_parse as _rename_name_parse,
    _return_type_of as _return_type_of,
    _shared_affix_len as _shared_affix_len,
    _should_filter_transitive_runtime_symbols as _should_filter_transitive_runtime_symbols,
    _skip_source_name as _skip_source_name,
    _skip_substitution as _skip_substitution,
    _skip_template_args as _skip_template_args,
    _strip_template_args as _strip_template_args,
    _truncate_at_param_list as _truncate_at_param_list,
    _unqualified_name as _unqualified_name,
    _unqualified_name_of as _unqualified_name_of,
    _unwrap_funcptr_declarator as _unwrap_funcptr_declarator,
)
from .diff_symbols_scalar import (  # noqa: F401  (public-surface re-exports)
    _abi_equivalent_scalar as _abi_equivalent_scalar,
    _canonical_int_spelling as _canonical_int_spelling,
    _scalar_repr as _scalar_repr,
)
from .diff_symbols_variables import _check_variable_alignment, _without_top_level_const
from .dumper_castxml import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    exported_symbol_names,
    is_abi_relevant_elf_symbol,
)
from .fact_provenance import (
    both_castxml_backed_fact,
    fact_producer,
    func_fact_key,
    var_fact_key,
)
from .model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    Variable,
    Visibility,
    canonicalize_type_name,
    cv_qualifiers_only_differ,
    func_signature_cv_only_differ,
    is_abi_surface_type_name,
    stdlib_namespaces_excluded,
)
from .name_classification import is_local_rtti_symbol

# Visibility levels that constitute the public ABI surface.
_PUBLIC_VIS = (Visibility.PUBLIC, Visibility.ELF_ONLY)


# Sentinel the dumper writes for the type/return type of a symbol whose
# signature is unknown — e.g. an ELF export from a stripped binary with no DWARF
# or header info. Diffing a known type against "?" yields a phantom change
# ("void → ?"), so type-bearing comparisons must treat "?" as "no evidence".
_UNKNOWN_TYPE = "?"


def _type_unknown(type_name: str | None) -> bool:
    return type_name is None or type_name.strip() == _UNKNOWN_TYPE


def _is_stripped_symbols_only(snap: AbiSnapshot) -> bool:
    """True when *snap* is a stripped, symbols-only dump: it exports symbols but
    carries no type-level evidence (no records/enums/typedefs, no DWARF content)
    and was flagged ``elf_only_mode`` by the dumper.

    Used to gate *parameter* comparison (RD2-5; Codex reviews on PR #275). The
    bare ``"?"`` sentinel is **not** a reliable per-function signal — castxml and
    dwarf_snapshot also emit ``"?"`` for an individually unresolved return/param
    while resolving the rest — so an empty parameter list only means "unknown
    params" when the whole snapshot is a symbols-only stub. In a real
    DWARF/header snapshot an empty list means "takes no arguments", and changes
    like ``f(void)`` → ``f(int)`` must still be diffed.
    """
    if not getattr(snap, "elf_only_mode", False):
        return False
    if snap.types or snap.enums or snap.typedefs:
        return False
    dwarf = getattr(snap, "dwarf", None)
    if dwarf is not None and (dwarf.structs or dwarf.enums):
        return False
    return bool(snap.functions or snap.variables)


def _is_local_type_rtti(mangled: str) -> bool:
    """True for typeinfo/vtable symbols of a function-local type (e.g. a lambda).

    Regression: RD2-4 (validation) — protobuf patch releases churn
    ``_ZTIZN…EUl…E_`` / ``_ZTSZN…`` typeinfo symbols for anonymous lambdas nested
    in ``Printer::WithDefs/WithVars``; they were scored as public ``var_removed``
    and drove a false ``BREAKING`` verdict on an ABI-compatible bump.
    """
    return is_local_rtti_symbol(mangled)


def _public_functions(snap: AbiSnapshot) -> dict[str, Function]:
    """Return public/ELF-only functions from *snap*.

    When ELF dynamic-symbol evidence is available, narrow the DWARF-derived
    public set to names that are actually exported (or explicitly ``= delete``,
    so an API becoming deleted stays observable). This keeps transitive
    runtime/stdlib subprograms that slipped into the DWARF DIEs out of the diff.

    The narrowing only happens when exports are present: a snapshot with no ELF
    symbol table (``elf`` absent/empty) keeps the full DWARF set untouched.

    Caveat: this trusts the ELF symbol table to be reasonably complete. A
    *partially* captured table (e.g. only a stripped ``.symtab`` subset) could in
    theory hide a genuine removal — but DWARF-primary snapshots carry the full
    ``.dynsym``, so in practice the export set is authoritative here.
    """
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    funcs = {
        k: v
        for k, v in snap.function_map.items()
        if (
            v.visibility in _PUBLIC_VIS
            and (
                v.visibility != Visibility.ELF_ONLY
                or is_abi_relevant_elf_symbol(
                    k,
                    filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
                )
            )
        )
    }
    elf = getattr(snap, "elf", None)
    if elf is None or not getattr(elf, "symbols", None):
        return funcs
    exported = exported_symbol_names(
        elf,
        FUNCTION_SYMBOL_TYPES,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
    )
    name_counts: dict[str, int] = {}
    for f in funcs.values():
        name_counts[f.name] = name_counts.get(f.name, 0) + 1
    return {
        k: v
        for k, v in funcs.items()
        if (
            k in exported
            or (v.name in exported and name_counts.get(v.name) == 1)
            or (v.is_deleted and not v.deleted_from_dwarf)
            # A synthetic constructor-overload key (castxml omitted its real
            # mangled name) can never equal a real exported symbol — it isn't
            # one, by construction (see dumper_castxml's synthesis comment).
            # Requiring an ELF match here would always fail and silently drop
            # a genuinely public, non-deleted constructor overload (case78's
            # removed / case111's added overload); its visibility was already
            # resolved from source access when castxml gave no name to check.
            or is_synthetic_ctor_key(k)
            # Same reasoning for a synthetic destructor key ("~ClassName",
            # castxml omitted the real mangled name): it can never equal a
            # real exported symbol either, so without this a genuinely
            # public virtual destructor's PUBLIC visibility
            # (_ctor_or_dtor_visibility) would still be silently dropped
            # here — necessary but not sufficient (Codex review, PR #582).
            or is_synthetic_dtor_key(k)
        )
    }


def _public_variables(snap: AbiSnapshot) -> dict[str, Variable]:
    """Return public/ELF-only variables from *snap*.

    Excludes RTTI/vtable symbols of function-local types (lambda closures and
    other in-function types): they are not nameable public ABI and only churn
    across builds (RD2-4).
    """
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    return {
        k: v
        for k, v in snap.variable_map.items()
        if (
            v.visibility in _PUBLIC_VIS
            and (
                v.visibility != Visibility.ELF_ONLY
                or is_abi_relevant_elf_symbol(
                    k,
                    filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
                )
            )
            and not _is_local_type_rtti(k)
        )
    }


def _format_params(params: list[Param]) -> str:
    """Format a parameter list as a human-readable string.

    ``Param.type`` already carries pointer/reference sigils (e.g. ``int *``,
    ``Foo &``), so we use it directly — appending ``_KIND_SUFFIX`` would
    duplicate them.
    """
    parts = [p.type for p in params]
    return ", ".join(parts) if parts else "(none)"


def _check_removed_function(
    mangled: str,
    f_old: Function,
    new_all: dict[str, Function],
    elf_only_mode: bool,
) -> Change:
    """Create a Change for a function that was removed or hidden."""
    f_hidden = new_all.get(mangled)
    if (
        f_hidden is not None
        and f_hidden.visibility == Visibility.HIDDEN
        and not (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
    ):
        return make_change(
            ChangeKind.FUNC_VISIBILITY_CHANGED,
            symbol=mangled,
            name=f_old.name,
            old_value=f_old.visibility.value,
            new_value=f_hidden.visibility.value,
        )
    removed_kind = (
        ChangeKind.FUNC_REMOVED_ELF_ONLY
        if (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
        else ChangeKind.FUNC_REMOVED
    )
    return make_change(
        removed_kind,
        symbol=mangled,
        description=f"{f_old.visibility.value.capitalize()} function removed: {f_old.name}",
        old_value=f_old.name,
    )


def _check_return_type_change(
    mangled: str,
    f_old: Function,
    f_new: Function,
    *,
    is_llp64: bool = False,
) -> list[Change]:
    """Emit a change if the return type was modified."""
    # RD2-5: a stripped side reports return_type "?"; that is unknown, not a change.
    if _type_unknown(f_old.return_type) or _type_unknown(f_new.return_type):
        return []
    if canonicalize_type_name(f_old.return_type) == canonicalize_type_name(
        f_new.return_type
    ):
        return []
    # A pointee/by-value const-or-volatile qualification change (e.g.
    # ``char *`` -> ``const char *``) does not change the return register or
    # calling convention; it is a source/API-signature difference, not a
    # binary ABI break (ISSUE-29/52: libuv/Wayland const-pointer churn).
    if cv_qualifiers_only_differ(f_old.return_type, f_new.return_type):
        return []
    # A top-level BY-VALUE cv change on the return type (``int`` -> ``volatile
    # int``) is absent from the function's mangled name entirely, unlike the
    # equivalent field/variable case — see func_signature_cv_only_differ's
    # docstring (Codex review, PR #582).
    if func_signature_cv_only_differ(f_old.return_type, f_new.return_type):
        return []
    # A name-only change between ABI-equivalent integer spellings (e.g.
    # long -> long long, size_t -> unsigned long on LP64) is not a binary ABI
    # break: same width, signedness, and calling convention.
    if _abi_equivalent_scalar(f_old.return_type, f_new.return_type, is_llp64):
        return []
    return [
        make_change(
            ChangeKind.FUNC_RETURN_CHANGED,
            symbol=mangled,
            name=f_old.name,
            old=f_old.return_type,
            new=f_new.return_type,
        )
    ]


def _params_differ(p_old: Param, p_new: Param, is_llp64: bool) -> bool:
    """Whether two positionally-matched parameters differ in an ABI-relevant way."""
    if _type_unknown(p_old.type) or _type_unknown(p_new.type):
        return False  # diffing a known type against unknown is meaningless
    if p_old.kind != p_new.kind:
        return True
    if canonicalize_type_name(p_old.type) == canonicalize_type_name(p_new.type):
        return False
    # A pointee/by-value const-or-volatile qualification change (e.g.
    # ``wl_display *`` -> ``const wl_display *``) leaves the parameter's
    # calling convention and binary layout identical — it is source/API churn,
    # not a binary ABI break (ISSUE-29/52).
    if cv_qualifiers_only_differ(p_old.type, p_new.type):
        return False
    # A top-level BY-VALUE cv change (``int`` -> ``volatile int``) is, unlike
    # the equivalent field/variable case, not merely layout-neutral but
    # genuinely absent from the function's type/mangled name — see
    # func_signature_cv_only_differ's docstring (Codex review, PR #582).
    if func_signature_cv_only_differ(p_old.type, p_new.type):
        return False
    # Same kind, different spelling: not a change if the integer types are
    # ABI-equivalent (long -> long long, size_t -> unsigned long on LP64).
    return not _abi_equivalent_scalar(p_old.type, p_new.type, is_llp64)


def _check_params_change(
    mangled: str,
    f_old: Function,
    f_new: Function,
    *,
    params_unconfirmed: bool = False,
    is_llp64: bool = False,
) -> list[Change]:
    """Emit a change if the parameter list was modified."""
    # RD2-5: suppress only when one side is a stripped symbols-only stub (its
    # empty param list is "unknown", not "zero args"). Otherwise compare
    # position-by-position, ignoring only the individual parameters whose type is
    # the unresolved "?" sentinel — diffing a known type against unknown is
    # meaningless, but an unrelated unknown must not mask a real change on a
    # fully-known parameter (e.g. f(?, int) -> f(?, long)). Parameter *count*
    # changes are always real in a resolved snapshot (Codex reviews, PR #275).
    if params_unconfirmed:
        return []
    changed: bool
    if len(f_old.params) != len(f_new.params):
        changed = True
    else:
        changed = any(
            _params_differ(p_old, p_new, is_llp64)
            for p_old, p_new in zip(f_old.params, f_new.params)
        )
    if not changed:
        return []
    return [
        make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol=mangled,
            name=f_old.name,
            old=_format_params(f_old.params),
            new=_format_params(f_new.params),
        )
    ]


def _check_ref_qualifier_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the ref-qualifier (&/&&) was modified."""
    old_rq = f_old.ref_qualifier or ""
    new_rq = f_new.ref_qualifier or ""
    if old_rq == new_rq:
        return []
    return [
        make_change(
            ChangeKind.FUNC_REF_QUAL_CHANGED,
            symbol=mangled,
            name=f_old.name,
            old=repr(old_rq),
            new=repr(new_rq),
            old_value=old_rq or "(none)",
            new_value=new_rq or "(none)",
        )
    ]


def _check_linkage_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the language linkage (extern \"C\" ↔ C++) was modified."""
    if f_old.is_extern_c == f_new.is_extern_c:
        return []
    old_linkage = 'extern "C"' if f_old.is_extern_c else "C++"
    new_linkage = 'extern "C"' if f_new.is_extern_c else "C++"
    return [
        make_change(
            ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED,
            symbol=mangled,
            name=f_old.name,
            old=old_linkage,
            new=new_linkage,
        )
    ]


def _check_noexcept_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the noexcept specifier was added or removed."""
    return bool_transition(
        f_old.is_noexcept,
        f_new.is_noexcept,
        mangled,
        added=(
            ChangeKind.FUNC_NOEXCEPT_ADDED,
            f"noexcept specifier added: {f_old.name}",
        ),
        removed=(
            ChangeKind.FUNC_NOEXCEPT_REMOVED,
            f"noexcept specifier removed: {f_old.name}",
        ),
    )


def _check_virtual_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the virtual specifier was added or removed."""
    return bool_transition(
        f_old.is_virtual,
        f_new.is_virtual,
        mangled,
        added=(ChangeKind.FUNC_VIRTUAL_ADDED, f"Function became virtual: {f_old.name}"),
        removed=(
            ChangeKind.FUNC_VIRTUAL_REMOVED,
            f"Function is no longer virtual: {f_old.name}",
        ),
    )


def _check_explicit_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the explicit specifier was added or removed.

    Tri-state: only fire when BOTH sides record explicit data. None means
    the dumper/loader couldn't determine it — typically an older snapshot
    that predates the field, or a Function/Destructor where ``explicit`` is
    N/A. Skipping in that case avoids false API_BREAK findings produced
    purely by snapshot schema evolution.
    """
    return bool_transition(
        f_old.is_explicit,
        f_new.is_explicit,
        mangled,
        skip_none=True,
        added=(
            ChangeKind.CTOR_EXPLICIT_ADDED,
            f"Constructor/conversion gained `explicit` specifier: {f_old.name}",
        ),
        added_values=("implicit", "explicit"),
        removed=(
            ChangeKind.CTOR_EXPLICIT_REMOVED,
            f"Constructor/conversion lost `explicit` specifier: {f_old.name}",
        ),
        removed_values=("explicit", "implicit"),
    )


def _check_variadic_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the C ellipsis (...) was added or removed.

    Tri-state — skip when either snapshot did not record variadicness
    (older snapshots / dumpers without the field).
    """
    return bool_transition(
        f_old.is_variadic,
        f_new.is_variadic,
        mangled,
        skip_none=True,
        added=(
            ChangeKind.FUNC_VARIADIC_ADDED,
            f"Function became variadic (gained ...): {f_old.name}",
        ),
        added_values=("fixed-arity", "variadic"),
        removed=(
            ChangeKind.FUNC_VARIADIC_REMOVED,
            f"Function is no longer variadic (lost ...): {f_old.name}",
        ),
        removed_values=("variadic", "fixed-arity"),
    )


#: Calling-convention attribute base names. When one of these flips inside
#: ``contract_attributes`` it is a parameter-passing change, not a semantic
#: contract change, so it routes to the existing BREAKING kind.
_CC_ATTRIBUTE_BASES = frozenset(
    {
        "cdecl",
        "stdcall",
        "fastcall",
        "thiscall",
        "regparm",
        "ms_abi",
        "sysv_abi",
        "vectorcall",
    }
)


def _is_cc_attribute(token: str) -> bool:
    return token.split("(", 1)[0] in _CC_ATTRIBUTE_BASES


def _check_contract_attributes_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit changes for gained/lost semantic contract attributes.

    Skips when either side did not capture attributes (None); an empty list
    means "captured, none present" and does participate. Calling-convention
    attribute flips (stdcall/regparm/ms_abi/...) route to the dedicated
    BREAKING ``CALLING_CONVENTION_CHANGED`` kind instead.
    """
    if f_old.contract_attributes is None or f_new.contract_attributes is None:
        return []
    old_attrs = set(f_old.contract_attributes)
    new_attrs = set(f_new.contract_attributes)
    if old_attrs == new_attrs:
        return []
    changes: list[Change] = []

    old_cc = {a for a in old_attrs if _is_cc_attribute(a)}
    new_cc = {a for a in new_attrs if _is_cc_attribute(a)}
    if old_cc != new_cc:
        changes.append(
            make_change(
                ChangeKind.CALLING_CONVENTION_CHANGED,
                symbol=mangled,
                description=(
                    f"Calling-convention attribute changed for {f_old.name}: "
                    f"{', '.join(sorted(old_cc)) or '(default)'} → "
                    f"{', '.join(sorted(new_cc)) or '(default)'}"
                ),
                old_value=", ".join(sorted(old_cc)) or "(default)",
                new_value=", ".join(sorted(new_cc)) or "(default)",
            )
        )
        old_attrs -= old_cc
        new_attrs -= new_cc

    gained = sorted(new_attrs - old_attrs)
    lost = sorted(old_attrs - new_attrs)
    if gained:
        changes.append(
            make_change(
                ChangeKind.FUNC_CONTRACT_ATTRIBUTE_ADDED,
                symbol=mangled,
                name=f_old.name,
                detail=", ".join(gained),
                new_value=", ".join(gained),
            )
        )
    if lost:
        changes.append(
            make_change(
                ChangeKind.FUNC_CONTRACT_ATTRIBUTE_REMOVED,
                symbol=mangled,
                name=f_old.name,
                detail=", ".join(lost),
                old_value=", ".join(lost),
            )
        )
    return changes


def _check_exception_spec_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the dynamic exception specification changed.

    ``noexcept`` transitions keep their dedicated kinds; this covers the
    legacy ``throw(...)`` spellings only. Tri-state: None = not captured.
    """
    if f_old.exception_spec is None or f_new.exception_spec is None:
        return []
    if f_old.exception_spec == f_new.exception_spec:
        return []
    return [
        make_change(
            ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED,
            symbol=mangled,
            name=f_old.name,
            old=f_old.exception_spec or "(none)",
            new=f_new.exception_spec or "(none)",
        )
    ]


def _check_vtable_index_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change when a persisting virtual method moved to another slot.

    ``vtable_index`` is modeled per-function; the per-type vtable array diff
    misses snapshots that carry indices but no reconstructed vtable list.
    Reuses TYPE_VTABLE_CHANGED — a moved slot IS a vtable reorder.
    """
    if f_old.vtable_index is None or f_new.vtable_index is None:
        return []
    if f_old.vtable_index == f_new.vtable_index:
        return []
    return [
        make_change(
            ChangeKind.TYPE_VTABLE_CHANGED,
            symbol=mangled,
            description=(
                f"vtable slot index changed for {f_old.name}: "
                f"{f_old.vtable_index} → {f_new.vtable_index}"
            ),
            old_value=str(f_old.vtable_index),
            new_value=str(f_new.vtable_index),
        )
    ]


def _check_function_signature(
    mangled: str,
    f_old: Function,
    f_new: Function,
    *,
    params_unconfirmed: bool = False,
    is_llp64: bool = False,
) -> list[Change]:
    """Compare signatures and qualifiers of two matched functions."""
    changes: list[Change] = []
    changes.extend(_check_return_type_change(mangled, f_old, f_new, is_llp64=is_llp64))
    changes.extend(
        _check_params_change(
            mangled,
            f_old,
            f_new,
            params_unconfirmed=params_unconfirmed,
            is_llp64=is_llp64,
        )
    )
    changes.extend(_check_ref_qualifier_change(mangled, f_old, f_new))
    changes.extend(_check_linkage_change(mangled, f_old, f_new))
    changes.extend(_check_noexcept_change(mangled, f_old, f_new))
    changes.extend(_check_virtual_change(mangled, f_old, f_new))
    changes.extend(check_hidden_friend_change(mangled, f_old, f_new))
    changes.extend(_check_explicit_change(mangled, f_old, f_new))
    changes.extend(_check_variadic_change(mangled, f_old, f_new))
    changes.extend(_check_contract_attributes_change(mangled, f_old, f_new))
    changes.extend(_check_exception_spec_change(mangled, f_old, f_new))
    changes.extend(_check_vtable_index_change(mangled, f_old, f_new))
    return changes


def _check_inline_transitions(
    old_map: dict[str, Function],
    new_map: dict[str, Function],
    new_snapshot: AbiSnapshot,
) -> list[Change]:
    """Detect inline/non-inline transitions for functions present in both snapshots."""
    changes: list[Change] = []
    for mangled in set(old_map) & set(new_map):
        f_old = old_map[mangled]
        f_new = new_map[mangled]
        if not f_old.is_inline and f_new.is_inline:
            new_elf = new_snapshot.elf
            still_exported = new_elf is not None and any(
                s.name == mangled for s in new_elf.symbols
            )
            changes.append(
                make_change(
                    ChangeKind.FUNC_BECAME_INLINE,
                    symbol=mangled,
                    description=(
                        f"Function became inline, symbol still exported: {f_old.name}"
                        if still_exported
                        else f"Function became inline (symbol may be removed from DSO): {f_old.name}"
                    ),
                    old_value="non-inline",
                    new_value="inline",
                )
            )
        elif f_old.is_inline and not f_new.is_inline:
            changes.append(
                make_change(
                    ChangeKind.FUNC_LOST_INLINE,
                    symbol=mangled,
                    name=f_old.name,
                    old="inline",
                    new="non-inline",
                )
            )
    return changes


def _match_old_function(
    mangled: str,
    f_old: Function,
    new_map: dict[str, Function],
    new_by_name: dict[str, list[Function]],
    new_all: dict[str, Function],
    matched_by_name: set[str],
    elf_only_mode: bool,
    params_unconfirmed: bool = False,
    is_llp64: bool = False,
) -> list[Change]:
    """Classify a single old function: matched by mangled, extern-C fallback, or removed."""
    if mangled in new_map:
        return list(
            _check_function_signature(
                mangled,
                f_old,
                new_map[mangled],
                params_unconfirmed=params_unconfirmed,
                is_llp64=is_llp64,
            )
        )

    # A function that still exists on the new side but is ``= delete``'d is a
    # deletion, not a removal: _detect_newly_deleted_functions reports it once
    # as FUNC_DELETED / FUNC_DELETED_DWARF from the full function map. When a
    # DWARF-deleted member also drops out of .dynsym, _public_functions excludes
    # it from new_map (it is no longer exported), so without this guard the old
    # exported peer would additionally be flagged FUNC_REMOVED here, double-
    # reporting the same symbol. The castxml-deleted path keeps such functions
    # in new_map and is matched above; this aligns the deleted_from_dwarf path.
    f_new_all = new_all.get(mangled)
    if (
        f_new_all is not None
        and f_new_all.is_deleted
        and f_new_all.visibility in _PUBLIC_VIS
    ):
        return []

    # Fallback by plain name when either side uses extern "C".
    # The name->Function mapping is a MULTIMAP: only fall back when there is
    # EXACTLY ONE extern-C candidate for this name, to avoid mis-pairing
    # overloaded or templated functions that share a display name.
    candidates = new_by_name.get(f_old.name, [])
    extern_c_candidates = [f for f in candidates if f.is_extern_c]
    if f_old.is_extern_c:
        # Old side is extern "C": match against the unique new extern-C peer.
        extern_c_candidates = candidates  # any single candidate is acceptable
    if len(extern_c_candidates) == 1:
        f_new = extern_c_candidates[0]
        result = list(
            _check_function_signature(
                f_old.name,
                f_old,
                f_new,
                params_unconfirmed=params_unconfirmed,
                is_llp64=is_llp64,
            )
        )
        matched_by_name.add(f_old.name)
        return result

    return [_check_removed_function(mangled, f_old, new_all, elf_only_mode)]


def _detect_newly_deleted_functions(
    old_all: dict[str, Function],
    new_all: dict[str, Function],
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
) -> list[Change]:
    """Detect functions that gained ``= delete`` between snapshots.

    FUNC_DELETED: detected via castxml is_deleted attribute (header analysis).
    FUNC_DELETED_DWARF: detected via DWARF DW_AT_deleted attribute (binary analysis).

    Only ABI-visible (PUBLIC / ELF_ONLY) functions are reported; hidden or
    internal functions are not part of the public ABI surface and must not
    produce spurious BREAKING findings.
    """
    changes: list[Change] = []
    new_elf = getattr(new_snapshot, "elf", None)
    exported = exported_symbol_names(new_elf, FUNCTION_SYMBOL_TYPES)
    old_exported = exported_symbol_names(
        getattr(old_snapshot, "elf", None), FUNCTION_SYMBOL_TYPES
    )
    # Whether the new side has an ELF symbol table at all. This tells "no ELF
    # evidence available" apart from "ELF table present but this function is not
    # exported": when a table exists, an empty *function* export set (e.g. the
    # library exports only data, or every function is hidden) is authoritative —
    # a DWARF-only DW_AT_deleted internal member is genuinely not exported and
    # must not be reported. Keying on ``exported`` truthiness instead would only
    # apply the filter when some *other* function happened to be exported.
    has_elf_symbol_table = bool(getattr(new_elf, "symbols", None))
    for mangled, f_new in new_all.items():
        if not f_new.is_deleted:
            continue
        # Suppress only a *genuinely internal* DWARF-deleted member: one that the
        # new ELF table proves is not exported AND that was not exported in the
        # old library either. A function that *was* an old export and is now
        # ``= delete``'d + dropped from .dynsym is a real deletion of a public
        # API and must still be reported (the removal-side path defers to this
        # detector for it, so suppressing here would drop the finding entirely).
        if (
            f_new.deleted_from_dwarf
            and has_elf_symbol_table
            and mangled not in exported
            and mangled not in old_exported
        ):
            continue
        # Skip functions that are not part of the public ABI surface.
        if f_new.visibility not in _PUBLIC_VIS:
            continue
        f_old_any = old_all.get(mangled)
        if f_old_any is not None and not f_old_any.is_deleted:
            kind = (
                ChangeKind.FUNC_DELETED_DWARF
                if f_new.deleted_from_dwarf
                else ChangeKind.FUNC_DELETED
            )
            changes.append(
                make_change(
                    kind,
                    symbol=mangled,
                    name=f_new.name,
                    old_value="callable",
                    new_value="deleted",
                )
            )
    return changes


@registry.detector("functions")
def _diff_functions(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    elf_only_mode = getattr(old, "elf_only_mode", False)
    # RD2-5: when one side is a stripped symbols-only stub, its parameter lists
    # are unknown (not "zero args"), so parameter diffs are unconfirmed.
    params_unconfirmed = _is_stripped_symbols_only(old) or _is_stripped_symbols_only(
        new
    )
    # LLP64 (Windows/PE): ``long`` is 32-bit, so e.g. long<->long long is a real
    # width change there; under LP64 (ELF/Mach-O) it is not. Resolves the
    # data-model-dependent integer ABI-equivalence checks below.
    is_llp64 = "pe" in (getattr(old, "platform", None), getattr(new, "platform", None))
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    # Lookups for the virtual-method-addition check below: type records
    # (via ambiguity-safe TypeMap, not a naive bare-name dict — PR #608), the
    # old surface's scope-qualified owner classes, and per-class virtual
    # signatures (to skip inherited overrides). See ``virtual_method_addition``.
    old_types = build_type_map(old.types)
    new_types = build_type_map(new.types)
    old_owner_classes = {
        owner for f in old_map.values() if (owner := owner_class_of(f)) is not None
    }
    old_virtual_sigs = old_virtual_signatures(old.function_map.values())

    # Build a lookup of ALL functions in new snapshot (including hidden).
    new_all = new.function_map

    # Build secondary index by plain name for extern-C fallback matching when
    # mangled names differ due to C/C++ compilation mode mismatch.
    # Use a multimap (name -> list) so overloaded/templated functions sharing a
    # display name are not silently collapsed to one candidate.
    new_by_name: dict[str, list[Function]] = {}
    for f in new_map.values():
        new_by_name.setdefault(f.name, []).append(f)
    matched_by_name: set[str] = set()

    for mangled, f_old in old_map.items():
        changes.extend(
            _match_old_function(
                mangled,
                f_old,
                new_map,
                new_by_name,
                new_all,
                matched_by_name,
                elf_only_mode,
                params_unconfirmed,
                is_llp64,
            )
        )

    for mangled, f_new in new_map.items():
        if mangled not in old_map and f_new.name not in matched_by_name:
            virtual_break = virtual_method_addition(
                f_new, old_owner_classes, old_types, new_types, old_virtual_sigs
            )
            changes.append(
                virtual_break
                if virtual_break is not None
                else make_change(
                    ChangeKind.FUNC_ADDED,
                    symbol=mangled,
                    new=f_new.name,
                )
            )

    old_all = old.function_map
    new_all_map = new.function_map
    changes.extend(_detect_newly_deleted_functions(old_all, new_all_map, old, new))

    # FUNC_BECAME_INLINE / FUNC_LOST_INLINE: detect inline↔non-inline transitions
    changes.extend(_check_inline_transitions(old_map, new_map, new))

    # HIDDEN_FRIEND_ADDED / HIDDEN_FRIEND_REMOVED for the inline-only case.
    # Inline hidden friends have no external symbol (visibility=HIDDEN) so
    # the public-symbol diff above does not see them. Match across versions
    # by mangled name across the FULL function map (not just public) —
    # old_map/new_map are passed too so a same-key pair already covered by
    # the public-symbol pairing above is not re-processed (Codex review).
    changes.extend(diff_inline_hidden_friends(old_all, new_all_map, old_map, new_map))

    return changes


# Word-boundary-anchored so a class whose own name merely *contains* "const"/
# "volatile" (e.g. ``myconst``) is not corrupted by the strip — a blind
# substring .replace() previously turned ``myconst`` into ``my`` and made the
# copy/move constructor look like a converting overload (Codex review).
_CV_QUALIFIER_RE = re.compile(r"\b(?:const|volatile)\b")


def _synthetic_ctor_scope(mangled: str) -> str | None:
    """Qualified scope in a castxml synthetic-ctor key (``SYNTHETIC_CTOR_KEY_PREFIX
    + "scope(params)"``), or ``None`` (Codex review, PR #608 follow-up).
    """
    if not is_synthetic_ctor_key(mangled):
        return None
    body = mangled[len(SYNTHETIC_CTOR_KEY_PREFIX) :]
    paren = body.find("(")
    return body[:paren] if paren != -1 else None


def _converting_ctors_by_class(
    snap: AbiSnapshot, class_aliases: dict[str, str]
) -> dict[str, dict[tuple[str, ...], Function]]:
    """Group each class's non-explicit, single-required-argument constructors.

    Grouped by ``class_aliases``' normalized canonical identity, not the raw
    spelling (Codex review, PR #608 follow-up) -- see ``_class_identity_aliases``.

    "Converting constructor": public, not deleted, definitively non-explicit
    (``is_explicit is False``; ``None`` is unknown and skipped), callable
    with exactly one argument. First parameter's type excludes copy/move
    constructors. Keyed by param-type tuple.
    """
    by_class: dict[str, dict[tuple[str, ...], Function]] = {}
    for f in snap.functions:
        owner = owner_class_of(f) or _synthetic_ctor_scope(f.mangled) or f.name
        canonical = class_aliases.get(owner) or class_aliases.get(
            owner.rsplit("::", 1)[-1]
        )
        if canonical is None:
            continue
        if f.is_deleted or f.is_explicit is not False:
            continue
        if f.access != AccessLevel.PUBLIC:
            continue
        if not f.params:
            continue
        required = [p for p in f.params if p.default is None]
        if len(required) > 1:
            continue
        arg_type = " ".join(
            _CV_QUALIFIER_RE.sub("", f.params[0].type).replace("&", "").split()
        )
        if arg_type == f.name:
            continue
        sig = tuple(p.type for p in f.params)
        by_class.setdefault(canonical, {})[sig] = f
    return by_class


def _class_identity_aliases(
    old_map: TypeMap[RecordType], new_map: TypeMap[RecordType]
) -> dict[str, str]:
    """Map every raw spelling ``owner_class_of``/synthetic-ctor-scope might
    produce for a matched class, on either side, to ONE shared canonical
    identity -- so old/new agree on a grouping key even when they spell the
    SAME class differently (e.g. a persisted snapshot predating namespace-
    qualified synthetic ctor keys vs. a fresh one), instead of every
    unchanged overload looking new on one side (Codex review, PR #608
    follow-up).
    """
    aliases: dict[str, str] = {}
    for t_old in old_map.values():
        t_new = lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        canonical = t_old.qualified_name or t_new.qualified_name or t_old.name
        aliases[type_map_key(t_old)] = canonical
        aliases[type_map_key(t_new)] = canonical
        # Bare-name alias only when unambiguous on both sides (mirrors
        # TypeMap's alias-safety rule) -- an unrelated class mustn't steal it.
        bare = t_old.name
        if old_map.bare_name_is_unambiguous(bare) and new_map.bare_name_is_unambiguous(
            bare
        ):
            aliases[bare] = canonical
    return aliases


@registry.detector("ctor_overload_ambiguity")
def _diff_ctor_overload_ambiguity(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a class gaining a 2nd+ non-explicit converting constructor.

    Best-effort RISK heuristic (case111): a real ambiguity depends on the
    consumer's actual call-site argument types, which no snapshot-level
    detector can see — only *count crossing from at most one converting
    constructor to two or more* is checked, on classes present on both sides
    (a brand-new class starting with 2+ is a fresh API decision, not a
    regression). Deliberately conservative: it will miss ambiguities that
    don't cross this threshold and, rarely, flag an addition that never
    collides with a real call site — see ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK.
    """
    # Ambiguity-safe, spelling-normalized matching (Codex review, PR #608
    # follow-up) — see _class_identity_aliases.
    aliases = _class_identity_aliases(
        build_type_map(old.types), build_type_map(new.types)
    )
    if not aliases:
        return []
    old_ctors = _converting_ctors_by_class(old, aliases)
    new_ctors = _converting_ctors_by_class(new, aliases)
    changes: list[Change] = []
    for cls in sorted(new_ctors):
        old_sigs = old_ctors.get(cls, {})
        new_sigs = new_ctors[cls]
        if len(new_sigs) < 2 or len(new_sigs) <= len(old_sigs):
            continue
        for sig in sorted(set(new_sigs) - set(old_sigs)):
            f = new_sigs[sig]
            changes.append(
                make_change(
                    ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK,
                    symbol=f.mangled,
                    name=cls,
                    new=f"{cls}({', '.join(sig)})",
                )
            )
    return changes


def _check_variable(
    mangled: str, v_old: Variable, v_new: Variable, *, cv_facts_reliable: bool = True
) -> list[Change]:
    """Compare a matched pair of public variables.

    *cv_facts_reliable* mirrors ``diff_types._field_type_genuinely_changed``:
    a pre-v9 CastXML snapshot silently dropped ``volatile`` from a variable's
    type spelling (no dedicated ``is_volatile`` fact to fall back on, unlike
    ``TypeField``), so an unchanged legacy-vs-fresh pair would otherwise
    misreport a breaking ``VAR_TYPE_CHANGED`` (Codex review, PR #582).
    """
    changes = _check_variable_alignment(mangled, v_old, v_new)
    # RD2-5: a stripped side reports type "?"; unknown is not a type change.
    if _type_unknown(v_old.type) or _type_unknown(v_new.type):
        return changes
    canon_old = canonicalize_type_name(v_old.type)
    canon_new = canonicalize_type_name(v_new.type)
    if canon_old != canon_new:
        # A pure TOP-LEVEL const-qualifier flip is a real, common case where
        # the type strings differ (the dumper bakes "const" into the type
        # text) but the base type is otherwise identical — that's a const
        # transition (below), not a base-type change. Only the trailing
        # (top-level) const is stripped for this comparison — a pointee-level
        # const (e.g. `int *` -> `const int *`) must still fall through to
        # VAR_TYPE_CHANGED, since the pointer itself didn't become const.
        is_pure_const_flip = (
            v_old.is_const != v_new.is_const
            and _without_top_level_const(canon_old)
            == _without_top_level_const(canon_new)
        )
        if not is_pure_const_flip:
            if not cv_facts_reliable and func_signature_cv_only_differ(
                canon_old, canon_new
            ):
                # Legacy-snapshot cv noise: the type-string difference itself
                # is untrustworthy (see this function's docstring), so don't
                # fall through to the const-transition check below either —
                # is_const may be equally unreliable for the same reason,
                # and falling through would just resurface the same false
                # positive as VAR_BECAME_CONST/VAR_LOST_CONST instead of
                # VAR_TYPE_CHANGED (Codex review, PR #589).
                return changes
            return changes + [
                make_change(
                    ChangeKind.VAR_TYPE_CHANGED,
                    symbol=mangled,
                    name=v_old.name,
                    old=v_old.type,
                    new=v_new.type,
                )
            ]
    # const-qualification transitions only matter when the type is unchanged.
    return changes + bool_transition(
        v_old.is_const,
        v_new.is_const,
        mangled,
        added=(
            ChangeKind.VAR_BECAME_CONST,
            f"Variable became const-qualified: {v_old.name} (writes now → SIGSEGV)",
        ),
        added_values=("non-const", "const"),
        removed=(
            ChangeKind.VAR_LOST_CONST,
            f"Variable lost const qualifier: {v_old.name} (ODR / inlining break)",
        ),
        removed_values=("const", "non-const"),
    )


def _var_removed(mangled: str, v_old: Variable) -> list[Change]:
    return [
        make_change(
            ChangeKind.VAR_REMOVED,
            symbol=mangled,
            name=v_old.name,
        )
    ]


def _var_added(mangled: str, v_new: Variable) -> list[Change]:
    return [
        make_change(
            ChangeKind.VAR_ADDED,
            symbol=mangled,
            name=v_new.name,
        )
    ]


@registry.detector("variables")
def _diff_variables(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    cv_facts_reliable = old.header_cv_facts_reliable and new.header_cv_facts_reliable
    return diff_by_key(
        _public_variables(old),
        _public_variables(new),
        on_removed=_var_removed,
        on_added=_var_added,
        on_common=lambda m, o, n: _check_variable(
            m, o, n, cv_facts_reliable=cv_facts_reliable
        ),
    )


def _both_header_aware(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """True only when BOTH snapshots carry *confirmed* header-tier evidence.

    ``from_headers_inferred`` is set when a legacy snapshot (one that predates
    the explicit ``from_headers`` key) is rehydrated and its header-awareness was
    only *guessed* — such a side may lack default-argument/constant data without
    it meaning "removed". Header-only detectors must require non-inferred header
    evidence on both sides so a mixed/legacy comparison never manufactures false
    ``*_REMOVED`` findings.
    """
    return (
        old.from_headers
        and not old.from_headers_inferred
        and new.from_headers
        and not new.from_headers_inferred
    )


@registry.detector("param_defaults")
def _diff_param_defaults(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect parameter default value changes/removals.

    Header-tier only: default-argument values are populated by both header-AST
    backends (castxml directly; ``dumper_clang.py`` too, falling back to a
    structural placeholder for anything beyond a bare literal). If either side
    was NOT (confirmed) parsed from headers (DWARF/symbols mode, or a
    legacy/inferred headerless snapshot), ``Param.default`` is ``None`` only
    because the value is *unavailable*, not removed — comparing would report
    every defaulted parameter as ``PARAM_DEFAULT_VALUE_REMOVED``. Skip unless
    both sides are header-aware.

    Additionally gated per-function-pair, whenever either side has a known
    header-AST producer (castxml, clang, or a hybrid merge — G28 Phase 3): the
    two backends' default VALUE representations are not cross-comparable
    (castxml keeps the real source expression; clang's is a placeholder/
    fingerprint for anything non-trivial) even though both now capture
    presence/absence correctly. This applies not only to a hybrid snapshot
    mixing both backends internally, but equally to a comparison between two
    pure single-backend snapshots — e.g. a pure ``--ast-frontend clang`` run
    on one side against a pure ``--ast-frontend castxml`` run on the other —
    since ``fact_producer`` already returns the (unconditional) producer for
    those non-hybrid cases too (Codex review). Requiring the SAME producer on
    both sides of a pair (not "castxml on both sides", which would wrongly
    suppress a same-producer clang-vs-clang pair that a plain
    ``--ast-frontend clang`` run compares just fine) avoids a false
    CHANGED/REMOVED from a representation mismatch while still catching a
    real change on either same-producer pairing.

    The per-pair skip itself only fires when BOTH producers are POSITIVELY
    known and DIFFER — never merely because one side's producer is unknown.
    An unset/``None`` ``ast_producer`` (e.g. a hand-built snapshot in a test,
    or a legacy pre-provenance baseline) makes ``fact_producer(...) is None``,
    so comparing it against a genuinely castxml-backed function on the other
    side is a perfectly legitimate same-producer comparison that must not be
    silently dropped just because one side lacks metadata it never had a
    chance to record — that would regress a previously-working legacy-
    baseline comparison into a silent miss (Codex review).
    """
    if not _both_header_aware(old, new):
        return []
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        key = func_fact_key(mangled, "param_defaults")
        old_producer = fact_producer(old, key)
        new_producer = fact_producer(new, key)
        if (
            old_producer is not None
            and new_producer is not None
            and old_producer != new_producer
        ):
            continue
        # Compare parameter defaults pairwise
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.default is not None and p_new.default is None:
                changes.append(
                    make_change(
                        ChangeKind.PARAM_DEFAULT_VALUE_REMOVED,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value=p_old.default,
                        new_value=None,
                    )
                )
            elif (
                p_old.default is not None
                and p_new.default is not None
                and p_old.default != p_new.default
            ):
                changes.append(
                    make_change(
                        ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value=p_old.default,
                        new_value=p_new.default,
                    )
                )

    return changes


@registry.detector("param_renames")
def _diff_param_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect parameter renames (same type+position, different name)."""
    changes: list[Change] = []
    # Require *explicit* header provenance on both sides. A legacy snapshot
    # predating the from_headers key has it inferred from a populated surface,
    # which a DWARF-only dump also satisfies — trusting that inference here
    # reintroduces PARAM_RENAMED/API_BREAK false positives on DWARF baselines.
    if not (old.from_headers and new.from_headers):
        return changes
    if old.from_headers_inferred or new.from_headers_inferred:
        return changes
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if (
                p_old.type == p_new.type
                and p_old.name
                and p_new.name
                and p_old.name != p_new.name
            ):
                changes.append(
                    make_change(
                        ChangeKind.PARAM_RENAMED,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(i),
                        old=p_old.name,
                        new=p_new.name,
                    )
                )

    return changes


@registry.detector("pointer_levels")
def _diff_pointer_levels(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect pointer level changes in params and return types."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)
    # RD2-5: param depths from a stripped symbols-only stub default to 0 and
    # would read as phantom level changes; suppress them. The return depth is
    # guarded independently by the unknown-return ("?") check below.
    params_unconfirmed = _is_stripped_symbols_only(old) or _is_stripped_symbols_only(
        new
    )

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue

        return_known = not (
            _type_unknown(f_old.return_type) or _type_unknown(f_new.return_type)
        )
        # Return pointer depth
        if (
            return_known
            and f_old.return_pointer_depth != f_new.return_pointer_depth
            and (f_old.return_pointer_depth > 0 or f_new.return_pointer_depth > 0)
        ):
            changes.append(
                make_change(
                    ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
                    symbol=mangled,
                    name=f_old.name,
                    old=str(f_old.return_pointer_depth),
                    new=str(f_new.return_pointer_depth),
                )
            )

        if params_unconfirmed:
            continue

        # Param pointer depths
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            # Skip individually unresolved params ("?"): depth falls back to 0
            # and would read as a phantom level change (matches _check_params_change).
            if _type_unknown(p_old.type) or _type_unknown(p_new.type):
                continue
            if p_old.pointer_depth != p_new.pointer_depth and (
                p_old.pointer_depth > 0 or p_new.pointer_depth > 0
            ):
                changes.append(
                    make_change(
                        ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old=str(p_old.pointer_depth),
                        new=str(p_new.pointer_depth),
                    )
                )

    return changes


def _is_access_narrowing(old_access: Any, new_access: Any) -> bool:
    """Return True if the access level transition is narrowing (breaking).

    Narrowing = less accessible: public→protected, public→private, protected→private.
    Widening (e.g., private→public) is backward-compatible and should NOT be flagged.
    """
    from .model import AccessLevel

    _RANK = {AccessLevel.PUBLIC: 0, AccessLevel.PROTECTED: 1, AccessLevel.PRIVATE: 2}  # pylint: disable=invalid-name
    return _RANK.get(new_access, 0) > _RANK.get(old_access, 0)


def _check_method_access_changes(
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> list[Change]:
    """Emit METHOD_ACCESS_CHANGED for narrowing method access transitions."""
    changes: list[Change] = []
    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        if f_old.access != f_new.access and _is_access_narrowing(
            f_old.access, f_new.access
        ):
            changes.append(
                make_change(
                    ChangeKind.METHOD_ACCESS_CHANGED,
                    symbol=mangled,
                    name=f_old.name,
                    old=f_old.access.value,
                    new=f_new.access.value,
                )
            )
    return changes


def _check_field_access_changes(
    old_types: Any,
    new_types: Any,
) -> list[Change]:
    """Emit FIELD_ACCESS_CHANGED for narrowing field access transitions."""
    changes: list[Change] = []
    for t_old in old_types.values():
        t_new = lookup_matched_type(old_types, new_types, t_old)
        if t_new is None:
            continue
        # Bare, not the qualified matching key -- matches the identity
        # diff_types.py detectors report field-level findings under.
        name = t_old.name
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}
        for fname, f_old_f in old_fields.items():
            f_new_f = new_fields.get(fname)
            if f_new_f is None:
                continue
            if f_old_f.access != f_new_f.access and _is_access_narrowing(
                f_old_f.access, f_new_f.access
            ):
                changes.append(
                    make_change(
                        ChangeKind.FIELD_ACCESS_CHANGED,
                        symbol=name,
                        name=name,
                        detail=fname,
                        old=f_old_f.access.value,
                        new=f_new_f.access.value,
                    )
                )
    return changes


@registry.detector("access_levels")
def _diff_access_levels(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect narrowing access level changes on methods and fields.

    Only flags narrowing transitions (public→protected/private, protected→private).
    Widening (e.g., private→public) is backward-compatible and not reported.
    """
    changes: list[Change] = []
    changes.extend(
        _check_method_access_changes(_public_functions(old), _public_functions(new))
    )
    excl = stdlib_namespaces_excluded(old, new)
    old_types = build_type_map(
        t
        for t in old.types
        if not t.is_union and is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    )
    new_types = build_type_map(
        t
        for t in new.types
        if not t.is_union and is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    )
    changes.extend(_check_field_access_changes(old_types, new_types))
    return changes


def _is_anon_field(f: Any) -> bool:
    """Return True for compiler-generated anonymous/unnamed fields."""
    return not f.name or f.name.startswith("__anon")


def _check_anon_field_at_offset(
    name: str,
    offset: int,
    f_old: Any,
    new_by_offset: dict[int, Any],
) -> Change | None:
    """Compare a single anonymous field (by offset) to what the new type has."""
    f_new = new_by_offset.get(offset)
    if f_new is None:
        return make_change(
            ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field removed at offset {offset} in {name}",
            old_value=f_old.type,
        )
    if f_old.type != f_new.type:
        return make_change(
            ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field type changed at offset {offset} in {name}",
            old_value=f_old.type,
            new_value=f_new.type,
        )
    return None


def _anon_fields_by_offset(fields: list[Any]) -> dict[int, Any]:
    """Index anonymous fields (no name or __anon prefix) by their bit offset."""
    return {
        f.offset_bits: f
        for f in fields
        if _is_anon_field(f) and f.offset_bits is not None
    }


def _check_anon_fields_for_type(name: str, t_old: Any, t_new: Any) -> list[Change]:
    """Compare anonymous fields by offset for a single matched type pair."""
    old_by_offset = _anon_fields_by_offset(t_old.fields)
    new_by_offset = _anon_fields_by_offset(t_new.fields)

    if not old_by_offset and not new_by_offset:
        return []

    changes: list[Change] = []
    for offset, f_old in old_by_offset.items():
        ch = _check_anon_field_at_offset(name, offset, f_old, new_by_offset)
        if ch is not None:
            changes.append(ch)
    return changes


@registry.detector("anon_fields")
def _diff_anon_fields(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect changes in anonymous struct/union members."""
    changes: list[Change] = []
    excl = stdlib_namespaces_excluded(old, new)
    old_map = build_type_map(
        t for t in old.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    )
    new_map = build_type_map(
        t for t in new.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    )

    for t_old in old_map.values():
        t_new = lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        # Bare, not the qualified matching key.
        name = t_old.name
        changes.extend(_check_anon_fields_for_type(name, t_old, t_new))

    return changes


def _find_rename_pairs(
    removed: set[str],
    added: set[str],
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> list[tuple[str, str]]:
    """Return (old_name, new_name) pairs where new_name has a common prefix added to old_name.

    The match condition is ``a_name.endswith(r_name)`` with ``a_name`` strictly
    longer (a prefix was prepended). The old ``endswith("_" + r_name)`` branch
    was redundant — any name ending with ``"_" + r_name`` already ends with
    ``r_name``. To avoid the O(removed × added) cross-product, index the added
    names *reversed* so the suffix test becomes a prefix lookup: a binary search
    locates the contiguous block of reversed added names that start with the
    reversed removed name. Both ``removed`` and the reversed index are iterated
    in sorted order, so the result is deterministic.
    """
    rev_index = sorted(
        (new_map[a_sym].name[::-1], new_map[a_sym].name) for a_sym in added
    )
    rev_keys = [k for k, _ in rev_index]
    pairs: list[tuple[str, str]] = []
    for r_sym in sorted(removed):
        r_name = old_map[r_sym].name
        rk = r_name[::-1]
        i = bisect.bisect_left(rev_keys, rk)
        while i < len(rev_keys) and rev_keys[i].startswith(rk):
            a_name = rev_index[i][1]
            if len(a_name) > len(r_name):
                pairs.append((r_name, a_name))
                break
            i += 1
    return pairs


def _emit_batch_rename(rename_pairs: list[tuple[str, str]]) -> list[Change]:
    """Emit a SYMBOL_RENAMED_BATCH change if all pairs share a single common prefix."""
    if len(rename_pairs) < 2:
        return []
    prefixes = {
        new_name[: new_name.rfind(old_name)] for old_name, new_name in rename_pairs
    }
    if len(prefixes) != 1:
        return []
    prefix = prefixes.pop()
    pair_desc = ", ".join(f"{o} → {n}" for o, n in rename_pairs[:5])
    if len(rename_pairs) > 5:
        pair_desc += f", ... ({len(rename_pairs)} total)"
    return [
        make_change(
            ChangeKind.SYMBOL_RENAMED_BATCH,
            symbol=f"batch_rename:{prefix}*",
            name=prefix,
            detail=f"{len(rename_pairs)} symbols ({pair_desc})",
            old_value=", ".join(o for o, _ in rename_pairs),
            new_value=", ".join(n for _, n in rename_pairs),
        )
    ]


@registry.detector("symbol_renames")
def _diff_symbol_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect batch symbol renames (namespace refactoring).

    When multiple symbols are removed and corresponding prefixed versions are
    added (e.g. ``init`` → ``mylib_init``), this indicates a namespace
    refactoring that breaks all existing consumers.

    Heuristic: if 2+ removed symbols each have a matching added symbol where
    the added name ends with the removed name (common prefix pattern), emit
    a SYMBOL_RENAMED_BATCH change.
    """
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    removed = set(old_map.keys()) - set(new_map.keys())
    added = set(new_map.keys()) - set(old_map.keys())

    if len(removed) < 2 or not added:
        return []

    rename_pairs = _find_rename_pairs(removed, added, old_map, new_map)
    return _emit_batch_rename(rename_pairs)


@registry.detector("param_restrict")
def _diff_param_restrict(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect restrict qualifier changes on parameters (ABICC: Parameter_Became_Restrict)."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.is_restrict != p_new.is_restrict:
                direction = "added" if p_new.is_restrict else "removed"
                changes.append(
                    make_change(
                        ChangeKind.PARAM_RESTRICT_CHANGED,
                        symbol=mangled,
                        name=f_old.name,
                        detail=direction,
                        old=str(p_old.name or i),
                        old_value=f"restrict={p_old.is_restrict}",
                        new_value=f"restrict={p_new.is_restrict}",
                    )
                )
    return changes


@registry.detector("func_deprecated")
def _diff_func_deprecated(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a function gaining or losing `[[deprecated]]`.

    Header-tier only, gated at the snapshot level like ``param_defaults``:
    ``Function.deprecated`` is ``None`` both for "not deprecated" and "the
    dumper doesn't capture this" (see its docstring in model.py), so a
    per-pair None check would silently miss every real transition (one side
    of a real add/remove is always None by construction). Gates per-pair on
    :func:`fact_provenance.both_castxml_backed_fact` rather than plain
    ``_both_header_aware``: the clang header backend doesn't populate
    ``Function.deprecated`` yet, so a castxml-vs-clang comparison would
    otherwise read as every deprecation having been removed (Codex review,
    PR #582). A per-pair check (rather than the whole-snapshot
    ``_both_castxml_backed``) also correctly handles a ``--ast-frontend
    hybrid`` snapshot (G28 Phase 3), where this fact is castxml-backed per
    *declaration*, not uniformly across the whole snapshot.
    """
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        if not both_castxml_backed_fact(old, new, func_fact_key(mangled, "deprecated")):
            continue
        if f_old.deprecated is None and f_new.deprecated is not None:
            changes.append(
                make_change(
                    ChangeKind.FUNC_DEPRECATED_ADDED,
                    symbol=mangled,
                    name=f_old.name,
                    detail=f_new.deprecated,
                    new_value=f_new.deprecated,
                )
            )
        elif f_old.deprecated is not None and f_new.deprecated is None:
            changes.append(
                make_change(
                    ChangeKind.FUNC_DEPRECATED_REMOVED,
                    symbol=mangled,
                    name=f_old.name,
                    old_value=f_old.deprecated,
                )
            )
    return changes


@registry.detector("func_override_specifier")
def _diff_func_override_specifier(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a virtual method gaining or losing the explicit `override` specifier.

    Tri-state, same rationale as the vtable-index/explicit checks elsewhere:
    only fire when BOTH sides record it (and only for a member-function form
    that can carry the specifier at all — see ``Function.is_override``'s
    docstring); ``None`` means not applicable / not determined, not "no
    override". Also gated per-pair on
    :func:`fact_provenance.both_castxml_backed_fact`: unlike ``is_final``,
    ``is_override`` is castxml-only today, so a clang-parsed side's
    unconditional ``None`` must not be misread as "override was removed"
    (Codex review, PR #582) — and a per-declaration check (rather than the
    whole-snapshot ``_both_castxml_backed``) is what correctly supports a
    ``--ast-frontend hybrid`` snapshot (G28 Phase 3).
    """
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        if f_old.is_override is None or f_new.is_override is None:
            continue
        if not both_castxml_backed_fact(
            old, new, func_fact_key(mangled, "is_override")
        ):
            continue
        if f_old.is_override == f_new.is_override:
            continue
        if f_new.is_override:
            changes.append(
                make_change(
                    ChangeKind.FUNC_OVERRIDE_SPECIFIER_ADDED,
                    symbol=mangled,
                    name=f_old.name,
                    old_value="no override",
                    new_value="override",
                )
            )
        else:
            changes.append(
                make_change(
                    ChangeKind.FUNC_OVERRIDE_SPECIFIER_REMOVED,
                    symbol=mangled,
                    name=f_old.name,
                    old_value="override",
                    new_value="no override",
                )
            )
    return changes


@registry.detector("var_deprecated")
def _diff_var_deprecated(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a variable gaining or losing `[[deprecated]]` (header-tier only).

    Gates per-pair on :func:`fact_provenance.both_castxml_backed_fact` — see
    ``FUNC_DEPRECATED_ADDED``'s docstring above (the clang backend doesn't
    populate ``Variable.deprecated`` yet; per-declaration gating is what
    correctly supports a ``--ast-frontend hybrid`` snapshot, G28 Phase 3).
    """
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        if not both_castxml_backed_fact(old, new, var_fact_key(mangled, "deprecated")):
            continue
        if v_old.deprecated is None and v_new.deprecated is not None:
            changes.append(
                make_change(
                    ChangeKind.VAR_DEPRECATED_ADDED,
                    symbol=mangled,
                    name=v_old.name,
                    detail=v_new.deprecated,
                    new_value=v_new.deprecated,
                )
            )
        elif v_old.deprecated is not None and v_new.deprecated is None:
            changes.append(
                make_change(
                    ChangeKind.VAR_DEPRECATED_REMOVED,
                    symbol=mangled,
                    name=v_old.name,
                    old_value=v_old.deprecated,
                )
            )
    return changes


@registry.detector("param_va_list")
def _diff_param_va_list(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect va_list parameter changes (ABICC: Parameter_Became_VaList/Non_VaList)."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if not p_old.is_va_list and p_new.is_va_list:
                changes.append(
                    make_change(
                        ChangeKind.PARAM_BECAME_VA_LIST,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value=p_old.type,
                        new_value="va_list",
                    )
                )
            elif p_old.is_va_list and not p_new.is_va_list:
                changes.append(
                    make_change(
                        ChangeKind.PARAM_LOST_VA_LIST,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value="va_list",
                        new_value=p_new.type,
                    )
                )
    return changes


@registry.detector("constants")
def _diff_constants(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect preprocessor / const-constant changes (ABICC: Changed/Added/Removed_Constant).

    Header-tier only: ``AbiSnapshot.constants`` is populated solely from castxml
    header parsing. If either side was NOT (confirmed) parsed from headers
    (DWARF/symbols mode, a snapshot taken before constant extraction, or a
    legacy/inferred headerless snapshot), its ``constants`` map is empty only
    because the data is *unavailable* — comparing would report every constant as
    removed (or added, depending on direction). Skip unless both sides are
    header-aware.
    """
    if not _both_header_aware(old, new):
        return []
    changes: list[Change] = []
    old_consts = old.constants
    new_consts = new.constants

    for name, old_val in old_consts.items():
        new_val = new_consts.get(name)
        if new_val is None:
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_REMOVED,
                    symbol=name,
                    name=name,
                    old_value=old_val,
                )
            )
        elif new_val != old_val:
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_CHANGED,
                    symbol=name,
                    name=name,
                    old=repr(old_val),
                    new=repr(new_val),
                    old_value=old_val,
                    new_value=new_val,
                )
            )

    for name, new_val in new_consts.items():
        if name not in old_consts:
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_ADDED,
                    symbol=name,
                    name=name,
                    new_value=new_val,
                )
            )
    return changes


@registry.detector("var_access")
def _diff_var_access(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect global data access level changes (ABICC: Global_Data_Became_Private/Protected/Public)."""
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        if v_old.access != v_new.access:
            if _is_access_narrowing(v_old.access, v_new.access):
                changes.append(
                    make_change(
                        ChangeKind.VAR_ACCESS_CHANGED,
                        symbol=mangled,
                        name=v_old.name,
                        old=v_old.access.value,
                        new=v_new.access.value,
                    )
                )
            else:
                changes.append(
                    make_change(
                        ChangeKind.VAR_ACCESS_WIDENED,
                        symbol=mangled,
                        name=v_old.name,
                        old=v_old.access.value,
                        new=v_new.access.value,
                    )
                )
    return changes

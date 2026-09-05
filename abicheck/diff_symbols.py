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

import re
from collections.abc import Mapping
from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .compare.constants import constant_index_pair, diff_constants
from .detector_registry import registry
from .diff_cxx_rules import (
    old_virtual_signatures,
    owner_class_of,
    virtual_method_addition,
)
from .diff_default_value_reliability import (
    constant_value_fingerprint_comparison_unreliable,
    default_value_fingerprint_comparison_unreliable,
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
from .diff_symbols_anon_fields import (
    check_anon_fields_for_type,
)
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
    emit_namespace_move_batches as emit_namespace_move_batches,
    emit_prefix_batch_rename as emit_prefix_batch_rename,
    find_namespace_move_groups as find_namespace_move_groups,
    find_prefix_rename_pairs as find_prefix_rename_pairs,
)
from .diff_symbols_scalar import (  # noqa: F401  (public-surface re-exports)
    _abi_equivalent_scalar as _abi_equivalent_scalar,
    _canonical_int_spelling as _canonical_int_spelling,
    _scalar_repr as _scalar_repr,
)
from .diff_symbols_variables import (
    _check_variable_alignment,
    _is_access_narrowing,
    _without_top_level_const,
    var_access_changes,
)
from .dumper_castxml import (
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    exported_symbol_names,
    is_abi_relevant_elf_symbol,
)
from .fact_provenance import (
    both_known_backed_fact,
    fact_producer,
    func_fact_key,
    var_fact_key,
)
from .finding_identity import SymbolIdentityIndex
from .finding_identity_ctor_dtor import (
    ctor_dtor_drift_old_by_new_key,
    iter_matched_function_pairs,
    reconcile_ctor_dtor_key_drift,
    synthetic_ctor_scope as _synthetic_ctor_scope,
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

# Real home is model/cc_attributes.py (ADR-061 D1): a pure membership test
# with no I/O, living in model so extract's tu_merge.py can use it too
# without a forbidden extract -> compare edge. Re-exported by value here
# for back-compat.
from .model.cc_attributes import is_cc_attribute as _is_cc_attribute
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
            # See Change.symbol_binding's docstring -- stamped here too, not just on removal below.
            symbol_binding=f_old.elf_binding.value if f_old.elf_binding else None,
            entity_id=f_old.entity_id or f_hidden.entity_id,
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
        # See Change.symbol_binding's docstring — None when not captured.
        symbol_binding=f_old.elf_binding.value if f_old.elf_binding else None,
        entity_id=f_old.entity_id,
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
            entity_id=f_old.entity_id or f_new.entity_id,
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
            entity_id=f_old.entity_id or f_new.entity_id,
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
            entity_id=f_old.entity_id or f_new.entity_id,
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
            entity_id=f_old.entity_id or f_new.entity_id,
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
        entity_id=f_old.entity_id or f_new.entity_id,
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
        entity_id=f_old.entity_id or f_new.entity_id,
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
        entity_id=f_old.entity_id or f_new.entity_id,
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
        entity_id=f_old.entity_id or f_new.entity_id,
    )


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
                entity_id=f_old.entity_id or f_new.entity_id,
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
                entity_id=f_old.entity_id or f_new.entity_id,
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
                entity_id=f_old.entity_id or f_new.entity_id,
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
            entity_id=f_old.entity_id or f_new.entity_id,
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
            entity_id=f_old.entity_id or f_new.entity_id,
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
    old_map: Mapping[str, Function],
    new_map: Mapping[str, Function],
    new_snapshot: AbiSnapshot,
) -> list[Change]:
    """Detect inline/non-inline transitions for functions present in both
    snapshots -- including a ctor/dtor pair only visible via synthetic-key
    format-drift reconciliation (``iter_matched_function_pairs``, PR #761
    finding 2)."""
    changes: list[Change] = []
    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
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
                    entity_id=f_old.entity_id or f_new.entity_id,
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
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )
    return changes


def _is_extern_c_function(f: Function) -> bool:
    """Eligibility predicate for the name-alias fallback below."""
    return f.is_extern_c


def _match_old_function(
    mangled: str,
    f_old: Function,
    new_index: SymbolIdentityIndex[Function],
    new_all: dict[str, Function],
    matched_by_name: set[str],
    elf_only_mode: bool,
    params_unconfirmed: bool = False,
    is_llp64: bool = False,
) -> list[Change]:
    """Classify a single old function: matched by mangled, extern-C fallback, or removed.

    ADR-049 Phase 2: both tiers of the join run through
    :class:`~abicheck.finding_identity.SymbolIdentityIndex` -- the exact-key
    tier as this index's own ``Mapping`` lookup, the ``extern "C"`` fallback
    as one ambiguity-checked alias lookup (``len(candidates) == 1``, the
    same rule the hand-rolled name multimap this replaced used, now living
    in the shared primitive every other flat join uses too).
    """
    f_new_exact = new_index.get(mangled)
    if f_new_exact is not None:
        return list(
            _check_function_signature(
                mangled,
                f_old,
                f_new_exact,
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

    # Fallback by plain name when either side uses extern "C". Only join when
    # the name resolves to EXACTLY ONE eligible candidate, so an overload set
    # or a template instantiation family sharing a display name is never
    # mis-paired -- `unique_alias_match` answers None for "no candidate" and
    # "several candidates" alike. Eligibility is unchanged: an extern-C old
    # side may match any single same-named peer (its mangled name is the bare
    # name on both sides, so a C++-mangled peer is still the same entity seen
    # through a different producer), a C++ old side only an extern-C one.
    name_match = new_index.unique_alias_match(
        f"name:{f_old.name}",
        where=None if f_old.is_extern_c else _is_extern_c_function,
    )
    if name_match is not None:
        result = list(
            _check_function_signature(
                f_old.name,
                f_old,
                name_match.declaration,
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

    FUNC_DELETED: castxml ``is_deleted`` (header analysis). FUNC_DELETED_DWARF:
    DWARF ``DW_AT_deleted`` (binary analysis). Only ABI-visible (PUBLIC /
    ELF_ONLY) functions are reported. ``drift_old_by_new_key`` covers a
    reconciled ctor/dtor pair (PR #761 finding 2).
    """
    changes: list[Change] = []
    drift_old_by_new_key = ctor_dtor_drift_old_by_new_key(old_all, new_all)
    new_elf = getattr(new_snapshot, "elf", None)
    exported = exported_symbol_names(new_elf, FUNCTION_SYMBOL_TYPES)
    old_exported = exported_symbol_names(
        getattr(old_snapshot, "elf", None), FUNCTION_SYMBOL_TYPES
    )
    # Whether the new side has an ELF symbol table at all -- "no ELF evidence
    # available" vs. "table present but this function is not exported": when a
    # table exists, an empty *function* export set is authoritative (a
    # DWARF-only DW_AT_deleted internal member is genuinely not exported).
    # Keying on ``exported`` truthiness alone would only apply this when some
    # *other* function happened to be exported.
    has_elf_symbol_table = bool(getattr(new_elf, "symbols", None))
    for mangled, f_new in new_all.items():
        if not f_new.is_deleted:
            continue
        # Suppress only a *genuinely internal* DWARF-deleted member: not
        # exported now AND not exported before either. One that *was* an old
        # export and is now ``= delete``'d + dropped from .dynsym is a real
        # deletion and must still be reported (the removal-side path defers
        # to this detector for it).
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
        f_old_any = old_all.get(mangled) or drift_old_by_new_key.get(mangled)
        if f_old_any is not None and not f_old_any.is_deleted:
            kind = (
                ChangeKind.FUNC_DELETED_DWARF
                if f_new.deleted_from_dwarf
                else ChangeKind.FUNC_DELETED
            )
            deleted_entity_id = f_old_any.entity_id or f_new.entity_id
            changes.append(
                make_change(
                    kind,
                    symbol=mangled,
                    name=f_new.name,
                    old_value="callable",
                    new_value="deleted",
                    entity_id=deleted_entity_id,
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
    # ADR-049 Phase 2: the new side's matching index. A ``Mapping`` over the
    # same keys ``_public_functions`` returns -- so every loop below is
    # unchanged and each function is still visited once -- plus the
    # ambiguity-checked alias tier ``_match_old_function``'s extern-C fallback
    # joins on. One shared primitive instead of a second hand-rolled multimap,
    # the same way ``build_type_map`` already backs flat *type* matching.
    new_map = SymbolIdentityIndex.for_functions(_public_functions(new))

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
    # Mirrors diff_types.py's own identical computation for the same pair of
    # snapshots -- see virtual_method_addition's own docstring for why it
    # needs this to decide whether TYPE_VTABLE_CHANGED would decline for a
    # reason unrelated to evidence (a legacy pre-v21 direct-clang snapshot).
    vtable_facts_reliable = (
        old.clang_vtable_facts_reliable and new.clang_vtable_facts_reliable
    )

    # Build a lookup of ALL functions in new snapshot (including hidden).
    new_all = new.function_map

    matched_by_name: set[str] = set()

    ctor_dtor_consumed_old, ctor_dtor_consumed_new, ctor_dtor_changes = (
        reconcile_ctor_dtor_key_drift(
            old_map, new_map, _check_function_signature, params_unconfirmed, is_llp64
        )
    )
    changes.extend(ctor_dtor_changes)

    for mangled, f_old in old_map.items():
        if mangled in ctor_dtor_consumed_old:
            continue
        changes.extend(
            _match_old_function(
                mangled,
                f_old,
                new_map,
                new_all,
                matched_by_name,
                elf_only_mode,
                params_unconfirmed,
                is_llp64,
            )
        )

    for mangled, f_new in new_map.items():
        if mangled in ctor_dtor_consumed_new:
            continue
        if mangled not in old_map and f_new.name not in matched_by_name:
            virtual_break = virtual_method_addition(
                f_new,
                old_owner_classes,
                old_types,
                new_types,
                old_virtual_sigs,
                old.function_map,
                new_all,
                vtable_facts_reliable=vtable_facts_reliable,
            )
            changes.append(
                virtual_break
                if virtual_break is not None
                else make_change(
                    ChangeKind.FUNC_ADDED,
                    symbol=mangled,
                    new=f_new.name,
                    entity_id=f_new.entity_id,
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
                    entity_id=f.entity_id,
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
                    entity_id=v_old.entity_id or v_new.entity_id,
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
        entity_id=v_old.entity_id or v_new.entity_id,
    )


def _var_removed(mangled: str, v_old: Variable) -> list[Change]:
    return [
        make_change(
            ChangeKind.VAR_REMOVED,
            symbol=mangled,
            name=v_old.name,
            # See Change.symbol_binding's docstring — None when not captured.
            symbol_binding=v_old.elf_binding.value if v_old.elf_binding else None,
            entity_id=v_old.entity_id,
        )
    ]


def _var_added(mangled: str, v_new: Variable) -> list[Change]:
    return [
        make_change(
            ChangeKind.VAR_ADDED,
            symbol=mangled,
            name=v_new.name,
            entity_id=v_new.entity_id,
        )
    ]


@registry.detector("variables")
def _diff_variables(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Diff public variables, joined through the shared identity index.

    ADR-049 Phase 2. Unlike functions, **no alias tier is enabled here**, and
    that is a decision rather than an omission: the only alias fallback the
    function join uses exists for ``extern "C"``, where the same entity is
    legitimately spelled two ways by two producers. A variable has no
    overload set and no C++/C linkage mismatch to heal -- its map key *is* its
    exported symbol, so a key that differs between the two sides is a
    different export, and joining the two by display name would report a
    genuine removal + addition as a modification instead. The index is still
    what performs the join, so both flat symbol paths share one implementation
    and one ambiguity contract.
    """
    cv_facts_reliable = old.header_cv_facts_reliable and new.header_cv_facts_reliable
    return diff_by_key(
        SymbolIdentityIndex.for_variables(_public_variables(old)),
        SymbolIdentityIndex.for_variables(_public_variables(new)),
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
    header-AST producer (castxml, clang, or a hybrid merge — G28 Phase 3):
    the two backends' default VALUE representations are not cross-comparable
    (castxml keeps the real source expression; clang's is a placeholder/
    fingerprint for anything non-trivial), even between two pure
    single-backend snapshots. Requiring the SAME producer on both sides (not
    "castxml on both sides", which would wrongly suppress a same-producer
    clang-vs-clang pair) avoids a false CHANGED/REMOVED from a
    representation mismatch while still catching a real change.

    The per-pair skip only fires when BOTH producers are POSITIVELY known
    and DIFFER, never merely because one side's producer is unknown: an
    unset ``ast_producer`` (a hand-built test snapshot, or a legacy
    pre-provenance baseline) must not be silently dropped as a mismatch
    just because it lacks metadata it never had a chance to record.

    A separate, narrower gate protects the VALUE-CHANGED comparison alone
    (Codex review) — see :mod:`diff_default_value_reliability`'s docstring.
    """
    if not _both_header_aware(old, new):
        return []
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    def _param_defaults_producer(snap: AbiSnapshot, f: Function) -> str | None:
        return fact_producer(snap, func_fact_key(f.mangled, "param_defaults"))

    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
        old_producer = _param_defaults_producer(old, f_old)
        new_producer = _param_defaults_producer(new, f_new)
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
                        entity_id=f_old.entity_id or f_new.entity_id,
                    )
                )
            elif (
                p_old.default is not None
                and p_new.default is not None
                and p_old.default != p_new.default
            ):
                if default_value_fingerprint_comparison_unreliable(
                    old, new, old_producer, new_producer, p_old.default, p_new.default
                ):
                    continue
                changes.append(
                    make_change(
                        ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
                        symbol=mangled,
                        name=f_old.name,
                        detail=str(p_old.name or i),
                        old_value=p_old.default,
                        new_value=p_new.default,
                        entity_id=f_old.entity_id or f_new.entity_id,
                    )
                )

    return changes


@registry.detector("param_renames")
def _diff_param_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect parameter renames (same type+position, different name)."""
    changes: list[Change] = []
    # Require *explicit* header provenance on both sides -- a legacy snapshot's
    # inferred-from-populated-surface fallback also matches a DWARF-only dump,
    # which would reintroduce PARAM_RENAMED/API_BREAK false positives.
    if not (old.from_headers and new.from_headers):
        return changes
    if old.from_headers_inferred or new.from_headers_inferred:
        return changes
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
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
                        entity_id=f_old.entity_id or f_new.entity_id,
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

    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
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
                    entity_id=f_old.entity_id or f_new.entity_id,
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
                        entity_id=f_old.entity_id or f_new.entity_id,
                    )
                )

    return changes


def _check_method_access_changes(
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> list[Change]:
    """Emit METHOD_ACCESS_CHANGED for narrowing access transitions, including a ctor/dtor pair only visible via synthetic-key format-drift reconciliation (``iter_matched_function_pairs``, PR #761 finding 2)."""
    changes: list[Change] = []
    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
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
                    entity_id=f_old.entity_id or f_new.entity_id,
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
                        entity_id=t_old.entity_id or t_new.entity_id,
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
        changes.extend(check_anon_fields_for_type(name, t_old, t_new))

    return changes


@registry.detector("symbol_renames")
def _diff_symbol_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect batch symbol renames (namespace refactoring).

    Two independent shapes, both rolled up into ``SYMBOL_RENAMED_BATCH`` and
    both implemented in the leaf ``diff_symbols_renames`` module:

    * a common *prefix* prepended to many leaf names (``init`` ->
      ``mylib_init``) — :func:`find_prefix_rename_pairs`;
    * a namespace *segment substitution* shared by many symbols
      (``tbb::detail::d1::X`` -> ``tbb::detail::d2::X`` for every ``X``) —
      :func:`find_namespace_move_groups`. A namespace move is neither a
      prefix nor a suffix of the old name, so the prefix shape above cannot
      see it and every moved symbol was reported as an unpaired
      ``func_removed``/``func_added`` with nothing tying the two halves
      together.

    The roll-up is additive: the per-symbol removals stay, because a moved
    symbol really is gone from the old name and a consumer linked against it
    really does fail to resolve.
    """
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    removed = set(old_map.keys()) - set(new_map.keys())
    added = set(new_map.keys()) - set(old_map.keys())

    if len(removed) < 2 or not added:
        return []

    changes = emit_prefix_batch_rename(
        find_prefix_rename_pairs(removed, added, old_map, new_map)
    )
    changes.extend(
        emit_namespace_move_batches(find_namespace_move_groups(removed, added))
    )
    return changes


@registry.detector("param_restrict")
def _diff_param_restrict(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect restrict qualifier changes on parameters (ABICC: Parameter_Became_Restrict).

    Header-tier only, and gated at the snapshot level for the same reason
    ``param_defaults`` is (G31 Phase C). ``Param.is_restrict`` is a plain
    bool with no "not collected" state, and it is populated ONLY by the two
    header-AST backends — DWARF, PDB, and the symbol-table paths never set
    it at all — so a side that was not (confirmed) parsed from headers reads
    as "no parameter is restrict-qualified" rather than "unknown", and
    comparing it against a header-parsed side reports every real ``restrict``
    as removed/added purely from an evidence-tier difference.

    Additionally declined when either side's restrict facts are marked
    unreliable (``AbiSnapshot.clang_restrict_facts_reliable``): the
    direct-clang backend populated this fact for the first time in schema
    v22, so a persisted pre-v22 clang/hybrid baseline's blanket ``False``
    is real-but-WRONG data — indistinguishable by value from a genuinely
    unqualified parameter, exactly like the pre-v21 clang vtable case.

    Two *reliable* header sides may safely be compared across producers:
    since v22 both backends populate this fact, and unlike ``Param.default``
    its value representation is directly cross-comparable (a plain bool,
    not a backend-specific encoding), so no same-producer check is needed —
    the same reasoning ``fact_provenance.both_known_backed_fact`` encodes
    for ``deprecated``/``is_scoped``.

    The loop itself lives in ``diff_param_qualifiers`` (this file is at the
    2000-line hard cap); the registration stays HERE so the detector keeps
    its original position in the registry, which orders findings in every
    report. See that module's docstring.
    """
    from .diff_param_qualifiers import param_restrict_changes

    if not _both_header_aware(old, new):
        return []
    if not (old.clang_restrict_facts_reliable and new.clang_restrict_facts_reliable):
        return []
    return param_restrict_changes(_public_functions(old), _public_functions(new))


@registry.detector("func_deprecated")
def _diff_func_deprecated(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a function gaining or losing `[[deprecated]]`.

    Header-tier only, gated at the snapshot level like ``param_defaults``:
    ``Function.deprecated`` is ``None`` both for "not deprecated" and "the
    dumper doesn't capture this" (see its docstring in model.py), so a
    per-pair None check would silently miss every real transition (one side
    of a real add/remove is always None by construction). Gates per-pair on
    :func:`fact_provenance.both_known_backed_fact` (not the narrower
    ``both_castxml_backed_fact``): both castxml and the direct-clang backend
    populate ``Function.deprecated`` today (G31 Phase C — see
    ``dumper_clang._clang_deprecated_message``), and the two backends'
    values are directly cross-comparable (a plain message string, not a
    backend-specific encoding), so a clang-vs-clang or clang-vs-castxml
    pair is just as comparable as a castxml-vs-castxml one. A per-pair
    check (rather than a whole-snapshot gate) also correctly handles a
    ``--ast-frontend hybrid`` snapshot (G28 Phase 3), where this fact's
    producer is recorded per *declaration*, not uniformly across the
    whole snapshot. Looks each side up under ITS OWN ``mangled`` (PR #761
    finding 3): a reconciled ctor/dtor pair's provenance lives under two
    different keys.
    """
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old, f_new in iter_matched_function_pairs(old_map, new_map):
        if fact_producer(old, func_fact_key(f_old.mangled, "deprecated")) is None:
            continue
        if fact_producer(new, func_fact_key(f_new.mangled, "deprecated")) is None:
            continue
        if f_old.deprecated is None and f_new.deprecated is not None:
            changes.append(
                make_change(
                    ChangeKind.FUNC_DEPRECATED_ADDED,
                    symbol=mangled,
                    name=f_old.name,
                    detail=f_new.deprecated,
                    new_value=f_new.deprecated,
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )
        elif f_old.deprecated is not None and f_new.deprecated is None:
            changes.append(
                make_change(
                    ChangeKind.FUNC_DEPRECATED_REMOVED,
                    symbol=mangled,
                    name=f_old.name,
                    old_value=f_old.deprecated,
                    entity_id=f_old.entity_id or f_new.entity_id,
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
    override". Gated per-pair on :func:`fact_provenance.both_known_backed_fact`
    (not the narrower ``both_castxml_backed_fact``): G31 Phase C wired real
    ``is_override`` extraction into the direct-clang backend too
    (``dumper_clang._clang_method_is_override``), so this is now a
    cross-producer, directly-comparable bool, the same shape ``deprecated``
    already has. A per-declaration check (not a whole-snapshot gate) is
    what correctly supports ``--ast-frontend hybrid``.
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
        if not both_known_backed_fact(old, new, func_fact_key(mangled, "is_override")):
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
                    entity_id=f_old.entity_id or f_new.entity_id,
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
                    entity_id=f_old.entity_id or f_new.entity_id,
                )
            )
    return changes


@registry.detector("var_deprecated")
def _diff_var_deprecated(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a variable gaining or losing `[[deprecated]]` (header-tier only).

    Gates per-pair on :func:`fact_provenance.both_known_backed_fact` — see
    ``FUNC_DEPRECATED_ADDED``'s docstring above (both castxml and the
    direct-clang backend populate ``Variable.deprecated`` today, G31 Phase C,
    with directly cross-comparable values; per-declaration gating is what
    correctly supports a ``--ast-frontend hybrid`` snapshot, G28 Phase 3).
    """
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        if not both_known_backed_fact(old, new, var_fact_key(mangled, "deprecated")):
            continue
        if v_old.deprecated is None and v_new.deprecated is not None:
            changes.append(
                make_change(
                    ChangeKind.VAR_DEPRECATED_ADDED,
                    symbol=mangled,
                    name=v_old.name,
                    detail=v_new.deprecated,
                    new_value=v_new.deprecated,
                    entity_id=v_old.entity_id or v_new.entity_id,
                )
            )
        elif v_old.deprecated is not None and v_new.deprecated is None:
            changes.append(
                make_change(
                    ChangeKind.VAR_DEPRECATED_REMOVED,
                    symbol=mangled,
                    name=v_old.name,
                    old_value=v_old.deprecated,
                    entity_id=v_old.entity_id or v_new.entity_id,
                )
            )
    return changes


@registry.detector("param_va_list")
def _diff_param_va_list(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect va_list parameter changes (ABICC: Parameter_Became_VaList/Non_VaList).

    Header-tier, "clang"-producer-ONLY (deliberately NOT "hybrid" -- unlike
    ``param_restrict``'s gate just above), and reliability-gated -- see
    ``diff_param_qualifiers.param_va_list_changes`` for the full reasoning
    (G31 Phase C continued, Codex review).

    The loop lives in ``diff_param_qualifiers`` (this file is at the
    2000-line hard cap); registration stays HERE for registry ordering.
    """
    from .diff_param_qualifiers import param_va_list_changes

    if not _both_header_aware(old, new):
        return []
    if old.ast_producer != "clang" or new.ast_producer != "clang":
        return []
    if not (old.clang_va_list_facts_reliable and new.clang_va_list_facts_reliable):
        return []
    return param_va_list_changes(_public_functions(old), _public_functions(new))


@registry.detector("constants")
def _diff_constants(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """The constant family, migrated onto the ``SemanticIR`` read index
    (ADR-063 Phase 6B's second checker cutover -- see ``compare/
    constants.py``'s own docstring for why constants went second).

    What stays here is only the *comparison-level* half: the header-tier
    gate (``AbiSnapshot.constants`` is empty, not merely absent, whenever
    either side wasn't parsed from headers -- comparing would report every
    constant as removed/added), which raw map the pair trusts (there is
    only one legacy collection here, unlike typedefs' alias-map choice), and
    the fingerprint-comparison-reliability predicate
    (``constant_value_fingerprint_comparison_unreliable``, closed over both
    snapshots -- see that function's own docstring for why a pre-
    stabilization direct-clang fingerprint can't be trusted against a fresh
    one). Detection itself moved to ``compare.constants.diff_constants``,
    which reads only through :class:`~abicheck.model.semantic_ir_index.
    SemanticIRIndex` and is forbidden by ``scripts/semantic_ir_cutover.py``
    from touching a legacy constant collection at all.

    ``constant_index_pair`` hands back the ``SemanticIR``-backed index when
    its own rendered names/values/identities exactly reproduce
    ``old.constants``/``new.constants`` on both sides, and the legacy
    adapter otherwise -- so this is a real read of the IR wherever the IR is
    faithful, and bit-for-bit the previous behavior everywhere else.

    Known limitation, not attempted here: a versioned inline namespace can
    make the same constant reachable under two qualified spellings
    (``detail::v1::x`` / ``detail::x``), double-reporting one real change.
    A value-equality merge was tried and reverted as unsound in both
    directions (merges unrelated same-valued constants; misses spellings
    that started with different values) -- see ``qualified_name_segments``'s
    module docstring for the full reasoning; a header constant has no
    identity beyond its own value to merge on safely.
    """
    if not _both_header_aware(old, new):
        return []
    old_index, new_index = constant_index_pair(
        old, new, old_constants=old.constants, new_constants=new.constants
    )
    return diff_constants(
        old_index,
        new_index,
        is_fingerprint_comparison_unreliable=(
            lambda old_value, new_value: (
                constant_value_fingerprint_comparison_unreliable(
                    old, new, old_value, new_value
                )
            )
        ),
        old_constants=old.constants,
        new_constants=new.constants,
    )


@registry.detector("var_access")
def _diff_var_access(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect global data access level changes (ABICC: Global_Data_Became_Private/Protected/Public).

    Header-tier, "castxml"-producer-ONLY (not "hybrid" either -- same
    coverage-shift risk as ``param_va_list`` above), reliability-gated --
    see ``AbiSnapshot.castxml_var_access_facts_reliable`` (G31 Phase C
    continued) for the full reasoning.
    """
    if not _both_header_aware(old, new):
        return []
    if old.ast_producer != "castxml" or new.ast_producer != "castxml":
        return []
    if not (
        old.castxml_var_access_facts_reliable and new.castxml_var_access_facts_reliable
    ):
        return []
    return var_access_changes(_public_variables(old), _public_variables(new))

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
from typing import Any

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_cxx_rules import (
    old_virtual_signatures,
    owner_class_of,
    virtual_method_addition,
)
from .diff_helpers import bool_transition, diff_by_key, make_change
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
from .elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    exported_symbol_names,
    is_abi_relevant_elf_symbol,
)
from .model import (
    AbiSnapshot,
    Function,
    Param,
    Variable,
    Visibility,
    canonicalize_type_name,
    cv_qualifiers_only_differ,
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
    return {
        k: v
        for k, v in funcs.items()
        if k in exported or (v.is_deleted and not v.deleted_from_dwarf)
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


def _check_hidden_friend_change(
    mangled: str, f_old: Function, f_new: Function
) -> list[Change]:
    """Emit a change if the hidden-friend status transitioned.

    Hidden-friend transitions: an in-class ``friend`` declaration was
    added or removed across versions. Tri-state — skip when either
    side's snapshot did not record the flag (e.g. DWARF-only path or
    an older snapshot). The matched-mangled iteration here handles
    the case where the friend has an out-of-line definition (i.e.
    a real symbol). Inline-only hidden friends never appear here
    because they have no symbol on either side; those transitions
    are picked up by ``_check_hidden_friend_additions_removals``
    below by matching on (name, params) rather than mangled name.
    """
    return bool_transition(
        f_old.is_hidden_friend,
        f_new.is_hidden_friend,
        mangled,
        skip_none=True,
        added=(
            ChangeKind.HIDDEN_FRIEND_ADDED,
            f"Function became an in-class friend declaration: {f_old.name}",
        ),
        added_values=("non-friend", "hidden friend"),
        removed=(
            ChangeKind.HIDDEN_FRIEND_REMOVED,
            f"Function is no longer an in-class friend declaration: {f_old.name}",
        ),
        removed_values=("hidden friend", "non-friend"),
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
    changes.extend(_check_hidden_friend_change(mangled, f_old, f_new))
    changes.extend(_check_explicit_change(mangled, f_old, f_new))
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

    # Lookups for the virtual-method-addition check below: type records, the
    # old surface's scope-qualified owner classes (disambiguates same-leaf
    # classes across namespaces), and per-class virtual signatures (to skip
    # inherited overrides). See ``virtual_method_addition``.
    old_types = {t.name: t for t in old.types}
    new_types = {t.name: t for t in new.types}
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
    # by mangled name across the FULL function map (not just public).
    changes.extend(_diff_inline_hidden_friends(old_all, new_all_map))

    return changes


def _diff_inline_hidden_friends(
    old_all: dict[str, Function],
    new_all: dict[str, Function],
) -> list[Change]:
    """Pick up hidden-friend additions/removals that have no public symbol.

    Inline-defined hidden friends never appear in the .so dynsym (the
    compiler emits them as `linkonce_odr`, often inlined into callers).
    They show up in the castxml snapshot with ``visibility=HIDDEN`` and
    ``is_hidden_friend=True``. The public-symbol diff above skips them.
    This pass compares across the full function map and only fires for
    functions that are flagged as hidden friends on one side.
    """
    changes: list[Change] = []
    for mangled, f_old in old_all.items():
        if not f_old.is_hidden_friend:
            continue
        if mangled in new_all:
            continue
        changes.append(
            make_change(
                ChangeKind.HIDDEN_FRIEND_REMOVED,
                symbol=mangled,
                old=f_old.name,
            )
        )
    for mangled, f_new in new_all.items():
        if not f_new.is_hidden_friend:
            continue
        if mangled in old_all:
            continue
        changes.append(
            make_change(
                ChangeKind.HIDDEN_FRIEND_ADDED,
                symbol=mangled,
                new=f_new.name,
            )
        )
    return changes


def _check_variable(mangled: str, v_old: Variable, v_new: Variable) -> list[Change]:
    """Compare a matched pair of public variables."""
    # RD2-5: a stripped side reports type "?"; unknown is not a type change.
    if _type_unknown(v_old.type) or _type_unknown(v_new.type):
        return []
    if canonicalize_type_name(v_old.type) != canonicalize_type_name(v_new.type):
        return [
            make_change(
                ChangeKind.VAR_TYPE_CHANGED,
                symbol=mangled,
                name=v_old.name,
                old=v_old.type,
                new=v_new.type,
            )
        ]
    # const-qualification transitions only matter when the type is unchanged.
    return bool_transition(
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
    return diff_by_key(
        _public_variables(old),
        _public_variables(new),
        on_removed=_var_removed,
        on_added=_var_added,
        on_common=_check_variable,
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

    Header-tier only: default-argument values are populated solely from castxml
    header parsing. If either side was NOT (confirmed) parsed from headers
    (DWARF/symbols mode, or a legacy/inferred headerless snapshot),
    ``Param.default`` is ``None`` only because the value is *unavailable*, not
    removed — comparing would report every defaulted parameter as
    ``PARAM_DEFAULT_VALUE_REMOVED``. Skip unless both sides are header-aware.
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
    old_types: dict[str, Any],
    new_types: dict[str, Any],
) -> list[Change]:
    """Emit FIELD_ACCESS_CHANGED for narrowing field access transitions."""
    changes: list[Change] = []
    for name, t_old in old_types.items():
        t_new = new_types.get(name)
        if t_new is None:
            continue
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
    old_types = {
        t.name: t
        for t in old.types
        if not t.is_union and is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    }
    new_types = {
        t.name: t
        for t in new.types
        if not t.is_union and is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    }
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
    old_map = {
        t.name: t
        for t in old.types
        if is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    }
    new_map = {
        t.name: t
        for t in new.types
        if is_abi_surface_type_name(t.name, exclude_stdlib=excl)
    }

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None:
            continue
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

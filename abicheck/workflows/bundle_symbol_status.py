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

"""The two per-symbol questions a bundle signature check asks, and their
compact projection.

Split out of ``bundle_signature_evidence.py`` for a dependency-direction
reason, not a size one. ``BundleSignatureEvidence`` stopped retaining
``Function``/``Variable`` objects and now stores these predicates' already
resolved answers, so *something* has to compute them at projection time --
and having ``bundle_models`` call back into ``bundle_signature_evidence``
for that made a real import cycle, which this repository's own
``import-cycle-growth`` gate rejects rather than allowlists (AGENTS.md:
"prefer a function-local import or moving the shared logic to a leaf module
both sides can depend on"). This is that leaf module: it depends on
``bundle_models`` one way, and nothing here imports back. It sits in
``workflows/`` because that is the responsibility package the whole
``bundle_*`` family is classified under (ADR-061); a new flat ``bundle_``
root sibling is rejected outright by ``check_architecture.py``.

There is exactly one definition of each answer, used by both the consumer
and the projection, which is what keeps a compact member and a
full-snapshot member from ever disagreeing.
"""

from __future__ import annotations

import re

from ..bundle_models import (
    BundleSignatureEvidence,
    SymbolSignatureStatus,
    symbol_signature_status,
)
from ..model import AbiSnapshot, Visibility
from ..model.export_index import build_raw_export_index, read_default_export_names
from ..model.surface_facts import (
    binary_exported,
    is_confirmed_true,
    is_export_table_only_record,
    is_legacy_derived,
)

__all__ = [
    "build_bundle_signature_evidence",
    "symbol_signature_statuses",
]

#: The sentinel `dumper_elf_fallback.py` (and any other L0-only extraction
#: path) writes into `Function.return_type`/`Param.type`/`Variable.type`
#: when no real type information exists. See that module's own
#: `Function(..., return_type="?", ...)`/`Variable(..., type="?", ...)`
#: construction -- this is the same literal, not a re-derivation of it.
_UNKNOWN_TYPE_SENTINEL = "?"


#: A parser doesn't only ever emit the bare sentinel above -- when
#: resolution fails partway through a composite type, the wrapping layer
#: still runs and produces a *composite* uncertainty marker instead
#: (Codex review, citing real parser code): `dwarf_snapshot.py`'s
#: `DW_TAG_reference_type`/`DW_TAG_rvalue_reference_type` handling emits
#: `"? &"`/`"? &&"` for a reference with no resolvable target;
#: `dumper_castxml.py`'s `PointerType`/`ReferenceType`/
#: `RValueReferenceType` handling appends `"*"`/`"&"`/`"&&"` to whatever
#: `_type_name_uncached` returned for the inner type, so an unresolved
#: pointee produces `"?*"`/`"?&"`/`"?&&"`. Both backends (plus
#: `dwarf_metadata.py`, `pdb_parser.py`) separately return the bare
#: literal `"..."` -- not the sentinel above -- when a type-resolution
#: recursion depth cap is hit. `"?"` is not a character any real C/C++
#: type spelling ever contains, so a substring check catches every one of
#: these composite forms without needing to enumerate each parser's exact
#: wrapping syntax. (`Param.is_variadic`/`Function.is_variadic` are separate
#: boolean fields -- a real C variadic parameter is never spelled `"..."`
#: as a bare `Param.type` value, so a substring check on the bare `"?"`
#: sentinel cannot misfire on one.)
#:
#: The recursion-depth-cap sentinel is not always emitted bare either --
#: `pdb_parser.py`'s `type_name()` returns `"..."` at the depth cap, but a
#: pointer/reference wrapper one level up (`f"{ref_name} *"`/
#: `f"{ref_name} &"`/`f"{ref_name} &&"`) then wraps that into
#: `"... *"`/`"... &"`/`"... &&"`, and a chain of such wrappers (pointer to
#: pointer to a depth-capped target, say) can nest further into
#: `"... * *"` and so on; `dwarf_snapshot.py`'s own `DW_TAG_pointer_type`/
#: `DW_TAG_reference_type`/`DW_TAG_rvalue_reference_type` handling does the
#: identical wrap.
#:
#: **Unlike `"?"`, a bare substring check on `"..."` is unsafe** (Codex
#: review, fresh evidence, correcting an earlier revision of this
#: docstring that claimed otherwise): a real, unrelated C/C++ type
#: spelling genuinely CAN contain the literal substring `"..."` -- a
#: variadic function-pointer parameter type like `"void (*)(int, ...)"`
#: is legitimate, complete, real evidence, not a truncated one. The regex
#: below matches only the sentinel's own finite shape: an optional
#: `const `/`volatile ` qualifier prefix (`pdb_parser.py`'s modifier
#: wrapping renders qualifiers *before* the base type, e.g. `"const
#: ..."`), the bare sentinel, then zero or more ` *`/` &`/` &&`/`[]`
#: pointer/reference/array wrapper suffixes in any combination -- e.g.
#: `"...[] *"` for a pointer to an array of depth-capped elements --
#: rather than treating any appearance of the substring anywhere in the
#: spelling as evidence of truncation.
#:
#: A third, unrelated base joined this same alternation (Codex review,
#: fresh evidence): `dwarf_snapshot.py`'s `_compute_type_name` fallback
#: branch -- reached for any DWARF type-DIE tag it has no dedicated
#: handling for (e.g. `DW_TAG_ptr_to_member_type`) -- returns
#: ``name or tag or "unknown"``. When the DIE carries no `DW_AT_name`
#: (the common case for such a tag), this leaks either the bare literal
#: `"unknown"` or the raw, unresolved DWARF tag spelling itself (e.g.
#: `"DW_TAG_ptr_to_member_type"`) as though it were a real type name --
#: neither is one, and both are subject to the identical qualifier-
#: prefix/pointer-reference-array-suffix wrapping the recursion-depth-cap
#: sentinel already is, since they pass through the same
#: `_resolve_inner_info`/`_resolve_inner_name` wrapping layer. Recognizing
#: only the bare forms would miss `"unknown *"`, `"DW_TAG_ptr_to_member_
#: type[]"`, and so on -- the same gap a bare-substring check on `"..."`
#: was already rejected for.
#:
#: The qualifier-prefix alternation only covered `const`/`volatile`, but
#: `dwarf_snapshot.py`'s identical prefix-wrapping branch also renders
#: `DW_TAG_restrict_type` as `"restrict "` and `DW_TAG_atomic_type` as
#: `"_Atomic "` (Codex review, fresh evidence) -- so `"restrict ..."`,
#: `"_Atomic unknown"`, `"restrict DW_TAG_ptr_to_member_type"` all leaked
#: through unrecognized the same way `"restrict *"`-shaped composites
#: would. Extended to every qualifier that module's own wrapping branch
#: emits, rather than only the two already-encountered examples.
_UNRESOLVED_WRAPPED_SENTINEL_RE = re.compile(
    r"^(?:const |volatile |restrict |_Atomic )*"
    r"(?:\.\.\.|unknown|DW_TAG_\w+)(?: \*| &&| &|\[\])*$"
)

#: A second, unrelated placeholder both backends emit -- not a recursion-
#: depth-cap artifact at all, but `dwarf_snapshot.py`'s `DW_TAG_
#: subroutine_type` handling and `pdb_parser.py`'s procedure/member-
#: function `type_name()` branches both render *any* function/subroutine
#: type (e.g. what a function-pointer field points to) as this exact,
#: fixed literal, unconditionally -- never the real return/parameter
#: types, regardless of depth (Codex review, fresh evidence). A field or
#: parameter carrying this spelling therefore never carries real
#: signature evidence for the type it names, on either backend.
_SUBROUTINE_TYPE_PLACEHOLDER = "fn(...)"


def _type_spelling_is_unresolved(spelling: str) -> bool:
    return (
        spelling == _SUBROUTINE_TYPE_PLACEHOLDER
        or bool(_UNRESOLVED_WRAPPED_SENTINEL_RE.match(spelling))
        or _UNKNOWN_TYPE_SENTINEL in spelling
    )


def _symbol_evidence_sufficient(
    symbol: str, snapshot: AbiSnapshot | BundleSignatureEvidence
) -> bool:
    """Does *snapshot* carry real DWARF/header-derived type evidence for
    *symbol*, as opposed to only a bare ELF export with no corroborating
    declaration?

    Checked purely from the *provider's* own snapshot -- the provider is
    the authority on what a symbol's signature actually is; an external,
    undefined import in a consumer's own snapshot carries no signature
    evidence of its own to cross-check against (a consumer's use site has
    no DWARF type for a symbol it doesn't define). Scoped to the function/
    variable entry `symbol` resolves to:

    - `visibility == Visibility.ELF_ONLY` -- an L0-only entry with no
      corroborating declaration at all (`dumper_elf_fallback.py`'s
      construction, or any other backend that degrades to it).
    - a return/variable type that is unresolved per
      `_type_spelling_is_unresolved` (the bare `"?"` sentinel, a composite
      form like `"?*"`/`"? &"`, or the recursion-depth-cap sentinel), or
      (for a function) any parameter whose own type is unresolved the same
      way -- evidence that is present in shape but not in content (a
      symbol crosschecked against *some* declaration whose own type
      resolution still failed, wholly or partway through a composite
      type).
    - (for a function) `is_variadic is None` or `contract_attributes is
      None` -- both real tri-state fields where `None` means "not
      captured by this backend" rather than a negative determination
      (`is_variadic=False`/`contract_attributes=[]` are the corresponding
      "captured, and it's not/there are none" states).
      `diff_symbols._check_variadic_change`/`_check_contract_attributes_
      change` themselves skip whenever either side's value is `None`, so
      treating an unknown value here as "the rest of the signature looks
      fine, therefore sufficient" would let a real fixed-arity<->variadic
      or calling-convention transition produce neither a confirmed
      diff-level finding nor this module's own risk finding.

    A symbol absent from both `function_map` and `variable_map` entirely
    is also treated as insufficient -- absence of any declaration entry is
    the weakest possible evidence state, not proof of a benign match.
    """
    if isinstance(snapshot, BundleSignatureEvidence):
        # A compact projection: this same function already answered for
        # this symbol, against the full snapshot, in
        # `symbol_signature_statuses` below. Absence means the symbol was
        # in neither map, which is this function's own `return False` tail.
        status = snapshot.symbol_status.get(symbol)
        return status is not None and status.evidence_sufficient
    fn = snapshot.function_map.get(symbol)
    if fn is not None:
        if is_export_table_only_record(fn):
            return False
        if fn.is_variadic is None:
            # Codex review, fresh evidence: diff_symbols._check_variadic_
            # change() itself skips (skip_none=True) whenever either side's
            # is_variadic is unknown -- a real, tri-state field, not merely
            # absent, since an older snapshot/dumper that never populated
            # it is indistinguishable here from one that positively
            # determined "not variadic". A fixed-arity<->variadic
            # transition changes the calling ABI on the platforms that
            # care, so treating unknown variadicness as "the rest of the
            # signature looks fine, therefore sufficient" would let that
            # transition produce neither a confirmed diff-level finding
            # nor this module's own unverified-risk one -- total silence
            # on a real ABI-relevant unknown.
            return False
        if fn.contract_attributes is None:
            # Codex review, fresh evidence: the identical shape as the
            # is_variadic gap above, for a different tri-state field.
            # `contract_attributes` (calling-convention attributes such as
            # `stdcall`/`ms_abi`/`vectorcall`) is `list[str] | None` --
            # `None` means "not captured by this backend" (an older
            # snapshot, or a dumper that never populates it), `[]` means
            # "captured, and there are none". `diff_symbols._check_
            # contract_attributes_change` itself skips whenever either side
            # is `None`, so treating an unknown value as sufficient would
            # let a real calling-convention transition produce neither a
            # confirmed diff-level finding nor this module's own risk
            # finding.
            return False
        if _type_spelling_is_unresolved(fn.return_type):
            return False
        return all(not _type_spelling_is_unresolved(p.type) for p in fn.params)
    var = snapshot.variable_map.get(symbol)
    if var is not None:
        if is_export_table_only_record(var):
            return False
        return not _type_spelling_is_unresolved(var.type)
    return False


def _symbol_was_exported(
    symbol: str, snapshot: AbiSnapshot | BundleSignatureEvidence
) -> bool:
    """Did *snapshot*'s own `Function`/`Variable` entry for *symbol* actually
    reach the binary's *dynamic* export table (`.dynsym`) -- as opposed to
    merely being *some* declaration, public or private, that `AbiSnapshot`
    retains?

    `Visibility.ELF_ONLY` is **not** a reliable "was exported" signal on its
    own -- it means two different things depending on how the snapshot was
    produced (Codex review, second round, citing `dumper_elf_symbols.py`'s
    own `.dynsym`-vs-`.symtab` split): on a snapshot dumped *without*
    headers at all (`AbiSnapshot.elf_only_mode == True`,
    `dumper_elf_fallback.py`), `ELF_ONLY` entries are built directly from
    the observed `.dynsym` set, so it genuinely means "exported, just no
    header/DWARF corroboration." But on a header-parsed snapshot
    (`dumper_castxml.py`/`dumper_clang.py`'s shared `_visibility()`
    policy), a declaration only reaches `ELF_ONLY` when it is present in
    `.symtab` (every global, including purely internal/static-linkage
    symbols) but **absent from `.dynsym`** -- i.e. declared, but
    *not* dynamically exported; only `Visibility.PUBLIC` means "confirmed
    in `.dynsym`" there. `diff_symbols.py`'s own `elf_only_mode and
    f_old.visibility == Visibility.ELF_ONLY` gate (its `FUNC_REMOVED_
    ELF_ONLY` vs. `FUNC_REMOVED` split) is the established precedent for
    this exact distinction, followed here rather than reinvented.

    `Visibility.HIDDEN` always means `__attribute__((visibility("hidden")))`
    -- compiled to not export, regardless of provenance -- so it is never
    treated as exported either way. A symbol absent from both maps was
    never declared at all, so it cannot have been exported.
    """
    if isinstance(snapshot, BundleSignatureEvidence):
        # As in `_symbol_evidence_sufficient`: the answer below, already
        # computed against the full snapshot.
        status = snapshot.symbol_status.get(symbol)
        return status is not None and status.exported
    fn = snapshot.function_map.get(symbol)
    entry = fn if fn is not None else snapshot.variable_map.get(symbol)
    if entry is None:
        return False
    # The export question is now its own fact (model/surface_facts.py), so a
    # producer that actually consulted an export table answers it directly
    # -- no provenance-dependent re-interpretation needed, and in
    # particular no need to guess for the combination the enum could not
    # hold (declared, promised, not exported).
    exported = binary_exported(entry)
    if not is_legacy_derived(exported):
        return is_confirmed_true(exported)
    # A pre-split snapshot: keep this function's own documented reading of
    # the conflated enum, which is strictly sharper than the bridge's.
    if entry.visibility is Visibility.PUBLIC:
        return True
    if entry.visibility is Visibility.ELF_ONLY:
        return snapshot.elf_only_mode
    return False


def symbol_signature_statuses(
    snapshot: AbiSnapshot,
) -> dict[str, SymbolSignatureStatus]:
    """Resolve both per-symbol answers for *snapshot*, once, up front.

    Called from :func:`build_bundle_signature_evidence` while the full
    snapshot is still alive, so the release can then drop it. The answers
    come from :func:`_symbol_was_exported` and
    :func:`_symbol_evidence_sufficient` themselves -- the same functions
    the consumer calls -- rather than from a second reading of the same
    rules, which is what keeps a compact member and a full-snapshot member
    from ever disagreeing (and is asserted as an invariant over both, per
    symbol, in ``tests/test_bundle_signature_evidence_projection.py``).

    Function-before-variable precedence for a name carried by both maps is
    **not** established here -- both predicates apply it themselves,
    against the snapshot, so this loop cannot get it wrong and the
    ``continue`` below is purely an optimisation that avoids resolving such
    a name twice (verified: removing it changes no answer). Said plainly
    because the obvious reading of the iteration order is that it encodes
    the precedence, and a future edit made on that belief would be
    reasoning from a guarantee this loop does not provide.
    """
    statuses: dict[str, SymbolSignatureStatus] = {}
    for symbol in (*snapshot.function_map, *snapshot.variable_map):
        if symbol in statuses:
            continue
        # Shared rather than constructed: two booleans have four
        # inhabitants, so one object per symbol is pure waste (see
        # `symbol_signature_status`'s own measurement).
        statuses[symbol] = symbol_signature_status(
            exported=_symbol_was_exported(symbol, snapshot),
            evidence_sufficient=_symbol_evidence_sufficient(symbol, snapshot),
        )
    return statuses


def build_bundle_signature_evidence(
    snapshot: AbiSnapshot,
) -> BundleSignatureEvidence:
    """The compact, declaration-free stand-in for *snapshot*.

    Replaces what used to be ``BundleSignatureEvidence.from_snapshot``. It
    lives here rather than on the dataclass because computing it needs the
    predicates above, and a classmethod reaching back for them is the
    import cycle this module exists to avoid.
    """
    # Projected here, while the full snapshot is still in hand: `elf` below
    # carries ELF members only, so a PE/Mach-O member's exports would
    # otherwise be unrecoverable from the compact form (see the field's own
    # docstring for the release-reconciliation failure that caused).
    raw_exports = build_raw_export_index(snapshot)
    return BundleSignatureEvidence(
        symbol_status=symbol_signature_statuses(snapshot),
        elf_only_mode=snapshot.elf_only_mode,
        elf=snapshot.elf,
        library_filename=snapshot.library,
        # `None` for an unread table too (gap A4): unknown, not "exports nothing".
        export_names=read_default_export_names(snapshot),
        export_platform=None if raw_exports is None else raw_exports.platform,
    )

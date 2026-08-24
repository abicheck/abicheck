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

"""C-boundary signature-evidence gate for the bundle layer (G38 Phase 4,
amendment to ADR-023 — see ``docs/contribute/plans/g38-bundle-facts-model-
and-multibuild-comparability.md``).

``abicheck.bundle``'s own ``bundle_intra_dep_signature_changed`` already
fires correctly when a provider's DWARF/header evidence shows a real
signature change on a symbol a sibling library imports. What it cannot say
is the negative case: when *at least one* side lacks that evidence (a
stripped provider, or a provider only ever dumped at L0, on either the old
or the new snapshot — not necessarily both), the bundle layer has no way
to say "this consumer's import still resolves by name, but nothing
establishes the signature agrees" — it silently reports nothing, which
reads as "compatible" even though compatibility was never actually checked.

This module answers exactly that question, as a standalone companion to
:func:`abicheck.bundle.compare_bundle` rather than a change to it:
:func:`find_unverified_signature_findings` takes the same bundle snapshots
and per-library diff results ``compare_bundle`` already receives, plus each
library's own :class:`~abicheck.model.AbiSnapshot` (old and new — the one
input ``compare_bundle`` itself does not need, since its own detectors work
entirely from ``ElfMetadata``/``DiffResult``), and returns the additional
``BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED`` findings a caller can append to
``BundleDiffResult.bundle_findings``.

Deliberately NOT folded into ``compare_bundle`` itself: ``abicheck/bundle.py``
sits at the AI-readiness 2000-line hard cap (confirmed at the time this
module was written), so any addition there needs an equal-or-greater
removal in the same change -- a scope this phase has no reason to take on.
Keeping this as a pure, additive function elsewhere costs nothing: it reads
the identical ``BundleSnapshot``/``DiffResult`` shapes ``compare_bundle``
already produces/consumes, and a caller wanting both simply calls both and
concatenates the finding lists (see this module's own tests for the exact
pattern). This also matches G38 Phase 3's `bundle_multibuild.py` precedent
(`pair_variants`/`coverage_regression_findings` are equally standalone, not
wired into `compare_bundle`) — no CLI/config surface yet calls this
function either; see this phase's status note in the plan doc.

This is a leaf module with respect to :mod:`abicheck.bundle`: it does not
import that module (only :mod:`abicheck.bundle_models` for ``BundleFinding``/
``BundleSnapshot``, and :mod:`abicheck.bundle_resolution_reachability` for
the ``DT_NEEDED``-reachability BFS both this module and ``bundle.py`` need
-- extracted into its own tiny leaf module for exactly this reason, see
its own docstring), so there is no import-cycle risk either direction.

**Provider-edge filtering (Codex review, fresh evidence):** a consumer is
only counted for a given provider when (1) the provider is actually
reachable from that consumer via a real ``DT_NEEDED`` path
(:func:`~abicheck.bundle_resolution_reachability.reachable_intra_libraries`)
and (2) the provider actually satisfies that consumer's own version/
default-binding requirement (:func:`_consumer_matches_provider`) -- both
the same constraints ``bundle._detect_unresolved_intra_dependency``
already applies to its own, more elaborate provider matching. The version
check is evaluated per (consumer, provider_entry) pair rather than that
sibling function's "does *some* provider in the whole set resolve this"
question, since this module's main loop already iterates one concrete
``provider_entry`` at a time -- it did not, in the end, need that
sibling's per-consumer resolution shape, contrary to an earlier revision
of this docstring.

A third check (Codex review, fresh evidence) guards the *old* side the
same way: a bare-name "was this symbol exported in the old snapshot"
check (:func:`_symbol_was_exported`) cannot tell a genuinely fresh symbol
*version* apart from an unrelated old-side version sharing the same bare
name -- a provider that previously exported only ``foo@V1`` and now adds
``foo@V2`` would otherwise read as "retained, evidence uncertain" for
V2 purely because *some* ``foo`` existed before.
:func:`_provider_entry_retained_from_old` closes this by matching on
``ProviderEntry.version`` at the bundle-resolution layer, which is
version-aware, rather than the version-blind ``AbiSnapshot.function_map``/
``variable_map`` layer ``_symbol_was_exported`` reads.

A fourth check (Codex review, fresh evidence) guards the evidence-
sufficiency lookup itself against the same version-blindness one layer
deeper: even a *retained* symbol (the third check above) can have
multiple co-existing GNU versions on one or both sides (``foo@V1`` and
``foo@@V2`` both still live -- an ordinary shape for a provider that has
never broken ABI compatibility), and ``AbiSnapshot.function_map``/
``variable_map`` keep only one bare-name-keyed entry regardless. Asking
whether *that* entry is "sufficient evidence" for one specific
``ProviderEntry.version`` risks silently borrowing a different version's
signature. :func:`_bare_name_version_collapsed` detects the collapse via
the bundle-resolution layer's own per-version ``ProviderEntry`` list
(which the ``AbiSnapshot`` layer does not carry) and fails the
sufficiency check closed rather than trusting the ambiguous entry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from .bundle_models import BundleFinding, BundleSnapshot, ConsumerEntry, ProviderEntry
from .bundle_resolution_reachability import reachable_intra_libraries
from .checker_policy import ChangeKind
from .checker_types import DiffResult
from .model import AbiSnapshot, Visibility

#: Every per-symbol diff kind this module's own evidence-sufficiency check
#: (`_symbol_evidence_sufficient`) can itself detect the *positive* side
#: of. The first three (`FUNC_PARAMS_CHANGED`/`FUNC_RETURN_CHANGED`/
#: `VAR_TYPE_CHANGED`) are the same three `bundle._detect_intra_dep_
#: signature_changed` promotes to `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`;
#: duplicated here (rather than imported from `bundle.py`) since that
#: module must not import this leaf-module's own detector (see this
#: module's own docstring on why it stays leaf-only) and a shared
#: constant would need a home neither module owns. `FUNC_VARIADIC_ADDED`/
#: `FUNC_VARIADIC_REMOVED`/`CALLING_CONVENTION_CHANGED` were added
#: alongside this module's own `is_variadic`/`contract_attributes`
#: sufficiency checks (Codex review, fresh evidence): without them, a
#: symbol that diff_symbols already confirmed changed on one of *those*
#: two axes, but which also happens to carry an unrelated unresolved
#: field (an unresolved parameter type, say), would still produce a
#: redundant, contradictory "cannot be confirmed or denied" finding
#: alongside the already-proven break. A confirmed change for a symbol
#: always takes precedence over the unverified kind this module emits --
#: real evidence of a break is strictly more informative than "couldn't
#: tell either way". `bundle._detect_intra_dep_signature_changed`'s own
#: `relevant_kinds` set does not (yet) include these two -- a
#: pre-existing, narrower gap in that sibling function, not something
#: this module's own precedence check needs to wait on.
#:
#: The next six kinds (Codex review, fresh evidence, filed after the three
#: above already went in) close the same coexistence gap for every *other*
#: `Function`-level fact `diff_symbols.py` can independently confirm or deny
#: with certainty, none of which `_symbol_evidence_sufficient` itself
#: inspects (it only ever reads `return_type`/`params`/`is_variadic`/
#: `contract_attributes`): `is_noexcept`/`is_virtual`/`ref_qualifier` are
#: plain (non-tri-state) `Function` fields, always confidently comparable
#: regardless of any other field's resolution state; `exception_spec` is a
#: tri-state field whose own `diff_symbols._check_exception_spec_change`
#: already skips on `None` the identical way `_check_variadic_change`/
#: `_check_contract_attributes_change` do. Any one of these being genuinely
#: confirmed for a symbol is exactly as informative as a confirmed
#: params/return/variadic/calling-convention change -- "we know something
#: concrete changed (or didn't) here," not "we couldn't tell" -- so it must
#: suppress this module's own risk finding the same way.
#:
#: Deliberately excluded: `FUNC_LANGUAGE_LINKAGE_CHANGED` (an `extern "C"`
#: transition changes the mangled name itself, so old/new can't share the
#: `symbol` key this module matches on in the first place); the
#: vtable-slot/inline-transition kinds (about virtual-dispatch layout and
#: definition placement, not the calling-signature-agreement question this
#: module exists to answer); and `CTOR_EXPLICIT_ADDED`/`CTOR_EXPLICIT_
#: REMOVED` (Codex review, fresh evidence -- tried once, reverted). Unlike
#: every kind actually included above, an `explicit` specifier transition
#: is a purely source-level fact: `checker_policy.py`'s own `ChangeKind`
#: comment for these two kinds states plainly "neither change alters the
#: mangled name," meaning it proves nothing about whether the *binary*
#: calling signature this module exists to verify (params/return/
#: variadic/calling-convention/...) actually still agrees. Including it in
#: this set let a confirmed, unrelated source-level fact silently suppress
#: a real, still-unresolved binary-signature-agreement question for the
#: same symbol.
_CONFIRMED_SIGNATURE_CHANGE_KINDS = frozenset(
    {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.FUNC_VARIADIC_ADDED,
        ChangeKind.FUNC_VARIADIC_REMOVED,
        ChangeKind.CALLING_CONVENTION_CHANGED,
        ChangeKind.FUNC_NOEXCEPT_ADDED,
        ChangeKind.FUNC_NOEXCEPT_REMOVED,
        ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED,
        ChangeKind.FUNC_REF_QUAL_CHANGED,
        ChangeKind.FUNC_VIRTUAL_ADDED,
        ChangeKind.FUNC_VIRTUAL_REMOVED,
    }
)

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


def _symbol_evidence_sufficient(symbol: str, snapshot: AbiSnapshot) -> bool:
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
    fn = snapshot.function_map.get(symbol)
    if fn is not None:
        if fn.visibility is Visibility.ELF_ONLY:
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
        if var.visibility is Visibility.ELF_ONLY:
            return False
        return not _type_spelling_is_unresolved(var.type)
    return False


def _symbol_was_exported(symbol: str, snapshot: AbiSnapshot) -> bool:
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
    fn = snapshot.function_map.get(symbol)
    entry = fn if fn is not None else snapshot.variable_map.get(symbol)
    if entry is None:
        return False
    if entry.visibility is Visibility.PUBLIC:
        return True
    if entry.visibility is Visibility.ELF_ONLY:
        return snapshot.elf_only_mode
    return False


def _basename_to_bundle_key(old: BundleSnapshot) -> dict[str, str]:
    """Map each library's real on-disk file basename to its bundle-canonical
    key (``old.libraries``' own keys -- the same version-stripped key
    :func:`~abicheck.binary_utils._canonical_library_key` produces, e.g.
    ``libfoo.so`` for a real ``libfoo.so.1.2.3``).

    :class:`~abicheck.checker_types.DiffResult.library` is always set from
    ``path.name`` (`abicheck/service.py`/`abicheck/dumper.py`, every ELF/PE/
    Mach-O dump site) -- the literal on-disk filename, not the bundle's
    canonical key -- so for any normally-versioned SONAME the two differ
    (Codex review, fresh evidence: `_confirmed_provider_symbols` previously
    compared a `DiffResult`'s raw basename directly against
    `BundleSnapshot.resolution`'s canonical keys, which never match for a
    realistically-versioned library, silently defeating the "a confirmed
    change outranks unverified" precedence for the overwhelmingly common
    case). A basename with no matching bundle entry is left unmapped --
    the caller degrades to comparing the raw basename, same as before this
    fix, rather than raising.
    """
    return {path.name: key for key, path in old.libraries.items()}


def _confirmed_provider_symbols(
    old: BundleSnapshot,
    per_library_results: Iterable[DiffResult],
) -> set[tuple[str, str]]:
    """`(provider_library, symbol)` pairs already carrying a real, diff-
    confirmed signature change -- these must never also produce an
    "unverified" finding (real evidence of a break outranks "couldn't tell
    either way").

    *provider_library* here is the bundle-canonical key (see
    :func:`_basename_to_bundle_key`), matching the key space
    `find_unverified_signature_findings`'s own main loop compares against
    (``new.resolution.provides``) -- not the raw ``DiffResult.library``
    basename this set was previously (incorrectly) keyed by.
    """
    basename_to_key = _basename_to_bundle_key(old)
    confirmed: set[tuple[str, str]] = set()
    for result in per_library_results:
        basename = Path(result.library).name
        provider_lib = basename_to_key.get(basename, basename)
        for change in result.changes:
            if change.kind in _CONFIRMED_SIGNATURE_CHANGE_KINDS:
                confirmed.add((provider_lib, change.symbol))
    return confirmed


def _consumer_matches_provider(
    consumer: ConsumerEntry, provider_entry: ProviderEntry, new: BundleSnapshot
) -> bool:
    """Does *provider_entry* actually satisfy *consumer*'s own version/
    default-binding requirement for the symbol they share?

    Mirrors ``bundle._detect_unresolved_intra_dependency``'s own
    version-aware provider matching:

    - A consumer requiring a specific version (``ConsumerEntry.version``)
      can only be satisfied by a provider definition carrying that exact
      version. When the precise ``version_soname`` is known, the match is
      further pinned to the provider library that soname actually
      resolves to (GNU version *labels* are not globally unique across
      providers).
    - An unversioned consumer reference can only be satisfied by an
      unversioned or default-version (``@@default``) provider definition
      (``ProviderEntry.is_default``) -- a provider whose only definition
      of this symbol is a non-default versioned one (``foo@V1``, not
      ``foo@@V1``) cannot satisfy it, even though the bare symbol name
      matches.
    """
    if consumer.version:
        if consumer.version_soname:
            target_lib = new.resolution.soname_to_name.get(consumer.version_soname)
            return (
                target_lib == provider_entry.library
                and provider_entry.version == consumer.version
            )
        return provider_entry.version == consumer.version
    return provider_entry.is_default


def _provider_entry_retained_from_old(
    provider_entry: ProviderEntry, old: BundleSnapshot, symbol: str
) -> bool:
    """Did *old*'s own bundle resolution graph already carry a provider
    definition of *symbol* from the same library, at the same GNU symbol
    version, as *provider_entry*?

    ``new.resolution.provides[symbol]`` can gain a fresh ``ProviderEntry``
    across a release the same way a symbol table can gain a fresh
    ``foo@V2`` definition alongside a pre-existing ``foo@V1`` one -- both
    entries share the bare *symbol* name, but only one of them is actually
    the *retained* export whose old-side signature evidence is worth
    asking about. A name-only check (``_symbol_was_exported``, which reads
    only ``AbiSnapshot.function_map``/``variable_map`` -- themselves keyed
    by bare name, with no per-version distinction) cannot tell these apart
    (Codex review, fresh evidence): it would treat a brand-new ``foo@V2``
    as "retained, evidence uncertain" purely because an unrelated
    ``foo@V1`` happened to exist on the old side, even though ``foo@V2``
    is a genuinely new export with no old-side counterpart to compare
    against. Matching on ``ProviderEntry.version`` (``""`` for an
    unversioned symbol, so two unversioned entries still match each other)
    is what actually answers "is this the same export, not just the same
    bare name" -- the same version-aware granularity
    ``_consumer_matches_provider`` already applies one layer up.
    """
    return any(
        old_pe.version == provider_entry.version
        for old_pe in old.resolution.provides.get(symbol, [])
        if old_pe.library == provider_entry.library
    )


def _consumer_retained_from_old(
    consumer: ConsumerEntry, old: BundleSnapshot, provider_lib: str, symbol: str
) -> bool:
    """Would *consumer* have resolved *symbol* from *provider_lib* against
    *old*'s own bundle resolution graph too -- i.e. is this genuinely a
    retained edge for *this specific consumer*, not one only newly made
    reachable by a default-binding change?

    Retention is not a uniform fact about the new provider entry alone
    (Codex review, fresh evidence): matching purely on
    ``ProviderEntry.version`` (:func:`_provider_entry_retained_from_old`)
    can hold while the *binding* that actually makes the new entry
    reachable to a given consumer did not exist on the old side. A
    concrete example: old exports only ``foo@V1`` (``is_default=False``);
    new exports the identical ``foo@@V1``, now marked default. An
    unversioned consumer binds only to a default definition
    (``_consumer_matches_provider``'s own rule) -- it could not have
    resolved ``foo`` from this provider in *old* at all, so for that
    consumer specifically the new binding is a genuinely new capability,
    not a retained one whose signature could have silently changed. A
    consumer requiring the specific version ``V1`` is unaffected either
    way: its own match rule never inspects ``is_default``, so it was
    already reachable to the identical old definition and stays counted.

    Deliberately does *not* replace :func:`_provider_entry_retained_from_
    old` -- an unversioned consumer's own match rule ignores symbol
    version entirely, so checking only per-consumer reachability would
    treat a provider entry with a genuinely new, never-before-existing
    version as "retained" merely because *some* old default entry (of a
    different version) satisfies an unversioned consumer. The two checks
    answer different questions and both must hold: does this exact
    version/entry have old-side evidence at all, and would this specific
    consumer have been able to reach *a* compatible old-side entry.
    """
    return any(
        _consumer_matches_provider(consumer, old_pe, old)
        for old_pe in old.resolution.provides.get(symbol, [])
        if old_pe.library == provider_lib
    )


def _bare_name_version_collapsed(
    snapshot: BundleSnapshot, provider_lib: str, symbol: str
) -> bool:
    """Does *snapshot*'s own bundle resolution graph record more than one
    distinct GNU symbol version of *symbol* exported by *provider_lib*?

    ``AbiSnapshot.function_map``/``variable_map`` carry exactly one
    ``Function``/``Variable`` entry per bare symbol name -- an ordinary
    provider that has never broken ABI compatibility across a versioned
    release routinely retains multiple live definitions of the same bare
    name (``foo@V1`` *and* ``foo@@V2``), and that single model entry
    cannot be attributed to any one specific version; it reflects
    whichever definition the header/DWARF parser happened to associate
    with the bare name (Codex review, fresh evidence -- the same
    last-entry-wins collapse this repo's own root ``AGENTS.md`` already
    documents for ``ElfMetadata.symbol_map``). Evaluating
    ``_symbol_evidence_sufficient`` against that single entry for a
    *specific* ``ProviderEntry.version`` would silently borrow whichever
    version's signature the model happened to keep, reading as "fully
    evidenced" for a version that was never actually captured. The bundle
    resolution graph, unlike ``AbiSnapshot``, keeps one ``ProviderEntry``
    per version -- exactly the granularity needed to detect the collapse,
    even though it cannot recover the lost per-version signature data
    itself. When collapsed, evidence sufficiency must fail closed rather
    than trust the ambiguous single entry.
    """
    versions = {
        pe.version
        for pe in snapshot.resolution.provides.get(symbol, [])
        if pe.library == provider_lib
    }
    return len(versions) > 1


def find_unverified_signature_findings(
    old: BundleSnapshot,
    new: BundleSnapshot,
    per_library_results: Iterable[DiffResult],
    old_snapshots: Mapping[str, AbiSnapshot],
    new_snapshots: Mapping[str, AbiSnapshot],
) -> list[BundleFinding]:
    """`BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` findings: a sibling library's
    undefined import resolves by name to a provider's export in *new* (the
    same C-linkage match `compare_bundle`'s own `BUNDLE_INTRA_DEP_SIGNATURE_
    CHANGED` detector uses), but the provider's own type evidence for that
    exact symbol cannot confirm or deny that the signature actually agrees
    between *old* and *new* -- distinct from both "no change" (evidence
    agrees) and the confirmed, `BREAKING` `BUNDLE_INTRA_DEP_SIGNATURE_
    CHANGED` (evidence disagrees).

    *old* is used for two things: it resolves each `DiffResult.library` (a
    real on-disk basename) back to its bundle-canonical key via
    :func:`_basename_to_bundle_key`, for the "a confirmed change already
    exists" precedence check below (see that function's docstring for why
    `compare_bundle`'s own signature does not need this), and it supplies
    the version-aware retained-export check
    (:func:`_provider_entry_retained_from_old`) that skips a freshly-added
    symbol *version* sharing an old bare name. It plays no role in the
    per-side evidence-sufficiency check itself, which reads only
    *old_snapshots*/*new_snapshots*.

    *old_snapshots*/*new_snapshots* map bundle-relative library name (the
    same canonical key `BundleSnapshot.libraries` uses) to that library's
    own `AbiSnapshot` -- the one input `compare_bundle` itself never
    receives. A provider absent from either mapping, or whose symbol was
    not actually part of the *old* side's export surface (`_symbol_was_
    exported`), is skipped for that symbol -- this covers both a genuine
    addition (no declaration on the old side at all) and a symbol that
    existed on the old side only as a private/internal declaration
    (`Visibility.HIDDEN`): `AbiSnapshot` deliberately retains a library's
    private declarations alongside its public ones, so mere presence in
    `function_map`/`variable_map` does not by itself mean the old binary
    ever exported it -- a symbol newly exported in *new* must not inherit
    "retained, evidence uncertain" status from an unrelated old-side
    declaration the old export table never actually carried (Codex
    review). This function only ever compares a symbol that was a real
    export on both sides.

    One finding per (consumer, provider, symbol) triple, mirroring
    `compare_bundle`'s own `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` granularity
    exactly, so a caller merging both finding lists gets a consistent shape
    regardless of which of the two kinds a given triple produced.
    """
    results = list(per_library_results)
    confirmed = _confirmed_provider_symbols(old, results)
    findings: list[BundleFinding] = []
    seen: set[tuple[str, str, str]] = set()
    reachable_cache: dict[str, set[str]] = {}

    def _reachable(lib: str) -> set[str]:
        if lib not in reachable_cache:
            reachable_cache[lib] = reachable_intra_libraries(new, lib)
        return reachable_cache[lib]

    for symbol, providers in new.resolution.provides.items():
        for provider_entry in providers:
            provider_lib = provider_entry.library
            version_collapsed = _bare_name_version_collapsed(
                old, provider_lib, symbol
            ) or _bare_name_version_collapsed(new, provider_lib, symbol)
            if (provider_lib, symbol) in confirmed and not version_collapsed:
                # A version-blind confirmed change is only trustworthy
                # precedence when there is no version ambiguity to begin
                # with -- when the bare name has collapsed onto multiple
                # co-existing versions, `confirmed`'s own (provider_lib,
                # symbol) key can't tell which version the diff-confirmed
                # change actually describes, so it must not silently
                # suppress the unverified finding for a *different*
                # version pinned to the same bare name.
                continue

            # Restricted to consumers that can actually reach *provider_lib*
            # via a real DT_NEEDED path (Codex review, fresh evidence): a
            # bare `consumers_of(symbol)` is name-only and set-wide, the
            # same limitation `bundle._detect_unresolved_intra_dependency`'s
            # own docstring documents for its own naive alternative -- two
            # unrelated libraries can each export a same-named symbol
            # without either one being loadable together with a given
            # consumer, and this loop's "which consumers does this provider
            # affect" question is exactly the one reachability answers.
            #
            # Also restricted to consumers whose own version/default-binding
            # requirement *this exact provider_entry* actually satisfies
            # (Codex review, fresh evidence): `consumers_of(symbol)` matches
            # by bare name only, so a consumer requiring `foo@V2` previously
            # still paired with a `provider_entry` whose only definition is
            # `foo@V1` -- a provider that cannot actually satisfy that
            # consumer at all (a real resolution failure, already covered
            # by `BUNDLE_UNRESOLVED_INTRA_DEPENDENCY`/
            # `BUNDLE_INTRA_DEP_REMOVED`, not a signature-mismatch risk
            # this module exists to flag). Mirrors `_detect_unresolved_
            # intra_dependency`'s own version/`version_soname`/`is_default`
            # compatibility rules, evaluated per (consumer, provider_entry)
            # pair rather than that function's "does *some* provider in the
            # whole set resolve this" question, since this loop already
            # iterates one concrete provider_entry at a time.
            consumer_libs = sorted(
                {
                    c.library
                    for c in new.resolution.consumers_of(symbol)
                    if c.library != provider_lib
                    and provider_lib in _reachable(c.library)
                    and _consumer_matches_provider(c, provider_entry, new)
                    # A consumer that could not have resolved this exact
                    # symbol from this provider under *old*'s own bindings
                    # (e.g. a default-binding change just made it newly
                    # reachable) has no old-side signature to be
                    # "unverified" against for this edge specifically
                    # (Codex review, fresh evidence) -- see
                    # _consumer_retained_from_old's own docstring for why
                    # this is a per-consumer question, not subsumed by the
                    # per-provider-entry _provider_entry_retained_from_old
                    # check below.
                    and _consumer_retained_from_old(c, old, provider_lib, symbol)
                }
            )
            if not consumer_libs:
                continue

            old_snap = old_snapshots.get(provider_lib)
            new_snap = new_snapshots.get(provider_lib)
            if old_snap is None or new_snap is None:
                continue
            if not _symbol_was_exported(symbol, old_snap):
                # Not a retained symbol whose evidence is in doubt -- either
                # entirely new (never declared in the old snapshot at all),
                # or was only ever a private/internal declaration there
                # (Visibility.HIDDEN) that this bundle's export table never
                # actually carried. Either way, the old side has nothing to
                # be "unverified" about: a genuinely new export doesn't
                # inherit uncertainty from an unrelated old-side declaration
                # that was never part of the export surface.
                continue
            if not _provider_entry_retained_from_old(provider_entry, old, symbol):
                # Not the same export retained across the release -- a
                # different, freshly-added version of this bare-named
                # symbol, so there is no old-side signature for it to be
                # "unverified" against.
                continue

            if version_collapsed:
                # The single bare-name-keyed AbiSnapshot entry can't be
                # attributed to this specific version -- fail closed
                # rather than trust evidence that may belong to a
                # different co-existing version of this symbol.
                old_sufficient = new_sufficient = False
            else:
                old_sufficient = _symbol_evidence_sufficient(symbol, old_snap)
                new_sufficient = _symbol_evidence_sufficient(symbol, new_snap)
            if old_sufficient and new_sufficient:
                continue

            if not old_sufficient and not new_sufficient:
                evidence_gap = "neither side has"
            elif not old_sufficient:
                evidence_gap = "the old side lacks"
            else:
                evidence_gap = "the new side lacks"

            for consumer_lib in consumer_libs:
                key = (consumer_lib, provider_lib, symbol)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    BundleFinding(
                        kind=ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED,
                        symbol=symbol,
                        description=(
                            f"{consumer_lib} calls {symbol} (mangled name "
                            f"unchanged), which {provider_lib} still exports "
                            f"by that name -- but {evidence_gap} real "
                            f"DWARF/header type evidence for this symbol, so "
                            f"whether ABI compatibility actually still holds "
                            f"cannot be confirmed or denied."
                        ),
                        consumer_library=consumer_lib,
                        provider_library=provider_lib,
                        affected_libraries=[consumer_lib],
                    )
                )
    return findings

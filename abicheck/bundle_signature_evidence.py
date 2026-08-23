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
``BundleSnapshot``, which ``bundle.py`` itself already depends on), so
there is no import-cycle risk either direction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .bundle_models import BundleFinding, BundleSnapshot
from .checker_policy import ChangeKind
from .checker_types import DiffResult
from .model import AbiSnapshot, Visibility

#: The same three per-symbol diff kinds `bundle._detect_intra_dep_signature_
#: changed` promotes to `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` -- duplicated
#: here (three enum members) rather than imported from `bundle.py`, since
#: that module must not import this leaf-module's own detector (see this
#: module's own docstring on why it stays leaf-only) and a shared constant
#: would need a home neither module owns. A confirmed change for a symbol
#: always takes precedence over the unverified kind this module emits --
#: real evidence of a break is strictly more informative than "couldn't
#: tell either way".
_CONFIRMED_SIGNATURE_CHANGE_KINDS = frozenset(
    {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
    }
)

#: The sentinel `dumper_elf_fallback.py` (and any other L0-only extraction
#: path) writes into `Function.return_type`/`Param.type`/`Variable.type`
#: when no real type information exists. See that module's own
#: `Function(..., return_type="?", ...)`/`Variable(..., type="?", ...)`
#: construction -- this is the same literal, not a re-derivation of it.
_UNKNOWN_TYPE_SENTINEL = "?"


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
    - a return/variable type equal to the `"?"` unknown-type sentinel, or
      (for a function) any parameter whose own type is `"?"` -- evidence
      that is present in shape but not in content (a symbol crosschecked
      against *some* declaration whose own type resolution still failed).

    A symbol absent from both `function_map` and `variable_map` entirely
    is also treated as insufficient -- absence of any declaration entry is
    the weakest possible evidence state, not proof of a benign match.
    """
    fn = snapshot.function_map.get(symbol)
    if fn is not None:
        if fn.visibility is Visibility.ELF_ONLY:
            return False
        if fn.return_type == _UNKNOWN_TYPE_SENTINEL:
            return False
        return all(p.type != _UNKNOWN_TYPE_SENTINEL for p in fn.params)
    var = snapshot.variable_map.get(symbol)
    if var is not None:
        if var.visibility is Visibility.ELF_ONLY:
            return False
        return var.type != _UNKNOWN_TYPE_SENTINEL
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


def _confirmed_provider_symbols(
    per_library_results: Iterable[DiffResult],
) -> set[tuple[str, str]]:
    """`(provider_library, symbol)` pairs already carrying a real, diff-
    confirmed signature change -- these must never also produce an
    "unverified" finding (real evidence of a break outranks "couldn't tell
    either way")."""
    confirmed: set[tuple[str, str]] = set()
    for result in per_library_results:
        provider_lib = Path(result.library).name
        for change in result.changes:
            if change.kind in _CONFIRMED_SIGNATURE_CHANGE_KINDS:
                confirmed.add((provider_lib, change.symbol))
    return confirmed


def find_unverified_signature_findings(
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
    confirmed = _confirmed_provider_symbols(results)
    findings: list[BundleFinding] = []
    seen: set[tuple[str, str, str]] = set()

    for symbol, providers in new.resolution.provides.items():
        for provider_entry in providers:
            provider_lib = provider_entry.library
            if (provider_lib, symbol) in confirmed:
                continue

            consumer_libs = sorted(
                {
                    c.library
                    for c in new.resolution.consumers_of(symbol)
                    if c.library != provider_lib
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

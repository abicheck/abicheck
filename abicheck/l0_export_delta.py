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

"""ADR-049 Phase 5 (§6.3): the one L0 hard-removal fold shared by ``compare``
and ``scan --against``'s baseline comparison.

Before this module, the same "re-resolve both sides symbols-only, diff them
unscoped, and keep only the ``func_removed_elf_only`` fact" logic was
hand-copied in two places -- ``cli_helpers_compare.fold_l0_hard_removals``
(direct ``compare``) and the since-retired ``cli_scan_baseline._run_baseline_compare`` (``scan
--against``) -- each docstring explicitly cross-referencing the other as its
twin. :func:`collect_l0_export_delta` is the single implementation both now
call; this is the first concrete step of Phase 5's "route both direct compare
and scan baseline compare through the same core" goal, not the goal itself --
the two call sites still differ in every other stage (policy, suppression,
public-surface scoping, exit-code derivation), which is real, tracked,
still-open Phase 5 work (see the plan doc's Phase 5 progress note).

A function present in the ELF/DWARF exports can be entirely absent from the
header AST -- most commonly because it is declared behind a
consumer-controlled macro the header pass parses without knowing the real
build's ``-D`` set (``examples/case97_api_depends_on_consumer_env``). When
that happens the function never enters the header-scoped model on either
side, so the diff has nothing to compare it against and a real ``BREAKING``
removal is missed. Re-resolving both sides symbols-only (bypassing the
header AST entirely) and diffing them unscoped recovers that one hard fact
without manufacturing anything the ELF layer doesn't already assert
(ADR-028 D3: artifact-backed evidence stays authoritative).

Deliberately narrow: this module owns only the "resolve two paths
symbols-only and extract the hard fact" core. It does NOT own staleness
checking against an already-resolved snapshot's own ``source_path`` (that
stays in ``cli_helpers_compare.fold_l0_hard_removals``, since only the
direct-``compare`` call site re-derives paths from snapshots that could have
been read from a stale pre-dumped JSON file; ``scan --against`` already holds
the real, freshly-resolved ``Path`` objects and has nothing to go stale
against).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .model.change import Change

if TYPE_CHECKING:
    from .model import AbiSnapshot


def _elf_symbol_identities(snapshot: object) -> frozenset[tuple[object, ...]] | None:
    elf = getattr(snapshot, "elf", None)
    symbols = getattr(elf, "symbols", None) if elf is not None else None
    if not symbols:
        return None
    return frozenset(
        (
            sym.name,
            getattr(sym.binding, "value", sym.binding),
            getattr(sym.sym_type, "value", sym.sym_type),
            sym.version,
            sym.is_default,
            sym.visibility,
            sym.origin_lib,
        )
        for sym in symbols
    )


def elf_exports_cannot_lose_symbol(old: object, new: object) -> bool:
    """True when *new*'s ELF symbol table provably keeps every *old* symbol.

    A ``func_removed_elf_only`` fact needs an OLD export with no NEW
    counterpart, so when every OLD symbol reappears in NEW with the same
    name, binding, type, version, default-ness, visibility and origin, a
    symbols-only re-diff cannot produce one and
    :func:`collect_l0_export_delta` would return ``()``. Conservative: any
    side without a captured, non-empty ELF symbol table answers ``False``
    (fall through to the real probe), never ``True``.
    """
    old_ids = _elf_symbol_identities(old)
    if old_ids is None:
        return False
    new_ids = _elf_symbol_identities(new)
    if new_ids is None:
        return False
    return old_ids <= new_ids


def symbols_only_view(snapshot: object, lang: str) -> AbiSnapshot | None:
    """The symbols-only snapshot a re-resolve of *snapshot*'s binary builds,
    assembled from the ELF table *snapshot* already carries.

    A symbols-only ELF resolve reads exactly two things from the file: the
    dynamic symbol table (which ``_elf_classify_symbols`` rebuilds from
    ``ElfMetadata.symbols`` whenever that list is captured) and a cheap
    debug-presence probe that no ``func_removed_elf_only`` fact depends on.
    So for a snapshot whose ELF table was captured -- and whose binary the
    caller has already identity-checked -- re-reading the file buys nothing:
    on a real oneCCL comparison it was 1.1 s (13% of the run) and found
    nothing the in-memory tables could not.

    ``None`` when there is no captured ELF table to build from (a PE/Mach-O
    snapshot, a parse failure, a table that is legitimately empty); the
    caller then re-resolves from the path, as before.
    """
    from .extract.elf_symbol_classify import _elf_classify_symbols
    from .extract.header_ast_backend import lang_to_profile
    from .model.dwarf_facts import AdvancedDwarfMetadata, DwarfMetadata
    from .workflows.dump.elf_fallback import _build_symbol_only_snapshot

    elf = getattr(snapshot, "elf", None)
    source_path = getattr(snapshot, "source_path", None)
    if (
        elf is None
        or not getattr(elf, "symbols", None)
        or not getattr(elf, "machine", "")
        or not source_path
    ):
        return None
    path = Path(source_path)
    _, funcs, objects, tls = _elf_classify_symbols(elf, set(), library_name=path.name)
    return _build_symbol_only_snapshot(
        path,
        "",
        elf,
        DwarfMetadata(),
        AdvancedDwarfMetadata(),
        funcs,
        objects,
        tls,
        [],
        lang_to_profile(lang),
    )


def collect_l0_export_delta_from_snapshots(
    old: object, new: object, lang: str
) -> tuple[Change, ...]:
    """:func:`collect_l0_export_delta` for two already-resolved snapshots.

    Builds each side's symbols-only view from its own captured ELF table
    (:func:`symbols_only_view`) and re-reads a binary only for a side that
    has none. The caller owns the precondition that each snapshot still
    describes the file at its ``source_path``.
    """
    old_view = symbols_only_view(old, lang)
    new_view = symbols_only_view(new, lang)
    if old_view is None or new_view is None:
        return collect_l0_export_delta(
            Path(getattr(old, "source_path", "")),
            Path(getattr(new, "source_path", "")),
            lang,
        )
    return _hard_removals(old_view, new_view)


def _hard_removals(l0_old: AbiSnapshot, l0_new: AbiSnapshot) -> tuple[Change, ...]:
    from .errors import AbicheckError
    from .policy.evaluate import evaluate
    from .workflows.compare_policy import compare_snapshots

    try:
        l0_diff = compare_snapshots(
            l0_old, l0_new, extra_changes=[], scope_to_public_surface=False
        )
    except AbicheckError:
        return ()
    return tuple(
        change
        for change in evaluate(l0_diff).breaking
        if getattr(getattr(change, "kind", None), "value", None)
        == "func_removed_elf_only"
    )


def collect_l0_export_delta(
    old_path: Path, new_path: Path, lang: str
) -> tuple[Change, ...]:
    """Re-resolve *old_path*/*new_path* symbols-only and return the hard
    ``func_removed_elf_only`` facts between them.

    Best-effort: if either path cannot be resolved (e.g. it no longer
    exists, or isn't a real binary/snapshot), returns an empty tuple rather
    than raising -- this is an enrichment on top of an already-succeeded
    compare, not something that should ever abort it.
    """
    # Imported from their real workflows-package owners, not the flat
    # abicheck.service facade -- ADR-061 gap A: service.py also re-exports
    # frontends-classified service_render.render_output, and this module is
    # workflows-classified, so importing service.py directly here would
    # widen that workflows -> frontends edge instead of letting it close.
    from .errors import AbicheckError
    from .workflows.input_resolution import resolve_input

    # This deliberately re-resolves both sides with no headers -- the point
    # is to see what ELF/DWARF alone exports -- so the "no headers provided"
    # note `resolve_input` would otherwise log is expected, not a real input
    # problem; swallow it rather than confuse the user with a warning about
    # an internal probe they didn't ask for.
    try:
        l0_old = resolve_input(
            old_path,
            [],
            [],
            version="",
            lang=lang,
            symbols_only=True,
            notify=lambda _msg: None,
        )
        l0_new = resolve_input(
            new_path,
            [],
            [],
            version="",
            lang=lang,
            symbols_only=True,
            notify=lambda _msg: None,
        )
    except AbicheckError:
        return ()
    # compare_snapshots is a thin wrapper over checker.compare -- a failure
    # there is just as much a "this best-effort probe didn't pan out" case as
    # a resolve_input failure, so `_hard_removals` swallows it too
    # (Codex/CodeRabbit review, carried over from the original compare-side
    # implementation).
    return _hard_removals(l0_old, l0_new)


def fold_l0_hard_removals(
    old: object,
    new: object,
    lang: str,
    extra_changes: list[Change] | None,
) -> list[Change] | None:
    """Preserve hard ELF-only removals a header-scoped compare could hide.

    A function present in the ELF/DWARF exports can be entirely absent from
    the header AST — most commonly because it is declared behind a
    consumer-controlled macro the header pass parses without knowing the
    real build's `-D` set (``examples/case97_api_depends_on_consumer_env``:
    the header AST is parsed once per compare, with no signal for which
    macro state the *binary* was actually built under, so a macro-gated
    declaration silently drops out on both sides). When that happens the
    function never enters the header-scoped model on either side, so the
    diff has nothing to compare it against and a real ``BREAKING`` removal
    is missed.

    Delegates the actual "resolve both inputs symbols-only and diff them
    unscoped" extraction to :func:`abicheck.l0_export_delta.collect_l0_export_delta`
    (ADR-049 Phase 5 §6.3) -- the same function the retired ``cli_scan_baseline._run_baseline_compare``
    called for ``scan --against`` (PR #494 originally hand-copied this
    logic in both places, locked in by ``tests/test_pr494_scan_regressions.py``;
    this function's own contribution beyond that shared core is only the
    staleness check below, since only this call site re-derives paths from
    an already-resolved snapshot that could have been read from a stale
    pre-dumped JSON file). Per ADR-028 D3 (artifact-backed evidence stays
    authoritative), this only restores a fact the ELF layer already
    asserts; it cannot manufacture a break that isn't really there.

    Re-resolves from each snapshot's own ``source_path`` — the binary it was
    actually dumped from — rather than the compare CLI's raw input paths, so
    this also covers the ``dump`` (with `-H`) *then* ``compare snap1.json
    snap2.json`` two-step workflow, not just a direct ``compare a.so b.so
    -H``: a pre-dumped JSON snapshot carries no `-H` flag of its own for
    ``compare`` to see, but it does remember the binary it came from.

    Best-effort: a raw binary input to re-resolve may not be available
    (e.g. a hand-authored JSON snapshot with no real ``source_path``, or one
    dumped on a different machine where that path no longer exists) —
    resolution failures are swallowed and *extra_changes* is returned
    unchanged.

    Identity-checked against ``source_mtime``/``source_size``: a pre-dumped
    JSON snapshot read back into ``compare snap1.json snap2.json`` records
    the mtime and byte size the binary had at dump time; if the file at
    ``source_path`` has since changed (rebuilt in place, or the path reused
    for something else) the re-probe would assert a fact about a *different*
    binary than the one the snapshots actually describe, making the compare
    non-reproducible. When either doesn't match — or either snapshot
    predates these fields — the fold-in declines rather than trust a
    possibly-stale binary. Not a cryptographic guarantee (a same-size,
    mtime-preserving rebuild — e.g. ``cp -p`` — can still slip through;
    Codex review), but a proportionate check for a best-effort enrichment
    that's already documented to swallow anything short of a clean match.

    The mtime side of that check is skipped independently for each side whose
    own ``source_mtime_epoch`` flag is set: ``dumper._safe_mtime`` recorded
    the fixed ``SOURCE_DATE_EPOCH`` value rather than that binary's real
    mtime at *dump* time (reproducible-builds spec), so a live re-probe's
    real mtime almost never equals it. Each side's flag is checked
    independently (not OR'd together) so a mixed CI/local compare — one
    snapshot dumped under a pinned epoch, the other dumped normally — still
    enforces the real mtime on the non-epoch side rather than letting one
    epoch-dumped side disable the check for both (Codex review, three
    rounds: same-process direct compares, then a dump/compare environment
    mismatch, then this per-side mix). The flag is checked per-snapshot
    rather than via the *compare*-time environment for the same reason as
    round two — a dump-time epoch must stay recognized regardless of what's
    set later. Size still applies unconditionally to both sides — it isn't
    epoch-gated and remains a real (if imperfect) identity signal.
    """
    old_path = getattr(old, "source_path", None)
    new_path = getattr(new, "source_path", None)
    if not old_path or not new_path:
        return extra_changes

    old_snapshot_mtime = getattr(old, "source_mtime", None)
    new_snapshot_mtime = getattr(new, "source_mtime", None)
    old_snapshot_size = getattr(old, "source_size", None)
    new_snapshot_size = getattr(new, "source_size", None)
    if (
        old_snapshot_mtime is None
        or new_snapshot_mtime is None
        or old_snapshot_size is None
        or new_snapshot_size is None
    ):
        return extra_changes
    try:
        old_now_stat = Path(old_path).stat()
        new_now_stat = Path(new_path).stat()
    except OSError:
        return extra_changes
    old_mtime_ok = getattr(old, "source_mtime_epoch", False) or (
        old_now_stat.st_mtime == old_snapshot_mtime
    )
    new_mtime_ok = getattr(new, "source_mtime_epoch", False) or (
        new_now_stat.st_mtime == new_snapshot_mtime
    )
    if (
        not old_mtime_ok
        or not new_mtime_ok
        or old_now_stat.st_size != old_snapshot_size
        or new_now_stat.st_size != new_snapshot_size
    ):
        return extra_changes

    # Both snapshots were identity-checked against the binaries above, so
    # their own ELF tables are the tables a symbols-only re-resolve would
    # read. When NEW keeps every OLD dynamic symbol unchanged there is no
    # removal to recover, and the re-resolve plus full unscoped compare
    # (~19% of a libmkl_rt scan) is skipped.
    if elf_exports_cannot_lose_symbol(old, new):
        return extra_changes

    # The same identity check is what lets each side's symbols-only view be
    # built from its own ELF table rather than by re-reading the binary.
    l0_hard_removals = collect_l0_export_delta_from_snapshots(old, new, lang)
    return [*(extra_changes or []), *l0_hard_removals]

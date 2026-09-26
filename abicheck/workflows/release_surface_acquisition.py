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

"""Acquire a release side's public surface **once**, keyed by its request.

The release fan-out used to hand every matched library the same ``-H``
header set and let each member's own dump acquire the complete product
header surface independently. With 28 Intel MKL libraries that is 28
acquisitions of one identical surface per side -- the parse repeated, the
evidence duplicated onto every member snapshot, and every declaration
another member provides reported missing from this one.

This module is the "once" half. :class:`SurfaceAcquisitionLedger` memoizes
an acquisition on :meth:`~abicheck.model.release_surface.
SurfaceAcquisitionIdentity.key`, so:

* an ordinary directory comparison, where every member shares one CLI/config
  acquisition request, performs exactly **one** acquisition per side;
* members whose request genuinely differs are *not* forced onto one
  snapshot -- they key separately and each gets its own surface, which is
  what keeps reuse a correctness-preserving optimization rather than an
  assumption that "same release" means "same compile context";
* the counts are observable (:meth:`SurfaceAcquisitionLedger.to_dict`),
  which is how the instrumentation test proves the one-per-side property
  instead of asserting it in prose.

The ledger hands out a frozen :class:`~abicheck.model.release_surface.
ReleasePublicSurface` (immutable value type, ``frozenset``/tuple fields
throughout), so a member-specific operation structurally cannot mutate
evidence another member observes -- the failure mode a shared *snapshot*
would invite. The acquired header-only ``AbiSnapshot`` itself is never
handed to member code; only the projection is.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

from ..model import AbiSnapshot
from ..model.declarations import Function, Variable
from ..model.release_surface import (
    PublicObligation,
    ReleasePublicSurface,
    SurfaceAcquisitionIdentity,
    unresolved_surface,
)


def surface_from_snapshot(
    snapshot: AbiSnapshot, *, acquisition_key: str, side: str
) -> ReleasePublicSurface:
    """Project an acquired header snapshot into a release public surface.

    The obligation predicate is **not** re-derived here: it is
    ``buildsource.cross_source_checks``'s own
    ``_has_export_obligation``/``_var_has_export_obligation``, the exact
    pair the single-artifact ``public_not_exported`` check applies, so a
    declaration that owes an export at release level is the same set of
    declarations that owed one per member. Likewise the documented-surface
    index uses ``_candidate_symbols``, which
    ``exported_not_public``'s own ``public_syms`` loop uses.

    An acquisition whose declarations carry no resolvable origin yields an
    *unresolved* surface rather than an empty one: with no public-header
    provenance nothing can be judged public, and reporting zero obligations
    would silently read as "this product promises nothing".
    """
    from ..buildsource.cross_source_checks import (
        _candidate_symbols,
        _has_export_obligation,
        _origin_resolvable,
        _var_has_export_obligation,
    )
    from ..compare.ownership_relations import contract_relations
    from ..model.vocabulary import ScopeOrigin

    if not _origin_resolvable(snapshot):
        return unresolved_surface(
            acquisition_key=acquisition_key,
            side=side,
            reason=(
                "the acquired header surface carries no public-header provenance, "
                "so no declaration can be classified as public"
            ),
        )

    obligations: list[PublicObligation] = []
    declared: set[str] = set()
    # ADR-075 D7: the same contract relation the per-member check reads.
    owned = contract_relations(snapshot)
    for i, fn in enumerate(snapshot.functions):
        if fn.origin == ScopeOrigin.PUBLIC_HEADER:
            declared.update(_candidate_symbols(fn))
        if _has_export_obligation(fn) and not owned.function_owes_no_export(i):
            obligations.append(
                PublicObligation(
                    symbol=fn.mangled or fn.name,
                    name=fn.name,
                    entity="function",
                    source_location=fn.source_location,
                )
            )
    for i, var in enumerate(snapshot.variables):
        if var.origin == ScopeOrigin.PUBLIC_HEADER:
            declared.update(_candidate_symbols(var))
        if _var_has_export_obligation(var) and not owned.variable_owes_no_export(i):
            obligations.append(
                PublicObligation(
                    symbol=var.mangled or var.name,
                    name=var.name,
                    entity="variable",
                    source_location=var.source_location,
                )
            )
    declaring_headers: set[str] = set()
    declarations: list[Function | Variable] = [
        *snapshot.functions,
        *snapshot.variables,
    ]
    for decl in declarations:
        if decl.origin == ScopeOrigin.PUBLIC_HEADER and decl.source_location:
            declaring_headers.add(decl.source_location.rsplit(":", 1)[0])
    type_names = sorted(
        {t.name for t in snapshot.types if t.origin == ScopeOrigin.PUBLIC_HEADER}
        | {e.name for e in snapshot.enums if e.origin == ScopeOrigin.PUBLIC_HEADER}
    )
    return ReleasePublicSurface(
        acquisition_key=acquisition_key,
        side=side,
        obligations=tuple(sorted(obligations, key=lambda o: (o.symbol, o.name))),
        declared_symbols=frozenset(declared),
        header_count=len(declaring_headers),
        type_names=tuple(type_names),
        resolvable=True,
    )


class SurfaceAcquisitionLedger:
    """Memoizes public-surface acquisition per acquisition key, and counts it.

    Thread-safe by construction: the release fan-out compares members in a
    ``ThreadPoolExecutor`` by default, so two members can reach the same
    key concurrently. The lock is held across the producer call so the
    surface is acquired *once* rather than by whichever threads happen to
    miss the memo together -- an acquisition is a castxml/clang parse of a
    whole header tree, so serializing duplicate work is the point, not a
    cost.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._surfaces: dict[str, ReleasePublicSurface] = {}
        self._acquisitions: dict[str, int] = {}
        self._reuses: dict[str, int] = {}

    def acquire(
        self,
        identity: SurfaceAcquisitionIdentity,
        side: str,
        producer: Callable[[], ReleasePublicSurface],
    ) -> ReleasePublicSurface:
        """The surface for *identity*, acquiring it only on a first miss."""
        key = identity.key()
        with self._lock:
            cached = self._surfaces.get(key)
            if cached is not None:
                self._reuses[key] = self._reuses.get(key, 0) + 1
                return cached
            surface = producer()
            self._surfaces[key] = surface
            self._acquisitions[key] = self._acquisitions.get(key, 0) + 1
            return surface

    def get(self, identity: SurfaceAcquisitionIdentity) -> ReleasePublicSurface | None:
        with self._lock:
            return self._surfaces.get(identity.key())

    @property
    def total_acquisitions(self) -> int:
        """How many times a surface was really acquired (producer calls)."""
        with self._lock:
            return sum(self._acquisitions.values())

    @property
    def total_reuses(self) -> int:
        with self._lock:
            return sum(self._reuses.values())

    def acquisitions_by_key(self) -> Mapping[str, int]:
        with self._lock:
            return dict(sorted(self._acquisitions.items()))

    def to_dict(self) -> dict[str, object]:
        """Deterministic instrumentation record for the release report.

        ``acquisitions`` is the number a test asserts against: one per side
        for an ordinary directory comparison, one per genuinely distinct
        acquisition request otherwise.
        """
        with self._lock:
            return {
                "acquisitions": sum(self._acquisitions.values()),
                "reuses": sum(self._reuses.values()),
                "keys": [
                    {
                        "key": key,
                        "acquisitions": count,
                        "reuses": self._reuses.get(key, 0),
                    }
                    for key, count in sorted(self._acquisitions.items())
                ],
            }


def _with_inferred_header_roots(
    header_inputs: Sequence[Path],
    includes: Sequence[Path],
    compile_context: object | None,
) -> tuple[list[Path], object | None]:
    """*includes* and *compile_context* widened by the ``-H`` include roots.

    The same split a member dump applies (``service_dump_native``): plain
    ``-I`` with no build-context include dirs, else a deferred token that
    rides in the compile flags below the build's own include dirs.
    """
    from ..header_utils import resolve_inferred_header_roots

    gcc_options = getattr(compile_context, "gcc_options", None)
    tokens: tuple[str, ...] = tuple(getattr(compile_context, "gcc_option_tokens", ()))
    extra, deferred = resolve_inferred_header_roots(
        [Path(h) for h in header_inputs],
        list(includes),
        gcc_options=gcc_options,
        gcc_option_tokens=tokens,
    )
    if not deferred:
        return [*includes, *extra], compile_context
    from dataclasses import replace

    from ..compile_context import CompileContext

    base = (
        compile_context
        if isinstance(compile_context, CompileContext)
        else CompileContext()
    )
    return list(includes), replace(base, gcc_option_tokens=(*tokens, *deferred))


def acquire_release_surface(
    identity: SurfaceAcquisitionIdentity,
    side: str,
    *,
    ledger: SurfaceAcquisitionLedger,
    headers: list[Path],
    includes: list[Path],
    public_headers: list[Path],
    public_header_dirs: list[Path],
    version: str = "unknown",
    backend: str = "auto",
    compile_context: object | None = None,
    header_inputs: Sequence[Path] = (),
) -> ReleasePublicSurface:
    """Acquire (or reuse) *side*'s public surface for *identity*.

    *header_inputs* are the side's ``-H`` inputs as given (directories not
    yet expanded). They imply include roots exactly as they do for a member
    dump (``header_utils.resolve_inferred_header_roots``): a ``-H`` directory
    is its own include root, so an umbrella header writing
    ``#include <pkg/detail.h>`` relative to it parses without a separate
    ``-I``. Without that the release contract failed to parse whenever a
    member dump needed the same inference, and every export obligation went
    unchecked.

    Parses the headers alone -- no binary is involved, so the surface is a
    property of the product's contract rather than of any one member, which
    is exactly the modelling correction this whole workstream is about
    (``header_only_dump.build_header_only_snapshot`` is the existing
    primitive for that; this function does not add a second one).

    A parse failure is returned as an *unresolved* surface carrying the
    reason, never as an empty one: a failed extractor must narrow the
    conclusions drawn from it, not silently license "the product declares
    nothing, so every export is undocumented".
    """

    def _produce() -> ReleasePublicSurface:
        from ..header_only_dump import build_header_only_snapshot

        if not headers:
            return unresolved_surface(
                acquisition_key=identity.key(),
                side=side,
                reason="no public header inputs were supplied for this release side",
            )
        parse_includes, parse_compile = _with_inferred_header_roots(
            header_inputs, includes, compile_context
        )
        try:
            snapshot = build_header_only_snapshot(
                library_hint=headers[0],
                version=version,
                headers=list(headers),
                extra_includes=parse_includes,
                dump_manifest=None,
                backend=backend,
                compile=parse_compile,  # type: ignore[arg-type]
                lang=identity.lang,
                lang_explicit=identity.lang_explicit,
                public_headers=list(public_headers),
                public_header_dirs=list(public_header_dirs),
                frontend_context=identity.frontend_context,
            )
        except Exception as exc:  # noqa: BLE001 -- an extractor failure is a fact
            return unresolved_surface(
                acquisition_key=identity.key(),
                side=side,
                reason=f"public-header acquisition failed: {exc}",
            )
        from ..provenance import apply_provenance
        from .ownership_request import classify_extracted

        # `build_header_only_snapshot` parses; it does not classify. Origin
        # classification is a separate, caller-owned pass everywhere else in
        # this codebase (`dumper.py`, `cli_resolve.py`, `service_dump_
        # native.py` each call it after their own parse), and without it every
        # declaration stays `ScopeOrigin.UNKNOWN` -- which `surface_from_
        # snapshot` correctly reports as an *unresolved* surface rather than an
        # empty one. The `-I` roots are passed as `include_search_dirs` so the
        # same public-root-ownership rule a member dump applies decides which
        # of them root this product's own public surface (an `-I` that only
        # resolves a dependency's `#include` stays compile context, and its
        # declarations stay UNKNOWN rather than becoming this product's export
        # obligations -- the MKL/MPI case `extract.public_root_ownership`
        # documents).
        apply_provenance(
            snapshot,
            list(public_headers),
            list(public_header_dirs),
            include_search_dirs=list(includes),
        )
        # ADR-075: the same one classification a member dump records, under
        # the run's project rules (`project_ownership_scope`), so the
        # obligations below read the contract relation a member would.
        classify_extracted(
            snapshot, None, [*public_headers, *public_header_dirs], public_header_dirs
        )
        return surface_from_snapshot(
            snapshot, acquisition_key=identity.key(), side=side
        )

    # `for_side` (not a re-acquisition): a reused surface carries the label
    # of whichever side acquired it first, and both sides of an ordinary
    # directory comparison pass the identical header request -- see
    # `ReleasePublicSurface.for_side`.
    return ledger.acquire(identity, side, _produce).for_side(side)


def surface_from_snapshots(
    snapshots: Mapping[str, AbiSnapshot], *, acquisition_key: str, side: str
) -> ReleasePublicSurface:
    """The product contract carried by a set of member snapshots.

    A stored baseline has nothing to acquire: its members were dumped long
    ago and each carries whatever header evidence that dump captured. This
    recovers the one product surface from them by *union* -- every member of
    an ordinary release was dumped against the same header set, so the union
    equals any one member's view, and where members genuinely differ the
    union is the product's contract rather than an arbitrary member's slice.

    Unresolvable when no member carries public-header provenance, which is
    the ordinary case for a binary-depth bundle baseline: such a document
    records no contract, and saying so is the honest answer. It is not the
    same as a product that promises nothing, and the caller must not treat
    it as one.
    """
    return union_surfaces(
        (
            surface_from_snapshot(
                snapshots[name], acquisition_key=acquisition_key, side=side
            )
            for name in sorted(snapshots)
        ),
        acquisition_key=acquisition_key,
        side=side,
    )


def union_surfaces(
    surfaces: Iterable[ReleasePublicSurface], *, acquisition_key: str, side: str
) -> ReleasePublicSurface:
    """One product contract from several members' projected surfaces.

    The union half of :func:`surface_from_snapshots`, exposed separately so
    a driver that must not retain every member's full ``AbiSnapshot`` (the
    stored-OLD/live-NEW path's memory discipline) can project each member as
    it is dumped and keep only the projections. Unresolvable members
    contribute nothing; all of them unresolvable means this side records no
    contract, which is not the same as promising nothing.
    """
    resolved = [surface for surface in surfaces if surface.resolvable]
    if not resolved:
        return unresolved_surface(
            acquisition_key=acquisition_key,
            side=side,
            reason=(
                "no member carries public-header provenance, so this side "
                "records no public contract to reconcile against"
            ),
        )
    obligations = {o.symbol: o for surface in resolved for o in surface.obligations}
    declared: set[str] = set()
    type_names: set[str] = set()
    for surface in resolved:
        declared |= surface.declared_symbols
        type_names |= set(surface.type_names)
    return ReleasePublicSurface(
        acquisition_key=acquisition_key,
        side=side,
        obligations=tuple(obligations[symbol] for symbol in sorted(obligations)),
        declared_symbols=frozenset(declared),
        header_count=max(surface.header_count for surface in resolved),
        type_names=tuple(sorted(type_names)),
        resolvable=True,
    )


def surface_from_bundle_facts(facts: object, *, side: str) -> ReleasePublicSurface:
    """A stored bundle document's public contract.

    Prefers the block the capture *recorded* (``BundleFacts.public_surface``,
    schema 4): that is the surface those members were actually reconciled
    against, and reusing it keeps a stored comparison's answer identical to
    the live run that produced the baseline. Falls back to deriving one from
    the stored member snapshots, so a pre-v4 document -- every baseline
    captured before this existed -- still reconciles instead of silently
    losing its contract.
    """
    recorded = getattr(facts, "public_surface", None)
    if recorded is not None:
        return cast("ReleasePublicSurface", recorded).for_side(side)
    snapshots = getattr(facts, "per_library_snapshots", None) or {}
    return surface_from_snapshots(
        cast("Mapping[str, AbiSnapshot]", snapshots),
        acquisition_key=f"derived:{side}",
        side=side,
    )

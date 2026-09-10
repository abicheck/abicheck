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

"""``compare``'s own evidence-contract floor for a pinned ``--depth build``/``--depth source``.

ADR-068 §3 #28 / plan Prerequisite P3: ``scan --against``'s legacy engine has
always failed loudly (exit 7, ``EVIDENCE_CONTRACT_ERROR``) when an explicit,
pinned ``--depth build``/``--depth source`` requested evidence that neither
side's resolved snapshot actually reached — never silently degrading to
whatever shallower evidence extraction happened to produce.
``workflows.artifact.execute.enforce_requested_depth`` already implements the
identical floor as a hard ``ValidationError`` (exit 64), but only for the one
resolution path that calls it (``resolve_compare_request``, the typed-API/
``run_compare_request`` composition) — the native ``compare`` CLI's own
resolution (``cli_resolve._resolve_compare_snapshots``) does not call it at
all, so ``compare --depth build old.so new.so`` with no ``--build-info``/
``--sources`` on either side silently falls back to symbols-only evidence and
reports ``NO_CHANGE``/exit 0 today — verified live, not from documentation.

:func:`record_depth_evidence_contract_error` closes that gap for every
`compare` caller from one place, instead of teaching each parallel resolution
path its own copy: it recomputes the same floor `enforce_requested_depth`
already checks (``evidence_depth.depth_rank``/``gated_source_label``, the
identical primitives), but — mirroring ``workflows.abi3_audit.
record_abi3_evidence_contract_error``'s existing pattern for the `--abi3`
evidence-contract case — records the failure as ADR-064's *exit-7 axis*
(``DiffResult.evidence_contract_error``) rather than raising. That is a
deliberate, narrower behavior than ``enforce_requested_depth``'s hard
``ValidationError``: this function runs *after* classification has already
produced a `DiffResult` (the same point the `--abi3` axis is recorded), so it
reports the same "the evidence contract this run pinned was not met" signal
through the one axis every `compare` front end already resolves through
`resolve_compare_exit_decision_with_abort_axes` — exit ``7``, not ``64`` —
whichever resolution path produced the pair.

**Superseded (PR #1195): this is now `compare`'s only depth-floor
mechanism.** An earlier revision of this paragraph recorded that
`enforce_requested_depth`'s hard-fail "is unaffected and keeps firing first
(exit 64) on the one path that already calls it". Keeping both meant a
shortfall meant three different things depending on the surface: the native
CLI (which never called it) recorded this axis, while `resolve_compare_
request` raised for *every* rung and for stored snapshots too, pre-empting
the recording below in exactly the cases it was written for. A directory
`compare` routes through the latter and a single-pair one through the
former, so packaging two binaries changed the exit code for the identical
comparison. `service_compare_pipeline.resolve_compare_request` no longer
calls `enforce_requested_depth` — see its own note — so every `compare`
surface now answers a shortfall from here: `build`/`source` only, live
sides only, exit 7, recorded rather than raised. `dump`'s own floors and
`workflows.bundle_stored_pair_compare`'s separate call are untouched; each
is a different command with its own tested contract.

Never fires when *depth* is ``None``/``"binary"``/``"headers"`` — those are
not the two rungs a request can outrun (``EVIDENCE_DEPTH_VALUES``): only a
pinned ``"build"``/``"source"`` has evidence to be missing this way.

**Live extraction only, not a stored snapshot's own embedded evidence**
(discovered while verifying this module against `tests/parity/`'s real
case192 fixture, Codex-style live check rather than a theoretical read):
a side that is *already* a serialized JSON snapshot (`compare old.json
new.json`) was never extracted by *this* run at all, so there is no
"reached a shallower depth than requested" failure to report for it —
the pin only means anything against a live artifact this run itself
resolves. `enforce_requested_depth` does not make this distinction
(`docs/contribute/known-gaps.md`'s own "`--depth` is a floor for live
extraction, not a ceiling for a pre-built snapshot" entry documents the
adjacent, ceiling-side half of the same asymmetry) — this module
deliberately does, via the *is_live* parameter below, rather than
reproducing that pre-existing narrowness newly on the native CLI path,
which had never enforced any floor at all before this module and so had
never surfaced it. A live/live pair is unaffected: this is strictly a
carve-out for a stored-snapshot side, not a general loosening.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..evidence_depth import depth_rank, gated_source_label

if TYPE_CHECKING:
    from ..model import AbiSnapshot

#: The two rungs whose evidence a resolved snapshot can fall short of.
#: Mirrors ``enforce_requested_depth``'s own implicit scope (it fires for
#: every non-``None`` depth, but ``binary``/``headers`` evidence is always
#: satisfied by symbol/header extraction alone -- see that function's
#: docstring). Stated explicitly here since this module's failure mode
#: (report, don't raise) makes silently no-op'ing on a satisfied depth more
#: important to keep obviously correct than relying on ``depth_rank``
#: comparisons to happen to work out.
_GATED_DEPTHS = frozenset({"build", "source"})


def depth_evidence_contract_satisfied(
    depth: str | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_is_live: bool = True,
    new_is_live: bool = True,
) -> bool:
    """Whether *old*/*new* both reach the evidence *depth* pinned, if any.

    ``True`` for every *depth* outside :data:`_GATED_DEPTHS` (nothing to
    fall short of) and for ``None`` (no pin at all -- omitting ``--depth``
    is never a contract). *old_is_live*/*new_is_live* (default ``True``,
    matching ``enforce_requested_depth``'s unconditional check) skip a side
    that is already a serialized snapshot this run never extracted --
    see the module docstring's "Live extraction only" note. Reused, not
    duplicated, by :func:`record_depth_evidence_contract_error` below.
    """
    if depth is None:
        return True
    normalized = depth.lower()
    if normalized not in _GATED_DEPTHS:
        return True
    requested_rank = depth_rank(normalized)
    for snap, is_live in ((old, old_is_live), (new, new_is_live)):
        if not is_live:
            continue
        effective = gated_source_label(getattr(snap, "build_source", None), snap)
        if depth_rank(effective) < requested_rank:
            return False
    return True


def record_depth_evidence_contract_error(
    result: Any,
    depth: str | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_is_live: bool = True,
    new_is_live: bool = True,
) -> None:
    """Record ADR-064's exit-7 axis on *result* when *depth* was pinned but not reached.

    Mirrors ``workflows.abi3_audit.record_abi3_evidence_contract_error``'s
    shape exactly: a plain flag set, orthogonal to policy (the finding set
    ``result.changes`` already carries is unaffected either way -- this is
    "the evidence this run promised itself was insufficient", not a
    detector's own verdict). Additive with any other exit-7 cause already
    recorded on *result* (e.g. a failed ``--abi3`` audit) -- never clears a
    flag another caller already set.
    """
    if not depth_evidence_contract_satisfied(
        depth, old, new, old_is_live=old_is_live, new_is_live=new_is_live
    ):
        result.evidence_contract_error = True


def record_no_baseline_depth_evidence_contract_error(
    result: Any,
    depth: str | None,
    candidate: AbiSnapshot,
    *,
    is_live: bool = True,
) -> None:
    """The one-sided (``compare --no-baseline``) spelling of the same floor.

    ADR-068 D2's single-build audit has exactly one operand, so "both sides
    reach the pinned depth" degenerates to "the candidate does". Expressed as
    its own named entry point rather than leaving each caller to spell the
    degenerate case itself (``old=candidate, new=candidate,
    old_is_live=False``) -- that call reads as a bug at every call site,
    and a reader would have to re-derive that passing the same snapshot
    twice is harmless here precisely *because* one side is excluded.

    Identical semantics to :func:`record_depth_evidence_contract_error` in
    every other respect: only a pinned ``build``/``source`` can be outrun,
    a stored-snapshot candidate this run never extracted is exempt
    (*is_live*), and the failure is recorded as ADR-064's exit-7 axis
    (``DiffResult.evidence_contract_error``) rather than raised -- so
    ``compare --no-baseline NEW --depth build`` with no ``--sources``/
    ``--build-info`` fails loudly with the same exit ``7`` the two-sided
    ``compare OLD NEW --depth build`` already does, instead of silently
    degrading to symbols-only evidence and reporting a clean audit.
    """
    record_depth_evidence_contract_error(
        result,
        depth,
        candidate,
        candidate,
        old_is_live=False,
        new_is_live=is_live,
    )

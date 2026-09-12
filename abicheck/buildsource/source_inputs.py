# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The **expected-input-set** model for every filesystem-reading pre-scan, and
the **read licence** that decides whether the filesystem may be consulted at
all.

Two defects motivated this module, both P1, both in the source-derived
enrichment of *stored* snapshots (``workflows/pattern_preprocessor_scan.py``):

1. **Historical facts were re-derived from today's filesystem.** A stored OLD
   snapshot records a ``source_header`` per declaration. The pre-scan rebuilt
   its roots from those recorded paths and then ``read_text()``-ed them, so a
   baseline dumped on another machine (or from a tree the working copy has
   since edited, deleted, or restructured) was re-characterised against
   whatever happens to live at the same path on the current runner. A path in a
   snapshot is *provenance* — a record of where a declaration came from — not a
   licence to re-read the current filesystem for a historical fact.

   The contract this module makes executable:

       **Comparing stored snapshots uses stored facts. A path recorded in a
       snapshot is provenance, not a licence to re-read the current
       filesystem for historical facts.**

   :class:`SourceReadLicence` is that licence. A side gets one only from live
   extraction in this run (the caller pointed at the binary/headers that are on
   disk right now) or from an explicitly supplied, provenance-verified context.
   Otherwise every recorded path resolves to
   :attr:`SourceInputDisposition.NOT_LICENSED` **without being stat'd**, and the
   result states that the historical evaluation was not possible — it never
   falls back to a same-looking path on the current runner.

2. **Discovery-derived completeness read a missing input as full coverage.**
   The old signal was ``files_scanned > 0 and files_skipped == 0`` over a walk
   that silently ``continue``d past a root that did not exist. A snapshot whose
   headers had all been relocated therefore produced *zero* inputs, *zero*
   skips, and — as soon as one surviving file was scanned — "fully covered",
   which let the evolution fold report ``introduced`` for a construct that was
   never established as absent in OLD.

   :class:`SourceInputSet` replaces that with an *expected*-input set: every
   root the caller declared is accounted for by exactly one disposition, and
   sufficiency is a statement about the whole expected set rather than about
   whatever the walk happened to find. A root that did not exist is
   :attr:`SourceInputDisposition.MISSING`, which can never be sufficient.

Both are general: this is the model *any* filesystem-reading pre-scan should
resolve its inputs through, not a patch on one caller.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypeVar

_T = TypeVar("_T")


class SourceInputDisposition(str, Enum):
    """What became of one declared input.

    Every root a caller declares resolves to exactly one of these, so the set
    is a complete account rather than a filtered survivor list.
    """

    #: Selected for scanning; the scan has not run yet. Never appears in a
    #: finished result — a finished input is ``SCANNED`` or ``UNREADABLE``.
    SELECTED = "selected"
    #: Read successfully and scanned.
    SCANNED = "scanned"
    #: Declared, but nothing exists at that path now. **This is the state the
    #: old discovery-derived signal silently dropped.**
    MISSING = "missing"
    #: Exists, but could not be read (permissions, I/O error, decode failure).
    UNREADABLE = "unreadable"
    #: Present in some form, but not a thing this scanner can read: a FIFO, a
    #: socket, a device node, or a symlink whose target is gone. Distinct from
    #: ``MISSING``, where nothing is at the path at all.
    UNSUPPORTED = "unsupported"
    #: Deliberately out of scope — the caller's ``changed_paths`` filter
    #: excluded it. Not a coverage gap: the caller asked for this.
    EXCLUDED = "excluded"
    #: Recorded in a snapshot, but re-reading it is not licensed (see
    #: :class:`SourceReadLicence`). Never stat'd, never opened.
    NOT_LICENSED = "not_licensed"


#: Dispositions that are *gaps*: the input was expected to contribute evidence
#: and did not. A set holding any of these can never be sufficient, so an
#: absence claim over it is never established.
GAP_DISPOSITIONS: frozenset[SourceInputDisposition] = frozenset(
    {
        SourceInputDisposition.SELECTED,
        SourceInputDisposition.MISSING,
        SourceInputDisposition.UNREADABLE,
        SourceInputDisposition.UNSUPPORTED,
        SourceInputDisposition.NOT_LICENSED,
    }
)


@dataclass(frozen=True)
class SourceReadLicence:
    """Whether a side's recorded source paths may be read from the filesystem.

    Deny-by-default on purpose: :meth:`withheld` is what an unqualified caller
    gets, so a new code path that forgets to establish provenance degrades to
    an honest "not evaluated" rather than silently re-characterising a stored
    snapshot against the current workspace.
    """

    permitted: bool
    #: Stable, machine-readable origin: ``live_extraction``,
    #: ``verified_context``, or ``stored_snapshot``.
    origin: str
    #: Human-readable justification, surfaced in coverage detail.
    reason: str

    @classmethod
    def live_extraction(cls) -> SourceReadLicence:
        """The caller pointed at inputs that exist on disk *now*, in this run."""
        return cls(
            permitted=True,
            origin="live_extraction",
            reason="snapshot was extracted from live inputs in this run",
        )

    @classmethod
    def verified_context(cls, reason: str) -> SourceReadLicence:
        """An explicitly supplied, provenance-verified source context."""
        return cls(permitted=True, origin="verified_context", reason=reason)

    @classmethod
    def withheld(cls, reason: str) -> SourceReadLicence:
        """No licence: the paths are provenance only (the default)."""
        return cls(permitted=False, origin="stored_snapshot", reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "permitted": self.permitted,
            "origin": self.origin,
            "reason": self.reason,
        }


#: The licence a caller that establishes nothing gets.
WITHHELD_FOR_STORED_SNAPSHOT = SourceReadLicence.withheld(
    "stored snapshot: recorded source paths are provenance, not a licence to "
    "read the current filesystem for historical facts"
)


def build_evidence_collected_live(*, precaptured: bool, collected_inline: bool) -> bool:
    """Were a pack's L3-L5 facts collected from inputs that exist on disk *now*?

    The rule, stated once so both the embed step that grants the flag and the
    tests that pin it read the same sentence: **only an inline collection
    performed in this run is live, and any pre-captured contribution makes the
    whole merged pack historical.**

    Fail-closed on the second half deliberately. A merge of a pre-captured
    ``--build-info`` pack with an inline ``--sources`` collection produces one
    pack whose compile units come from both, and which layer each fact came
    from is a per-layer precedence decision (``_combine_packs``) that the
    consumer of the licence -- a lexical scan over recorded paths -- has no way
    to re-derive per path. Treating the merge as historical loses assurance;
    treating it as live would let a pack captured on another machine be
    re-read from this one, which is the fabrication the licence exists to
    prevent.
    """
    return collected_inline and not precaptured


class LiveSourceEvidence(Protocol):
    """A snapshot-shaped object, as far as the licence is concerned.

    Structural on purpose: this module is a dependency-free leaf, and a real
    ``AbiSnapshot`` import would point it at ``model`` for three attributes.
    """

    live_source_evidence: bool
    from_headers: bool
    from_headers_inferred: bool


def extraction_read_source_inputs(snapshot: Any) -> bool:
    """Did the extraction that produced *snapshot* actually **read** the source
    paths the snapshot records?

    "Extracted in this run" is not sufficient on its own, and assuming it was
    is a hole worth naming: a headerless DWARF dump derives every declaration's
    ``source_header`` from ``DW_AT_decl_file``, which names a file on the
    *build* machine that this run never opened and that may not exist here at
    all. A live extraction of a downloaded binary would then have licensed a
    re-read of whatever happens to occupy those paths now -- the same
    fabrication the licence exists to prevent, reached through the binary's
    debug info instead of through a stored snapshot (Codex review, P2).

    So the licence requires *header-derived* provenance: the header-AST
    frontends (castxml / ``clang -ast-dump``) genuinely opened the files they
    attribute declarations to. ``from_headers_inferred`` excludes a legacy
    snapshot where ``from_headers`` was guessed rather than established, on the
    same "a guess is not evidence" reasoning
    ``diff_symbols``/``diff_platform_elf_symbols`` already apply to it.

    Scoped to the *declared-header* evidence source on purpose. A dump that
    also collected L3 build evidence (``--sources``) did read its compile
    units, and that is a separate provenance question with a separate answer:
    ``BuildSourcePack.live_source_evidence``, stamped by
    :func:`build_evidence_collected_live` at the one place that can tell an
    inline collection from a loaded pack. ``workflows/
    pattern_preprocessor_scan.py`` resolves one licence per source rather than
    one per side, so neither answer can license reads the other established
    (this replaced an earlier, deliberately conservative note here that
    withheld the build-evidence reads outright -- Codex review, P2).
    """
    return bool(
        getattr(snapshot, "from_headers", False)
        and not getattr(snapshot, "from_headers_inferred", False)
    )


def granting_live_source_licence(
    extract: Callable[..., _T], *, snapshot_attr: str | None = None
) -> Callable[..., _T]:
    """Wrap a live-extraction function so its result carries the licence.

    Apply this to the *one* operation that builds a snapshot by reading inputs
    from the filesystem right now -- for the snapshot pipeline that is
    ``service.run_dump``, which the ``dump`` CLI, ``compare``'s implicit operand
    dump, ``scan``'s candidate resolution, the cached-dump wrapper's cache-miss
    path, and the typed Python API all funnel through.

    Granting it at that shared operation rather than at one front end's wrapper
    is the point: an earlier revision stamped it in the cached-dump wrapper
    instead, which left the documented ``abicheck.service.run_dump`` API
    unstamped, so identical inputs produced "not evaluated" source-derived
    facts through the Python API while working through the CLI -- the
    front-end parity break AGENTS.md forbids (Codex review).

    The grant is **conditional**: see :func:`extraction_read_source_inputs` for
    why "extracted in this run" is not by itself evidence that the recorded
    source paths were read. Re-applying is idempotent, which matters because
    the dump pipeline recurses back through its own entry point for the hybrid
    AST frontend.

    *snapshot_attr* is for an operation whose result *holds* the snapshot rather
    than being one (``DumpResult.snapshot``). It exists so that a pipeline with
    several execution branches is stamped at the one function they all return
    through, instead of once per branch: a per-branch grant is what let the
    typed API's binary-less header-only and source-only dispatches come back
    unlicensed after the ordinary and ABICC paths had each been fixed
    individually (Codex review, P2). The rule stays in one place either way.
    """

    @functools.wraps(extract)
    def _stamped(*args: Any, **kwargs: Any) -> _T:
        result = extract(*args, **kwargs)
        target = (
            result if snapshot_attr is None else getattr(result, snapshot_attr, None)
        )
        # Conditional, not unconditional: "this run produced it" does not mean
        # "this run read the files it names" (see
        # :func:`extraction_read_source_inputs`). Duck-typed rather than
        # isinstance-checked, per LiveSourceEvidence.
        if extraction_read_source_inputs(target):
            target.live_source_evidence = True  # type: ignore[attr-defined,union-attr]
        return result

    return _stamped


@dataclass(frozen=True)
class SourceInput:
    """One declared input and what became of it."""

    path: str
    disposition: SourceInputDisposition

    def resolved(self, disposition: SourceInputDisposition) -> SourceInput:
        return SourceInput(path=self.path, disposition=disposition)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "disposition": self.disposition.value}


#: Precedence when two evidence sources report *different* dispositions for the
#: same path (see :meth:`SourceInputSet.merged`). Highest first, and a total
#: order on purpose: a first-wins merge makes a side's sufficiency depend on
#: which evidence source happened to resolve first, which is the order-
#: dependence this repository's own primitive-level property tests exist to
#: catch (found by exactly such a test here).
#:
#: ``SCANNED`` wins outright -- the file genuinely was read, so no coverage is
#: missing for it whatever the other source made of the path. Every *gap* then
#: outranks ``EXCLUDED``, fail-closed: one source deliberately putting a path
#: out of scope must not license ignoring another source's real gap on it. The
#: three filesystem-observed gaps outrank ``SELECTED``/``NOT_LICENSED``, which
#: assert only that nothing was looked at; sufficiency is identical across all
#: five, so this half of the order decides the *label* a reader is given, and
#: prefers the one carrying an observation.
DISPOSITION_PRECEDENCE: tuple[SourceInputDisposition, ...] = (
    SourceInputDisposition.SCANNED,
    SourceInputDisposition.UNREADABLE,
    SourceInputDisposition.MISSING,
    SourceInputDisposition.UNSUPPORTED,
    SourceInputDisposition.SELECTED,
    SourceInputDisposition.NOT_LICENSED,
    SourceInputDisposition.EXCLUDED,
)


@dataclass(frozen=True)
class SourceInputSet:
    """The complete account of one scan's expected inputs.

    ``sufficient`` is the replacement for ``files_scanned > 0 and
    files_skipped == 0``: it is true only when the licence permitted reading,
    at least one input was actually scanned, and **no** input is in a gap
    state. In particular a declared root that did not exist keeps the set
    insufficient, which is the specific hole the old signal had.
    """

    inputs: tuple[SourceInput, ...] = ()
    licence: SourceReadLicence = WITHHELD_FOR_STORED_SNAPSHOT

    def with_inputs(self, inputs: Iterable[SourceInput]) -> SourceInputSet:
        return SourceInputSet(inputs=tuple(inputs), licence=self.licence)

    def selected(self) -> tuple[SourceInput, ...]:
        return tuple(
            i for i in self.inputs if i.disposition is SourceInputDisposition.SELECTED
        )

    def counts(self) -> dict[str, int]:
        counts = {d.value: 0 for d in SourceInputDisposition}
        for item in self.inputs:
            counts[item.disposition.value] += 1
        return counts

    @property
    def scanned(self) -> int:
        return sum(
            1 for i in self.inputs if i.disposition is SourceInputDisposition.SCANNED
        )

    def gaps(self) -> tuple[SourceInput, ...]:
        return tuple(i for i in self.inputs if i.disposition in GAP_DISPOSITIONS)

    @property
    def sufficient(self) -> bool:
        """True only when an absence claim over these inputs is established."""
        return self.licence.permitted and self.scanned > 0 and not self.gaps()

    def insufficiency_reason(self) -> str:
        """Why :attr:`sufficient` is false, or ``""`` when it is true."""
        if not self.licence.permitted:
            return self.licence.reason
        gaps = self.gaps()
        if gaps:
            counts = self.counts()
            parts = [
                f"{counts[d.value]} {d.value}"
                for d in SourceInputDisposition
                if d in GAP_DISPOSITIONS and counts[d.value]
            ]
            return "incomplete expected-input set: " + ", ".join(parts)
        if self.scanned == 0:
            return "no source inputs were declared for this side"
        return ""

    def with_read_outcomes(self, outcomes: Mapping[str, bool]) -> SourceInputSet:
        """Fold per-path read outcomes back in, keeping the account complete.

        A ``SELECTED`` input becomes ``SCANNED`` or ``UNREADABLE``; every other
        disposition is preserved verbatim. An outcome for a path this set does
        not hold is still recorded when it failed, so a surplus failure is
        never dropped on the floor.
        """
        resolved = [
            item.resolved(
                SourceInputDisposition.SCANNED
                if outcomes.get(item.path)
                else SourceInputDisposition.UNREADABLE
            )
            if item.disposition is SourceInputDisposition.SELECTED
            else item
            for item in self.inputs
        ]
        known = {i.path for i in self.inputs}
        resolved.extend(
            SourceInput(path=path, disposition=SourceInputDisposition.UNREADABLE)
            for path, ok in sorted(outcomes.items())
            if path not in known and not ok
        )
        return self.with_inputs(resolved)

    def merged(self, other: SourceInputSet) -> SourceInputSet:
        """Combine two accounts resolved under *different* licences.

        One scan can draw on two evidence sources with independent provenance
        (a snapshot's declared headers and an embedded build pack's compile
        units), so it needs one account spanning both. Inputs concatenate,
        de-duplicated by path under :data:`DISPOSITION_PRECEDENCE` -- a path
        both sources declare must not be counted twice, and the outcome must
        not depend on which source was resolved first.

        The set-level ``licence`` is the permitted one when exactly one side
        is permitted, which is what keeps mixed provenance honest rather than
        collapsing it either way: the unlicensed side's roots stay in the
        account as ``NOT_LICENSED`` gaps, so :attr:`sufficient` is false and
        :meth:`insufficiency_reason` names them -- while the licensed side's
        observations are still reported. Neither side permitted keeps this
        set's own withheld licence and its reason.
        """
        rank = {d: i for i, d in enumerate(DISPOSITION_PRECEDENCE)}
        by_path: dict[str, SourceInput] = {}
        for item in (*self.inputs, *other.inputs):
            held = by_path.get(item.path)
            if held is None or rank[item.disposition] < rank[held.disposition]:
                by_path[item.path] = item
        licence = self.licence
        if not licence.permitted and other.licence.permitted:
            licence = other.licence
        return SourceInputSet(
            inputs=tuple(by_path[p] for p in sorted(by_path)), licence=licence
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "licence": self.licence.to_dict(),
            "sufficient": self.sufficient,
            "insufficiency_reason": self.insufficiency_reason(),
            "counts": self.counts(),
            "gaps": [i.to_dict() for i in self.gaps()],
        }


#: Directory names never descended into during a directory-root walk.
PRUNED_DIR_SEGMENTS: frozenset[str] = frozenset({".git", ".hg", ".svn"})


def resolve_source_inputs(
    roots: Iterable[str | Path],
    changed_paths: Iterable[str] | None = None,
    *,
    licence: SourceReadLicence = WITHHELD_FOR_STORED_SNAPSHOT,
    classify_candidate: Any = None,
    path_changed: Any = None,
    pruned_dirs: frozenset[str] = PRUNED_DIR_SEGMENTS,
) -> SourceInputSet:
    """Account for every declared *root* and return the expected-input set.

    Unlike a discovery walk, **nothing is silently dropped**: a root that does
    not exist becomes :attr:`SourceInputDisposition.MISSING`, a root that is
    neither file nor directory becomes ``UNSUPPORTED``, and a candidate the
    caller's ``changed_paths`` filter rejects becomes ``EXCLUDED``. When
    ``licence`` does not permit reading, every root becomes ``NOT_LICENSED``
    and the filesystem is not touched at all.

    ``classify_candidate``/``path_changed``/``pruned_dirs`` are the caller's own
    walk policy, so this module stays free of any one scanner's file-type
    policy. ``classify_candidate`` is deliberately **tri-state**: it returns
    :attr:`SourceInputDisposition.SELECTED` for a real candidate, ``None`` for a
    file that was never expected evidence (a ``.md``, a binary blob), and any
    gap disposition -- in practice ``UNREADABLE`` -- for a candidate it could
    not examine. A two-state predicate cannot tell the last two apart, and
    collapsing them is how an unreadable header disappears from the account
    while the set still reports full coverage.
    """
    root_list = [str(r) for r in roots]
    if not licence.permitted:
        return SourceInputSet(
            inputs=tuple(
                SourceInput(path=r, disposition=SourceInputDisposition.NOT_LICENSED)
                for r in sorted(set(root_list))
            ),
            licence=licence,
        )

    changed: set[str] | None = None
    if changed_paths is not None:
        changed = {str(p).replace("\\", "/") for p in changed_paths}

    def _keep(candidate: Path) -> bool:
        if changed is None:
            return True
        if path_changed is None:
            return True
        return bool(path_changed(candidate, changed))

    resolved: dict[str, SourceInputDisposition] = {}

    def _record(path: Path | str, disposition: SourceInputDisposition) -> None:
        key = str(path)
        # A path reachable both as an explicit root and through a directory
        # walk keeps the strongest claim: a gap never downgrades to EXCLUDED,
        # and a real selection never downgrades to a gap.
        prior = resolved.get(key)
        if prior is None or _rank(disposition) > _rank(prior):
            resolved[key] = disposition

    for root in root_list:
        rp = Path(root)
        try:
            is_file = rp.is_file()
            is_dir = rp.is_dir()
            exists = rp.exists()
        except OSError:
            _record(rp, SourceInputDisposition.UNREADABLE)
            continue
        if is_file:
            # An explicit file root is honored regardless of suffix: the
            # caller pointed at it directly.
            _record(
                rp,
                SourceInputDisposition.SELECTED
                if _keep(rp)
                else SourceInputDisposition.EXCLUDED,
            )
            continue
        if is_dir:
            # `os.walk` swallows traversal errors by default (`onerror=None`):
            # an unreadable directory yields *no entries* and no exception, so
            # without this callback the root would silently vanish from the
            # account and another readable root could then make the set
            # "sufficient" -- the exact failure this model exists to prevent
            # (Codex review, P2). A directory we could not enumerate is a gap,
            # recorded under the path `os.walk` itself reports as failing, which
            # may be a subdirectory rather than the root we started from.
            def _walk_error(error: OSError) -> None:
                failed = getattr(error, "filename", None) or str(rp)
                _record(failed, SourceInputDisposition.UNREADABLE)

            for dirpath, dirnames, filenames in os.walk(rp, onerror=_walk_error):
                dirnames[:] = [d for d in dirnames if d not in pruned_dirs]
                base = Path(dirpath)
                for name in filenames:
                    cand = base / name
                    try:
                        regular = cand.is_file()
                    except OSError:
                        # `is_file()` stats, and a stat can fail on its own (an
                        # ACL, a dead mount, a name the filesystem rejects). A
                        # candidate we cannot even classify is a gap, never a
                        # crash: this walk is advisory and must not abort, but it
                        # must also not quietly shrink its own expected set.
                        _record(cand, SourceInputDisposition.UNREADABLE)
                        continue
                    if not regular:
                        # FIFOs/sockets/devices/broken symlinks are listed by
                        # os.walk; opening one would block or fail. They were
                        # never expected evidence, so they are not gaps.
                        continue
                    try:
                        verdict: SourceInputDisposition | None = (
                            SourceInputDisposition.SELECTED
                            if classify_candidate is None
                            else classify_candidate(cand)
                        )
                    except OSError:
                        _record(cand, SourceInputDisposition.UNREADABLE)
                        continue
                    if verdict is None:
                        # Never expected evidence: not a gap, not recorded.
                        continue
                    if verdict is not SourceInputDisposition.SELECTED:
                        # A candidate the classifier could not examine (an
                        # unreadable extensionless header). Recorded as the gap
                        # it is, rather than filtered out as uninteresting.
                        _record(cand, verdict)
                        continue
                    _record(
                        cand,
                        SourceInputDisposition.SELECTED
                        if _keep(cand)
                        else SourceInputDisposition.EXCLUDED,
                    )
            continue
        try:
            dangling = rp.is_symlink()
        except OSError:
            dangling = False
        # A dangling symlink is `UNSUPPORTED`, not `MISSING`, and the difference
        # is what a reader does next: `MISSING` sends them looking for a deleted
        # file, when `ls` will plainly show the link still sitting there. Either
        # way it is a gap, so sufficiency is unaffected -- this is about the
        # label being true (Codex/CodeRabbit review).
        _record(
            rp,
            SourceInputDisposition.UNSUPPORTED
            if exists or dangling
            else SourceInputDisposition.MISSING,
        )

    return SourceInputSet(
        inputs=tuple(
            SourceInput(path=p, disposition=d) for p, d in sorted(resolved.items())
        ),
        licence=licence,
    )


#: Precedence used when one path is reachable twice (explicit root + walk).
_DISPOSITION_RANK: dict[SourceInputDisposition, int] = {
    SourceInputDisposition.EXCLUDED: 0,
    SourceInputDisposition.SELECTED: 1,
    SourceInputDisposition.SCANNED: 1,
    SourceInputDisposition.UNSUPPORTED: 2,
    SourceInputDisposition.MISSING: 3,
    SourceInputDisposition.UNREADABLE: 3,
    SourceInputDisposition.NOT_LICENSED: 4,
}


def _rank(disposition: SourceInputDisposition) -> int:
    return _DISPOSITION_RANK[disposition]

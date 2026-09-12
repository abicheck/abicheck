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
    #: Exists, but is not a thing this scanner can read (a FIFO, a socket, a
    #: device node, a dangling symlink target type).
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


class LiveSourceEvidence(Protocol):
    """Anything carrying the runtime-only source-read licence flag.

    Structural on purpose: this module is a dependency-free leaf, and a real
    ``AbiSnapshot`` import would point it at ``model`` for one attribute.
    """

    live_source_evidence: bool


def granting_live_source_licence(
    extract: Callable[..., _T],
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

    Re-applying is idempotent, which matters because the dump pipeline recurses
    back through its own entry point for the hybrid AST frontend.
    """

    @functools.wraps(extract)
    def _stamped(*args: Any, **kwargs: Any) -> _T:
        result = extract(*args, **kwargs)
        # Duck-typed rather than isinstance-checked: see LiveSourceEvidence.
        result.live_source_evidence = True  # type: ignore[attr-defined]
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
    is_scannable: Any = None,
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

    ``is_scannable``/``path_changed``/``pruned_dirs`` are the caller's own walk
    policy (the pattern scanner supplies its suffix allowlist, its tail-match
    join, and its build-output pruning), so this module stays free of any one
    scanner's file-type policy.
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
                    if not cand.is_file():
                        # FIFOs/sockets/devices/broken symlinks are listed by
                        # os.walk; opening one would block or fail. They were
                        # never expected evidence, so they are not gaps.
                        continue
                    if is_scannable is not None and not is_scannable(cand):
                        continue
                    _record(
                        cand,
                        SourceInputDisposition.SELECTED
                        if _keep(cand)
                        else SourceInputDisposition.EXCLUDED,
                    )
            continue
        _record(
            rp,
            SourceInputDisposition.UNSUPPORTED
            if exists
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

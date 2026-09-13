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

"""Per-bundle-member *baseline header staging* — the missing old-side
evidence path behind :data:`~abicheck.buildsource.project_targets.
BUNDLE_CHECK_DEPTHS`.

**The problem this closes.** A ``kind: bundle`` check compares two
directories: the resolved baseline ``binaries/`` directory against the
candidate bundle directory (``actions/check-target/action.yml``'s
``old-library``). That old-side operand is a directory of *raw ELF
binaries* — it carries no historical header evidence at all — while
``check-project.yml`` has a single project-wide ``header:`` input pointing
at the *current* checkout. So at ``depth: headers`` both sides would be
header-parsed from the same current sources, and a header-only change (an
inline function or a template removed, a default argument changed) would be
silently invisible: only one version of the headers is ever parsed. That is
why ``headers`` is rejected for a bundle check today, and why the rejection
is a false-clean guard rather than mere lost evidence.

**What is genuinely already available.** ``actions/baseline/run.sh`` dumps
*one snapshot per library* at baseline time, from the baseline checkout,
using that library's own declared ``header:``/``include:`` inputs — for
bundle members too (a bundle-scoped baseline stages the member's binary
*in addition to*, never instead of, its snapshot). Those snapshots are
therefore real, historical, per-member header evidence, recorded on the
old side at the right commit. They are simply unreachable from the bundle
compare path, for one mechanical reason: a baseline-set directory mixes
``manifest.json``, the per-library snapshots, and ``binaries/``, and
``compare``'s directory operand walks a tree recursively — so a member
would be discovered twice (once as ``libfoo.abicheck.json``, once as
``binaries/libfoo``) and collide on one match key.

This module is that missing link, in two steps that stay separable:

* :func:`resolve_member_snapshots` — read-only. Resolves each selected
  member's ``artifacts[].snapshot`` under the baseline-set directory with
  the *same* containment and digest guarantees
  :func:`~abicheck.buildsource.baseline_set.resolve_bundle` already
  applies to the staged binary, and records per-member *problems* rather
  than collapsing "not in the manifest", "escaping path" and "digest
  mismatch" into one state.
* :func:`stage_member_snapshots` — the staging. Materializes a *clean*
  directory holding exactly one snapshot per member, suitable as a
  directory ``compare`` old-side operand, and reports for each staged
  member whether its snapshot actually carries header-derived evidence
  (:attr:`~abicheck.model.snapshot.AbiSnapshot.from_headers`) — the one
  honest fact that decides whether a header-depth bundle compare would
  have two genuinely different header sources or one.

**Deliberately does not lift the restriction.** Staging the old side is
necessary but not sufficient: ``actions/check-target`` still has to route
the bundle compare's old operand at this directory (and keep real ELF
binaries reachable for the cross-library bundle graph, which
``abicheck/bundle.py``'s ``build_bundle_snapshot()`` cannot build from
snapshots), and the new side still has no per-member header selection.
:data:`~abicheck.buildsource.project_targets.BUNDLE_CHECK_DEPTHS` stays as
it is until those land — see ``docs/contribute/known-gaps.md``. Lifting a
false-clean guard before its backing evidence path is fully wired would
re-create exactly the failure the guard exists for.

Lives here, next to :mod:`~abicheck.buildsource.baseline_set` whose
manifest model and path guards it builds on, rather than inside it:
``architecture/debt.yaml`` pins that module ``no_growth``, and ADR-061's
answer to a pinned module is to give the new responsibility its own owned
module, never to grow the pinned one.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .baseline_set import (
    BaselineManifest,
    _resolve_under_baseline_dir,
    _snapshot_digest_issue,
)

#: Directory name :func:`stage_member_snapshots` materializes its clean,
#: one-snapshot-per-member tree into (relative to the caller's staging root).
#: A dedicated directory, never the baseline-set root itself: the root also
#: holds ``manifest.json`` and ``binaries/``, and ``compare``'s directory
#: operand walks recursively (see this module's docstring).
MEMBER_SNAPSHOTS_DIRNAME = "member-snapshots"

#: Snapshot filename suffixes ``actions/baseline/run.sh`` can produce
#: (``snapshot-compression``: none/gzip/zstd). A staged file keeps its own
#: suffix — the reader sniffs magic bytes, but every human-facing path and
#: the writer treat the suffix as authoritative, so stripping it would make
#: the staged tree lie about its own encoding.
_SNAPSHOT_SUFFIXES = (".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst")


class MemberStagingError(ValueError):
    """A staging input that cannot be honored safely (unsafe member name,
    or two members colliding on one staged filename)."""


@dataclass(frozen=True)
class MemberSnapshot:
    """One member's resolved baseline snapshot (pre-staging)."""

    member: str
    path: Path


@dataclass(frozen=True)
class MemberSnapshotResolution:
    """What :func:`resolve_member_snapshots` found, per member.

    ``problems`` keeps each rejected member's *reason*, so a caller can
    distinguish a member absent from the manifest (a ``bundle.targets``
    typo, or a target never staged into this baseline-set) from a corrupt
    one — the same per-cause discipline
    :func:`~abicheck.buildsource.baseline_set.resolve_bundle` applies to
    binaries.
    """

    snapshots: tuple[MemberSnapshot, ...] = ()
    problems: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """Every requested member resolved to a usable snapshot."""
        return bool(self.snapshots) and not self.problems


@dataclass(frozen=True)
class StagedMemberSnapshot:
    """One member's staged snapshot plus the one fact that matters about it."""

    member: str
    path: Path
    #: ``AbiSnapshot.from_headers`` as recorded *by the baseline dump* — the
    #: honest question "was this member's old side parsed from public
    #: headers?". ``False`` for an ELF/DWARF-only baseline dump, and
    #: ``False`` (never an exception) for a snapshot this build cannot load.
    from_headers: bool = False


@dataclass(frozen=True)
class StagedBundleBaseline:
    """The staged old-side operand and its header-evidence assessment."""

    snapshots_dir: Path
    staged: tuple[StagedMemberSnapshot, ...] = ()
    #: Members with no usable baseline snapshot at all, with the reason.
    problems: dict[str, str] = field(default_factory=dict)

    @property
    def members_with_header_evidence(self) -> tuple[str, ...]:
        return tuple(s.member for s in self.staged if s.from_headers)

    @property
    def members_without_header_evidence(self) -> tuple[str, ...]:
        return tuple(s.member for s in self.staged if not s.from_headers)

    @property
    def header_evidence_complete(self) -> bool:
        """True only when *every* requested member staged a snapshot that
        genuinely carries header-derived evidence.

        Deliberately a conjunction over all three failure modes — an
        unresolved member, an empty selection, and a staged-but-ELF-only
        snapshot are each enough to make a header-depth bundle compare fall
        back to one side's headers for at least one member, which is the
        false-clean this whole module exists to prevent. "Some members have
        historical headers" is not a safe basis for a run-wide depth
        decision.
        """
        return (
            bool(self.staged)
            and not self.problems
            and not self.members_without_header_evidence
        )

    @property
    def header_evidence_state(self) -> str:
        """``complete`` / ``partial`` / ``none`` — a stable token for a
        workflow output (``actions/resolve-baseline``)."""
        if self.header_evidence_complete:
            return "complete"
        if self.members_with_header_evidence:
            return "partial"
        return "none"


def _unsafe_member_reason(member: str) -> str | None:
    """Why *member* may not be used as a staged filename stem, or ``None``.

    Mirrors ``actions/baseline/run.sh``'s own library-name validation: the
    staged path is built directly from this string, and ``pathlib``'s ``/``
    silently discards the left operand for an absolute right-hand side, so a
    separator/traversal/absolute/control-character name must be rejected
    before any file is written — never sanitized into a different name,
    which would silently stage one member's snapshot as another's.
    """
    if not member:
        return "empty member name"
    if Path(member).is_absolute():
        return f"member name {member!r} is an absolute path"
    if "/" in member or "\\" in member:
        return f"member name {member!r} contains a path separator"
    if member in (".", ".."):
        return f"member name {member!r} is a traversal segment"
    if any(ord(c) < 0x20 or c == "\x7f" for c in member):
        return f"member name {member!r} contains a control character"
    return None


def resolve_member_snapshots(
    baseline_dir: Path | str,
    manifest: BaselineManifest,
    members: list[str] | tuple[str, ...],
) -> MemberSnapshotResolution:
    """Resolve every *member*'s baseline snapshot under *baseline_dir*.

    Read-only: nothing is copied or written. Applies, in order, the same
    checks :func:`~abicheck.buildsource.baseline_set.resolve_bundle` applies
    to a staged binary — duplicate ``artifacts[]`` rows first (so a
    row-order-dependent misdiagnosis is impossible), then manifest presence,
    then a declared ``snapshot`` field, then containment under the
    baseline-set directory, then existence, then the recorded content digest
    (via the one shared
    :func:`~abicheck.buildsource.baseline_set._snapshot_digest_issue`, so
    this resolver and the target resolver can never disagree about what a
    valid snapshot is).
    """
    baseline_dir = Path(baseline_dir)
    snapshots: list[MemberSnapshot] = []
    problems: dict[str, str] = {}
    for member in members:
        unsafe = _unsafe_member_reason(member)
        if unsafe is not None:
            problems[member] = unsafe
            continue
        if manifest.artifact_count_for(member) > 1:
            problems[member] = (
                "multiple artifacts[] entries in this baseline-set's manifest "
                "-- ambiguous which one is authoritative"
            )
            continue
        artifact = manifest.artifact_for(member)
        if artifact is None:
            known = sorted(a.library for a in manifest.artifacts if a.library)
            problems[member] = (
                f"not in this baseline-set's manifest (known targets: {known})"
            )
            continue
        if not artifact.snapshot:
            problems[member] = "no snapshot declared in the manifest"
            continue
        resolved = _resolve_under_baseline_dir(baseline_dir, artifact.snapshot)
        if resolved is None or not resolved.is_file():
            problems[member] = (
                f"snapshot {artifact.snapshot!r} is missing, unreadable, or "
                "outside the baseline-set directory"
            )
            continue
        digest_issue = _snapshot_digest_issue(member, resolved, artifact.sha256)
        if digest_issue is not None:
            problems[member] = digest_issue[1]
            continue
        snapshots.append(MemberSnapshot(member=member, path=resolved))
    return MemberSnapshotResolution(snapshots=tuple(snapshots), problems=problems)


def _snapshot_suffix(path: Path) -> str:
    """The staged filename's suffix for *path* — its own recognized snapshot
    suffix when it has one, else its trailing extension chain."""
    name = path.name
    for suffix in sorted(_SNAPSHOT_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix):
            return suffix
    return "".join(path.suffixes[-2:]) or path.suffix


def _from_headers(path: Path) -> bool:
    """Whether *path*'s snapshot records header-derived evidence.

    Reads the one recorded fact rather than inferring from the presence of
    declarations: DWARF-derived declarations populate the same
    functions/types lists and must not be mistaken for header-level
    evidence (``AbiSnapshot.from_headers``'s own contract). A snapshot this
    build cannot load answers ``False`` — "no proven header evidence" — and
    never raises: staging must still produce a usable old-side operand and
    let the *assessment* carry the bad news.
    """
    from ..serialization import load_snapshot

    try:
        snapshot = load_snapshot(path)
    except Exception:
        return False
    return bool(getattr(snapshot, "from_headers", False))


def stage_member_snapshots(
    resolution: MemberSnapshotResolution,
    staging_root: Path | str,
) -> StagedBundleBaseline:
    """Materialize *resolution*'s snapshots as a clean compare-able directory.

    The destination is ``<staging_root>/member-snapshots/``. Any snapshot
    file left there by an earlier run is removed first — same reasoning as
    ``actions/baseline/run.sh``'s own stale-snapshot sweep: a member dropped
    since that run would otherwise sit in the directory, invisible to this
    result but still physically present for a ``compare`` that walks the
    tree, where it would read as a *removed library* on the new side. Only
    files this function itself writes are removed, never the whole
    directory.

    Two members that would stage to the same filename is a hard
    :class:`MemberStagingError`, not a silent overwrite: the second copy
    would replace the first while the result still listed both.
    """
    dest = Path(staging_root) / MEMBER_SNAPSHOTS_DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    for stale in dest.iterdir():
        if stale.is_file() and any(
            stale.name.endswith(suffix) for suffix in _SNAPSHOT_SUFFIXES
        ):
            stale.unlink()

    staged: list[StagedMemberSnapshot] = []
    written: dict[str, str] = {}
    for entry in resolution.snapshots:
        unsafe = _unsafe_member_reason(entry.member)
        if unsafe is not None:  # defensive: resolution already rejects these
            raise MemberStagingError(unsafe)
        target_name = f"{entry.member}{_snapshot_suffix(entry.path)}"
        if target_name in written:
            raise MemberStagingError(
                f"members {written[target_name]!r} and {entry.member!r} both "
                f"stage to {target_name!r} -- each member needs a distinct "
                "staged snapshot filename"
            )
        written[target_name] = entry.member
        target = dest / target_name
        shutil.copyfile(entry.path, target)
        staged.append(
            StagedMemberSnapshot(
                member=entry.member,
                path=target,
                from_headers=_from_headers(target),
            )
        )
    return StagedBundleBaseline(
        snapshots_dir=dest,
        staged=tuple(staged),
        problems=dict(resolution.problems),
    )


def stage_bundle_baseline_headers(
    baseline_dir: Path | str,
    manifest: BaselineManifest,
    members: list[str] | tuple[str, ...],
    staging_root: Path | str,
) -> StagedBundleBaseline:
    """:func:`resolve_member_snapshots` followed by
    :func:`stage_member_snapshots` — the one call a front end makes."""
    return stage_member_snapshots(
        resolve_member_snapshots(baseline_dir, manifest, members), staging_root
    )

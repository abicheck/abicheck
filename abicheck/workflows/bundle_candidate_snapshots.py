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
"""Which candidate file each bundle member is, and how to dump it, for a
``depth: headers`` bundle check.

A header-depth bundle compares two directories of per-member snapshots. The
old side is the baseline's own per-member snapshots, dumped at baseline time
with each member's own headers (``resolve-baseline``'s
``member-snapshot-paths``). The candidate side must be made the same way:
one dump per member, against that member's own ``public_headers:``. Parsing
every member against the union of all members' headers would invent or hide
header-level changes, which is why a bundle check used to refuse this depth.

This module only plans: it finds each member's candidate file in the staged
candidate directory by the canonical release key of its baseline binary (the
same key ``compare`` pairs members by), and names each output after the
member's staged baseline snapshot, so the two directories pair file for file.
``actions/check-target`` runs the dumps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .extraction import _canonical_library_key

__all__ = [
    "CandidateDump",
    "CandidatePlan",
    "CandidatePlanError",
    "plan_candidate_dumps",
]

#: Storage suffixes a staged baseline snapshot may carry; the candidate dump
#: is written uncompressed, and ``compare`` keys both spellings the same.
_COMPRESSION_SUFFIXES = (".gz", ".zst")


class CandidatePlanError(ValueError):
    """The dumps cannot be planned safely; refuse rather than guess."""


@dataclass(frozen=True)
class CandidateDump:
    """One member's candidate dump."""

    member: str
    candidate: Path
    #: File name for the dump, equal to the member's staged baseline
    #: snapshot name less any compression suffix.
    output_name: str
    headers: tuple[str, ...]


@dataclass(frozen=True)
class CandidatePlan:
    dumps: tuple[CandidateDump, ...]
    #: Members with no candidate file (the scope gate reports them).
    missing: tuple[str, ...]


def _split_lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())


def _output_name(snapshot: str) -> str:
    name = Path(snapshot).name
    for suffix in _COMPRESSION_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def plan_candidate_dumps(
    members: Sequence[str],
    member_headers: Mapping[str, str],
    baseline_binaries: Mapping[str, str],
    baseline_snapshots: Mapping[str, str],
    candidate_dir: Path,
) -> CandidatePlan:
    """Plan one header dump per member found in *candidate_dir*.

    Raises :class:`CandidatePlanError` when a member has no headers, no
    baseline binary or no staged baseline snapshot, or when two candidate
    files (or two members) share a release key.
    """
    if not members:
        raise CandidatePlanError("bundle has no members")
    by_key: dict[str, Path] = {}
    for path in sorted(p for p in candidate_dir.iterdir() if p.is_file()):
        key = _canonical_library_key(path)
        if key in by_key:
            raise CandidatePlanError(
                f"candidate files {by_key[key].name!r} and {path.name!r} share "
                f"the release key {key!r}"
            )
        by_key[key] = path
    dumps: list[CandidateDump] = []
    missing: list[str] = []
    claimed: dict[str, str] = {}
    for member in members:
        headers = _split_lines(member_headers.get(member, ""))
        if not headers:
            raise CandidatePlanError(
                f"member {member!r} declares no public_headers; a header-depth "
                "bundle dumps each member against its own headers"
            )
        binary = baseline_binaries.get(member)
        snapshot = baseline_snapshots.get(member)
        if not binary or not snapshot:
            raise CandidatePlanError(
                f"member {member!r} has no "
                f"{'baseline binary' if not binary else 'staged baseline snapshot'}"
            )
        key = _canonical_library_key(Path(binary))
        if key in claimed:
            raise CandidatePlanError(
                f"members {claimed[key]!r} and {member!r} share the release key {key!r}"
            )
        claimed[key] = member
        candidate = by_key.get(key)
        if candidate is None:
            missing.append(member)
            continue
        dumps.append(
            CandidateDump(
                member=member,
                candidate=candidate,
                output_name=_output_name(snapshot),
                headers=headers,
            )
        )
    names = [d.output_name for d in dumps]
    if len(set(names)) != len(names):
        raise CandidatePlanError(f"two members dump to one file name: {names!r}")
    return CandidatePlan(dumps=tuple(dumps), missing=tuple(missing))

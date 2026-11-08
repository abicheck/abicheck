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

"""Is the default-branch snapshot the same library as this PR's build?

Plan: ``docs/contribute/plans/pr-delta-vs-main-snapshot.md`` § Operand
identity. The delta-baseline rerun replaces the gate's old operand with the
base commit's stored snapshot. That is only this PR's delta when the snapshot
describes *the same library* as the PR's ``new-library``; a renamed output, a
different build directory or a SONAME bump must not stop the delta, and an
unrelated library must not be compared as if it were the old version (its
whole surface would read as removed -- ADR-065: absent is not removed).

The answer is one :class:`OperandMatch`: matched (and by which rule), or a
named refusal -- ``operand_unmatched`` (nothing links the two), or
``operand_ambiguous`` (the evidence disagrees: the same file name or path,
but unrelated SONAMEs). Neither refusal is ever rendered as a removal; the PR
comment says the delta is unavailable and why, and how to declare the pairing
(``delta-operand-map``).

Identity of one side is the path it was given as (``main-snapshot`` records
it in ``operand.json`` next to the snapshot; an artifact written before that
record existed simply has no path), its file name, and its loader name -- the
ELF ``DT_SONAME`` or the Mach-O install name's base name. PE images have no
loader name beyond the file name.
"""

from __future__ import annotations

import enum
import json
import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "OPERAND_RECORD_NAME",
    "OPERAND_RECORD_SCHEMA",
    "MatchRule",
    "OperandIdentity",
    "OperandMatch",
    "OperandMatchOutcome",
    "identity_of_operand",
    "identity_of_snapshot_artifact",
    "match_delta_operand",
    "operand_record",
    "parse_operand_map",
]

#: The file ``main-snapshot`` writes beside ``snapshot.json`` in the artifact.
OPERAND_RECORD_NAME = "operand.json"
OPERAND_RECORD_SCHEMA = 1


class OperandMatchOutcome(enum.StrEnum):
    MATCHED = "resolved"
    UNMATCHED = "operand_unmatched"
    AMBIGUOUS = "operand_ambiguous"
    UNREADABLE = "operand_unreadable"


class MatchRule(enum.StrEnum):
    """Why the two sides are the same library. Empty when they are not."""

    NONE = ""
    #: ``delta-operand-map`` names this pair.
    DECLARED = "declared"
    #: Same ELF SONAME / Mach-O install name.
    LOADER_NAME = "loader_name"
    #: Loader names differ only by version (``libfoo.so.1`` -> ``libfoo.so.2``).
    LOADER_NAME_VERSION = "loader_name_version"
    #: No loader name on one side; the same path or file name.
    FILE_NAME = "file_name"
    #: No loader name on one side; file names differ only by version.
    FILE_NAME_VERSION = "file_name_version"


@dataclass(frozen=True)
class OperandIdentity:
    #: Path as given to the producer / the Action (POSIX, normalized); may be "".
    path: str = ""
    file_name: str = ""
    #: ELF ``DT_SONAME`` or the base name of the Mach-O install name; may be "".
    loader_name: str = ""

    def names(self) -> tuple[str, ...]:
        """Every spelling a ``delta-operand-map`` entry may use for this side."""
        return tuple(n for n in (self.path, self.file_name, self.loader_name) if n)

    def label(self) -> str:
        shown = self.loader_name or self.file_name or "?"
        if self.path and self.path != shown:
            shown += f" ({self.path})"
        return shown


@dataclass(frozen=True)
class OperandMatch:
    outcome: OperandMatchOutcome
    rule: MatchRule = MatchRule.NONE
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is OperandMatchOutcome.MATCHED


def _norm_path(raw: str) -> str:
    raw = raw.strip().replace("\\", "/")
    if not raw:
        return ""
    out = posixpath.normpath(raw)
    return out[2:] if out.startswith("./") else out


def _version_key(name: str) -> str:
    """The release-matching key for a library file/loader name.

    The same rule release comparisons pair members by
    (``binary_utils._canonical_library_key``: ``libfoo.so.1.2`` ->
    ``libfoo.so``, ``libfoo.1.dylib`` -> ``libfoo.dylib``, vendor hashes
    stripped, PE names case-folded).
    """
    from ..binary_utils import _canonical_library_key

    return _canonical_library_key(Path(name))


def parse_operand_map(text: str) -> tuple[tuple[str, str], ...]:
    """``delta-operand-map``: one ``SNAPSHOT=PR`` pair per line.

    Either side may be a path, a file name or a loader name. Blank lines and
    ``#`` comments are ignored; a line without ``=`` or with an empty side is
    a usage error, not a silently-dropped pairing.
    """
    pairs: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        old, sep, new = line.partition("=")
        old, new = old.strip(), new.strip()
        if not sep or not old or not new:
            raise ValueError(
                f"delta-operand-map line {lineno}: expected SNAPSHOT=PR, got {line!r}"
            )
        pairs.append((old, new))
    return tuple(pairs)


def _mentions(identity: OperandIdentity, name: str) -> bool:
    name = name.strip()
    return name in identity.names() or _norm_path(name) in identity.names()


def match_delta_operand(
    snapshot: OperandIdentity,
    pr: OperandIdentity,
    operand_map: Sequence[tuple[str, str]] = (),
) -> OperandMatch:
    """Decide whether *snapshot* and *pr* describe the same library.

    An explicit *operand_map* is the project's own claim and is decisive:
    when it is given it must name the snapshot's library, and the entry must
    name the PR's library -- a map that does not cover this pair is reported,
    never overridden by the automatic rules. Without a map:

    1. equal loader names (SONAME / install name) match, whatever the paths;
    2. loader names that differ only by version match (a SONAME bump -- the
       delta then reports the bump itself as a finding);
    3. unrelated loader names on both sides never match; if the path or file
       name is nevertheless the same, the evidence disagrees and the answer
       is ``operand_ambiguous``;
    4. with a loader name missing on a side (PE, or a snapshot without one),
       the same path or file name matches, then a file name that differs
       only by version;
    5. anything else is ``operand_unmatched``.
    """
    pair = f"snapshot `{snapshot.label()}` vs this PR's `{pr.label()}`"
    if operand_map:
        for old, new in operand_map:
            if not _mentions(snapshot, old):
                continue
            if _mentions(pr, new):
                return OperandMatch(
                    OperandMatchOutcome.MATCHED,
                    MatchRule.DECLARED,
                    f"{pair}: paired by delta-operand-map ({old}={new})",
                )
            return OperandMatch(
                OperandMatchOutcome.UNMATCHED,
                message=f"{pair}: delta-operand-map pairs `{old}` with `{new}`, not with this PR's library",
            )
        return OperandMatch(
            OperandMatchOutcome.UNMATCHED,
            message=f"{pair}: no delta-operand-map entry names the snapshot's library",
        )

    same_place = bool(
        (snapshot.path and snapshot.path == pr.path)
        or (snapshot.file_name and snapshot.file_name == pr.file_name)
    )
    if snapshot.loader_name and pr.loader_name:
        if snapshot.loader_name == pr.loader_name:
            return OperandMatch(
                OperandMatchOutcome.MATCHED,
                MatchRule.LOADER_NAME,
                f"{pair}: same loader name",
            )
        if _version_key(snapshot.loader_name) == _version_key(pr.loader_name):
            return OperandMatch(
                OperandMatchOutcome.MATCHED,
                MatchRule.LOADER_NAME_VERSION,
                f"{pair}: loader name changed by version only",
            )
        if same_place:
            return OperandMatch(
                OperandMatchOutcome.AMBIGUOUS,
                message=f"{pair}: same file but unrelated loader names",
            )
        return OperandMatch(
            OperandMatchOutcome.UNMATCHED, message=f"{pair}: unrelated libraries"
        )

    if same_place:
        return OperandMatch(
            OperandMatchOutcome.MATCHED,
            MatchRule.FILE_NAME,
            f"{pair}: same path or file name",
        )
    if (
        snapshot.file_name
        and pr.file_name
        and _version_key(snapshot.file_name) == _version_key(pr.file_name)
    ):
        return OperandMatch(
            OperandMatchOutcome.MATCHED,
            MatchRule.FILE_NAME_VERSION,
            f"{pair}: file name changed by version only",
        )
    return OperandMatch(
        OperandMatchOutcome.UNMATCHED, message=f"{pair}: unrelated libraries"
    )


# ── reading each side ───────────────────────────────────────────────────────


def _loader_name_of(snapshot: Any) -> str:
    elf = getattr(snapshot, "elf", None)
    soname = str(elf.soname or "") if elf is not None else ""
    if soname:
        return soname
    macho = getattr(snapshot, "macho", None)
    install_name = str(macho.install_name or "") if macho is not None else ""
    return posixpath.basename(install_name)


def _identity_of_snapshot_file(path: Path, recorded_path: str = "") -> OperandIdentity:
    from ..serialization import load_snapshot

    snap = load_snapshot(path)
    return OperandIdentity(
        path=_norm_path(recorded_path),
        file_name=posixpath.basename(_norm_path(recorded_path))
        or str(snap.library or ""),
        loader_name=_loader_name_of(snap),
    )


def identity_of_operand(path: Path, given_as: str = "") -> OperandIdentity:
    """The PR side: a built library, or a snapshot file the user dumped.

    Raises ``OSError``/``ValueError`` (or a snapshot error) when *path* is
    neither a readable library nor a readable snapshot.
    """
    from .input_resolution import detect_binary_format, sniff_text_format

    shown = _norm_path(given_as or str(path))
    fmt = detect_binary_format(path)
    if fmt is None:
        if sniff_text_format(path) != "json":
            raise ValueError(f"{path} is neither a library nor an abicheck snapshot")
        return _identity_of_snapshot_file(path, shown)
    loader = ""
    if fmt == "elf":
        from ..elf_metadata import parse_elf_metadata

        loader = parse_elf_metadata(path).soname or ""
    elif fmt == "macho":
        from ..macho_metadata import parse_macho_metadata

        loader = posixpath.basename(parse_macho_metadata(path).install_name or "")
    return OperandIdentity(
        path=shown, file_name=posixpath.basename(shown), loader_name=loader
    )


def operand_record(library: str, profile: str = "") -> dict[str, Any]:
    """The ``operand.json`` document ``main-snapshot`` uploads."""
    return {
        "schema": OPERAND_RECORD_SCHEMA,
        "path": _norm_path(library),
        "profile": profile,
    }


def identity_of_snapshot_artifact(snapshot_path: Path) -> OperandIdentity:
    """The snapshot side: ``snapshot.json`` plus its ``operand.json``, if any.

    A record that is present but malformed is refused (``ValueError``) rather
    than ignored: it would otherwise silently drop the recorded path.
    """
    record = snapshot_path.parent / OPERAND_RECORD_NAME
    recorded = ""
    if record.is_file():
        data = json.loads(record.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping) or data.get("schema") != OPERAND_RECORD_SCHEMA:
            raise ValueError(f"{record}: unsupported operand record")
        raw = data.get("path", "")
        if not isinstance(raw, str):
            raise ValueError(f"{record}: 'path' is not a string")
        recorded = raw
    return _identity_of_snapshot_file(snapshot_path, recorded)

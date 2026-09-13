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

"""Assembling a ``compat check`` run's operands, and the notes it owes.

Deciding what an operand *is* (an ABICC descriptor, an ABICC Perl dump, an
abicheck snapshot, or a bare binary) and loading it, resolving both sides
into one pair of inputs, and emitting a run's informational notes are one
responsibility -- and none of it is Click surface: it takes paths and
returns loaded documents and text.

Split out of ``compat/cli.py`` for the reason
:mod:`abicheck.compat.multi_library_run` was: that file carries a
``no_growth`` baseline in ``architecture/debt.yaml``, and the way to respect
one is to give responsibility an owner rather than trim the file to fit.

Deliberately **not** moved, though both would fit the description above:
``_snapshot_from_compat_input`` and ``compat_dump_cmd`` hold this front
end's two allowlisted direct ``dumper.dump()`` calls, and the comparison
loop holds its single ``checker.compare``. The ``cli-contract`` gate scans
``cli*.py``/``compat/cli.py``, so relocating a Tier-1 call site to a module
outside that glob would take it out of the gate's scope rather than out of
the file's line budget -- which is not what a line budget is for.
``_take_snapshots_with_logging`` stays for a plainer reason: it calls
``_snapshot_from_compat_input``, so moving it would make this module import
back into ``cli.py``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import SnapshotError
from ..serialization import load_snapshot
from ._errors import _compat_fail
from ._helpers import _do_echo, _load_skip_headers
from .abicc_dump_import import (
    import_abicc_perl_dump,
    is_abicc_perl_dump_file,
    looks_like_perl_dump,
)
from .descriptor import CompatDescriptor, parse_descriptor
from .descriptor_expansion import expand_descriptor_headers, expand_descriptor_libs

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot


def _emit_compat_info_notes(
    *,
    quiet: bool,
    compat_html: bool,
    use_dumps: bool,
    filter_path: Path | None,
    params_path: Path | None,
    app_path: Path | None,
    arch: str | None,
    keep_cxx: bool,
    keep_reserved: bool,
    count_symbols: str | None,
    count_all_symbols: str | None,
) -> None:
    """Emit informational notes for ABICC-compat flags with limited effect."""
    notes: list[str] = []
    if compat_html:
        notes.append(
            "Note: -compat-html / -old-style enabled: HTML will match ABICC element IDs."
        )
    if use_dumps:
        notes.append(
            "Note: -use-dumps is accepted; abicheck auto-detects JSON dumps by extension."
        )
    if filter_path:
        notes.append(
            f"Note: -filter {filter_path} is accepted for compatibility (not yet applied)."
        )
    if params_path:
        notes.append(
            f"Note: -params {params_path} is accepted for compatibility (not yet applied)."
        )
    if app_path:
        notes.append(
            f"Note: -app {app_path} is accepted for compatibility (not yet applied)."
        )
    if arch:
        notes.append(f"Note: -arch {arch} is recorded for informational purposes.")
    if keep_cxx:
        notes.append(
            "Note: -keep-cxx is accepted; abicheck includes all exported symbols by default."
        )
    if keep_reserved:
        notes.append(
            "Note: -keep-reserved is accepted; abicheck reports all field changes by default."
        )
    if count_symbols:
        notes.append(
            f"Note: -count-symbols {count_symbols} is accepted for compatibility (not yet applied)."
        )
    if count_all_symbols:
        notes.append(
            f"Note: -count-all-symbols {count_all_symbols} is accepted for compatibility (not yet applied)."
        )
    for note in notes:
        _do_echo(note, quiet)


def _load_descriptor_or_dump(
    path: Path, *, relpath: str | None = None
) -> CompatDescriptor | AbiSnapshot:
    """Load either an ABICC XML descriptor or a JSON ABI dump.

    Returns:
        CompatDescriptor for XML descriptor files, AbiSnapshot for JSON dumps.

    Raises:
        ValueError: If the file is an ABICC Perl dump (unsupported format).
    """
    # ABICC Perl dump support (minimal migration-focused importer)
    if path.suffix == ".dump":
        return import_abicc_perl_dump(path)

    # Heuristic: if the file is JSON, load as a dump
    if path.suffix == ".json":
        return load_snapshot(path)

    # ADR-059 (Codex review): `compat dump -dump-path v1.json.gz`/`.json.zst`
    # writes a valid compressed snapshot (its documented companion command,
    # AGENTS.md), but Path.suffix only sees the *last* component ("gz"/
    # "zst"), so those fell through to the XML-descriptor heuristic below
    # and failed before ever reaching load_snapshot(). Recognize the
    # canonical compressed suffixes directly, and fall back to magic-byte
    # detection (never trusts the filename either way) so a compressed
    # snapshot written under a neutral name is still dispatched correctly.
    name = path.name.lower()
    if name.endswith((".json.gz", ".json.zst")):
        return load_snapshot(path)
    from ..workflows.storage import SnapshotCompression, detect_snapshot_compression

    try:
        compression_hint = detect_snapshot_compression(path)
    except SnapshotError:
        compression_hint = SnapshotCompression.NONE
    if compression_hint is not SnapshotCompression.NONE:
        return load_snapshot(path)

    # For XML files, peek at content to detect ABICC Perl dump disguised as .xml
    # (ABICC -dump-format xml produces a different XML schema than descriptors)
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:512]
    except OSError:
        head = ""

    # Detect ABICC Perl Data::Dumper format (starts with $VAR1 = { or similar)
    if looks_like_perl_dump(head):
        return import_abicc_perl_dump(path)

    # Detect ABICC XML dump format (contains <ABI_dump_* or <abi_dump tags)
    if "<ABI_dump" in head or "<abi_dump" in head or "ABI_COMPLIANCE_CHECKER" in head:
        raise ValueError(
            f"ABICC XML dump format detected: {path}\n"
            "  abicheck currently supports ABICC Perl Data::Dumper dumps, not ABICC XML dumps.\n"
            "  If possible, generate the default ABI.dump (Perl) format with abi-dumper,\n"
            "  or convert via descriptor using 'abicheck compat dump' to abicheck JSON."
        )

    # Otherwise parse as XML descriptor. Directory operands in <headers>/
    # <libs> are expanded here, at the boundary, so every downstream consumer
    # sees a concrete file list -- ABICC's ordinary usage points both elements
    # at directories, and an unexpanded one previously reached the header
    # parser as `#include "<dir>"` / the binary parser as "Unrecognised binary
    # format".
    desc = parse_descriptor(path, relpath=relpath)
    return dataclasses.replace(
        desc,
        headers=expand_descriptor_headers(desc.headers),
        libs=expand_descriptor_libs(desc.libs),
    )


def _load_compat_inputs(
    old_desc: Path,
    new_desc: Path,
    relpath: str | None,
    relpath1: str | None,
    relpath2: str | None,
    skip_headers: Path | None,
    quiet: bool,
) -> tuple[CompatDescriptor | AbiSnapshot, CompatDescriptor | AbiSnapshot, set[str]]:
    """Resolve relpath overrides, notify about Perl dumps, parse descriptors, load skip-headers set.

    Returns (old_d, new_d, skip_headers_set).
    """
    old_relpath = relpath1 or relpath
    new_relpath = relpath2 or relpath

    old_is_abicc_perl = is_abicc_perl_dump_file(old_desc)
    new_is_abicc_perl = is_abicc_perl_dump_file(new_desc)
    if old_is_abicc_perl or new_is_abicc_perl:
        _do_echo(
            "Info: ABICC Perl ABI.dump input detected. "
            "Using migration-focused importer (full ABICC dump parity is not guaranteed). "
            "Prefer abicheck JSON dumps for best fidelity.",
            quiet,
        )

    old_d, new_d = _parse_compat_descriptors(
        old_desc, new_desc, old_relpath, new_relpath
    )
    skip_headers_set = _load_skip_headers(skip_headers)
    if skip_headers_set:
        _do_echo(
            f"Applying -skip-headers: excluding {len(skip_headers_set)} header(s).",
            quiet,
        )

    return old_d, new_d, skip_headers_set


def _parse_compat_descriptors(
    old_desc: Path,
    new_desc: Path,
    old_relpath: str | None,
    new_relpath: str | None,
) -> tuple[CompatDescriptor | AbiSnapshot, CompatDescriptor | AbiSnapshot]:
    """Parse old/new descriptors or dumps with compat-mode error mapping."""
    try:
        return (
            _load_descriptor_or_dump(old_desc, relpath=old_relpath),
            _load_descriptor_or_dump(new_desc, relpath=new_relpath),
        )
    # TypeError: a malformed nested contract field rejected at the storage
    # boundary (storage AGENTS.md invariant 6), caught like a bad descriptor.
    except (TypeError, ValueError, FileNotFoundError, OSError) as exc:
        _compat_fail("parsing descriptor", exc)

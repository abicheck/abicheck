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
from ..model.header_exclusion_record import (
    DESCRIPTOR_MATCHING,
    record_header_exclusions,
)
from ..model.header_skip_rules import (
    HeaderSkipRule,
    achieved_exclusion_patterns,
    apply_skip_rules,
    compile_skip_rules,
)
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
    from collections.abc import Sequence

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


def effective_skip_rules(
    desc: CompatDescriptor, cli_skips: Sequence[str] = ()
) -> tuple[HeaderSkipRule, ...]:
    """The descriptor's own skip elements, plus ``-skip-headers FILE``'s.

    The two union rather than replace: both are exclusions and ABICC applies
    both. The descriptor's are per-side by construction (each side names its
    own), which is why they are merged here rather than once in
    :func:`_load_compat_inputs`.

    ``-skip-headers`` entries are ``<skip_headers>``-equivalent (exclude, not
    "do not include directly") and go through the same compiler, so one rule
    language governs both spellings -- a second, private membership test for
    the CLI file is exactly how the descriptor path came to have one.
    """
    return compile_skip_rules(sorted(cli_skips), ()) + desc.skip_rules()


def descriptor_header_universe(desc: CompatDescriptor) -> list[Path]:
    """The headers a descriptor's rules are matched against.

    A directory operand has to be walked before a rule naming a file beneath
    it can match, so "which headers did this rule take" is answered against
    the expanded list, not the declared one.
    """
    return expand_descriptor_headers(desc.headers)


def resolve_and_narrow_headers(
    desc: CompatDescriptor,
    rules: Sequence[HeaderSkipRule],
    headers_list_path: Path | None = None,
    single_header: str | None = None,
) -> tuple[list[Path], list[Path]]:
    """``(universe, narrowed)`` -- the operand list before and after *rules*.

    Returned as a pair, and resolved once, because the universe the rules
    are *recorded* against has to be the same one they were *applied* to.
    Deriving the record from the descriptor's ``<headers>`` alone meant a
    rule whose only match arrived via ``-headers-list`` or ``-header`` was
    reported as matching nothing and its achieved narrowing went unrecorded
    (CodeRabbit review) -- and an unrecorded narrowing is precisely what
    lets the comparability gate accept an asymmetric pair, the failure
    :func:`record_descriptor_skips` exists to prevent.

    Lives here rather than in ``compat.cli`` because it is the input half
    of the same question :func:`record_descriptor_skips` answers, and
    keeping the two together is what stops a third caller doing one without
    the other.
    """
    from ._helpers import _resolve_headers_from_list

    universe = _resolve_headers_from_list(
        headers_list_path, single_header, desc.headers
    )
    return universe, apply_skip_rules(universe, rules)


def warn_unreachable_skip_rules(
    rules: Sequence[HeaderSkipRule], headers: Sequence[Path], quiet: bool
) -> None:
    """Say so when a compiled skip rule took no header in this run.

    The three ABICC rule classes are all really matched now
    (:mod:`abicheck.model.header_skip_rules`), so the old
    "a wildcard excluded nothing" warning no longer describes anything --
    a pattern rule works. What remains worth reporting is the more general
    case it was a special instance of: a rule the user wrote that matched
    nothing at all, which usually means a typo or a rule written against a
    tree this descriptor does not point at. Silently doing nothing with a
    rule is what made the previous behavior look like working
    configuration.
    """
    unmatched = [r.value for r in rules if not any(r.matches(h) for h in headers)]
    if not unmatched:
        return
    _do_echo(
        "Warning: descriptor skip rule(s) "
        + ", ".join(repr(s) for s in sorted(set(unmatched)))
        + " matched no header in this run -- check the spelling against the "
        "<headers> tree (a bare name matches a file name, a value containing "
        "'/' matches a tree-relative path or directory, and one containing "
        "'*'/'?'/'[' is matched as a pattern).",
        quiet,
    )


def record_descriptor_skips(
    snapshot: AbiSnapshot,
    rules: Sequence[HeaderSkipRule],
    quiet: bool,
    headers: Sequence[Path] = (),
) -> AbiSnapshot:
    """Record a descriptor's achieved header narrowing, and report what it
    could not achieve.

    One function because the two steps always belong together: what gets
    *recorded* is the subset that really narrowed the analyzed surface, and
    what is left over is precisely what has to be *reported*. Splitting them
    is how a third caller ends up doing one and not the other -- the bug
    shape this whole area keeps producing.

    Only ``<skip_headers>`` rules are recorded
    (:func:`~abicheck.model.header_skip_rules.achieved_exclusion_patterns`).
    A ``<skip_including>`` rule changes how a declaration is reached, not
    whether it belongs to the contract, so recording it would make the
    snapshot claim a narrowing it did not perform -- the coverage warning
    would then report headers omitted, and the comparability gate would
    refuse an otherwise identical unexcluded operand.
    """
    warn_unreachable_skip_rules(rules, headers, quiet)
    return record_header_exclusions(
        snapshot,
        achieved_exclusion_patterns(rules, headers),
        matching=DESCRIPTOR_MATCHING,
    )

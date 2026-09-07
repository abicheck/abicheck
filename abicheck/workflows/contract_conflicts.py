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

"""Workstream E slice S3, case 3: package-claim vs. contained-binary.

Depends on :mod:`abicheck.debian_symbols`, a ``workflows``-classified
module, so this detector lives here rather than beside its siblings in
:mod:`abicheck.policy.contract_conflicts`. Also owns the CLI-facing helper
(:func:`debian_symbols_binary_conflict_lines`) the directory/package release
fan-out calls -- it needs :mod:`abicheck.elf_metadata` (an ``extract``
module `workflows` may import but `frontends` may not), so the matching
and formatting logic lives here rather than in the ``frontends``-classified
CLI helper module that calls it.
"""

from __future__ import annotations

from pathlib import Path

from ..model.contract_conflicts import (
    CONFLICT_PACKAGE_BINARY_MISMATCH,
    ConflictSourceClaim,
    ContractSourceConflict,
)


def detect_package_binary_mismatch(
    symbols_file: object,
    elf_meta: object,
) -> list[ContractSourceConflict]:
    """A package's declared metadata (SONAME, symbol list) vs. the binary
    actually contained in that same package.

    Reuses :func:`abicheck.debian_symbols.validate_symbols` -- already the
    codebase's one matcher between a ``dpkg-gensymbols(1)`` contract and an
    ELF binary's real exports -- rather than re-deriving the match, and
    turns its ``missing`` list (declared by the package, absent from the
    binary) plus a direct SONAME comparison into conflict records with both
    sides' claims kept. ``new_symbols`` (exported by the binary but
    undeclared in the package's symbols file) is deliberately not folded in
    here: that is exactly
    :func:`abicheck.policy.contract_conflicts.detect_exported_but_undeclared`'s
    own question, asked between a *different* pair of sources (export table
    vs. public headers, not export table vs. package metadata) --
    conflating the two would double-report the same kind of asymmetry under
    two different conflict kinds for what is, evidentially, two different
    disagreements.

    ``symbols_file``/``elf_meta`` are typed loosely (``object``) so this
    module's own import stays a single deferred one below; callers pass real
    :class:`~abicheck.debian_symbols.DebianSymbolsFile`/
    :class:`~abicheck.elf_metadata.ElfMetadata` instances.
    """
    from ..debian_symbols import validate_symbols

    result = validate_symbols(elf_meta, symbols_file)  # type: ignore[arg-type]
    conflicts: list[ContractSourceConflict] = []

    declared_library = getattr(symbols_file, "library", None)
    observed_soname = getattr(elf_meta, "soname", None)
    if declared_library and observed_soname and declared_library != observed_soname:
        conflicts.append(
            ContractSourceConflict(
                conflict_kind=CONFLICT_PACKAGE_BINARY_MISMATCH,
                entity="soname",
                sources=(
                    ConflictSourceClaim(
                        source_kind="package_metadata",
                        claim=f"package declares library {declared_library!r}",
                        detail={"soname": declared_library},
                    ),
                    ConflictSourceClaim(
                        source_kind="binary",
                        claim=f"contained binary's SONAME is {observed_soname!r}",
                        detail={"soname": observed_soname},
                    ),
                ),
                reason_code="package_soname_mismatch",
            )
        )

    for entry in result.missing:
        name = getattr(entry, "name", None)
        if not name:
            continue
        version_node = getattr(entry, "version_node", "")
        conflicts.append(
            ContractSourceConflict(
                conflict_kind=CONFLICT_PACKAGE_BINARY_MISMATCH,
                entity=name,
                sources=(
                    ConflictSourceClaim(
                        source_kind="package_metadata",
                        claim=(
                            f"package declares symbol {name!r} "
                            f"(version node {version_node!r})"
                        ),
                        detail={"symbol": name, "version_node": version_node},
                    ),
                    ConflictSourceClaim(
                        source_kind="binary",
                        claim=("contained binary does not export this symbol"),
                        detail={"symbol": name},
                    ),
                ),
                reason_code="package_declares_symbol_binary_lacks",
            )
        )
    return conflicts


def _find_declared_symbols_binary(lib_dir: Path, declared_library: str) -> Path | None:
    """Best-effort match of a Debian ``.symbols`` file's own ``library``
    header field (e.g. ``libfoo.so.1``) against an extracted package
    directory's actual files.

    A ``dpkg-gensymbols(1)`` ``library`` field is conventionally the SONAME,
    which a real shipped file often extends with a patch version
    (``libfoo.so.1.2.3``) -- an exact match is tried first, then a
    ``startswith`` match against every regular file under *lib_dir*, sorted
    for determinism. Returns ``None`` on any I/O error or when nothing
    matches -- this is advisory package-hygiene context, never allowed to
    fail the release compare it feeds into (ADR-028 D3's "evidence may add
    context, never invent/hide a break" principle, applied here too).
    """
    if not declared_library:
        return None
    try:
        candidates = sorted(p for p in lib_dir.rglob("*") if p.is_file())
    except OSError:
        return None
    for c in candidates:
        if c.name == declared_library:
            return c
    for c in candidates:
        if c.name.startswith(declared_library):
            return c
    return None


def debian_symbols_binary_conflict_lines(
    symbols_file: Path | None, lib_dir: Path | None
) -> list[str]:
    """Workstream E slice S3, case 3: does *this* package's own declared
    ``dpkg-gensymbols(1)`` contract (SONAME, symbol list) match the binary
    actually contained in that same package -- as opposed to the directory/
    package release fan-out's pre-existing Debian-symbols warning, which
    diffs two *different* packages' contracts against each other.

    Returns formatted, human-readable lines (empty when nothing conflicts,
    the symbols file/binary can't be matched, or either fails to parse) --
    purely additive/advisory; never gates the release compare's own verdict/
    exit code. The recorded
    :class:`~abicheck.model.contract_conflicts.ContractSourceConflict`
    objects (both sides' claims kept, per ADR-067) are what
    :func:`detect_package_binary_mismatch` produces -- this function only
    formats them for the release fan-out's existing plain-text warnings
    list, which has no structured slot for a third package-level evidence
    source yet.
    """
    if symbols_file is None or lib_dir is None:
        return []
    try:
        from ..debian_symbols import load_symbols_file
        from ..elf_metadata import parse_elf_metadata

        parsed = load_symbols_file(symbols_file)
        so_path = _find_declared_symbols_binary(lib_dir, parsed.library)
        if so_path is None:
            return []
        elf_meta = parse_elf_metadata(so_path)
    except Exception:
        # Advisory only (see docstring) -- any parse/lookup failure here
        # must never interrupt the release compare it's merely annotating.
        return []
    conflicts = detect_package_binary_mismatch(parsed, elf_meta)
    if not conflicts:
        return []
    lines = [f"Package metadata vs. contained binary ({so_path.name}) disagree:"]
    for c in conflicts:
        claims = "; ".join(f"{s.source_kind}: {s.claim}" for s in c.sources)
        lines.append(f"  - {c.entity} ({c.reason_code}): {claims}")
    return lines


def debian_symbols_release_conflict_lines(
    old_symbols_file: Path | None,
    old_lib_dir: Path | None,
    new_symbols_file: Path | None,
    new_lib_dir: Path | None,
) -> list[str]:
    """:func:`debian_symbols_binary_conflict_lines` for both release-compare
    sides at once, each line prefixed ``[old]``/``[new]`` -- the shape the
    directory/package release fan-out's plain-text warnings list wants.
    """
    lines: list[str] = []
    for side_label, symbols_file, lib_dir in (
        ("old", old_symbols_file, old_lib_dir),
        ("new", new_symbols_file, new_lib_dir),
    ):
        for line in debian_symbols_binary_conflict_lines(symbols_file, lib_dir):
            lines.append(f"[{side_label}] {line}")
    return lines


__all__ = [
    "debian_symbols_binary_conflict_lines",
    "debian_symbols_release_conflict_lines",
    "detect_package_binary_mismatch",
]

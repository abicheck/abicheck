# Copyright 2026 Nikolay Petrov
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

"""ADR-050 D1/D2 — the comparability contract: profile/scope fingerprints
and the gate that proves two snapshots were extracted comparably before
``compare`` is allowed to produce a verdict.

**Scope of this module today (ADR-050 "Phase A, slice 1" — see
``docs/development/plans/g32-comparability-contract-and-multi-tu-manifest.md``
Phase A): the fingerprint algorithm and the gate itself, as pure,
independently unit-tested functions.** Not yet wired in:

- ``dumper.py`` does not call :func:`compute_extraction_contract` yet, so
  every freshly-produced snapshot still has ``contract=None`` — the gate
  below is fully specified but currently inert in the real CLI/API surface.
- :func:`check_contracts_comparable` is not yet called from
  ``checker.compare`` or any of the ADR's other six entry points
  (``service.py``, ``mcp_server.py``, ``cli_compare_release.py``,
  ``compat/cli.py``, ``cli_scan.py``, ``stack_checker.py``).
- The legacy-CLI labeled ``--include old:LABEL=PATH`` grammar
  (``SidedIncludePathParam``) does not exist yet; this module accepts a
  resolved ``label`` per :class:`IncludeDir` directly; only the CLI-parsing
  glue that would populate it from a command line is missing.
- ``snapshot_cache.py``'s cache-key order-sensitivity fix is not part of
  this module.

These are tracked as explicit follow-up work, not silently dropped scope.

## The two fingerprints

``scope_fingerprint`` identifies the *declared surface* being compared
(header/TU names, never absolute paths). ``profile_fingerprint`` identifies
the *resolved compile context* used to extract it (compiler, macros,
``-I`` search-path *content* — never path shape, since a two-checkout
compare's old/new sides necessarily resolve to different absolute paths for
an identical logical surface). See :func:`compute_extraction_contract`'s
docstring for the full algorithm.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
from .model import AbiSnapshot, ExtractionContract

# Named sub-components hashed into profile_fingerprint / scope_fingerprint,
# also stored verbatim in ExtractionContract.profile_fields/scope_fields so a
# mismatch can be attributed to a specific field instead of an opaque hash.
PROFILE_FIELD_KEYS = (
    "compiler_family",
    "compiler_version",
    "abi_dialect",
    "language_standard",
    "target_triple",
    "pointer_width",
    "endianness",
    "macro_ops",
    "include_sequence",
)
SCOPE_FIELD_KEYS = (
    "headers",
    "public_header_paths",
    "public_header_dirs",
)

# The only profile_fields keys the platform-identity carve-out (ADR-050
# Phase A) is allowed to treat as non-fatal, and only when the snapshots'
# own binary-derived platform metadata confirms a genuine architecture
# difference on that same axis (see check_contracts_comparable).
_PLATFORM_IDENTITY_FIELDS = frozenset({"target_triple", "pointer_width", "endianness"})


@dataclass(frozen=True)
class IncludeDir:
    """One declared ``-I`` search-path entry, in the order it was declared
    on the command line (or manifest, once that exists) — order is itself a
    hashed input, since ``-I`` order is real compiler search-precedence
    order, not cosmetic.

    ``label`` is the resolved value of a legacy-CLI labeled
    ``--include old:LABEL=PATH`` entry (ADR-050 D1) — ``None`` for an
    ordinary, unlabeled entry. This module accepts the resolved label
    directly; the CLI grammar that would parse it from a command line is
    separate, not-yet-built work (see this module's docstring).
    """

    path: Path
    label: str | None = None


def _resolved(path: Path) -> Path:
    return path.resolve()


def _is_ancestor_or_equal(root: Path, path: Path) -> bool:
    root = _resolved(root)
    path = _resolved(path)
    return path == root or root in path.parents


def _common_ancestor_of_parents(paths: Sequence[Path]) -> Path | None:
    """The common ancestor **directory** of ``paths``' own *parent*
    directories (never of the paths themselves) — taking the parent first
    is what keeps a single-header side's basename from collapsing to an
    empty/root marker (ADR-050 D1)."""
    if not paths:
        return None
    parents = [str(_resolved(p).parent) for p in paths]
    return Path(os.path.commonpath(parents))


def _content_hash(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        # ADR-050 D1: a resolved header's content that can't be read at
        # fingerprint time must fail extraction outright, not fold an
        # "unresolvable" sentinel into the hash — two runs unresolvable for
        # different reasons must not spuriously fingerprint-match.
        raise SnapshotError(
            f"cannot read {path} while computing profile_fingerprint: {exc}"
        ) from exc
    return hashlib.sha256(data).hexdigest()


def _sha256_of(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return f"sha256:{h.hexdigest()}"


def _classify_include_dirs(
    declared_headers: Sequence[Path],
    declared_includes: Sequence[IncludeDir],
) -> list[bool]:
    """Return, per ``declared_includes`` entry (same order/length), whether
    that directory is project-owned: labeled explicitly (a sibling support
    root with no owned declared header), or equal to/an ancestor of any
    declared header (ADR-050 D1)."""
    owned = []
    for inc in declared_includes:
        if inc.label is not None:
            owned.append(True)
            continue
        owned.append(any(_is_ancestor_or_equal(inc.path, h) for h in declared_headers))
    return owned


def _slot_token_for_ancestor(inc: IncludeDir, declared_headers: Sequence[Path]) -> str:
    owned_header_names = sorted(
        h.name for h in declared_headers if _is_ancestor_or_equal(inc.path, h)
    )
    return "hdrs:" + ",".join(owned_header_names)


def _attribute_file(
    file_path: Path,
    declared_includes: Sequence[IncludeDir],
    ownership: Sequence[bool],
    declared_headers: Sequence[Path],
) -> int | None:
    """Return the index into ``declared_includes`` that ``file_path`` is
    attributed to (longest-prefix match among directories that actually
    contain it), or ``None`` if it falls under no declared ``-I`` directory
    at all (the system/toolchain bucket) or under a declared header's own
    (implicitly project-owned) parent directory."""
    best_idx: int | None = None
    best_len = -1
    for idx, inc in enumerate(declared_includes):
        if _is_ancestor_or_equal(inc.path, file_path):
            depth = len(_resolved(inc.path).parts)
            if depth > best_len:
                best_len = depth
                best_idx = idx
    if best_idx is not None:
        return best_idx
    # A declared header's own parent directory is implicitly project-owned
    # even with no matching --include at all (quote-include same-directory
    # resolution) — attribute such a file to a synthetic "owned, excluded"
    # bucket by returning -1, distinct from "no declared -I dir at all".
    for h in declared_headers:
        if _is_ancestor_or_equal(h.parent, file_path):
            return -1
    return None


def compute_extraction_contract(
    *,
    compiler_family: str | None = None,
    compiler_version: str | None = None,
    abi_dialect: str | None = None,
    language_standard: str | None = None,
    target_triple: str | None = None,
    pointer_width: int | None = None,
    endianness: str | None = None,
    macro_ops: Sequence[tuple[str, str]] = (),
    declared_headers: Sequence[Path] = (),
    declared_includes: Sequence[IncludeDir] = (),
    depfile_resolved_paths: Sequence[Path] = (),
    generated_driver_path: Path | None = None,
    l2_frontend_ran: bool = False,
    public_header_paths: Sequence[Path] = (),
    public_header_dirs: Sequence[Path] = (),
) -> ExtractionContract | None:
    """Compute one side's :class:`ExtractionContract` for the legacy,
    non-manifest CLI path (ADR-050 D1; the manifest-driven path is Phase B,
    not yet implemented).

    All inputs are already-resolved data ``dumper.py`` would hand this
    function after running the actual castxml/clang invocation and parsing
    its ``-MD`` depfile (not yet wired — see this module's docstring); this
    function itself never shells out or re-parses anything.

    Returns ``None`` when there is nothing to fingerprint at all (no L2
    frontend ran and no public-header provenance inputs were given) — the
    same "computed from nothing, not from unused inputs" rule ADR-050
    documents for a plain symbols-only/binary-only dump.

    ``profile_fingerprint`` is ``None`` whenever ``l2_frontend_ran`` is
    False (no castxml/clang invocation actually ran, so those resolved
    fields describe nothing the snapshot depends on) even if some of the
    profile keyword arguments were passed — the caller states explicitly
    whether an L2 frontend ran rather than this function guessing from
    which fields happen to be non-empty.

    ``-I`` **ownership and tokenization** (the load-bearing part of
    ``profile_fingerprint``):

    - The generated aggregate-driver TU (``generated_driver_path``, if any)
      is dropped from ``depfile_resolved_paths`` before any bucketing —
      its content embeds side-specific absolute paths that would otherwise
      make every routine two-checkout compare mismatch.
    - A declared ``-I`` directory is **project-owned** when it is labeled
      (an explicit sibling-support-root escape hatch) or is equal to/an
      ancestor of any declared header — every file under it, named or not,
      is excluded from the digest entirely (it belongs to
      ``scope_fingerprint``'s job, not this one).
    - A declared header's own parent directory is *implicitly*
      project-owned too, even with no matching ``--include`` at all
      (quote-include same-directory resolution needs no compiler flag).
    - Every other declared ``-I`` directory is **external**: its slot's
      content is the sorted set of (path relative to that directory,
      content hash) pairs for every depfile-listed file attributed to it.
    - A project-owned slot keeps its **position** in the ordered sequence
      (order is search-precedence order, a real compile difference) but its
      content is replaced with a per-slot logical token: the sorted set of
      declared header basenames it is an ancestor of, or its user-supplied
      ``label`` for an explicitly labeled entry — never one shared
      constant, which would collapse two differently-ordered project-owned
      roots to the same sequence.
    - Every depfile-listed file attributed to no declared ``-I`` directory
      (and not under a declared header's own parent) feeds one additional,
      unordered **system/toolchain bucket**, appended last.
    """
    l2_inputs_present = bool(
        l2_frontend_ran
        or compiler_family
        or declared_includes
        or depfile_resolved_paths
        or macro_ops
    )
    scope_inputs_present = bool(
        declared_headers or public_header_paths or public_header_dirs
    )
    if not l2_inputs_present and not scope_inputs_present:
        return None

    profile_fingerprint: str | None = None
    profile_fields: dict[str, str] = {}
    if l2_frontend_ran:
        ownership = _classify_include_dirs(declared_headers, declared_includes)

        resolved_paths = [
            p for p in depfile_resolved_paths if p != generated_driver_path
        ]
        per_slot_files: list[list[Path]] = [[] for _ in declared_includes]
        system_bucket_files: list[Path] = []
        for file_path in resolved_paths:
            idx = _attribute_file(
                file_path, declared_includes, ownership, declared_headers
            )
            if idx is None:
                system_bucket_files.append(file_path)
            elif idx == -1:
                continue  # implicitly project-owned via a declared header's parent
            else:
                per_slot_files[idx].append(file_path)

        slot_tokens: list[str] = []
        for idx, inc in enumerate(declared_includes):
            if ownership[idx]:
                if inc.label is not None:
                    token = f"label:{inc.label}"
                else:
                    token = _slot_token_for_ancestor(inc, declared_headers)
            else:
                pairs = sorted(
                    (
                        str(_resolved(f).relative_to(_resolved(inc.path))),
                        _content_hash(f),
                    )
                    for f in per_slot_files[idx]
                )
                token = "ext:" + _sha256_of(*[f"{p}={h}" for p, h in pairs])
            slot_tokens.append(f"{idx}:{token}")

        if system_bucket_files:
            sys_pairs = sorted(
                (str(_resolved(f)), _content_hash(f)) for f in system_bucket_files
            )
            slot_tokens.append("sys:" + _sha256_of(*[f"{p}={h}" for p, h in sys_pairs]))

        profile_fields = {
            "compiler_family": compiler_family or "",
            "compiler_version": compiler_version or "",
            "abi_dialect": abi_dialect or "",
            "language_standard": language_standard or "",
            "target_triple": target_triple or "",
            "pointer_width": str(pointer_width) if pointer_width is not None else "",
            "endianness": endianness or "",
            "macro_ops": "|".join(f"{op}:{val}" for op, val in macro_ops),
            "include_sequence": "|".join(slot_tokens),
        }
        profile_fingerprint = _sha256_of(
            *[profile_fields[k] for k in PROFILE_FIELD_KEYS]
        )

    scope_fingerprint: str | None = None
    scope_fields: dict[str, str] = {}
    if scope_inputs_present:
        root = _common_ancestor_of_parents(declared_headers)
        if root is not None:
            normalized_headers = sorted(
                str(_resolved(h).relative_to(root)) for h in declared_headers
            )
        else:
            normalized_headers = []
        scope_fields = {
            "headers": "|".join(normalized_headers),
            "public_header_paths": "|".join(
                sorted(str(p) for p in public_header_paths)
            ),
            "public_header_dirs": "|".join(sorted(str(p) for p in public_header_dirs)),
        }
        scope_fingerprint = _sha256_of(*[scope_fields[k] for k in SCOPE_FIELD_KEYS])

    return ExtractionContract(
        profile_fingerprint=profile_fingerprint,
        scope_fingerprint=scope_fingerprint,
        profile_fields=profile_fields,
        scope_fields=scope_fields,
    )


def _binary_platform_axis(snap: AbiSnapshot) -> tuple[str, str, str] | None:
    """Read the same binary-header platform-identity fields
    ``elf_machine_changed``/``elf_class_changed``/``elf_endianness_changed``
    (and PE/Mach-O equivalents) already detect directly, so the profile
    carve-out below can confirm a target-only mismatch corresponds to a
    genuine architecture difference rather than a misconfigured extraction.
    Returns None when no binary-derived platform metadata is available."""
    if snap.elf is not None:
        elf_machine = getattr(snap.elf, "machine", "")
        if elf_machine:
            return ("elf", elf_machine, getattr(snap.elf, "ei_data", ""))
    if snap.pe is not None:
        pe_machine = getattr(snap.pe, "machine", "")
        if pe_machine:
            return ("pe", pe_machine, "")
    if snap.macho is not None:
        macho_cpu_type = getattr(snap.macho, "cpu_type", "")
        if macho_cpu_type:
            return ("macho", macho_cpu_type, "")
    return None


def check_contracts_comparable(old: AbiSnapshot, new: AbiSnapshot) -> None:
    """ADR-050 D2 — the comparability gate. Raises :class:`ProfileMismatchError`
    or :class:`ScopeMismatchError` when both sides carry the corresponding
    fingerprint and it differs; does nothing (including when one or both
    sides carry no ``contract`` at all) otherwise.

    Each fingerprint is gated **independently** — a symbols-only side with
    only a ``scope_fingerprint`` compared against a full L2 side still gets
    its scope checked, without spuriously hard-failing on
    ``profile_fingerprint`` alone just because one side never ran an L2
    frontend (an ordinary depth difference, not scope drift).

    **Platform-identity carve-out:** a ``profile_fingerprint`` mismatch
    confined to ``target_triple``/``pointer_width``/``endianness`` does not
    raise when the snapshots' own binary-derived platform metadata (the same
    fields ``elf_machine_changed``/``elf_class_changed``/
    ``elf_endianness_changed`` already read) confirms a genuine architecture
    difference — comparing genuinely different target architectures is
    already correctly, more specifically classified ``BREAKING`` by
    ``diff_platform.py``; gating it into a generic ``not_comparable`` first
    would only downgrade a proven verdict. If the only differing fields are
    the platform-identity ones but the binaries themselves do **not**
    differ on that axis, this is a misconfigured extraction (e.g. a
    cross-compiler flag set for only one side), not a legitimate
    cross-architecture compare, and still raises.
    """
    old_contract = old.contract
    new_contract = new.contract

    if (
        old_contract is not None
        and new_contract is not None
        and old_contract.scope_fingerprint is not None
        and new_contract.scope_fingerprint is not None
        and old_contract.scope_fingerprint != new_contract.scope_fingerprint
    ):
        raise ScopeMismatchError(
            "old and new snapshots do not cover the same declared surface "
            "(scope_fingerprint mismatch) — the comparison is not "
            "comparable. This commonly means a manifest/CLI-flag drift "
            "between the two extraction runs, not a real API change."
        )

    if (
        old_contract is not None
        and new_contract is not None
        and old_contract.profile_fingerprint is not None
        and new_contract.profile_fingerprint is not None
        and old_contract.profile_fingerprint != new_contract.profile_fingerprint
    ):
        old_fields = old_contract.profile_fields
        new_fields = new_contract.profile_fields
        differing = {
            k
            for k in PROFILE_FIELD_KEYS
            if old_fields.get(k, "") != new_fields.get(k, "")
        }
        if differing and differing <= _PLATFORM_IDENTITY_FIELDS:
            old_axis = _binary_platform_axis(old)
            new_axis = _binary_platform_axis(new)
            if old_axis is not None and new_axis is not None and old_axis != new_axis:
                return  # genuine cross-architecture compare; diff_platform.py handles it
        raise ProfileMismatchError(
            "old and new snapshots were extracted under different compile "
            f"contexts (profile_fingerprint mismatch; differing fields: "
            f"{', '.join(sorted(differing)) or 'unknown'}) — the comparison "
            "is not comparable."
        )

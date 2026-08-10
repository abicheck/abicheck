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

"""ADR-050 D1 — the extraction-contract *field* layer.

``comparability.py`` owns the two questions a caller asks of a contract: what
one a dump has (:func:`~abicheck.comparability.compute_extraction_contract`),
and whether two of them are comparable
(:func:`~abicheck.comparability.check_contracts_comparable`). This module owns
the inner half of the first — how a resolved compile context and a declared
surface each become the ``profile_fields``/``scope_fields`` dicts those
fingerprints are hashed from, plus the side-local path-identity primitives that
keep those fields free of checkout-root noise.

Split out of ``comparability.py`` for file size (the AI-readiness gate's
2000-line hard cap), along the seam that was already there: nothing here needs
anything from the gate side, so this is a leaf depending only on
``comparability_json`` and the standard library, and nothing in it may import
back. ``comparability.py`` re-exports the names its own public surface
historically carried (``IncludeDir``, ``_sha256_of``,
``_fingerprint_matches_fields``), so no caller's import path changes.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import SnapshotError


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


def _common_root(candidates: Sequence[str]) -> Path | None:
    """``os.path.commonpath``, tolerant of candidates with no shared anchor
    at all (CodeRabbit review, PR #624): mixed drives on Windows, or a local
    vs. UNC root, make ``commonpath`` raise ``ValueError`` instead of
    degrading gracefully — that must not propagate out of fingerprinting as
    an unhandled crash. Returns ``None`` when there is no common root to
    strip; callers fall back to :func:`_side_local_identity`'s
    drive-stripped form in that case."""
    try:
        return Path(os.path.commonpath(candidates))
    except ValueError:
        return None


def _side_local_identity(path: Path, root: Path | None) -> str:
    """A path's identity relative to ``root``, or — when ``root`` is
    ``None`` because this side's declared paths share no common anchor at
    all — the drive-stripped absolute path (still deterministic and
    drive-letter-independent, just without a common prefix to strip)."""
    resolved = _resolved(path)
    if root is not None:
        return str(resolved.relative_to(root))
    return os.path.splitdrive(str(resolved))[1]


def _is_ancestor_or_equal(root: Path, path: Path) -> bool:
    root = _resolved(root)
    path = _resolved(path)
    return path == root or root in path.parents


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


def _fingerprint_matches_fields(
    fingerprint: str, fields: dict[str, str], keys: tuple[str, ...]
) -> bool:
    """Whether *fingerprint* is exactly what :func:`compute_extraction_contract`
    would produce from *fields* over *keys* (``SCOPE_FIELD_KEYS`` or
    ``PROFILE_FIELD_KEYS``) -- i.e. whether this contract's stored fields
    are actually what its own fingerprint was computed from (Codex review,
    PR #641 follow-up, sixth P1).

    Every carve-out above reasons entirely from ``scope_fields``/
    ``profile_fields`` -- "this recognized field grew additively, so the
    fingerprint mismatch is explained" -- but that reasoning only holds if
    the stored fingerprint was genuinely computed from those exact fields.
    For a snapshot dumped by this codebase's own
    :func:`compute_extraction_contract`, that invariant always holds by
    construction. It is NOT verified anywhere, though, so a deserialized or
    externally constructed contract can carry a stale, fabricated, or
    otherwise unrelated fingerprint alongside fields that merely *look*
    additive: confirmed by direct repro before any fix -- two arbitrary,
    unrelated fingerprint strings with ``headers`` genuinely growing from
    ``["a.h", "b.h"]`` to ``["a.h", "b.h", "c.h"]`` made
    :func:`check_contracts_comparable` return ``None`` (comparable) even
    though neither fingerprint was ever verified to have anything to do
    with those fields -- the true cause of the mismatch remains completely
    unverified. Called on BOTH sides before any carve-out below is trusted;
    a mismatch on either side means the fields can't be trusted to explain
    that side's own fingerprint, so nothing reasoned from them is safe to
    act on.
    """
    return fingerprint == _sha256_of(*[fields.get(k, "") for k in keys])


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


def _header_identities(
    declared_headers: Sequence[Path], extra_root_paths: Sequence[Path] = ()
) -> dict[Path, str]:
    """A stable, side-local identity string per declared header — its path
    relative to the common ancestor of every declared header's *parent*
    directory (the same normalization ``scope_fingerprint`` uses for its own
    header identity). Used only to build ancestor-derived slot tokens below;
    the basename alone (Codex review, PR #624) is not enough to disambiguate
    two project-owned roots that each own a different declared header
    sharing the same basename (e.g. ``include/foo.h`` vs.
    ``generated/foo.h``) — both would otherwise collapse to token
    ``hdrs:foo.h``, silently losing the order-sensitivity a swapped
    ``-I include -I generated`` vs. ``-I generated -I include`` is supposed
    to preserve.

    *extra_root_paths* (``public_header_paths`` — provenance-only headers
    never fed to the L2 frontend, so never in *declared_headers* or the
    returned dict) widens the root candidates the same way the scope
    ``"headers"`` field's own root computation already does (Codex review,
    PR #641 follow-up, sixteenth P2): without this, a ``public_header_paths``
    entry outside every declared header's common root makes this function's
    root narrower than the scope side's, so the SAME physical file gets two
    different identity strings across the two fields (e.g. scope
    ``"foo/c.h"`` vs. sequence ``"c.h"``) — and the header/include-sequence
    carve-outs' specific-correspondence check (``_scope_newly_added_headers``)
    compares these by exact string equality, so a genuinely safe, purely
    additive header change spuriously hard-fails with ``ProfileMismatchError``
    the moment any ``public_header_paths`` entry reaches outside that
    narrower root. Widening the root here to match closes the mismatch
    without changing what a declared header's identity actually disambiguates
    (its path relative to a root is still unique per file — the root is
    just wider than before when *extra_root_paths* is non-empty)."""
    if not declared_headers:
        return {}
    parents = [str(_resolved(h).parent) for h in (*declared_headers, *extra_root_paths)]
    root = _common_root(parents)
    return {h: _side_local_identity(h, root) for h in declared_headers}


def _slot_token_for_ancestor(
    inc: IncludeDir,
    declared_headers: Sequence[Path],
    header_identities: dict[Path, str],
    single_header_mode: bool,
) -> str:
    # A single declared header's own name is not load-bearing here either
    # (Codex review, PR #624 follow-up -- CI went red at scale once real
    # dumps started populating contract): P3's auto-added include root
    # (`resolve_inferred_header_roots`, cli_dump_helpers.py) makes a lone
    # `-H v1.h` umbrella's own parent directory project-owned, and without
    # this short-circuit the owned-slot token below would embed `v1.h`'s own
    # basename via the dir-relative component -- so a legitimate single-
    # header rename (`v1.h` -> `v2.h`, examples/case189's exact CI failure)
    # would spuriously flip `include_sequence` even though `header_sequence`
    # and the scope `headers` field both already correctly collapse to the
    # same "<single-header>" placeholder for this case. With only one
    # declared header there is nothing to disambiguate a name against
    # anyway, same reasoning as those two fields.
    if single_header_mode:
        return "hdrs:<single-header>"
    # Codex review (PR #624): pair each owned header's GLOBAL root-relative
    # identity (disambiguates the same-basename-under-two-separate-roots case
    # already fixed once -- e.g. `include/foo.h` vs. `generated/foo.h`) with
    # its identity RELATIVE TO THIS SPECIFIC include dir (disambiguates two
    # NESTED/overlapping project-owned roots, e.g. `-I work` and
    # `-I work/include`, that both own the exact same header). Global
    # identity alone is identical for both slots in the nested case -- e.g.
    # both would tokenize as `hdrs:[["foo.h", ...]]` regardless of which
    # dir owns it -- silently losing the order-sensitivity a swapped
    # `-I work -I work/include` vs. `-I work/include -I work` is supposed to
    # preserve. The dir-relative component is always safe to compute here:
    # ownership (checked by the filter below) means `inc.path` is already
    # an ancestor-or-equal of `h`, so `h.relative_to(inc.path)` never raises.
    # sorted(set(...)), not sorted(...) (Codex review, PR #624): declared_headers
    # is not itself deduplicated before reaching this function, so the same
    # header supplied twice in one CLI/manifest invocation must not retain a
    # duplicate (header_identity, relative_path) pair -- mirroring the same
    # duplicate-collapse rule scope's "headers" field and header_sequence
    # both already apply.
    owned = sorted(
        {
            (header_identities[h], str(_resolved(h).relative_to(_resolved(inc.path))))
            for h in declared_headers
            if _is_ancestor_or_equal(inc.path, h)
        }
    )
    # json.dumps, not a raw "," join (Codex review, PR #624): a header
    # identity string is not guaranteed comma-free, so an unescaped join
    # could let two structurally different owned-header sets collapse to
    # the same token — the identical class of bug reported for macro_ops.
    return "hdrs:" + json.dumps(owned)


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
    # A declared header's own parent directory is implicitly project-owned
    # even with no matching --include at all (quote-include same-directory
    # resolution) -- checked BEFORE the declared_includes longest-prefix
    # match, not after (Codex review, PR #624): a file under a declared
    # header's own parent that ALSO happens to fall under a nested, non-
    # owned --include (e.g. --header old/include/foo.h plus --include
    # old/include/sub, with foo.h quote-including sub/detail.h) would
    # otherwise get attributed to that external slot and content-hashed,
    # even though it is structurally part of the same project directory
    # tree the implicit-parent rule exists to exclude -- an ordinary
    # internal support-header edit could then spuriously raise
    # ProfileMismatchError. Attribute such a file to a synthetic
    # "owned, excluded" bucket by returning -1, distinct from "no declared
    # -I dir at all".
    for h in declared_headers:
        if _is_ancestor_or_equal(h.parent, file_path):
            return -1
    # An owned ancestor -I directory's exclusion ("every file under it,
    # named or not") wins over a deeper, non-owned nested -I directory
    # (Codex review, PR #624): with `--header old/include/foo.h --include
    # old --include old/generated`, `old` is project-owned (an ancestor of
    # the declared header) and `old/generated` is not (it isn't itself an
    # ancestor of any declared header) -- a plain longest-prefix match would
    # pick the deeper, non-owned `old/generated` slot for a file under it,
    # content-hashing it even though the broader owned `old` root already
    # claims everything beneath it. Owned matches are therefore preferred
    # outright over non-owned ones, with longest-prefix only breaking ties
    # within the same ownership class (which owned index wins is otherwise
    # immaterial: an owned slot's token never depends on per_slot_files).
    best_owned_idx: int | None = None
    best_owned_len = -1
    best_external_idx: int | None = None
    best_external_len = -1
    for idx, inc in enumerate(declared_includes):
        if _is_ancestor_or_equal(inc.path, file_path):
            depth = len(_resolved(inc.path).parts)
            if ownership[idx]:
                if depth > best_owned_len:
                    best_owned_len = depth
                    best_owned_idx = idx
            elif depth > best_external_len:
                best_external_len = depth
                best_external_idx = idx
    if best_owned_idx is not None:
        return best_owned_idx
    if best_external_idx is not None:
        return best_external_idx
    return None


def _fingerprint_from_fields(fields: dict[str, str], keys: Sequence[str]) -> str:
    """Hash *fields* over *keys* into one fingerprint — the profile/scope
    fingerprint algorithm itself, shared by both sides of
    :func:`compute_extraction_contract`. Indexes rather than ``.get``s: a field
    this function is asked to hash but never populated is a bug in the caller,
    not a value to silently substitute (:func:`_fingerprint_matches_fields`
    recomputes the same value from an already-*stored* field dict, where a
    missing key is an ordinary deserialization outcome, and does use ``.get``).
    """
    return _sha256_of(*[fields[k] for k in keys])


def _single_header_mode(
    declared_headers: Sequence[Path],
    declared_includes: Sequence[IncludeDir],
    ownership: Sequence[bool],
    header_identities: dict[Path, str],
) -> bool:
    """Whether the owned-slot token is safe to collapse to a constant
    placeholder instead of encoding the (rename-prone) declared header's own
    name.

    True only when the WHOLE declared surface is one logical header (by
    identity, not raw list length -- the same header supplied twice is still
    "one distinct header") AND there is at most one owned, unlabeled
    include-dir slot for it. Both conditions matter -- gating on single-header
    alone regressed test_13c (Codex review, PR #624 follow-up): two NESTED
    project-owned roots (``-I work`` and ``-I work/include``) that both own the
    SAME single declared header must still tokenize distinctly per slot to
    preserve order-sensitivity (which nested root search comes first is a
    genuine compile-context fact) -- collapsing both to one constant would
    silently lose that. It is only the single-owner case (P3's
    ``resolve_inferred_header_roots`` auto-adding exactly one owned root for a
    lone ``-H v1.h``/``-H old=<dir>`` umbrella, examples/case189's exact CI
    failure) where there is nothing left to disambiguate and the header's own
    name is pure rename-noise.
    """
    if len({header_identities[h] for h in declared_headers}) > 1:
        return False
    owned_unlabeled_count = sum(
        1
        for idx, inc in enumerate(declared_includes)
        if ownership[idx] and inc.label is None
    )
    return owned_unlabeled_count <= 1


def _deduplicated_resolved_paths(
    depfile_resolved_paths: Sequence[Path], generated_driver_path: Path | None
) -> list[Path]:
    """The depfile's resolved dependencies, minus the generated driver TU and
    minus repeats.

    Deduplicated by resolved identity, not just filtered (Codex review, PR
    #624): ``depfile_resolved_paths`` can realistically list the same resolved
    file more than once (e.g. concatenated per-TU depfiles, or an
    un-deduplicated depfile parse). Left un-deduped, a repeated entry is
    bucketed and hashed twice, so an otherwise identical extraction
    fingerprints differently purely because one side happens to repeat the same
    dependency entry.
    """
    seen: set[Path] = set()
    resolved_paths: list[Path] = []
    for p in depfile_resolved_paths:
        if p == generated_driver_path:
            continue
        key = _resolved(p)
        if key in seen:
            continue
        seen.add(key)
        resolved_paths.append(p)
    return resolved_paths


def _bucket_resolved_files(
    resolved_paths: Sequence[Path],
    declared_includes: Sequence[IncludeDir],
    ownership: Sequence[bool],
    declared_headers: Sequence[Path],
) -> tuple[list[list[Path]], list[Path]]:
    """Attribute each resolved dependency to the ``-I`` slot it came through.

    Returns ``(per_slot_files, system_bucket_files)`` -- the latter holding
    everything no declared slot claims. A file attributed to ``-1`` is
    implicitly project-owned via a declared header's own parent directory and
    belongs to neither bucket.
    """
    per_slot_files: list[list[Path]] = [[] for _ in declared_includes]
    system_bucket_files: list[Path] = []
    for file_path in resolved_paths:
        idx = _attribute_file(file_path, declared_includes, ownership, declared_headers)
        if idx is None:
            system_bucket_files.append(file_path)
        elif idx == -1:
            continue  # implicitly project-owned via a declared header's parent
        else:
            per_slot_files[idx].append(file_path)
    return per_slot_files, system_bucket_files


def _external_slot_token(inc: IncludeDir, files: Sequence[Path]) -> str:
    """The content token for a non-project-owned ``-I`` slot: every file reached
    through it, by its path *relative to that slot* plus its content hash."""
    pairs = sorted(
        (str(_resolved(f).relative_to(_resolved(inc.path))), _content_hash(f))
        for f in files
    )
    return "ext:" + _sha256_of(*[f"{p}={h}" for p, h in pairs])


def _system_bucket_token(files: Sequence[Path]) -> str:
    """The content token for the unattributed system/toolchain bucket.

    Content hashes only, no path component (Codex review, PR #624): unlike the
    ``ext:`` bucket, a system-bucket file has no declared :class:`IncludeDir` to
    make its path side-local against, and its raw resolved path is
    checkout/cache-root-dependent (e.g. an auto-injected sysroot under
    ``/tmp/old-sysroot/...`` vs. ``/tmp/new-sysroot/...``). Two toolchains with
    byte-identical system headers must fingerprint identically regardless of
    where those headers happen to sit on disk -- the bucket is already
    unordered/unattributed, so path identity was never load-bearing here, only
    content is.
    """
    return "sys:" + _sha256_of(*sorted(_content_hash(f) for f in files))


def _include_slot_tokens(
    declared_headers: Sequence[Path],
    declared_includes: Sequence[IncludeDir],
    ownership: Sequence[bool],
    header_identities: dict[Path, str],
    single_header_mode: bool,
    per_slot_files: Sequence[Sequence[Path]],
    system_bucket_files: Sequence[Path],
) -> list[str]:
    """One ordered token per declared ``-I`` slot, plus a trailing system-bucket
    token when anything landed there.

    A project-owned slot tokenizes by *which* declared surface it owns (never
    by its own checkout-dependent path); an external one by the content reached
    through it.
    """
    slot_tokens: list[str] = []
    for idx, inc in enumerate(declared_includes):
        if not ownership[idx]:
            token = _external_slot_token(inc, per_slot_files[idx])
        elif inc.label is not None:
            token = f"label:{inc.label}"
        else:
            token = _slot_token_for_ancestor(
                inc, declared_headers, header_identities, single_header_mode
            )
        slot_tokens.append(f"{idx}:{token}")

    if system_bucket_files:
        slot_tokens.append(_system_bucket_token(system_bucket_files))
    return slot_tokens


def _declared_header_sequence(
    declared_headers: Sequence[Path], header_identities: dict[Path, str]
) -> list[str]:
    """The declared headers' ORDER, as a profile/extraction-context fact.

    Declared-header order is a profile fact, not a scope one (Codex review, PR
    #624): the aggregate driver TU ``dumper.py`` generates includes declared
    headers sequentially in the caller's given order, so a macro/pragma side
    effect from one header can change how a LATER header in the sequence parses
    -- ``-H a.h -H b.h`` and ``-H b.h -H a.h`` can genuinely produce different
    ASTs even though the same header SET is declared either way.
    ``scope_fingerprint`` deliberately stays order-independent (the declared
    *surface* -- which headers are public -- doesn't depend on dump order; see
    the "headers" field), but ``profile_fingerprint`` must still catch a
    reordering that could change the extracted AST. Order-preserving
    de-duplication (first occurrence wins), not a sorted set, mirroring the same
    duplicate-collapse rule scope's "headers" field applies -- a header named
    twice must not itself change the sequence.

    A single declared header's own name is not load-bearing here either (Codex
    review, PR #624 follow-up — same reasoning as the scope "headers" field):
    with only one header, there is no order to disambiguate, so a rename
    (v1.h -> v2.h) must not collide with the ORDER-sensitivity this field
    exists to catch for 2+ headers.
    """
    seen: set[str] = set()
    sequence: list[str] = []
    for h in declared_headers:
        identity = header_identities[h]
        if identity not in seen:
            seen.add(identity)
            sequence.append(identity)
    if len(sequence) == 1:
        return ["<single-header>"]
    return sequence


def _compute_profile_fields(
    *,
    compiler_family: str | None,
    compiler_version: str | None,
    abi_dialect: str | None,
    language_standard: str | None,
    target_triple: str | None,
    pointer_width: int | None,
    endianness: str | None,
    macro_ops: Sequence[tuple[str, str]],
    pass_through_flags: Sequence[str | Path],
    declared_headers: Sequence[Path],
    declared_includes: Sequence[IncludeDir],
    depfile_resolved_paths: Sequence[Path],
    generated_driver_path: Path | None,
    public_header_paths: Sequence[Path],
    frontend_context_kind: str | None,
) -> dict[str, str]:
    """The ``profile_fields`` half of :func:`compute_extraction_contract` — the
    *resolved compile context* an L2 frontend actually ran under."""
    ownership = _classify_include_dirs(declared_headers, declared_includes)
    header_identities = _header_identities(declared_headers, public_header_paths)
    single_header_mode = _single_header_mode(
        declared_headers, declared_includes, ownership, header_identities
    )
    per_slot_files, system_bucket_files = _bucket_resolved_files(
        _deduplicated_resolved_paths(depfile_resolved_paths, generated_driver_path),
        declared_includes,
        ownership,
        declared_headers,
    )
    slot_tokens = _include_slot_tokens(
        declared_headers,
        declared_includes,
        ownership,
        header_identities,
        single_header_mode,
        per_slot_files,
        system_bucket_files,
    )
    header_sequence = _declared_header_sequence(declared_headers, header_identities)

    # Path-valued pass-through operands are content-hashed, never
    # hashed as their raw string form (Codex review, PR #624): a
    # forced-include flag like `-include /checkout-old/force.h` names
    # a real file, and that file's checkout-root-dependent absolute
    # path is exactly the class of noise this whole algorithm exists
    # to strip everywhere else -- old/new checkouts using
    # `/checkout-old/...` vs. `/checkout-new/...` must not
    # fingerprint differently for byte-identical forced-include
    # content. A `str` element (the flag name itself, or a non-path
    # operand) is opaque and hashed as literal text; there is no
    # principled root to normalize a forced-include path against (it
    # need not fall under any declared header or -I directory at
    # all), so it gets the same treatment as the unattributed
    # system/toolchain bucket: content only, no path.
    normalized_pass_through = [
        f"path:{_content_hash(item)}" if isinstance(item, Path) else f"str:{item}"
        for item in pass_through_flags
    ]

    return {
        "compiler_family": compiler_family or "",
        "compiler_version": compiler_version or "",
        "abi_dialect": abi_dialect or "",
        "language_standard": language_standard or "",
        "target_triple": target_triple or "",
        "pointer_width": str(pointer_width) if pointer_width is not None else "",
        "endianness": endianness or "",
        # json.dumps, not a raw "|"/":" join (Codex review, PR #624): a
        # macro value or slot token is not guaranteed pipe/colon-free —
        # macro_ops=[("D", "A|U:B")] and [("D", "A"), ("U", "B")] would
        # otherwise both serialize to the identical string "D:A|U:B",
        # letting the gate miss a real profile drift. json.dumps
        # length-delimits each element unambiguously regardless of its
        # content.
        "macro_ops": json.dumps(list(macro_ops)),
        # Ordered, not sorted (Codex review, PR #624): repeatable
        # pass-through frontend flags like `-include a.h -include b.h`
        # force preprocessing content whose ORDER can change
        # macro/pragma state before the rest of the TU is parsed --
        # `-include a.h -include b.h` and `-include b.h -include a.h`
        # can genuinely produce different ASTs even when the depfile's
        # resolved dependency SET is identical (which bucketing above
        # would otherwise report as unchanged, since bucket contents
        # are order-independent by design). This is deliberately a raw,
        # caller-supplied ordered flag list, not parsed or validated --
        # dumper.py's actual `-include`/other repeatable-flag wiring is
        # separate, not-yet-built work (see this module's docstring).
        "pass_through_flags": json.dumps(normalized_pass_through),
        "include_sequence": json.dumps(slot_tokens),
        "header_sequence": json.dumps(header_sequence),
        "frontend_context_kind": frontend_context_kind or "",
    }


def _common_root_of_parents(paths: Sequence[Path]) -> Path | None:
    """The common root of every path's *parent* directory, or ``None`` when
    there are no paths at all.

    Rooting on the parent rather than the path itself is what preserves each
    entry's own basename in the normalized identity — the same
    single-entry-preserving trick used everywhere else in this module.
    """
    candidates = [str(_resolved(p).parent) for p in paths]
    return _common_root(candidates) if candidates else None


def _normalized_identities(paths: Sequence[Path], root: Path | None) -> list[str]:
    """Side-local identities for *paths*, deduplicated and ordered.

    ``sorted(set(...))``, not ``sorted(...)`` (Codex review, PR #624): the same
    logical header reaching :func:`compute_extraction_contract` through both
    ``declared_headers`` and ``public_header_paths`` on one side (e.g. a full L2
    dump that also passes ``--public-header`` for the same file) must not retain
    a duplicate entry a side naming it only once wouldn't have --
    ``["foo.h", "foo.h"]`` vs. ``["foo.h"]`` would otherwise mismatch on element
    count alone, despite describing the identical declared surface.
    """
    return sorted({_side_local_identity(p, root) for p in paths})


def _compute_scope_fields(
    declared_headers: Sequence[Path],
    public_header_paths: Sequence[Path],
    public_header_dirs: Sequence[Path],
    manifest_tu_scope: str | None,
) -> dict[str, str]:
    """The ``scope_fields`` half of :func:`compute_extraction_contract` — the
    *declared surface* being compared, independent of how it was extracted.

    All scope-identity inputs normalize against a shared, side-local root —
    never raw absolute paths (Codex review, PR #624): a lone
    ``--public-header``/``--public-header-dir`` provenance input (the
    symbols-only-with-provenance case, no ``declared_headers`` at all) is
    exactly as checkout-root-dependent as ``declared_headers``, and hashing it
    unnormalized would make an ordinary two-checkout compare relying only on
    public-header provenance spuriously ``ScopeMismatchError``.
    ``declared_headers`` and ``public_header_paths`` are merged into one
    combined "headers" identity, not two separate ``scope_fields`` entries
    (Codex review, PR #624): both name individual public header *files* — the
    same declared surface, captured by two different mechanisms (a full L2
    header-AST dump's ``-H`` vs. a symbols-only dump's ``--public-header``
    provenance tag). Keeping them in separate fields made an ordinary depth
    difference between two dumps of the *same* header (one via each mechanism)
    fingerprint as a scope mismatch, even though nothing about the declared
    surface actually differs. ``public_header_dirs`` stays its own field — a
    directory asserts "everything under here is public," a categorically
    different claim from naming individual files, so merging it into "headers"
    would conflate the two rather than recognize genuine equivalence.

    "headers" and "public_header_dirs" normalize against SEPARATE roots, not one
    shared across both (found via real-world CI: a ``--devel-pkg``/``-H <dir>``
    umbrella whose ``declared_headers`` live several directories below the
    extracted/declared root -- e.g. ``<root>/usr/include/*.h`` and
    ``<root>/usr/share/doc/.../*.h`` next to ``<root>`` itself passed as a
    ``public_header_dir``). A single shared root, computed from every entry's
    parent including the directory entries, gets pulled up to the directory's
    *own* parent (one level above the declared root) the moment that directory
    sits shallower than the header files -- leaking that root's name (often
    per-run-random, e.g. a tempfile-extracted package) into "headers"' normalized
    identities even though "public_header_dirs" collapses its own single-entry
    case away separately. Two byte-identical extractions into two different temp
    directories then spuriously fingerprint as a scope mismatch. Each field
    already hashes independently (:data:`SCOPE_FIELD_KEYS`), so there is no
    reason for them to share a root: files use their parent for the root;
    ``public_header_dirs`` are themselves directories, so their *own* parent is
    the analogous root candidate for *that field alone*.

    A single declared header's own filename is NOT load-bearing scope identity
    (Codex review, PR #624 follow-up — CI went red at scale once real dumps
    started populating contract): renaming a project's one main header between
    versions (v1.h -> v2.h, or any other rename) is a common, legitimate
    practice, not the "manifest/CLI-flag drift" mistake this fingerprint exists
    to catch -- and with only one header declared there is nothing to
    disambiguate a name against anyway. The multi-header case still needs real
    per-file identity: two co-located headers must not collapse to the same
    token (``["a.h","b.h"]`` vs ``["a.h","c.h"]`` is a genuine declared-surface
    difference). The header's actual API surface is still verified by the
    ordinary diff engine; this only concerns whether the extraction inputs count
    as "the same declared surface" for the comparability gate. The same holds
    for a single declared public-header *directory* (same CI incident: a lone
    ``-H old=<dir>``/``-H new=<dir>`` umbrella, e.g. test_perf_binary_scan.py's
    old-include/new-include fixture dirs).
    """
    header_identities = _normalized_identities(
        (*declared_headers, *public_header_paths),
        _common_root_of_parents((*declared_headers, *public_header_paths)),
    )
    if len(header_identities) == 1:
        header_identities = ["<single-header>"]

    dir_identities = _normalized_identities(
        public_header_dirs, _common_root_of_parents(public_header_dirs)
    )
    if len(dir_identities) == 1:
        dir_identities = ["<single-header-dir>"]

    # json.dumps, not a raw "|" join (Codex review, PR #624, same class
    # of bug already fixed for macro_ops/include_sequence above): a
    # normalized path is not guaranteed pipe-free.
    return {
        "headers": json.dumps(header_identities),
        "public_header_dirs": json.dumps(dir_identities),
        # Present in scope_fields (so it's visible for reporting/
        # debugging either way) but deliberately NOT always folded into
        # scope_fingerprint itself -- see the manifest_tu_scope branch
        # in compute_extraction_contract (Codex review, PR #636).
        "translation_units": manifest_tu_scope or "[]",
    }

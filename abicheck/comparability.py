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

**Scope of this module today (ADR-050 Phase A — see
``docs/contribute/plans/g32-comparability-contract-and-multi-tu-manifest.md``
Phase A): the fingerprint algorithm and the gate are real, and both are wired
into real production dumps, not just ``checker.compare``'s call site.**
``dumper.py`` calls :func:`compute_extraction_contract` unconditionally on
every dump and attaches whatever it returns, so a freshly-produced snapshot
with fingerprintable extraction inputs (an L2 header/AST frontend ran, or
public-header provenance was given) now carries a real ``contract`` — not
``contract=None`` — and the gate is live for anyone comparing two such
dumps. A dump with neither (plain binary/symbols-only, no headers) still
gets ``contract=None``, exactly as documented on
:func:`compute_extraction_contract` itself ("nothing to fingerprint at
all"). :func:`check_contracts_comparable` is reachable — and has its own
exception→outcome handling — from all seven ADR-050 D2 entry points: the
native ``compare`` CLI command (``cli_compare_helpers.run_compare`` — a
``--diagnostic-comparison`` flag, a schema-conformant ``verdict: null`` JSON
report, exit ``16``), ``cli_compare_release.py``'s directory/package fan-out
(a per-library ``"not_comparable"`` verdict string, dominating the release
rollup, exit ``16``), ``compat/cli.py``'s ``compat check`` (exit ``9``),
``cli_scan.py``'s ``scan --against`` via ``scan_engine.run_scan_core`` (a
``NOT_COMPARABLE`` verdict, exit ``6``), ``stack_checker.py``'s
``deps compare`` (``StackChange.not_comparable_reason``, exit ``5``), the
``abi_compare`` MCP tool (a dedicated ``{"status": "not_comparable", ...}``
envelope, plus its own ``diagnostic_comparison`` parameter), and
``service.py``'s ``CompareRequest``/``run_compare_request``/legacy
``run_compare`` keyword shim (threads ``diagnostic_comparison``; a raised
mismatch propagates to whichever of the above front-ends called it — no
handling of its own, since it isn't itself a front-end). ``appcompat.py``'s
``check_appcompat``/``check_plugin_host_contract`` are deliberately *not*
among these seven — raw propagation is their documented default contract.
Not yet wired in — tracked as explicit follow-up work, not silently
dropped scope:

- The legacy-CLI labeled ``--include old:LABEL=PATH``/``new:LABEL=PATH``
  grammar (``cli_params.SidedIncludePathParam``) is wired end-to-end for the
  native ``compare`` command only: its ``--include`` option resolves a
  ``label`` per entry, ``cli_options.split_sided_include_paths`` collects a
  ``path -> label`` map, and it threads through
  ``run_compare``/``_resolve_compare_snapshots``/``service.resolve_input``/
  ``run_dump``/``dumper.dump()`` into :class:`IncludeDir`'s ``label`` field.
  ``scan --against``'s own separate inline ``--include`` registration and
  ``dump``'s single-input ``--include`` (which needs the narrower
  ``both:LABEL=PATH``-only ``cli_params.LabeledIncludePathParam``, already
  built but not yet wired into ``dump_cmd``) do not thread a label yet — a
  labeled entry there is parsed as an ordinary unlabeled path, silently.
  ``compare``'s directory/package (release) fan-out rejects a labeled
  ``--include`` outright (see below) rather than silently dropping it.
- ``sarif.py``/``junit_report.py`` now render a not_comparable outcome as a
  real, spec-conformant document of their own — a failed-invocation SARIF
  run (``executionSuccessful: False`` + a ``toolExecutionNotification``,
  never a synthetic finding-shaped ``result``) via
  :func:`sarif.to_sarif_not_comparable`, and an errored JUnit testcase via
  :func:`junit_report.to_junit_xml_not_comparable` — wired into native
  ``compare``'s ``_report_not_comparable`` for ``--format sarif``/``junit``,
  and into ``compare-release``'s own ``_format_release_junit`` (a
  ``"not_comparable"`` per-library entry was previously excluded from
  ``error_libs`` entirely, silently producing zero testsuites for it).
  ``html_report.py``/``action/run.sh`` are not part of this module either —
  ``markdown``/``html``/``review`` still get only the clear stderr message
  (see ``_report_not_comparable``'s own docstring for why: those are
  human-facing formats already reading that stderr output, with no
  equivalent "run failed" document convention worth fabricating one for).
  ``reporter.py``'s JSON output (``contract_coverage``/``assurance`` on an
  ordinary completed diff, plus the ``verdict: null`` document on a
  hard-fail) is unaffected — already covered. ``aggregate.py`` (the
  multi-target CI fan-in gate) *is* wired: ``_load_report_file`` special-cases
  a real ``verdict: null`` + structured ``reason`` (schema 2.17) the same way
  it already special-cased a compare-release operational-error report — a
  synthetic blocking ``BREAKING``/exit-4 ``GateInfo`` with
  ``blocking_categories=("not_comparable",)`` and the reason preserved on
  :class:`TargetReport`, so it folds into ``exit_code()`` unconditionally
  (including in ``discovered_only`` mode, which has no coverage axis at all)
  instead of silently decaying into the same "unavailable" bucket a report
  that simply never arrived gets.
  (``snapshot_cache.py``'s cache-key order-sensitivity — headers/includes
  order is real, load-bearing input, the same rule ``profile_fingerprint``
  below already enforces — is fixed, cache version ``4``.)
- ``cli_compare_release.py``'s fan-out does not accept
  ``--diagnostic-comparison`` at all (rejected up front by
  ``_reject_set_input_flags``, alongside every other single-pair-only flag)
  — it only closes the *default hard-fail* half of D2 (a mismatch reported
  cleanly instead of crashing), not the escape-hatch half.

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
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .comparability_json import _SCOPE_SINGLE_ENTRY_SENTINELS, _json_load_str_list
from .comparability_sequences import (
    _HEADER_SEQUENCE_FIELDS,
    _INCLUDE_SEQUENCE_FIELDS,
    _header_sequence_is_additive_reorder_free,
    _include_sequence_is_additive_owned_growth,
    _scope_newly_added_headers,
)
from .errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
from .model import AbiSnapshot, ExtractionContract

# A sentinel distinct from every valid profile_fields/scope_fields value
# (Codex review, PR #641 follow-up, fifth P1) -- used as the `.get()`
# fallback when checking whether an unrecognized field key differs between
# two contracts. Using `""` as that fallback (as every other comparison in
# this module does, where an absent recognized field really does mean "no
# value") conflates "key absent entirely" with "key present with an empty
# string value": a newer-schema field added on only one side with an empty
# value (`{"future_scope": ""}` vs. no key at all) compares `"" == ""` and
# is wrongly invisible to the unknown-field checks below, even though the
# key's very presence is exactly the kind of schema drift they exist to
# catch.
_FIELD_ABSENT = object()

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
    "pass_through_flags",
    "include_sequence",
    "header_sequence",
)
#: Appended to PROFILE_FIELD_KEYS only when a dump actually went through a
#: DPC++-capable frontend (ADR-050 D5, G32 Phase D) -- never for an ordinary
#: clang/castxml dump, so every pre-Phase-D profile_fingerprint stays
#: byte-for-byte unchanged (the identical "don't break legacy fingerprints"
#: discipline manifest_tu_scope's own SCOPE_FIELD_KEYS/_MANIFEST_SCOPE_FIELD_KEYS
#: split already established).
_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS = (*PROFILE_FIELD_KEYS, "frontend_context_kind")
SCOPE_FIELD_KEYS = (
    "headers",
    "public_header_dirs",
)
#: Appended to SCOPE_FIELD_KEYS for a manifest-driven scope_fingerprint only
#: (never the legacy path) -- see compute_extraction_contract's own note on
#: why this can't simply join SCOPE_FIELD_KEYS unconditionally.
_MANIFEST_SCOPE_FIELD_KEYS = (*SCOPE_FIELD_KEYS, "translation_units")

# The only profile_fields keys the platform-identity carve-out (ADR-050
# Phase A) is allowed to treat as non-fatal, and only when the snapshots'
# own binary-derived platform metadata confirms a genuine architecture
# difference on that same axis (see check_contracts_comparable).
_PLATFORM_IDENTITY_FIELDS = frozenset({"target_triple", "pointer_width", "endianness"})

# The only profile_fields keys the build-context carve-out (PR #624
# follow-up: examples/case98_cxx_standard_floor_raised's real CI failure)
# is allowed to treat as non-fatal, and only when both snapshots were
# actually parsed against real build-system evidence (see
# check_contracts_comparable / _build_context_corroborated below) -- a
# raised C++-standard floor or a build-derived macro delta between two
# genuine build configurations is exactly the fact
# CXX_STANDARD_FLOOR_RAISED/ABI_RELEVANT_BUILD_FLAG_CHANGED exist to
# surface as a RISK finding, not a reason to refuse a verdict outright.
_BUILD_CONTEXT_FIELDS = frozenset({"language_standard", "macro_ops"})


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


def manifest_tu_scope_field(dump_manifest: Any) -> str:
    """JSON-serializable, order-preserving encoding of every ``--dump-manifest``
    translation unit's own scope-affecting fields (ADR-050 D1: name, ordered
    ``forced_includes``/``includes`` including ``project_owned``, ``required``,
    ``contributes_to_abi``) -- fed into :func:`compute_extraction_contract`'s
    ``scope_fingerprint`` as its ``"translation_units"`` field whenever a
    manifest-driven dump is being fingerprinted.

    Without this, two manifests declaring the identical ``roots`` (the only
    scope input ``compute_extraction_contract`` otherwise sees for a
    manifest-driven dump) but different TU structure -- a TU renamed, its
    ``includes``/``forced_includes`` reordered, or a ``required``/
    ``contributes_to_abi``/``project_owned`` flag flipped -- would silently
    fingerprint identically: exactly the class of manifest/extraction-contract
    drift ADR-050 exists to catch (D1's own text: "flipping ``contributes_to_abi``
    changes which declarations feed the ABI model... without necessarily
    changing that TU's includes at all").

    Every path is normalized relative to *dump_manifest*'s own ``base_dir``
    (the manifest file's own directory) -- ADR-050's "for the manifest path,
    both fingerprints' roots are simply the manifest file's own directory"
    rule, mirroring ``dumper_contract._manifest_declared_includes``'s
    identical ``os.path.relpath``/``ValueError`` handling (falls back to the
    resolved absolute path only for a genuinely cross-drive Windows path) so
    two checkouts of the same manifest at different mount points still
    normalize identically.

    Computable from the manifest document alone, no compiler invocation --
    genuinely available pre-dump, which is what lets
    :mod:`abicheck.service_dump_cache` (G32 Phase E) fold it into the
    whole-snapshot cache key *before* running a live dump.

    Typed ``Any`` rather than ``dump_manifest.DumpManifest`` to avoid a
    module-level import of :mod:`abicheck.dump_manifest` here purely for a
    type hint (this module has no other reason to depend on it); only
    ``.base_dir``/``.translation_units`` are read, structurally.
    """
    # The literal, UN-resolved base_dir string -- dump_manifest.py's own
    # _resolve_path() builds every relative-in-YAML path as exactly
    # `base_dir / raw`, so its str() always carries this exact prefix
    # before any ".." components get collapsed by .resolve() below. This is
    # what lets _rel tell "a relative-declared sibling path (../src)" apart
    # from "a genuinely external absolute path (/usr/include)" -- checking
    # AFTER resolving can't: .resolve() collapses `manifest_dir/../src`
    # down to the same shape as an unrelated absolute path, discarding the
    # one signal that distinguishes them (Codex review, PR #636).
    _base_dir_str = str(dump_manifest.base_dir)
    # A base_dir that already ends in a separator (a filesystem root "/" or
    # a Windows drive root "C:\") must not gain a second one here -- "//" or
    # "C:\\" would never prefix-match any real child path string, so every
    # path under such a root-level checkout would be misclassified as
    # "external" and fingerprinted as an absolute, checkout-depth-dependent
    # path instead of being properly relativized (CodeRabbit review).
    _base_dir_prefix = (
        _base_dir_str if _base_dir_str.endswith(os.sep) else _base_dir_str + os.sep
    )

    def _rel(p: Path) -> str:
        p_str = str(p)
        if p_str != _base_dir_str and not p_str.startswith(_base_dir_prefix):
            # A genuinely external path (declared absolute in the manifest,
            # e.g. `/usr/include`, and not already under this checkout) has
            # no structural relationship to base_dir at all -- relativizing
            # it would climb a `../` distance that depends on how deeply
            # THIS checkout happens to be nested, not on anything about the
            # external path itself, so two otherwise-identical manifests at
            # different checkout depths would spuriously fingerprint
            # differently. Keep it as the resolved absolute path instead,
            # which is already checkout-depth-independent by construction.
            return str(_resolved(p))
        # Lexical normalization only (os.path.normpath), never real
        # filesystem resolution: a relative-declared path whose lexical
        # structure crosses a symlink (e.g. a checkout-local `vendor ->
        # /opt/sdk`, or that symlink's real target being relocated/
        # versioned per checkout) must not have the symlink followed
        # before computing the relative form -- two checkouts declaring
        # the identical `vendor/api.h` must fingerprint identically
        # regardless of what `vendor` actually resolves to on either
        # host. `.resolve()` follows symlinks as well as collapsing
        # `..`/`.`; `os.path.normpath` collapses `..`/`.` purely
        # lexically, with no filesystem access at all (Codex review).
        try:
            return os.path.relpath(
                os.path.normpath(p_str), os.path.normpath(_base_dir_str)
            )
        except ValueError:
            return str(_resolved(p))

    tus = [
        {
            "name": tu.name,
            "forced_includes": [_rel(p) for p in tu.forced_includes],
            "includes": [
                {"path": _rel(inc.path), "project_owned": inc.project_owned}
                for inc in tu.includes
            ],
            "required": tu.required,
            "contributes_to_abi": tu.contributes_to_abi,
        }
        for tu in dump_manifest.translation_units
    ]
    # Sorted by name -- the manifest declares a SET of translation units,
    # identified by their (parse-time-enforced-unique) name, not by list
    # position (ADR-050 D1: "the set of translation units (by name, not by
    # list position)... reordering two independent TU entries... must not
    # change the fingerprint"). Each TU's own internal forced_includes/
    # includes order is preserved above and stays order-sensitive -- only
    # the outer TU-to-TU ordering is canonicalized (Codex review, PR #636).
    tus.sort(key=lambda t: t["name"])
    return json.dumps(tus)


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
    pass_through_flags: Sequence[str | Path] = (),
    declared_headers: Sequence[Path] = (),
    declared_includes: Sequence[IncludeDir] = (),
    depfile_resolved_paths: Sequence[Path] = (),
    generated_driver_path: Path | None = None,
    l2_frontend_ran: bool = False,
    public_header_paths: Sequence[Path] = (),
    public_header_dirs: Sequence[Path] = (),
    manifest_tu_scope: str | None = None,
    frontend_context_kind: str | None = None,
) -> ExtractionContract | None:
    """Compute one side's :class:`ExtractionContract`, for either the legacy
    non-manifest CLI path or a ``--dump-manifest`` (ADR-050 D1/D3).

    All inputs are already-resolved data ``dumper.py`` hands this function
    after running the actual castxml/clang invocation and parsing its
    ``-MD`` depfile; this function itself never shells out or re-parses
    anything.

    *manifest_tu_scope* is the pre-serialized :func:`manifest_tu_scope_field`
    string for a manifest-driven dump (``None`` for the legacy path) --
    passed in already-computed, rather than a raw ``DumpManifest``, so this
    function stays generic over its scope inputs the same way it already is
    over *declared_headers*/*declared_includes* (a manifest caller builds
    *declared_headers* from ``dump_manifest.roots`` the same way it always
    did; this parameter only adds the per-TU structure a flat header list
    can't express).

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

    ``pass_through_flags`` is an ordered list of repeatable frontend flags
    with ABI-relevant preprocessing order (e.g. ``-include a.h -include
    b.h``) hashed *in the given order*, unlike the sorted/unordered depfile
    buckets below (Codex review, PR #624): such a flag forces preprocessing
    content whose order can change macro/pragma state even when the
    resolved dependency *set* the depfile reports is identical between two
    extraction runs. Each element is either a bare ``str`` (opaque flag
    text, hashed as literal content) or a ``Path`` naming a real file the
    flag references (e.g. a forced-include target) — a ``Path`` element is
    content-hashed, never hashed as its raw string form, since its
    checkout-root-dependent absolute path (Codex review, PR #624) is
    exactly the class of noise this whole algorithm strips everywhere
    else. This function does not otherwise parse or validate the flags —
    the CLI/manifest glue that would collect them (and classify which
    operands are paths) from a real invocation is separate, not-yet-built
    work.

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
      unordered **system/toolchain bucket**, appended last. Its content is
      the sorted set of content hashes alone (no path component): unlike an
      external slot, a system-bucket file has no declared ``-I`` directory
      to make its path side-local against, so including its raw resolved
      path would make the fingerprint checkout/cache-root-dependent.
    """
    scope_inputs_present = bool(
        declared_headers or public_header_paths or public_header_dirs
    )
    # Gated on l2_frontend_ran alone (Codex review, PR #624), not on whether
    # any L2-shaped keyword argument happens to be non-empty: the profile
    # block below only ever runs `if l2_frontend_ran:`, so a caller that
    # passes e.g. declared_includes/macro_ops without also setting
    # l2_frontend_ran=True (no L2 invocation actually ran) must not make
    # this function return a non-None "empty shell" ExtractionContract whose
    # profile_fingerprint AND scope_fingerprint are both None — checker.py's
    # contract_coverage logic keys off whether `contract is None` at all, so
    # such a shell would misreport as full contract coverage.
    if not l2_frontend_ran and not scope_inputs_present:
        return None

    profile_fingerprint: str | None = None
    profile_fields: dict[str, str] = {}
    if l2_frontend_ran:
        ownership = _classify_include_dirs(declared_headers, declared_includes)
        header_identities = _header_identities(declared_headers, public_header_paths)
        # Whether the owned-slot token below is safe to collapse to a
        # constant placeholder instead of encoding the (rename-prone)
        # declared header's own name: true only when the WHOLE declared
        # surface is one logical header (by identity, not raw list length --
        # the same header supplied twice is still "one distinct header") AND
        # there is at most one owned, unlabeled include-dir slot for it.
        # Both conditions matter -- gating on single-header alone regressed
        # test_13c (Codex review, PR #624 follow-up): two NESTED project-
        # owned roots (`-I work` and `-I work/include`) that both own the
        # SAME single declared header must still tokenize distinctly per
        # slot to preserve order-sensitivity (which nested root search comes
        # first is a genuine compile-context fact) -- collapsing both to one
        # constant would silently lose that. It is only the single-owner
        # case (P3's `resolve_inferred_header_roots` auto-adding exactly one
        # owned root for a lone `-H v1.h`/`-H old=<dir>` umbrella, examples/
        # case189's exact CI failure) where there is nothing left to
        # disambiguate and the header's own name is pure rename-noise.
        single_header_mode = len({header_identities[h] for h in declared_headers}) <= 1
        if single_header_mode:
            _owned_unlabeled_count = sum(
                1
                for idx, inc in enumerate(declared_includes)
                if ownership[idx] and inc.label is None
            )
            single_header_mode = _owned_unlabeled_count <= 1

        # Deduplicated by resolved identity, not just filtered (Codex
        # review, PR #624): depfile_resolved_paths can realistically list
        # the same resolved file more than once (e.g. concatenated per-TU
        # depfiles, or an un-deduplicated depfile parse). Left un-deduped,
        # a repeated entry is bucketed and hashed twice, so an otherwise
        # identical extraction fingerprints differently purely because one
        # side happens to repeat the same dependency entry.
        seen_resolved: set[Path] = set()
        resolved_paths: list[Path] = []
        for p in depfile_resolved_paths:
            if p == generated_driver_path:
                continue
            key = _resolved(p)
            if key in seen_resolved:
                continue
            seen_resolved.add(key)
            resolved_paths.append(p)
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
                    token = _slot_token_for_ancestor(
                        inc, declared_headers, header_identities, single_header_mode
                    )
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
            # Content hashes only, no path component (Codex review, PR #624):
            # unlike the "ext:" bucket, a system-bucket file has no declared
            # IncludeDir to make its path side-local against, and its raw
            # resolved path is checkout/cache-root-dependent (e.g. an
            # auto-injected sysroot under /tmp/old-sysroot/... vs.
            # /tmp/new-sysroot/...). Two toolchains with byte-identical
            # system headers must fingerprint identically regardless of
            # where those headers happen to sit on disk -- the bucket is
            # already unordered/unattributed, so path identity was never
            # load-bearing here, only content is.
            sys_hashes = sorted(_content_hash(f) for f in system_bucket_files)
            slot_tokens.append("sys:" + _sha256_of(*sys_hashes))

        # Declared-header ORDER is a profile/extraction-context fact, not a
        # scope one (Codex review, PR #624): the aggregate driver TU
        # dumper.py generates includes declared headers sequentially in the
        # caller's given order, so a macro/pragma side effect from one
        # header can change how a LATER header in the sequence parses --
        # `-H a.h -H b.h` and `-H b.h -H a.h` can genuinely produce
        # different ASTs even though the same header SET is declared either
        # way. scope_fingerprint deliberately stays order-independent (the
        # declared *surface* -- which headers are public -- doesn't depend
        # on dump order; see the "headers" field above), but
        # profile_fingerprint must still catch a reordering that could
        # change the extracted AST. Order-preserving de-duplication (first
        # occurrence wins), not a sorted set, mirroring the same
        # duplicate-collapse rule scope's "headers" field applies -- a
        # header named twice must not itself change the sequence.
        seen_header_identities: set[str] = set()
        header_sequence: list[str] = []
        for h in declared_headers:
            identity = header_identities[h]
            if identity not in seen_header_identities:
                seen_header_identities.add(identity)
                header_sequence.append(identity)

        # A single declared header's own name is not load-bearing here
        # either (Codex review, PR #624 follow-up — same reasoning as the
        # scope "headers" field above): with only one header, there is no
        # order to disambiguate, so a rename (v1.h -> v2.h) must not
        # collide with the ORDER-sensitivity this field exists to catch
        # for 2+ headers.
        if len(header_sequence) == 1:
            header_sequence = ["<single-header>"]

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

        profile_fields = {
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
        _profile_fingerprint_keys = (
            _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS
            if frontend_context_kind is not None
            else PROFILE_FIELD_KEYS
        )
        profile_fingerprint = _sha256_of(
            *[profile_fields[k] for k in _profile_fingerprint_keys]
        )

    scope_fingerprint: str | None = None
    scope_fields: dict[str, str] = {}
    if scope_inputs_present:
        # All scope-identity inputs normalize against a shared, side-local
        # root — never raw absolute paths (Codex review, PR #624): a lone
        # `--public-header`/`--public-header-dir` provenance input (the
        # symbols-only-with-provenance case, no declared_headers at all) is
        # exactly as checkout-root-dependent as declared_headers, and
        # hashing it unnormalized would make an ordinary two-checkout
        # compare relying only on public-header provenance spuriously
        # ScopeMismatchError. declared_headers and public_header_paths are
        # merged into one combined "headers" identity, not two separate
        # scope_fields entries (Codex review, PR #624): both name individual
        # public header *files* — the same declared surface, captured by two
        # different mechanisms (a full L2 header-AST dump's `-H` vs. a
        # symbols-only dump's `--public-header` provenance tag). Keeping
        # them in separate fields made an ordinary depth difference between
        # two dumps of the *same* header (one via each mechanism) fingerprint
        # as a scope mismatch, even though nothing about the declared
        # surface actually differs. public_header_dirs stays its own field —
        # a directory asserts "everything under here is public," a
        # categorically different claim from naming individual files, so
        # merging it into "headers" would conflate the two rather than
        # recognize genuine equivalence.
        #
        # "headers" and "public_header_dirs" normalize against SEPARATE
        # roots, not one shared across both (found via real-world CI: a
        # `--devel-pkg`/`-H <dir>` umbrella whose declared_headers live
        # several directories below the extracted/declared root -- e.g.
        # `<root>/usr/include/*.h` and `<root>/usr/share/doc/.../*.h` next
        # to `<root>` itself passed as a public_header_dir). A single shared
        # root, computed from every entry's parent including the directory
        # entries, gets pulled up to the directory's *own* parent (one level
        # above the declared root) the moment that directory sits shallower
        # than the header files -- leaking that root's name (often
        # per-run-random, e.g. a tempfile-extracted package) into "headers"'
        # normalized identities even though "public_header_dirs" collapses
        # its own single-entry case away separately. Two byte-identical
        # extractions into two different temp directories then spuriously
        # fingerprint as a scope mismatch. Each field already hashes
        # independently (SCOPE_FIELD_KEYS), so there is no reason for them
        # to share a root: files use their parent for the root (preserving
        # the basename, the same single-entry-preserving trick used
        # everywhere else in this function); public_header_dirs are
        # themselves directories, so their *own* parent is the analogous
        # root candidate for *that field alone* (preserving the directory's
        # own basename the same way a lone header's basename survives).
        headers_root_candidates = [
            str(_resolved(p).parent) for p in (*declared_headers, *public_header_paths)
        ]
        headers_root = (
            _common_root(headers_root_candidates) if headers_root_candidates else None
        )
        header_dirs_root_candidates = [
            str(_resolved(p).parent) for p in public_header_dirs
        ]
        header_dirs_root = (
            _common_root(header_dirs_root_candidates)
            if header_dirs_root_candidates
            else None
        )

        def _normalize(paths: Sequence[Path], root: Path | None) -> list[str]:
            # sorted(set(...)), not sorted(...) (Codex review, PR #624): the
            # same logical header reaching this function through both
            # declared_headers and public_header_paths on one side (e.g. a
            # full L2 dump that also passes --public-header for the same
            # file) must not retain a duplicate entry a side naming it only
            # once wouldn't have -- ["foo.h", "foo.h"] vs. ["foo.h"] would
            # otherwise mismatch on element count alone, despite describing
            # the identical declared surface.
            return sorted({_side_local_identity(p, root) for p in paths})

        # A single declared header's own filename is NOT load-bearing scope
        # identity (Codex review, PR #624 follow-up — CI went red at scale
        # once real dumps started populating contract): renaming a
        # project's one main header between versions (v1.h -> v2.h, or any
        # other rename) is a common, legitimate practice, not the
        # "manifest/CLI-flag drift" mistake this fingerprint exists to
        # catch -- and with only one header declared there is nothing to
        # disambiguate a name against anyway. The multi-header case below
        # still needs real per-file identity: two co-located headers must
        # not collapse to the same token (["a.h","b.h"] vs ["a.h","c.h"]
        # is a genuine declared-surface difference). The header's actual
        # API surface is still verified by the ordinary diff engine; this
        # only concerns whether the extraction inputs count as "the same
        # declared surface" for the comparability gate.
        _scope_header_identities = _normalize(
            (*declared_headers, *public_header_paths), headers_root
        )
        if len(_scope_header_identities) == 1:
            _scope_header_identities = ["<single-header>"]

        # A single declared public-header directory's own name is not
        # load-bearing scope identity either (Codex review, PR #624
        # follow-up -- same reasoning as "headers" above, same CI incident:
        # a lone `-H old=<dir>`/`-H new=<dir>` umbrella, e.g.
        # test_perf_binary_scan.py's old-include/new-include fixture dirs,
        # is exactly as legitimate a rename as a single header file, and
        # with only one directory declared there is nothing to disambiguate
        # a name against anyway. Two co-located declared dirs still need
        # real per-directory identity (a genuine declared-surface
        # difference), so this only collapses the single-entry case.
        _scope_public_header_dirs = _normalize(public_header_dirs, header_dirs_root)
        if len(_scope_public_header_dirs) == 1:
            _scope_public_header_dirs = ["<single-header-dir>"]

        # json.dumps, not a raw "|" join (Codex review, PR #624, same class
        # of bug already fixed for macro_ops/include_sequence above): a
        # normalized path is not guaranteed pipe-free.
        scope_fields = {
            "headers": json.dumps(_scope_header_identities),
            "public_header_dirs": json.dumps(_scope_public_header_dirs),
            # Present in scope_fields (so it's visible for reporting/
            # debugging either way) but deliberately NOT always folded into
            # scope_fingerprint itself -- see the manifest_tu_scope branch
            # below (Codex review, PR #636).
            "translation_units": manifest_tu_scope or "[]",
        }
        # A non-manifest (legacy) dump's scope_fingerprint is computed from
        # exactly the same field set as before this ADR's D6/G32-Phase-E
        # translation_units addition -- SCOPE_FIELD_KEYS alone, never
        # _MANIFEST_SCOPE_FIELD_KEYS (Codex review, PR #636). A persisted,
        # pre-upgrade `.abi.json` baseline's contract.scope_fingerprint is a
        # bare hash string frozen at dump time (serialization.py never
        # recomputes it on load); an abicheck upgrade that folded a new
        # constant field into every legacy fingerprint would change what a
        # *freshly* dumped snapshot of the identical header set hashes to,
        # without changing the old persisted baseline's already-stored
        # value -- spuriously tripping ScopeMismatchError on the single most
        # common workflow (compare a committed/CI-cached baseline against a
        # fresh dump), a regression this ADR exists to prevent, not cause.
        # A manifest-driven fingerprint has no such installed base to
        # protect: the manifest path's own scope_fingerprint algorithm was
        # incomplete -- missing exactly this TU-level data -- until this
        # same change, so there is no correctly-comparable prior value a
        # manifest baseline could have been relying on.
        _fingerprint_keys = (
            _MANIFEST_SCOPE_FIELD_KEYS
            if manifest_tu_scope is not None
            else SCOPE_FIELD_KEYS
        )
        scope_fingerprint = _sha256_of(*[scope_fields[k] for k in _fingerprint_keys])

    return ExtractionContract(
        profile_fingerprint=profile_fingerprint,
        scope_fingerprint=scope_fingerprint,
        profile_fields=profile_fields,
        scope_fields=scope_fields,
    )


def _binary_platform_components(snap: AbiSnapshot) -> dict[str, str] | None:
    """Read the same binary-header platform-identity fields
    ``elf_machine_changed``/``elf_class_changed``/``elf_endianness_changed``
    (and PE/Mach-O equivalents) already detect directly, keyed by the
    :data:`_PLATFORM_IDENTITY_FIELDS` name each one corresponds to — so the
    carve-out below can confirm each *specific* differing profile field maps
    to a genuine difference in its own corresponding binary component, not
    merely that some unspecified component of the platform identity changed
    (Codex review, PR #624: comparing whole axis tuples let a bogus
    ``pointer_width`` extraction hide behind an unrelated, genuine
    ``machine``/architecture change on a different field). Returns ``None``
    when no binary-derived platform metadata is available at all.

    Only ELF exposes a word-size (``elf_class``) and endianness (``ei_data``)
    field distinct from ``machine`` — PE/Mach-O metadata has no equivalent,
    so a PE/Mach-O snapshot's dict only ever contains ``target_triple``.
    """
    if snap.elf is not None:
        elf_machine = getattr(snap.elf, "machine", "")
        if elf_machine:
            return {
                "target_triple": elf_machine,
                "endianness": getattr(snap.elf, "ei_data", ""),
                "pointer_width": str(getattr(snap.elf, "elf_class", "")),
            }
    if snap.pe is not None:
        pe_machine = getattr(snap.pe, "machine", "")
        if pe_machine:
            return {"target_triple": pe_machine}
    if snap.macho is not None:
        macho_cpu_type = getattr(snap.macho, "cpu_type", "")
        if macho_cpu_type:
            return {"target_triple": macho_cpu_type}
    return None


def _scope_field_is_additive_superset(
    old_value: str | None, new_value: str | None
) -> bool:
    """Whether *new_value* (one ``scope_fields[...]`` json-encoded sorted
    identity list -- ``"headers"`` or ``"public_header_dirs"``) declares
    everything *old_value* did, plus possibly more (PR #641 follow-up, the
    pvxs full-version-matrix scan's F8: a released library adding one new
    public header between versions -- e.g. epics-base/pvxs's
    ``include/pvxs/json.h`` landing on ``master`` with no other header
    added/removed/renamed -- is ordinary, common evolution, not the
    "manifest/CLI-flag drift between two extraction runs" mistake this
    fingerprint exists to catch, yet both symptoms fingerprint identically:
    a differing declared-file set).

    An unchanged field (``old_value == new_value``) is always ``True``, even
    when both sides are the single-entry sentinel (Codex review, PR #641
    follow-up): the caller checks *every* :data:`SCOPE_FIELD_KEYS` field,
    not only the ones that actually differ (unlike the profile-fingerprint
    carve-outs, which pre-filter to a ``differing`` set) -- the real F8
    scenario declares headers via a single ``-H old=<dir> -H new=<dir>``
    each side, so ``public_header_dirs`` collapses to the identical
    ``"<single-header-dir>"`` sentinel on *both* sides even though old and
    new point at different physical directories. Declining on the sentinel
    unconditionally, before checking for equality, wrongly hard-failed a
    field that never actually changed at all, before this carve-out could
    ever reach a genuinely differing field like ``headers``.

    Otherwise declines (returns False) whenever either side is ``None`` or
    collapsed to a single-entry sentinel (:data:`_SCOPE_SINGLE_ENTRY_SENTINELS`)
    with a genuinely *different* value on the other side -- with no real
    per-file/per-dir identity to compare, "new looks like it might be
    bigger" can't be told apart from "the one entry was simultaneously
    renamed AND something else added", so there is nothing here to safely
    verify a true superset against; the existing hard-fail is the correct,
    conservative answer for that case, same as it always was.

    Also declines whenever *old_list* or *new_list* contains a duplicate
    identity (Codex review, PR #641 follow-up, fourteenth P2):
    ``compute_extraction_contract`` always emits a sorted, deduplicated
    identity list for these fields, so a duplicate is never genuine
    evidence -- e.g. growing ``["a.h", "b.h"]`` to ``["a.h", "b.h", "c.h",
    "c.h"]`` is malformed. The final ``set(new_list) >= set(old_list)``
    comparison would otherwise collapse that duplicate away silently and
    still authorize the carve-out, the same gap already closed for
    ``header_sequence`` and the owned-header pair lists.
    """
    if old_value is None or new_value is None:
        return False
    if old_value == new_value:
        return True
    old_list = _json_load_str_list(old_value)
    new_list = _json_load_str_list(new_value)
    if old_list is None or new_list is None:
        return False
    if len(old_list) != len(set(old_list)) or len(new_list) != len(set(new_list)):
        return False
    if len(old_list) == 1 and old_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return False
    if len(new_list) == 1 and new_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return False
    return set(new_list) >= set(old_list)


def _scope_growth_corroborated(
    old_contract: ExtractionContract, new_contract: ExtractionContract
) -> bool:
    """Whether the scope-level declared-surface check independently confirms
    a genuine additive header-set change between *old_contract* and
    *new_contract* — i.e. ``scope_fingerprint`` actually differs AND every
    :data:`SCOPE_FIELD_KEYS` field is a verified superset growth (see
    :func:`_scope_field_is_additive_superset`).

    The header/include-sequence carve-outs below must not accept a
    profile-level "sequence grew" shape on its own (Codex review, PR #641
    follow-up): ``scope_fields["headers"]`` treats a file reaching it via
    ``declared_headers`` (fed to the L2 frontend via ``-H``) and via
    ``public_header_paths`` (bare ``--public-header`` provenance, never
    actually parsed) as the *same* declared-surface membership — see
    :func:`compute_extraction_contract`'s docstring — so a header already
    declared identically on both sides via ``--public-header``, but fed to
    the L2 frontend only on the new side, leaves ``scope_fingerprint``
    completely UNCHANGED while ``profile_fields["header_sequence"]`` still
    grows additively, purely from which mechanism happened to feed the
    parser. That old snapshot then has NO parsed AST content for that header
    at all, so a real removal made inside it between old and new is
    invisible rather than reported — a silent false negative, not merely
    extra noise. Requiring an independently-verified, genuinely differing
    scope-level growth corroborates that the sequence growth corresponds to
    content that is actually new to the declared surface, not merely
    re-routed between the two provenance mechanisms "headers" treats as
    equivalent.

    Requiring ``scope_fingerprint`` to merely differ is not enough on its
    own (Codex review, PR #641 follow-up, eighth P1): ``scope_fingerprint``
    hashes ALL of :data:`SCOPE_FIELD_KEYS` together, including
    ``public_header_dirs``, which is wholly unrelated to whether the
    *declared header set* actually grew. An unrelated
    ``public_header_dirs`` addition (e.g. a new ``-I`` search directory,
    with the declared ``headers`` set completely unchanged) makes
    ``scope_fingerprint`` differ and trivially satisfies the ``all(...)``
    additive-superset check below (an unchanged ``headers`` field is a
    same-value "superset" of itself), so it could corroborate a
    ``header_sequence``/``include_sequence`` waiver for exactly the silent-
    false-negative scenario this function exists to catch — the very
    ``headers`` field the sequence carve-outs need evidence about never
    moved at all. Requiring ``headers`` specifically to differ closes this:
    a genuine header-set addition always changes ``headers``, so this adds
    no new restriction for the real F8 scenario, only for the case where
    the fingerprint moved for an unrelated reason.
    """
    if old_contract.scope_fingerprint is None or new_contract.scope_fingerprint is None:
        return False
    if old_contract.scope_fingerprint == new_contract.scope_fingerprint:
        return False
    if old_contract.scope_fields.get("headers") == new_contract.scope_fields.get(
        "headers"
    ):
        return False
    return all(
        _scope_field_is_additive_superset(
            old_contract.scope_fields.get(key),
            new_contract.scope_fields.get(key),
        )
        for key in SCOPE_FIELD_KEYS
    )


def _build_context_corroborated(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Whether both *old* and *new* were actually parsed against real
    build-system evidence -- ``-p``/``--compile-db``/``--build-info``
    resolving the active ``-D`` defines and (via the same reconciliation)
    the real ``-std=`` flag, rather than a bare CLI invocation with no
    build context at all (ADR-020a/039; see ``snap.parsed_with_build_context``'s
    own docstring in ``cli_dump_helpers.py``).

    This is the build-context carve-out's one corroborating signal,
    deliberately coarser than the platform carve-out's per-field binary
    check above: there is no snapshot-level "this side's real language
    standard was X" fact to verify a *specific* field against (unlike
    ``elf_machine``/``elf_class``), only whether the resolved
    ``language_standard``/``macro_ops`` facts came from a genuine build
    system at all on both sides. A one-sided real build (only one snapshot
    has ``parsed_with_build_context``) is still exactly the "manifest/CLI-
    flag drift" mistake the gate exists to catch -- e.g. a stale cached dump
    compared against a freshly build-reconciled one -- so this only waives
    the mismatch when BOTH sides carry that evidence.
    """
    return old.parsed_with_build_context and new.parsed_with_build_context


@dataclass(frozen=True)
class ComparabilityMismatch:
    """Returned by :func:`check_contracts_comparable` in ``diagnostic=True``
    mode instead of raising — describes the one mismatch that would
    otherwise have raised (scope is checked first, so a scope mismatch
    shadows a co-occurring profile one, same as the raising path)."""

    kind: str  # "scope" | "profile" | "dependency_scope"
    reason: str


def _effective_dependency_scope(snap: AbiSnapshot) -> str:
    """*snap.dependency_scope*, treating a missing/``None`` value as
    ``"full"`` — the only behavior that existed before ``dumper_scoping.py``
    (a pre-v18 baseline, or any snapshot ``dumper_scoping`` never ran
    against, was never filtered)."""
    return snap.dependency_scope if snap.dependency_scope is not None else "full"


def _check_dependency_scope_comparable(
    old: AbiSnapshot, new: AbiSnapshot
) -> ComparabilityMismatch | None:
    """Independent of and checked before the fingerprint-based gate below:
    refuses to compare a dependency-filtered snapshot (``dump``'s default,
    since ``dumper_scoping.py``) against an unfiltered one — the asymmetry a
    plain ``dump old.so -H ... -o baseline.json`` followed by
    ``compare baseline.json new.so -H ...`` produces today, since ``compare``'s
    own live-binary dumping never applies this filter. Neither
    ``scope_fingerprint`` nor ``profile_fingerprint`` observe this axis at all
    (dependency scope isn't a declared-header-set or compile-context fact —
    it's a post-parse filtering decision made after both are already
    computed), so this cannot be folded into either fingerprint's existing
    field set without a much larger change to their carve-out logic; a
    separate, simple check is deliberately lower-risk here.

    Only fires when at least one side actually has header-derived
    declarations (``from_headers``) — the axis this field describes is
    meaningless for a binary/DWARF-only snapshot, and neither side needing to
    have dependency-scoped anything means there's nothing to mismatch.
    """
    if not (old.from_headers or new.from_headers):
        return None
    old_scope = _effective_dependency_scope(old)
    new_scope = _effective_dependency_scope(new)
    if old_scope == new_scope:
        return None
    reason = (
        "old and new snapshots have different dependency-scoping modes "
        f"(old: {old_scope!r}, new: {new_scope!r}) — one side excludes "
        "toolchain/system-header declarations (`dump`'s default; see "
        "dumper_scoping.py) and the other does not, so they do not cover "
        "the same declared surface. Regenerate both snapshots with the same "
        "mode: pass --include-dependencies on both sides, or on neither. A "
        "baseline dumped before this field existed is treated as 'full' "
        "(unfiltered) — if it was actually produced with dependency "
        "filtering by some other means, regenerate it."
    )
    return ComparabilityMismatch(kind="dependency_scope", reason=reason)


def check_contracts_comparable(
    old: AbiSnapshot, new: AbiSnapshot, *, diagnostic: bool = False
) -> ComparabilityMismatch | None:
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

    **Every carve-out below is preceded by a fingerprint-authenticity check
    (Codex review, PR #641 follow-up, sixth P1):** each side's stored
    ``scope_fingerprint``/``profile_fingerprint`` must actually equal what
    :func:`compute_extraction_contract` would compute from that same
    side's own ``scope_fields``/``profile_fields`` (see
    :func:`_fingerprint_matches_fields`). Every carve-out below reasons
    entirely from the *fields* ("this recognized field grew additively, so
    the fingerprint mismatch is explained") — but that reasoning is only
    sound if the fingerprint was genuinely computed from those fields in
    the first place. For a snapshot this codebase's own
    :func:`compute_extraction_contract` produced, that invariant always
    holds; it is unenforced for a deserialized or externally constructed
    contract, though, so a stale, fabricated, or otherwise unrelated
    fingerprint could previously sit alongside fields that merely *look*
    additive and still be waived through — confirmed by direct repro
    before this fix: two arbitrary, unrelated fingerprint strings with
    ``headers`` genuinely growing additively still made this function
    return ``None`` (comparable). A mismatch on either side here is
    unconditionally fatal, before any of the carve-outs that follow are
    even reached.

    **Additive-only header-set carve-out (PR #641 follow-up, pvxs scan F8):**
    a ``scope_fingerprint`` mismatch does not raise when *every* differing
    :data:`SCOPE_FIELD_KEYS` field on the new side is a superset of the old
    side's (see :func:`_scope_field_is_additive_superset`) — an upstream
    project adding a new public header (or a new declared public-header
    directory) between two releases, with nothing removed or renamed, is
    ordinary evolution the ordinary diff engine can correctly report as
    additions, not the "manifest/CLI-flag drift between two extraction
    runs" mistake this gate exists to catch. A single-entry side (collapsed
    to a ``<single-header>``/``<single-header-dir>`` sentinel, since a lone
    header/dir's own name is not itself load-bearing scope identity — see
    :func:`compute_extraction_contract`) can never be verified this way and
    still raises, same as any *non*-superset mismatch (a removal, a rename,
    or a disjoint set) — this carve-out only ever *widens* what counts as
    comparable, never narrows the cases the gate still correctly refuses.
    Also rejects an **unknown** ``scope_fields`` key outside
    :data:`SCOPE_FIELD_KEYS` that differs (Codex review, PR #641 follow-up,
    third P1) — checked independently of and before this carve-out, the
    same reasoning as the profile side's ``unknown_differing`` check below:
    the ``all(...)`` here only ever examines :data:`SCOPE_FIELD_KEYS`, so a
    contract carrying a field this build doesn't recognize was invisible to
    it, and could be silently waived through if ``headers``/
    ``public_header_dirs`` happened to be equal or additive. Also rejects an
    **opaque** ``scope_fingerprint`` mismatch (Codex review, PR #641
    follow-up, fourth P1) — the scope-side equivalent of the opaque
    profile-fingerprint check below: the additive-superset check only ever
    runs over the recognized :data:`SCOPE_FIELD_KEYS` fields that actually
    *differ*, never the ones that happen to be identical (an identical
    field trivially passes :func:`_scope_field_is_additive_superset`'s own
    ``old_value == new_value`` branch without needing to be checked at
    all), so if *no* recognized field differs at all, there is nothing here
    to positively verify and this raises rather than treating the absence
    of any explanation as one.

    **Header-sequence-growth carve-out (PR #641 follow-up, third round):**
    waiving the scope mismatch above is not sufficient on its own — adding a
    header also changes ``profile_fields["header_sequence"]`` (declared-header
    *order* is a genuine extraction-context fact, tracked separately from
    scope's order-independent declared *set*; see
    :func:`compute_extraction_contract`), so the exact same "pure addition"
    case would otherwise still raise ``ProfileMismatchError`` immediately
    after the scope carve-out waives it. A ``profile_fingerprint`` mismatch
    confined to ``header_sequence`` does not raise when the new sequence is
    the old sequence, byte-for-byte unchanged, with new entries appended
    STRICTLY AFTER it (see :func:`_header_sequence_is_additive_reorder_free`)
    — proving every *existing* header's own preprocessing context (the
    headers parsed before it) is identical to before, not merely that
    existing headers keep their relative order to each other. A new header
    inserted before or between existing ones (Codex review, PR #641
    follow-up, seventh P1) still raises even though it superficially looks
    additive, since it changes what an existing header downstream of the
    insertion point is parsed after — the same "reorder of existing headers"
    risk this carve-out was always meant to exclude, just reached via
    insertion rather than a literal swap. A reorder of existing headers
    entangled with growth (the same genuine profile-relevant risk) still
    raises too, same as any other profile drift this carve-out doesn't
    cover. **Also requires
    :func:`_scope_growth_corroborated` (Codex review, PR #641 follow-up,
    P1):** an additive-shaped ``header_sequence`` on its own is not
    sufficient — a header already declared identically on both sides via
    ``--public-header``, but fed to the L2 frontend via ``-H`` only on the
    new side, produces the identical shape with ``scope_fingerprint``
    completely unchanged, even though the old snapshot never actually
    parsed that header's content (a real removal inside it would then be
    silently invisible, not reported). This carve-out therefore only fires
    when the scope-level check *also* independently confirms a genuinely
    differing, verified-additive growth.

    **Include-sequence-owned-growth carve-out (PR #641 follow-up, fourth
    round):** the real ``-H old=<dir> -H new=<dir>`` F8 invocation changes
    ``profile_fields["include_sequence"]`` too, not just
    ``header_sequence`` — the production dump path
    (``resolve_inferred_header_roots``, ``cli_dump_helpers.py``) auto-adds
    the header-owning directory as a declared include, and that slot's own
    token encodes the declared-header set it owns
    (:func:`_slot_token_for_ancestor`). A ``profile_fingerprint`` mismatch
    confined to ``include_sequence`` does not raise when every differing
    slot's owned ``"hdrs:..."`` token is itself a pure superset growth (see
    :func:`_include_sequence_is_additive_owned_growth`) — an ``"ext:"``/
    ``"label:"`` slot differing, a slot count change, or either side
    collapsing to the ``<single-header>`` sentinel all still raise, the
    same conservative defaults as the other carve-outs. Also requires
    :func:`_scope_growth_corroborated`, for the identical reason as the
    header-sequence carve-out above.

    **Opaque profile-fingerprint mismatches are rejected, not silently
    waived (Codex review, PR #641 follow-up, P1):** the four carve-outs
    above narrow an ``unexplained`` working set that starts as ``differing``
    — the set of :data:`PROFILE_FIELD_KEYS` that actually differ between
    ``old_fields``/``new_fields``. If ``profile_fingerprint`` differs but
    ``differing`` itself is *empty* — e.g. one or both sides' ``profile_fields``
    were entirely absent/malformed on deserialization
    (``_extraction_contract_from_dict`` substitutes ``{}``, so every field
    trivially compares equal) — there is nothing here to positively verify
    as safe, so this raises rather than treating the absence of an
    explanation as one. Only a ``differing`` set that is non-empty and gets
    fully narrowed to nothing by the carve-outs above counts as comparable.

    **Unknown profile deltas are also rejected, checked independently of
    and before any carve-out (Codex review, PR #641 follow-up, second P1):**
    ``differing`` above only ever iterates :data:`PROFILE_FIELD_KEYS`, so a
    contract carrying a field this build doesn't recognize at all (a newer
    schema key) was invisible to it — if that unrecognized delta happened to
    co-occur with an otherwise-legitimate, carve-out-waived delta (e.g.
    additive ``header_sequence`` growth), the pair was wrongly reported
    comparable once the recognized delta alone was waived, silently
    ignoring the unrecognized one. ``unknown_differing`` separately computes
    every key outside ``PROFILE_FIELD_KEYS`` (over the union of both sides'
    field-dict keys) that actually differs; its presence is unconditionally
    fatal, regardless of what the carve-outs above conclude about the
    recognized fields, since no carve-out here understands an unrecognized
    key's semantics well enough to vouch for it. Both ``unknown_differing``
    here and ``scope_unknown_differing`` above compare each key via
    ``.get(k, _FIELD_ABSENT)`` rather than ``.get(k, "")`` (Codex review, PR
    #641 follow-up, fifth P1): the empty-string fallback would conflate "key
    absent entirely" with "key present with an empty string value" — a
    newer-schema field added on only one side with an empty value (e.g.
    ``{"future_profile": ""}`` vs. no key at all) would otherwise compare
    ``"" == ""`` and stay invisible to these checks even though the key's
    very presence is exactly the schema drift they exist to catch.
    :data:`_FIELD_ABSENT` is a sentinel object distinct from every valid
    field value, so presence and absence are never conflated.

    **Carve-outs compose (PR #641 follow-up, fourth round):** a release
    combining two independently-sanctioned deltas — e.g. adding a header
    *and* making a corroborated C++-standard raise — has a ``differing``
    set that matches neither carve-out's static field-set on its own
    (``{"header_sequence", "language_standard"}`` is a subset of neither
    :data:`_HEADER_SEQUENCE_FIELDS` nor :data:`_BUILD_CONTEXT_FIELDS`
    alone). Each carve-out therefore claims and verifies only the subset of
    ``differing`` it understands, narrowing an ``unexplained`` working set;
    the pair is comparable once nothing remains unexplained, not only when
    one carve-out's field-set covers the whole mismatch by itself. The four
    carve-outs' field-sets are mutually disjoint, so application order
    never matters.

    **Known, accepted limitation, not a correctness bug (PR #641
    follow-up, fourth round):** a header added *outside* the old side's
    common ancestor directory shifts the common root every remaining
    ``headers`` identity is computed relative to (see
    :func:`compute_extraction_contract`), so even the *existing* headers'
    identity strings change shape (``"a.h"`` → ``"foo/a.h"``) and the
    additive-superset check correctly declines — this still raises
    ``ScopeMismatchError`` rather than silently producing a wrong verdict,
    exactly the same safe default as every other case these carve-outs
    don't cover. Out of scope for this round: the real F8 scenario adds a
    header *within* the existing common directory, which this decline does
    not affect (see the "real directory-based F8 scenario" regression test).

    ``diagnostic=True`` (ADR-050's ``--diagnostic-comparison`` escape hatch)
    downgrades a hard-fail into a :class:`ComparabilityMismatch` descriptor
    returned to the caller instead of raised — the one sanctioned way to
    force a tentative diff through a genuine contract mismatch. ``None`` is
    returned (in either mode) when the pair is comparable.
    """
    dependency_scope_mismatch = _check_dependency_scope_comparable(old, new)
    if dependency_scope_mismatch is not None:
        if diagnostic:
            return dependency_scope_mismatch
        raise ScopeMismatchError(dependency_scope_mismatch.reason)

    old_contract = old.contract
    new_contract = new.contract

    if (
        old_contract is not None
        and new_contract is not None
        and old_contract.scope_fingerprint is not None
        and new_contract.scope_fingerprint is not None
        and old_contract.scope_fingerprint != new_contract.scope_fingerprint
    ):
        if not (
            _fingerprint_matches_fields(
                old_contract.scope_fingerprint,
                old_contract.scope_fields,
                SCOPE_FIELD_KEYS,
            )
            and _fingerprint_matches_fields(
                new_contract.scope_fingerprint,
                new_contract.scope_fields,
                SCOPE_FIELD_KEYS,
            )
        ):
            # Neither carve-out below may be trusted: at least one side's
            # scope_fields don't actually produce that side's own
            # scope_fingerprint, so nothing reasoned from those fields
            # explains the real mismatch (Codex review, PR #641 follow-up,
            # sixth P1) -- see _fingerprint_matches_fields's own docstring.
            reason = (
                "old and new snapshots do not cover the same declared "
                "surface (scope_fingerprint mismatch), and at least one "
                "side's scope_fields do not reproduce its own "
                "scope_fingerprint — the comparison cannot be verified "
                "safe."
            )
            if diagnostic:
                return ComparabilityMismatch(kind="scope", reason=reason)
            raise ScopeMismatchError(reason)
        # A differing scope_fields key OUTSIDE SCOPE_FIELD_KEYS entirely --
        # a newer schema field this build doesn't recognize -- must also
        # block the additive-only carve-out below, for the identical reason
        # as the profile side's unknown_differing check (Codex review, PR
        # #641 follow-up, third P1): the carve-out's `all(...)` below only
        # ever checks SCOPE_FIELD_KEYS, so an unrecognized field's delta was
        # invisible to it -- if headers/public_header_dirs happened to be
        # equal or additive, the whole scope mismatch was wrongly waived
        # without ever examining the unrecognized field.
        scope_unknown_differing = {
            k
            for k in set(old_contract.scope_fields) | set(new_contract.scope_fields)
            if k not in SCOPE_FIELD_KEYS
            and old_contract.scope_fields.get(k, _FIELD_ABSENT)
            != new_contract.scope_fields.get(k, _FIELD_ABSENT)
        }
        # Which recognized SCOPE_FIELD_KEYS actually differ -- an entirely
        # empty set here (Codex review, PR #641 follow-up, fourth P1) means
        # NOTHING recognized explains the differing scope_fingerprint: a
        # deserialized/externally-constructed contract can carry an opaque
        # scope_fingerprint that doesn't match what this version would
        # recompute from scope_fields, the scope-side equivalent of the
        # opaque profile-fingerprint mismatch rejected below. Restricting
        # the additive-superset check to only the fields that actually
        # differ (rather than calling it for every SCOPE_FIELD_KEYS entry,
        # where an unchanged field would trivially pass via
        # _scope_field_is_additive_superset's old==new branch) makes an
        # empty `scope_differing` impossible to satisfy silently.
        scope_differing = {
            key
            for key in SCOPE_FIELD_KEYS
            if old_contract.scope_fields.get(key, "")
            != new_contract.scope_fields.get(key, "")
        }
        # Additive-only header-set carve-out (PR #641 follow-up, pvxs scan
        # F8) -- see check_contracts_comparable's own docstring. Gated into
        # the *condition* itself, not a `return None` inside the block
        # (Codex review, PR #641 follow-up): waiving the scope mismatch
        # must fall through to the profile check below, not skip it -- a
        # release that both adds a header AND changes an unrelated
        # extraction-profile field (compiler flags, macros, include order)
        # must still be caught by that check, not silently waved through.
        if (
            scope_unknown_differing
            or not scope_differing
            or not all(
                _scope_field_is_additive_superset(
                    old_contract.scope_fields.get(key),
                    new_contract.scope_fields.get(key),
                )
                for key in scope_differing
            )
        ):
            reason = (
                "old and new snapshots do not cover the same declared "
                "surface (scope_fingerprint mismatch) — the comparison is "
                "not comparable. This commonly means a manifest/CLI-flag "
                "drift between the two extraction runs, not a real API "
                "change."
            )
            if diagnostic:
                return ComparabilityMismatch(kind="scope", reason=reason)
            raise ScopeMismatchError(reason)

    if (
        old_contract is not None
        and new_contract is not None
        and old_contract.profile_fingerprint is not None
        and new_contract.profile_fingerprint is not None
        and old_contract.profile_fingerprint != new_contract.profile_fingerprint
    ):
        old_fields = old_contract.profile_fields
        new_fields = new_contract.profile_fields
        if not (
            _fingerprint_matches_fields(
                old_contract.profile_fingerprint, old_fields, PROFILE_FIELD_KEYS
            )
            and _fingerprint_matches_fields(
                new_contract.profile_fingerprint, new_fields, PROFILE_FIELD_KEYS
            )
        ):
            # Neither carve-out below may be trusted: at least one side's
            # profile_fields don't actually produce that side's own
            # profile_fingerprint (Codex review, PR #641 follow-up, sixth
            # P1) -- see _fingerprint_matches_fields's own docstring, and
            # the scope-side equivalent check above.
            reason = (
                "old and new snapshots were extracted under different "
                "compile contexts (profile_fingerprint mismatch), and at "
                "least one side's profile_fields do not reproduce its own "
                "profile_fingerprint — the comparison cannot be verified "
                "safe."
            )
            if diagnostic:
                return ComparabilityMismatch(kind="profile", reason=reason)
            raise ProfileMismatchError(reason)
        differing = {
            k
            for k in _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS
            if old_fields.get(k, "") != new_fields.get(k, "")
        }
        # A differing key OUTSIDE PROFILE_FIELD_KEYS entirely -- a newer
        # schema field this build doesn't recognize -- must also block the
        # "comparable" outcome, checked independently of and before any
        # carve-out (Codex review, PR #641 follow-up, P1): `differing` above
        # only ever iterates PROFILE_FIELD_KEYS, so a contract carrying an
        # extra field this version doesn't know how to interpret (mixed
        # with an otherwise-legitimate, carve-out-waived delta like additive
        # `header_sequence` growth) was invisible to `unexplained` and the
        # pair was wrongly reported comparable once the recognized delta
        # alone got waived. No carve-out here understands an unrecognized
        # key's semantics, so its presence is unconditionally fatal
        # regardless of what the recognized fields' carve-outs conclude.
        unknown_differing = {
            k
            for k in set(old_fields) | set(new_fields)
            if k not in PROFILE_FIELD_KEYS
            and old_fields.get(k, _FIELD_ABSENT) != new_fields.get(k, _FIELD_ABSENT)
        }
        # Each carve-out below claims and verifies only the subset of
        # `differing` it actually understands, removing exactly those
        # fields from `unexplained` -- carve-outs COMPOSE (Codex review, PR
        # #641 follow-up, fourth round): a release combining two
        # independently-sanctioned deltas (e.g. a header addition AND a
        # corroborated C++-standard raise) must not raise just because
        # neither carve-out's static field-set covers `differing` in full
        # on its own. The four carve-outs' field-sets
        # (_PLATFORM_IDENTITY_FIELDS/_BUILD_CONTEXT_FIELDS/
        # _HEADER_SEQUENCE_FIELDS/_INCLUDE_SEQUENCE_FIELDS) are mutually
        # disjoint, so processing order never matters -- each only ever
        # narrows `unexplained`, never re-adds to it.
        unexplained = set(differing)

        platform_candidate = unexplained & _PLATFORM_IDENTITY_FIELDS
        if platform_candidate:
            old_components = _binary_platform_components(old)
            new_components = _binary_platform_components(new)
            # Every candidate field must itself map to a binary-derived
            # component present on BOTH sides AND genuinely differing on
            # that same field (Codex review, PR #624) -- not just "some"
            # component of the platform identity differs somewhere. A field
            # with no corresponding binary component on one side (e.g.
            # pointer_width/endianness for a PE/Mach-O snapshot, which has
            # no distinct word-size/endianness field) can never be confirmed
            # this way, so the carve-out correctly declines to waive it.
            #
            # `target_triple` is the one exception, verified against the
            # FULL axis rather than its own single "machine" component
            # (Codex review, PR #624): some ELF families share `e_machine`
            # across word sizes (e.g. EM_RISCV for both RV32 and RV64), so a
            # target_triple change that's really just an expression of a
            # genuine word-size change (riscv32-... vs. riscv64-...) would
            # otherwise fail verification on its own narrow "machine"
            # component even though `elf_class` already confirms the
            # architecture genuinely differs. target_triple is a coarse,
            # composite descriptor -- unlike pointer_width/endianness, which
            # map to one specific, independently-meaningful field, it can be
            # corroborated by any genuine difference on this axis.
            if old_components is not None and new_components is not None:
                common_keys = old_components.keys() & new_components.keys()
                any_component_differs = any(
                    old_components[k] != new_components[k] for k in common_keys
                )

                def _field_verified(field: str) -> bool:
                    if field not in old_components or field not in new_components:
                        return False
                    if field == "target_triple":
                        return any_component_differs
                    return old_components[field] != new_components[field]

                if all(_field_verified(field) for field in platform_candidate):
                    # genuine cross-architecture compare; diff_platform.py handles it
                    unexplained -= platform_candidate

        build_candidate = unexplained & _BUILD_CONTEXT_FIELDS
        if build_candidate and _build_context_corroborated(old, new):
            # Build-context carve-out (Codex review, PR #624 follow-up --
            # examples/case98_cxx_standard_floor_raised's real CI failure):
            # a raised C++-standard floor or a build-derived macro delta
            # between two snapshots BOTH actually reconciled against real
            # build-system evidence is exactly the fact
            # CXX_STANDARD_FLOOR_RAISED/ABI_RELEVANT_BUILD_FLAG_CHANGED
            # (diff_build_config.py) exist to surface as a RISK finding --
            # gating it into a generic not_comparable first would only
            # discard that finding instead of letting the more specific
            # detector classify it correctly.
            unexplained -= build_candidate

        # Both sequence carve-outs below additionally require
        # _scope_growth_corroborated (Codex review, PR #641 follow-up, P1):
        # an additive-shaped header_sequence/include_sequence on its own is
        # not sufficient evidence -- a header already declared identically
        # on both sides via --public-header, but fed to the L2 frontend via
        # -H only on the new side, produces the identical additive-growth
        # SHAPE with scope_fingerprint completely UNCHANGED, even though the
        # old snapshot never actually parsed that header's content at all
        # (see _scope_growth_corroborated's own docstring for why that's
        # unsafe to wave through). Requiring a genuinely differing,
        # independently-verified scope-level growth corroborates that the
        # sequence growth reflects real new declared content, not just a
        # same-declared-surface extraction-mechanism difference.
        scope_growth_corroborated = _scope_growth_corroborated(
            old_contract, new_contract
        )
        # The specific set of header identities the sequence carve-outs
        # below are allowed to treat an appended/newly-owned entry as
        # corresponding to (Codex review, PR #641 follow-up, ninth P1) --
        # see _scope_newly_added_headers's own docstring for why
        # scope_growth_corroborated alone (proving the scope grew by SOME
        # header) isn't enough; the carve-outs must additionally verify
        # they're waiving growth in the SAME header(s).
        scope_new_headers = _scope_newly_added_headers(
            old_contract.scope_fields.get("headers"),
            new_contract.scope_fields.get("headers"),
        )

        header_seq_candidate = unexplained & _HEADER_SEQUENCE_FIELDS
        if (
            header_seq_candidate
            and scope_growth_corroborated
            and _header_sequence_is_additive_reorder_free(
                old_fields.get("header_sequence"),
                new_fields.get("header_sequence"),
                scope_new_headers,
            )
        ):
            # Header-sequence-growth carve-out (PR #641 follow-up, third
            # round) -- see check_contracts_comparable's own docstring.
            unexplained -= header_seq_candidate

        include_seq_candidate = unexplained & _INCLUDE_SEQUENCE_FIELDS
        if (
            include_seq_candidate
            and scope_growth_corroborated
            and _include_sequence_is_additive_owned_growth(
                old_fields.get("include_sequence"),
                new_fields.get("include_sequence"),
                scope_new_headers,
            )
        ):
            # Include-sequence-owned-growth carve-out (PR #641 follow-up,
            # fourth round) -- see check_contracts_comparable's own
            # docstring.
            unexplained -= include_seq_candidate

        if unknown_differing:
            reason = (
                "old and new snapshots were extracted under different "
                "compile contexts (profile_fingerprint mismatch), and "
                f"differ on field(s) this version does not recognize: "
                f"{', '.join(sorted(unknown_differing))} — the comparison "
                "cannot be verified safe."
            )
        elif not differing:
            # Codex review, PR #641 follow-up (P1): profile_fingerprint
            # differs but NONE of the known PROFILE_FIELD_KEYS explain it --
            # profile_fields was entirely absent/malformed on
            # deserialization (_extraction_contract_from_dict substitutes
            # {}, so every old_fields.get(k, "")/new_fields.get(k, "")
            # compares "" == "" for every k). An empty `differing` must NOT
            # be treated as "nothing to explain, therefore comparable" --
            # that would silently bypass this fail-closed gate exactly when
            # the granular field data needed to verify safety is missing or
            # incomplete, which is the opposite of the gate's purpose.
            reason = (
                "old and new snapshots were extracted under different "
                "compile contexts (profile_fingerprint mismatch), but no "
                "recognized profile field explains the difference — "
                "profile_fields may be absent/incomplete — so the "
                "comparison cannot be verified safe."
            )
        elif not unexplained:
            return None
        else:
            reason = (
                "old and new snapshots were extracted under different compile "
                f"contexts (profile_fingerprint mismatch; differing fields: "
                f"{', '.join(sorted(unexplained))}) — the comparison "
                "is not comparable."
            )
        if diagnostic:
            return ComparabilityMismatch(kind="profile", reason=reason)
        raise ProfileMismatchError(reason)

    return None

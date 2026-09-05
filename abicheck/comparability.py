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

import importlib as _importlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .comparability_fields import (
    # The redundant `X as X` aliases are explicit re-exports, for import-path
    # stability: these names were part of this module's surface before the
    # field layer moved to comparability_fields (see that module's docstring),
    # and dumper_contract.py/dumper_hybrid.py plus the comparability test
    # modules still import them from here. Without the alias, `--no-implicit-
    # reexport` refuses the import at the call site rather than here.
    IncludeDir as IncludeDir,
    _compute_profile_fields,
    _compute_scope_fields,
    _fingerprint_from_fields,
    _fingerprint_matches_fields as _fingerprint_matches_fields,
    _resolved,
    _sha256_of as _sha256_of,
)
from .comparability_json import _SCOPE_SINGLE_ENTRY_SENTINELS, _json_load_str_list
from .comparability_language_mode import (
    _PROBED_STANDARD_PREFIX as _PROBED_STANDARD_PREFIX,
    # `_is_gcc_gxx_driver_pair`/`_PROBED_STANDARD_PREFIX` are re-exported
    # (`X as X`) for the same import-path-stability reason as the
    # comparability_fields aliases above --
    # tests/test_comparability_gate_probe_upgrade.py imports both directly
    # from this module, predating this split.
    _is_gcc_gxx_driver_pair as _is_gcc_gxx_driver_pair,
    # `language_standard_content_divergence_corroborated` moved to
    # comparability_profile.py alongside the rest of the profile-fingerprint
    # axis, but tests/test_comparability_gate_probe_upgrade.py still imports
    # it from here -- same re-export idiom as the two names above.
    language_standard_content_divergence_corroborated as language_standard_content_divergence_corroborated,
)
from .comparability_sequences import (
    # `_header_sequence_is_additive_reorder_free`/
    # `_include_sequence_is_additive_owned_growth`/`_scope_newly_added_headers`
    # are used only by comparability_profile.py's own code now (the
    # profile-fingerprint axis moved there), but
    # tests/test_comparability_gate*.py still import them from this module --
    # same re-export idiom as the comparability_fields/comparability_language_mode
    # aliases above.
    _header_sequence_is_additive_reorder_free as _header_sequence_is_additive_reorder_free,
    _include_sequence_is_additive_owned_growth as _include_sequence_is_additive_owned_growth,
    _scope_newly_added_headers as _scope_newly_added_headers,
)
from .errors import AbicheckError, ProfileMismatchError, ScopeMismatchError
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

# E-S2 (docs/contribute/plans/cli-cleanup-phase-two.md, Block 5) -- the
# per-dimension comparability vocabulary. ``ComparabilityMismatch`` used to
# carry only a single coarse ``kind`` ("scope" | "profile" |
# "dependency_scope"), so a GCC-vs-Clang pair with a genuinely narrow mismatch
# (say, only ``macro_ops`` differing) was reported exactly as untrustworthy as
# one where every axis diverged -- "wholly refused or wholly trusted", per
# that plan's own words. ``ComparabilityMismatch.dimensions`` names which of
# these five report-facing axes the *specific* detected mismatch leaves
# unverified; a dimension absent from the set stays as trustworthy as it was
# before the mismatch was found. This module only ever populates dimensions
# ADR-050's contract can actually speak to (declaration/layout/runtime, from
# the header-declared-surface and compile-context fingerprints) -- it never
# claims authority over ``symbol`` (the binary's own exported-symbol-table
# identity, L0/L1, wholly independent of header/compile-context evidence) or
# ``source`` (L4/L5 build-source-graph evidence, a separate comparability
# axis this module's contract does not cover at all).
COMPARABILITY_DIMENSIONS = frozenset(
    {"symbol", "declaration", "layout", "runtime", "source"}
)

#: Which :data:`COMPARABILITY_DIMENSIONS` a differing ``profile_fields`` key
#: leaves unverified. A field can affect more than one dimension (e.g.
#: ``compiler_family`` bears on both struct layout and calling-convention/
#: runtime behavior); union across every actually-differing field is what
#: :func:`_dimensions_for_fields` computes.
_PROFILE_FIELD_DIMENSIONS: dict[str, frozenset[str]] = {
    "compiler_family": frozenset({"layout", "runtime"}),
    "compiler_version": frozenset({"layout", "runtime"}),
    "abi_dialect": frozenset({"layout", "runtime"}),
    # A raised/lowered language standard can gate which declarations exist
    # at all (e.g. a C++17-only member) as well as change layout (e.g.
    # inline variables, [[no_unique_address]]).
    "language_standard": frozenset({"declaration", "layout"}),
    "target_triple": frozenset({"layout", "runtime"}),
    "pointer_width": frozenset({"layout", "runtime"}),
    "endianness": frozenset({"runtime"}),
    # Macro state / pass-through flags can gate declarations (macro-guarded
    # APIs) and change layout (conditional fields, packing pragmas).
    "macro_ops": frozenset({"declaration", "layout"}),
    "pass_through_flags": frozenset({"declaration", "layout"}),
    "include_sequence": frozenset({"declaration"}),
    "header_sequence": frozenset({"declaration"}),
    # A DPC++ device/host frontend-context split changes both which
    # declarations the frontend sees and their runtime (device vs. host)
    # semantics (ADR-050 D5).
    "frontend_context_kind": frozenset({"declaration", "runtime"}),
}
#: Every dimension any recognized profile field can affect -- the
#: conservative fallback for a profile mismatch this module cannot attribute
#: to specific fields (an opaque/unauthenticated fingerprint).
_ALL_PROFILE_DIMENSIONS: frozenset[str] = frozenset().union(
    *_PROFILE_FIELD_DIMENSIONS.values()
)

#: :data:`_PROFILE_FIELD_DIMENSIONS`'s counterpart for ``scope_fields`` keys.
#: The declared-header-set/public-header-directory axis only ever bears on
#: which declarations exist to compare -- never runtime behavior, and layout
#: only insofar as an undeclared type has no layout facts to compare at all
#: (folded into "declaration" here, not double-counted as "layout").
_SCOPE_FIELD_DIMENSIONS: dict[str, frozenset[str]] = {
    "headers": frozenset({"declaration"}),
    "public_header_dirs": frozenset({"declaration"}),
    "translation_units": frozenset({"declaration"}),
}
_ALL_SCOPE_DIMENSIONS: frozenset[str] = frozenset().union(
    *_SCOPE_FIELD_DIMENSIONS.values()
)

#: A dependency-scoping mode mismatch (filtered vs. unfiltered) changes which
#: declarations were even parsed, which in turn removes any layout evidence
#: for whatever was filtered out on one side.
_DEPENDENCY_SCOPE_DIMENSIONS: frozenset[str] = frozenset({"declaration", "layout"})


def _dimensions_for_fields(
    fields: Sequence[str] | set[str], mapping: dict[str, frozenset[str]]
) -> frozenset[str]:
    """Union of ``mapping[field]`` over every *fields* entry, skipping any key
    *mapping* doesn't recognize (an unrecognized field's own caller is
    responsible for falling back to the conservative "affects everything"
    set -- see ``_ALL_PROFILE_DIMENSIONS``/``_ALL_SCOPE_DIMENSIONS`` above)."""
    result: frozenset[str] = frozenset()
    for field in fields:
        result |= mapping.get(field, frozenset())
    return result


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
        profile_fields = _compute_profile_fields(
            compiler_family=compiler_family,
            compiler_version=compiler_version,
            abi_dialect=abi_dialect,
            language_standard=language_standard,
            target_triple=target_triple,
            pointer_width=pointer_width,
            endianness=endianness,
            macro_ops=macro_ops,
            pass_through_flags=pass_through_flags,
            declared_headers=declared_headers,
            declared_includes=declared_includes,
            depfile_resolved_paths=depfile_resolved_paths,
            generated_driver_path=generated_driver_path,
            public_header_paths=public_header_paths,
            frontend_context_kind=frontend_context_kind,
        )
        profile_fingerprint = _fingerprint_from_fields(
            profile_fields,
            _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS
            if frontend_context_kind is not None
            else PROFILE_FIELD_KEYS,
        )

    scope_fingerprint: str | None = None
    scope_fields: dict[str, str] = {}
    if scope_inputs_present:
        scope_fields = _compute_scope_fields(
            declared_headers, public_header_paths, public_header_dirs, manifest_tu_scope
        )
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
        scope_fingerprint = _fingerprint_from_fields(
            scope_fields,
            _MANIFEST_SCOPE_FIELD_KEYS
            if manifest_tu_scope is not None
            else SCOPE_FIELD_KEYS,
        )

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
    ``public_header_paths`` (bare public-header provenance, never
    actually parsed) as the *same* declared-surface membership — see
    :func:`compute_extraction_contract`'s docstring — so a header already
    declared identically on both sides as public headers, but fed to
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
    shadows a co-occurring profile one, same as the raising path).

    ``kind``/``reason`` are unchanged (ADR-050 D2's original two fields --
    ``kind`` is what ``_MISMATCH_ERRORS`` keys the raised exception type on,
    and every existing caller of the raising path is unaffected). ``dimensions``
    is E-S2's addition (Block 5,
    ``docs/contribute/plans/cli-cleanup-phase-two.md``): the subset of
    :data:`COMPARABILITY_DIMENSIONS` this specific mismatch leaves unverified.
    A caller in ``diagnostic=True`` mode can use it to keep trusting
    conclusions on a dimension the mismatch never touched -- e.g. an
    intentional GCC-vs-Clang ``macro_ops`` divergence marks only
    ``{"declaration", "layout"}`` unverified, leaving ``symbol``-dimension
    (exported binary identity) conclusions untouched -- rather than the
    previous all-or-nothing ``assurance: none``. Consuming this into the diff
    pipeline's own per-finding assurance (rather than merely exposing it on
    the mismatch descriptor) is E-S2's own next slice, not this one -- see
    that plan section's own status note.
    """

    kind: str  # "scope" | "profile" | "dependency_scope"
    reason: str
    dimensions: frozenset[str] = frozenset()


def _check_dependency_scope_comparable(
    old: AbiSnapshot, new: AbiSnapshot
) -> ComparabilityMismatch | None:
    """Independent of and checked before the fingerprint-based gate below:
    refuses to compare a dependency-filtered snapshot (``dump``'s default,
    since ``dumper_scoping.py``) against an unfiltered one — the asymmetry a
    plain ``dump old.so -H ... -o baseline.json`` followed by
    ``compare baseline.json new.so -H ...`` used to produce, back when
    ``compare``'s own live-binary dumping never applied this filter by
    default (``compare`` now filters by default too, matching ``dump`` —
    this gate still matters for a direct Python API caller of
    ``service.run_dump``, whose own default remains unfiltered ("full") for
    backward compatibility). Neither ``scope_fingerprint`` nor
    ``profile_fingerprint`` observe this axis at all
    (dependency scope isn't a declared-header-set or compile-context fact —
    it's a post-parse filtering decision made after both are already
    computed), so this cannot be folded into either fingerprint's existing
    field set without a much larger change to their carve-out logic; a
    separate, simple check is deliberately lower-risk here.

    Only fires when at least one side actually has header-derived
    declarations (``from_headers``) — the axis this field describes is
    meaningless for a binary/DWARF-only snapshot, and neither side needing to
    have dependency-scoped anything means there's nothing to mismatch.

    **Deliberately does NOT treat a missing/``None`` value as ``"full"``**
    (Codex review, PR #651 follow-up): ``dumper_scoping.py``'s default
    filtering already shipped before this field existed, so an ordinary
    pre-v18 baseline dumped with `dump`'s default (no
    ``--include-system-declarations``) is almost always *already filtered* content
    that simply predates the tag — treating its ``None`` as ``"full"`` would
    spuriously ``ScopeMismatchError`` the single most common workflow
    (compare a committed/cached baseline against a fresh default dump),
    exactly the class of regression this codebase's own schema-version
    history repeatedly warns against. There is no way to recover which of
    "filtered" or "full" an old, untagged snapshot actually is from the
    object alone, so this only fires when BOTH sides carry an explicit,
    non-``None`` value and they differ — every live-binary or ``dump``
    snapshot produced by a current abicheck build is tagged
    ``"filtered"``/``"full"`` explicitly
    (``dumper_scoping.resolve_dependency_scope``, via
    ``wrap_run_dump_with_dependency_scope``), so the originally-reported
    danger — a filtered snapshot compared against an unfiltered one — is
    still caught once both sides come from a current abicheck build. Only a
    genuinely ambiguous old baseline (``None``) is left unchecked on this
    axis, the same conservative only-flag-what's-confidently-known bias
    ``dumper_scoping.py`` itself already uses throughout.
    """
    if not (old.from_headers or new.from_headers):
        return None
    old_scope = old.dependency_scope
    new_scope = new.dependency_scope
    if old_scope is None or new_scope is None or old_scope == new_scope:
        return None
    reason = (
        "old and new snapshots have different dependency-scoping modes "
        f"(old: {old_scope!r}, new: {new_scope!r}) — one side excludes "
        "toolchain/system-header declarations (`dump`'s default; see "
        "dumper_scoping.py) and the other does not, so they do not cover "
        "the same declared surface. Regenerate both snapshots with the same "
        "mode: pass --include-system-declarations on both sides, or on neither."
    )
    return ComparabilityMismatch(
        kind="dependency_scope", reason=reason, dimensions=_DEPENDENCY_SCOPE_DIMENSIONS
    )


#: Every key set :func:`compute_extraction_contract` may have hashed a
#: ``profile_fingerprint``/``scope_fingerprint`` over. Each fingerprint is
#: computed over the base set, *or* over the extended one when that dump
#: carried the extra field (``frontend_context_kind`` for a DPC++-capable
#: frontend, ADR-050 D5; ``translation_units`` for a ``--dump-manifest`` dump,
#: D6) -- so authenticating against the base set alone declared every such
#: contract inauthentic (CodeRabbit review; confirmed by direct repro: a
#: `frontend_context_kind="device"` contract's own fingerprint did not
#: reproduce under `PROFILE_FIELD_KEYS`, and a `manifest_tu_scope` one's did
#: not reproduce under `SCOPE_FIELD_KEYS`).
_PROFILE_FINGERPRINT_KEY_SETS = (
    PROFILE_FIELD_KEYS,
    _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS,
)
_SCOPE_FINGERPRINT_KEY_SETS = (SCOPE_FIELD_KEYS, _MANIFEST_SCOPE_FIELD_KEYS)


def _fingerprint_is_authentic(
    fingerprint: str, fields: dict[str, str], key_sets: Sequence[tuple[str, ...]]
) -> bool:
    """Whether *fingerprint* reproduces from *fields* under any of *key_sets*.

    Which set a given contract's fingerprint was hashed over is not always
    recoverable from the stored fields: ``profile_fields`` records
    ``frontend_context_kind`` as ``""`` both when the dump passed ``None``
    (base key set) and when it passed an empty string (extended set), so the
    gate cannot re-derive the choice and must accept either.

    That is no weaker than knowing the answer. This check exists to catch a
    *fabricated or stale* fingerprint sitting alongside fields that merely look
    additive (:func:`_fingerprint_matches_fields`); accepting one of two
    specific SHA-256 values instead of one leaves it exactly as hard to forge.
    """
    return any(
        _fingerprint_matches_fields(fingerprint, fields, keys) for keys in key_sets
    )


def _differing_keys(
    old_fields: dict[str, str], new_fields: dict[str, str], keys: Sequence[str]
) -> set[str]:
    """Which of the *recognized* ``keys`` actually differ between the two sides.

    Missing keys compare as ``""`` here, deliberately unlike
    :func:`_unknown_differing_keys` below: a recognized field this build knows
    about but neither side recorded is not a difference, whereas an
    *unrecognized* key's very presence on one side is exactly the schema drift
    that check exists to catch.
    """
    return {key for key in keys if old_fields.get(key, "") != new_fields.get(key, "")}


def _unknown_differing_keys(
    old_fields: dict[str, str], new_fields: dict[str, str], known: Sequence[str]
) -> set[str]:
    """Which keys *outside* ``known`` differ — a newer schema field this build
    doesn't recognize at all.

    Shared by both fingerprint gates below, which each reject such a key
    unconditionally and before any of their own carve-outs (Codex review, PR
    #641 follow-up, first and third P1): every carve-out reasons only over its
    own recognized field set, so an unrecognized delta was invisible to them and
    could ride along, silently waived, whenever a *recognized* delta happened to
    be carve-out-eligible. No carve-out here understands an unrecognized key's
    semantics well enough to vouch for it.

    Compares via ``.get(k, _FIELD_ABSENT)`` rather than ``.get(k, "")`` (Codex
    review, PR #641 follow-up, fifth P1): the empty-string fallback would
    conflate "key absent entirely" with "key present with an empty string
    value" — a newer-schema field added on only one side with an empty value
    (e.g. ``{"future_profile": ""}`` vs. no key at all) would otherwise compare
    ``"" == ""`` and stay invisible even though the key's very presence is the
    drift being looked for. :data:`_FIELD_ABSENT` is a sentinel object distinct
    from every valid field value, so presence and absence are never conflated.
    """
    return {
        key
        for key in set(old_fields) | set(new_fields)
        if key not in known
        and old_fields.get(key, _FIELD_ABSENT) != new_fields.get(key, _FIELD_ABSENT)
    }


def _scope_mismatch_is_additive(
    old_contract: ExtractionContract, new_contract: ExtractionContract
) -> bool:
    """Whether an already-authenticated ``scope_fingerprint`` mismatch is pure
    additive growth — the additive-only header-set carve-out (PR #641
    follow-up, pvxs scan F8); see :func:`check_contracts_comparable`'s own
    docstring.

    An unrecognized differing ``scope_fields`` key blocks the carve-out
    outright, checked independently of and before it (Codex review, PR #641
    follow-up, third P1) — the ``all(...)`` below only ever examines
    :data:`SCOPE_FIELD_KEYS`.

    An entirely empty ``scope_differing`` is *not* an explanation either (Codex
    review, PR #641 follow-up, fourth P1): a deserialized/externally-constructed
    contract can carry an opaque ``scope_fingerprint`` that doesn't match what
    this version would recompute from ``scope_fields``, the scope-side
    equivalent of the opaque profile-fingerprint mismatch rejected below.
    Restricting the additive-superset check to only the fields that actually
    differ (rather than calling it for every :data:`SCOPE_FIELD_KEYS` entry,
    where an unchanged field would trivially pass
    :func:`_scope_field_is_additive_superset`'s ``old == new`` branch) makes an
    empty ``scope_differing`` impossible to satisfy silently.
    """
    if _unknown_differing_keys(
        old_contract.scope_fields, new_contract.scope_fields, SCOPE_FIELD_KEYS
    ):
        return False
    scope_differing = _differing_keys(
        old_contract.scope_fields, new_contract.scope_fields, SCOPE_FIELD_KEYS
    )
    if not scope_differing:
        return False
    return all(
        _scope_field_is_additive_superset(
            old_contract.scope_fields.get(key),
            new_contract.scope_fields.get(key),
        )
        for key in scope_differing
    )


def _check_scope_fingerprint_comparable(
    old_contract: ExtractionContract | None, new_contract: ExtractionContract | None
) -> ComparabilityMismatch | None:
    """The ``scope_fingerprint`` half of :func:`check_contracts_comparable`.

    Gated independently of the profile half — a symbols-only side carrying only
    a ``scope_fingerprint`` still gets its scope checked. Returns the mismatch
    that would raise :class:`ScopeMismatchError`, or ``None`` when this axis is
    comparable (including when either side carries no contract/fingerprint).
    """
    if (
        old_contract is None
        or new_contract is None
        or old_contract.scope_fingerprint is None
        or new_contract.scope_fingerprint is None
        or old_contract.scope_fingerprint == new_contract.scope_fingerprint
    ):
        return None

    if not (
        _fingerprint_is_authentic(
            old_contract.scope_fingerprint,
            old_contract.scope_fields,
            _SCOPE_FINGERPRINT_KEY_SETS,
        )
        and _fingerprint_is_authentic(
            new_contract.scope_fingerprint,
            new_contract.scope_fields,
            _SCOPE_FINGERPRINT_KEY_SETS,
        )
    ):
        # The carve-out below may not be trusted: at least one side's
        # scope_fields don't actually produce that side's own
        # scope_fingerprint, so nothing reasoned from those fields
        # explains the real mismatch (Codex review, PR #641 follow-up,
        # sixth P1) -- see _fingerprint_matches_fields's own docstring.
        # Opaque/unauthenticated fingerprint: no specific scope_fields key
        # can be trusted to explain the mismatch, so every dimension this
        # axis can ever affect is reported unverified (see
        # _ALL_SCOPE_DIMENSIONS's own docstring) -- the same fail-closed
        # default the rest of this branch already applies to `kind`/`reason`.
        return ComparabilityMismatch(
            kind="scope",
            reason=(
                "old and new snapshots do not cover the same declared "
                "surface (scope_fingerprint mismatch), and at least one "
                "side's scope_fields do not reproduce its own "
                "scope_fingerprint — the comparison cannot be verified "
                "safe."
            ),
            dimensions=_ALL_SCOPE_DIMENSIONS,
        )

    # Waiving the scope mismatch must fall through to the profile check, not
    # skip it (Codex review, PR #641 follow-up) -- a release that both adds a
    # header AND changes an unrelated extraction-profile field (compiler flags,
    # macros, include order) must still be caught by that check, not silently
    # waved through. Returning None here, rather than short-circuiting the whole
    # gate, is what keeps that true.
    if _scope_mismatch_is_additive(old_contract, new_contract):
        return None
    # An unrecognized scope_fields key differing (checked first, since it can
    # never be attributed to a specific known dimension) falls back to the
    # same conservative "affects everything this axis can affect" set as the
    # opaque-fingerprint branch above; otherwise dimensions are exactly the
    # ones the actually-differing recognized fields map to.
    if _unknown_differing_keys(
        old_contract.scope_fields, new_contract.scope_fields, SCOPE_FIELD_KEYS
    ):
        scope_dimensions = _ALL_SCOPE_DIMENSIONS
    else:
        scope_dimensions = _dimensions_for_fields(
            _differing_keys(
                old_contract.scope_fields, new_contract.scope_fields, SCOPE_FIELD_KEYS
            ),
            _SCOPE_FIELD_DIMENSIONS,
        )
    return ComparabilityMismatch(
        kind="scope",
        reason=(
            "old and new snapshots do not cover the same declared "
            "surface (scope_fingerprint mismatch) — the comparison is "
            "not comparable. This commonly means a manifest/CLI-flag "
            "drift between the two extraction runs, not a real API "
            "change."
        ),
        dimensions=scope_dimensions,
    )


def _check_profile_fingerprint_comparable(
    old: AbiSnapshot, new: AbiSnapshot
) -> ComparabilityMismatch | None:
    """``check_contracts_comparable``'s profile-fingerprint axis.

    The real implementation lives in
    :mod:`abicheck.comparability_profile` (split out to stay under this
    module's own file-size soft limit) — see that module's docstring for
    the full rationale. Resolved via ``importlib.import_module`` rather
    than a static import: that sibling module needs
    :class:`ComparabilityMismatch` and several private fingerprint-
    diagnostic helpers back from here, and a static two-way import would be
    the exact ``comparability <-> comparability_profile`` cycle
    ``scripts/check_ai_readiness.py``'s ``import-cycle-growth`` check
    rejects — the identical shape ``type_reachability.py``'s own
    ``type_reachability_stdlib_spellings.py`` split resolved the same way.
    """
    module = _importlib.import_module(".comparability_profile", __package__)
    result: ComparabilityMismatch | None = module._check_profile_fingerprint_comparable(
        old, new
    )
    return result


# Which error each :attr:`ComparabilityMismatch.kind` raises as outside
# ``diagnostic`` mode. ``dependency_scope`` shares ScopeMismatchError with
# ``scope``: both say the two sides do not cover the same declared surface.
_MISMATCH_ERRORS: dict[str, type[AbicheckError]] = {
    "dependency_scope": ScopeMismatchError,
    "scope": ScopeMismatchError,
    "profile": ProfileMismatchError,
}


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
    as a public header, but fed to the L2 frontend via ``-H`` only on the
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
    waived (Codex review, PR #641 follow-up, P1):** the six carve-outs
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
    one carve-out's field-set covers the whole mismatch by itself. Four of
    the six carve-outs' field-sets (:data:`_PLATFORM_IDENTITY_FIELDS`/
    :data:`_BUILD_CONTEXT_FIELDS`/:data:`_HEADER_SEQUENCE_FIELDS`/
    :data:`_INCLUDE_SEQUENCE_FIELDS`) are mutually disjoint, so their
    relative order never matters among themselves. The remaining two,
    :func:`language_standard_probe_upgrade_corroborated` and
    :func:`language_standard_content_divergence_corroborated`, are
    narrower, single-field carve-outs over ``language_standard`` -- a field
    the build-context carve-out's own set already covers -- and are checked
    *after* the build-context one specifically, since either can still only
    narrow (never re-add to) the working set, so their position relative to
    the other four still never changes the outcome (see
    :func:`_unexplained_profile_fields`'s own docstring for the ordering
    itself).

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
    # Deferred as thunks rather than evaluated into a tuple up front: the three
    # axes are checked in a fixed order and the first mismatch wins (scope
    # shadows a co-occurring profile one, as ComparabilityMismatch's own
    # docstring states), so a later check must not run once an earlier one
    # already answered.
    checks: tuple[Callable[[], ComparabilityMismatch | None], ...] = (
        lambda: _check_dependency_scope_comparable(old, new),
        lambda: _check_scope_fingerprint_comparable(old.contract, new.contract),
        lambda: _check_profile_fingerprint_comparable(old, new),
    )
    for check in checks:
        mismatch = check()
        if mismatch is None:
            continue
        if diagnostic:
            return mismatch
        raise _MISMATCH_ERRORS[mismatch.kind](mismatch.reason)
    return None

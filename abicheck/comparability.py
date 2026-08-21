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

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
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
from .comparability_sequences import (
    _HEADER_SEQUENCE_FIELDS,
    _INCLUDE_SEQUENCE_FIELDS,
    _header_sequence_is_additive_reorder_free,
    _include_sequence_is_additive_owned_growth,
    _scope_newly_added_headers,
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


#: The literal ``language_standard`` prefix a probed (never explicitly
#: pinned) default carries -- see ``dumper_toolchain._probe_default_
#: language_standard``'s own docstring. Mirrored here (not imported) since
#: this module has no other reason to depend on ``dumper_toolchain``.
_PROBED_STANDARD_PREFIX = "probed:"

#: The literal standard both header-AST command builders unconditionally
#: force for an unpinned C/gnu-dialect parse -- mirrors
#: ``dumper_toolchain._FORCED_C_STANDARD`` (not imported, same reasoning as
#: ``_PROBED_STANDARD_PREFIX`` above).
_FORCED_C_STANDARD = "gnu11"

#: Mirrors ``dumper_toolchain._resolve_standard_provenance``'s ``"gnu++20"``
#: literal for a ``force_cpp20`` heuristic hit (not imported, same
#: reasoning as ``_PROBED_STANDARD_PREFIX`` above).
_FORCED_CPP20_STANDARD = "gnu++20"

#: Per resolved language *mode*, the one bare (non-``"probed:"``-prefixed)
#: ``language_standard`` literal an *unpinned* parse can ever actually
#: produce -- used by
#: :func:`_language_standard_content_divergence_corroborated` to reject a
#: bare value that merely happens to collide with one of these (Codex
#: review: ``-std=gnu11``/``-std=gnu++17`` given without ``--lang`` produces
#: the identical bare literal an unpinned parse would, and
#: ``_language_standard_is_lang_pinned`` only recognizes an explicit *lang
#: tag*, not an explicit *standard* given without one). Any other bare
#: literal can only have come from an explicit pin.
_KNOWN_AUTO_RESOLVED_BARE_STANDARDS = {
    "c": _FORCED_C_STANDARD,
    "c++": _FORCED_CPP20_STANDARD,
}

#: Every ``language_standard_field`` spelling an *unpinned, no-resolved-
#: standard* dump could have produced before either the probe or the
#: forced-``gnu11`` report existed, that this carve-out can still safely
#: corroborate: an explicit ``--lang`` with nothing else resolved (bare
#: ``"c"``/``"c++"``/``"cpp"`` -- ``"cpp"`` is a second, still-supported
#: spelling for C++ alongside ``"c++"``, per :func:`_resolve_force_cpp`'s
#: own ``lang.upper() in ("C++", "CPP")`` check; ``language_standard_field``
#: lowercases but does not otherwise canonicalize the tag, so the two
#: spellings persist as distinct strings, not merely distinct casings —
#: Codex review, fresh evidence). Deliberately excludes the bare empty
#: string (no ``--lang`` given at all, pure content-based auto-detection)
#: -- see :func:`_newly_resolved_standard_remainder`'s own docstring for why.
_UNRESOLVED_STANDARD_SPELLINGS = ("c", "c++", "cpp")


def _newly_resolved_standard_remainder(old_std: str, new_std: str) -> str | None:
    """If *old_std* is one of :data:`_UNRESOLVED_STANDARD_SPELLINGS` and
    *new_std* carries the identical lang tag plus something newly resolved,
    return that "something" (the part after the tag); otherwise ``None``.

    Split out of :func:`_language_standard_probe_upgrade_corroborated` so
    the "same lang tag, newly populated" structural check has one
    definition, checked in both directions there (Codex review, fresh
    evidence: an explicit-``--lang`` baseline moves from a bare ``"c"``/
    ``"c++"`` to ``"c:gnu11"``/``"c++:probed:..."``, not from an empty
    string, which an earlier version of this check could not recognize).

    Deliberately does **not** accept a bare empty *old_std* (no ``--lang``
    given at all) the way an earlier version of this function did (Codex
    review, fresh evidence): an empty ``language_standard`` carries *no*
    signal about which language mode a pre-upgrade, pure-auto-detection
    dump actually resolved to (:func:`_resolve_force_cpp`'s decision is a
    function of the header *content*, which this carve-out has no access
    to) -- so a header that later gains enough C++-only syntax to flip
    ``_resolve_force_cpp``'s decision (a real language-mode change, not an
    upgrade artifact) would otherwise be indistinguishable from the
    upgrade-only case this carve-out exists to waive, and a matching
    ``compiler_family``/``compiler_version``/``compiler_sha256`` says
    nothing about which mode either side's *headers* actually resolved to.
    An explicit ``--lang c++``/``"cpp"`` has no such ambiguity:
    :func:`_resolve_force_cpp` returns C++ mode unconditionally for those,
    with no retry that could revise it downward, so the lang-tag-equality
    check above is sufficient corroboration on its own. An explicit
    ``--lang c`` is a narrower exception this function's own signature
    cannot see: ``dumper.py``'s C->C++ self-heal retry can still override
    it mid-parse when a header turns out to need a C++ stdlib header, so a
    ``"c"`` tag alone does not prove the *actual* parse stayed C --
    :func:`_language_standard_probe_upgrade_corroborated` checks the
    resolved remainder's own content for that case specifically (Codex
    review, fresh evidence).
    """
    if old_std not in _UNRESOLVED_STANDARD_SPELLINGS:
        return None
    prefix = f"{old_std}:"
    if not new_std.startswith(prefix):
        return None
    return new_std[len(prefix) :]


def _language_standard_probe_upgrade_corroborated(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_fields: dict[str, str],
    new_fields: dict[str, str],
) -> bool:
    """Whether a differing ``language_standard`` is fully explained by this
    probe (or the sibling forced-``gnu11`` report) having been *added* by an
    abicheck upgrade, not by a genuine toolchain difference (Codex review,
    fresh evidence).

    A baseline persisted before ``dumper_toolchain._probe_default_language_
    standard``/the forced-C-standard report existed recorded a bare
    ``"c"``/``"c++"`` (:data:`_UNRESOLVED_STANDARD_SPELLINGS`) for an
    unpinned (no explicit ``-std=``, no forced-C++20-heuristic) dump given
    an explicit ``--lang`` -- that was the only value this field could ever
    take for that shape of input. A freshly re-dumped snapshot of the
    identical input under the identical toolchain now records a real,
    newly-resolved value there instead (:data:`_FORCED_C_STANDARD` for an
    unpinned C/gnu parse, or a ``"probed:..."`` value for an unpinned C++
    parse or an MSVC-dialect C parse), purely because the tool was upgraded
    -- comparing the two would otherwise raise ``ProfileMismatchError`` on
    every such baseline, solely from the upgrade itself, not from anything
    about the library changing. :func:`_newly_resolved_standard_remainder`
    checks both directions share the same lang tag and isolates the
    newly-resolved remainder for the check below -- see that function's own
    docstring for why a bare *empty* ``language_standard`` (no ``--lang`` at
    all) is deliberately **not** eligible here, unlike an earlier version of
    this carve-out.

    Waived only when independently corroborated by an EXACT, non-empty
    ``compiler_family``/``compiler_version`` match on both sides: the
    probed default is a deterministic function of the exact resolved
    compiler binary, so an unchanged ``compiler_version`` is strong,
    specific evidence the unpinned default did not actually change either
    -- mirroring the platform carve-out's own "verify each specific
    differing field, not just some other field on the same axis" discipline
    (:func:`_platform_identity_confirmed`). A *changed* ``compiler_version``
    already raises today (no carve-out exists for it, and none is added
    here), which is correct: upgrading the compiler between the baseline
    and the comparison is a real profile change this gate should still
    catch.

    When both sides' ``AbiSnapshot.ast_toolchain`` carry a non-empty
    ``compiler_sha256`` (Codex review, fresh evidence), that content-address
    is also required to match: a compiler *wrapper* replaced at the same
    path can report an identical ``compiler_family``/``compiler_version``
    string while actually selecting a different default dialect --
    ``compiler_version`` is text a wrapper's own ``--version`` output
    chooses to emit, not a fact this tool independently verifies, whereas
    ``compiler_sha256`` is the resolved binary's own content hash
    (``dumper_toolchain._tool_identity_metadata``) and cannot lie the same
    way. Checked only when available on *both* sides -- an older/legacy
    snapshot predating this field, or a side whose compiler resolution
    itself failed (``ast_toolchain["compiler_error"]``, no ``compiler_*``
    keys at all), falls back to the family/version check above rather than
    failing closed on missing evidence it was never in a position to carry.

    The ``"probed:"`` marker is checked by *containment*, not
    ``str.startswith`` (Codex review, fresh evidence): when a dump also
    pins an explicit ``--lang``, ``language_standard_field`` prefixes the
    probed value with ``"c++:"``/``"c:"`` (e.g.
    ``"c++:probed:__cplusplus=201703L"``), so the marker does not
    necessarily sit at position 0 -- mirroring
    ``dumper_toolchain._cplusplus_macro_for_standard``'s identical fix.

    **Known, narrower residual (Codex review, fresh evidence), not fixed
    here**: the ``compiler_family``/``compiler_version`` corroboration
    above reads ``dumper_toolchain._stamp_ast_parser``'s ``compiler_*``
    stamping, which this same PR also fixed to resolve against the
    header-AST parse's *actual* post-retry compiler (``resolved_compiler``)
    rather than the caller's original, unresolved request -- see that
    function's own docstring. For a castxml dump where those two diverge
    (a force_cpp self-heal/remap changed which binary was actually
    invoked), a **legacy baseline persisted by pre-fix abicheck** recorded
    ``compiler_selected``/``compiler_family``/``compiler_version`` from the
    *wrong*, unresolved binary -- so comparing it against a freshly
    re-dumped snapshot of the identical input under the identical
    toolchain installation can now see a *changed* ``compiler_family``/
    ``compiler_version`` purely because this PR's own stamping fix
    corrected which binary's identity gets recorded, not because the
    installation changed. That is the same "an abicheck upgrade must not
    by itself make an unchanged baseline ``NOT_COMPARABLE``" problem this
    whole carve-out exists to solve, just on the corroboration axis rather
    than the ``language_standard`` axis it corroborates -- and it
    compounds: a real, waivable ``language_standard`` transition on such a
    baseline now also fails this function's own compiler-identity check,
    so the corroboration this carve-out depends on is unavailable exactly
    when the legacy stamping bug applies. Deliberately not addressed with
    a further carve-out: there is no sound way, from either snapshot's
    persisted content alone, to distinguish "this compiler_family/version
    difference is purely the stamping fix correcting a mis-recorded legacy
    baseline" from "the installation's compiler genuinely changed between
    the two dumps" -- the old baseline recorded only the (wrong) binary it
    picked, never what the corrected resolution *would* have produced, so
    there is no fact to corroborate against. Affected users should
    re-``dump`` their baseline once after upgrading past this fix rather
    than rely on the carve-out to bridge it, the same guidance any
    ``ProfileMismatchError`` from an unrelated real toolchain change
    already implies.
    """
    old_std = old_fields.get("language_standard", "")
    new_std = new_fields.get("language_standard", "")
    forward = _newly_resolved_standard_remainder(old_std, new_std)
    tag, remainder = (
        (old_std, forward)
        if forward is not None
        else (new_std, _newly_resolved_standard_remainder(new_std, old_std))
    )
    if remainder is None:
        return False
    is_newly_resolved = (
        remainder == _FORCED_C_STANDARD or _PROBED_STANDARD_PREFIX in remainder
    )
    if not is_newly_resolved:
        return False
    # Codex review, fresh evidence: an explicit "c" tag does not pin the
    # mode unconditionally the way this function's docstring above assumes
    # -- both header-AST backends self-heal an explicit --lang c request
    # into C++ when the header turns out to need a C++ stdlib header
    # (dumper.py's C->C++ retry applies regardless of whether C mode was
    # auto-detected or explicitly requested). A self-healed parse's probed
    # value always names __cplusplus (never __STDC_VERSION__, and never the
    # bare _FORCED_C_STANDARD literal, which _resolve_standard_provenance
    # only ever returns for a genuinely-C final mode) -- so a "c"-tagged
    # remainder containing it is real evidence the *new* side's actual
    # parse mode diverged from its own explicit tag, not merely newly
    # discovered upgrade evidence, and must not be waived.
    if tag == "c" and "__cplusplus" in remainder:
        return False
    for key in ("compiler_family", "compiler_version"):
        old_v = old_fields.get(key, "")
        new_v = new_fields.get(key, "")
        if not old_v or not new_v or old_v != new_v:
            return False
    old_sha = old.ast_toolchain.get("compiler_sha256", "")
    new_sha = new.ast_toolchain.get("compiler_sha256", "")
    if old_sha and new_sha and old_sha != new_sha:
        return False
    return True


def _language_standard_is_lang_pinned(std: str) -> bool:
    """Whether *std* (a ``language_standard`` profile-field value) reflects
    an explicit ``--lang`` given by the caller, as opposed to pure
    content-based auto-detection (:func:`dumper_toolchain._resolve_force_cpp`).

    ``language_standard_field`` only ever prefixes the resolved standard
    with a lang tag (``"c:"``/``"c++:"``/``"cpp:"``) -- or returns the bare
    tag alone, pre-probe -- when ``lang`` was actually passed; a pure
    auto-detected value (whether the bare :data:`_FORCED_C_STANDARD`
    literal, a ``"probed:..."`` value, or the ``force_cpp20`` literal
    ``"gnu++20"``) never carries one. See that function's own docstring.
    """
    return std in _UNRESOLVED_STANDARD_SPELLINGS or std.startswith(
        tuple(f"{tag}:" for tag in _UNRESOLVED_STANDARD_SPELLINGS)
    )


def _language_standard_is_known_auto_resolved_form(std: str, mode: str) -> bool:
    """Whether *std* (an un-lang-pinned ``language_standard`` value) is a
    form :func:`dumper_toolchain._resolve_standard_provenance` can actually
    produce for an *unpinned* parse resolved to language *mode* (``"c"``/
    ``"c++"``) -- as opposed to a bare literal that merely happens to look
    like one (Codex review, real finding on this PR).

    An explicit ``-std=gnu11``/``-std=gnu++17`` given with no ``--lang`` at
    all is not caught by :func:`_language_standard_is_lang_pinned` (which
    only recognizes an explicit *lang tag*), and
    :func:`dumper_toolchain._extract_explicit_std_value` returns that value
    verbatim with no marker distinguishing it from an auto-resolved one.
    Most explicit standards are harmless here (a literal like ``"c++17"``
    is never something the unpinned path can produce either), but
    ``-std=gnu11``/``-std=gnu++20`` collide exactly with
    :data:`_FORCED_C_STANDARD`/:data:`_FORCED_CPP20_STANDARD` -- an
    explicit, toolchain-config-driven divergence between those two literals
    would otherwise read as "purely content-driven" and be wrongly waived.

    A ``"probed:..."``-prefixed value is always genuine (only
    :func:`dumper_toolchain._probe_default_language_standard` ever produces
    it); a bare literal is only trusted when it exactly matches the one
    form the unpinned path can produce for *this* mode
    (:data:`_KNOWN_AUTO_RESOLVED_BARE_STANDARDS`) -- any other bare literal
    can only have come from an explicit pin.
    """
    if std.startswith(_PROBED_STANDARD_PREFIX):
        return True
    return std == _KNOWN_AUTO_RESOLVED_BARE_STANDARDS.get(mode)


def _compiler_version_sans_driver_name(version: str) -> str:
    """Strip a leading driver-name token from a raw ``--version`` banner
    (real CI failure, Codex review): castxml resolves a language-mode-
    specific host compiler (``dumper_ast_config._resolve_compiler_binary``
    maps C mode to ``gcc``/``cc``, C++ to ``g++``/``c++``), so a GNU
    compiler's banner differs *only* in that leading word between the two
    modes for the identical toolchain: ``"gcc (Ubuntu 13.3.0-...)
    13.3.0..."`` vs. ``"g++ (Ubuntu 13.3.0-...) 13.3.0..."``. A no-op for
    clang (``clang``/``clang++`` report byte-identical banners) and for two
    genuinely different compiler versions, whose remainders still differ.
    """
    parts = version.split(None, 1)
    return parts[1] if len(parts) == 2 else version


def _compiler_install_dir(ast_toolchain: dict[str, str]) -> str:
    """The resolved host compiler's own directory (``compiler_realpath``,
    falling back to ``compiler_selected``) -- proof of one toolchain install.
    ``PureWindowsPath`` (accepts ``/``/``\\``), not the platform-dependent
    ``Path``: a persisted path was captured on whichever OS produced it."""
    path = ast_toolchain.get("compiler_realpath") or ast_toolchain.get(
        "compiler_selected", ""
    )
    return str(PureWindowsPath(path).parent) if path else ""


def _driver_prefix(word: str, bare: str, suffix: str) -> str | None:
    """*word*'s cross-compile prefix if it's a ``bare``/``*-suffix`` driver
    name (``""`` for the bare form), else ``None``."""
    if word == bare:
        return ""
    return word[: -len(suffix)] if word.endswith(suffix) else None


def _is_gcc_gxx_driver_pair(old_version: str, new_version: str) -> bool:
    """Whether each banner's leading token is genuinely a C vs. C++ driver
    from the *same* toolchain -- not just two words separately matching
    ``gcc``/``g++`` by suffix (Codex review, fresh evidence, fourth round):
    ``vendor-a-g++``/``vendor-b-gcc`` both end in a recognized suffix but
    are two unrelated builds, so the two sides' cross-compile *prefixes*
    (``""`` for a bare ``gcc``/``g++``) must also match."""
    if not old_version or not new_version:
        return False
    # MinGW banners lead with "gcc.exe"/"g++.exe" -- strip the suffix first.
    old_word = old_version.split(None, 1)[0].lower().removesuffix(".exe")
    new_word = new_version.split(None, 1)[0].lower().removesuffix(".exe")
    dp = _driver_prefix
    old_c, old_cxx = dp(old_word, "cc", "gcc"), dp(old_word, "c++", "g++")
    new_c, new_cxx = dp(new_word, "cc", "gcc"), dp(new_word, "c++", "g++")
    return (old_c is not None and old_c == new_cxx) or (
        old_cxx is not None and old_cxx == new_c
    )


def _language_standard_content_divergence_corroborated(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_fields: dict[str, str],
    new_fields: dict[str, str],
) -> bool:
    """Whether a differing, purely content-driven ``language_standard`` is
    safe to waive -- distinct from
    :func:`_language_standard_probe_upgrade_corroborated`'s narrower
    "an abicheck upgrade added the probe" case (real CI failure:
    examples/case66_language_linkage_changed and
    examples/case69_trivial_to_nontrivial).

    When neither side was given an explicit ``--lang``,
    :func:`dumper_toolchain._resolve_force_cpp` decides the parse language
    purely from each side's own header *content* -- losing an ``extern
    "C"`` wrapper (case66) or gaining a C++-only construct (case69) is a
    real, ABI-relevant edit, not evidence of a different extraction
    *environment* (ADR-050 D1/D2) under an identical, corroborated
    toolchain -- real signal for the dedicated detectors to report, not a
    reason to refuse the comparison outright.

    **Scoped to a genuine mode switch (``c`` <-> ``c++``), not merely a
    differing edition within the same mode** (pinned by
    ``test_dumper_contract_wiring.py::
    test_cpp20_heuristic_forced_standard_flows_into_profile_fingerprint``):
    two header sets both parsed as C++ but resolving to a different
    *edition* purely because ``force_cpp20`` fires on only one side are
    still genuinely different dialects. Checked via
    ``AbiSnapshot.ast_toolchain["resolved_lang_mode"]``
    (:func:`dumper_toolchain._stamp_ast_parser`), not the
    ``language_standard`` string shape.

    Waived only when: (1) neither side's non-empty ``language_standard``
    reflects an explicit ``--lang`` (:func:`_language_standard_is_lang_pinned`);
    (2) each side's non-empty value is a form the *unpinned* path can
    actually produce for its own mode
    (:func:`_language_standard_is_known_auto_resolved_form` -- an explicit
    ``-std=gnu11``/``-std=gnu++17`` given without ``--lang`` collides with
    the unpinned path's own literal, so check (1) alone can't see it); and
    (3) corroborated by matching ``compiler_family``/``producer``/frontend
    ``version``, a driver-name-normalized ``compiler_version``, and
    ``compiler_sha256`` -- skipped only for a corroborated castxml gcc/g++
    pair sharing one install dir and ``compiler_target_triple`` (see code).
    """
    old_std = old_fields.get("language_standard", "")
    new_std = new_fields.get("language_standard", "")
    if old_std == new_std:
        return False
    # A bare-empty side (the probe can reject for reasons unrelated to
    # language mode -- real Windows CI failure) is deliberately not
    # excluded, unlike the sibling carve-out: `resolved_lang_mode` below
    # already proves the mode switch regardless of probe success, and
    # `language_standard_explicit` confirms it wasn't a pin either way.
    if old_std and _language_standard_is_lang_pinned(old_std):
        return False
    if new_std and _language_standard_is_lang_pinned(new_std):
        return False
    old_mode = old.ast_toolchain.get("resolved_lang_mode", "")
    new_mode = new.ast_toolchain.get("resolved_lang_mode", "")
    if (
        not old_mode
        or not new_mode
        or old_mode == new_mode
        or old_mode not in ("c", "c++")
        or new_mode not in ("c", "c++")
    ):
        return False
    if old_std and not _language_standard_is_known_auto_resolved_form(
        old_std, old_mode
    ):
        # A bare literal not in the unpinned-producible set can only be an
        # explicit -std=/--std=/std: pin given without --lang -- invisible
        # to the lang-pinned check above, so this is a separate guard.
        return False
    if new_std and not _language_standard_is_known_auto_resolved_form(
        new_std, new_mode
    ):
        return False
    # The check above still can't tell apart the exact-collision pair --
    # an explicit -std=gnu11/-std=gnu++20 given without --lang produces the
    # identical literal :data:`_KNOWN_AUTO_RESOLVED_BARE_STANDARDS` uses for
    # the unpinned path. Needs real provenance instead:
    # AbiSnapshot.ast_toolchain["language_standard_explicit"]
    # (dumper_toolchain._stamp_ast_parser) records whether the caller
    # actually gave an explicit pin, independent of the resulting literal.
    # Missing on either side (a legacy pre-provenance snapshot) fails
    # closed -- re-dump once rather than trusting an unconfirmed pair.
    if (
        old.ast_toolchain.get("language_standard_explicit") != "0"
        or new.ast_toolchain.get("language_standard_explicit") != "0"
    ):
        return False
    old_family = old_fields.get("compiler_family", "")
    new_family = new_fields.get("compiler_family", "")
    if not old_family or not new_family or old_family != new_family:
        return False
    old_producer = old.ast_toolchain.get("producer", "")
    new_producer = new.ast_toolchain.get("producer", "")
    if not old_producer or not new_producer or old_producer != new_producer:
        return False
    # The frontend's OWN version (castxml's, or clang's own --version for
    # the direct-clang backend) must match independently of the host
    # compiler check below -- confirms the same castxml/clang build parsed
    # both sides, not just an equivalent host toolchain.
    old_frontend_version = old.ast_toolchain.get("version", "")
    new_frontend_version = new.ast_toolchain.get("version", "")
    if (
        not old_frontend_version
        or not new_frontend_version
        or old_frontend_version != new_frontend_version
    ):
        return False
    old_host_version = old.ast_toolchain.get("compiler_version", "")
    new_host_version = new.ast_toolchain.get("compiler_version", "")
    if not old_host_version or not new_host_version:
        return False
    banners_differ = old_host_version != new_host_version
    if banners_differ and _compiler_version_sans_driver_name(
        old_host_version
    ) != _compiler_version_sans_driver_name(new_host_version):
        # castxml resolves a separate host-compiler binary per side
        # ("gcc"/"g++"), differing only in that leading word under one
        # unchanged toolchain; a real cross-version skew still differs.
        return False
    # sha256 skipped only for: castxml, gnu, a real same-toolchain gcc/g++
    # pair (_is_gcc_gxx_driver_pair), same install dir, AND the same
    # compiler_target_triple -- dir and driver names alone still accept two
    # unrelated cross-compilers side by side sharing a version-suffix
    # banner but targeting different architectures. Missing on either side
    # (a best-effort probe) fails this leg closed, not "unknown = same".
    old_dir = _compiler_install_dir(old.ast_toolchain)
    new_dir = _compiler_install_dir(new.ast_toolchain)
    old_triple = old.ast_toolchain.get("compiler_target_triple", "")
    new_triple = new.ast_toolchain.get("compiler_target_triple", "")
    if not (
        banners_differ
        and old_producer == "castxml"
        and old_family == "gnu"
        and _is_gcc_gxx_driver_pair(old_host_version, new_host_version)
        and old_triple
        and old_triple == new_triple
        and old_dir
        and old_dir == new_dir
    ):
        old_sha = old.ast_toolchain.get("compiler_sha256", "")
        new_sha = new.ast_toolchain.get("compiler_sha256", "")
        if old_sha and new_sha and old_sha != new_sha:
            return False
    return True


@dataclass(frozen=True)
class ComparabilityMismatch:
    """Returned by :func:`check_contracts_comparable` in ``diagnostic=True``
    mode instead of raising — describes the one mismatch that would
    otherwise have raised (scope is checked first, so a scope mismatch
    shadows a co-occurring profile one, same as the raising path)."""

    kind: str  # "scope" | "profile" | "dependency_scope"
    reason: str


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
    return ComparabilityMismatch(kind="dependency_scope", reason=reason)


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
        return ComparabilityMismatch(
            kind="scope",
            reason=(
                "old and new snapshots do not cover the same declared "
                "surface (scope_fingerprint mismatch), and at least one "
                "side's scope_fields do not reproduce its own "
                "scope_fingerprint — the comparison cannot be verified "
                "safe."
            ),
        )

    # Waiving the scope mismatch must fall through to the profile check, not
    # skip it (Codex review, PR #641 follow-up) -- a release that both adds a
    # header AND changes an unrelated extraction-profile field (compiler flags,
    # macros, include order) must still be caught by that check, not silently
    # waved through. Returning None here, rather than short-circuiting the whole
    # gate, is what keeps that true.
    if _scope_mismatch_is_additive(old_contract, new_contract):
        return None
    return ComparabilityMismatch(
        kind="scope",
        reason=(
            "old and new snapshots do not cover the same declared "
            "surface (scope_fingerprint mismatch) — the comparison is "
            "not comparable. This commonly means a manifest/CLI-flag "
            "drift between the two extraction runs, not a real API "
            "change."
        ),
    )


def _platform_identity_confirmed(
    old: AbiSnapshot, new: AbiSnapshot, platform_candidate: set[str]
) -> bool:
    """Whether the binaries themselves confirm every candidate platform-identity
    field as a genuine cross-architecture difference.

    Every candidate field must itself map to a binary-derived component present
    on BOTH sides AND genuinely differing on that same field (Codex review, PR
    #624) -- not just "some" component of the platform identity differs
    somewhere. A field with no corresponding binary component on one side (e.g.
    pointer_width/endianness for a PE/Mach-O snapshot, which has no distinct
    word-size/endianness field) can never be confirmed this way, so the
    carve-out correctly declines to waive it.

    ``target_triple`` is the one exception, verified against the FULL axis
    rather than its own single "machine" component (Codex review, PR #624):
    some ELF families share ``e_machine`` across word sizes (e.g. EM_RISCV for
    both RV32 and RV64), so a target_triple change that's really just an
    expression of a genuine word-size change (riscv32-... vs. riscv64-...)
    would otherwise fail verification on its own narrow "machine" component
    even though ``elf_class`` already confirms the architecture genuinely
    differs. target_triple is a coarse, composite descriptor -- unlike
    pointer_width/endianness, which map to one specific, independently-
    meaningful field, it can be corroborated by any genuine difference on this
    axis.
    """
    old_components = _binary_platform_components(old)
    new_components = _binary_platform_components(new)
    if old_components is None or new_components is None:
        return False

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

    return all(_field_verified(field) for field in platform_candidate)


def _unexplained_profile_fields(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_contract: ExtractionContract,
    new_contract: ExtractionContract,
    differing: set[str],
) -> set[str]:
    """Narrow ``differing`` down to the fields no carve-out can account for.

    Each carve-out claims and verifies only the subset of ``differing`` it
    actually understands, removing exactly those fields -- carve-outs COMPOSE
    (Codex review, PR #641 follow-up, fourth round): a release combining two
    independently-sanctioned deltas (e.g. a header addition AND a corroborated
    C++-standard raise) must not raise just because neither carve-out's static
    field-set covers ``differing`` in full on its own. Four of the six
    carve-outs' field-sets (:data:`_PLATFORM_IDENTITY_FIELDS`/
    :data:`_BUILD_CONTEXT_FIELDS`/:data:`_HEADER_SEQUENCE_FIELDS`/
    :data:`_INCLUDE_SEQUENCE_FIELDS`) are mutually disjoint, so their relative
    order never matters -- each only ever narrows the working set, never
    re-adds to it. The remaining two,
    :func:`_language_standard_probe_upgrade_corroborated` and
    :func:`_language_standard_content_divergence_corroborated`, are narrower,
    single-field carve-outs over ``language_standard`` -- a field the
    build-context carve-out's own set already covers -- so both are checked
    *after* the build-context one specifically (its broader waiver, when it
    applies, already subsumes either; when it doesn't, each still gets its
    own independent chance, checked in that order since the upgrade carve-out
    is the more specific of the two).
    """
    old_fields = old_contract.profile_fields
    new_fields = new_contract.profile_fields
    unexplained = set(differing)

    platform_candidate = unexplained & _PLATFORM_IDENTITY_FIELDS
    if platform_candidate and _platform_identity_confirmed(
        old, new, platform_candidate
    ):
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

    # abicheck-internal-bugs finding 2 follow-up (Codex review): waive a
    # language_standard-only mismatch that is fully explained by this PR's
    # own probe having been added by an upgrade -- see
    # _language_standard_probe_upgrade_corroborated's own docstring. Checked
    # after the build-context carve-out (whose broader waiver already
    # subsumes this one when both sides carry real build evidence) and
    # narrowly scoped to this single field so it can never mask a genuine
    # compiler_family/compiler_version difference riding alongside it.
    if (
        "language_standard" in unexplained
        and _language_standard_probe_upgrade_corroborated(
            old, new, old_fields, new_fields
        )
    ):
        unexplained.discard("language_standard")

    # Real CI failure (Codex review, fresh evidence:
    # examples/case66_language_linkage_changed,
    # examples/case69_trivial_to_nontrivial): a purely content-driven
    # language_standard divergence under an identical, corroborated
    # toolchain -- see _language_standard_content_divergence_corroborated's
    # own docstring for why this must never be treated as a blocking
    # extraction-environment mismatch, unlike the narrower upgrade-only
    # carve-out just above. Also discards "compiler_version" when present
    # (real CI failure, second round): under castxml specifically, the
    # mode switch this carve-out corroborates changes which host-compiler
    # binary gets resolved ("gcc" vs "g++"), which by itself makes the
    # *raw* compiler_version profile field differ even though
    # _language_standard_content_divergence_corroborated's own,
    # driver-name-normalized comparison already confirmed it's the
    # identical toolchain -- so once that corroboration succeeds,
    # compiler_version's raw-field divergence is explained by the exact
    # same fact language_standard's was, not a second, independent one.
    if (
        "language_standard" in unexplained
        and _language_standard_content_divergence_corroborated(
            old, new, old_fields, new_fields
        )
    ):
        unexplained.discard("language_standard")
        unexplained.discard("compiler_version")

    # Both sequence carve-outs below additionally require
    # _scope_growth_corroborated (Codex review, PR #641 follow-up, P1):
    # an additive-shaped header_sequence/include_sequence on its own is
    # not sufficient evidence -- a header already declared identically
    # on both sides as public headers, but fed to the L2 frontend via
    # -H only on the new side, produces the identical additive-growth
    # SHAPE with scope_fingerprint completely UNCHANGED, even though the
    # old snapshot never actually parsed that header's content at all
    # (see _scope_growth_corroborated's own docstring for why that's
    # unsafe to wave through). Requiring a genuinely differing,
    # independently-verified scope-level growth corroborates that the
    # sequence growth reflects real new declared content, not just a
    # same-declared-surface extraction-mechanism difference.
    scope_growth_corroborated = _scope_growth_corroborated(old_contract, new_contract)
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

    return unexplained


def _profile_mismatch_reason(
    unknown_differing: set[str], differing: set[str], unexplained: set[str]
) -> str | None:
    """Why an authenticated ``profile_fingerprint`` mismatch is not comparable,
    or ``None`` when every differing field was explained by a carve-out."""
    if unknown_differing:
        return (
            "old and new snapshots were extracted under different "
            "compile contexts (profile_fingerprint mismatch), and "
            f"differ on field(s) this version does not recognize: "
            f"{', '.join(sorted(unknown_differing))} — the comparison "
            "cannot be verified safe."
        )
    if not differing:
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
        return (
            "old and new snapshots were extracted under different "
            "compile contexts (profile_fingerprint mismatch), but no "
            "recognized profile field explains the difference — "
            "profile_fields may be absent/incomplete — so the "
            "comparison cannot be verified safe."
        )
    if unexplained:
        return (
            "old and new snapshots were extracted under different compile "
            f"contexts (profile_fingerprint mismatch; differing fields: "
            f"{', '.join(sorted(unexplained))}) — the comparison "
            "is not comparable."
        )
    return None


def _check_profile_fingerprint_comparable(
    old: AbiSnapshot, new: AbiSnapshot
) -> ComparabilityMismatch | None:
    """The ``profile_fingerprint`` half of :func:`check_contracts_comparable`.

    Gated independently of the scope half — a side that never ran an L2
    frontend carries no ``profile_fingerprint`` and is not hard-failed on this
    axis for that ordinary depth difference alone. Returns the mismatch that
    would raise :class:`ProfileMismatchError`, or ``None`` when this axis is
    comparable.
    """
    old_contract = old.contract
    new_contract = new.contract
    if (
        old_contract is None
        or new_contract is None
        or old_contract.profile_fingerprint is None
        or new_contract.profile_fingerprint is None
        or old_contract.profile_fingerprint == new_contract.profile_fingerprint
    ):
        return None

    old_fields = old_contract.profile_fields
    new_fields = new_contract.profile_fields
    if not (
        _fingerprint_is_authentic(
            old_contract.profile_fingerprint, old_fields, _PROFILE_FINGERPRINT_KEY_SETS
        )
        and _fingerprint_is_authentic(
            new_contract.profile_fingerprint, new_fields, _PROFILE_FINGERPRINT_KEY_SETS
        )
    ):
        # No carve-out below may be trusted: at least one side's
        # profile_fields don't actually produce that side's own
        # profile_fingerprint (Codex review, PR #641 follow-up, sixth
        # P1) -- see _fingerprint_matches_fields's own docstring, and
        # the scope-side equivalent check above.
        return ComparabilityMismatch(
            kind="profile",
            reason=(
                "old and new snapshots were extracted under different "
                "compile contexts (profile_fingerprint mismatch), and at "
                "least one side's profile_fields do not reproduce its own "
                "profile_fingerprint — the comparison cannot be verified "
                "safe."
            ),
        )

    differing = _differing_keys(
        old_fields, new_fields, _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS
    )
    # `_FRONTEND_CONTEXT_PROFILE_FIELD_KEYS`, not `PROFILE_FIELD_KEYS`
    # (CodeRabbit review): `frontend_context_kind` is a field this build knows
    # about -- `differing` directly above iterates it -- so reporting it as one
    # "this version does not recognize" was simply wrong. The outcome for a
    # differing `frontend_context_kind` is unchanged, only its reason: no
    # carve-out's field-set contains it, so it stays in `unexplained` and the
    # pair is still not comparable.
    unknown_differing = _unknown_differing_keys(
        old_fields, new_fields, _FRONTEND_CONTEXT_PROFILE_FIELD_KEYS
    )
    unexplained = _unexplained_profile_fields(
        old, new, old_contract, new_contract, differing
    )
    reason = _profile_mismatch_reason(unknown_differing, differing, unexplained)
    if reason is None:
        return None
    return ComparabilityMismatch(kind="profile", reason=reason)


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
    :func:`_language_standard_probe_upgrade_corroborated` and
    :func:`_language_standard_content_divergence_corroborated`, are
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

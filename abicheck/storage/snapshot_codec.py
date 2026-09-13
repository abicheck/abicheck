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

"""AbiSnapshot <-> JSON codec (ADR-061 gap E) -- the real storage-owned
implementation behind :mod:`abicheck.serialization`'s public compatibility
facade.

``abicheck/serialization.py`` stays the documented, stable import path
(``load_snapshot``/``save_snapshot``/``write_snapshot`` and friends); this
module and its siblings are where the codec itself now lives, classified
``storage`` per ADR-061's responsibility-package architecture. The codec is
split across several sibling modules purely to keep each one under the
ADR-061 new-file production line ceiling (a mechanical extraction, not a
redesign -- every comment and Codex-review citation moved verbatim):

* ``snapshot_schema_versions`` -- ``SCHEMA_VERSION``, its full per-bump
  history, and the derived ``_MIN_SCHEMA_VERSION_FOR_*``/
  ``_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`` thresholds.
* ``snapshot_encode`` -- the encode direction (``snapshot_to_dict``/
  ``snapshot_to_json``/``snapshot_content_digest``), re-exported here for
  the facade's own convenience.
* ``snapshot_decode_declarations`` -- the functions/variables/types/enums/
  typedefs decode (the largest single contiguous slice).
* ``snapshot_reliability_flags`` -- the seven ``*_facts_reliable`` flag
  computations.

This module itself owns: the sectioned-document unwrap and schema-version
gate, platform-block/provenance decode, the final ``AbiSnapshot(...)``
assembly, and the two functions :mod:`abicheck.serialization` calls
directly. Two of the historical ``snapshot_from_dict`` pipeline's steps are
genuinely NOT storage's to own and stay in that facade instead of living
here:

* ``workflows.snapshot_load.backfill_python_ext_from_evidence`` -- real
  evidence-derived extraction logic, not a fact lookup, so it cannot run
  inside a module whose own ``may_import`` is ``[model]`` only (see that
  function's own docstring, and the ADR-061 gap E closure history in
  ``docs/contribute/known-gaps.md``).
* ``policy.analysis_assurance_degraded_facts.degraded_reliability_facts`` --
  the load-time "which facts are stale" warning is a ``policy`` judgement
  over an already-decoded snapshot, not a decode step.

:func:`decode_snapshot` therefore returns a snapshot that has NOT yet run
either step, paired with the raw ``schema_version`` integer the facade's own
warning message needs; :func:`finalize_snapshot` is the storage-legal tail
(ELF-binding backfill, anonymous-type-spelling normalization, anonymous
closure-identity renumbering) that runs after those two facade-level steps.
``abicheck.serialization.snapshot_from_dict`` is the one place that stitches
all of it back into the original, unchanged five-step pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from ..snapshot_io import SnapshotWriteResult
from ..errors import IncompatibleSnapshotSchemaError, SnapshotError
from ..model import AbiSnapshot, DependencyInfo
from ..model.semantic_ir_legacy_adapter import assert_snapshot_semantic_ir_consistent
from ..snapshot_platform_blocks import (
    dwarf_advanced_from_dict as _dwarf_advanced_from_dict,
    dwarf_from_dict as _dwarf_from_dict,
    elf_from_dict as _elf_from_dict,
    kabi_from_dict as _kabi_from_dict,
    macho_from_dict as _macho_from_dict,
    numpy_capi_from_dict as _numpy_capi_from_dict,
    pe_from_dict as _pe_from_dict,
    python_api_from_dict as _python_api_from_dict,
    python_ext_from_dict as _python_ext_from_dict,
    sycl_from_dict as _sycl_from_dict,
)
from . import closure_identity
from .fact_codec import (
    apply_legacy_fact_backfill,
    decode_snapshot_facts,
    evidenced_producers,
)
from .sectioned_document import from_sectioned_document, is_sectioned_document
from .semantic_ir_codec import decode_semantic_ir
from .snapshot_decode_declarations import decode_declarations
from .snapshot_encode import (
    snapshot_content_digest as snapshot_content_digest,
    snapshot_to_dict as snapshot_to_dict,
    snapshot_to_json as snapshot_to_json,
)
from .snapshot_load_normalization import (
    backfill_missing_elf_binding,
    build_mode_from_dict,
    extraction_contract_from_dict,
    normalize_anonymous_type_spellings_on_load,
)
from .snapshot_reliability_flags import decode_reliability_flags
from .snapshot_schema_versions import (
    _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION,
    SCHEMA_VERSION as SCHEMA_VERSION,
)
from .surface_graph_codec import decode_surface_graph

_T = TypeVar("_T")


def _sub_block(parser: Callable[[dict[str, Any]], _T], raw: Any) -> _T | None:
    """Parse one optional sub-block of a serialized snapshot, or ``None``.

    Every ``elf``/``pe``/``macho``/``dwarf``/... section of a snapshot document
    is optional and, per this module's forward-compatibility convention, is
    ignored rather than fatal when it is present but not an object. Sharing the
    one guard keeps a dozen call sites from each spelling it out.
    """
    return parser(raw) if isinstance(raw, dict) else None


def decode_snapshot(
    d: dict[str, Any], *, max_known_schema_version: int = SCHEMA_VERSION
) -> tuple[AbiSnapshot, int]:
    # `max_known_schema_version` defaults to this module's own SCHEMA_VERSION
    # but is accepted as an explicit parameter so `abicheck.serialization`'s
    # facade-level (patchable) `SCHEMA_VERSION` global is the value actually
    # consulted below when called through `serialization.snapshot_from_dict`
    # -- a test that does `monkeypatch.setattr(serialization, "SCHEMA_VERSION",
    # ...)` must still change what this function treats as "known", which a
    # bare module-level reference to this module's own constant could not
    # honor once the codec moved out of the facade module itself.
    #
    # ADR-062/063 Phase 8 (redesign): a document written as the single-file
    # sectioned shape (`storage.sectioned_document`) is unwrapped into this
    # function's own long-established flat shape *before* anything below
    # runs -- every existing reliability-backfill/schema-version rule stays
    # exactly as it was, now just fed a document `export_legacy_snapshot`
    # reconstructed rather than one written flat. `is_sectioned_document`
    # checks for a `"sections"` key, never a real `AbiSnapshot` field, so a
    # flat document from any schema version cannot collide with it.
    if is_sectioned_document(d):
        d = from_sectioned_document(d)
    # Inspect schema version for future migration hooks.
    # Snapshots without schema_version are treated as v1 (pre-versioning format).
    # Currently only v1 and v2 exist and have the same on-disk layout, so no
    # migration is required.  This baseline lets future PRs add migration logic here.
    _schema_version: int = int(d.get("schema_version", 1))
    if (
        _schema_version > max_known_schema_version
        and _schema_version >= _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION
    ):
        # ADR-050 D1 — this reader has no code path that even looks for a
        # verdict-blocking field introduced at or after this threshold
        # (starting with ``contract``). Warn-and-continue here would let this
        # reader silently compare two possibly-incomparable snapshots and
        # produce an ordinary, wrong verdict — the exact failure mode this
        # ADR exists to close. Raised as a SnapshotError subclass so existing
        # ``except SnapshotError`` handling (e.g. cli_resolve.py's clean
        # click.UsageError/ClickException translation) still catches it.
        raise IncompatibleSnapshotSchemaError(
            f"Snapshot schema_version {_schema_version} requires abicheck "
            f"supporting at least schema_version "
            f"{_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION} to read safely "
            f"(this reader supports up to schema_version {max_known_schema_version}). "
            "Upgrade abicheck to read this snapshot."
        )
    if _schema_version > max_known_schema_version:
        import warnings

        warnings.warn(
            f"Snapshot schema_version {_schema_version} is newer than this abicheck "
            f"(supports up to schema_version {max_known_schema_version}). "
            "Data may be incomplete or misinterpreted. "
            "Upgrade abicheck to read this snapshot correctly.",
            UserWarning,
            stacklevel=2,
        )

    declarations = decode_declarations(d, _schema_version)
    funcs = declarations.functions
    variables = declarations.variables
    types = declarations.types
    enums = declarations.enums
    typedefs = declarations.typedefs
    typedefs_qualified = declarations.typedefs_qualified
    sidecar_entity_ids = declarations.sidecar_entity_ids

    elf_data = d.get("elf")
    pe_data = d.get("pe")
    macho_data = d.get("macho")
    dwarf_data = d.get("dwarf")
    dwarf_adv_data = d.get("dwarf_advanced")

    elf = _sub_block(lambda e: _elf_from_dict(e, _schema_version), elf_data)
    pe = _sub_block(lambda e: _pe_from_dict(e, _schema_version), pe_data)
    macho = _sub_block(lambda e: _macho_from_dict(e, _schema_version), macho_data)
    dwarf = _sub_block(_dwarf_from_dict, dwarf_data)
    dwarf_advanced = _sub_block(_dwarf_advanced_from_dict, dwarf_adv_data)

    sycl_data = d.get("sycl")
    sycl = _sub_block(_sycl_from_dict, sycl_data)

    kabi_data = d.get("kabi")
    kabi = _sub_block(_kabi_from_dict, kabi_data)
    numpy_capi_data = d.get("numpy_capi")
    numpy_capi = _sub_block(_numpy_capi_from_dict, numpy_capi_data)
    python_ext_data = d.get("python_ext")
    python_ext = _sub_block(_python_ext_from_dict, python_ext_data)

    python_api_data = d.get("python_api")
    python_api = _sub_block(_python_api_from_dict, python_api_data)

    dep_data = d.get("dependency_info")
    dep_info = (
        DependencyInfo(
            nodes=dep_data.get("nodes", []),
            edges=dep_data.get("edges", []),
            unresolved=dep_data.get("unresolved", []),
            bindings_summary=dep_data.get("bindings_summary", {}),
            missing_symbols=dep_data.get("missing_symbols", []),
        )
        if isinstance(dep_data, dict)
        else None
    )

    # Rehydrate BuildMode (schema v5). Missing key = older snapshot →
    # leave as None so build-mode-aware detectors fall back to "unknown".
    build_mode = build_mode_from_dict(d.get("build_mode"))

    # Build/source pack reference (schema v7, ADR-028). Optional: a missing key
    # on an older snapshot loads as None; a malformed (non-dict) value is ignored
    # rather than aborting the load. Back-compat: snapshots written before the
    # evidence→buildsource rename store the (unchanged) ref shape under the
    # legacy ``evidence_pack`` key, which this falls back to when present.
    ep_raw = d.get("build_source_pack")
    if ep_raw is None:
        ep_raw = d.get("evidence_pack")
    build_source_pack = None
    if isinstance(ep_raw, dict):
        from ..buildsource.model import BuildSourceRef

        build_source_pack = BuildSourceRef.from_dict(ep_raw)

    # Inline embedded build-info/source facts (single-artifact UX). Optional and
    # additive: a missing or malformed value loads as None and the compare falls
    # back to out-of-band --old/--new flags (or skips evidence entirely).
    bs_raw = d.get("build_source")
    build_source = None
    if isinstance(bs_raw, dict):
        from ..buildsource.pack import BuildSourcePack

        build_source = BuildSourcePack.from_embedded_dict(bs_raw)

    # from_headers provenance (added alongside the HEADER_AWARE tier-honesty
    # fix). An absent key means a legacy snapshot dumped before the field
    # existed: preserve the prior evidence-tier behavior by inferring header
    # provenance from a populated, non-elf-only surface, so saved baselines
    # (e.g. `abicheck compare libfoo-1.0.json libfoo-2.0.json`) do not silently
    # downgrade from HEADER_AWARE. A present key — including a legitimate False
    # for DWARF-only/symbols-only dumps — is honored verbatim.
    elf_only_mode = bool(d.get("elf_only_mode", False))
    if "from_headers" in d:
        from_headers = bool(d["from_headers"])
        from_headers_inferred = False
    else:
        from_headers = (not elf_only_mode) and bool(
            funcs or variables or types or enums or typedefs
        )
        # This provenance was guessed, not recorded. A legacy DWARF-only dump
        # populates the same surface lists, so the inference cannot tell it
        # apart from a header dump. Mark it inferred so source-level detectors
        # that demand genuine header evidence (parameter renames) stay quiet.
        from_headers_inferred = from_headers

    ast_producer_value = d.get("ast_producer")
    raw_ast_toolchain = d.get("ast_toolchain")
    ast_toolchain = (
        {
            str(key): str(value)
            for key, value in raw_ast_toolchain.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(raw_ast_toolchain, dict)
        else {}
    )
    raw_fallback_reason = d.get("ast_fallback_reason")
    ast_fallback_reason = (
        raw_fallback_reason if isinstance(raw_fallback_reason, str) else None
    )
    raw_ast_supported = d.get("ast_toolchain_supported")
    ast_toolchain_supported = (
        raw_ast_supported if isinstance(raw_ast_supported, bool) else None
    )
    raw_ast_unsupported_reasons = d.get("ast_toolchain_unsupported_reasons")
    ast_toolchain_unsupported_reasons = (
        [str(r) for r in raw_ast_unsupported_reasons]
        if isinstance(raw_ast_unsupported_reasons, list)
        else []
    )
    raw_resolved_standard = d.get("ast_resolved_standard")
    ast_resolved_standard = (
        raw_resolved_standard if isinstance(raw_resolved_standard, str) else None
    )
    raw_cplusplus_macro = d.get("ast_cplusplus_macro")
    ast_cplusplus_macro = (
        raw_cplusplus_macro if isinstance(raw_cplusplus_macro, str) else None
    )
    raw_compile_args = d.get("ast_compile_args")
    ast_compile_args = (
        tuple(str(a) for a in raw_compile_args)
        if isinstance(raw_compile_args, (list, tuple))
        else ()
    )
    raw_ast_sysroot = d.get("ast_sysroot")
    ast_sysroot = raw_ast_sysroot if isinstance(raw_ast_sysroot, str) else None
    raw_layout_coherence = d.get("dwarf_layout_coherence")
    dwarf_layout_coherence = (
        raw_layout_coherence if isinstance(raw_layout_coherence, str) else None
    )
    raw_layout_coherence_mismatches = d.get("dwarf_layout_coherence_mismatches")
    dwarf_layout_coherence_mismatches = (
        tuple(str(m) for m in raw_layout_coherence_mismatches)
        if isinstance(raw_layout_coherence_mismatches, (list, tuple))
        else ()
    )

    reliability_flags = decode_reliability_flags(
        d,
        from_headers=from_headers,
        ast_producer_value=ast_producer_value,
        schema_version=_schema_version,
    )
    header_cv_facts_reliable_value = reliability_flags["header_cv_facts_reliable"]
    clang_deprecation_facts_reliable_value = reliability_flags[
        "clang_deprecation_facts_reliable"
    ]
    clang_field_initializer_facts_reliable_value = reliability_flags[
        "clang_field_initializer_facts_reliable"
    ]
    clang_vtable_facts_reliable_value = reliability_flags["clang_vtable_facts_reliable"]
    clang_restrict_facts_reliable_value = reliability_flags[
        "clang_restrict_facts_reliable"
    ]
    clang_va_list_facts_reliable_value = reliability_flags[
        "clang_va_list_facts_reliable"
    ]
    castxml_var_access_facts_reliable_value = reliability_flags[
        "castxml_var_access_facts_reliable"
    ]
    param_kind_facts_reliable_value = reliability_flags["param_kind_facts_reliable"]

    # ADR-063 Phase 0 (schema v26) and Phase 5's own case-(a) batches: see
    # storage/fact_codec.py. One call, not one per converted field -- every
    # rule it applies is "a document predating this field's own Fact[T]
    # conversion carries a value its snapshot-level reliability flag says
    # cannot be trusted", and every such flag is resolved by the time we get
    # here -- which is why this call sits below every one of those flag
    # computations rather than in the middle of them.
    apply_legacy_fact_backfill(
        d,
        types,
        funcs,
        _schema_version,
        clang_vtable_facts_reliable_value,
        clang_va_list_facts_reliable_value,
        ast_producer_value,
        # Which producers this document actually evidences -- recorded (never
        # inferred) header provenance, plus its own platform. A debug block
        # names no producer (BTF/CTF/PDB all write into the DWARF blocks), so
        # none is credited from one. See
        # storage/fact_backfill.evidenced_producers for why "which backend
        # could produce this fact" is the wrong question here.
        evidenced=evidenced_producers(
            header_provenance_confirmed=from_headers and not from_headers_inferred,
            ast_producer=ast_producer_value,
            platform=d.get("platform"),
        ),
        variables=variables,
        enums=enums,
        header_cv_facts_reliable_value=header_cv_facts_reliable_value,
        clang_field_initializer_facts_reliable_value=clang_field_initializer_facts_reliable_value,
        clang_deprecation_facts_reliable_value=clang_deprecation_facts_reliable_value,
        clang_restrict_facts_reliable_value=clang_restrict_facts_reliable_value,
        castxml_var_access_facts_reliable_value=castxml_var_access_facts_reliable_value,
        param_kind_facts_reliable_value=param_kind_facts_reliable_value,
        # T9 / ADR-063 Phase 6 item 4 (legacy-hybrid backfill blocker): the
        # raw per-declaration provenance map, same source as the
        # `AbiSnapshot.fact_provenance` field constructed further below --
        # read here too since this call happens before that field exists.
        fact_provenance=dict(d.get("fact_provenance", {})),
    )

    # ADR-050 D1 (schema v12) — profile/scope fingerprints. Missing key (every
    # snapshot predating this field) loads as None, same as every other
    # additive optional field.
    contract = extraction_contract_from_dict(d.get("contract"))
    # ADR-050 D5 (G32 Phase D) — resolved SYCL/DPC++ "host"/"device" kind.
    # Missing key (every pre-Phase-D snapshot) loads as None (Codex review:
    # snapshot_to_dict already writes this field, but snapshot_from_dict
    # never read it back, so a persisted/cached host-vs-device snapshot
    # silently lost the tag on every round-trip).
    raw_frontend_context_kind = d.get("frontend_context_kind")
    frontend_context_kind = (
        raw_frontend_context_kind
        if isinstance(raw_frontend_context_kind, str)
        else None
    )
    # Schema v18 — dependency-scoping mode. Missing key (every pre-v18
    # snapshot) or an explicit ``null`` (a current-schema snapshot that never
    # went through the tagging wrapper) both load as None, same as every
    # other additive optional field; see AbiSnapshot.dependency_scope's own
    # docstring for why the comparability gate does NOT treat None as either
    # mode. A *present, non-null* value that isn't one of the two values a
    # producer can actually write is a different case (Codex review,
    # second round): the comparability gate deliberately lets a None side
    # through unchecked (an untagged legacy snapshot has no way to recover
    # its real mode), so silently downgrading a corrupt/hand-edited value
    # (e.g. a "filterd" typo) to None would let it exploit that same
    # leniency and bypass a real filtered-vs-full mismatch instead of
    # failing loudly.
    raw_dependency_scope = d.get("dependency_scope")
    if raw_dependency_scope is not None and raw_dependency_scope not in (
        "filtered",
        "full",
    ):
        raise SnapshotError(
            f"invalid dependency_scope {raw_dependency_scope!r} in snapshot "
            "-- expected 'filtered', 'full', or the key to be absent/null; "
            "this snapshot is corrupt or was hand-edited."
        )
    dependency_scope = raw_dependency_scope

    snap = AbiSnapshot(
        library=d["library"],
        version=d["version"],
        source_path=d.get("source_path"),
        source_mtime=d.get("source_mtime"),
        source_mtime_epoch=d.get("source_mtime_epoch", False),
        source_size=d.get("source_size"),
        functions=funcs,
        variables=variables,
        types=types,
        enums=enums,
        typedefs=typedefs,
        typedefs_qualified=typedefs_qualified,
        typedef_entity_ids=sidecar_entity_ids["typedef_entity_ids"],
        constant_entity_ids=sidecar_entity_ids["constant_entity_ids"],
        elf=elf,
        pe=pe,
        macho=macho,
        dwarf=dwarf,
        dwarf_advanced=dwarf_advanced,
        sycl=sycl,
        kabi=kabi,
        numpy_capi=numpy_capi,
        python_ext=python_ext,
        python_api=python_api,
        elf_only_mode=elf_only_mode,
        from_headers=from_headers,
        from_headers_inferred=from_headers_inferred,
        # Which L2 header-AST backend produced this snapshot ("castxml" |
        # "clang"); missing on older snapshots loads as None, which
        # correctly fails _both_castxml_backed (Codex review, PR #582 —
        # this was omitted entirely, so every persisted-then-reloaded
        # castxml snapshot silently lost the tag and permanently disabled
        # all 8 detectors gated on it).
        ast_producer=ast_producer_value,
        ast_toolchain=ast_toolchain,
        ast_fallback_reason=ast_fallback_reason,
        ast_toolchain_supported=ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=ast_toolchain_unsupported_reasons,
        frontend_context_kind=frontend_context_kind,
        dependency_scope=dependency_scope,
        ast_resolved_standard=ast_resolved_standard,
        **decode_snapshot_facts(d, _schema_version),
        ast_cplusplus_macro=ast_cplusplus_macro,
        ast_compile_args=ast_compile_args,
        ast_sysroot=ast_sysroot,
        dwarf_layout_coherence=dwarf_layout_coherence,
        dwarf_layout_coherence_mismatches=dwarf_layout_coherence_mismatches,
        # See header_cv_facts_reliable_value's computation above: prefers an
        # explicit dict key (round-trip stability) and otherwise derives
        # from schema_version scoped to the CastXML header path specifically
        # (Codex review, PR #582).
        header_cv_facts_reliable=header_cv_facts_reliable_value,
        # See clang_deprecation_facts_reliable_value's computation above:
        # prefers an explicit dict key (round-trip stability) and otherwise
        # derives from schema_version, scoped to the clang-producer path
        # specifically (Codex review, fresh evidence).
        clang_deprecation_facts_reliable=clang_deprecation_facts_reliable_value,
        # See clang_field_initializer_facts_reliable_value's computation
        # above: prefers an explicit dict key, falling back to a
        # schema_version + producer derivation.
        clang_field_initializer_facts_reliable=(
            clang_field_initializer_facts_reliable_value
        ),
        # See clang_vtable_facts_reliable_value's computation above: prefers
        # an explicit dict key, falling back to a schema_version + producer
        # derivation scoped to the direct-clang path specifically.
        clang_vtable_facts_reliable=clang_vtable_facts_reliable_value,
        # See clang_restrict_facts_reliable_value's computation above: prefers
        # an explicit dict key, falling back to a schema_version + producer
        # derivation covering the clang and hybrid paths alike.
        clang_restrict_facts_reliable=clang_restrict_facts_reliable_value,
        # See clang_va_list_facts_reliable_value's computation above: prefers
        # an explicit dict key, falling back to a schema_version + producer
        # derivation scoped to the "clang" producer specifically (NOT
        # "hybrid" — see the field's own docstring).
        clang_va_list_facts_reliable=clang_va_list_facts_reliable_value,
        # See castxml_var_access_facts_reliable_value's computation above:
        # prefers an explicit dict key, falling back to a schema_version +
        # producer derivation scoped to the "castxml" producer specifically.
        castxml_var_access_facts_reliable=castxml_var_access_facts_reliable_value,
        # See param_kind_facts_reliable_value's computation above: prefers an
        # explicit dict key, falling back to a schema_version + from_headers
        # derivation covering both header-AST backends alike.
        param_kind_facts_reliable=param_kind_facts_reliable_value,
        # G28 Phase 3 — per-fact provenance map for a hybrid (castxml+clang
        # merged) snapshot. Absent on every non-hybrid / pre-Phase-3 snapshot,
        # loads as the empty dict (same "unknown" default as a fresh snapshot).
        fact_provenance=dict(d.get("fact_provenance", {})),
        constants=d.get("constants", {}),
        platform=d.get("platform"),
        # Schema v44 -- see AbiSnapshot.header_only's own docstring. Missing
        # key (every pre-v44 snapshot) loads as False, same as every other
        # additive boolean field.
        header_only=bool(d.get("header_only", False)),
        # Schema v47 -- see AbiSnapshot.excluded_header_patterns. Absent on
        # every pre-v47 snapshot, which loads as "no exclusions", matching
        # what every such snapshot actually was: the flag did not exist.
        excluded_header_patterns=tuple(d.get("excluded_header_patterns") or ()),
        language_profile=d.get("language_profile"),
        scope_fallback=d.get("scope_fallback"),
        dependency_info=dep_info,
        # Provenance metadata (v4)
        git_commit=d.get("git_commit"),
        git_tag=d.get("git_tag"),
        created_at=d.get("created_at"),
        build_id=d.get("build_id"),
        # Build-mode capture (v5)
        build_mode=build_mode,
        # Evidence-pack reference (v7)
        build_source_pack=build_source_pack,
        # Inline embedded build-info/source facts (single-artifact UX)
        build_source=build_source,
        # Build-context parse provenance (v7, ADR-029) — absent on older
        # snapshots loads as False.
        parsed_with_build_context=bool(d.get("parsed_with_build_context", False)),
        # ADR-039 — active build-time define set (context-free dumps: empty).
        build_context_defines=set(d.get("build_context_defines", [])),
        # ADR-039 — {type: {field: {guard, type, is_bitfield, bitfield_bits}}}
        # registry of conditional record fields (full declaration, not just guard).
        conditional_fields={
            str(t): {str(fn): dict(decl) for fn, decl in fields.items()}
            for t, fields in dict(d.get("conditional_fields", {})).items()
        },
        # ADR-050 D1 — extraction-contract fingerprints (v12).
        contract=contract,
    )
    decode_surface_graph(d, snap)  # storage/surface_graph_codec.py (v29)
    decode_semantic_ir(d, snap)  # storage/semantic_ir_codec.py (v38)
    # ADR-063 Track T3: decode_semantic_ir() mutates snap.semantic_ir directly,
    # after AbiSnapshot.__post_init__ already ran (with semantic_ir still None
    # at that point) -- so a loaded snapshot's own Track T3 consistency check
    # never ran at all without this second, explicit call (Codex review).
    assert_snapshot_semantic_ir_consistent(snap)
    return snap, _schema_version


def finalize_snapshot(snap: AbiSnapshot) -> AbiSnapshot:
    """The storage-legal tail of the historical ``snapshot_from_dict``
    pipeline, run after the facade's own workflows/policy steps
    (``backfill_python_ext_from_evidence``, the degraded-facts warning) have
    already run against the :func:`decode_snapshot` result -- see this
    module's own docstring for why those two steps cannot live here.
    """
    backfill_missing_elf_binding(snap)
    normalize_anonymous_type_spellings_on_load(snap)
    return closure_identity.renumber_anonymous_closure_identities(snap)


def load_snapshot_document(path: str | Path) -> dict[str, Any]:
    """*path*'s flat, `snapshot_to_dict()`-shaped document — the raw dict,
    not a typed `AbiSnapshot` (`load_snapshot`'s own return). For a
    document-only key `AbiSnapshot` itself does not carry (e.g. a real
    `dump`'s own `dump_provenance`, folded in by the CLI after
    `snapshot_to_dict()` already ran) rather than any real snapshot field.

    Transparently unwraps the single-file sectioned shape
    (`storage.sectioned_document`, Phase 8 redesign) the same way
    `snapshot_from_dict` does internally, so a caller never needs to know
    which of the two on-disk shapes *path* actually is.
    """
    from ..snapshot_io import read_snapshot_text

    parsed: Any = json.loads(read_snapshot_text(path))
    # json.loads() can return a list/str/number/bool/None for arbitrary
    # JSON text -- this function's own dict[str, Any] contract (and
    # is_sectioned_document's "sections" key lookup below) both assume a
    # JSON object, so a non-dict root must fail loudly here rather than
    # surface as a confusing downstream AttributeError/KeyError, or (for a
    # list/str, where `in` is still syntactically valid but semantically
    # wrong) silently misclassify as flat/sectioned (Codex review).
    if not isinstance(parsed, dict):
        raise SnapshotError(
            f"{path}: expected a JSON object at the document root, got "
            f"{type(parsed).__name__}"
        )
    if is_sectioned_document(parsed):
        return from_sectioned_document(parsed)
    return parsed


def save_snapshot(
    snap: AbiSnapshot,
    path: str | Path,
    *,
    compression: str = "auto",
) -> None:
    """Save *snap* to *path*.

    *compression* is one of ``"auto"`` (default; inferred from *path*'s
    canonical suffix — ``.json.gz``/``.json.zst``, else plain),
    ``"none"``, ``"gzip"``, or ``"zstd"`` (ADR-059). Keyword-only so every
    existing positional ``save_snapshot(snap, path)`` call keeps working
    unchanged.

    Returns nothing, matching the historical signature; use
    :func:`write_snapshot` for the richer :class:`SnapshotWriteResult`.
    """
    write_snapshot(snap, path, compression=compression)


def write_snapshot(
    snap: AbiSnapshot,
    path: str | Path,
    *,
    compression: str = "auto",
    zstd_level: int | None = None,
) -> SnapshotWriteResult:
    """Save *snap* to *path* and return a :class:`SnapshotWriteResult`
    (compression used, decoded/stored sizes, stored digest) — ADR-059.
    """
    from ..snapshot_io import SnapshotCompression, write_snapshot_text

    return write_snapshot_text(
        snapshot_to_json(snap),
        path,
        compression=SnapshotCompression(compression),
        zstd_level=zstd_level,
    )

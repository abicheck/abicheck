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

"""Serialization helpers — AbiSnapshot ↔ JSON."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from .bundle_facts import BundleFacts
    from .snapshot_io import SnapshotWriteResult
from . import qualified_name_segments
from .errors import IncompatibleSnapshotSchemaError, SnapshotError
from .model import (
    AbiSnapshot,
    AccessLevel,
    DependencyInfo,
    ElfVisibility,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    ScopeOrigin,
    SymbolBinding,
    TypeField,
    Variable,
    Visibility,
)
from .snapshot_platform_blocks import (
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
from .storage.entity_id_codec import decode_entity_ids, encode_entity_ids
from .storage.enum_codec import encode_platform_enums
from .storage.fact_codec import (
    apply_legacy_fact_backfill,
    decode_enum_facts,
    decode_fact,
    decode_function_facts,
    decode_record_facts,
    decode_variable_facts,
    encode_fact_fields,
)
from .storage.snapshot_load_normalization import (
    backfill_missing_elf_binding,
    build_mode_from_dict,
    extraction_contract_from_dict,
    normalize_anonymous_type_spellings_on_load,
)
from .storage.surface_graph_codec import decode_surface_graph, encode_surface_graph

# Current schema version for snapshot serialization.
# Increment this whenever the snapshot format changes in a backward-incompatible way.
# v1: initial format (pre-schema-versioning; snapshots without schema_version are treated as v1)
# v2: schema_version field added (PR #89)
# v3: pe/macho metadata fields added (multi-format support)
# v4: provenance metadata (git_commit, git_tag, created_at, build_id)
# v5: build_mode capture (compiler/stdlib/std normalization)
# v6: declaration provenance (source_header + origin on functions/variables/types/enums; ADR-015)
# v7: optional evidence_pack reference (ADR-028; lightweight ref to an out-of-band pack)
# v8: pack ref key renamed evidence_pack→build_source_pack + optional inline-embedded
#     build_source payload (single-artifact UX, PR #356). The bump is deliberate:
#     a v7-only reader knows only the old evidence_pack key, so without it a v8
#     snapshot's renamed provenance would be silently dropped — bumping makes such
#     readers reject the format (forward-version error) instead of misreading it.
# v9: CastXML field const/volatile/mutable facts (TypeField.is_const/
#     is_volatile/is_mutable) and full CV-qualifier type spelling became
#     reliably populated (previously silently dead — see CHANGELOG). Unlike
#     earlier bumps, a pre-v9 snapshot is not merely missing a key — it has
#     real but WRONG data (permanently False booleans, qualifier-less type
#     spelling) that reads identically to a genuine "not const"/"not
#     volatile" fact. `snapshot_from_dict` marks such a snapshot's
#     `AbiSnapshot.header_cv_facts_reliable` False so the affected detectors
#     in diff_types.py can skip it, instead of misreporting a false
#     FIELD_BECAME_CONST/VOLATILE/MUTABLE or TYPE_FIELD_TYPE_CHANGED purely
#     from a tool upgrade comparing a legacy snapshot to a fresh dump of
#     unchanged headers (Codex review, PR #582).
# v10: `--ast-frontend hybrid` (G28 Phase 3) — `AbiSnapshot.ast_producer` can
#     now be `"hybrid"`, and `AbiSnapshot.fact_provenance` records per-fact
#     producer for a snapshot that mixes castxml- and clang-backed
#     declarations. A pre-v10 reader's own detector code has no concept of
#     per-fact provenance at all (it gates purely on whole-snapshot
#     `from_headers`, which a hybrid snapshot also satisfies) — reading a v10
#     hybrid snapshot with pre-v10 code can misread a legitimate producer
#     coverage gap (e.g. a clang-only function's placeholder default value)
#     as a real removal, exactly the false positive the provenance map exists
#     to prevent. Bumping bumps the version-mismatch `UserWarning` in
#     `snapshot_from_dict` for such a reader, giving a visible "upgrade
#     abicheck" signal instead of silence (Codex review).
# v11: persist the resolved header-AST executable/compiler identity and an
#     explicit CastXML→Clang fallback reason.  This makes producer changes
#     observable in saved baselines instead of only in transient logs.
# v12: `Function.hidden_friend_owner` — the qualified name of the class whose
#     body declares an in-class `friend` (resolved from castxml's
#     `befriending` attribute). Lets surface classification demote a hidden
#     friend whose owner lives in a system/private header instead of always
#     retaining it (previously an unconditional exemption keyed only on
#     ChangeKind — see AGENTS.md P0 "hidden-friend origin-before-exemption").
#     Purely additive: a pre-v12 reader loads `hidden_friend_owner` as None,
#     which only means the origin-based demotion cannot fire for that
#     snapshot — the finding stays retained (conservative fallback), never
#     silently mis-demoted.
# v13: `AbiSnapshot.ast_toolchain_supported` / `ast_toolchain_unsupported_reasons`
#     — the outcome of the CastXML version gate (`castxml_policy.py`) run
#     before headers were parsed. Purely additive: a pre-v13 reader loads both
#     as their defaults (None / []), i.e. "gate outcome unknown" — the same
#     conservative default a fresh in-memory snapshot has before any gate ran.
# v14: ADR-050 D1 — ``AbiSnapshot.contract`` (profile/scope fingerprints
#     proving the extraction contract two snapshots were compared under).
#     Unlike every earlier bump, this one is *verdict-blocking*: a reader
#     that doesn't recognize ``contract`` would silently compare two
#     possibly-incomparable snapshots and produce an ordinary, wrong verdict
#     — exactly the failure mode ADR-050 exists to close. See
#     ``_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`` below:
#     ``snapshot_from_dict``'s hard-rejection guard protects any reader BUILT
#     FROM THIS COMMIT ONWARD whose own ``SCHEMA_VERSION`` constant is below
#     a future verdict-blocking bump's threshold — it cannot, and structurally
#     never could, retroactively protect an already-released pre-v14 install
#     (e.g. a deployed abicheck whose ``SCHEMA_VERSION`` is 13): such a reader
#     simply does not contain this guard's code at all, so it falls through
#     to the ordinary warn-and-continue path every earlier additive bump got,
#     silently drops the unrecognized ``contract`` key, and produces an
#     ordinary verdict (Codex review, PR #624) — no in-band schema-version
#     change can close that gap for code that already shipped without it.
#     `checker.compare`'s ``contract_coverage="partial"`` disclosure (ADR-050
#     D2) is the mitigation available for exactly this case -- but it comes
#     from whichever *v14-aware* `compare()` later evaluates the resulting
#     pair, never from the pre-v14 reader that did the dropping (that reader
#     predates the coverage logic too, and stays just as unaware of the drop
#     as it was of ``contract`` itself; Codex review, PR #624). A pair where
#     one side's contract is missing -- whether dropped by an old re-save or
#     never populated -- is reported as partially covered rather than
#     silently full, once a current reader does the comparing. As of this
#     PR no real producer populates ``contract`` yet (``dumper.py`` wiring
#     is separate, later work), so there is no snapshot in the wild today
#     for an old reader to mis-handle.
# v15: `AbiSnapshot.ast_resolved_standard` / `ast_cplusplus_macro` /
#     `ast_compile_args` / `ast_sysroot` — structured compile-context
#     provenance for the header-AST parse (P1 toolchain-profile audit):
#     the resolved C/C++ standard actually used (explicit or heuristic-forced
#     "gnu++20"), its standard-mandated `__cplusplus` literal, the ordered
#     extra compiler arguments, and the sysroot. Purely additive (not
#     verdict-blocking like v14's `contract`): a pre-v15 reader loads all
#     four as their defaults (None / None / () / None), i.e. "not recorded"
#     — never mistaken for "the frontend's bare default was explicitly
#     confirmed". Bumped to v15 rather than folded into v14 because v14
#     (ADR-050 D1's `contract`) had already shipped on `main` independently
#     of this work; reusing the same integer for two unrelated additions
#     would make "schema_version 14" ambiguous about which fields a given
#     snapshot actually carries.
# v16: `AbiSnapshot.dwarf_layout_coherence` / `dwarf_layout_coherence_mismatches`
#     (P0 evidence-coherence audit) — observability over
#     `dumper_layout_backfill.backfill_dwarf_layout`'s existing DWARF-vs-
#     header-AST corroboration (unchanged: it already refused to merge an
#     uncorroborated record's layout before this bump, so no snapshot
#     gained different *data*, only a visible record of that refusal).
#     `dwarf_layout_coherence` is one of "matched"/"partial"/"mismatch"/
#     "unavailable", or `None` on any snapshot not built via the clang L2
#     backend (castxml computes layout directly — not a coherence question)
#     — see `AbiSnapshot`'s own field docstring. Purely additive: a pre-v16
#     reader loads both as their conservative "not recorded" defaults
#     (`None` / `()`).
# v17: `AbiSnapshot.frontend_context_kind` (ADR-050 D5, G32 Phase D) — which
#     SYCL/DPC++ AST pass ("host"/"device") a header-AST snapshot was built
#     from, threaded through from `sycl_context.py`'s selector. Purely
#     additive: a pre-v17 reader loads it as its default (`None`), i.e. "not
#     recorded" — the same conservative default a non-SYCL snapshot always
#     had. Bumped (rather than left at v16) so a pre-v17 reader gets the
#     usual version-mismatch `UserWarning` instead of silently discarding
#     this provenance on re-save, matching every other purely-additive bump
#     from v9 onward (Codex review, PR #636).
# v18: `AbiSnapshot.dependency_scope` — records whether `dump`'s default
#     toolchain/system-header exclusion (`dumper_scoping.py`) was applied
#     ("filtered") or opted out of via `--include-system-declarations` ("full").
#     Purely additive: a pre-v18 reader loads it as its default (`None`),
#     i.e. "not recorded". `comparability.check_contracts_comparable`
#     deliberately does NOT treat that `None` as either mode -- an ordinary
#     pre-v18 baseline is usually already-filtered content that simply
#     predates the tag, so assuming "full" would spuriously flag the single
#     most common workflow (compare a cached baseline against a fresh
#     dump). The gate only raises when BOTH sides carry an explicit,
#     differing tag; a `None` on either side is left unchecked on this axis
#     (see `AbiSnapshot.dependency_scope`'s own docstring for the full
#     reasoning).
# v19: the direct-clang (`--ast-frontend clang`) L2 header backend started
#     populating `Function`/`Variable`/`TypeField`/`RecordType`/
#     `EnumType.deprecated` and `EnumType.is_scoped` (G31 Phase C -- see
#     `dumper_clang._clang_deprecated_message`/the enum "scopedEnumTag"
#     handling), both previously castxml-only. Like v9's CV facts, a pre-v19
#     CLANG-producer snapshot has real but WRONG data here -- every one of
#     these fields is unconditionally `None`/`False`, indistinguishable by
#     value alone from a genuine "not deprecated"/"not scoped" fact. Unlike
#     v9 (scoped to castxml), this only affects the `clang`/`hybrid`
#     producer path -- a pre-v19 CASTXML-producer snapshot's own value for
#     these facts was always reliable (G28 Phase 1). `snapshot_from_dict`
#     marks such a snapshot's `AbiSnapshot.clang_deprecation_facts_reliable`
#     False so `fact_provenance.fact_producer` can decline to trust it,
#     instead of misreporting a false `*_DEPRECATED_ADDED`/`ENUM_BECAME_SCOPED`
#     purely from a tool upgrade comparing a legacy clang-backed snapshot to
#     a fresh dump of unchanged headers (Codex review, fresh evidence).
# v20: the direct-clang L2 header backend started populating
#     `TypeField.default` (the default member initializer) — G31 Phase C's
#     last remaining fact-completeness gap that backend can actually close,
#     see `dumper_clang_expr._field_initializer_value`. Exactly v19's shape, one
#     version later and for one more fact: a pre-v20 CLANG-producer
#     snapshot's `TypeField.default` is unconditionally `None`, and that
#     field's own docstring makes `None` mean "no initializer" AND "this
#     dumper doesn't capture it" alike — so the stale value is real but
#     WRONG, not merely absent, and cannot be told from a genuine
#     no-initializer field by value. `snapshot_from_dict` marks such a
#     snapshot's `AbiSnapshot.clang_field_initializer_facts_reliable` False
#     so `fact_provenance.fact_producer` declines to trust it, instead of
#     reporting a false `FIELD_DEFAULT_INITIALIZER_REMOVED` for every
#     defaulted field purely from a tool upgrade. Tracked as its own flag
#     rather than reusing v19's: a v19 snapshot has reliable
#     deprecated/is_scoped but unreliable field defaults, which one shared
#     flag could not express.
#   21 — G31 Phase C (dumper_clang_vtable.py): the direct-clang backend's
#     RecordType.vtable/vptr_offset_bits became real, reconstructed facts
#     instead of an unconditional vtable=[]/vptr_offset_bits=None stub for
#     EVERY record. A pre-v21, clang-producer record's blanket-empty vtable
#     is real-but-WRONG, not merely absent, and cannot be told from a
#     genuine non-polymorphic class by value alone. `snapshot_from_dict`
#     marks such a snapshot's `AbiSnapshot.clang_vtable_facts_reliable`
#     False so `diff_types`/`diff_layout` decline to trust it, instead of
#     reporting a false `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED` for every
#     already-polymorphic class purely from a tool upgrade (Codex review,
#     fresh evidence, real end-to-end repro against a persisted schema-v20
#     clang snapshot).
#   22 — G31 Phase C (`dumper_clang._clang_param_is_restrict`): the
#     direct-clang backend's `Param.is_restrict` became a real extracted fact
#     instead of the model default `False` for EVERY parameter. castxml had
#     been the only producer of it since it shipped, so this also closes a
#     live cross-backend false positive (`_diff_param_restrict` compares the
#     two bools with no producer gate). Same real-but-WRONG shape as v19-v21:
#     `is_restrict` is a plain bool with no "not collected" state, so a
#     pre-v22 clang/hybrid-producer parameter's blanket False cannot be told
#     from a genuinely unqualified parameter by value alone.
#     `snapshot_from_dict` marks such a snapshot's
#     `AbiSnapshot.clang_restrict_facts_reliable` False so
#     `diff_symbols._diff_param_restrict` declines to trust it, instead of
#     reporting a false `PARAM_RESTRICT_CHANGED` for every restrict-qualified
#     parameter purely from a tool upgrade.
#   23 — G31 Phase C continued (`dumper_clang_qualifiers._clang_param_is_va_list`):
#     the direct-clang backend's `Param.is_va_list` became a real extracted
#     fact (x86-64 System V spelling only) instead of the model default
#     `False` for EVERY parameter. No backend had ever populated this fact
#     before (unlike `is_restrict`, castxml never did either — see
#     `diff_param_qualifiers.param_va_list_changes`'s pre-v23 docstring),
#     so this is purely a producer *gaining* the fact, not a cross-backend
#     disagreement, but the schema-version risk is identical: a pre-v23
#     clang-producer parameter's blanket False cannot be told from a
#     genuine non-`va_list` parameter by value alone. `snapshot_from_dict`
#     marks such a snapshot's `AbiSnapshot.clang_va_list_facts_reliable`
#     False so `diff_symbols._diff_param_va_list` declines to trust it,
#     instead of reporting a false `PARAM_BECAME_VA_LIST` for every
#     `va_list` parameter purely from a tool upgrade.
#   24 — G31 Phase C continued (`dumper_castxml._CastxmlParser._access_level`
#     applied to `Variable`, and `el.get("init")` applied to `Variable.value`):
#     the castxml backend's `Variable.access`/`Variable.value` became real
#     extracted facts instead of the model defaults (`AccessLevel.PUBLIC`,
#     `None`) for EVERY variable. `Variable.value` needs no reliability flag
#     — `diff_types_abicc_parity._diff_var_values` already declines per-pair
#     unless BOTH sides are non-`None`, so a legacy blanket-`None` side is
#     silently skipped, never misread. `Variable.access` has no such
#     "unknown" state (a plain enum, PUBLIC by construction), so the same
#     real-but-WRONG shape as v19-v23 applies: `snapshot_from_dict` marks
#     such a snapshot's `AbiSnapshot.castxml_var_access_facts_reliable`
#     False so `diff_symbols._diff_var_access` declines to trust it, instead
#     of reporting a false `VAR_ACCESS_WIDENED` for every private/protected
#     static member purely from a tool upgrade.
#   25 — G31 Phase C continued: `AbiSnapshot.typedefs_qualified`, a
#     fully-qualified-name-keyed twin of `typedefs` populated by both header
#     backends, added to close the bare-name collision gap documented in
#     AGENTS.md's "Known gaps" (two member typedefs sharing a bare spelling
#     in different classes/namespaces silently overwrote one another in
#     `typedefs`). Needs no reliability flag, unlike v19-v23 above: an empty
#     dict is not "real but wrong" here the way a blanket `False`/`None`
#     scalar was for those facts — a pre-v25 snapshot's empty
#     `typedefs_qualified` is exactly the same value a v25+ snapshot with no
#     typedefs at all would carry, and every consumer already treats the
#     unqualified `typedefs` dict as the fallback source of truth, so
#     "empty" degrades cleanly to "no extra qualified-identity data
#     available" rather than being misread as a real fact.
#   26 — ADR-063 Phase 0: `Fact[T]` siblings for `RecordType.bases_fact`/
#     `virtual_bases_fact`/`vtable_fact`/`vptr_offset_bits_fact` and
#     `Param.is_va_list_fact` — see `storage/fact_codec.py`.
#   27 — `Function.is_compiler_generated`: closes the castxml L4 extractor
#     bug documented in AGENTS.md's "PR C" known-gaps entry, where a
#     compiler-synthesized implicit special member (or a synthesized
#     `operator=`, which castxml gives a real-looking Itanium mangled name)
#     leaked into the L4 source-ABI extractor's reachable declaration
#     surface as if it were genuine public API. Needs no reliability flag,
#     unlike v19-v23: `None` (a pre-v27 snapshot's default) is exactly
#     "not captured", never a real-but-wrong scalar — `entity_from_
#     function`'s own `api_relevant` exclusion only fires on a confirmed
#     `True`, so an older snapshot degrades cleanly to today's (buggy)
#     inclusive behavior rather than being misread as "confirmed
#     user-written".
#
# Reading an OLDER snapshot (the direction every CI baseline actually hits —
# a baseline is committed once and outlives however many abicheck pin bumps
# happen before it's next regenerated) used to be entirely silent whenever it
# degraded one of the `*_facts_reliable` flags above: the flag itself was set
# correctly, but nothing surfaced that fact to whoever was reading the
# comparison. `snapshot_from_dict` now emits a `UserWarning` naming exactly
# which flags got degraded, once, at load time — but only when the version
# gap actually degraded something; a trivial one-version-behind snapshot that
# doesn't hit any producer-specific threshold above stays silent, since every
# CI baseline is *always* some number of versions behind and warning
# regardless of relevance would just be noise.
SCHEMA_VERSION: int = 34  # v34: Function.contract_attributes_fact/is_explicit_fact/is_hidden_friend_fact/source_header_fact/is_variadic_fact/exception_spec_fact/is_override_fact/hidden_friend_owner_fact/elf_binding_fact/is_compiler_generated_fact persisted (storage/fact_codec.py); v33: Variable.source_header_fact/alignment_bits_fact/elf_binding_fact persisted (storage/fact_codec.py); v32: EnumType.qualified_name_fact/source_header_fact persisted (storage/fact_codec.py); v31: RecordType.is_abstract_fact/data_size_bits_fact/is_standard_layout_fact/is_trivially_copyable_fact/qualified_name_fact/source_header_fact persisted (storage/fact_codec.py); v30: RecordType.is_final_fact persisted (storage/fact_codec.py); v29: AbiSnapshot.surface_graph persisted (storage/surface_graph_codec.py); v28: entity_id carrier persisted (storage/entity_id_codec.py).

# Schema version at which CastXML field CV facts became reliable (see v9 above).
_MIN_SCHEMA_VERSION_FOR_CV_FACTS = 9

# Schema version at which the direct-clang backend's deprecated/is_scoped
# facts became reliable (see v19 above).
_MIN_SCHEMA_VERSION_FOR_CLANG_DEPRECATION_FACTS = 19

# Schema version at which the direct-clang backend's TypeField.default facts
# became reliable (see v20 above).
_MIN_SCHEMA_VERSION_FOR_CLANG_FIELD_INITIALIZER_FACTS = 20

# Schema version at which the direct-clang backend's RecordType.vtable/
# vptr_offset_bits facts became reliable (see v21 above).
_MIN_SCHEMA_VERSION_FOR_CLANG_VTABLE_FACTS = 21

# Schema version at which the direct-clang backend's Param.is_restrict facts
# became reliable (see v22 above).
_MIN_SCHEMA_VERSION_FOR_CLANG_RESTRICT_FACTS = 22

# Schema version at which the direct-clang backend's Param.is_va_list facts
# became reliable (see v23 above).
_MIN_SCHEMA_VERSION_FOR_CLANG_VA_LIST_FACTS = 23

# Schema version at which the castxml backend's Variable.access facts
# became reliable (see v24 above).
_MIN_SCHEMA_VERSION_FOR_CASTXML_VAR_ACCESS_FACTS = 24

# ADR-050 D1 — the schema version at which a verdict-blocking field
# (``AbiSnapshot.contract``) was first introduced. This constant only takes
# effect inside code that already contains this guard (this commit onward);
# it cannot retroactively make an already-released, pre-this-commit reader
# (whose own code simply doesn't have this check) hard-reject — that reader
# falls through to its old warn-and-continue path regardless of what this
# constant says (Codex review, PR #624; see the v14 note above for the full
# scope of what this guard can and cannot protect). Within code that DOES
# contain this guard, ``snapshot_from_dict`` raises IncompatibleSnapshotSchemaError
# whenever the snapshot's version is BOTH newer than this reader's own
# SCHEMA_VERSION AND at or above this threshold — not merely "this reader
# predates the threshold," which would stop protecting the moment a reader's
# own SCHEMA_VERSION reaches it.
_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION = 14


def _sets_to_lists(obj: Any) -> Any:
    """Recursively convert any set to a sorted list for JSON serialization.

    dataclasses.asdict() does NOT convert set → list, so json.dumps() would
    raise TypeError. This post-processes the entire dict tree.
    """
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _sets_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sets_to_lists(v) for v in obj]
    return obj


def snapshot_to_dict(snap: AbiSnapshot) -> dict[str, Any]:
    # asdict() would recursively copy the lazy lookup caches, and
    # surface_graph's potentially-large nodes/edges, for nothing --
    # encode_surface_graph() below unconditionally replaces the latter with
    # its own to_dict(), never this recursion (Codex review, PR #962). Clear
    # them for the call and restore after, so this stays pure from the caller.
    caches = (snap._func_by_mangled, snap._var_by_mangled, snap._type_by_name)
    graph = snap.surface_graph
    try:
        snap._func_by_mangled = snap._var_by_mangled = snap._type_by_name = None
        snap.surface_graph = None
        d = asdict(snap)
    finally:
        snap._func_by_mangled, snap._var_by_mangled, snap._type_by_name = caches
        snap.surface_graph = graph
    d.pop("_func_by_mangled", None)
    d.pop("_var_by_mangled", None)
    d.pop("_type_by_name", None)
    # Runtime-only provenance qualifier — never persisted.
    d.pop("from_headers_inferred", None)
    # If ``from_headers`` was only *inferred* (a legacy snapshot loaded without
    # the explicit key), do not persist it as explicit provenance: drop the key
    # so a reload re-runs the same inference and re-marks it inferred, rather
    # than promoting a guess to explicit provenance and re-enabling source-level
    # param-rename detection on DWARF-only baselines this is meant to suppress.
    if snap.from_headers_inferred:
        d.pop("from_headers", None)

    # ElfMetadata/PeMetadata/MachoMetadata enums -> strings (storage/enum_codec.py).
    encode_platform_enums(d)

    # ADR-063 Phase 0 (schema v26): see storage/fact_codec.py.
    encode_fact_fields(d)

    # Convert all sets → sorted lists (needed for AdvancedDwarfMetadata.packed_structs and ToolchainInfo.abi_flags; json.dumps raises TypeError on set objects), having first encoded the ADR-063 Phase 2 (c1) `entity_id` carrier (storage/entity_id_codec.py).
    converted: dict[str, Any] = _sets_to_lists(encode_entity_ids(d, snap))

    # BuildMode enums are (str, Enum), so dataclasses.asdict() carries
    # them through as Enum instances rather than plain strings; normalize
    # the build_mode subtree to bare strings for JSON serialization.
    bm = converted.get("build_mode")
    if isinstance(bm, dict):
        for k in ("compiler_family", "language_std", "stdlib", "glibcxx_dual_abi"):
            v = bm.get(k)
            if v is not None and not isinstance(v, str):
                bm[k] = v.value if hasattr(v, "value") else str(v)

    # The inline embedded BuildSourcePack carries Path/enum/set-bearing nested
    # models asdict() cannot faithfully serialize; replace the raw asdict output
    # with the pack's canonical inline form, or drop the key when nothing was embedded.
    if snap.build_source is not None:
        converted["build_source"] = snap.build_source.to_embedded_dict()
    else:
        converted.pop("build_source", None)
    encode_surface_graph(converted, snap)  # storage/surface_graph_codec.py

    # Embed schema version for forward-compatibility.
    # Placed at top level so loaders can inspect it without parsing the full snapshot.
    converted["schema_version"] = SCHEMA_VERSION

    return converted


def _scope_origin_or_unknown(raw: Any) -> ScopeOrigin:
    """Deserialize a ScopeOrigin, defaulting unknown/invalid values to UNKNOWN.

    A hand-edited or newer-schema snapshot may carry an origin string this
    build does not recognize; that must not abort the whole load."""
    try:
        return ScopeOrigin(raw if raw is not None else "unknown")
    except ValueError:
        return ScopeOrigin.UNKNOWN


def _enum_type_from_dict(e: dict[str, Any], schema_version: int) -> EnumType:
    return EnumType(
        name=e["name"],
        members=[
            EnumMember(name=m["name"], value=m["value"]) for m in e.get("members", [])
        ],
        underlying_type=e.get("underlying_type", "int"),
        source_location=e.get("source_location"),
        source_header=e.get("source_header"),
        origin=_scope_origin_or_unknown(e.get("origin")),
        is_scoped=e.get("is_scoped"),
        deprecated=e.get("deprecated"),
        qualified_name=e.get("qualified_name"),
        **decode_enum_facts(e, schema_version),
    )


def snapshot_to_json(snap: AbiSnapshot, indent: int = 2) -> str:
    return json.dumps(snapshot_to_dict(snap), indent=indent)


_T = TypeVar("_T")


def _sub_block(parser: Callable[[dict[str, Any]], _T], raw: Any) -> _T | None:
    """Parse one optional sub-block of a serialized snapshot, or ``None``.

    Every ``elf``/``pe``/``macho``/``dwarf``/... section of a snapshot document
    is optional and, per this module's forward-compatibility convention, is
    ignored rather than fatal when it is present but not an object. Sharing the
    one guard keeps a dozen call sites from each spelling it out.
    """
    return parser(raw) if isinstance(raw, dict) else None


def snapshot_from_dict(d: dict[str, Any]) -> AbiSnapshot:
    # Inspect schema version for future migration hooks.
    # Snapshots without schema_version are treated as v1 (pre-versioning format).
    # Currently only v1 and v2 exist and have the same on-disk layout, so no
    # migration is required.  This baseline lets future PRs add migration logic here.
    _schema_version: int = int(d.get("schema_version", 1))
    if (
        _schema_version > SCHEMA_VERSION
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
            f"(this reader supports up to schema_version {SCHEMA_VERSION}). "
            "Upgrade abicheck to read this snapshot."
        )
    if _schema_version > SCHEMA_VERSION:
        import warnings

        warnings.warn(
            f"Snapshot schema_version {_schema_version} is newer than this abicheck "
            f"(supports up to schema_version {SCHEMA_VERSION}). "
            "Data may be incomplete or misinterpreted. "
            "Upgrade abicheck to read this snapshot correctly.",
            UserWarning,
            stacklevel=2,
        )
    funcs = [
        Function(
            name=f["name"],
            mangled=f["mangled"],
            return_type=f["return_type"],
            params=[
                Param(
                    name=p.get("name", ""),
                    type=p.get("type", ""),
                    kind=ParamKind(p.get("kind", "value")),
                    default=p.get("default", None),
                    pointer_depth=p.get("pointer_depth", 0),
                    is_restrict=p.get("is_restrict", False),
                    is_va_list=p.get("is_va_list", False),
                    is_va_list_fact=decode_fact(
                        p.get("is_va_list_fact"), _schema_version
                    ),
                )
                for p in f.get("params", [])
            ],
            visibility=Visibility(f.get("visibility", "public")),
            is_virtual=f.get("is_virtual", False),
            is_noexcept=f.get("is_noexcept", False),
            vtable_index=f.get("vtable_index"),
            source_location=f.get("source_location"),
            is_static=f.get("is_static", False),
            is_const=f.get("is_const", False),
            is_volatile=f.get("is_volatile", False),
            is_pure_virtual=f.get("is_pure_virtual", False),
            is_deleted=f.get("is_deleted", False),
            # Provenance of is_deleted: True when set via DW_AT_deleted. Must be
            # rehydrated (asdict writes it) so the public-map bypass in
            # diff_symbols keeps DWARF-deleted unexported members out of the
            # public surface after a dump-to-file → compare-files round-trip,
            # rather than re-emitting FUNC_REMOVED against a stripped build.
            deleted_from_dwarf=f.get("deleted_from_dwarf", False),
            is_inline=f.get("is_inline", False),
            is_extern_c=f.get("is_extern_c", False),
            access=AccessLevel(f.get("access", "public")),
            return_pointer_depth=f.get("return_pointer_depth", 0),
            elf_visibility=ElfVisibility(f["elf_visibility"])
            if f.get("elf_visibility")
            else None,
            # Missing on an older snapshot (predates this field) → None,
            # same "not captured" default every other ELF-derived fact here
            # uses.
            elf_binding=SymbolBinding(f["elf_binding"])
            if f.get("elf_binding")
            else None,
            ref_qualifier=f.get("ref_qualifier", ""),
            # Tri-state: a missing key (older snapshot) loads as None,
            # which suppresses CTOR_EXPLICIT_ADDED/_REMOVED in the diff
            # rather than producing spurious findings from schema evolution.
            is_explicit=f.get("is_explicit"),
            # Tri-state, same rationale as is_explicit — a missing key on
            # an older snapshot loads as None and suppresses the
            # HIDDEN_FRIEND_ADDED/_REMOVED transition detector.
            is_hidden_friend=f.get("is_hidden_friend"),
            # Owner class of a hidden friend — missing on older snapshots (and
            # for non-friends) loads as None.
            hidden_friend_owner=f.get("hidden_friend_owner"),
            # Provenance (v6) — missing on older snapshots → None / UNKNOWN.
            source_header=f.get("source_header"),
            origin=_scope_origin_or_unknown(f.get("origin")),
            # Tri-state language-contract fields (coverage extension) —
            # missing keys on older snapshots load as None and suppress the
            # corresponding transition detectors.
            is_variadic=f.get("is_variadic"),
            contract_attributes=f.get("contract_attributes"),
            exception_spec=f.get("exception_spec"),
            deprecated=f.get("deprecated"),
            is_override=f.get("is_override"),
            # Tri-state (v27) — missing on a pre-v27 snapshot loads as None.
            is_compiler_generated=f.get("is_compiler_generated"),
            **decode_function_facts(f, _schema_version),
        )
        for f in d.get("functions", [])
    ]
    variables = [
        Variable(
            name=v["name"],
            mangled=v["mangled"],
            type=v["type"],
            visibility=Visibility(v.get("visibility", "public")),
            source_location=v.get("source_location"),
            is_const=v.get("is_const", False),
            value=v.get("value"),
            access=AccessLevel(v.get("access", "public")),
            elf_visibility=ElfVisibility(v["elf_visibility"])
            if v.get("elf_visibility")
            else None,
            source_header=v.get("source_header"),
            origin=_scope_origin_or_unknown(v.get("origin")),
            alignment_bits=v.get("alignment_bits"),
            deprecated=v.get("deprecated"),
            elf_binding=SymbolBinding(v["elf_binding"])
            if v.get("elf_binding")
            else None,
            **decode_variable_facts(v, _schema_version),
        )
        for v in d.get("variables", [])
    ]
    types = [
        RecordType(
            name=t["name"],
            kind=t["kind"],
            size_bits=t.get("size_bits"),
            alignment_bits=t.get("alignment_bits"),
            fields=[
                TypeField(
                    name=f["name"],
                    type=f["type"],
                    offset_bits=f.get("offset_bits"),
                    is_bitfield=f.get("is_bitfield", False),
                    bitfield_bits=f.get("bitfield_bits"),
                    is_const=f.get("is_const", False),
                    is_volatile=f.get("is_volatile", False),
                    is_mutable=f.get("is_mutable", False),
                    access=AccessLevel(f.get("access", "public")),
                    default=f.get("default"),
                    deprecated=f.get("deprecated"),
                )
                for f in t.get("fields", [])
            ],
            bases=t.get("bases", []),
            virtual_bases=t.get("virtual_bases", []),
            vtable=t.get("vtable", []),
            source_location=t.get("source_location"),
            is_union=t.get("is_union", t.get("kind") == "union"),
            is_opaque=t.get("is_opaque", False),
            is_final=t.get("is_final"),  # tri-state; absent on pre-v? snapshots → None
            is_template_pattern=t.get("is_template_pattern", False),
            has_anonymous_aggregate_fields=t.get(
                "has_anonymous_aggregate_fields", False
            ),
            source_header=t.get("source_header"),
            origin=_scope_origin_or_unknown(t.get("origin")),
            # Fine-grained layout descriptor (layout-closure work); all
            # optional/tri-state, absent on snapshots predating these fields.
            data_size_bits=t.get("data_size_bits"),
            is_standard_layout=t.get("is_standard_layout"),
            is_trivially_copyable=t.get("is_trivially_copyable"),
            vptr_offset_bits=t.get("vptr_offset_bits"),
            base_offsets=t.get("base_offsets", {}),
            qualified_name=t.get("qualified_name"),
            is_abstract=t.get("is_abstract"),
            deprecated=t.get("deprecated"),
            **decode_record_facts(t, _schema_version),
        )
        for t in d.get("types", [])
    ]
    enums = [_enum_type_from_dict(e, _schema_version) for e in d.get("enums", [])]
    decode_entity_ids(d, functions=funcs, variables=variables, types=types, enums=enums)
    typedefs: dict[str, str] = d.get("typedefs", {})
    typedefs_qualified: dict[str, str] = d.get("typedefs_qualified", {})
    elf_data = d.get("elf")
    pe_data = d.get("pe")
    macho_data = d.get("macho")
    dwarf_data = d.get("dwarf")
    dwarf_adv_data = d.get("dwarf_advanced")

    elf = _sub_block(_elf_from_dict, elf_data)
    pe = _sub_block(_pe_from_dict, pe_data)
    macho = _sub_block(_macho_from_dict, macho_data)
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
    # A snapshot dumped without the G14 key (older abicheck, or a `dump` writer
    # path that didn't attach it) has no serialized ``python_ext``. Derive it on
    # load from the already-parsed binary metadata so `dump` → `compare` never
    # silently disables the extension detector — the same recognition the dumper
    # runs, applied at read time. ``_derive_python_ext_key_absent`` records that
    # the key was missing (vs. an explicit ``null`` meaning "checked, not an
    # extension") so we only re-derive when there is no recorded answer.
    _python_ext_key_absent = "python_ext" not in d

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
        from .buildsource.model import BuildSourceRef

        build_source_pack = BuildSourceRef.from_dict(ep_raw)

    # Inline embedded build-info/source facts (single-artifact UX). Optional and
    # additive: a missing or malformed value loads as None and the compare falls
    # back to out-of-band --old/--new flags (or skips evidence entirely).
    bs_raw = d.get("build_source")
    build_source = None
    if isinstance(bs_raw, dict):
        from .buildsource.pack import BuildSourcePack

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
    if "header_cv_facts_reliable" in d:
        # Trust an explicit marker over re-deriving from schema_version: a
        # load -> snapshot_to_dict -> (save) -> load round-trip always
        # re-stamps schema_version to the CURRENT SCHEMA_VERSION (it
        # describes the writing tool's format capability, not the
        # snapshot's true field-fact origin), so re-deriving purely from
        # schema_version on a reserialized legacy snapshot would silently
        # flip an already-known-unreliable snapshot's stale, real-but-wrong
        # cv facts back to "reliable" — reintroducing the exact false
        # FIELD_BECAME_CONST/VOLATILE/TYPE_FIELD_TYPE_CHANGED positives this
        # flag exists to prevent (Codex review, PR #582).
        header_cv_facts_reliable_value = bool(d["header_cv_facts_reliable"])
    else:
        header_cv_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "clang"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CV_FACTS
        )

    if "clang_deprecation_facts_reliable" in d:
        # Same round-trip-stability reasoning as header_cv_facts_reliable
        # above: trust an explicit marker over re-deriving from
        # schema_version, since a load -> save -> load round-trip always
        # re-stamps schema_version to the CURRENT SCHEMA_VERSION.
        clang_deprecation_facts_reliable_value = bool(
            d["clang_deprecation_facts_reliable"]
        )
    else:
        # Only the clang/hybrid producer path is affected -- a castxml (or
        # a from-scratch, non-header) snapshot's own deprecated/is_scoped
        # values were always reliable (G28 Phase 1), regardless of
        # schema_version (v19 above).
        clang_deprecation_facts_reliable_value = (
            not from_headers
            or ast_producer_value != "clang"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_DEPRECATION_FACTS
        )

    if "clang_field_initializer_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the two flags above.
        clang_field_initializer_facts_reliable_value = bool(
            d["clang_field_initializer_facts_reliable"]
        )
    else:
        # Unlike clang_deprecation_facts_reliable, this covers "hybrid" too,
        # not just "clang" (Codex review, fresh evidence, second round): a
        # pre-v20 hybrid merge's clang-only-appended fields never had
        # `default` provenance stamped at all (only `deprecated` was), so an
        # absent entry for one of those fields on a legacy hybrid snapshot is
        # real-but-WRONG data, same as a legacy pure-clang snapshot's
        # unconditional None -- see AbiSnapshot.clang_field_initializer_
        # facts_reliable's own docstring for the full reasoning, including
        # why a MATCHED field's own provenance is unaffected either way.
        #
        # `ast_producer_value == "castxml"` (Codex review, fresh evidence,
        # third round), not `not in ("clang", "hybrid")`: a snapshot
        # persisted before `ast_producer` was tracked at all (e.g. schema
        # v9) has `ast_producer_value is None`, which `not in (...)` treated
        # as "definitely not clang/hybrid" -- i.e. reliable -- when it is
        # exactly the reverse. `ast_producer` has always had exactly three
        # real producers (`"clang"`/`"castxml"`/`"hybrid"`, verified against
        # every write site), so a `None` here means "unknown," not
        # "castxml." This function's own consumer,
        # `default_value_representation_unreliable`
        # (diff_default_value_reliability.py), already treats a per-
        # declaration producer of `None` as clang-family risk for the exact
        # same reason -- only `"castxml"` is excluded there too. Reproduced
        # empirically: loading a from_headers=True, schema-v9 dict with no
        # `ast_producer` key yielded `clang_field_initializer_facts_reliable
        # =True` before this fix, silently trusting a legacy direct-clang
        # snapshot's pre-stabilization `"expr:"` fingerprint and reporting a
        # false PARAM_DEFAULT_VALUE_CHANGED/FIELD_DEFAULT_INITIALIZER_CHANGED
        # against an unchanged default after upgrading. Safe for a genuine
        # legacy castxml snapshot too: castxml never produces an `"expr:"`-
        # prefixed value (it keeps the verbatim source expression instead),
        # so the reliability flag is never even consulted for one -- both
        # gate functions above check the VALUE's own `"expr:"` prefix first.
        clang_field_initializer_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "castxml"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_FIELD_INITIALIZER_FACTS
        )

    if "clang_vtable_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        clang_vtable_facts_reliable_value = bool(d["clang_vtable_facts_reliable"])
    else:
        # Only the direct-clang ("clang") producer path is affected, same as
        # clang_deprecation_facts_reliable above -- not "hybrid" too: the
        # vtable/vptr reconstruction lives entirely in dumper_clang_vtable.py,
        # a direct-clang-backend-only module never invoked by the hybrid
        # merge path, so a legacy hybrid snapshot's own vtable facts came
        # from castxml (dumper_hybrid.py's "prefer castxml" merge policy)
        # and carry no equivalent false-reliability risk. A castxml (or
        # non-header) snapshot's own vtable extraction predates this field
        # entirely, so it's always reliable regardless of schema version.
        clang_vtable_facts_reliable_value = (
            not from_headers
            or ast_producer_value != "clang"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_VTABLE_FACTS
        )

    if "clang_restrict_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        clang_restrict_facts_reliable_value = bool(d["clang_restrict_facts_reliable"])
    else:
        # Covers "hybrid" as well as "clang" -- and, like
        # clang_field_initializer_facts_reliable above, spells that as
        # `== "castxml"` rather than `not in ("clang", "hybrid")` so a
        # snapshot persisted before `ast_producer` was tracked at all (its
        # value here is None, i.e. UNKNOWN, not "castxml") is treated as
        # possibly clang-family rather than silently trusted. A hybrid
        # merge keeps castxml's `params` verbatim for every matched
        # function, so only its clang-ONLY appended functions carry clang's
        # blanket-False parameters -- but that is enough to need the flag,
        # exactly as the pre-v20 hybrid clang-only-append case did for
        # field initializers. See AbiSnapshot.clang_restrict_facts_reliable.
        clang_restrict_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "castxml"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_RESTRICT_FACTS
        )

    if "clang_va_list_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        clang_va_list_facts_reliable_value = bool(d["clang_va_list_facts_reliable"])
    else:
        # Unlike clang_restrict_facts_reliable, this does NOT special-case
        # "hybrid" as trusted — `diff_symbols._diff_param_va_list` excludes
        # "hybrid" from its producer gate entirely (Codex review; see
        # AbiSnapshot.clang_va_list_facts_reliable's own docstring for why),
        # so this flag's value is consulted only for a "clang" snapshot. The
        # `== "castxml"` spelling still matters for treating an untracked
        # pre-`ast_producer` snapshot (None here) as possibly clang-family
        # rather than silently trusted.
        clang_va_list_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "castxml"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CLANG_VA_LIST_FACTS
        )

    # ADR-063 Phase 0 (schema v26): see storage/fact_codec.py.
    apply_legacy_fact_backfill(
        d,
        types,
        funcs,
        _schema_version,
        clang_vtable_facts_reliable_value,
        clang_va_list_facts_reliable_value,
        ast_producer_value,
    )

    if "castxml_var_access_facts_reliable" in d:
        # Same explicit-marker-wins reasoning as the flags above.
        castxml_var_access_facts_reliable_value = bool(
            d["castxml_var_access_facts_reliable"]
        )
    else:
        # Inverted producer spelling from the clang-side flags above: this
        # fact is castxml-only, so it's `== "clang"` (rather than
        # `== "castxml"`) that means "not this producer, therefore
        # trusted-by-irrelevance" — an untracked pre-`ast_producer` snapshot
        # (None here) is treated as possibly castxml rather than silently
        # trusted, same principle as the others. See
        # AbiSnapshot.castxml_var_access_facts_reliable's own docstring for
        # why "hybrid" is NOT treated as trusted-by-irrelevance either.
        castxml_var_access_facts_reliable_value = (
            not from_headers
            or ast_producer_value == "clang"
            or _schema_version >= _MIN_SCHEMA_VERSION_FOR_CASTXML_VAR_ACCESS_FACTS
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
        # G28 Phase 3 — per-fact provenance map for a hybrid (castxml+clang
        # merged) snapshot. Absent on every non-hybrid / pre-Phase-3 snapshot,
        # loads as the empty dict (same "unknown" default as a fresh snapshot).
        fact_provenance=dict(d.get("fact_provenance", {})),
        constants=d.get("constants", {}),
        platform=d.get("platform"),
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

    # G14: derive the CPython extension surface for snapshots that predate the
    # key (or a `dump` path that didn't attach it), so a saved abi3 baseline is
    # still checked at compare time. Skip when the key was present (the dumper
    # already answered, including an explicit "not an extension" null).
    #
    # Mach-O caveat: the ``imported_symbols`` table is itself new in G14. A
    # legacy Mach-O ``.abi.json`` written before it existed has no import data;
    # ``_macho_from_dict`` defaults the absent key to ``[]``. Deriving an
    # extension from that empty set would be actively misleading: `scan --abi3`
    # would audit *zero* CPython imports and certify the module clean, and
    # `compare` would treat every import re-captured from the new binary as
    # newly gained. So when a Mach-O snapshot never recorded its imports, leave
    # ``python_ext`` as ``None`` (unknown) — `--abi3` then honestly reports the
    # artifact must be re-dumped rather than silently passing.
    _macho_imports_uncaptured = (
        isinstance(macho_data, dict) and "imported_symbols" not in macho_data
    )
    if (
        snap.python_ext is None
        and _python_ext_key_absent
        and not _macho_imports_uncaptured
    ):
        if snap.elf is not None or snap.pe is not None or snap.macho is not None:
            from .python_ext import detect_python_extension

            snap.python_ext = detect_python_extension(snap)

    # A degraded *_facts_reliable flag used to load with no signal at all --
    # the flag itself was computed correctly, but nothing ever told the
    # person running the comparison that any detector would decline to
    # trust a stale-but-real-looking fact on this snapshot. Two separate
    # situations both leave a flag False, and both need the same visible
    # signal rather than silence:
    #   (1) THIS snapshot's own schema_version predates SCHEMA_VERSION, and
    #       the gap crossed one of the per-flag thresholds above -- the
    #       direction every CI baseline actually hits, since a baseline is
    #       committed once and outlives however many abicheck pin bumps
    #       happen before it's next regenerated.
    #   (2) schema_version reads as CURRENT, but an explicit False marker in
    #       `d` carried a degraded flag forward from an earlier, genuinely
    #       older extraction -- the "explicit-marker-wins" round-trip-
    #       stability path every flag's own computation above already
    #       implements. `snapshot_to_dict` always re-stamps schema_version
    #       to the CURRENT SCHEMA_VERSION on save (it describes the writing
    #       tool's format capability, not the snapshot's true field-fact
    #       origin -- see e.g. header_cv_facts_reliable_value's own comment),
    #       so a legacy snapshot that was loaded and simply re-saved reads
    #       as current-schema on its next load even though the underlying
    #       facts were never regenerated. Gating this warning on
    #       schema_version alone would make it disappear across exactly that
    #       round-trip (Codex review, PR #720) -- the flags themselves, not
    #       the version number, are the ground truth here.
    # Each entry pairs a flag with whether it is even CONSULTED by the one
    # detector that reads it, given THIS side's own AST producer and header
    # confirmation. Most flags' value computation already collapses to
    # "reliable" for every producer their consumer doesn't gate on (see each
    # flag's own computation above) -- but two don't: clang_va_list_facts_
    # reliable's value treats "hybrid" the same as "clang" (correct for the
    # fact's own provenance), yet diff_symbols._diff_param_va_list only ever
    # consults it when BOTH sides are exactly "clang" (never "hybrid").
    # castxml_var_access_facts_reliable is the mirror case for "castxml" vs.
    # diff_symbols._diff_var_access. Listing either one for a hybrid
    # snapshot would claim reduced detection coverage that regenerating the
    # snapshot could never restore, since no detector consults it for that
    # producer regardless of schema version (Codex review, PR #720).
    #
    # Separately, five of the seven flags' one real consumer requires
    # CONFIRMED (non-inferred) header awareness on this side before it ever
    # reads the flag at all: clang_restrict/clang_va_list/
    # castxml_var_access's detectors each exit through
    # diff_symbols._both_header_aware before consulting their flag, and
    # clang_deprecation/clang_field_initializer's shared consumer
    # (fact_provenance.fact_producer) opens with the identical
    # ``from_headers and not from_headers_inferred`` check. A schema-v1..v5
    # snapshot that predates the explicit ``from_headers`` key gets
    # ``from_headers`` GUESSED true from a populated surface -- real, but
    # not "confirmed" -- so none of these five detectors will ever consult
    # their flag for it regardless of whether a fresh dump would restore it
    # (Codex review, PR #720). header_cv_facts_reliable and
    # clang_vtable_facts_reliable are the two exceptions: their consumers
    # (variable/field cv checks, layout/vtable diffing) apply to any
    # snapshot carrying the underlying fact, header-confirmed or not.
    _header_confirmed = from_headers and not from_headers_inferred
    _degraded_facts = sorted(
        name
        for name, reliable, consulted in (
            ("header_cv_facts_reliable", header_cv_facts_reliable_value, True),
            (
                "clang_deprecation_facts_reliable",
                clang_deprecation_facts_reliable_value,
                _header_confirmed,
            ),
            (
                "clang_field_initializer_facts_reliable",
                clang_field_initializer_facts_reliable_value,
                _header_confirmed,
            ),
            (
                "clang_vtable_facts_reliable",
                clang_vtable_facts_reliable_value,
                True,
            ),
            (
                "clang_restrict_facts_reliable",
                clang_restrict_facts_reliable_value,
                _header_confirmed,
            ),
            (
                "clang_va_list_facts_reliable",
                clang_va_list_facts_reliable_value,
                _header_confirmed and ast_producer_value == "clang",
            ),
            (
                "castxml_var_access_facts_reliable",
                castxml_var_access_facts_reliable_value,
                _header_confirmed and ast_producer_value == "castxml",
            ),
        )
        if not reliable and consulted
    )
    if _degraded_facts:
        import warnings

        if _schema_version < SCHEMA_VERSION:
            _reason = (
                f"Snapshot schema_version {_schema_version} predates this "
                f"abicheck's schema_version {SCHEMA_VERSION}"
            )
        else:
            _reason = (
                "This snapshot carries facts preserved from an earlier, "
                f"older extraction (its schema_version reads as the current "
                f"{SCHEMA_VERSION} because it was re-saved since, but the "
                "underlying facts below were never regenerated)"
            )
        warnings.warn(
            f"{_reason}: "
            f"{', '.join(_degraded_facts)} "
            f"{'is' if len(_degraded_facts) == 1 else 'are'} marked unreliable on "
            "this snapshot, so the affected detectors will decline to trust these "
            "stale facts rather than risk a false positive purely from this tool "
            "upgrade. Detection coverage for these fields is reduced until this "
            "snapshot is regenerated with the current abicheck.",
            UserWarning,
            stacklevel=2,
        )

    backfill_missing_elf_binding(snap)
    normalize_anonymous_type_spellings_on_load(snap)
    return qualified_name_segments.renumber_anonymous_closure_identities(snap)


def load_snapshot(path: str | Path) -> AbiSnapshot:
    """Load a snapshot from *path*, transparently handling plain, gzip, and
    zstd storage (ADR-059) — detected from magic bytes, not the filename."""
    from .snapshot_io import read_snapshot_text

    return snapshot_from_dict(json.loads(read_snapshot_text(path)))


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
    from .snapshot_io import SnapshotCompression, write_snapshot_text

    return write_snapshot_text(
        snapshot_to_json(snap),
        path,
        compression=SnapshotCompression(compression),
        zstd_level=zstd_level,
    )


# ADR-061: BundleFacts (de)serialization moved to bundle_facts_serialization.py
# (classified `workflows`, alongside the `BundleFacts` it serializes) --
# bundle_facts.py is itself at its own 800-line production cap, so this is a
# new sibling rather than growing that module. Each wrapper below resolves
# its implementation via `importlib.import_module` (a runtime call, not a
# static `ast.Import`/`ast.ImportFrom` node) rather than a
# `from .bundle_facts_serialization import ...` -- that module itself needs
# `snapshot_to_dict`/`snapshot_from_dict` from *this* module, and a static
# import in both directions is exactly the `serialization <->
# bundle_facts_serialization` cycle `scripts/check_ai_readiness.py`'s
# `import-cycle-growth` check flags via a full `ast.walk` (so even a
# function-scoped `from ... import ...` counts) -- the same reason
# `abicheck.cli`'s own `__getattr__` resolves its moved names through
# `abicheck.frontends.cli.moved` instead of importing them back. Unlike that
# facade, these are real typed `def`s rather than a blanket module
# `__getattr__`: these four names are called with real argument/return types
# by other first-party modules (`bundle_variants_config.py`,
# `cli_compare_release_helpers.py`, ...), and `__getattr__(...) -> Any` would
# silently erase that checking for every caller reaching them through this
# module's documented `from abicheck.serialization import ...` path (Codex
# review).
def _bundle_facts_serialization() -> Any:
    import importlib

    return importlib.import_module(".bundle_facts_serialization", __package__)


def bundle_facts_to_dict(facts: BundleFacts) -> dict[str, Any]:
    """Serialize a :class:`~abicheck.bundle_facts.BundleFacts` to a
    JSON-able dict (G38 Phase 2). See
    :func:`abicheck.bundle_facts_serialization.bundle_facts_to_dict`."""
    return cast(
        "dict[str, Any]", _bundle_facts_serialization().bundle_facts_to_dict(facts)
    )


def bundle_facts_from_dict(d: dict[str, Any]) -> BundleFacts:
    """Inverse of :func:`bundle_facts_to_dict`. See
    :func:`abicheck.bundle_facts_serialization.bundle_facts_from_dict`."""
    return cast("BundleFacts", _bundle_facts_serialization().bundle_facts_from_dict(d))


def load_bundle_facts(
    path: str | Path, *, format: str = "auto", max_json_object_nodes: int | None = None
) -> BundleFacts:
    """Load a BundleFacts. See
    :func:`abicheck.bundle_facts_serialization.load_bundle_facts`."""
    return cast(
        "BundleFacts",
        _bundle_facts_serialization().load_bundle_facts(
            path, format=format, max_json_object_nodes=max_json_object_nodes
        ),
    )


def save_bundle_facts(
    facts: BundleFacts,
    path: str | Path,
    *,
    format: str = "json",
    compression: str = "auto",
) -> SnapshotWriteResult:
    """Save *facts*. See
    :func:`abicheck.bundle_facts_serialization.save_bundle_facts`."""
    return cast(
        "SnapshotWriteResult",
        _bundle_facts_serialization().save_bundle_facts(
            facts, path, format=format, compression=compression
        ),
    )

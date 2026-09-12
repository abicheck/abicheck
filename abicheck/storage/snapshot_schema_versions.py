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

"""``AbiSnapshot`` on-disk schema version, its full per-bump history, and the
derived "since which version is this fact reliable" thresholds.

Split out of :mod:`abicheck.storage.snapshot_codec` (ADR-061 gap E) purely
to keep that module under the ADR-061 new-file production line ceiling --
the version history itself is unchanged, verbatim, from its original home
in ``serialization.py``/``snapshot_codec.py``.
"""

from __future__ import annotations

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
SCHEMA_VERSION: int = 46  # v46: Function/Variable.declared_in_headers_fact/in_public_contract_fact/binary_exported_fact persisted (storage/fact_codec.py) -- the split of the three independent facts Visibility conflated (a declaration exists in the parsed headers; it belongs to the promised public contract; a binary symbol is exported), see model/surface_facts.py and the Visibility.PUBLIC entry in docs/contribute/known-gaps.md. Missing on a pre-v46 snapshot, which model/surface_facts.py resolves by deriving each fact from the stored `visibility` value as a PARTIAL, diagnostic-stamped reading -- never as a confirmed negative, so a headerless snapshot still reads "declaration not established" rather than "no declaration"; v45: Param.kind_fact persisted (storage/fact_codec.py) -- ADR-063 Phase 5's eleventh batch, closing the last case-(a) field the "field-by-field conversion complete" v41 note missed: Param.kind was never flagged as availability-ambiguous (neither header-AST backend had ever set it to anything but the dataclass's own resting ParamKind.VALUE, so nothing about it looked wrong by inspection until diff_symbols._params_differ's raw kind comparison was found to fabricate FUNC_PARAMS_CHANGED whenever a header-derived snapshot was compared against a DWARF-derived one of the identical library). Guarded by AbiSnapshot.param_kind_facts_reliable; v44: AbiSnapshot.header_only persisted (workstream F "Header-only comparison" S1) -- the explicit marker for a snapshot built by the binary-less header-AST dump path; missing on a pre-v44 snapshot loads as False, matching every prior snapshot's implicit "this has a binary, or is a pre-existing source-only dump" status; v43: Variable.is_static persisted (PR #1024 review fix -- closes the plain-C/extern-"C" same-named static-vs-external variable identity collision `tu_merge._variable_key`'s own docstring long documented as a known, accepted limitation; missing on a pre-v43 snapshot loads as False, matching every prior reader's implicit assumption since the field did not exist); v42: ADR-062/063 Phase 8 (redesign) -- the on-disk wire format itself changed (snapshot_to_json() now writes storage.sectioned_document's single-file sectioned envelope instead of a flat document), not just a field. Bumped specifically so a pre-Phase-8 reader (whose own SCHEMA_VERSION was already 41) hits the ">SCHEMA_VERSION and >=_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION" hard-rejection path below instead of silently reading every top-level field as absent/empty -- a same-numbered envelope change would have given that reader no signal at all (Codex review, fresh evidence). This build itself reads the envelope transparently regardless of version, per snapshot_from_dict's own is_sectioned_document check; v41: Param.is_restrict_fact and Variable.access_fact persisted (storage/fact_codec.py) -- ADR-063 Phase 5's field-by-field conversion complete; v40: Function/Variable/RecordType/EnumType.deprecated_fact and EnumType.is_scoped_fact persisted (storage/fact_codec.py); v39: TypeField.is_const_fact/is_volatile_fact/is_mutable_fact persisted (storage/fact_codec.py); v38: AbiSnapshot.semantic_ir + semantic_ir_conflicts persisted (storage/semantic_ir_codec.py); v37: ElfMetadata.dynamic_flags_fact/has_init_fact/has_fini_fact, PeMetadata.delay_imports_fact, MachoMetadata.rpaths_fact persisted (snapshot_platform_blocks.py/storage/fact_codec.py); v36: AbiSnapshot.ast_resolved_standard_fact persisted (storage/fact_codec.py); v35: Function.contract_attributes_fact/is_explicit_fact/is_hidden_friend_fact/source_header_fact/is_variadic_fact/exception_spec_fact/is_override_fact/hidden_friend_owner_fact/elf_binding_fact/is_compiler_generated_fact persisted (storage/fact_codec.py); v34: Variable.source_header_fact/alignment_bits_fact/elf_binding_fact persisted (storage/fact_codec.py); v33: EnumType.qualified_name_fact/source_header_fact persisted (storage/fact_codec.py); v32: RecordType.is_abstract_fact/data_size_bits_fact/is_standard_layout_fact/is_trivially_copyable_fact/qualified_name_fact/source_header_fact persisted (storage/fact_codec.py); v31: typedef/constant entity_id sidecars persisted (storage/entity_id_codec.py); v30: RecordType.is_final_fact persisted (storage/fact_codec.py); v29: AbiSnapshot.surface_graph persisted (storage/surface_graph_codec.py); v28: entity_id carrier persisted (storage/entity_id_codec.py).

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

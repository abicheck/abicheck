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

"""``AbiSnapshot`` — the complete captured ABI surface of one library build.

Every extraction layer (L0 symbols through L5 source graph) writes its facts
into one of this dataclass's fields, and every comparison, policy and report
stage reads them back out. It owns the shape of a snapshot only: producing one
belongs to ``extract``, persisting one to ``storage``.
"""

from __future__ import annotations

import logging as _logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .declarations import Function, Variable
from .entities import EnumType, RecordType
from .extraction_contract import DependencyInfo, ExtractionContract
from .first_wins_index import build_first_wins_index, describe_dropped
from .graph_facts import SurfaceGraphLike

if TYPE_CHECKING:
    from ..buildsource.model import BuildSourceRef
    from ..buildsource.pack import BuildSourcePack
    from .build_mode_facts import BuildMode
    from .dwarf_facts import AdvancedDwarfMetadata, DwarfMetadata
    from .elf_facts import ElfMetadata
    from .identity import EntityId
    from .kabi_facts import KabiMetadata
    from .macho_facts import MachoMetadata
    from .pe_facts import PeMetadata
    from .python_facts import (
        NumPyCapiSurface,
        PythonApiSurface,
        PythonExtMetadata,
    )
    from .sycl_facts import SyclMetadata

_model_log = _logging.getLogger(__name__)


@dataclass
class AbiSnapshot:
    """Complete ABI snapshot of one version of a library."""

    library: str  # e.g. "libfoo.so.1"
    version: str  # e.g. "1.2.3"
    functions: list[Function] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    types: list[RecordType] = field(default_factory=list)
    elf: ElfMetadata | None = field(
        default=None
    )  # ELF dynamic/symbol metadata (Sprint 2)
    pe: PeMetadata | None = field(default=None)  # PE/COFF metadata (Windows DLL)
    macho: MachoMetadata | None = field(default=None)  # Mach-O metadata (macOS dylib)
    dwarf: DwarfMetadata | None = field(
        default=None
    )  # DWARF layout metadata (Sprint 3)
    dwarf_advanced: AdvancedDwarfMetadata | None = field(default=None)  # Sprint 4
    sycl: SyclMetadata | None = field(
        default=None
    )  # SYCL PI plugin metadata (ADR-020b)
    python_ext: PythonExtMetadata | None = field(
        default=None
    )  # CPython extension-module facts: init export, abi3/Limited-API status,
    # and imported CPython C-API symbols (G14). None for non-extension libraries.
    kabi: KabiMetadata | None = field(
        default=None, kw_only=True
    )  # Linux kernel Module.symvers metadata (G23-D1). Keyword-only so inserting
    # it among the optional metadata fields cannot shift any positional argument.
    python_api: PythonApiSurface | None = field(
        default=None, kw_only=True
    )  # Python-visible API surface (functions/classes/methods/signatures)
    # recovered from a `.pyi` type stub (G23). Keyword-only (like ``kabi``) so
    # inserting it among the optional metadata fields cannot shift the positional
    # slot of ``enums``/``typedefs``/… for callers that build snapshots
    # positionally. None when no stub was found — the C-ABI/export view can't see
    # this surface, so it's a separate check.
    numpy_capi: NumPyCapiSurface | None = field(
        default=None, kw_only=True
    )  # NumPy C-API consumption (_ARRAY_API/_UFUNC_API, NPY_TARGET_VERSION)
    # recovered from binary evidence (G26). Keyword-only for the same reason
    # as ``kabi``/``python_api``. None when the binary could not be scanned
    # or predates this field; an ordinary, successfully-scanned non-NumPy
    # library carries a real surface with both flags False (CodeRabbit review).
    enums: list[EnumType] = field(default_factory=list)
    typedefs: dict[str, str] = field(
        default_factory=dict
    )  # alias -> underlying type name
    constants: dict[str, str] = field(
        default_factory=dict
    )  # #define / constexpr name -> value string
    elf_only_mode: bool = False  # True when dumped without headers (all functions are ELF_ONLY provenance)
    from_headers: bool = False  # True when the ABI surface was parsed from public headers (castxml/AST), as opposed to DWARF debug info or the symbol table. Drives the HEADER_AWARE evidence tier — DWARF-derived declarations populate the same functions/types lists but must NOT be mistaken for header-level evidence.
    # Which L2 header-AST backend produced this snapshot ("castxml" | "clang" |
    # "hybrid"), set only when from_headers is True. Some facts were captured
    # by only one backend originally (deprecated/EnumType.is_scoped/
    # TypeField.default/RecordType.is_abstract/Function.is_override —
    # castxml-only; see dumper_clang.py — G31 Phase C's backend audit closed
    # that gap for all five, across two passes);
    # TypeField.default and Param.default (both now cross-producer but NOT
    # cross-comparable — castxml keeps the verbatim source expression, clang
    # a literal/structural fingerprint) still need detectors to gate on BOTH
    # sides sharing the SAME producer, not merely on from_headers, or a
    # producer mismatch reads as every such fact being silently removed
    # (Codex review, PR #582; fact_provenance.py's
    # same_producer_backed_fact_qualified/fact_producer vs.
    # both_known_backed_fact is the two variants of that gate — the latter
    # for a directly cross-comparable fact like deprecated/is_scoped). None
    # for non-header snapshots (DWARF/symbols-only) and for snapshots
    # predating this field.
    #
    # "hybrid" (G28 Phase 3, ``--ast-frontend hybrid``, ``dumper_hybrid.
    # merge_snapshots()``) means this snapshot was built by running BOTH
    # castxml and clang over the same headers and merging them field-by-field
    # — see ``fact_provenance`` below for which specific facts were actually
    # castxml-sourced on this merged snapshot, since a whole-snapshot producer
    # tag alone can't tell a caller that.
    # Keyword-only (Codex review, PR #582): both this and the next field were
    # inserted ahead of several existing positional fields (platform,
    # language_profile, ...) — without kw_only, an existing positional
    # caller shifts silently, e.g. binding "elf" to ast_producer instead of
    # platform, corrupting provenance rather than failing loudly.
    ast_producer: str | None = field(default=None, kw_only=True)

    # Resolved L2 executable/compiler identity used to create the header AST.
    # Kept as string metadata so older readers can ignore it and newer tools can
    # add fields without another model migration.  Empty on snapshots predating
    # schema v11 and on binary/debug-only snapshots.
    ast_toolchain: dict[str, str] = field(default_factory=dict, kw_only=True)
    # Set only when the user explicitly opted into an auto CastXML→Clang
    # fallback.  The reason remains visible after snapshot serialization.
    ast_fallback_reason: str | None = field(default=None, kw_only=True)

    # CastXML version-gate outcome (schema v13, ``castxml_policy.py``). None on
    # snapshots not produced by a version-gated CastXML L2 scan (older
    # snapshots, clang/DWARF/symbols-only snapshots). False means the scan ran
    # against an out-of-policy CastXML build ONLY because the caller passed the
    # explicit ``--allow-unsupported-castxml`` override — by default an
    # unsupported version aborts the scan before headers are parsed, so no
    # snapshot with ``ast_toolchain_supported=False`` should exist unless that
    # override was used. A caller must treat such a snapshot as not eligible
    # to become a new strict baseline without a further explicit acknowledgment.
    ast_toolchain_supported: bool | None = field(default=None, kw_only=True)
    ast_toolchain_unsupported_reasons: list[str] = field(
        default_factory=list, kw_only=True
    )

    # Resolved SYCL/DPC++ host-vs-device AST context (ADR-050 D5, G32 Phase
    # D) -- "host"/"device" when the L2 header AST was produced by a
    # DPC++-capable compiler (abicheck.sycl_context's selector), None for an
    # ordinary clang/castxml dump, which has no host/device "kind" concept
    # at all. Read by dumper_contract._attach_extraction_contract the same
    # way it already reads ast_toolchain, and folded into
    # compute_extraction_contract's profile_fingerprint only when non-None
    # (never for an ordinary dump, to keep every pre-Phase-D fingerprint
    # byte-for-byte unchanged).
    frontend_context_kind: str | None = field(default=None, kw_only=True)

    # Structured compile-context provenance (schema v15, P1 toolchain-profile
    # audit). None/() on a pre-v15 snapshot and on any snapshot not built from
    # a header-AST parse (DWARF/symbols-only, PE/Mach-O without headers) — the
    # same conservative "unknown, don't guess" default every other tri-state
    # provenance field here uses.
    #
    # The resolved C/C++ standard actually used for the header parse: an
    # explicit -std=/--std=/std: value verbatim, or "gnu++20" when the
    # requires/concept heuristic forced it (dumper.py's force_cpp20 path) —
    # never a guess at the frontend's own unpinned default.
    ast_resolved_standard: str | None = field(default=None, kw_only=True)
    # The __cplusplus literal mandated by ast_resolved_standard (e.g.
    # "201703L" for "gnu++17"), looked up from a static ISO-standard table —
    # None when ast_resolved_standard is unset or not a recognized C++ edition.
    ast_cplusplus_macro: str | None = field(default=None, kw_only=True)
    # The ordered extra compiler arguments passed to the header frontend
    # (--compiler-option tokens, then a shlex-split composed-flags string) — the exact
    # argv tail, for reproducibility and fingerprinting.
    ast_compile_args: tuple[str, ...] = field(default_factory=tuple, kw_only=True)
    # The --sysroot passed to the header frontend, if any.
    ast_sysroot: str | None = field(default=None, kw_only=True)

    # DWARF-vs-header-AST layout-backfill coherence (schema v16, P0 evidence-
    # coherence audit). Populated only on the clang-L2-backend + DWARF-present
    # path where dumper_layout_backfill.backfill_dwarf_layout() actually runs
    # (None/() everywhere else — castxml already computes its own layout, so
    # there is nothing to reconcile against DWARF at all, and this is *not*
    # the same as "unavailable": a castxml snapshot's layout is trivially
    # self-consistent by construction). One of:
    #   "matched"     — every header record eligible for backfill found a
    #                    corroborated DWARF counterpart (or none needed one).
    #   "partial"     — some records backfilled; others had no DWARF
    #                    candidate at all (e.g. declared but never
    #                    instantiated in this binary) or an ambiguous bare
    #                    name — benign, expected on a real binary, not a
    #                    disagreement.
    #   "mismatch"    — at least one header record found a *unique* DWARF
    #                    candidate by name but the two disagreed on kind
    #                    (struct/union) or had no corroborating field/base
    #                    overlap — backfill_dwarf_layout already refuses to
    #                    merge that record's layout (it stays header-only,
    #                    incomplete), so no incorrect data reaches the
    #                    snapshot; this field exists purely so that refusal
    #                    is visible instead of silent. See
    #                    dwarf_layout_coherence_mismatches for which types.
    #   "unavailable" — the clang L2 backend ran but the binary carried no
    #                    usable DWARF at all, so backfill could not be
    #                    attempted (every header record needing layout stays
    #                    permanently layout-blind).
    dwarf_layout_coherence: str | None = field(default=None, kw_only=True)
    # Header record names backfill_dwarf_layout found a uniquely-named DWARF
    # candidate for but rejected as not corroborated (the "mismatch" reason
    # above) — empty unless dwarf_layout_coherence == "mismatch".
    dwarf_layout_coherence_mismatches: tuple[str, ...] = field(
        default_factory=tuple, kw_only=True
    )

    # G28 Phase 3 — per-fact producer provenance for a "hybrid" snapshot only
    # (empty for every ordinary single-backend snapshot; ``ast_producer`` alone
    # already answers the question there). Keyed by the stable strings built by
    # ``fact_provenance.func_fact_key``/``var_fact_key``/``type_fact_key``/
    # ``enum_fact_key``/``field_fact_key``, valued "castxml" or "clang" — which
    # backend's value ``dumper_hybrid.merge_snapshots()`` actually used for
    # that one fact on that one declaration. A key absent from this dict (on a
    # hybrid snapshot) means neither backend populated it — same "unknown,
    # don't manufacture a finding" convention as every other tri-state field
    # here. See ``abicheck/fact_provenance.py`` for the reader-side helpers
    # every ``_both_castxml_backed``-gated detector uses instead of trusting
    # ``ast_producer`` alone once a hybrid snapshot is in play.
    fact_provenance: dict[str, str] = field(default_factory=dict, kw_only=True)

    # True when TypeField.is_const/is_volatile/is_mutable and CV-qualifier
    # type spelling are known-reliable for this snapshot's fields. The
    # castxml parser silently left these permanently False/unqualified
    # before a fix (see CHANGELOG); a *persisted* snapshot dumped before
    # that fix has real "false" data, not merely absent data, so it cannot
    # be told apart from a genuine "not const" field by the value alone —
    # only a snapshot-level marker can. False only for a snapshot rehydrated
    # from a persisted schema_version predating the fix (see
    # serialization.SCHEMA_VERSION); a freshly-built in-memory snapshot
    # (dump(), or any snapshot never round-tripped through JSON) defaults
    # True, since it was necessarily produced by the current, fixed parser
    # (Codex review, PR #582).
    header_cv_facts_reliable: bool = field(default=True, kw_only=True)

    # True when this snapshot's deprecated (every surface kind) and
    # EnumType.is_scoped facts are known-reliable when its own
    # ``ast_producer`` is ``"clang"`` -- G31 Phase C (schema v19) wired real
    # extraction of both into the direct-clang backend, previously
    # unconditionally None/False. Same "real but WRONG data" shape as
    # ``header_cv_facts_reliable`` above: a pre-v19 clang-producer snapshot's
    # ``deprecated=None``/``is_scoped=False`` is indistinguishable by value
    # alone from a genuine "not deprecated"/"not scoped" fact, so only a
    # snapshot-level marker can tell them apart. False only for a snapshot
    # rehydrated from a persisted pre-v19, clang-producer schema (see
    # serialization.SCHEMA_VERSION); a freshly-built in-memory snapshot
    # defaults True, since it was necessarily produced by the current, fixed
    # parser. Does NOT need to be checked for "castxml" or "hybrid"
    # producers: castxml's own deprecated/is_scoped extraction predates this
    # field entirely (G28 Phase 1, always reliable), and a hybrid snapshot's
    # per-declaration ``fact_provenance`` already resolves to "castxml" for
    # these two facts under the OLD (pre-fix) merge code — the old
    # backfill's own "prefer castxml, backfill from clang only when
    # castxml's own value is null" policy always recorded "castxml"
    # provenance for a fact clang could never populate, so a legacy hybrid
    # snapshot carries no equivalent false-reliability risk.
    clang_deprecation_facts_reliable: bool = field(default=True, kw_only=True)

    # True when this snapshot's TypeField.default (default member initializer)
    # facts are known-reliable when its own ``ast_producer`` is ``"clang"`` OR
    # ``"hybrid"`` -- G31 Phase C (schema v20) wired real extraction into the
    # direct-clang backend (``dumper_clang_expr._field_initializer_value``),
    # previously unconditionally None. Exactly the shape of
    # ``clang_deprecation_facts_reliable`` above, and needed for the same
    # reason: ``TypeField.default`` is documented (see the field itself) as
    # ``None`` both for "no initializer" and "this dumper doesn't capture
    # it", so a pre-v20 clang snapshot's blanket ``None`` is indistinguishable
    # by value alone from a genuine "this field has no initializer". Without
    # this marker, comparing a fresh clang dump against a persisted pre-v20
    # clang baseline in the new-side-legacy direction reads as every
    # initializer having been REMOVED. Tracked separately from the
    # deprecation flag rather than folded into it because the two facts
    # landed in different schema versions -- a v19 snapshot has reliable
    # deprecated/is_scoped but unreliable field defaults, which a single
    # shared flag could not express. Not needed for "castxml" (its own
    # extraction predates both flags, G28 Phase 1). UNLIKE
    # ``clang_deprecation_facts_reliable``, this one DOES need to cover a
    # legacy "hybrid" snapshot too (Codex review, fresh evidence, second
    # round): a pre-v20 hybrid merge's clang-only-APPENDED record types
    # (``merge_snapshots()``'s ``clang_only_types`` loop) never had
    # ``default`` provenance stamped at all -- only ``deprecated`` was, since
    # clang couldn't populate ``default`` yet -- so an absent provenance
    # entry for such a field on a pre-v20 hybrid snapshot is real-but-WRONG
    # data (the field's own value is unconditionally None), not genuinely
    # unrecorded. A MATCHED field's ``default`` provenance is unaffected
    # either way -- it's unconditionally stamped "castxml" regardless of
    # schema version (``_backfill_fact`` records provenance for every
    # matched declaration; clang's own value was always None pre-fix, so
    # there was nothing to ever backfill from), so it always has a real,
    # trusted provenance entry and never depends on this flag.
    clang_field_initializer_facts_reliable: bool = field(default=True, kw_only=True)

    # True when this snapshot's RecordType.vtable/vptr_offset_bits facts are
    # known-reliable when its own ``ast_producer`` is ``"clang"`` -- G31
    # Phase C (schema v21) wired real virtual-method-table reconstruction
    # into the direct-clang backend (``dumper_clang_vtable.py``), previously
    # unconditionally ``vtable=[]``/``vptr_offset_bits=None`` for EVERY
    # record regardless of whether it was actually polymorphic. Same
    # "real but WRONG data" shape as ``clang_field_initializer_facts_reliable``
    # above: a pre-v21 clang-producer record's blanket empty vtable is
    # indistinguishable by value alone from a genuine "this class has no
    # virtuals", so only a snapshot-level marker can tell them apart. Without
    # this flag, comparing a fresh clang dump of an UNCHANGED, already-
    # polymorphic header against a persisted pre-v21 clang baseline reads as
    # every polymorphic class gaining its first vptr (Codex review, fresh
    # evidence, real end-to-end repro: a persisted schema-v20 clang snapshot
    # of ``struct A { virtual void f(); };`` compared against a fresh dump of
    # the identical, unchanged header emitted a false ``VPTR_INTRODUCED`` --
    # and, for a class whose vtable differs in slot count/order from the
    # blanket-empty legacy reading, a false ``TYPE_VTABLE_CHANGED`` too).
    # False only for a snapshot rehydrated from a persisted pre-v21,
    # clang-producer schema (see serialization.SCHEMA_VERSION); a freshly-
    # built in-memory snapshot defaults True, since it was necessarily
    # produced by the current, fixed parser. Not needed for "castxml" or
    # "hybrid" producers: castxml's own vtable reconstruction predates this
    # field entirely (always reliable), and DWARF's own vtable/vptr
    # extraction (``dwarf_snapshot.py``) is a wholly separate code path this
    # flag does not describe.
    clang_vtable_facts_reliable: bool = field(default=True, kw_only=True)

    # True when this snapshot's Param.is_restrict facts are known-reliable
    # when its own ``ast_producer`` is ``"clang"`` OR ``"hybrid"`` -- G31
    # Phase C (schema v22) wired real extraction into the direct-clang
    # backend (``dumper_clang._clang_param_is_restrict``), previously
    # unconditionally False for EVERY parameter regardless of its actual
    # qualification. Same "real but WRONG data" shape as the three flags
    # above: ``Param.is_restrict`` is a plain bool with no "not collected"
    # state, so a pre-v22 clang-producer parameter's blanket False is
    # indistinguishable by value alone from a genuine "not restrict-
    # qualified" parameter, and comparing a fresh clang dump of UNCHANGED
    # headers against a persisted pre-v22 clang baseline reads as every
    # restrict qualifier having been ADDED (and, in the other direction,
    # REMOVED). Covers "hybrid" too, for the same reason as
    # ``clang_field_initializer_facts_reliable``: a hybrid merge keeps
    # castxml's own ``params`` verbatim for every MATCHED function (there is
    # no per-param backfill), so only a clang-ONLY function -- appended
    # verbatim by ``dumper_hybrid._merge_functions`` -- carries clang's
    # parameters, and on a pre-v22 hybrid snapshot those are exactly the
    # blanket-False ones. Not needed for "castxml": its own
    # ``_resolve_cv_restrict`` extraction predates this field entirely.
    # False only for a snapshot rehydrated from a persisted pre-v22,
    # clang/hybrid-producer schema (see serialization.SCHEMA_VERSION); a
    # freshly-built in-memory snapshot defaults True, since it was
    # necessarily produced by the current, fixed parser.
    clang_restrict_facts_reliable: bool = field(default=True, kw_only=True)

    # True when this snapshot's Param.is_va_list facts are known-reliable
    # when its own ``ast_producer`` is ``"clang"`` -- G31 Phase C continued
    # (schema v23) wired real extraction into the direct-clang backend
    # (``dumper_clang._clang_param_is_va_list``, x86-64 System V spelling
    # only), previously unconditionally False for EVERY parameter on every
    # backend. Identical "real but WRONG data" shape as
    # ``clang_restrict_facts_reliable`` immediately above, for the identical
    # reason: ``Param.is_va_list`` is a plain bool with no "not collected"
    # state, so a pre-v23 clang-producer parameter's blanket False is
    # indistinguishable by value alone from a genuine non-``va_list``
    # parameter, and comparing a fresh clang dump of UNCHANGED headers
    # against a persisted pre-v23 clang baseline would read as every
    # ``va_list`` parameter having just been added.
    #
    # Deliberately does NOT cover "hybrid" the way
    # ``clang_restrict_facts_reliable`` does (Codex review, fresh evidence):
    # a hybrid merge keeps castxml's own ``params`` verbatim for every
    # MATCHED function, and unlike ``is_restrict`` -- where castxml IS a
    # real producer -- castxml has NEVER populated ``is_va_list`` at all, so
    # a matched function's param reads a permanent, version-independent
    # False regardless of schema version, not a legacy-baseline artifact
    # this flag could describe. ``diff_symbols._diff_param_va_list``
    # excludes "hybrid" from its producer gate entirely rather than
    # consulting this flag for it; see that detector's and
    # ``diff_param_qualifiers.param_va_list_changes``'s docstrings for the
    # full reasoning. Not needed for "castxml" either, for the ordinary
    # reason: it has never populated this fact at all (still true after
    # this change — see ``dumper_castxml.py``), so a castxml snapshot's
    # blanket False is unconditionally correct-as-"not collected" the same
    # way it always was, on any schema version. False only for a snapshot
    # rehydrated from a persisted pre-v23, clang-producer schema (see
    # serialization.SCHEMA_VERSION); a freshly-built in-memory snapshot
    # defaults True, since it was necessarily produced by the current,
    # fixed parser.
    clang_va_list_facts_reliable: bool = field(default=True, kw_only=True)

    # True when this snapshot's Variable.access facts are known-reliable
    # when its own ``ast_producer`` is ``"castxml"`` -- G31 Phase C continued
    # (schema v24) wired real extraction into the castxml backend
    # (``dumper_castxml._CastxmlParser._access_level``, already used for
    # ``Function``/``TypeField.access`` -- verified against real castxml
    # output that a static class member's ``<Variable>`` element carries the
    # identical structured ``access`` attribute), previously unconditionally
    # ``AccessLevel.PUBLIC`` for EVERY variable on every backend.
    # ``Variable.access`` is a plain enum with no "not collected" state, so
    # a pre-v24 castxml-producer variable's blanket PUBLIC is
    # indistinguishable by value alone from a genuine public variable, and
    # comparing a fresh castxml dump of UNCHANGED headers against a
    # persisted pre-v24 castxml baseline would read every real
    # private/protected static member as newly WIDENED to public.
    #
    # Deliberately does NOT cover "clang" (it has never populated this fact
    # at all, so its blanket PUBLIC is unconditionally correct-as-"not
    # collected" the same way it always was) or "hybrid" (mirroring
    # ``clang_va_list_facts_reliable``'s own reasoning: a hybrid merge keeps
    # castxml's own ``Variable`` verbatim for a matched declaration -- so
    # THAT part is genuinely reliable once castxml itself is fixed -- but a
    # clang-only-appended variable carries no access signal at all, and
    # nothing distinguishes the two per-declaration today).
    # ``diff_symbols._diff_var_access`` requires ``ast_producer == "castxml"``
    # on both sides rather than consulting this flag for any other producer.
    # False only for a snapshot rehydrated from a persisted pre-v24,
    # castxml-producer schema (see serialization.SCHEMA_VERSION); a
    # freshly-built in-memory snapshot defaults True, since it was
    # necessarily produced by the current, fixed parser.
    castxml_var_access_facts_reliable: bool = field(default=True, kw_only=True)

    # Phase 3: binary format platform — detected from ELF/PE/MachO metadata.
    # None = unknown / not yet detected.
    # Populated by detect_platform() in pipeline or by the dumper.
    platform: str | None = None  # "elf" | "pe" | "macho" | None

    # Phase 4: language profile — detected from symbol mangling / extern "C" annotations.
    # None = unknown / mixed / not yet detected.
    # Populated by detect_profile() in pipeline or by the dumper.
    language_profile: str | None = None  # "c" | "cpp" | "sycl" | None

    # ADR-024 §D5.3 — structured confidence signal for header-scope resolution.
    # Set by the dumper when public-header scoping was *requested* but could not
    # be applied as intended, so the surface had to fall back to the export
    # table. The previously bare ``UserWarning`` (PR #259) is retained for human
    # output; this field makes the same fact machine-readable so the surface
    # ledger can disclose reduced confidence. None = scoping succeeded or was
    # never requested. Recognised values:
    #   "header-backend-unavailable" — selected header backend missing / header
    #                                  parse failed
    #   "mangling-fallback"          — headers parsed but no declared symbol
    #                                  matched the export table (typically MSVC
    #                                  C++ name mangling)
    scope_fallback: str | None = None

    # Full-stack dependency info (populated by --follow-deps)
    dependency_info: DependencyInfo | None = field(default=None)

    # Provenance metadata (schema v4) — tracks where/when a snapshot was created
    git_commit: str | None = None  # SHA from git rev-parse HEAD at dump time
    git_tag: str | None = None  # e.g. "v2.0.0", set via --git-tag or auto-detected
    created_at: str | None = None  # ISO 8601 timestamp, auto-set at dump time
    build_id: str | None = None  # opaque CI identifier (run ID, build number, etc.)
    # Build-mode capture (schema v5) — normalized compiler / stdlib / std
    # mode derived from DWARF DW_AT_producer, ELF .comment, and mangled
    # symbol heuristics. Used to attribute layout/mangling differences
    # to build configuration rather than real ABI breaks. See
    # ``abicheck/build_mode.py`` for the dataclass and detector logic.
    # None when capture is unavailable or the dumper predates v5.
    build_mode: BuildMode | None = None
    # Optional on-disk artifact path that produced this snapshot.
    # Keyword-only (placed after all other fields) to prevent accidental positional binding.
    # Used by binary-only fallback detectors that need lightweight disassembly.
    source_path: str | None = field(default=None, kw_only=True)
    # mtime (st_mtime, seconds) of source_path at dump time. Lets a later
    # best-effort re-probe against source_path (e.g. cli_helpers_compare's
    # fold_l0_hard_removals) detect that the on-disk binary has since changed
    # — e.g. rebuilt in place after this snapshot was dumped to JSON — and
    # decline to trust it, keeping a pre-dumped-snapshot compare reproducible.
    # None for snapshots predating this field, or when source_path is None.
    # Honours SOURCE_DATE_EPOCH the same way created_at does (dumper._safe_mtime)
    # so two dumps of identical binary content stay byte-identical.
    source_mtime: float | None = field(default=None, kw_only=True)
    # True when source_mtime is a SOURCE_DATE_EPOCH substitution rather than
    # source_path's real filesystem mtime (dumper._safe_mtime). Persisted
    # because the *compare*-time environment may not have SOURCE_DATE_EPOCH
    # set even though the *dump* that produced this snapshot did (e.g. a CI
    # dump step under a pinned epoch, followed by an interactive compare
    # later with no such variable set) — fold_l0_hard_removals needs to know
    # the recorded value can never match a live re-probe's real mtime
    # regardless of what's in its own environment (Codex review: gating on
    # compare-time os.environ alone missed this combination). False (not
    # None) for snapshots predating this field, matching the pre-epoch-aware
    # default of trusting a real mtime.
    source_mtime_epoch: bool = field(default=False, kw_only=True)
    # st_size of source_path at dump time — a second, cheap identity signal
    # alongside source_mtime for the same fold_l0_hard_removals re-check.
    # mtime alone can't catch a content-preserving-timestamp rebuild (e.g.
    # `cp -p`, `touch -r`, a coarse-mtime filesystem); size doesn't need
    # SOURCE_DATE_EPOCH gating the way mtime does — two reproducible builds
    # of identical content have identical size by definition, so recording
    # the real size never threatens the byte-identical-dump guarantee.
    source_size: int | None = field(default=None, kw_only=True)

    # ADR-028 (schema v7) — optional reference to an out-of-band BuildSourcePack
    # carrying L3/L4/L5 source/build/graph evidence. Only a lightweight
    # reference (content hash + coverage summary) lives in the snapshot; the
    # heavyweight pack is content-addressed on disk and versions independently
    # (BUILD_SOURCE_PACK_VERSION). None when no evidence was collected. Old readers
    # ignore this optional field (ADR-015 backward-compatibility).
    build_source_pack: BuildSourceRef | None = field(default=None, kw_only=True)

    # Single-artifact UX — optional *inline* BuildSourcePack carrying the
    # normalized L3 build-info + L4/L5 source facts directly inside the
    # snapshot, so `compare old.json new.json` works with no out-of-band pack
    # directories. Populated by `dump --build-info/--sources`; serialized under
    # the "build_source" key. None when nothing was embedded. Old readers ignore
    # this optional field (ADR-015). When both are present, the embedded facts
    # are authoritative for the compare and `build_source_pack` is the matching
    # provenance reference.
    build_source: BuildSourcePack | None = field(default=None, kw_only=True)

    # ADR-063 Phase 3 (D5, schema v29) — the one evidence graph
    # `compare/surface_graph.py`'s public-surface builder and (when present)
    # the L5 builder both write into, unconditionally: unlike `build_source`
    # above, this is never gated on `--sources`/`--build-info` evidence —
    # every freshly extracted snapshot whose headers were parsed gets one.
    # `None` only for a snapshot predating this field, or one whose headers
    # were never parsed at all (a pure binary-only L0/L1 dump); a query over
    # such a snapshot goes through `policy.public_surface.
    # resolve_public_surface()`'s lazy, in-memory approximate backfill
    # instead — never through `PublicSurfaceQuery.resolve()` directly, and
    # never persisted back onto the loaded object. `SurfaceGraphLike`
    # (`model/graph_facts.py`), not the concrete `SourceGraphSummary`, so
    # this module needs no `buildsource` import — `build_source.
    # source_graph` stays a live alias to the identical object whenever both
    # are populated (one graph, two attribute paths), never a second,
    # independently-built copy.
    surface_graph: SurfaceGraphLike | None = field(default=None, kw_only=True)

    # ADR-029 — True when this snapshot's public-header AST was parsed using the
    # real build context (a compile_commands.json supplied to `dump -p`), so the
    # declared API facts reflect the build's ABI-relevant flags. Lets the
    # build-evidence diff suppress HEADER_PARSE_CONTEXT_DRIFT when the headers
    # were in fact parsed with that context. Defaults False (older snapshots and
    # context-free dumps); ignored by old readers (additive optional field).
    parsed_with_build_context: bool = field(default=False, kw_only=True)

    # ADR-039 — the preprocessor macros the build actually defines (its active
    # ``-D`` set, harvested from the compile database). Empty means context-free
    # / unknown.
    build_context_defines: set[str] = field(default_factory=set, kw_only=True)
    # ADR-039 — registry of *conditional* record fields the header parse knows
    # about, with their full declaration: ``{type: {field: {"guard": macro,
    # "type": type_name, "is_bitfield": bool, "bitfield_bits": int|None,
    # "access": str, "is_const": bool, "is_volatile": bool, "is_mutable": bool,
    # "is_last": bool}}}`` (each field entry is a mixed-value dict, not
    # ``dict[str, str]``; a field may also carry ``"negative": True`` for an
    # ``#ifndef`` guard or ``"ambiguous": True`` when its guard macro is
    # conditionally ``#undef``/``#define``d). ``is_last`` marks a field that is
    # terminal in its record's source order — the reconciler only clears a presence
    # delta for a terminal field, so re-adding it cannot reorder a sibling. A
    # field lives here iff its presence is gated by a ``#if defined(GUARD)``
    # region, whether or not a context-free parse pruned it from the type's
    # ``fields`` list. Carrying the *declaration* (not just the guard) lets
    # ``diff_reconcile`` prove a pruned-field presence delta is a
    # context-free-parse artifact **and** that the field's declaration is
    # unchanged — so a guarded field whose type changed (a real ABI break) is
    # never cleared. Corroborating build evidence only; it never deletes a finding
    # artifact evidence proves (the authority rule, ADR-028).
    conditional_fields: dict[str, dict[str, dict[str, object]]] = field(
        default_factory=dict, kw_only=True
    )

    # ADR-050 D1 (schema v14) — profile/scope fingerprints proving this
    # snapshot's extraction contract, checked by
    # ``comparability.check_contracts_comparable`` before a compare is
    # allowed to produce a verdict. None on every snapshot predating this
    # field and on a symbols-only dump with no header-AST/public-header
    # inputs at all (see ``ExtractionContract``'s own docstring). Keyword-only
    # for the same reason as the other optional metadata fields above.
    contract: ExtractionContract | None = field(default=None, kw_only=True)

    # Dependency-scoping mode (schema v18) — whether toolchain/system-header
    # declarations were excluded from this snapshot's flat lists/DWARF
    # collections by ``dumper_scoping.scope_snapshot_excluding_dependencies``
    # ("filtered") or deliberately kept ("full", via ``--include-system-declarations``).
    # ``dump`` and ``compare``'s own live-binary dumping (``service.run_dump``)
    # both filter by default and tag "filtered"; a direct Python API caller of
    # ``service.run_dump``/``resolve_input`` gets the opposite default
    # (``include_dependencies=True``, tagging "full"), preserving every other
    # caller (``scan``, MCP, ...) that doesn't opt in explicitly.
    # ``None`` means "not recorded" — every snapshot predating this field.
    # Deliberately NOT treated as equivalent to ``"full"`` anywhere: since
    # ``dumper_scoping.py``'s filtering already shipped as the ``dump``
    # default before this field existed, an ordinary pre-v18 baseline is
    # usually already-filtered content that simply predates the tag —
    # assuming ``"full"`` for it would spuriously flag the single most
    # common workflow (compare a cached baseline against a fresh dump) as
    # not comparable. ``comparability.check_contracts_comparable`` only
    # raises ``ScopeMismatchError`` when BOTH sides carry an explicit,
    # non-``None`` value and they differ — which still catches the
    # originally-reported danger (a filtered ``dump`` baseline compared
    # against an unfiltered ``compare`` live dump, e.g. one built by a
    # direct Python API caller) once both sides come from a current abicheck
    # build, without touching the irrecoverable ambiguity of an old,
    # untagged snapshot.
    dependency_scope: str | None = field(default=None, kw_only=True)

    # Fully-qualified typedef alias -> underlying type name (schema v25,
    # G31 Phase C). Additive twin of ``typedefs`` above, not a replacement:
    # ``typedefs`` is keyed by *bare* (unqualified) name on both header
    # backends, so two distinct member typedefs sharing a bare spelling in
    # different classes/namespaces (e.g. two unrelated ``value_type``
    # member aliases — an extremely common STL-container-shaped pattern)
    # silently collide, and whichever declaration a backend visits last
    # wins; the other's aliasing information is dropped from the snapshot
    # entirely with no way to recover it downstream (see AGENTS.md's "Known
    # gaps" entry for the full incident history). Since a qualified name is
    # unique per declaration, this dict cannot suffer that collision — both
    # header backends populate it using the same scope-joining they already
    # use for every other declaration kind, so it carries no *new*
    # collision-avoidance logic of its own, just a different key shape.
    # ``typedefs`` itself is deliberately left untouched (same key, same
    # values, same silent-overwrite behavior) so no existing consumer's
    # behavior changes — this is a pure addition for a consumer able to use
    # qualified identity, not a schema replacement. Empty for every
    # snapshot produced by a DWARF-only dump (which never had per-class
    # qualified typedef scoping in the first place) and for any snapshot
    # predating this field. See ``type_reachability_spelling.
    # _typedef_spelling_targets`` for the first consumer.
    typedefs_qualified: dict[str, str] = field(default_factory=dict, kw_only=True)

    # ADR-063 Phase 2's closing slice: ``EntityId`` sidecars for typedefs and
    # constants (schema v31). Unlike ``RecordType``/``EnumType``/``Function``/
    # ``Variable``, ``typedefs``/``typedefs_qualified``/``constants`` are plain
    # ``dict[str, str]`` with no parsed declaration object to carry an
    # ``entity_id`` on, so the identity both header-AST backends already resolve
    # while walking their own intermediate representation had nowhere to go and
    # was discarded. These are additive twins keyed exactly like their partner
    # dict — ``typedef_entity_ids`` by the same qualified name as
    # ``typedefs_qualified``, ``constant_entity_ids`` by the same qualified name
    # as ``constants`` — so a consumer joins one against the other without a
    # second key convention. Empty on a DWARF-only snapshot (which resolves no
    # scope for either kind) and on one predating this field, same as
    # ``typedefs_qualified``; ``diff_types._diff_typedefs``/
    # ``diff_symbols._diff_constants`` read them with ``.get``, so absence
    # degrades to today's identity-less ``Change`` rather than misreporting.
    # A sidecar's keys must be renumbered exactly when its partner dict's are,
    # which is why ``qualified_name_segments._LAMBDA_IDENTITY_FIELDS`` lists
    # ``typedef_entity_ids`` (``typedefs_qualified`` is rewritten there) and
    # deliberately does not list ``constant_entity_ids`` (``constants`` is
    # excluded from that walk, its values being payload literals). Rewriting
    # only one half of either pair would break the join it exists to support.
    typedef_entity_ids: dict[str, EntityId] = field(default_factory=dict, kw_only=True)
    constant_entity_ids: dict[str, EntityId] = field(default_factory=dict, kw_only=True)

    # Runtime-only provenance qualifier (not serialized — popped in
    # snapshot_to_dict). True when ``from_headers`` was *inferred* for a legacy
    # snapshot that predates the explicit ``from_headers`` key, rather than set
    # explicitly by the dumper or loaded verbatim. Source-level detectors that
    # must only fire on genuine header evidence (e.g. parameter renames) require
    # ``from_headers and not from_headers_inferred`` so ambiguous legacy
    # DWARF-only baselines do not produce false API breaks.
    from_headers_inferred: bool = field(default=False, repr=False, compare=False)

    # Indexes (built lazily)
    _func_by_mangled: dict[str, Function] | None = field(
        default=None, repr=False, compare=False
    )
    _var_by_mangled: dict[str, Variable] | None = field(
        default=None, repr=False, compare=False
    )
    _type_by_name: dict[str, RecordType] | None = field(
        default=None, repr=False, compare=False
    )

    def index(self) -> None:
        """Build lookup indexes. Uses first-wins for duplicate mangled names.

        Idempotent — a second call is a no-op. ``function_map``/
        ``variable_map``/``type_by_name`` already guard their own lazy
        ``self.index()`` call on ``is None``, but several detector modules
        (``diff_cpp_patterns.py``, ``diff_templates.py``, ``diff_filtering.py``,
        ``post_processing.py``) call ``index()`` directly, once per detector,
        without knowing whether an earlier detector already indexed this same
        snapshot. Without this guard, ``index()`` unconditionally rebuilt all
        three maps *and re-logged* every duplicate-name warning below on each
        call — for one compare/scan run with a genuine duplicate type name,
        the identical warning could log up to ~7 times per side.

        This trades away rebuild-on-mutation (Codex/CodeRabbit review):
        appending to ``functions``/``variables``/``types`` *after* an
        earlier ``index()`` call and expecting a following ``index()`` to
        pick up the addition will instead keep serving the stale maps —
        there is no cheap, reliable way to detect an in-place list mutation
        to invalidate on. This is safe because every real producer already
        treats a snapshot's collections as fixed once built and resets all
        three cache fields to ``None`` together when it *does* need to
        rebuild them from a changed collection (see ``dumper_hybrid.py``,
        ``dumper_scoping.py``, ``clang_layout_tool.py``) — ``index()`` is
        never called, mutate, called again on the same live object anywhere
        in this codebase. A caller with a genuine need to re-index after
        mutating a snapshot in place must reset ``_func_by_mangled``/
        ``_var_by_mangled``/``_type_by_name`` to ``None`` first, the same
        way those three modules already do.
        """
        if self._type_by_name is not None:
            return
        functions = build_first_wins_index(self.functions, lambda f: f.mangled)
        self._warn_dropped("mangled symbols", functions.dropped)
        self._func_by_mangled = functions.mapping

        variables = build_first_wins_index(self.variables, lambda v: v.mangled)
        self._warn_dropped("mangled variables", variables.dropped)
        self._var_by_mangled = variables.mapping

        types = build_first_wins_index(self.types, lambda t: t.name)
        self._warn_dropped("type names", types.dropped)
        self._type_by_name = types.mapping

    def _warn_dropped(self, subject: str, dropped: dict[str, int]) -> None:
        """Report the declarations a first-wins index had to drop, if any."""
        if not dropped:
            return
        _model_log.warning(
            "Duplicate %s skipped (first-wins) in %s@%s: %s",
            subject,
            self.library,
            self.version,
            describe_dropped(dropped),
        )

    @property
    def function_map(self) -> dict[str, Function]:
        if self._func_by_mangled is None:
            self.index()
        assert self._func_by_mangled is not None
        return self._func_by_mangled

    @property
    def variable_map(self) -> dict[str, Variable]:
        if self._var_by_mangled is None:
            self.index()
        assert self._var_by_mangled is not None
        return self._var_by_mangled

    def func_by_mangled(self, mangled: str) -> Function | None:
        return self.function_map.get(mangled)

    def var_by_mangled(self, mangled: str) -> Variable | None:
        return self.variable_map.get(mangled)

    def type_by_name(self, name: str) -> RecordType | None:
        if self._type_by_name is None:
            self.index()
        assert self._type_by_name is not None
        return self._type_by_name.get(name)

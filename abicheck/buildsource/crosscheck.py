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

"""Intra-version cross-source validation engine (ADR-035 D4, phase 2 / G19.2).

Unlike every other diff in abicheck, this engine consumes **one** merged
:class:`~abicheck.model.AbiSnapshot` and diffs its evidence *sources against
each other within a single version* — no baseline compare. It surfaces a class
of "bad ABI hygiene" findings that only become visible when the binary export
table, the public-header AST, the build flags, and the include/provenance graph
are checked for *mutual consistency*:

================================  =====================================  ==========
Check                             Inputs                                  Tier
================================  =====================================  ==========
``exported_not_public``           binary exports ↔ L2 header decls         RISK
``public_not_exported``           L2 header decls ↔ binary exports         RISK
``header_build_context_mismatch`` L2 header context ↔ L3 build flags       API_BREAK
``private_header_leak``           public API ↔ private-header provenance   RISK
``odr_type_variant``              L4 per-TU layouts of one type            API_BREAK
``public_to_internal_dependency`` L5 reachability ↔ changed files          RISK
``unversioned_exported_symbol``   binary exports ↔ ELF version table       RISK
``rtti_for_internal_type``        typeinfo exports ↔ header provenance     RISK
================================  =====================================  ==========

The last two are the ADR-035 D8 single-release hygiene audit: intra-version "bad
ABI hygiene" surfaced from one build (no baseline), exposed through ``scan
--audit`` / ``surface-report --audit``.

Per ADR-035 D1/D4 the findings are **never** ``BREAKING`` on their own (an
artifact diff still proves a shipped break); they default to ``RISK`` or
``API_BREAK`` and are advisory/suppressible until a check earns its FP-rate-gate
corpus and is promoted.

**Coverage honesty (ADR-035 D4).** A check whose required evidence is absent
(e.g. no public-header provenance, or no L3 build evidence) is reported as a
``NOT_COLLECTED`` coverage row naming what to enable — it is **never** counted
as clean and **never** emits a finding. With sources + provenance present the
check runs for real. This keeps the engine false-positive-free on an ELF-only
snapshot: it simply reports every check as skipped.

Everything here is a pure function over an in-memory snapshot — no binaries are
parsed and no external tools are run — so the whole module is exercised by fast
unit tests.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..checker_policy import ChangeKind, Confidence
from ..checker_types import Change
from ..model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Variable,
)

# Export accounting (ADR-035 D4) lives in a sibling module (crosscheck hit the
# 2000-line file cap). Re-exported so ``_check_exported_not_public`` and the tests
# keep importing these names from ``crosscheck``.
from .export_accounting import (
    _ALLOCATOR_INTERPOSER_MARKER,
    _ALLOCATOR_INTERPOSER_SYMBOLS,
    _UNDOCUMENTED_ACCOUNTS,
    ACCOUNT_ALLOCATOR_INTERPOSER,
    ACCOUNT_CXX_ARTIFACT,
    ACCOUNT_EXTERNAL_DEP,
    ACCOUNT_INTERNAL_NS,
    ACCOUNT_PUBLIC,
    ACCOUNT_TEMPLATE_INST,
    _account_undocumented_export,
    _external_dependency_origin,
    _library_self_names,
    _linked_library_names,
)
from .source_graph import (
    DECL_NODE_KINDS,
    DEPENDENCY_EDGE_KINDS,
    INTERNAL_VISIBILITIES,
    PUBLIC_VISIBILITIES,
    UNANNOTATED_VISIBILITIES,
    GraphNode,
    SourceGraphSummary,
    decl_declaring_files,
    is_internal_dependency_node,
    is_public_dependency_node,
    looks_like_system_name,
)

#: Cross-check fact-schema version. Independent of every other buildsource
#: schema version (see ``buildsource/CLAUDE.md`` "Versioning").
CROSSCHECK_VERSION: int = 1

# -- check + provider vocabulary ---------------------------------------------

CHECK_EXPORTED_NOT_PUBLIC = "exported_not_public"
CHECK_PUBLIC_NOT_EXPORTED = "public_not_exported"
CHECK_HEADER_BUILD_CONTEXT_MISMATCH = "header_build_context_mismatch"
CHECK_PRIVATE_HEADER_LEAK = "private_header_leak"
CHECK_ODR_TYPE_VARIANT = "odr_type_variant"
CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY = "public_to_internal_dependency"
CHECK_UNVERSIONED_EXPORTED_SYMBOL = "unversioned_exported_symbol"
CHECK_RTTI_FOR_INTERNAL_TYPE = "rtti_for_internal_type"
CHECK_IDENTITY_COLLISION = "identity_collision_detected"
CHECK_COMPILE_CONTEXT_CONFLICT = "compile_context_conflict"
CHECK_SOURCE_SURFACE_DSO_MISMATCH = "source_surface_dso_mismatch"

#: Every check the engine knows, in cheapest-first order (ADR-035 D4 table).
ALL_CHECKS: tuple[str, ...] = (
    CHECK_EXPORTED_NOT_PUBLIC,
    CHECK_PUBLIC_NOT_EXPORTED,
    CHECK_HEADER_BUILD_CONTEXT_MISMATCH,
    CHECK_PRIVATE_HEADER_LEAK,
    CHECK_ODR_TYPE_VARIANT,
    CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY,
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    CHECK_RTTI_FOR_INTERNAL_TYPE,
    CHECK_IDENTITY_COLLISION,
    CHECK_COMPILE_CONTEXT_CONFLICT,
    CHECK_SOURCE_SURFACE_DSO_MISMATCH,
)

# The finding/coverage primitives and the §6.8 provider-agreement vocabulary
# (ADR-035 D4) live in the leaf ``crosscheck_base`` so a split-out check module
# (``crosscheck_coherence``) can share them without forming an import cycle back
# to this engine. Re-exported so existing ``from .crosscheck import _change``
# call sites and the tests keep resolving these names here.
from .crosscheck_base import (  # noqa: E402
    PROVIDER_BINARY_EXPORTS,
    PROVIDER_BUILD_CONFIG,
    PROVIDER_PUBLIC_HEADER_AST,
    PROVIDER_SOURCE_INDEX,
    _change,
    _CheckOutput,
    _exported_symbol_names,
)

# The two evidence-coherence checks (AC-008/AC-009) live in their own module to
# keep this file under the 2000-line cap. That module depends only on the leaf
# ``crosscheck_base`` (never on this engine), so this is a one-directional edge
# — no import cycle (CLAUDE.md "M1-3").
from .crosscheck_coherence import (  # noqa: E402
    _check_compile_context_conflict,
    _check_source_surface_dso_mismatch,
)


@dataclass(frozen=True)
class CrosscheckConfig:
    """Which cross-checks run, and the per-check finding cap.

    ``enabled`` defaults to every check; the orchestrator (Phase 3 ``scan``)
    narrows it from the ``crosschecks:`` config block. ``max_per_check`` caps a
    single check's findings so a pathological library cannot flood the report;
    0 disables the cap. ``changed_paths`` is the optional PR/revision changed-file
    set: ``public_to_internal_dependency`` elevates a finding whose internal
    target was changed (ADR-035 D4 "L5 reachability ↔ PR changed files"). It only
    refines the message/confidence — the base finding fires regardless.
    """

    enabled: frozenset[str] = frozenset(ALL_CHECKS)
    max_per_check: int = 200
    changed_paths: frozenset[str] = frozenset()


@dataclass
class CrosscheckResult:
    """Outcome of an intra-version cross-source validation pass (ADR-035 D4).

    ``findings`` are ordinary :class:`Change` objects ready to fold into a
    ``DiffResult`` / audit report. ``coverage`` carries one row per check (run
    or skipped) so a partial pass is legible — never read as clean. ``providers``
    maps each *run* check to the evidence sources that corroborated it (the
    §6.8 provider-agreement matrix).
    """

    findings: list[Change] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    providers: dict[str, list[str]] = field(default_factory=dict)
    version: int = CROSSCHECK_VERSION

    def counts_by_check(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.findings:
            counts[c.kind.value] = counts.get(c.kind.value, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "findings": len(self.findings),
            "counts_by_check": self.counts_by_check(),
            "coverage": list(self.coverage),
            "providers": {k: list(v) for k, v in self.providers.items()},
        }


def run_crosschecks(
    snapshot: AbiSnapshot, config: CrosscheckConfig | None = None
) -> CrosscheckResult:
    """Run the enabled intra-version cross-source checks over one merged snapshot.

    Returns a :class:`CrosscheckResult`; each disabled-or-skipped check still
    produces a coverage row so the caller can tell "ran and clean" from "could
    not run" (ADR-035 D4 coverage honesty).
    """
    cfg = config or CrosscheckConfig()
    result = CrosscheckResult()
    runners = {
        CHECK_EXPORTED_NOT_PUBLIC: _check_exported_not_public,
        CHECK_PUBLIC_NOT_EXPORTED: _check_public_not_exported,
        CHECK_HEADER_BUILD_CONTEXT_MISMATCH: _check_header_build_context_mismatch,
        CHECK_PRIVATE_HEADER_LEAK: _check_private_header_leak,
        CHECK_ODR_TYPE_VARIANT: _check_odr_type_variant,
        CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY: _check_public_to_internal_dependency,
        CHECK_UNVERSIONED_EXPORTED_SYMBOL: _check_unversioned_exported_symbol,
        CHECK_RTTI_FOR_INTERNAL_TYPE: _check_rtti_for_internal_type,
        CHECK_IDENTITY_COLLISION: _check_identity_collision,
        CHECK_COMPILE_CONTEXT_CONFLICT: _check_compile_context_conflict,
        CHECK_SOURCE_SURFACE_DSO_MISMATCH: _check_source_surface_dso_mismatch,
    }
    for name in ALL_CHECKS:
        if name not in cfg.enabled:
            result.coverage.append(
                _coverage_row(name, "not_collected", "disabled by configuration")
            )
            continue
        out = runners[name](snapshot, cfg)
        capped = (
            out.findings[: cfg.max_per_check]
            if cfg.max_per_check and len(out.findings) > cfg.max_per_check
            else out.findings
        )
        result.findings.extend(capped)
        status = out.status
        detail = out.detail
        if len(capped) < len(out.findings):
            status = "partial"
            detail += f" (capped at {cfg.max_per_check} of {len(out.findings)})"
        result.coverage.append(
            _coverage_row(name, status, detail, facts=out.facts, counters=out.counters)
        )
        if out.status == "present":
            result.providers[name] = out.providers
    return result


# ---------------------------------------------------------------------------
# exported_not_public — a symbol is exported but no public header declares it.
# ---------------------------------------------------------------------------


def _check_exported_not_public(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """Exported symbols with no *public* declaration, RISK.

    Driven from the **binary export table**, not the declaration list: in a
    header-backed dump castxml only creates ``Function``/``Variable`` entries for
    declarations it parsed, so a symbol that exists *only* in the export table
    (the canonical accidental-ABI case) has no decl object to iterate — the check
    must start from ``exported`` and ask which exports no public header declares
    (Codex review).

    A public header "declares" an export when a ``PUBLIC_HEADER`` decl maps to
    that symbol. Itanium constructor/destructor exports (``…C1Ev``/``…D1Ev``) are
    skipped: castxml routinely leaves members unmangled, so they would not match
    a public class's decls and would false-positive; the class itself, if it
    leaks, is caught by ``private_header_leak``.

    Provenance only runs when a public-header set was supplied, so the check
    skips cleanly on an ELF-only / no-header snapshot; it also needs an export
    table to compare against.

    Every export is *accounted* with a precise reason (ADR-035 D4 accounting): a
    documented public-API symbol and a compiler artifact are legitimate; each
    undocumented one is bucketed as an external-dependency leak (libstdc++/{fmt}/…,
    named on the finding), an internal-namespace escape, a template instantiation,
    or a bare undeclared export. The per-category counts ride the coverage row's
    ``counters`` and sum to the export count, so a report can state "100 %
    accounted" and a maintainer can triage a leaked dependency differently from an
    internal-namespace escape.
    """
    providers = [PROVIDER_BINARY_EXPORTS, PROVIDER_PUBLIC_HEADER_AST]
    if not _origin_resolvable(snapshot):
        return _CheckOutput([], "skipped", _NO_PROVENANCE, providers)
    exported = _exported_symbol_names(snapshot)
    if exported is None:
        return _CheckOutput(
            [], "skipped", "no binary export table on the snapshot", providers
        )

    # Symbols a public header declares (so an export of them is documented), and
    # a decl lookup for enriching the message when one exists in a non-public
    # header.
    public_syms: set[str] = set()
    decl_by_sym: dict[str, Function | Variable] = {}
    all_decls: list[Function | Variable] = [*snapshot.functions, *snapshot.variables]
    for d in all_decls:
        for sym in _candidate_symbols(d):
            decl_by_sym.setdefault(sym, d)
            if d.origin == ScopeOrigin.PUBLIC_HEADER:
                public_syms.add(sym)

    # The binary's linked-library list (ELF DT_NEEDED / Mach-O LC_LOAD_DYLIB / PE
    # imports) feeds the external-dependency origin finders, so a leaked C++-runtime
    # symbol names the runtime the binary actually links (e.g. the ``libc++.1.dylib``
    # dylib on macOS rather than a hard-coded ELF soname).
    needed_libs = _linked_library_names(snapshot)
    # The audited library's own identity — a vendored namespace (fmt/boost/…) that
    # is the library *being scanned* is native, not a leaked dependency.
    self_names = _library_self_names(snapshot)
    # An allocator-interposition library (malloc proxy) deliberately exports
    # malloc/operator-new/… replacements; those are native, not a leaked dependency.
    interposer = _ALLOCATOR_INTERPOSER_MARKER in exported

    # Account for *every* export with a precise reason so the report can state
    # "100 % accounted": documented API and compiler artifacts are legitimate;
    # each undocumented reason yields a finding whose message names the reason
    # (an external-dependency leak reads very differently from an internal-
    # namespace escape). ``account`` sums to len(exported).
    account: Counter[str] = Counter()
    findings: list[Change] = []
    for sym in sorted(exported):
        if sym in public_syms:
            account[ACCOUNT_PUBLIC] += 1
            continue
        # A malloc-proxy library deliberately exports allocator replacements
        # (``malloc``/``operator new``/…); they are native + intentional, so account
        # them as legitimate and emit no finding — never advise hiding them (Codex).
        if interposer and sym in _ALLOCATOR_INTERPOSER_SYMBOLS:
            account[ACCOUNT_ALLOCATOR_INTERPOSER] += 1
            continue
        # The external-dependency check runs *before* the C++ compiler-artifact
        # exemption: a leaked libstdc++/{fmt} vtable or typeinfo (``_ZTVNSt…``,
        # ``_ZTIN3fmt…``) is that exact leaked surface these counters measure, and
        # exempting it as a class artifact would silently undercount it (Codex
        # review). Only a *native* class's artifact is then exempted below.
        origin_lib = _external_dependency_origin(sym, needed_libs, self_names)
        if origin_lib is not None:
            account[ACCOUNT_EXTERNAL_DEP] += 1
            findings.append(
                _exported_not_public_finding(
                    sym, ACCOUNT_EXTERNAL_DEP, origin_lib, decl_by_sym.get(sym)
                )
            )
            continue
        if _is_cxx_implementation_symbol(sym):
            account[ACCOUNT_CXX_ARTIFACT] += 1
            continue
        category = _account_undocumented_export(sym)
        account[category] += 1
        findings.append(
            _exported_not_public_finding(sym, category, None, decl_by_sym.get(sym))
        )

    documented = (
        account[ACCOUNT_PUBLIC]
        + account[ACCOUNT_CXX_ARTIFACT]
        + account[ACCOUNT_ALLOCATOR_INTERPOSER]
    )
    breakdown = ", ".join(
        f"{cat}={account[cat]}" for cat in _UNDOCUMENTED_ACCOUNTS if account[cat]
    )
    detail = (
        f"binary exports ↔ public headers: {len(findings)} of {sum(account.values())} "
        f"export(s) undocumented ({documented} accounted as documented API / "
        f"compiler artifact)" + (f"; by reason: {breakdown}" if breakdown else "")
    )
    return _CheckOutput(findings, "present", detail, providers, counters=dict(account))


#: Per-category message templates for an undocumented export. Each states the
#: precise reason and the fix, so a maintainer can triage a leaked dependency
#: symbol differently from an internal-namespace escape (ADR-035 D4 accounting).
def _exported_not_public_finding(
    sym: str,
    category: str,
    origin_lib: str | None,
    decl: Function | Variable | None,
) -> Change:
    """Build the ``exported_not_public`` finding for one undocumented export.

    The message is category-specific — an external-dependency leak names the
    originating library and points at the linkage fix, an internal-namespace or
    template escape points at the visibility fix — so the *precise reason* rides
    on the finding, not just the aggregate count.
    """
    where = ""
    if decl is not None and category != ACCOUNT_EXTERNAL_DEP:
        kind = "function" if isinstance(decl, Function) else "variable"
        where = f" (declared as {kind} {decl.name!r} in a non-public header)"
    if category == ACCOUNT_EXTERNAL_DEP:
        message = (
            f"Symbol {sym!r} is exported by the binary but originates from an "
            f"external dependency ({origin_lib}) statically linked and re-exported "
            "— not part of this library's API. Hide it (visibility/version script) "
            "or link the dependency dynamically; a differing dependency version on "
            "another host makes the leaked symbol an ODR/compatibility hazard."
        )
    elif category == ACCOUNT_INTERNAL_NS:
        message = (
            f"Symbol {sym!r} is exported by the binary but declared in no public "
            f"header{where}; it belongs to an internal namespace "
            "(impl/internal/detail/anonymous). It is accidental ABI surface — hide "
            "it with -fvisibility=hidden or a version script."
        )
    elif category == ACCOUNT_TEMPLATE_INST:
        message = (
            f"Symbol {sym!r} is an exported C++ template instantiation with no "
            f"matching public declaration{where} (the public headers declare the "
            "template, the binary carries this instantiation). Confirm it is "
            "intended surface, or hide it."
        )
    else:  # ACCOUNT_UNDECLARED
        message = (
            f"Symbol {sym!r} is exported by the binary but declared in no public "
            f"header{where}. It is accidental ABI surface — hide it "
            "(visibility/version script) or document it."
        )
    return _change(
        ChangeKind.EXPORTED_NOT_PUBLIC,
        sym,
        message,
        new_value=sym,
        old_value=origin_lib,
        confidence=Confidence.HIGH,
    )


# ---------------------------------------------------------------------------
# public_not_exported — a public header promises a symbol the binary lacks.
# ---------------------------------------------------------------------------


def _check_public_not_exported(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """Public declarations with an export obligation absent from the binary, RISK.

    Intentionally narrow (ADR-035 D4): only declarations that *promise a dynamic
    symbol* are compared — default-visibility, non-inline, non-pure-virtual,
    non-deleted, non-template free functions / methods / extern data with a
    mangled name. Inline / templated / constexpr / hidden-visibility decls are
    public source surface that legitimately emit no symbol and are excluded, so
    the check does not light up a healthy header-only API.
    """
    providers = [PROVIDER_PUBLIC_HEADER_AST, PROVIDER_BINARY_EXPORTS]
    exported = _exported_symbol_names(snapshot)
    if not _origin_resolvable(snapshot):
        return _CheckOutput([], "skipped", _NO_PROVENANCE, providers)
    if exported is None:
        return _CheckOutput(
            [], "skipped", "no binary export table on the snapshot", providers
        )

    # Two-way reconciliation (ADR-035 D4): exempt decls the L4 linker already tied
    # to an export under a variant spelling (ctor clone / Mach-O / demangle drift),
    # so the check does not double-report a symbol that is genuinely exported.
    reconciled = _l4_reconciled_symbols(snapshot, exported)

    findings: list[Change] = []
    for fn in snapshot.functions:
        if not _has_export_obligation(fn):
            continue
        if fn.mangled not in exported and fn.mangled not in reconciled:
            findings.append(
                _change(
                    ChangeKind.PUBLIC_NOT_EXPORTED,
                    fn.mangled or fn.name,
                    f"Public header declares {fn.name!r} (expected symbol "
                    f"{fn.mangled!r}) but the binary does not export it. Code that "
                    "compiles against the header gets an undefined-symbol link error.",
                    old_value=fn.mangled,
                    confidence=Confidence.HIGH,
                    source_location=fn.source_location,
                )
            )
    for var in snapshot.variables:
        if not _var_has_export_obligation(var):
            continue
        if var.mangled not in exported and var.mangled not in reconciled:
            findings.append(
                _change(
                    ChangeKind.PUBLIC_NOT_EXPORTED,
                    var.mangled or var.name,
                    f"Public header declares extern variable {var.name!r} (expected "
                    f"symbol {var.mangled!r}) but the binary does not export it. "
                    "Consumers linking against it get an undefined-symbol error.",
                    old_value=var.mangled,
                    confidence=Confidence.HIGH,
                    source_location=var.source_location,
                )
            )
    findings.sort(key=lambda c: c.symbol)
    detail = (
        f"public headers ↔ binary exports: {len(findings)} declaration(s) with an "
        "export obligation the binary does not satisfy"
    )
    return _CheckOutput(findings, "present", detail, providers)


# ---------------------------------------------------------------------------
# header_build_context_mismatch — headers parsed without the build's context.
# ---------------------------------------------------------------------------


def _check_header_build_context_mismatch(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """L2 header AST captured without the L3 build's ABI-relevant context, API_BREAK.

    When the build evidence records ABI-affecting flags/macros but the public
    headers were parsed *context-free* (``parsed_with_build_context`` is False),
    the declared API surface may not match what the shipped translation units
    compile to (a macro-conditional field, a packing pragma, an ABI-tag flag).
    Emits a single aggregate finding naming the divergent flags; stays silent
    when the headers *were* parsed with the build context.
    """
    providers = [PROVIDER_BUILD_CONFIG, PROVIDER_PUBLIC_HEADER_AST]
    abi_flags = _abi_relevant_build_flags(snapshot)
    if not snapshot.from_headers:
        return _CheckOutput(
            [], "skipped", "snapshot has no public-header AST (L2)", providers
        )
    if abi_flags is None:
        return _CheckOutput(
            [], "skipped", "no L3 build evidence on the snapshot", providers
        )
    if not abi_flags:
        return _CheckOutput(
            [], "present", "build evidence carries no ABI-relevant flags", providers
        )
    if snapshot.parsed_with_build_context:
        return _CheckOutput(
            [],
            "present",
            f"headers parsed with the build context ({len(abi_flags)} ABI flag(s))",
            providers,
        )

    sample = ", ".join(abi_flags[:6])
    if len(abi_flags) > 6:
        sample += ", …"
    finding = _change(
        ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH,
        "",
        "Public headers were parsed without the build's ABI-relevant context: the "
        f"build records {len(abi_flags)} ABI-affecting flag(s) ({sample}) but the "
        "header AST was captured context-free, so the declared API surface may not "
        "match the shipped translation units. Re-dump the headers with the build's "
        "compile_commands.json.",
        new_value=sample,
        confidence=Confidence.MEDIUM,
        evidence_category="build_context",
    )
    detail = (
        f"header context ↔ build flags: {len(abi_flags)} ABI flag(s) not reflected "
        "in the context-free header parse"
    )
    return _CheckOutput([finding], "present", detail, providers)


# ---------------------------------------------------------------------------
# private_header_leak — public API exposes a private-header-only type.
# ---------------------------------------------------------------------------


def _check_private_header_leak(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """Public API surface that references a private-header type, RISK.

    A public-header function/variable whose signature names a type declared
    *only* in a private (non-installed) header transitively pulls that header
    into a consumer's build; once the private header is absent from the install
    tree the consumer fails to compile. Detected from declaration provenance
    (``origin``) — the strongest always-available signal in a merged snapshot;
    when an L5 include graph is present it can refine the localization. Skips
    cleanly when no private-header provenance is available.
    """
    providers = [PROVIDER_PUBLIC_HEADER_AST]
    # Only claim L5 (source-index) corroboration when the attached graph actually
    # indexed something — an empty ``SourceGraphSummary`` is still a truthy object,
    # so checking presence alone would record a provider with no fact behind it and
    # mask regressions in real source-graph extraction (ADR-035 D4 coverage honesty
    # — Codex review).
    sg = (
        snapshot.build_source.source_graph
        if snapshot.build_source is not None
        else None
    )
    if sg is not None and sg.nodes:
        providers.append(PROVIDER_SOURCE_INDEX)
    if not _origin_resolvable(snapshot):
        return _CheckOutput([], "skipped", _NO_PROVENANCE, providers)

    private_types = _private_type_names(snapshot)
    if not private_types:
        return _CheckOutput(
            [],
            "present",
            "no private-header types declared in the snapshot",
            providers,
        )

    findings: list[Change] = []
    seen: set[tuple[str, str]] = set()
    for fn in snapshot.functions:
        if fn.origin != ScopeOrigin.PUBLIC_HEADER:
            continue
        for leaked in _referenced_private_types(_function_type_refs(fn), private_types):
            key = (fn.mangled or fn.name, leaked)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _change(
                    ChangeKind.PRIVATE_HEADER_LEAK,
                    fn.mangled or fn.name,
                    f"Public API {fn.name!r} exposes type {leaked!r}, which is "
                    "declared only in a private (non-installed) header. Consumers "
                    "including the public header pull in an unshipped declaration. "
                    "Make the header self-contained or install the leaked header.",
                    new_value=leaked,
                    confidence=Confidence.MEDIUM,
                    caused_by_type=leaked,
                )
            )
    for var in snapshot.variables:
        if var.origin != ScopeOrigin.PUBLIC_HEADER:
            continue
        for leaked in _referenced_private_types({var.type}, private_types):
            key = (var.mangled or var.name, leaked)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _change(
                    ChangeKind.PRIVATE_HEADER_LEAK,
                    var.mangled or var.name,
                    f"Public variable {var.name!r} exposes type {leaked!r}, declared "
                    "only in a private (non-installed) header.",
                    new_value=leaked,
                    confidence=Confidence.MEDIUM,
                    caused_by_type=leaked,
                )
            )
    findings.sort(key=lambda c: (c.symbol, c.new_value or ""))
    n_private = len(set(private_types.values()))
    detail = (
        f"public API ↔ private-header provenance: {len(findings)} public "
        f"declaration(s) exposing one of {n_private} private type(s)"
    )
    return _CheckOutput(findings, "present", detail, providers)


# ---------------------------------------------------------------------------
# odr_type_variant — one type has divergent per-TU layouts (L4 ODR conflict).
# ---------------------------------------------------------------------------


def _surface_has_l4_facts(surface: Any) -> bool:
    """Whether a linked L4 source surface carries any real replay evidence.

    An ``inline`` collector attaches an empty :class:`SourceAbiSurface` when L4
    replay ran but parsed zero TUs (missing clang/castxml), so the presence of a
    surface object is not proof of evidence — check for actual content.
    """
    if surface.odr_conflicts:
        return True
    if any(
        (
            surface.reachable_declarations,
            surface.reachable_types,
            surface.reachable_macros,
            surface.reachable_templates,
            surface.reachable_inline_bodies,
        )
    ):
        return True
    mappings = surface.mappings or {}
    if any(
        mappings.get(k)
        for k in ("source_decl_to_binary_symbol", "source_type_to_debug_type")
    ):
        return True
    # Only *parsed-TU* coverage counts. ``exported_symbols``/``matched_symbols``
    # are recorded from the binary export table even when replay parsed zero TUs,
    # so counting them would re-mask an all-failed/zero-TU L4 run as a clean ODR
    # audit (Codex review). Require a real parsed-compile-unit count.
    coverage = surface.coverage or {}
    return any(
        coverage.get(k) for k in ("parsed_tus", "compile_units_parsed", "parsed")
    )


def _surface_boundary_counters(surface: Any) -> tuple[int, dict[str, int]]:
    """The L4 source-link boundary integrity numbers (ADR-035 D4) for a surface.

    Returns ``(facts, counters)`` where ``counters`` names exported vs matched vs
    unmatched symbols, so a degraded link (parsed decls — or even zero TUs — yet
    zero matched exports, the oneDAL shape) is named on the coverage row rather
    than read as clean. Emitted on the present *and* the empty-surface skip path.
    """
    cov = surface.coverage or {}
    unmatched = surface.unmatched or {}
    counters = {
        "exported_symbols": int(cov.get("exported_symbols", 0) or 0),
        "matched_symbols": int(cov.get("matched_symbols", 0) or 0),
        "unmatched_symbols": len(unmatched.get("symbols_without_decl", []) or []),
    }
    facts = int(cov.get("reachable_declarations", 0) or 0) or len(
        surface.reachable_declarations or []
    )
    return facts, counters


def _check_odr_type_variant(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """A type with divergent per-TU definitions in the L4 source surface, API_BREAK.

    The L4 linker (``source_link.link_source_abi``) already records, per merged
    library surface, every ``(qualified_name, header)`` whose ``type_hash``
    diverged between the translation units that defined it — an ODR conflict.
    This check turns each recorded conflict into a finding: mixing those
    definitions at link time is undefined behavior (a consumer compiled against
    one layout reads a struct laid out the other way), and it is almost always a
    macro/flag that changes the type per TU.

    Needs the L4 source-ABI surface; skips cleanly (never false-positives) on a
    snapshot that carries no source-replay evidence.
    """
    providers = [PROVIDER_SOURCE_INDEX]
    surface = (
        snapshot.build_source.source_abi if snapshot.build_source is not None else None
    )
    if surface is None:
        return _CheckOutput(
            [],
            "skipped",
            "no L4 source-ABI surface on the snapshot (run --depth source)",
            providers,
        )
    # An empty surface is attached when L4 replay ran but parsed zero TUs (e.g.
    # clang/castxml unavailable). Reading that as a clean ODR audit would mask the
    # missing evidence, so skip with an honest reason instead of "present"
    # (ADR-035 D4 coverage honesty — Codex review).
    if not _surface_has_l4_facts(surface):
        # Still surface the boundary counters: a zero-TU L4 run with exports on
        # record is a degraded link, named on the skipped row, never read clean.
        facts, counters = _surface_boundary_counters(surface)
        return _CheckOutput(
            [],
            "skipped",
            "L4 source surface present but empty (no TUs parsed — clang/castxml "
            "unavailable?); ODR audit not run",
            providers,
            facts,
            counters,
        )

    findings: list[Change] = []
    for conflict in surface.odr_conflicts:
        name = str(conflict.get("qualified_name", "")) or "<anonymous>"
        header = str(conflict.get("header", ""))
        where = f" in {header!r}" if header else ""
        findings.append(
            _change(
                ChangeKind.ODR_TYPE_VARIANT,
                name,
                f"Type {name!r} has divergent per-translation-unit definitions"
                f"{where}: the source-replay surface recorded different layouts for "
                "the same type. Linking code that mixes them is undefined behavior — "
                "a consumer compiled against one layout silently reads the other. "
                "Reconcile the definitions (usually a macro/flag that changes the "
                "type per TU).",
                new_value=name,
                confidence=Confidence.MEDIUM,
                caused_by_type=name,
                source_location=header or None,
            )
        )
    findings.sort(key=lambda c: (c.symbol, c.source_location or ""))
    detail = (
        f"L4 per-TU type layouts: {len(findings)} type(s) with divergent "
        "cross-TU definitions (ODR conflict)"
    )
    # Carry the boundary integrity counters (ADR-035 D4) onto the coverage row so
    # the rendered ScanResult names a degraded link: an L4 surface can carry parsed
    # decls yet match zero exports (the oneDAL shape) — a clean ODR pass over it
    # must still show matched_symbols == 0.
    facts, counters = _surface_boundary_counters(surface)
    return _CheckOutput(findings, "present", detail, providers, facts, counters)


# ---------------------------------------------------------------------------
# public_to_internal_dependency — public API reaches an internal entity (L5).
# ---------------------------------------------------------------------------

#: L5 edge kinds that express a decl→decl/type dependency. They are produced
#: only by an S4/S5 semantic pass (``call_graph``/AST augmentation), so a
#: structural-only graph carries none of them — the check then skips with a soft
#: advisory rather than reading clean (ADR-035 D4 coverage honesty).
#: Sourced from ``source_graph.DEPENDENCY_EDGE_KINDS`` (ADR-041 P0) so this
#: intra-version check and the version-over-version diff never drift apart on
#: what "reaches an internal entity" means.
_DEPENDENCY_EDGE_KINDS = DEPENDENCY_EDGE_KINDS

#: Graph node kinds that carry a declaration/type visibility we can classify.
#: Sourced from ``source_graph.DECL_NODE_KINDS`` (ADR-041 P0 slice 2, fourth
#: Codex review) — see the note on ``_DEPENDENCY_EDGE_KINDS`` above.
_DECL_NODE_KINDS = DECL_NODE_KINDS

#: Node visibilities that put an entity *on* the public source surface. Mirrors
#: ``source_link._is_public`` (which the L5 graph's ``visibility`` attr is
#: derived from): ``generated`` means a generated header **under the public
#: roots** — a public, consumer-visible entity — so it is NOT an internal
#: dependency. Sourced from ``source_graph.PUBLIC_VISIBILITIES``.
_PUBLIC_VISIBILITIES = PUBLIC_VISIBILITIES

#: Node visibilities that make an entity *internal* (not public surface).
#: Sourced from ``source_graph.INTERNAL_VISIBILITIES``.
_INTERNAL_VISIBILITIES = INTERNAL_VISIBILITIES

#: Visibilities that carry no provenance. Sourced from
#: ``source_graph.UNANNOTATED_VISIBILITIES``.
_UNANNOTATED_VISIBILITIES = UNANNOTATED_VISIBILITIES

#: Whether *name* is a standard-library / compiler-internal decl spelling.
#: Sourced from ``source_graph.looks_like_system_name`` so the intra-version
#: and inter-version checks agree on what "looks like stdlib/system" means.
_looks_system = looks_like_system_name


def _norm_path(path: str) -> str:
    """Normalize a path for cross-source comparison: forward slashes, no ``./``."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_matches(candidate: str, changed: frozenset[str]) -> bool:
    """Whether *candidate* refers to one of the *changed* paths.

    Graph node labels are often absolute build/clang paths (``/work/src/foo.cc``)
    while ``scan`` passes ``git diff --name-only`` repo-relative paths
    (``src/foo.cc``); match when either is a path-component suffix of the other,
    so the two spellings line up without a basename-only false hit (mirrors
    ``source_replay._path_matches`` — kept local to avoid importing that heavy,
    castxml-dependent module into this pure engine; Codex review).
    """
    if not candidate:
        return False
    c = _norm_path(candidate)
    for ch in changed:
        n = _norm_path(ch)
        if c == n or c.endswith("/" + n) or n.endswith("/" + c):
            return True
    return False


def _decl_declaring_files(
    graph: SourceGraphSummary, node_by_id: dict[str, GraphNode]
) -> dict[str, str]:
    """Map each decl/type id to its declaring file via ``SOURCE_DECLARES`` edges.

    Delegates to ``source_graph.decl_declaring_files`` (ADR-041 P0 slice 2);
    *node_by_id* is accepted for call-site compatibility but no longer needed —
    the shared implementation derives it from *graph*.
    """
    return decl_declaring_files(graph)


#: Whether the decl is public: exported-symbol-mapped or public-header
#: visible. Sourced from ``source_graph.is_public_dependency_node`` (ADR-041
#: P0 slice 2, fourth Codex review) so this and the version-over-version diff
#: classify a node identically.
_is_public_decl = is_public_dependency_node

#: Whether the decl is an internal entity consumers cannot see. Sourced from
#: ``source_graph.is_internal_dependency_node`` (ADR-041 P0 slice 2, fourth
#: Codex review).
_is_internal_decl = is_internal_dependency_node


def _decl_label(node_id: str, node_by_id: dict[str, GraphNode]) -> str:
    """The node's human-readable label, falling back to its id."""
    node = node_by_id.get(node_id)
    return node.label if node and node.label else node_id


def _internal_decl_file(
    node_id: str, node_by_id: dict[str, GraphNode], decl_to_file: dict[str, str]
) -> str:
    """Declaring file of the internal decl, for changed-path elevation."""
    # Declaring file from SOURCE_DECLARES (L4), else the call-graph node's
    # source-location ``def_file`` — so changed-file elevation also works for
    # a call-graph-only internal helper (Codex review).
    changed_file = decl_to_file.get(node_id, "")
    if not changed_file:
        node = node_by_id.get(node_id)
        if node is not None:
            changed_file = str(node.attrs.get("def_file", ""))
    return changed_file


def _public_to_internal_change(
    pub: str, internal: str, changed_file: str, is_changed: bool, edge_kind: str
) -> Change:
    """Build the PUBLIC_TO_INTERNAL_DEPENDENCY finding for one public→internal edge.

    Names *edge_kind* — the concrete graph edge proving the dependency (a
    call, a non-call reference, or a field/base/parameter type) — in the
    description (ADR-041 P0 roadmap item 3, "graph explain proof path"), so
    the finding shows *how* the dependency was proved, not just that it was.
    """
    note = (
        f" — {internal!r} is declared in changed file {changed_file!r}, so the "
        "API's behavior may have shifted this revision"
        if is_changed
        else ""
    )
    return _change(
        ChangeKind.PUBLIC_TO_INTERNAL_DEPENDENCY,
        pub,
        f"Public API {pub!r} depends on internal entity {internal!r} "
        "(declared in a private header / source file, not the public "
        f"surface) via a {edge_kind} edge{note}. Consumers cannot see it, so a "
        "change to it is an undeclared behavioral risk. Make the dependency "
        "public or sever it.",
        new_value=internal,
        confidence=Confidence.HIGH if is_changed else Confidence.MEDIUM,
        # Only stamp source_location when the internal entity was actually
        # changed this revision — otherwise the finding is about the public
        # API (``symbol``), and pointing the location at the unchanged
        # private file would mislead SARIF/suppression matching (review).
        source_location=changed_file if is_changed else None,
    )


def _check_public_to_internal_dependency(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """A public/exported decl that reaches an internal entity via the L5 graph, RISK.

    Reads the source graph's decl-dependency edges (``DECL_CALLS_DECL`` /
    ``DECL_REFERENCES_DECL`` / ``DECL_HAS_TYPE`` / ``TYPE_HAS_FIELD_TYPE`` /
    ``TYPE_INHERITS``, the last two folded by ``type_graph.py``, ADR-041 P0):
    when a *public* declaration or type
    (public-header visibility, or one mapped to an exported binary symbol) points
    at an *internal* declaration/type (private-header or source-file visibility;
    ``generated`` is a public generated header and excluded), the public surface
    depends on something consumers cannot see —
    a behavioral risk, elevated when the internal entity is among the revision's
    changed files (``cfg.changed_paths``).

    The dependency edges only exist after an S4/S5 semantic pass. With a
    structural-only graph (or no graph at all) the check skips with a soft
    advisory naming what to enable — it is never counted clean.
    """
    providers = [PROVIDER_SOURCE_INDEX]
    graph = (
        snapshot.build_source.source_graph
        if snapshot.build_source is not None
        else None
    )
    if graph is None:
        return _CheckOutput(
            [],
            "skipped",
            "no L5 source graph on the snapshot (run --depth source)",
            providers,
        )
    if not any(e.kind in _DEPENDENCY_EDGE_KINDS for e in graph.edges):
        return _CheckOutput(
            [],
            "skipped",
            "L5 source graph has no decl-dependency edges — run a semantic source "
            "mode with clang++ available (`--depth source`, or `--source-abi "
            "--source-graph summary` with `collect`) or fold in a pre-captured "
            "Kythe/CodeQL call graph; a structural-only graph emits no call edges",
            providers,
        )

    node_by_id = {n.id: n for n in graph.nodes}
    exported_decls = {
        e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    decl_to_file = _decl_declaring_files(graph, node_by_id)

    findings: list[Change] = []
    seen: set[tuple[str, str]] = set()
    for e in graph.edges:
        if e.kind not in _DEPENDENCY_EDGE_KINDS:
            continue
        if not _is_public_decl(e.src, node_by_id, exported_decls):
            continue
        if not _is_internal_decl(e.dst, node_by_id, exported_decls, decl_to_file):
            continue
        pub = _decl_label(e.src, node_by_id)
        internal = _decl_label(e.dst, node_by_id)
        key = (pub, internal)
        if key in seen:
            continue
        seen.add(key)
        changed_file = _internal_decl_file(e.dst, node_by_id, decl_to_file)
        is_changed = _path_matches(changed_file, cfg.changed_paths)
        findings.append(
            _public_to_internal_change(pub, internal, changed_file, is_changed, e.kind)
        )
    findings.sort(key=lambda c: (c.symbol, c.new_value or ""))
    detail = (
        f"L5 reachability: {len(findings)} public declaration(s) depending on an "
        "internal entity"
    )
    if cfg.changed_paths:
        detail += f" ({len(cfg.changed_paths)} changed path(s) considered)"
    return _CheckOutput(findings, "present", detail, providers)


# ---------------------------------------------------------------------------
# unversioned_exported_symbol — export with no version though a scheme exists.
# ---------------------------------------------------------------------------


def _check_unversioned_exported_symbol(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """Exported symbols with no version node though the library is versioned, RISK.

    When the ELF carries a symbol-versioning scheme (``.gnu.version_d`` defines at
    least one real version node — ``snapshot.elf.versions_defined``, which already
    excludes the ``VER_FLG_BASE`` soname entry), an exported symbol that carries no
    version is a hygiene smell: it cannot be evolved compatibly later, since
    consumers bind the bare name with no version guarantee. ELF-only — PE/Mach-O
    have no GNU symbol versioning — so it skips cleanly on every other format and
    on an unversioned ELF.
    """
    providers = [PROVIDER_BINARY_EXPORTS]
    elf = snapshot.elf
    if elf is None:
        return _CheckOutput(
            [],
            "skipped",
            "symbol versioning is ELF-only; no ELF symbol table",
            providers,
        )
    if not elf.versions_defined:
        return _CheckOutput(
            [], "present", "library defines no symbol-versioning scheme", providers
        )

    from ..elf_symbol_filter import is_abi_relevant_elf_symbol

    findings: list[Change] = []
    for sym in elf.symbols:
        if not sym.name or not sym.is_default or sym.version:
            continue
        if sym.visibility not in ("default", "protected"):
            continue
        # Reuse the shared ELF ABI-relevance filter so linker/runtime artifacts
        # (`_edata`/`_end`/`_init`, GCC internals, weak transitive stdlib, private
        # `__` C symbols) don't produce noisy hygiene failures (Codex review), and
        # skip C++ structor artifacts the same way the public-export check does.
        if not is_abi_relevant_elf_symbol(sym.name):
            continue
        if _is_cxx_implementation_symbol(sym.name):
            continue
        findings.append(
            _change(
                ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
                sym.name,
                f"Symbol {sym.name!r} is exported with no version node even though the "
                f"library defines a versioning scheme ({len(elf.versions_defined)} "
                "version(s)). Add it to the version script so it can be evolved "
                "compatibly — or hide it if it is not public API.",
                new_value=sym.name,
                confidence=Confidence.MEDIUM,
            )
        )
    findings.sort(key=lambda c: c.symbol)
    detail = (
        f"binary exports ↔ version table: {len(findings)} exported symbol(s) with no "
        f"version under a {len(elf.versions_defined)}-node scheme"
    )
    return _CheckOutput(findings, "present", detail, providers)


# ---------------------------------------------------------------------------
# rtti_for_internal_type — typeinfo/vtable exported for a private-header type.
# ---------------------------------------------------------------------------

#: Itanium RTTI symbol prefixes: typeinfo (``_ZTI``), vtable (``_ZTV``), VTT
#: (``_ZTT``), typeinfo-name (``_ZTS``). Each is followed by the encoded type.
_RTTI_PREFIXES = ("_ZTI", "_ZTV", "_ZTT", "_ZTS")


def _typeinfo_type_tokens(symbol: str) -> tuple[str, ...]:
    """Matchable type tokens an Itanium RTTI symbol names, most-specific first.

    Pure (no demangler subprocess): strips the RTTI prefix and an optional nested
    ``N…E`` wrapper, then parses the length-prefixed ``<len><name>`` segments.
    Returns the **fully-qualified** spelling first, then the bare leaf, so a
    private ``detail::Internal`` is matched on its qualified name even when a
    public ``api::Internal`` shares the leaf token (``_private_type_names``
    suppresses the bare alias in that collision — Codex review):
    ``_ZTI6Widget`` → ``("Widget",)``; ``_ZTIN6detail8InternalE`` →
    ``("detail::Internal", "Internal")``. Empty for forms it cannot parse.
    """
    rest = ""
    for p in _RTTI_PREFIXES:
        if symbol.startswith(p):
            rest = symbol[len(p) :]
            break
    else:
        return ()
    if rest.startswith("N"):
        rest = rest[1:]
    segments: list[str] = []
    i = 0
    while i < len(rest) and rest[i].isdigit():
        j = i
        while j < len(rest) and rest[j].isdigit():
            j += 1
        n = int(rest[i:j])
        if j + n > len(rest):
            break
        segments.append(rest[j : j + n])
        i = j + n
    if not segments:
        return ()
    qualified = "::".join(segments)
    leaf = segments[-1]
    return (qualified,) if qualified == leaf else (qualified, leaf)


def _check_rtti_for_internal_type(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """Exported RTTI (typeinfo/vtable) for a private-header type, RISK.

    A polymorphic type declared only in a private / non-installed header should
    not leak its run-time type information onto the ABI surface: consumers cannot
    name the type, yet its ``_ZTI``/``_ZTV`` is exported, bloating the export set
    and risking cross-module RTTI/``dynamic_cast`` coupling to an internal class.
    Reuses :func:`_private_type_names` (private/non-public-generated origin,
    public-collision-safe) so it only fires for a genuinely internal type, and
    needs provenance + an export table — skips cleanly otherwise.
    """
    providers = [PROVIDER_BINARY_EXPORTS, PROVIDER_PUBLIC_HEADER_AST]
    if not _origin_resolvable(snapshot):
        return _CheckOutput([], "skipped", _NO_PROVENANCE, providers)
    exported = _exported_symbol_names(snapshot)
    if exported is None:
        return _CheckOutput(
            [], "skipped", "no binary export table on the snapshot", providers
        )
    private_types = _private_type_names(snapshot)
    if not private_types:
        return _CheckOutput(
            [], "present", "no private-header types declared in the snapshot", providers
        )

    findings: list[Change] = []
    seen: set[tuple[str, str]] = set()
    for sym in sorted(exported):
        if not sym.startswith(_RTTI_PREFIXES):
            continue
        # Try the fully-qualified spelling first, then the bare leaf, so a private
        # ``detail::Internal`` matches even when a public ``api::Internal`` shares
        # the leaf (Codex review).
        canonical = next(
            (
                c
                for tok in _typeinfo_type_tokens(sym)
                if (c := private_types.get(tok)) is not None
            ),
            None,
        )
        if canonical is None:
            continue
        key = (sym, canonical)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            _change(
                ChangeKind.RTTI_FOR_INTERNAL_TYPE,
                sym,
                f"Symbol {sym!r} exports run-time type information for type "
                f"{canonical!r}, which is declared only in a private (non-installed) "
                "header. Its typeinfo leaks onto the ABI surface though consumers "
                "cannot name the type — hide the type or stop exporting its RTTI.",
                new_value=canonical,
                confidence=Confidence.MEDIUM,
                caused_by_type=canonical,
            )
        )
    n_private = len(set(private_types.values()))
    detail = (
        f"typeinfo exports ↔ private-header provenance: {len(findings)} RTTI symbol(s) "
        f"for one of {n_private} private type(s)"
    )
    return _CheckOutput(findings, "present", detail, providers)


# ---------------------------------------------------------------------------
# identity_collision_detected — two distinct decls share one identity() key.
# ---------------------------------------------------------------------------


def _check_identity_collision(
    snapshot: AbiSnapshot, cfg: CrosscheckConfig
) -> _CheckOutput:
    """Two distinct declarations linked onto the same L4 identity() key, RISK.

    ``source_link._route_declaration`` detects (but never resolves) the rare
    case where two *different* declarations — proven distinct because each
    carries a different clang-computed USR — fold onto the same
    ``SourceEntity.identity()`` key (the mangled name, else
    ``qualified_name#signature_hash``, else the bare qualified name; see
    ``SourceAbiSurface.identity_collisions``' docstring). This turns each
    recorded collision into a finding so it is visible in reports rather than
    silently merging two declarations' evidence under one name (ADR-041 P1
    #5). Needs the L4 source-ABI surface; skips cleanly on a snapshot that
    carries no source-replay evidence.
    """
    providers = [PROVIDER_SOURCE_INDEX]
    surface = (
        snapshot.build_source.source_abi if snapshot.build_source is not None else None
    )
    if surface is None:
        return _CheckOutput(
            [],
            "skipped",
            "no L4 source-ABI surface on the snapshot (run --depth source)",
            providers,
        )
    if not _surface_has_l4_facts(surface):
        facts, counters = _surface_boundary_counters(surface)
        return _CheckOutput(
            [],
            "skipped",
            "L4 source surface present but empty (no TUs parsed — clang/castxml "
            "unavailable?); identity-collision audit not run",
            providers,
            facts,
            counters,
        )

    findings: list[Change] = []
    for collision in surface.identity_collisions:
        identity = str(collision.get("identity", "")) or "<unknown>"
        qname = str(collision.get("qualified_name", "")) or identity
        usr_a = str(collision.get("usr_a", ""))
        usr_b = str(collision.get("usr_b", ""))
        findings.append(
            _change(
                ChangeKind.IDENTITY_COLLISION_DETECTED,
                qname,
                f"Two distinct declarations (USR {usr_a!r} and {usr_b!r}) were both "
                f"linked onto the L4 identity key {identity!r}. The identity fallback "
                "chain accepts this rare collision by design for unmangled "
                "cross-scope declarations — any L4/L5 finding attributed to this "
                "identity may actually describe either declaration; treat it as "
                "ambiguous between the two USRs above.",
                new_value=identity,
                confidence=Confidence.MEDIUM,
            )
        )
    findings.sort(key=lambda c: c.symbol)
    facts, counters = _surface_boundary_counters(surface)
    detail = (
        f"L4 identity() collisions: {len(findings)} distinct-declaration "
        "collision(s) detected"
    )
    return _CheckOutput(findings, "present", detail, providers, facts, counters)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_NO_PROVENANCE = (
    "no public-header provenance (supply --public-header/--public-header-dir so "
    "declarations are classified)"
)


def _origin_resolvable(snapshot: AbiSnapshot) -> bool:
    """Whether provenance classification ran (any non-UNKNOWN origin present).

    ``ScopeOrigin`` is only populated when a public-header set was supplied
    (ADR-024 D1); without one every declaration is ``UNKNOWN`` and the
    origin-based checks must skip rather than emit noise.
    """
    if not snapshot.from_headers:
        return False
    for fn in snapshot.functions:
        if fn.origin != ScopeOrigin.UNKNOWN:
            return True
    for var in snapshot.variables:
        if var.origin != ScopeOrigin.UNKNOWN:
            return True
    for rec in snapshot.types:
        if rec.origin != ScopeOrigin.UNKNOWN:
            return True
    return False


#: Mangling sigils: Itanium C++ (``_Z…``) and MSVC (``?…``). A non-extern-C
#: declaration whose ``mangled`` lacks one of these is a castxml fallback to the
#: display name (notably for constructors/destructors), not a comparable symbol.
_MANGLE_SIGILS = ("_Z", "?")

#: Itanium constructor (``C1``/``C2``/``C3``) and destructor (``D0``/``D1``/``D2``)
#: encodings — used to skip structor exports castxml cannot reliably mangle.
_STRUCTOR_RE = re.compile(r"_ZN.*?[CD][0-4]E")

#: Compiler-generated C++ ABI artifacts that belong to a class, not to a
#: free function/variable: vtables/typeinfo/VTT/construction-vtables/thunks (Itanium
#: ``_ZTV``/``_ZTI``/``_ZTS``/``_ZTT``/``_ZTC``/``_ZTh``/``_ZTv``/``_ZTc``) and MSVC
#: ``??_`` vftable/vbtable/RTTI/deleting-dtor names. castxml records the owning class
#: as a ``RecordType`` (not a ``Function``/``Variable``), so these would never be in
#: the documented symbol set and must be exempted from ``exported_not_public`` when
#: they belong to a *native* class (a leaked *dependency* construction vtable is
#: caught earlier by the external-dependency origin check — Codex review).
_CXX_ARTIFACT_PREFIXES = (
    "_ZTV",
    "_ZTI",
    "_ZTS",
    "_ZTT",
    "_ZTC",
    "_ZTh",
    "_ZTv",
    "_ZTc",
    "??_",
)


def _bare_name_exports(decl: Function | Variable) -> bool:
    """Whether *decl* legitimately exports under its bare (un-mangled) name.

    Data (variables) and ``extern "C"`` functions do; a C++ function must carry a
    real mangled symbol.
    """
    return isinstance(decl, Variable) or getattr(decl, "is_extern_c", False)


def _looks_mangled(decl: Function | Variable) -> bool:
    """Whether *decl* carries a real export symbol (not a castxml display-name fallback).

    castxml leaves some C++ members (notably ctors/dtors) unmangled and falls
    back to the bare display name, so ``mangled`` then equals a source spelling
    rather than ``_ZN…``/``?…``. Bare-name exporters (data, ``extern "C"``) are
    fine with any non-empty name (Codex review).
    """
    if _bare_name_exports(decl):
        return bool(decl.mangled)
    return decl.mangled.startswith(_MANGLE_SIGILS)


def _is_cxx_implementation_symbol(symbol: str) -> bool:
    """Whether *symbol* is a compiler-generated C++ class artifact, not free surface.

    Covers constructors/destructors (Itanium ``_ZN…C1Ev``/``…D1Ev``, MSVC
    ``??0…``/``??1…``) — which castxml leaves unmangled on the header side — and
    vtable/typeinfo/thunk artifacts (``_CXX_ARTIFACT_PREFIXES``), which belong to
    a ``RecordType`` rather than a function/variable. Both classes of symbol would
    otherwise false-positive in ``exported_not_public`` (Codex review).
    """
    if symbol.startswith(_CXX_ARTIFACT_PREFIXES):
        return True
    if symbol.startswith("_ZN") and _STRUCTOR_RE.match(symbol):
        return True
    return symbol.startswith(("??0", "??1"))


def _candidate_symbols(decl: Function | Variable) -> tuple[str, ...]:
    """Export symbols *decl* could provide, for matching against the export table.

    Keyed on whether ``mangled`` is a *real* mangling (``_Z…`` / ``?…``): a C++
    function or namespace/global variable exports under its mangled name only, so
    its bare source spelling must **not** be added (an unrelated accidental export
    sharing that spelling would otherwise look documented — Codex review). An
    un-mangled decl (C / ``extern "C"`` / C data, where the extractor left the
    bare name) exports under that bare name.
    """
    if decl.mangled.startswith(_MANGLE_SIGILS):
        return (decl.mangled,)
    return tuple({s for s in (decl.mangled, decl.name) if s})


def _l4_reconciled_symbols(snapshot: AbiSnapshot, exported: set[str]) -> set[str]:
    """Decl mangled names the L4 source-linker tied to a *currently-exported* symbol.

    ``public_not_exported`` compares each public-header decl's mangled name against
    the *literal* binary export table. But the L4 source-ABI linker
    (``source_link``) reconciles spellings the table lists under a *variant*: a
    ctor/dtor ABI clone (a decl mangled ``C1``/``C4`` whose binary lists only
    ``C2``), a Mach-O leading underscore, or an ABI-tag / substitution drift caught
    by demangled identity. A decl the linker resolved to an export *is* exported,
    so flagging it as missing would be a two-way-reconciliation false positive.

    Crucially, the mapping is trusted **only when the current snapshot still
    exports the mapped symbol**: a ``merge`` pack whose ``exported_symbols`` were
    pre-set is *not* relinked, so its L4 mapping can reference an older/different
    binary — a symbol the current binary no longer exports must still be flagged or
    a consumer hits an undefined symbol (Codex review). ``sym`` is checked against
    the current default export set both directly (ELF) and with one leading
    underscore stripped (the Mach-O export table drops one, e.g. ``__ZN…`` →
    ``_ZN…``). Returns the decl mangled names (Mach-O-normalized to the L2
    ``Function.mangled`` spelling). Empty when no L4 surface is attached, so it can
    only *suppress* a false positive, never add one (ADR-028 D3).
    """
    pack = snapshot.build_source
    surface = pack.source_abi if pack is not None else None
    if surface is None:
        return set()
    # The single-leading-underscore strip only applies on Mach-O, whose export
    # table drops one `_` from every symbol. On ELF/PE the mapped `sym` is already
    # the raw exported spelling, so applying the strip there would over-match: a
    # stale mapping to a leading-underscore C symbol `_bar` (no longer exported)
    # would be reconciled whenever an unrelated `bar` is exported, wrongly
    # suppressing a real public_not_exported (Codex review). Gate on the platform.
    is_macho = getattr(snapshot, "macho", None) is not None
    mapping = (surface.mappings or {}).get("source_decl_to_binary_symbol") or {}
    reconciled: set[str] = set()
    for key, sym in mapping.items():
        if not sym:
            continue
        # The mapped symbol must be in the CURRENT default export table (directly,
        # or — on Mach-O only — with the single leading underscore stripped as
        # `_exported_symbol_names` does); otherwise the mapping is stale evidence
        # from another binary and must not suppress the finding.
        present = sym in exported or (
            is_macho and sym.startswith("_") and sym[1:] in exported
        )
        if present:
            reconciled.add(key[1:] if key.startswith("__Z") else key)
    return reconciled


def _has_export_obligation(fn: Function) -> bool:
    """Whether *fn* promises a dynamic symbol (so absence from exports is a risk).

    Conservative on purpose (ADR-035 D4): exclude everything that legitimately
    emits no exported symbol — inline, pure-virtual, deleted, static, non-public
    access, mangle-less, and template-shaped declarations.

    Deliberately **not** gated on ``visibility``: castxml derives
    ``Visibility.PUBLIC`` from the export table, so the very decl this check looks
    for (declared public, *not* exported) is ``HIDDEN``/``ELF_ONLY`` here —
    gating on PUBLIC would skip every real case (Codex review). The obligation
    rests on the public-header source declaration instead.
    """
    if fn.access != AccessLevel.PUBLIC:
        return False
    if fn.origin != ScopeOrigin.PUBLIC_HEADER:
        return False
    # ``static`` free functions have internal linkage and emit no dynamic
    # symbol, so a static header helper must not be read as a missing export
    # (Codex review).
    if fn.is_static:
        return False
    if fn.is_inline or fn.is_pure_virtual or fn.is_deleted:
        return False
    if not fn.mangled:
        return False
    # A C++ member whose ``mangled`` is just the display name is a castxml
    # fallback (notably ctors/dtors); comparing that bare name against the
    # binary's real ``_ZN…`` symbols would false-positive (Codex review).
    if not _looks_mangled(fn):
        return False
    # Template instantiations are spelled with angle brackets; an uninstantiated
    # template emits no symbol, so skip anything template-shaped to stay low-FP.
    if _looks_templated(fn.name):
        return False
    return True


def _var_has_export_obligation(var: Variable) -> bool:
    """Whether *var* is genuine extern data that must export a symbol.

    ``const``/``constexpr`` header constants are excluded: at namespace scope a
    ``const`` variable has internal linkage in C++ (and a ``constexpr`` is
    ``const``), so it is inlined into consumers and emits no exported symbol. The
    exclusion keys off ``is_const`` alone — castxml stores the initializer in
    ``snapshot.constants`` rather than ``Variable.value``, so ``value`` is often
    ``None`` for a real parsed constant and must not be required (Codex review).
    Not gated on ``visibility`` — same export-table-derived-visibility reason as
    :func:`_has_export_obligation`.
    """
    if var.access != AccessLevel.PUBLIC:
        return False
    if var.origin != ScopeOrigin.PUBLIC_HEADER:
        return False
    if not var.mangled:
        return False
    if not _looks_mangled(var):
        return False
    if var.is_const:
        return False
    if _looks_templated(var.name):
        return False
    return True


def _looks_templated(name: str) -> bool:
    """Whether *name* is a template instantiation spelling (``Foo<int>``), not an operator.

    A bare ``<`` is not enough: ``operator<``, ``operator<<``, and ``operator<=>``
    legitimately contain one but are ordinary (non-template) functions with a real
    exported symbol, so testing ``"<" in name`` would wrongly skip a genuinely
    missing exported operator (Codex review). A template's ``<`` opens an argument
    list immediately after the template name, so the token right before the first
    ``<`` is the template's name — never ``operator``.
    """
    idx = name.find("<")
    if idx == -1:
        return False
    return not name[:idx].rstrip().endswith("operator")


def _abi_relevant_build_flags(snapshot: AbiSnapshot) -> list[str] | None:
    """ABI-relevant build-option keys, or ``None`` when there is no L3 evidence."""
    pack = snapshot.build_source
    if pack is None or pack.build_evidence is None:
        return None
    return sorted(
        opt.key for opt in pack.build_evidence.build_options if opt.abi_relevant
    )


#: Pointer/reference/array/cv decorators stripped to reach a base type spelling.
_DECORATOR_RE = re.compile(r"[*&\[\]]")
_BUILTIN_WORDS = frozenset(
    {
        "const",
        "volatile",
        "struct",
        "class",
        "union",
        "enum",
        "unsigned",
        "signed",
        "void",
        "bool",
        "char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "int",
        "long",
        "float",
        "double",
        "auto",
    }
)


def _function_type_refs(fn: Function) -> set[str]:
    """All type strings named in *fn*'s signature (return + parameters)."""
    refs: set[str] = set()
    if fn.return_type:
        refs.add(fn.return_type)
    for p in fn.params:
        if p.type:
            refs.add(p.type)
    return refs


def _base_type_tokens(type_str: str) -> set[str]:
    """Reduce a type spelling to the identifier tokens it could be naming.

    Yields both the full canonical spelling (``ns::Widget``) and its trailing
    segment (``Widget``) so a private record named either way is matched, while
    builtin keywords and template-argument punctuation are dropped.
    """
    cleaned = _DECORATOR_RE.sub(" ", type_str)
    cleaned = cleaned.replace("<", " ").replace(">", " ").replace(",", " ")
    tokens: set[str] = set()
    for raw in cleaned.split():
        tok = raw.strip()
        if not tok or tok in _BUILTIN_WORDS:
            continue
        tokens.add(tok)
        if "::" in tok:
            tokens.add(tok.rsplit("::", 1)[1])
    return tokens


def _referenced_private_types(
    type_refs: set[str], private_types: dict[str, str]
) -> list[str]:
    """Canonical private type names referenced by any of *type_refs*, deduped.

    *private_types* maps every matchable token (canonical spelling *and* its
    trailing segment) to the one canonical name, so a reference to
    ``ns::detail::Impl`` and a bare ``Impl`` both resolve to a single finding.
    """
    hit: set[str] = set()
    for ref in type_refs:
        for tok in _base_type_tokens(ref):
            canonical = private_types.get(tok)
            if canonical is not None:
                hit.add(canonical)
    return sorted(hit)


#: Origins that put a type *on* the public surface, so a same-named definition
#: elsewhere is not a leak. Only ``PUBLIC_HEADER`` qualifies — provenance maps a
#: public generated header to ``PUBLIC_HEADER`` first, so a bare ``GENERATED``
#: origin is a non-public generated header (Codex review).
_PUBLIC_TYPE_ORIGINS = frozenset({ScopeOrigin.PUBLIC_HEADER})

#: Origins whose type, if exposed by a public API, is a private-header leak: a
#: project-private header or a *non-public* generated header (e.g.
#: ``build/generated/internal_config.h``). System headers are excluded (they are
#: third-party, not the library's own un-shipped surface).
_PRIVATE_TYPE_ORIGINS = frozenset({ScopeOrigin.PRIVATE_HEADER, ScopeOrigin.GENERATED})


def _private_type_names(snapshot: AbiSnapshot) -> dict[str, str]:
    """Map matchable token → canonical name for records/enums only in private headers.

    Each private type contributes its canonical name and (when namespaced) its
    trailing segment, both pointing at the canonical name so a match on either
    spelling collapses to one finding. "Private" here is a project-private or
    non-public generated header (``_PRIVATE_TYPE_ORIGINS``).

    A type that *also* has a public-header declaration is excluded — the common
    opaque-handle/PIMPL pattern forward-declares ``class Impl;`` in a public
    header and defines it privately, so a public API taking ``Impl *`` is not a
    leak (Codex review).

    Public-type *tokens* (canonical names **and** their trailing segments) are
    collected first, and any private canonical name or trailing-segment alias that
    collides with one is skipped. That stops a public ``Impl`` and a private
    ``detail::Impl`` from registering the bare token ``Impl`` as a leak candidate,
    which would mis-flag a public ``Impl *`` signature (Codex review).
    """
    # (name, origin) for every record and enum, so both type kinds are scanned
    # with one pass and no `object`-typed merged iteration.
    type_decls = [(rec.name, rec.origin) for rec in snapshot.types] + [
        (en.name, en.origin) for en in snapshot.enums
    ]

    public_tokens: set[str] = set()

    def _add_public(name: str) -> None:
        if not name:
            return
        public_tokens.add(name)
        if "::" in name:
            public_tokens.add(name.rsplit("::", 1)[1])
        # Template instantiations: also reserve the base spelling (``ns::Box`` and
        # ``Box`` for ``ns::Box<int>``) so a private ``detail::Box<…>`` cannot
        # register a bare alias that collides with a public template's base
        # (Codex review — template RTTI matching).
        if "<" in name:
            _add_public(name.split("<", 1)[0])

    for name, origin in type_decls:
        if origin in _PUBLIC_TYPE_ORIGINS:
            _add_public(name)

    names: dict[str, str] = {}

    def _register(name: str) -> None:
        # A type that *also* has a public-header declaration (PIMPL forward-decl)
        # is on the public surface — register nothing (Codex review).
        if name in public_tokens:
            return
        canonical = name

        def _alias(token: str) -> None:
            if token and token not in public_tokens:
                names.setdefault(token, canonical)

        names[name] = canonical
        if "::" in name:
            _alias(name.rsplit("::", 1)[1])
        # Template instantiation (``detail::Box<int>``): also match its base
        # spelling, because an Itanium RTTI symbol (``_ZTIN6detail3BoxIiEE``) and
        # a signature token reduce to ``detail::Box``/``Box`` without the template
        # arguments — so the base must resolve back to the instantiation to catch
        # exported RTTI / leaked references to internal template types (Codex
        # review). Public-collision-guarded via ``public_tokens`` above.
        if "<" in name:
            base = name.split("<", 1)[0]
            _alias(base)
            if "::" in base:
                _alias(base.rsplit("::", 1)[1])

    for name, origin in type_decls:
        if origin in _PRIVATE_TYPE_ORIGINS and name:
            _register(name)
    return names


def _coverage_row(
    check: str,
    status: str,
    detail: str,
    *,
    facts: int = 0,
    counters: dict[str, int] | None = None,
) -> dict[str, Any]:
    """One serialized coverage row for a check (ADR-035 D4 coverage honesty).

    ``facts``/``counters`` are optional D4 boundary-integrity numbers, added only
    when non-empty so existing rows stay byte-for-byte unchanged.
    """
    row: dict[str, Any] = {
        "layer": f"crosscheck:{check}",
        "status": status,
        "detail": detail,
    }
    if facts:
        row["facts"] = facts
    if counters:
        row["counters"] = dict(counters)
    return row

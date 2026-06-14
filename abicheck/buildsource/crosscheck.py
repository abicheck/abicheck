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

==============================  =====================================  ==========
Check                           Inputs                                  Tier
==============================  =====================================  ==========
``exported_not_public``         binary exports ↔ L2 header decls         RISK
``public_not_exported``         L2 header decls ↔ binary exports         RISK
``header_build_context_mismatch`` L2 header context ↔ L3 build flags     API_BREAK
``private_header_leak``         public API ↔ private-header provenance   RISK
==============================  =====================================  ==========

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

#: Cross-check fact-schema version. Independent of every other buildsource
#: schema version (see ``buildsource/CLAUDE.md`` "Versioning").
CROSSCHECK_VERSION: int = 1

# -- check + provider vocabulary ---------------------------------------------

CHECK_EXPORTED_NOT_PUBLIC = "exported_not_public"
CHECK_PUBLIC_NOT_EXPORTED = "public_not_exported"
CHECK_HEADER_BUILD_CONTEXT_MISMATCH = "header_build_context_mismatch"
CHECK_PRIVATE_HEADER_LEAK = "private_header_leak"

#: Every check the engine knows, in cheapest-first order (ADR-035 D4 table).
ALL_CHECKS: tuple[str, ...] = (
    CHECK_EXPORTED_NOT_PUBLIC,
    CHECK_PUBLIC_NOT_EXPORTED,
    CHECK_HEADER_BUILD_CONTEXT_MISMATCH,
    CHECK_PRIVATE_HEADER_LEAK,
)

#: The §6.8 provider-agreement vocabulary (ADR-035 D4) — which evidence source
#: corroborates a finding, driving its confidence tag.
PROVIDER_BINARY_EXPORTS = "binary_exports"
PROVIDER_PUBLIC_HEADER_AST = "public_header_ast"
PROVIDER_DEBUG_INFO = "debug_info"
PROVIDER_BUILD_CONFIG = "build_config"
PROVIDER_SOURCE_INDEX = "source_index"


@dataclass(frozen=True)
class CrosscheckConfig:
    """Which cross-checks run, and the per-check finding cap.

    ``enabled`` defaults to every check; the orchestrator (Phase 3 ``scan``)
    narrows it from the ``crosschecks:`` config block. ``max_per_check`` caps a
    single check's findings so a pathological library cannot flood the report;
    0 disables the cap.
    """

    enabled: frozenset[str] = frozenset(ALL_CHECKS)
    max_per_check: int = 200


@dataclass(frozen=True)
class _CheckOutput:
    """One check's result: findings, its coverage row, and the providers used."""

    findings: list[Change]
    status: str  # "present" | "skipped"
    detail: str
    providers: list[str]


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
        result.coverage.append(_coverage_row(name, status, detail))
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

    findings: list[Change] = []
    for sym in sorted(exported):
        if sym in public_syms or _is_cxx_implementation_symbol(sym):
            continue
        decl: Function | Variable | None = decl_by_sym.get(sym)
        where = ""
        if decl is not None:
            kind = "function" if isinstance(decl, Function) else "variable"
            where = f" (declared as {kind} {decl.name!r} in a non-public header)"
        findings.append(
            _change(
                ChangeKind.EXPORTED_NOT_PUBLIC,
                sym,
                f"Symbol {sym!r} is exported by the binary but declared in no "
                f"public header{where}. It is accidental ABI surface — hide it "
                "(visibility/version script) or document it.",
                new_value=sym,
                confidence=Confidence.HIGH,
            )
        )
    detail = (
        f"binary exports ↔ public headers: {len(findings)} exported symbol(s) "
        "with no public declaration"
    )
    return _CheckOutput(findings, "present", detail, providers)


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

    findings: list[Change] = []
    for fn in snapshot.functions:
        if not _has_export_obligation(fn):
            continue
        if fn.mangled not in exported:
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
        if var.mangled not in exported:
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
    if snapshot.build_source is not None and snapshot.build_source.source_graph:
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
#: free function/variable: vtables/typeinfo/VTT/thunks (Itanium ``_ZTV``/``_ZTI``/
#: ``_ZTS``/``_ZTT``/``_ZTh``/``_ZTv``/``_ZTc``) and MSVC ``??_`` vftable/vbtable/
#: RTTI/deleting-dtor names. castxml records the owning class as a ``RecordType``
#: (not a ``Function``/``Variable``), so these would never be in the documented
#: symbol set and must be exempted from ``exported_not_public`` (Codex review).
_CXX_ARTIFACT_PREFIXES = (
    "_ZTV",
    "_ZTI",
    "_ZTS",
    "_ZTT",
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


def _exported_symbol_names(snapshot: AbiSnapshot) -> set[str] | None:
    """The binary's exported symbol names, or ``None`` if no export table exists.

    Only **default/unversioned** ELF exports count toward the obligation set: a
    symbol that exists *only* as a non-default version alias (``foo@LIB_1``,
    ``is_default == False``) does not satisfy an unversioned consumer link
    (which needs ``foo@@…``), so including it would mask the exact
    missing-export case this set feeds (Codex review).

    Mach-O names are normalized the same way the dumper normalizes
    ``Function.mangled`` (strip the platform's single leading underscore:
    ``_foo`` → ``foo``, ``__Z...`` → ``_Z...``) so the comparison set matches the
    header-side mangled spelling instead of flagging every C/C++ symbol as
    missing (Codex review).
    """
    if snapshot.elf is not None:
        return {s.name for s in snapshot.elf.symbols if s.name and s.is_default}
    if snapshot.pe is not None:
        return {e.name for e in snapshot.pe.exports if e.name}
    if snapshot.macho is not None:
        return {
            e.name[1:] if e.name.startswith("_") else e.name
            for e in snapshot.macho.exports
            if e.name
        }
    return None


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
    for name, origin in type_decls:
        if origin in _PUBLIC_TYPE_ORIGINS and name:
            public_tokens.add(name)
            if "::" in name:
                public_tokens.add(name.rsplit("::", 1)[1])

    names: dict[str, str] = {}

    def _register(name: str) -> None:
        if name in public_tokens:
            return
        names[name] = name
        if "::" in name:
            segment = name.rsplit("::", 1)[1]
            # Skip a trailing-segment alias that collides with a public type's
            # name/segment — the public type owns that bare spelling.
            if segment not in public_tokens:
                names.setdefault(segment, name)

    for name, origin in type_decls:
        if origin in _PRIVATE_TYPE_ORIGINS and name:
            _register(name)
    return names


def _change(
    kind: ChangeKind,
    symbol: str,
    description: str,
    *,
    old_value: str | None = None,
    new_value: str | None = None,
    source_location: str | None = None,
    confidence: Confidence = Confidence.MEDIUM,
    caused_by_type: str | None = None,
    evidence_category: str = "source_only",
) -> Change:
    """Build a cross-check :class:`Change` with the shared metadata defaults."""
    return Change(
        kind=kind,
        symbol=symbol,
        description=description,
        old_value=old_value,
        new_value=new_value,
        source_location=source_location,
        confidence=confidence,
        caused_by_type=caused_by_type,
        evidence_category=evidence_category,
    )


def _coverage_row(check: str, status: str, detail: str) -> dict[str, Any]:
    """One serialized coverage row for a check (ADR-035 D4 coverage honesty)."""
    return {
        "layer": f"crosscheck:{check}",
        "status": status,
        "detail": detail,
    }

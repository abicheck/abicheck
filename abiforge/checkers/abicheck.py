"""The abicheck adapter: the only abiforge module that imports abicheck.

abiforge's core (the space model, the edit operators, their declared ground
truth, the compiler/execution oracle and the coverage accounting) is written
from the C/C++ language and platform ABI rules, not from any checker. A
checker under test plugs in through an adapter that answers four questions:

* ``snapshot`` -- what the checker records for one built library;
* ``answer`` -- its verdict and finding kinds for an old/new pair;
* ``kind_catalog`` -- the finding vocabulary it can report (for coverage);
* ``header_backend_problem`` -- why one of its header backends cannot run here.

The operators' expectations are spelled in abicheck's ``ChangeKind``
vocabulary today; a second checker needs a translation of that vocabulary
(see ``abiforge/README.md`` "Checking other tools").

``tests/test_abiforge_boundaries.py`` fails when any other abiforge module
imports abicheck, or when abiforge imports ``tests`` or ``pytest``.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from abiforge.model.finding import Finding

if TYPE_CHECKING:
    from abicheck.checker_types import DiffResult
    from abicheck.model.change import Change
    from abicheck.model.snapshot import AbiSnapshot

#: The verdict an adapter returns when the checker refuses to compare.
REFUSED = "REFUSED"

#: Run the checker's full public dump workflow instead of its library entry
#: point (record-layout enrichment included).
PUBLIC_DUMP_ENV = "ABICHECK_EDIT_CORPUS_PUBLIC_DUMP"


def _consumer_baseline_facts(consumer: Path, baseline: Path):
    from abicheck.extract.consumer_imports import read_consumer_imports
    from abicheck.extract.library_export_facts import read_library_export_facts
    from abicheck.model.availability import FactStatus
    old = read_library_export_facts(baseline)
    imports = read_consumer_imports(consumer, old.soname)
    if old.status != FactStatus.PRESENT or imports.status != FactStatus.PRESENT:
        raise ValueError(f"could not read baseline or consumer: {baseline}, {consumer}")

    return imports, old


def consumer_required_names(consumer: Path, baseline: Path) -> set[str]:
    """Return the requirements scoped to the actual baseline provider."""
    from abicheck.policy.consumer_requirement_evaluation import (
        scope_requirements_to_library,
    )

    imports, old = _consumer_baseline_facts(consumer, baseline)
    return scope_requirements_to_library(imports, old).undefined_symbols


def consumer_missing_symbol_probe(consumer: Path, baseline: Path):
    """Read the baseline once for a series of candidate symbol-removal checks."""
    from abicheck.extract.library_export_facts import read_library_export_facts
    from abicheck.model.availability import FactStatus
    from abicheck.policy.consumer_requirement_evaluation import (
        evaluate_consumer_requirements,
    )

    imports, old = _consumer_baseline_facts(consumer, baseline)

    def probe(candidate: Path) -> list[str]:
        new = read_library_export_facts(candidate)
        if new.status != FactStatus.PRESENT:
            raise ValueError(f"could not read candidate exports: {candidate}")
        return evaluate_consumer_requirements(imports, old, new, ()).missing_symbols

    return probe


@dataclass
class ElfReleaseSurface:
    soname: str
    exports: list[tuple[str, str, str, str, int]]
    versions_required: dict[str, list[str]]
    imports: list[tuple[str, str, str, str]]
    needed: list[str]
    rpath: str
    runpath: str


def elf_release_surface(path: Path) -> ElfReleaseSurface:
    """Read dynamic symbols, version requirements and loader metadata once."""
    from abicheck.elf_metadata import parse_elf_metadata

    metadata = parse_elf_metadata(path)
    exports = []
    for symbol in metadata.symbols:
        name = symbol.name
        if symbol.version:
            name += ("@@" if symbol.is_default else "@") + symbol.version
        exports.append((name, symbol.sym_type.value, symbol.binding.value, symbol.visibility, symbol.size))
    imports = [
        (symbol.name, symbol.binding.value, symbol.version, symbol.version_soname)
        for symbol in metadata.imports
    ]
    return ElfReleaseSurface(
        metadata.soname, exports, metadata.versions_required, imports,
        metadata.needed, metadata.rpath, metadata.runpath,
    )


def pe_declaration_names(path: Path) -> tuple[list[str], list[str]]:
    """Return the header-free PE dump's function and variable link names."""
    from abicheck.workflows.dump.pe import extract_pe

    declarations = extract_pe(path, "audit").declarations
    return (
        [function.mangled for function in declarations.functions],
        [variable.mangled for variable in declarations.variables],
    )


def pe_loader_surface(path: Path) -> tuple[list[tuple[str, int, str]], dict[str, list[str]]]:
    """Return export identities, forwarding destinations and ordinary imports."""
    from abicheck.pe_metadata import parse_pe_metadata

    meta = parse_pe_metadata(path)
    exports = [(entry.name, entry.ordinal, entry.forwarder) for entry in meta.exports]
    return exports, meta.imports


def snapshot(
    so: Path,
    headers: Sequence[Path],
    *,
    version: str,
    is_cxx: bool,
    backend: str,
    gcc_options: str | None,
    build_info: Path | None = None,
    sources: Path | None = None,
    follow_deps: bool = False,
) -> AbiSnapshot:
    """abicheck's snapshot of one built library, made the way users get it.

    *build_info* is the build's ``compile_commands.json`` (L3 build data).
    Such a snapshot is made by the typed dump workflow ``abicheck dump -H
    --build-info`` runs, which also parses the headers in the build's own
    compile context and embeds the build facts. *sources* is the build's
    source tree (L4/L5): the same typed workflow ``abicheck dump -H
    --sources`` runs, which embeds the source facts. *follow_deps* resolves
    the library's DT_NEEDED closure as ``abicheck dump --follow-deps`` does,
    searching the library's own directory (where the harness puts it).
    """
    snap = _snapshot(
        so, headers, version, is_cxx, backend, gcc_options, build_info, sources
    )
    if follow_deps:
        from abicheck.dependency_info import populate_dependency_info

        populate_dependency_info(snap, so, [so.parent], None, "")
    return snap


def _snapshot(
    so: Path,
    headers: Sequence[Path],
    version: str,
    is_cxx: bool,
    backend: str,
    gcc_options: str | None,
    build_info: Path | None,
    sources: Path | None,
) -> AbiSnapshot:
    from abicheck.compile_context import CompileContext
    from abicheck.dumper import dump

    headers = list(headers)
    lang = "c++" if is_cxx else "c"
    if build_info is not None or sources is not None:
        from abicheck.workflows.dump.request import run_dump_request
        from abicheck.workflows.request_inputs import InputSpec
        from abicheck.workflows.service_requests import DumpRequest

        return run_dump_request(
            DumpRequest(
                input=InputSpec(
                    path=so,
                    headers=tuple(headers),
                    version=version,
                    build_info=build_info,
                    sources=sources,
                    compile=CompileContext(
                        gcc_options=gcc_options if headers else None,
                        frontend=backend,
                    ),
                ),
                lang=lang,
                frontend=backend,
            )
        )
    if os.environ.get(PUBLIC_DUMP_ENV):
        # The public dump workflow end to end (``run_dump``), record-layout
        # enrichment included.
        from abicheck.workflows.dump.native import run_dump

        snap = run_dump(
            so,
            "elf",
            headers,
            version=version,
            lang=lang,
            lang_explicit=True,
            frontend=backend,
            compile=CompileContext(
                gcc_options=gcc_options if headers else None, frontend=backend
            ),
        )
    else:
        snap = dump(
            so,
            headers,
            version=version,
            compiler="c++" if is_cxx else "cc",
            lang=lang,
            header_backend=backend,
            gcc_options=gcc_options if headers else None,
            # ``abicheck dump -H`` tags each header it is given public
            # (ADR-015 provenance); the bare ``dump()`` only does when told,
            # and without it the cross-source checks (public-not-exported,
            # exported-not-public) never run.
            public_headers=headers or None,
        )
        if headers and backend == "clang":
            # The public dump workflow (``abicheck dump``/``compare``) enriches
            # a clang-backend snapshot with record layout after ``dump()``; the
            # bare ``dumper.dump()`` does not (known-gaps "`dumper.dump()` never
            # runs the clang record-layout pass"). Mirror the workflow, so the
            # corpus judges what users get.
            from abicheck.extract.headers.clang.layout_tool import (
                attach_clang_layout,
            )

            snap = attach_clang_layout(
                snap,
                headers,
                [],
                lang=lang,
                compile=CompileContext(gcc_options=gcc_options),
            )
    if headers:
        # ``abicheck dump -H`` takes the headers it parses as the library's
        # public-header set and classifies every declaration's origin against
        # it (``finish_provenance``); the bare ``dumper.dump()`` leaves origin
        # unknown, so a declared-but-unexported entry has no public-contract
        # fact and its gained export reads as a plain addition. Mirror it.
        from abicheck.workflows.snapshot_factory import finish_provenance

        snap = finish_provenance(snap, headers, None)
    return snap


def _projected(old: AbiSnapshot, new: AbiSnapshot) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Both operands at the evidence rung both reach, as ``abicheck compare`` does."""
    from abicheck.policy.common_evidence_depth import common_evidence_depth
    from abicheck.policy.depth_projection import project_pair_to_depth

    return project_pair_to_depth(old, new, common_evidence_depth(old, new))


def _judge(old: AbiSnapshot, new: AbiSnapshot) -> DiffResult:
    """abicheck's own result for projected operands; raises ``AbicheckError`` on
    refusal, which the callers decide on."""
    from abicheck.checker import compare

    # ``abicheck compare`` always runs the pattern-verdict pass (ADR-068
    # made it unconditional on the CLI); the bare ``checker.compare()``
    # keeps it opt-in so a direct caller can score raw detector output.
    # Mirror the CLI: without it, HANDLE_TYPE_CHANGED,
    # OPAQUE_INVARIANT_BROKEN and every newly-introduced anti-pattern
    # finding can never be reported, whatever the snapshots say.
    extra = _build_data_changes(old, new)
    if extra is None:
        return compare(old, new, pattern_verdicts=True)
    # A pair carrying L3-L5 evidence is compared through the
    # ``abicheck compare`` chokepoint itself (``compare_snapshots``:
    # surface metrics, build-context reconciliation and each side's
    # resolved public surface on top of the pattern verdicts), so the
    # evidence variants judge exactly what the CLI reports.
    from abicheck.workflows.compare_policy import compare_snapshots

    return compare_snapshots(old, new, extra_changes=extra)


def judged(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[str, frozenset[str], frozenset[str], tuple[Finding, ...]]:
    """(verdict name, ChangeKind names, kinds whose ADR-084 downgrade is
    disclosed, each finding with the entity it is attributed to).

    ``REFUSED`` plus the error type on refusal, with nothing disclosed and no
    findings.
    """
    from abicheck.errors import AbicheckError
    from abicheck.policy.binary_gate_marker import BINARY_GATE_RULE_ID

    old, new = _projected(old, new)
    try:
        result = _judge(old, new)
    except AbicheckError as exc:
        return REFUSED, frozenset({type(exc).__name__}), frozenset(), ()
    kinds = frozenset(ch.kind.name for ch in result.changes)
    disclosed = frozenset(
        ch.kind.name
        for ch in result.changes
        if ch.modulation_rule == BINARY_GATE_RULE_ID
    )
    return result.verdict.name, kinds, disclosed, _findings(result)


def _findings(result: DiffResult) -> tuple[Finding, ...]:
    """Each reported change as a structured ``Finding``: kind, the entity it is
    attributed to (the change's symbol and resolved qualified name) and the
    verdict category it contributes, read from the typed result."""
    from abicheck.checker_policy import (
        API_BREAK_KINDS,
        BREAKING_KINDS,
        COMPATIBLE_KINDS,
        RISK_KINDS,
    )
    from abicheck.policy.classification import effective_category

    sets = (
        frozenset(BREAKING_KINDS),
        frozenset(API_BREAK_KINDS),
        frozenset(COMPATIBLE_KINDS),
        frozenset(RISK_KINDS),
    )
    return tuple(
        Finding(
            kind=ch.kind.name,
            symbol=ch.symbol,
            qualified_name=ch.qualified_name,
            impact=effective_category(ch, *sets).name,
            field_name=ch.field_name,
            old_value=None if ch.old_value is None else str(ch.old_value),
            new_value=None if ch.new_value is None else str(ch.new_value),
        )
        for ch in result.changes
    )


def answer(old: AbiSnapshot, new: AbiSnapshot) -> tuple[str, frozenset[str]]:
    """(verdict name, ChangeKind names); ``REFUSED`` plus the error type on refusal.

    A refusal (``abicheck.errors.AbicheckError``, e.g. a profile mismatch) is
    abicheck's answer, not a harness failure: it is neither clean nor a break.
    """
    verdict, kinds, _disclosed, _findings_ = judged(old, new)
    return verdict, kinds


def debug_evidence(snap: AbiSnapshot) -> str:
    """What debug-info evidence the snapshot recorded, per channel.

    A diagnostic for a failing cell: a DWARF-derived kind can only appear if
    the snapshot carries the layout channel it reads.
    """
    from abicheck.model.debug_evidence import debug_info_evidence

    ev = debug_info_evidence(snap)
    return f"basic={ev.basic} advanced={ev.advanced} layout_content={ev.layout_content}"


def function_findings(old: AbiSnapshot, new: AbiSnapshot) -> tuple[str, ...]:
    """Each function-level finding (``FUNC_*``) as ``KIND symbol (qualified name)``.

    A diagnostic for a failing cell only: it names the exact symbols abicheck
    emitted, so a renamed thunk shows up as its removal plus its addition.
    """
    from abicheck.errors import AbicheckError

    old, new = _projected(old, new)
    try:
        result = _judge(old, new)
    except AbicheckError:
        return ()
    return tuple(
        f"{c.kind.name} {c.symbol} ({c.qualified_name or c.description})"
        for c in result.changes
        if c.kind.name.startswith("FUNC_")
    )


def _build_data_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change] | None:
    """The L3-L5 findings ``abicheck compare`` folds in from embedded evidence.

    Only a pair carrying embedded build or source data
    (``snapshot(build_info=..., sources=...)``)
    takes this step, so every other cell is compared as before.
    """
    from abicheck.buildsource.evidence_report import prepare_embedded_build_source
    from abicheck.evidence_depth import embedded_header_graph

    if not any(
        s.build_source is not None or embedded_header_graph(s) is not None
        for s in (old, new)
    ):
        return None

    extra, _rows, _metrics, _ev = prepare_embedded_build_source(
        old, new, "build", None, None, None, None, None
    )
    return list(extra or ())


def kind_names() -> tuple[str, ...]:
    """Every finding kind abicheck can report (``ChangeKind`` names)."""
    from abicheck.model.change_catalog.kinds import ChangeKind

    return tuple(k.name for k in ChangeKind)


def kind_catalog() -> Iterator[tuple[str, str, str]]:
    """(name, value, taxonomy module) for every ``ChangeKind``.

    The taxonomy module is the catalog file that declares the kind
    (``build``, ``platform``, ``symbols``, ``types``, ...), which the coverage
    report uses to place a kind in a scope bucket.
    """
    from abicheck.model.change_catalog import (
        build,
        disposition,
        platform,
        source,
        source_graph_risk,
        symbols,
        types,
    )
    from abicheck.model.change_catalog.kinds import ChangeKind

    modules = {
        "build": build.BUILD_ENTRIES,
        "disposition": disposition.DISPOSITION_ENTRIES,
        "platform": platform.PLATFORM_ENTRIES,
        "source": source.SOURCE_ENTRIES,
        "source_graph_risk": source_graph_risk.SOURCE_GRAPH_RISK_ENTRIES,
        "symbols": symbols.SYMBOLS_ENTRIES,
        "types": types.TYPES_ENTRIES,
    }
    module_of = {e.kind: mod for mod, entries in modules.items() for e in entries}
    for k in ChangeKind:
        yield k.name, k.value, module_of[k.value]


def header_backend_problem(backend: str, driver: str, lang: str) -> str | None:
    """Why abicheck's *backend* header backend cannot run on this host, or None.

    Only the native gcc plugin needs a probe here (it must be built for the
    installed gcc); the other backends are checked by the caller.
    """
    if backend != "gcc":
        return None
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.gcc.discovery import find_plugin

    try:
        find_plugin(driver, lang)
    except (SnapshotError, OSError, subprocess.SubprocessError) as exc:
        return f"gcc header backend: {str(exc).splitlines()[0]}"
    return None


@dataclass(frozen=True)
class ObservableSpec:
    """One ``Observability`` of a kind: its minimal evidence configurations.

    ``alternatives`` are frozensets of evidence atoms (``"B"``, ``"D"``, ``"H"``,
    ``"BD"``, ``"S"``, ``"G"``), an antichain. ``platform`` and ``language``
    restrict the spec to some values of those axes (``None`` = every value).
    """

    alternatives: frozenset[frozenset[str]]
    platform: frozenset[str] | None = None
    language: frozenset[str] | None = None
    arch: frozenset[str] | None = None


def default_verdict(kind_name: str) -> str | None:
    """The catalog's ``default_verdict`` of ``ChangeKind`` *kind_name*, or None."""
    from abicheck.model.change_catalog.assembled import REGISTRY
    from abicheck.model.change_catalog.kinds import ChangeKind

    meta = REGISTRY.get(ChangeKind[kind_name].value)
    return None if meta is None else meta.default_verdict.name


def binary_gated_kind_names() -> frozenset[str]:
    """The kinds ADR-084 caps to COMPATIBLE_WITH_RISK at binary or DWARF depth.

    The policy's own set (``BINARY_GATED``): a binary-producible kind whose
    API_BREAK verdict needs header or source evidence to be backed.
    """
    from abicheck.policy.binary_gated_contract import BINARY_GATED

    return frozenset(k.name for k in BINARY_GATED)


def observable_at(kind_name: str) -> tuple[ObservableSpec, ...] | None:
    """The ``observable_at`` of ``ChangeKind`` *kind_name*, or None when UNCLASSIFIED.

    None is a value, not an empty answer: the kind has no declared minimal
    configuration, so a cell cannot be built for it from its metadata.
    """
    from abicheck.model.change_catalog.assembled import REGISTRY
    from abicheck.model.change_catalog.kinds import ChangeKind

    meta = REGISTRY.get(ChangeKind[kind_name].value)
    if meta is None or not isinstance(meta.observable_at, tuple):
        return None
    return tuple(
        ObservableSpec(
            alternatives=frozenset(
                frozenset(str(s.value) for s in alt) for alt in obs.alternatives
            ),
            platform=(
                None
                if obs.condition is None or obs.condition.platform is None
                else frozenset(str(p.value) for p in obs.condition.platform)
            ),
            language=(
                None
                if obs.condition is None or obs.condition.language is None
                else frozenset(str(x.value) for x in obs.condition.language)
            ),
            arch=(
                None
                if obs.condition is None or obs.condition.arch is None
                else frozenset(str(a.value) for a in obs.condition.arch)
            ),
        )
        for obs in meta.observable_at
    )


def record_return_exports(path: Path, exports: set[str], record_name: str) -> set[str]:
    """Read compiler-confirmed by-value returns from a stored checker snapshot."""
    from abicheck.serialization import load_snapshot

    return {
        f.mangled
        for f in load_snapshot(path).declarations.functions
        if f.mangled in exports
        and f.return_by_value_fact is not None
        and f.return_by_value_fact.value is True
        and f.return_type_identities_fact is not None
        and f.return_type_identities_fact.is_present
        and record_name in (f.return_type_identities_fact.value or ())
    }

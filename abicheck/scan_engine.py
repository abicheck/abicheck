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

"""``scan`` engine core (ADR-035 D10) — classify → always-on tier → pinned level
→ optional baseline compare.

:func:`run_scan_core` is "the one body the CLI, ``service.run_scan``, and the
MCP scan tool share" (ADR-035 D10) — this module is where that body actually
lives. It is deliberately free of ``@click.option`` decorators and argv
parsing so it can be called directly from ``service_scan.run_scan`` without a
front-end dependency: ``cli_scan.py`` (the Click command) and
``service_scan.py`` (the typed request/result API) both import from here,
never from each other.

Historically this lived inside ``cli_scan.py`` alongside the ``scan`` Click
command, which meant ``service_scan.run_scan`` had to reach into a
front-end module (via a function-local import) to call it — the reverse of
the intended frontend → service → engine dependency direction (ADR-037 D1).
Splitting it out here removes that inversion; ``cli_scan.py`` now imports
:func:`run_scan_core` from this module the same way ``service_scan.py`` does.

One pre-existing exception to "no CLI concerns": :func:`_build_new_snapshot`
raises ``click.ClickException`` on a resolve failure, and :func:`run_scan_core`
prints a ``click.echo`` note when ``--baseline`` is combined with ``--audit``.
Both predate this split and are left as-is (unrelated to the dependency-
direction fix) — ``click.ClickException`` is a plain exception subclass safe
to raise outside a running CLI context, and a future cleanup can route that
note through the advisories list like every other cross-cutting message.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from . import deadline
from .buildsource.crosscheck import CrosscheckConfig, run_crosschecks
from .buildsource.pattern_scan import scan_files
from .buildsource.poi import (
    build_points_of_interest,
    resolve_changed_paths_public_impact,
    resolve_symbol_tus,
)
from .buildsource.preprocessor_scan import run_preprocessor_scan
from .buildsource.risk import RiskScore
from .buildsource.scan_levels import (
    EvidenceDepth,
    ScanMode,
    SourceMethod,
    public_depth_value,
)
from .checker_policy import API_BREAK_KINDS, BREAKING_KINDS
from .checker_types import validate_evidence_depth
from .cli_scan_baseline import _expand_public_headers, _run_baseline_compare
from .cli_scan_helpers import (
    _intrinsic_coverage,
    _l3_collected,
    _pack_coverage,
    _source_abi_coverage,
    _uses_debug_presence_only,
    l4_coverage_advisories,
    resolve_effective_allow_query,
    scan_pattern_roots,
)
from .errors import PlanningError, ProfileMismatchError, ScopeMismatchError
from .schemas import SCAN_SCHEMA_VERSION
from .workflows.artifact.execute import SideResolution
from .workflows.artifact.resolve import BaselineReuseContext
from .workflows.plan import scan_bazel_scoping_failure
from .workflows.scan_abort_result import attach_prior_on_budget_overflow

if TYPE_CHECKING:
    from .environment_matrix import EnvironmentMatrix
    from .policy_file import PolicyFile
    from .service_scan import CompileContext
    from .suppression import SuppressionList


def _preprocessor_scan_clang_bin(compile_context: CompileContext | None) -> str:
    """Resolve the ``clang -E``/``clang -M`` binary for the S2 pre-scan from
    the scan's own compile context (D2), instead of a hardcoded ``clang++``.

    Thin wrapper around :func:`abicheck.dumper_clang.resolve_source_frontend_clang_bin`
    (the shared resolver also used for L4 source-ABI replay's ``clang_bin``,
    see ``embed_build_source``'s callers) — see that function's docstring for
    the override rules and rationale. ``compile_context is None`` (no
    ``--compiler``/``--compiler-prefix`` given at all) short-circuits to the
    ``clang++`` fallback directly, same as passing two ``None``s would.
    """
    from .dumper_clang import resolve_source_frontend_clang_bin

    if compile_context is None:
        return "clang++"
    return resolve_source_frontend_clang_bin(
        compile_context.gcc_path, compile_context.gcc_prefix, fallback="clang++"
    )


@dataclass
class ScanOutcome:
    """The composed result of a ``scan`` run, rendered to text or JSON.

    Holds enough to print one coverage- and confidence-annotated report: the
    resolved level, the risk score, the always-on tier results, the optional
    baseline diff, and the combined verdict/exit code.
    """

    mode: str
    resolved_method: str
    depth: str | None
    collect_mode: str
    risk: RiskScore
    auto: bool
    changed_path_count: int
    changed_path_source: str
    coverage: list[dict[str, Any]] = field(default_factory=list)
    pattern: dict[str, Any] = field(default_factory=dict)
    preprocessor: dict[str, Any] = field(default_factory=dict)
    crosscheck: dict[str, Any] = field(default_factory=dict)
    crosscheck_severities: dict[str, str] = field(default_factory=dict)
    poi: dict[str, Any] = field(default_factory=dict)
    advisories: list[str] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)
    audit: bool = False
    diff_summary: dict[str, Any] | None = None
    verdict: str = "COMPATIBLE"
    exit_code: int = 0
    elapsed_s: float = 0.0
    budget_s: float | None = None
    # ADR-047 §7 report-identity envelope (G30 P0.3) — optional, additive.
    # Nothing populates these yet; see DiffResult's matching fields in
    # checker_types.py for the full rationale (shared across compare/scan).
    check_id: str | None = None
    profile_id: str | None = None
    requested_depth: str | None = None
    effective_depth: str | None = None
    baseline_channel: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "scan_schema_version": SCAN_SCHEMA_VERSION,
            "mode": self.mode,
            "level": {
                "source_method": self.resolved_method,
                "depth": self.depth,
                "collect_mode": self.collect_mode,
                "auto": self.auto,
            },
            "risk": self.risk.to_dict(),
            "changed_paths": {
                "count": self.changed_path_count,
                "source": self.changed_path_source,
            },
            "coverage": list(self.coverage),
            "pattern_scan": self.pattern,
            "preprocessor_scan": self.preprocessor,
            "crosscheck": self.crosscheck,
            "crosscheck_severities": dict(self.crosscheck_severities),
            "poi": self.poi,
            "advisories": list(self.advisories),
            "stage_timings": {
                k: round(v, 3) for k, v in sorted(self.stage_timings.items())
            },
            "diff": self.diff_summary,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "elapsed_s": round(self.elapsed_s, 3),
            "budget_s": self.budget_s,
        }
        if self.check_id is not None:
            d["check_id"] = self.check_id
        if self.profile_id is not None:
            d["profile_id"] = self.profile_id
        if self.requested_depth is not None:
            validate_evidence_depth("requested_depth", self.requested_depth)
            d["requested_depth"] = self.requested_depth
        if self.effective_depth is not None:
            validate_evidence_depth("effective_depth", self.effective_depth)
            d["effective_depth"] = self.effective_depth
        if self.baseline_channel is not None:
            d["baseline_channel"] = self.baseline_channel
        return d


def _build_new_snapshot(
    binary: Path,
    headers: list[Path],
    includes: list[Path],
    sources: Path | None,
    collect_mode: str,
    lang: str,
    allow_build_query: bool,
    changed_paths: tuple[str, ...] = (),
    build_info: Path | None = None,
    build_config: Path | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    compile_context: CompileContext | None = None,
    defer_cleanup: list[Callable[[], None]] | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    include_dependencies: bool = False,
    build_targets: tuple[str, ...] = (),
    baseline_reuse_hint: BaselineReuseContext | None = None,
) -> SideResolution:
    """Dump the candidate's L0-L2 surface and embed L3-L5 inline at *collect_mode*.

    Returns the shared resolver's own :class:`SideResolution` — the snapshot,
    the effective includes (carrying any build-derived L2 seed), the effective
    compile context (carrying the P0.3 L3->L2 fold's merged result, when
    applied), and, when *baseline_reuse_hint* is given, the context the
    ``--against`` baseline's own native parse should use. A ``--baseline``
    compare needs all four so it reuses the candidate's extraction recipe
    rather than the caller's original one (Codex review: without this, the
    candidate folded real L3 context while the baseline side parsed with the
    caller's original, un-folded context, silently reintroducing the very
    ``NOT_COMPARABLE``/false-ABI-difference risk this fold exists to close).

    **This function no longer resolves anything itself** (CLI cleanup phase
    two, PR 3A -- dump/scan resolver convergence). It builds this side's
    :class:`~abicheck.api_types.InputSpec`/
    :class:`~abicheck.service_compare_evidence.SideEvidence` and hands them to
    :func:`~abicheck.service_input_resolution._resolve_side_snapshot_impl`, the
    same primitive ``compare``'s implicit-dump operand and ``dump``'s typed
    ``run_dump_request`` already use, instead of calling
    ``service.resolve_input``/``embed_build_source`` directly. Everything that
    used to be hand-written here -- the L2 include/compile seed, the
    ``parsed_with_build_context`` stamp, the ADR-039 build-context collector's
    gate, the "drain the seed's cleanups before the embed step contends on the
    same inferred-build-dir lock" ordering, and the pair-aware baseline-context
    rule -- is inherited from that one implementation. Each of those had
    already needed its own independent correction on this path (see the root
    ``AGENTS.md``'s L3->L2-fold entry, findings 8/12/13/15, and the 2026-08-21
    note recording that ``scan`` never ran the ADR-039 collector at all), which
    is precisely the drift a second copy produces.

    ``scan``'s own long-standing behaviour is preserved exactly, through
    opt-in parameters on that primitive rather than by changing it: its real
    ``collect_mode`` for the L2 seed (so the zero-config inferred build query
    still seeds includes for a compile-DB-less source tree), its ``lang == "c"``
    seed guard, its caller-owned ``defer_cleanup`` list, its ``"auto"`` L4
    extractor, its expanded public-header roots, and its folded compile
    context as the L4 replay compiler selector. See each parameter's own
    docstring paragraph in ``service_input_resolution``.

    The resolved ``changed_paths`` (from ``--changed-path``/``--since``) are
    threaded into the inline source replay so a ``source-changed`` collection
    actually narrows to the affected TUs — the ADR-035 D7 POI-focused cost model —
    instead of falling back to a full ``target`` replay.

    ``build_info`` (an out-of-tree compile DB / build dir / pack) and
    ``build_config`` (a trusted ``.abicheck.yml`` enabling ``build.query``) are
    threaded through so a pinned s5/s6 scan can collect L3/L4 even when the build
    context lives outside ``--sources`` — otherwise it silently degrades to
    partial coverage (Codex review).

    ``build_targets`` (P0.2, lab report follow-up): forwarded to
    ``embed_build_source`` unchanged, scoping L3 collection to the given
    root target(s) the same way `dump --build-target` already does — see
    ``run_scan_core``'s own docstring for the full rationale.
    """
    from .api_types import InputSpec
    from .errors import AbicheckError
    from .header_utils import split_public_header_inputs
    from .service_compare_evidence import SideEvidence
    from .workflows.artifact.execute import _resolve_side_snapshot_impl

    # L4 replay's own public-header roots, kept deliberately WIDER than the
    # L2/crosscheck-origin provenance set (`public_headers`/`public_header_dirs`
    # above -- `_public_provenance_set`'s deliberate "a lone -H file doesn't
    # establish a directory boundary" default, unchanged here). `dump`'s
    # write-time embed and `compare`'s implicit-dump operand both derive their
    # public-header roots via this same, more permissive
    # `split_public_header_inputs` (every `-H` file/dir is a root, no
    # directory required) -- without matching that for L4 specifically, a
    # `dump`-produced baseline for a lone-`-H`-file project correctly links
    # its L4 declarations to binary symbols while `scan`'s own candidate
    # degrades to zero matches, producing a spurious
    # source_decl_binary_symbol_mismatch/source_to_binary_mapping_changed RISK
    # finding on an *unchanged* library purely from this L2-vs-L4 root-set
    # asymmetry (reproduced end-to-end; see
    # `service_input_resolution.embed_side_build_source`'s own
    # `l4_public_headers` docstring paragraph for the full account).
    _l4_split_files, _l4_split_dirs = split_public_header_inputs(list(headers))
    # Union with the caller's own `public_headers`/`public_header_dirs` (the
    # narrower, `_public_provenance_set`-derived set already computed for
    # L2/crosscheck-origin purposes), deduped, rather than *replacing* it --
    # an explicit `--public-header-dir` (or a file `_public_provenance_set`
    # already promoted alongside an activating directory) must still reach
    # L4 even when it isn't itself derivable from the raw `headers` list.
    _l4_header_files = list(dict.fromkeys([*_l4_split_files, *(public_headers or ())]))
    _l4_header_dirs = list(
        dict.fromkeys([*_l4_split_dirs, *(public_header_dirs or ())])
    )

    side = InputSpec(
        path=binary,
        headers=tuple(headers),
        includes=tuple(includes),
        version="",
        sources=sources,
        build_info=build_info,
        build_targets=build_targets,
        # This side's caller-supplied, *pre-fold* L2 context. The ADR-039
        # collector reads it (never the folded result) inside the shared
        # resolver, which is the same rule this function used to enforce by
        # capturing `_user_gcc_option_tokens` before its own fold reassigned
        # `compile_context` (`user_define_flags`' own docstring; the ninth
        # finding on the root AGENTS.md's L3->L2-fold entry).
        compile=compile_context,
        # Filter dependency scope by default, matching `dump`/`compare`
        # (Codex review): without this, scan's candidate defaults to "full"
        # while a `dump`-produced --against baseline defaults to "filtered",
        # so the comparability gate hard-fails the routine "scan against a
        # plain dump'd baseline" workflow. *include_dependencies* itself is
        # derived from the baseline's own explicit tag when one is given (see
        # run_scan_core's _scan_candidate_include_dependencies) so the
        # inverse, explicit `dump --include-system-declarations` baseline
        # workflow isn't hard-broken the other way (Codex review, fresh
        # evidence).
        include_dependencies=include_dependencies,
    )
    evidence = SideEvidence(
        headers=list(headers),
        compile=compile_context,
        collect_mode=collect_mode,
        dump_manifest=None,
    )
    # Bazel target-scoping is checked once, in run_scan_core (the only caller,
    # before its S3/POI work) -- not repeated here as a second, unguarded copy
    # that would reintroduce the depth=binary false positive (Codex review).
    try:
        return _resolve_side_snapshot_impl(
            side,
            evidence,
            lang=lang,
            # `scan` has no `--lang`-was-typed signal, so the *parse* keeps
            # its own auto-detection exactly as before; only the L2 seed is
            # guarded, below, since "c" is never scan's Click default.
            lang_explicit=False,
            # Not passed to `resolve_input` before this migration either --
            # scan's frontend rides in `compile_context.frontend`, and
            # `resolve_input`'s own default is this same "auto".
            header_backend="auto",
            fmt=None,
            public_headers=list(public_headers or ()),
            public_header_dirs=list(public_header_dirs or ()),
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            changed_paths=changed_paths,
            build_config=build_config,
            allow_build_query=allow_build_query,
            # `build_config`'s own *query* field is independently, correctly
            # gated downstream (`collect_inline_pack`'s presence-based
            # `build_config_trusted_for_query`, computed by both
            # `l2_seed._resolve_l2_seed_pack_args` and `cli_buildsource.
            # embed_build_source` regardless of this flag) -- `allow_build_
            # query` itself only ever answers whether `resolve_effective_
            # allow_query` (ADR-037 D4) authorized the config's executable
            # `build.query`, never whether the config may be read at all.
            # Without this, `_resolve_side_snapshot_impl`'s own local gate
            # (`_gated_build_query_inputs`, meant for `dump`/`compare`'s typed
            # API, which has no equivalent CLI-side consent step of its own)
            # would blanket-null `build_config` -- silently dropping an
            # ordinary `scan --config <path>`'s *passive* settings
            # (`build.compile_db`, `build.internal_namespaces`, ...) whenever
            # the config declares no `build.query` at all, the common case
            # (Codex review, fresh evidence -- a real regression this
            # migration introduced, not a hypothetical). See
            # `_gated_build_query_inputs`'s own docstring for the full
            # reasoning.
            build_config_locally_trusted=True,
            baseline_reuse_hint=baseline_reuse_hint,
            # --- scan's own preserved behaviour (PR 3A) ---------------------
            # Its real collect mode, not the Tier-2 pin: `scan` may run the
            # zero-config inferred build query in its seed, which is how a
            # source tree with no compile database gets build-derived include
            # dirs at all.
            seed_collect_mode=collect_mode,
            # `lang` defaults to "c++" (scan's own Click default), so it can't
            # distinguish a genuinely explicit request from the default the
            # way DumpRequest.lang_explicit does -- but "c" is never a
            # default, only ever a real request (mirrors perform_elf_dump's
            # identical squash-guard rule). Without this, an explicit `scan
            # --lang c` against a matched C++ compile unit's own `-std=c++20`
            # would let the derived standard reach a parse scan is explicitly
            # forcing into C mode, which a real compiler rejects outright
            # (Codex review).
            seed_lang_explicit=lang.lower() == "c",
            defer_cleanup=defer_cleanup,
            # scan has always taken `embed_build_source`'s own "auto" default,
            # which `_make_source_extractor` reads as clang; every other
            # resolver passes `effective_frontend`, which resolves "auto" to
            # castxml. Matching them would newly require castxml for a `scan
            # --depth source` that works with clang today -- a real behaviour
            # change for real users, unverifiable without a castxml-capable
            # lane, so it stays a documented divergence (plan, PR 3A) rather
            # than a guessed fix folded into this migration.
            source_extractor="auto",
            # Kept expanded (individual header files). A change to the shared
            # primitive's raw pass-through was landed and reverted for
            # regressing `clang_public_roots._equivalent_public_roots_for_
            # unit`'s mirror detection: a *file* root promotes an equivalent
            # build-tree header on a single sampled match, a *directory* root
            # needs two, so a build include dir mirroring only one header out
            # of a larger public root loses that promotion once the directory
            # stops being pre-expanded (plan, 2026-08-20 note).
            expand_public_header_roots=True,
            # L4 replay must invoke the compiler this scan's own L2 header AST
            # was actually pointed at -- the *folded* context, since the P0.3
            # fold can fill in a matched MSVC/clang-cl driver the caller did
            # not name.
            source_frontend_from_folded_context=True,
            # See this function's own comment above computing
            # _l4_header_files/_l4_header_dirs: L4 replay's root set stays
            # wider than L2/crosscheck-origin provenance, matching dump's/
            # compare's own derivation, so scan doesn't silently degrade to
            # zero L4-matched symbols relative to a dump baseline of the
            # identical project.
            l4_public_headers=_l4_header_files,
            l4_public_header_dirs=_l4_header_dirs,
        )
    except AbicheckError as exc:
        raise click.ClickException(f"Failed to load --binary {binary}: {exc}") from exc


def _scan_candidate_include_dependencies(baseline: Path | None) -> bool:
    """Whether the candidate's own dependency-scope filtering should match a
    ``--against``/``--baseline`` JSON snapshot's *explicit* ``"full"`` tag.

    Defaults to ``False`` (filtered, matching `dump`/`compare`'s own default)
    -- correct for the single most common case: no baseline, a native-binary
    baseline (which now resolves filtered too), or a JSON baseline that is
    itself filtered/untagged. Only a JSON baseline explicitly dumped with
    ``dump --include-system-declarations`` (tagged ``"full"``) needs the candidate
    to go unfiltered too, else the comparability gate hard-fails that
    legitimate, if less common, inverse workflow (Codex review, fresh
    evidence) -- and ``scan`` has no ``--include-system-declarations`` flag of its
    own to let a caller request it directly. A cheap, best-effort JSON peek
    (not a full ``resolve_input``/dump) so this never triggers expensive
    work merely to decide a default; any failure to read/parse falls back to
    the filtered default.

    Deliberately does NOT pre-filter on :func:`cli_scan_baseline.
    _baseline_is_native_library` before attempting the JSON parse (Codex
    review, fresh evidence): that helper's own filename-suffix fallback
    (``".so" in name``, ...) only applies once magic-byte sniffing finds no
    recognized binary format -- exactly the case for a real JSON snapshot
    saved under a library-like name (e.g. a baseline written to
    ``libfoo.so.json`` and then renamed, or just handed a ``libfoo.so``
    path by a caller's own naming convention). Calling it first would skip
    the peek entirely for that baseline, silently keeping the candidate
    filtered against a "full"-tagged snapshot.

    Content-sniffs the first 4 bytes via :func:`binary_utils.
    detect_binary_format` first, though (a real magic-byte check, not the
    filename-fallback heuristic above) -- a real native binary's raw bytes
    would still fail to decode/parse as JSON either way, but only after
    ``json.load`` reads and decodes the *entire* file first; for a large
    native baseline that's a real, avoidable memory/I/O cost merely to
    choose a default (Codex review, fresh evidence). A recognized magic
    number short-circuits straight to the filtered default without ever
    opening the file as text.
    """
    if baseline is None:
        return False
    from .binary_utils import detect_binary_format

    if detect_binary_format(baseline) is not None:
        return False
    # ADR-059 (Codex review, fresh evidence): a gzip/zstd-compressed baseline
    # (`dump --compression gzip|zstd`) fails `detect_binary_format` the same
    # way plain JSON does (neither magic is ELF/PE/Mach-O), so it reaches
    # this point same as before -- but its raw *stored* bytes are compressed,
    # not JSON text, so both the tail-byte-scan trick below and a plain-text
    # `json.load` would silently fail to find `dependency_scope` regardless
    # of its real value, always falling through to the `False` (filtered)
    # default even for a baseline explicitly dumped `--include-system-declarations`
    # (tagged `"full"`). Decode through the canonical snapshot I/O path
    # first for a compressed file -- skipping the tail-scan heuristic
    # entirely (it has no equivalent for compressed content: the *decoded*
    # tail isn't at any fixed offset in the *stored* bytes), going straight
    # to a full decode + parse. This is still cheap relative to a real dump:
    # decompression is fast, and a "full"-tagged snapshot with the largest
    # dependency surface is exactly the case that compresses best.
    from .snapshot_io import SnapshotCompression, detect_snapshot_compression

    try:
        compression = detect_snapshot_compression(baseline)
    except Exception:
        compression = SnapshotCompression.NONE
    if compression is not SnapshotCompression.NONE:
        try:
            from .snapshot_io import read_snapshot_text

            data = json.loads(read_snapshot_text(baseline))
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        return bool(data.get("dependency_scope") == "full")
    # `dependency_scope` (model.py) is declared as one of `AbiSnapshot`'s very
    # last fields -- serialized (via dataclasses.asdict field order) as one
    # of the last keys in the JSON object, right before `schema_version` is
    # appended, well after the (potentially huge) functions/types/DWARF
    # payload. A real `dump`-produced snapshot is `json.dumps(..., indent=2)`
    # (never minified), so the tag is reliably within the file's last ~4KB
    # regardless of how large the payload before it is. Try a cheap tail
    # regex scan first -- avoiding a full json.load for exactly the case
    # Codex flagged as most expensive (an explicitly unfiltered "full"
    # snapshot, which by definition can carry the largest transitive
    # dependency surface) -- and only fall back to the full parse when the
    # tail scan doesn't confidently resolve it (non-standard formatting, a
    # tiny file, the key genuinely absent, ...), so correctness never
    # actually depends on the heuristic.
    try:
        size = baseline.stat().st_size
        with open(baseline, "rb") as f:
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", errors="ignore")
        match = re.search(r'"dependency_scope"\s*:\s*"(full|filtered)"', tail)
        if match is not None:
            return match.group(1) == "full"
        if re.search(r'"dependency_scope"\s*:\s*null', tail) is not None:
            return False
    except OSError:
        pass
    try:
        with open(baseline, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return bool(data.get("dependency_scope") == "full")


def _load_exports_for_poi(path: Path | None, lang: str) -> Any | None:
    """Best-effort cheap load for the D7 export-delta POI walk (ADR-035 D7).

    Loads *path* **header-free** — so no castxml/L2 and no L3-L5 collection, just
    the L0 export tables (and, for a JSON baseline, its embedded L5 graph). The
    export-delta walk in ``build_points_of_interest`` needs both sides' export
    tables *before* the expensive collection runs; this is how it gets the
    candidate's. Returns ``None`` on any failure (a registry-ref baseline, a load
    error, …) so POI focusing simply degrades to changed-paths/triggers/risk —
    it must never break the scan. This is an L0/L1-only read (well below the L4
    cost cliff); the one expensive collection still runs once, below.
    """
    if path is None:
        return None
    from .service import resolve_input

    try:
        return resolve_input(path, [], [], version="", lang=lang, symbols_only=True)
    except Exception:  # noqa: BLE001 - best-effort focusing input, never fatal
        return None


def _resolve_public_impact_tus(
    poi_baseline: Any, changed: list[str]
) -> tuple[str, ...]:
    """Resolve ADR-041 P1 #3's public-entry impact closure to declaring TUs.

    The complement of :func:`~abicheck.buildsource.poi.resolve_symbol_tus`
    (export delta -> declaring TU, the *forward* focusing half): a public
    entry whose own export/declaration is untouched by this diff can still
    transitively depend (via a call, a non-call reference, or a field/base/
    parameter type — the ``DEPENDENCY_EDGE_KINDS`` family) on something the
    diff *did* change. Reading the baseline's cached L5 graph (the same one
    ``resolve_symbol_tus`` reads),
    :func:`~abicheck.buildsource.poi.resolve_changed_paths_public_impact`
    names those impacted public entries; this resolves each back to its own
    declaring file, so the L4 replay + L5 fold seed also covers it — without
    which a narrowed/PR-scoped replay could miss the entry entirely (never
    parsing its TU at all, let alone detecting a body/type-hash correlation
    or a newly-added ``public_api_internal_dependency_added`` on it).

    Best-effort, mirroring ``resolve_symbol_tus``'s degrade contract: returns
    ``()`` whenever the baseline carries no L5 graph or nothing resolves —
    this only ever *adds* to the replay seed, never narrows the existing
    changed-path floor. Resolves each impacted decl's file(s) the same way
    ``resolve_symbol_tus`` does: every ``SOURCE_DECLARES`` edge into it (a
    decl can be declared/defined from more than one file), *plus* its own
    ``def_file``/``source_location`` attr — unconditionally, not only as a
    fallback when no edge exists. A decl declared in a public header but
    defined out-of-line in a separate implementation file needs the L4 seed
    to cover *both*; stopping at the first-found path (via the single-file
    ``decl_declaring_files`` lookup, then skipping the attr check entirely)
    silently dropped the implementation TU whenever a declaring edge also
    existed (Codex review).
    """
    pack = getattr(poi_baseline, "build_source", None)
    graph = getattr(pack, "source_graph", None) if pack is not None else None
    if graph is None or not getattr(graph, "nodes", None):
        return ()
    impacted = resolve_changed_paths_public_impact(changed, graph)
    if not impacted:
        return ()
    from .buildsource.poi import _path_of_location

    node_by_id = {n.id: n for n in graph.nodes}
    files: dict[str, None] = {}
    for e in graph.edges:
        if e.kind == "SOURCE_DECLARES" and e.dst in impacted:
            fn = node_by_id.get(e.src)
            if fn is not None and fn.label:
                files.setdefault(fn.label, None)
    for d in sorted(impacted):
        node = node_by_id.get(d)
        attrs = getattr(node, "attrs", None) or {} if node is not None else {}
        loc = attrs.get("def_file") or attrs.get("source_location")
        if loc:
            path = _path_of_location(str(loc))
            if path:
                files.setdefault(path, None)
    return tuple(files)


def _crosscheck_severity_exit(findings: list[Any], severities: dict[str, str]) -> int:
    """Exit-code floor from cross-checks the maintainer promoted to ``error``.

    A cross-check stays advisory (exit 0) until the maintainer opts it into
    gating with ``--crosscheck KEY=error`` (ADR-035 UX step 7 / D6). Once opted
    in, a finding for that check raises the exit to the source-break tier (2) —
    even for a RISK-class check — so the documented promotion path actually
    gates CI. ``info``/``warning`` never gate.
    """
    gating = {k for k, level in severities.items() if level == "error"}
    if gating and any(f.kind.value in gating for f in findings):
        return 2
    return 0


#: The ``blocking_categories`` member naming a promoted cross-check, for a
#: published gate that a ``--crosscheck KEY=error`` raised rather than a
#: severity category. Deliberately outside ``IssueCategory``'s vocabulary,
#: because it is not one: no severity level produced it. That is already the
#: established shape rather than a new invention -- ``aggregate`` itself
#: publishes ``operational_error`` and ``not_comparable`` the same way, and
#: ``GateInfo.from_report_data`` validates the field as strings, not against a
#: closed set.
CROSSCHECK_BLOCKING_CATEGORY = "promoted_crosscheck"


def _promote_published_gate(diff_summary: dict[str, Any] | None, sev_exit: int) -> None:
    """Raise the published ``diff.severity`` gate to a promoted cross-check's exit.

    Called by ``run_scan_core`` whenever *sev_exit* is positive at all --
    not only when it strictly exceeds the baseline compare's own exit code
    (Codex review, fresh evidence: an earlier revision called this only
    from inside that stricter guard, so a crosscheck that merely *tied* an
    already-blocking exit never reached this function at all, and the tie
    case the ``exit``-block reconstruction below was written to handle was
    unreachable in practice). The actual process exit code/verdict
    promotion stays a strict floor at the call site; this function's own
    job -- keeping the two persisted blocks honest about which axes tied
    for the published code -- is a separate concern from whether the
    crosscheck raised the number.

    A no-op unless this run published a gate at all (severity scheme only).

    Without this the block was written by ``_run_baseline_compare`` from the
    *baseline diff alone* and then contradicted by the promotion just above:
    a passing baseline gate alongside an error-level cross-check published
    ``exit_code: 0, blocking: false`` while the process exited 2. That is not
    merely a cosmetic disagreement -- ``aggregate.GateInfo.from_scan_report``
    now *prefers* this block, so the explicitly gated target read as
    nonblocking and dropped out of ``blocking_targets`` (Codex review). It is
    the same un-blocking failure the nested-block preference was introduced to
    fix, reached by the other route.

    Raises ``exit_code`` only, and only to ``max``: a cross-check promotion
    is a floor (:func:`_crosscheck_severity_exit`), so it can add a blocking
    reason to a gate but never clear one a severity category already
    raised. ``blocking_categories`` gains ``promoted_crosscheck`` on a
    strict raise *or* an exact tie against the gate's existing exit code
    (Codex review, fresh evidence: a tie genuinely co-determined the
    published code and must be named the same way ``diff.exit.reasons``
    already names a tied axis -- an earlier revision's strict ``>`` here
    left the two persisted blocks disagreeing about a real tie once the
    call-site restructuring above made a tie actually reach this
    function). A *strictly higher* existing gate still adds nothing: the
    crosscheck didn't determine that code either.

    Also keeps the persisted ``exit`` block (CLI cleanup phase two, PR E)
    consistent the same way: that block is built from the baseline compare
    alone, before this promotion runs, so a promoted cross-check left it
    naming ``compatibility_gate``/etc. for a code lower than the one
    actually published -- the exact "explains nothing about why the exit
    is N" trap :class:`~abicheck.exit_decision.ExitReason` exists to avoid.

    **Reconstructs the whole block through
    :func:`~abicheck.exit_decision.resolve_exit_decision` rather than
    hand-patching ``code``/``reasons`` in place (Codex review, fresh
    evidence).** An earlier revision only overwrote those two fields,
    which broke :class:`~abicheck.exit_decision.ExitDecision`'s own
    documented invariant that ``code`` equals the max of its contribution
    fields (the three pre-existing ones stayed at their pre-promotion
    values, now summing to less than the new ``code``) and silently
    skipped a promotion that only *ties* the block's existing code (a
    strict ``>`` check, unlike :func:`resolve_exit_decision`'s own
    tie-inclusive fold). Reading the three existing contributions back off
    the persisted dict and re-folding them alongside
    ``crosscheck_promotion_contribution=sev_exit`` reproduces this
    function's "raise, never clear" discipline for free -- ``max`` cannot
    lower ``code``, and any prior crosscheck contribution already in the
    block (in case this function is ever called more than once for the
    same run) is folded in rather than dropped.
    """
    if not isinstance(diff_summary, dict):
        return
    gate = diff_summary.get("severity")
    if isinstance(gate, dict):
        current = gate.get("exit_code")
        # `>=`, not `>` (Codex review, fresh evidence): a crosscheck that
        # only *ties* the gate's existing exit code still genuinely
        # co-determined it and must be named in `blocking_categories` --
        # the same tie-inclusive rule `diff.exit`'s own reasons now follow
        # below. Only raise `exit_code`/`blocking` on a strict `>`, so a
        # tie never re-derives a value that was already correct; a
        # strictly higher existing gate (`current > sev_exit`) still adds
        # nothing, since the crosscheck didn't determine that code either.
        if isinstance(current, int) and sev_exit >= current:
            if sev_exit > current:
                gate["exit_code"] = sev_exit
            gate["blocking"] = True
            cats = gate.get("blocking_categories")
            cats = list(cats) if isinstance(cats, list) else []
            if CROSSCHECK_BLOCKING_CATEGORY not in cats:
                cats.append(CROSSCHECK_BLOCKING_CATEGORY)
            gate["blocking_categories"] = cats
    exit_block = diff_summary.get("exit")
    if isinstance(exit_block, dict):
        compat = exit_block.get("compatibility_contribution")
        coverage = exit_block.get("contract_coverage_contribution")
        assurance = exit_block.get("analysis_assurance_contribution")
        prior_crosscheck = exit_block.get("crosscheck_promotion_contribution")
        if (
            isinstance(compat, int)
            and isinstance(coverage, int)
            and isinstance(assurance, int)
        ):
            from .exit_decision import resolve_exit_decision

            crosscheck_contribution = max(
                sev_exit, prior_crosscheck if isinstance(prior_crosscheck, int) else 0
            )
            diff_summary["exit"] = resolve_exit_decision(
                compatibility_contribution=compat,
                contract_coverage_contribution=coverage,
                analysis_assurance_contribution=assurance,
                crosscheck_promotion_contribution=crosscheck_contribution,
            ).to_dict()


def _audit_exit_code(
    findings: list[Any], severities: dict[str, str]
) -> tuple[str, int, dict[str, Any]]:
    """Verdict/exit/prior-decision dict for the no-baseline path (cross-source tiers).

    Cross-source findings are never ``BREAKING`` on their own (authority rule), so
    an audit can reach at most ``API_BREAK`` (exit 2); ``RISK`` stays advisory (exit
    0) unless the maintainer promoted that check to ``error`` (D6). The dict lets a
    *later* budget overflow preserve these contributions instead of discarding them.
    """
    assert not any(f.kind in BREAKING_KINDS for f in findings), (
        "cross-source findings must never be BREAKING (ADR-035 D1 authority rule)"
    )
    has_api_break = any(f.kind in API_BREAK_KINDS for f in findings)
    crosscheck_exit = _crosscheck_severity_exit(findings, severities)
    exit_code = max(2 if has_api_break else 0, crosscheck_exit)
    from .workflows.scan_abort_result import audit_prior_decision

    verdict = "API_BREAK" if exit_code >= 2 else "COMPATIBLE"
    return verdict, exit_code, audit_prior_decision(has_api_break, crosscheck_exit)


class _BudgetOverflow(Exception):
    """Raised by ``run_scan_core`` when the scan exceeds ``--budget`` (ADR-035 D3).

    A scan-engine signal (not a click concern): the budget is a *failure guard*
    that never shrinks scope, so the core raises and the CLI maps it onto exit 5.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.prior_decision: dict[str, object] | None = None


class _EvidenceContractError(Exception):
    """Raised by ``run_scan_core`` when a *pinned* depth can't collect its evidence.

    ADR-037 D5 (#2 auto-strict): an explicitly-pinned ``--depth``/``--source-method``
    is a contract — if the requested source/build evidence is unavailable the scan
    fails loudly rather than silently degrading to a shallower one. Like
    :class:`_BudgetOverflow`, it is an engine signal the CLI maps onto an error
    exit (a clean ``ClickException``, exit 1) and ``service.run_scan`` maps onto a
    failed :class:`ScanResult`. The implicit ``auto`` default never raises it.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ScanCoreResult:
    """The engine core's typed output — the rendered :class:`ScanOutcome` plus the
    raw cross-source findings and the candidate snapshot, so ``service.run_scan``
    can build a typed ``ScanResult`` without re-running anything."""

    outcome: ScanOutcome
    findings: list[Any]
    snapshot: Any


def _run_abi3_audit(
    new_snap: Any,
    abi3_floor: tuple[int, int],
    binary: Path,
    cc: Any,
) -> None:
    """Run the opt-in stable-ABI (abi3) audit, folding its findings into ``cc``.

    A single-artifact audit of the candidate's CPython imports against a target
    Py_LIMITED_API floor. Its findings ride the cross-check stream: they are
    RISK ``python_stable_abi_violation`` rows, advisory by default (like every
    single-artifact check) and gated only via ``--crosscheck
    python_stable_abi_violation=error``. Requires the --binary to be a CPython
    extension module; --abi3 on a plain library is a usage error.
    """
    py_ext = new_snap.python_ext
    if py_ext is None or not py_ext.is_extension:
        from .python_ext import abi3_precondition_message

        raise _EvidenceContractError(abi3_precondition_message(abi3_floor, binary.name))
    from .diff_python import audit_stable_abi_imports

    abi3_findings = audit_stable_abi_imports(py_ext, abi3_floor)
    cc.findings.extend(abi3_findings)
    # Name the offending symbols in the coverage row (rendered verbatim in
    # text and carried in JSON) so a CI artifact tells the user WHICH import
    # to fix — the cross-check summary only reports a per-kind count, which
    # would otherwise hide the symbol in Change.detail/new_value (Codex
    # review). Capped so a pathological module cannot flood the report.
    offending: list[str] = []
    for f in abi3_findings:
        offending.extend(f.new_value if isinstance(f.new_value, list) else [])
    detail = (
        f"{len(py_ext.cpython_imports)} CPython import(s) audited against "
        f"Py_LIMITED_API {abi3_floor[0]}.{abi3_floor[1]}; "
        f"{len(abi3_findings)} violation finding(s)"
    )
    if offending:
        shown = ", ".join(offending[:20])
        more = f" (+{len(offending) - 20} more)" if len(offending) > 20 else ""
        detail += f" — outside the stable ABI: {shown}{more}"
    cc.coverage.append({"layer": "abi3_audit", "status": "ran", "detail": detail})


def _build_scan_poi(
    baseline: Path | None,
    seeded: bool,
    collect_mode: str,
    binary: Path,
    lang: str,
    changed: list[str],
    risk: RiskScore,
    pattern: Any,
) -> tuple[Any, Any]:
    """Build the D7 points-of-interest work-list + the baseline export view used.

    Returns ``(poi, poi_baseline)``. The export-delta walk needs both sides'
    export tables, so a cheap header-free L0 view of the candidate and baseline is
    loaded only when there is a baseline to diff against (else a wasted parse).
    """
    needs_export_delta_poi = (
        baseline is not None
        and seeded
        and collect_mode in {"source-changed", "graph-full"}
    )
    poi_baseline = (
        _load_exports_for_poi(baseline, lang) if needs_export_delta_poi else None
    )
    poi_candidate = (
        _load_exports_for_poi(binary, lang) if poi_baseline is not None else None
    )
    poi = build_points_of_interest(
        changed_paths=changed,
        risk=risk,
        pattern_triggers=pattern.escalation_triggers,
        baseline=poi_baseline,
        candidate=poi_candidate,
    )
    return poi, poi_baseline


def _append_replay_scope_advisory(
    advisories: list[str],
    seeded: bool,
    collect_mode: str,
    sources: Path | None,
) -> None:
    """Advise (ADR-035 P3) when an unseeded run falls back to headers-only replay.

    Only when L4 replay can actually run (a --sources tree is present) does the
    headers-only fallback apply; firing otherwise would report a replay that never
    happened (CodeRabbit review).
    """
    if not seeded and collect_mode == "source-changed" and sources is not None:
        advisories.append(
            "no --since/--changed-path seed; the L4 replay and the L5 call-graph "
            "pass both cover the public-API surface (headers-only) instead of a "
            "focused diff — cost grows with the project, not the change. Pass "
            "--since <ref> or --changed-path to scope both to the changed TUs."
        )


def _check_scan_evidence_contract(
    advisories: list[str],
    new_snap: Any,
    collect_mode: str,
    pinned_explicit: bool,
    sources: Path | None,
    effective_build_info: Path | None,
    eff_depth_enum: EvidenceDepth,
    resolved: SourceMethod,
) -> None:
    """Fail-loud on a pinned depth with no evidence; else advise on missing L3.

    ADR-037 D5 (#2 auto-strict): a depth the user *explicitly pinned* with no
    source evidence at all (no --sources/--build-info, and the trusted --config
    build.query flow produced no L3) is a usage-contract violation → raise. When a
    source input *was* supplied but L3 still came back empty, that stays a pointed
    advisory naming the remedy. The implicit 'auto' default never errors here.
    """
    if collect_mode == "off":
        return
    gave_source_input = sources is not None or effective_build_info is not None
    l3 = _l3_collected(new_snap)
    if pinned_explicit and not gave_source_input and not l3:
        raise _EvidenceContractError(
            f"pinned depth '{eff_depth_enum.value}' (source-method {resolved.value}) "
            "needs source evidence, but no --sources/--build-info was given — there "
            "is nothing to collect L3/L4/L5 from. Pass --sources <tree> or "
            "--build-info <dir|compile_commands.json> (or a trusted --config whose "
            "build.query this pinned depth auto-enables), or drop the pin / use the "
            "default 'auto' for a best-effort binary scan. (Pinned depths are a "
            "contract.)"
        )
    if gave_source_input and not l3:
        advisories.append(
            f"requested depth '{eff_depth_enum.value}' (source-method "
            f"{resolved.value}) needs an L3 compile database, but none was found — "
            "L3/L4/L5 were skipped. Provide one with --build-info (a "
            "compile_commands.json or build dir), or a trusted --config whose "
            "build.query this pinned depth auto-enables."
        )


def _remaining_budget_s(start: float, budget_s: float | None) -> float | None:
    """Seconds left of ``--budget`` from *now*, or ``None`` if no budget is set.

    Shared by every expensive-stage deadline scope in :func:`run_scan_core` (the
    candidate snapshot build, the baseline comparison) so each one gets the
    budget that is actually left *at that point in the scan*, not the original
    total re-applied fresh (which would let a slow early stage silently grant a
    later stage more time than the user's budget actually leaves).
    """
    return budget_s - (time.monotonic() - start) if budget_s is not None else None


def _check_scan_budget(
    budget: str | None,
    budget_s: float | None,
    elapsed: float,
) -> None:
    """Budget overflow FAILS, never shrinks scope (ADR-035 D3)."""
    if budget_s is not None and elapsed > budget_s:
        raise _BudgetOverflow(
            f"error: --budget {budget} exceeded "
            f"({elapsed:.1f}s > {budget_s:.0f}s). "
            "Pin a shallower level or raise the budget; a budget never silently "
            "shrinks the pinned scope."
        )


def run_scan_core(
    *,
    start: float,
    binary: Path,
    headers: list[Path],
    includes: list[Path],
    public_headers: list[Path],
    public_header_dirs: list[Path],
    sources: Path | None,
    effective_build_info: Path | None,
    build_config: Path | None,
    baseline: Path | None,
    lang: str,
    allow_build_query: bool,
    baseline_headers: list[Path] | None = None,
    baseline_includes: list[Path] | None = None,
    scan_mode: ScanMode,
    resolved: SourceMethod,
    eff_depth_enum: EvidenceDepth,
    collect_mode: str,
    changed: list[str],
    changed_src: str,
    seeded: bool,
    risk: RiskScore,
    is_auto: bool,
    enabled_checks: frozenset[str],
    severities: dict[str, str],
    budget: str | None,
    budget_s: float | None,
    level_explicit: bool = False,
    pinned_explicit: bool = False,
    compile_context: CompileContext | None = None,
    defer_cleanup: list[Callable[[], None]] | None = None,
    abi3_floor: tuple[int, int] | None = None,
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    pattern_verdicts: bool = False,
    env_matrix: EnvironmentMatrix | None = None,
    collapse_versioned_symbols: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    resolved_config: Any = None,
    sev_config: Any = None,
    exit_code_scheme: str = "legacy",
    sibling_exported_symbols: frozenset[str] | None = None,
    max_findings: int | None = None,
    require_complete_analysis: bool = False,
    build_targets: tuple[str, ...] = (),
) -> ScanCoreResult:
    """The shared scan orchestration (classify → always-on tier → level → compare).

    Pure of click/argv: it takes already-resolved inputs, runs the engine, and
    returns a :class:`ScanCoreResult`. Raises :class:`_BudgetOverflow` on budget
    overflow (the CLI maps it to exit 5). This is the one body the CLI,
    ``service.run_scan``, and the MCP scan tool share (ADR-035 D10).

    ``sibling_exported_symbols`` (G35, ``scan --artifact-set`` only via
    ``service_scan.run_scan_set``) is forwarded to the always-on cross-check
    tier's ``CrosscheckConfig`` unchanged — see
    :class:`~abicheck.buildsource.crosscheck.CrosscheckConfig` for what it
    does. ``None``/empty for the single-binary ``scan``/``compare`` paths.

    ``max_findings`` overrides the default ``--against`` report cap (default
    20; see ``cli_scan_baseline._resolve_max_baseline_findings``) and is
    forwarded to ``_run_baseline_compare`` unchanged. ``None`` (the default)
    resolves via the ``ABICHECK_MAX_BASELINE_FINDINGS`` env var, else the
    built-in default. No-op when ``baseline`` is not given.

    ``sev_config``/``exit_code_scheme`` are forwarded, unchanged, to
    ``_run_baseline_compare`` when a ``baseline`` is given — closing the
    asymmetry documented in AGENTS.md's "Known gaps": `scan --against` used
    to compute its exit code from the verdict alone (``legacy_exit_code``)
    regardless of any ``--severity-preset``/``.abicheck.yml`` ``severity:``
    setting, unlike `compare`. ``exit_code_scheme == "severity"`` there now
    uses ``severity.compute_exit_code`` the same way `compare` does; the
    default ``"legacy"`` reproduces the prior, unchanged behavior exactly.
    Orthogonal to the budget/evidence-contract/NOT_COMPARABLE exit codes
    this function already special-cases (5/1/6) — those are returned before
    ever reaching the baseline comparison.

    ``build_targets`` (P0.2, lab report follow-up): the CLI equivalent of
    `dump`'s own ``--build-target`` (``BuildEvidence.target_scope``), scoping
    L3 evidence collection to the given root target(s) and their transitive
    deps instead of a workspace-wide query. `dump`'s own ``embed_build_source``
    call already threaded this through (see ``cli_buildsource.py``); `scan`'s
    identical call below did not, so a `scan --against` a `dump`-produced
    baseline of the same scoped root target(s) previously always ran
    unscoped, capturing unrelated fixture/test targets alongside the real
    library and diverging from the baseline's own scoped evidence. Empty by
    default (the pre-existing, unscoped behavior).
    """
    # ADR-063 Phase 4 (Codex review): checked before S3/POI work, since a typed
    # run_scan()/run_scan_subprocess caller has no cli_scan.py pre-flight.
    if _bf := scan_bazel_scoping_failure(
        headers, eff_depth_enum, collect_mode, effective_build_info, build_targets
    ):
        raise PlanningError((_bf,))

    stage_timings: dict[str, float] = {}

    def _record_stage(name: str, started: float) -> None:
        stage_timings[name] = time.monotonic() - started

    # --- always-on tier: compiler-free pattern pre-scan (S3) ------------------
    # Runs *before* the snapshot build so its escalation triggers feed the D7
    # points-of-interest work-list that focuses the (expensive) source replay.
    # Scope: a *seeded* diff (even an empty one) confines the scan to the changed
    # set — an empty seed (no-op PR) scans nothing, preserving the empty-diff
    # scope; only a genuinely *unseeded* run (no --since/--changed-path) falls
    # back to the whole-tree scan (Codex review).
    pattern_roots = scan_pattern_roots(list(headers), sources, eff_depth_enum)
    _stage = time.monotonic()
    pattern = scan_files(pattern_roots, changed if seeded else None)
    _record_stage("pattern_scan", _stage)

    # --- D7 points-of-interest: cheap facts steer the expensive scan ----------
    # Floor = the directly-changed paths (always included); the pattern triggers,
    # risk score, and the L0↔L2 export deltas only *add* candidates, never drop a
    # changed TU (ADR-035 D7). The export-delta walk needs both sides' export
    # tables up front, so read a cheap, header-free L0 view of the candidate and
    # baseline here (no castxml/L3-L5); the one expensive collection still runs
    # once, below, with the resulting focus seed. The candidate view is only
    # loaded when there is a baseline to diff it against — the delta walk consumes
    # the two together, so loading it baseline-less would be a wasted L0/L1 parse.
    _stage = time.monotonic()
    poi, poi_baseline = _build_scan_poi(
        baseline, seeded, collect_mode, binary, lang, changed, risk, pattern
    )
    _record_stage("poi", _stage)

    # --- build the candidate snapshot (L0-L2 + inline L3-L5 at the level) ------
    # --build-info is the one build-context operand now (the separate
    # --compile-db flag it subsumed is gone): a build dir, a
    # compile_commands.json, or a pack, all feeding embed_build_source's
    # build_info input. The POI path set
    # focuses the replay — but ONLY when a real diff seed was supplied
    # (``seeded``). Without --since/--changed-path the scan is broad by contract
    # (the report says so), so passing pattern-trigger POIs as the changed set
    # would wrongly narrow PR-mode replay to a single pattern-flagged TU and skip
    # source-only checks elsewhere (Codex review). When seeded, the focusing
    # work-list is the changed-path floor *plus* the TUs resolved from changed
    # exports via the baseline's L5 graph (resolve_symbol_tus) — so a changed
    # export with an unchanged header still points the replay at the one TU that
    # emits it (ADR-035 D7, the focusing half) — *plus* the declaring TUs of any
    # public entry the baseline's L5 graph shows transitively depends on a
    # changed file (resolve_changed_paths_public_impact, ADR-041 P1 #3) — the
    # mirror-image walk: an untouched public export whose struct field/base/
    # parameter type or inline body reaches something the diff changed still
    # gets replayed, instead of silently falling outside a narrowed PR scope.
    symbol_tus = resolve_symbol_tus(poi, poi_baseline) if seeded else ()
    impact_tus = _resolve_public_impact_tus(poi_baseline, changed) if seeded else ()
    replay_seed = (
        tuple(dict.fromkeys((*poi.changed_paths(), *symbol_tus, *impact_tus)))
        if seeded
        else ()
    )
    # ADR-035 P3: an unseeded s5/pr run cannot narrow 'source-changed' to a diff,
    # so the L4 replay falls back to the public-API 'headers-only' surface
    # (inline.collect_inline_pack). Record an advisory naming the cost + the knob
    # that focuses it, rather than silently paying a broad replay (validation P3
    # "no auto-warn"). Carried on the result (text + JSON) so it never pollutes a
    # structured-format stdout.
    advisories: list[str] = []
    _append_replay_scope_advisory(advisories, seeded, collect_mode, sources)
    effective_allow_query, _query_advisory = resolve_effective_allow_query(
        allow_build_query, build_config, collect_mode, level_explicit, resolved
    )
    if _query_advisory is not None:
        advisories.append(_query_advisory)

    _stage = time.monotonic()
    # P0 fix: give the expensive L2-L5 collection a *shrinking* deadline (whatever
    # is left of --budget, from *now*, not from `start`) instead of the previous
    # behaviour of only checking `elapsed > budget_s` once the whole scan had
    # already finished. Subprocess call sites reached from _build_new_snapshot
    # (dumper.py's clang/castxml header parse today; future L3/L4 call sites can
    # opt in the same way) read this via deadline.bounded_timeout()/deadline.check()
    # so a pathological header stops itself mid-parse rather than running to
    # completion (or hanging) regardless of --budget.
    try:
        with deadline.deadline_scope(_remaining_budget_s(start, budget_s)):
            _candidate = _build_new_snapshot(
                binary,
                list(headers),
                list(includes),
                sources,
                collect_mode,
                lang,
                effective_allow_query,
                changed_paths=replay_seed,
                build_info=effective_build_info,
                build_config=build_config,
                public_headers=list(public_headers),
                public_header_dirs=list(public_header_dirs),
                compile_context=compile_context,
                defer_cleanup=defer_cleanup,
                symbols_only=eff_depth_enum is EvidenceDepth.BINARY,
                debug_presence_only=_uses_debug_presence_only(eff_depth_enum),
                include_dependencies=_scan_candidate_include_dependencies(baseline),
                build_targets=build_targets,
                # PR 3A blocker 6: the *other* side's resolved scope, handed in
                # so the shared resolver answers the pair-shaped
                # "may the baseline reuse the candidate's folded context"
                # question itself, on `SideResolution.baseline_compile_context`
                # -- rather than this function computing it a second time from
                # the values it got back. Harmless when no baseline is given:
                # nothing reads the answer.
                baseline_reuse_hint=BaselineReuseContext(
                    baseline_headers=tuple(baseline_headers or ()),
                    baseline_includes=tuple(baseline_includes or ()),
                ),
            )
            new_snap = _candidate.snapshot
            eff_includes = list(_candidate.effective_includes)
    except deadline.DeadlineExceeded as exc:
        elapsed = time.monotonic() - start
        raise _BudgetOverflow(
            f"error: --budget {budget} exceeded ({elapsed:.1f}s > {budget_s:.0f}s) "
            "while collecting the candidate snapshot (header/build/source parse). "
            "Pin a shallower level or raise the budget; a budget never silently "
            "shrinks the pinned scope."
        ) from exc
    _record_stage("candidate_snapshot", _stage)
    l4_cov = _source_abi_coverage(new_snap)
    advisories.extend(l4_coverage_advisories(l4_cov))

    # --- level-vs-evidence: fail-loud on missing input, advise otherwise ------
    # A deep depth (build/source/full → collect_mode != "off") needs an L3 compile
    # database; without one the L3/L4/L5 layers cannot be collected.
    _check_scan_evidence_contract(
        advisories,
        new_snap,
        collect_mode,
        pinned_explicit,
        sources,
        effective_build_info,
        eff_depth_enum,
        resolved,
    )

    # --- conditional tier: S2 preprocessor pre-scan (D2) ----------------------
    # Runs only when L3 build evidence + a preprocessor (`clang -E`) are present;
    # otherwise the coverage row honestly reports it skipped (never clean). Emits
    # advisory macro-divergence + private/generated-header-leak facts. Headers are
    # expanded to the individual public header *files* (``-H include/`` accepts a
    # directory) so the per-header leak pass preprocesses each header, not the
    # directory as one bogus TU (Codex review).
    pp_build = (
        new_snap.build_source.build_evidence
        if new_snap.build_source is not None
        else None
    )
    _stage = time.monotonic()
    # Unlike the candidate-snapshot/baseline-compare stages, a deadline
    # overflow here is never re-raised as _BudgetOverflow: preprocessor_scan
    # is advisory (ADR-028 D3) and already degrades per-TU internally
    # (ClangPreprocessorExtractor._run catches deadline.DeadlineExceeded and
    # records a diagnostic instead of propagating) — this scope just gives it
    # the same shrinking, process-group-safe deadline the other stages get,
    # so it can't run its own remaining compile units past the budget.
    with deadline.deadline_scope(_remaining_budget_s(start, budget_s)):
        preproc = run_preprocessor_scan(
            pp_build,
            _expand_public_headers(list(headers)),
            clang_bin=_preprocessor_scan_clang_bin(compile_context),
        )
    _record_stage("preprocessor_scan", _stage)

    # --- always-on tier: intra-version cross-source checks (D4) ---------------
    # The resolved changed-path set is handed to the engine so
    # ``public_to_internal_dependency`` can elevate a finding whose internal
    # target was touched this revision (ADR-035 D4 "L5 reachability ↔ PR
    # changed files").
    # The changed-path set handed to the engine also carries the TUs the D7
    # export-delta walk resolved (symbol_tus), so ``public_to_internal_dependency``
    # elevates a finding whose internal target sits in a TU this revision touched
    # *via a changed export* — not only the literally git-changed files.
    _stage = time.monotonic()
    cc = run_crosschecks(
        new_snap,
        CrosscheckConfig(
            enabled=frozenset(enabled_checks),
            changed_paths=frozenset(changed) | set(symbol_tus),
            sibling_exported_symbols=frozenset(sibling_exported_symbols or ()),
        ),
    )
    _record_stage("crosschecks", _stage)

    # --- stable-ABI (abi3) audit (opt-in via --abi3) --------------------------
    if abi3_floor is not None:
        _run_abi3_audit(new_snap, abi3_floor, binary, cc)

    # --- pinned-level baseline comparison (if any) ----------------------------
    diff_summary: dict[str, Any] | None = None
    audit_prior: dict[str, Any] | None = None
    if baseline is not None and scan_mode is not ScanMode.AUDIT:
        _stage = time.monotonic()
        not_comparable = False
        try:
            # A native --against library is parsed through the same L2 clang/
            # castxml header-AST path as the candidate (Codex review) — without
            # its own deadline scope here, that parse would silently fall back to
            # the unbudgeted fixed timeout even though --budget was given.
            with deadline.deadline_scope(_remaining_budget_s(start, budget_s)):
                verdict, exit_code, diff_summary = _run_baseline_compare(
                    baseline,
                    binary,
                    new_snap,
                    [],
                    lang,
                    collect_mode,
                    list(headers),
                    # Effective (seeded) includes so the baseline native parse gets
                    # the same build-derived dependency include dirs as the
                    # candidate (Codex review).
                    list(eff_includes),
                    list(public_headers),
                    list(public_header_dirs),
                    # The *effective* compile_context (the P0.3 L3->L2 fold's
                    # own merged result, when applied) -- but ONLY when the
                    # baseline actually reuses the candidate's own resolved
                    # header *and* include scope.
                    #
                    # The rule itself, and the three review rounds it took to
                    # get right (content-vs-truthiness on the header axis, and
                    # the independently-built include axis -- the twelfth,
                    # thirteenth and fifteenth findings on the root AGENTS.md's
                    # L3->L2-fold entry), live in ONE place:
                    # `service_input_resolution.BaselineReuseContext` /
                    # `resolve_baseline_compile_context` (PR 3A blocker 6). It
                    # was hand-rolled here as a four-clause boolean, which is
                    # exactly the shape that drifts once a second caller needs
                    # it. Since `_build_new_snapshot` now routes through the
                    # shared resolver (PR 3A), the answer is simply *read off*
                    # the resolution it already returned -- the hint was handed
                    # in at resolve time -- rather than recomputed here from
                    # the values it handed back. One computation, one place.
                    compile_context=_candidate.baseline_compile_context,
                    baseline_headers=baseline_headers,
                    baseline_includes=baseline_includes,
                    symbols_only=eff_depth_enum is EvidenceDepth.BINARY,
                    debug_presence_only=_uses_debug_presence_only(eff_depth_enum),
                    suppression=suppression,
                    policy=policy,
                    policy_file=policy_file,
                    scope_to_public_surface=scope_to_public_surface,
                    force_public_symbols=force_public_symbols,
                    pattern_verdicts=pattern_verdicts,
                    env_matrix=env_matrix,
                    collapse_versioned_symbols=collapse_versioned_symbols,
                    contract_evaluation=contract_evaluation,
                    contract_mode=contract_mode,
                    resolved_config=resolved_config,
                    sev_config=sev_config,
                    exit_code_scheme=exit_code_scheme,
                    max_findings=max_findings,
                    require_complete_analysis=require_complete_analysis,
                    # P0.4 (Codex review): only when the caller genuinely
                    # pinned this depth (an explicit --depth or a non-auto
                    # --source-method), mirroring compare's own "explicit
                    # override, never inferred" discipline for
                    # DiffResult.requested_depth -- an auto-resolved depth
                    # is not this scan's stated request the same way an
                    # explicit pin is.
                    #
                    # `public_depth_value()` (Codex review, fresh evidence):
                    # a typed caller can pin the internal-only FULL/GRAPH
                    # rungs (ScanRequest.depth, or source_method s6/s4),
                    # which `analysis_assurance.py`'s public four-rung
                    # ladder does not recognize -- its `_DEPTH_RANK.get(...,
                    # 0)` would read either as rank 0, the *shallowest*
                    # rung, letting `depth_satisfied`/`status` read as
                    # satisfied/complete for a request the ladder never
                    # actually evaluated. Both normalize to `source`, the
                    # rung this class's own docstring already says they are.
                    requested_depth=(
                        public_depth_value(eff_depth_enum) if pinned_explicit else None
                    ),
                )
        except deadline.DeadlineExceeded as exc:
            elapsed = time.monotonic() - start
            raise _BudgetOverflow(
                f"error: --budget {budget} exceeded ({elapsed:.1f}s > {budget_s:.0f}s) "
                "while parsing the --against baseline (header/build/source parse). "
                "Pin a shallower level or raise the budget; a budget never silently "
                "shrinks the pinned scope."
            ) from exc
        except (ProfileMismatchError, ScopeMismatchError) as exc:
            # ADR-050 D2: not a comparable profile/scope contract -- a hard gate
            # result no promoted cross-check finding (below) can soften/override.
            not_comparable = True
            verdict = "NOT_COMPARABLE"
            exit_code = 6
            from .exit_decision import resolve_scan_exit_decision  # ADR-064 stage 1b

            nc_decision = resolve_scan_exit_decision(not_comparable=True)
            assert nc_decision is not None  # always set when not_comparable=True
            diff_summary = {"reason": str(exc), "exit": nc_decision.to_dict()}
        if not not_comparable:
            # A cross-check the maintainer promoted to `error` (D6) gates the exit
            # even when the baseline diff itself is clean.
            sev_exit = _crosscheck_severity_exit(cc.findings, severities)
            # Refold the persisted `exit` block whenever the crosscheck
            # contributes *anything* positive -- not only when it strictly
            # exceeds the current exit code (Codex review, fresh evidence).
            # A crosscheck that only *ties* the baseline compare's own exit
            # (e.g. both are 2) never reaches the `sev_exit > exit_code`
            # branch below, but `resolve_exit_decision`'s own tie-inclusive
            # fold (inside `_promote_published_gate`) still needs to run for
            # `reasons` to correctly name `promoted_crosscheck` alongside
            # whichever axis already held that code -- decoupled here from
            # the actual exit-code/verdict promotion, which stays a strict
            # floor.
            if sev_exit > 0:
                _promote_published_gate(diff_summary, sev_exit)
            if sev_exit > exit_code:
                exit_code = sev_exit
                # Keep the reported verdict in sync with the promoted exit code so a
                # consumer keying off the verdict string isn't misled (Codex review).
                # `_run_baseline_compare` used to tie exit_code to verdict exactly
                # (BREAKING->4, API_BREAK->2, else->0), so `sev_exit > exit_code`
                # only held when exit_code was 0 -- i.e. verdict was already one of
                # the three non-breaking values, and relabeling it "API_BREAK" was
                # always a genuine promotion.
                #
                # That invariant broke once `exit_code_scheme == "severity"`
                # started feeding `_run_baseline_compare` (Codex review): a
                # `--severity-preset info-only` run can leave a genuinely
                # BREAKING/API_BREAK diff at exit 0, so `exit_code == 0` no longer
                # implies the verdict was non-breaking. Raise the promoted exit
                # code either way (a maintainer-promoted cross-check must still
                # gate), but only relabel the verdict when it wasn't already
                # BREAKING/API_BREAK -- else a severity-demoted BREAKING diff
                # would be misreported as the *less* severe API_BREAK merely
                # because its own severity-gated exit happened to be lower than
                # the cross-check's.
                if verdict not in ("BREAKING", "API_BREAK"):
                    verdict = "API_BREAK"
        _record_stage("baseline_compare", _stage)
    else:
        if baseline is not None:
            click.echo(
                "note: --audit ignores --baseline (intra-version scan).", err=True
            )
        verdict, exit_code, audit_prior = _audit_exit_code(cc.findings, severities)

    elapsed = time.monotonic() - start
    with attach_prior_on_budget_overflow(diff_summary or audit_prior):
        _check_scan_budget(budget, budget_s, elapsed)

    outcome = ScanOutcome(
        mode=scan_mode.value,
        resolved_method=resolved.value,
        depth=eff_depth_enum.value,
        collect_mode=collect_mode,
        risk=risk,
        auto=is_auto,
        changed_path_count=len(changed),
        changed_path_source=changed_src,
        coverage=[
            *_intrinsic_coverage(new_snap),
            pattern.coverage().to_dict(),
            preproc.coverage().to_dict(),
            *_pack_coverage(new_snap),
            *cc.coverage,
        ],
        pattern=pattern.to_dict(),
        preprocessor=preproc.to_dict(),
        crosscheck=cc.to_dict(),
        crosscheck_severities=severities,
        poi=poi.to_dict(),
        advisories=advisories,
        stage_timings=stage_timings,
        audit=scan_mode is ScanMode.AUDIT,
        diff_summary=diff_summary,
        verdict=verdict,
        exit_code=exit_code,
        elapsed_s=elapsed,
        budget_s=budget_s,
    )
    return ScanCoreResult(
        outcome=outcome, findings=list(cc.findings), snapshot=new_snap
    )

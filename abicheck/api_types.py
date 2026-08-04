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

"""Typed request/response structs for the Tier-2 service layer (ADR-037 D2).

"Options are data, not signatures": the service verbs take frozen request
dataclasses instead of an ever-growing list of keyword arguments. A new feature
becomes a new field with a default, never a signature break — and the same
struct is assembled identically from CLI flags, MCP JSON, and direct Python
callers, so a default can no longer silently diverge between front-ends (the
``scope_public`` True-vs-False drift ADR-037 §Context #1 documents).

This module is Phase 1 of the G22 plan: it introduces :class:`InputSpec`,
:class:`CompareRequest` (with :meth:`CompareRequest.validate`), and
:class:`OutputSpec`. Later phases extend ``CompareRequest`` with the depth
(D5), policy/severity (D4), and frontend (D8) fields the ADR sketches — each as
an additive field with a default.

ADR-055 D1 (this module's ``InputSpec.sources``/``build_info``/
``dump_manifest``/``compile``/``public_header_dirs`` and
``CompareRequest.depth``/``frontend_context``) closes the gap that ADR's own
Gap 1 documents: ``CompareRequest`` previously had no way to express
``compare``'s ``--depth``/``--sources``/``--build-info``/``--dump-manifest``/
per-side ``CompileContext`` feature set at all, so a Python caller wanting
that had to fall back to loose keyword arguments on lower-level functions.
``service.run_compare_request`` reads these new fields directly (see its own
docstring for exactly how). A second D1 slice then closed the rest of the
gap against the CLI's own, separately-maintained
``cli_resolve._resolve_compare_snapshots`` — ``dwarf_only``,
``debug_format``, ``include_labels``, and ``--follow-deps`` — so the two
paths now differ in *structure*, not in what they can express. Keeping both,
rather than migrating the CLI onto this one, is a recorded decision:
ADR-055's "Two-resolution-path finding" answers it as option (b), since
rewriting the CLI's most heavily-tested resolution path buys nothing a user
can observe.

ADR-055 D2 adds :class:`CompareResult`, the result side of the same pair, and
D4 adds ``InputSpec.follow_linker_scripts`` — see each one's own docstring.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ValidationError

if TYPE_CHECKING:
    from .checker_types import DiffResult
    from .compile_context import CompileContext
    from .dump_manifest import DumpManifest
    from .model import AbiSnapshot
    from .suppression import SuppressionList

#: Languages the C/C++ frontends accept (mirrors the CLI ``--lang`` choices).
SUPPORTED_LANGS = frozenset({"c", "c++"})

#: AST frontends the ``--ast-frontend`` flag accepts (ADR-037 D8). ``auto`` /
#: ``castxml`` / ``clang`` / ``hybrid`` drive header-AST parsing *and* L4
#: source-ABI replay; ``android`` is a source-ABI-only value (it reuses a
#: pre-captured header-abi dump and has no header-AST path), so selecting it
#: without source inputs is a validation error (D9).
SUPPORTED_FRONTENDS = frozenset({"auto", "castxml", "clang", "hybrid", "android"})

#: ELF debug formats ``--debug-format`` accepts, matching its ``click.Choice``
#: exactly (including ``auto``). Compared case-insensitively, the way that
#: choice is declared ``case_sensitive=False`` -- so an API caller passing
#: ``"DWARF"`` behaves like the CLI caller who typed it, instead of reaching
#: ``dumper_debug._resolve_debug_metadata`` and failing its lowercase-only
#: comparison there (Codex review).
SUPPORTED_DEBUG_FORMATS = frozenset({"auto", "dwarf", "btf", "ctf"})

#: The subset of :data:`SUPPORTED_FRONTENDS` valid for header-AST parsing.
#: "hybrid" (G28 Phase 3) runs both castxml and clang and merges them —
#: without it here, ``run_compare``'s own frontend resolution (below) would
#: silently fall back to "auto" for a "hybrid" request instead of running
#: dumper_hybrid.merge_snapshots.
HEADER_AST_FRONTENDS = frozenset({"auto", "castxml", "clang", "hybrid"})


def _path_tuple(paths: Iterable[Path | str] | None) -> tuple[Path, ...]:
    """Normalise an optional iterable of path-likes into a tuple of ``Path``.

    A bare ``str``/``Path`` is treated as a *single* path, not an iterable of
    characters/parts — so ``headers="include/api.h"`` yields one path, not one
    per character.
    """
    if paths is None:
        return ()
    if isinstance(paths, (str, Path)):
        return (Path(paths),)
    return tuple(Path(p) for p in paths)


@dataclass(frozen=True)
class InputSpec:
    """One side of a comparison: a binary/snapshot path plus its build context.

    Frozen so a request can be hashed/shared without a caller mutating it after
    validation. Use :meth:`of` to build one from loose CLI/MCP values (it
    coerces ``str`` to ``Path`` and ``None`` lists to empty tuples).
    """

    path: Path
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    version: str = ""
    pdb: Path | None = None
    debug_roots: tuple[Path, ...] = ()
    # Mirrors `dump --include-dependencies`/`compare --include-dependencies`
    # (dumper_scoping.py): default True preserves the historical unfiltered
    # behavior for any caller that doesn't opt in; `run_compare` (and the
    # CLI's `--include-dependencies` default False) sets it explicitly.
    include_dependencies: bool = True
    # ADR-055 D1: this side's inline build/source evidence (mirrors
    # `--sources`/`--build-info`, side-scoped like the CLI's own
    # `old=`/`new=` sided values) -- `run_compare_request` embeds them via
    # `cli_buildsource.embed_build_source` when set, at `CompareRequest.depth`'s
    # resolved collect mode. `None` on both sides is a no-op (unchanged
    # behavior from before this field existed).
    sources: Path | None = None
    build_info: Path | None = None
    # ADR-055 D1 / ADR-050 D3: a parsed `--dump-manifest` document for this
    # side only, in place of a single header list -- forwarded directly to
    # `resolve_input`'s own `dump_manifest` parameter, which already supports
    # it (this field is new surface on the *request*, not new resolution
    # logic).
    dump_manifest: DumpManifest | None = None
    # ADR-055 D1: this side's L2 cross-toolchain/AST-frontend override
    # (`--gcc-*`/`--sysroot`/`--nostdinc`/`--ast-frontend`, ADR-037 D3).
    # `None` falls back to whatever `run_compare_request` would otherwise
    # resolve (e.g. its own pair-wide C++20 dialect override) -- this is a
    # per-side override layered on top of that, not a replacement for it.
    compile: CompileContext | None = None
    # ADR-055 D1: additional public-header *directories* beyond what's
    # already inferred by splitting `headers` into files/dirs
    # (`split_public_header_inputs`) -- mirrors `--public-header-dir`.
    public_header_dirs: tuple[Path, ...] = ()
    # ADR-055 D4: whether resolving `path` may follow a GNU ld linker script's
    # INPUT()/GROUP() target to the real library. Default True matches
    # `resolve_input`'s own default (and therefore every pre-existing caller);
    # the MCP server sets it False because it enforces MCP_MAX_FILE_SIZE on the
    # *caller-supplied* path before resolving, and following a script would
    # reach a target that never went through that guard -- a tiny script
    # pointing at a huge library would otherwise defeat the resource limit.
    # Without this field, routing `abi_compare` through `run_compare_request`
    # (D4) would have silently dropped that guard, so it is request surface,
    # not an MCP-local wrapper concern.
    follow_linker_scripts: bool = True

    @classmethod
    def of(
        cls,
        path: Path | str,
        *,
        headers: Iterable[Path | str] | None = None,
        includes: Iterable[Path | str] | None = None,
        version: str = "",
        pdb: Path | str | None = None,
        debug_roots: Iterable[Path | str] | None = None,
        include_dependencies: bool = True,
        sources: Path | str | None = None,
        build_info: Path | str | None = None,
        dump_manifest: DumpManifest | None = None,
        compile: CompileContext | None = None,
        public_header_dirs: Iterable[Path | str] | None = None,
        follow_linker_scripts: bool = True,
    ) -> InputSpec:
        """Build an :class:`InputSpec`, coercing loose front-end values."""
        return cls(
            path=Path(path),
            headers=_path_tuple(headers),
            includes=_path_tuple(includes),
            version=version,
            pdb=Path(pdb) if pdb is not None else None,
            debug_roots=_path_tuple(debug_roots),
            include_dependencies=include_dependencies,
            sources=Path(sources) if sources is not None else None,
            build_info=Path(build_info) if build_info is not None else None,
            dump_manifest=dump_manifest,
            compile=compile,
            public_header_dirs=_path_tuple(public_header_dirs),
            follow_linker_scripts=follow_linker_scripts,
        )


@dataclass(frozen=True)
class OutputSpec:
    """Where/how a result is rendered — the invocation-level output choice.

    ``path is None`` means "write to stdout". Kept deliberately small for
    Phase 1; the rendering verbs still take an explicit format today, but the
    struct gives later phases a single place to grow output options.
    """

    fmt: str = "text"
    path: Path | None = None


@dataclass(frozen=True)
class CompareRequest:
    """A fully-specified comparison request — the single input to ``run_compare``.

    Every front-end (CLI, MCP, ``compare-release`` fan-out, ``appcompat``)
    assembles one of these and hands it to :func:`abicheck.service.run_compare`,
    so there is exactly one classification path and one set of defaults.
    """

    old: InputSpec
    new: InputSpec
    lang: str = "c++"
    frontend: str = "auto"
    has_sources: bool = False
    policy: str = "strict_abi"
    policy_file_path: Path | None = None
    suppress: Path | None = None
    scope_public: bool = True
    force_public_symbols: frozenset[str] | None = None
    # `compare --post-manifest`: the committed `pp_*`/ufunc-loop surface of a POST
    # manifest. When set, the comparison is scoped to this set — export findings
    # outside it (e.g. private `__pp_*` kernel churn) are demoted. None = not
    # manifest-scoped.
    public_surface_allowlist: frozenset[str] | None = None
    pattern_verdicts: bool = False
    enable_debuginfod: bool = False
    # Override debuginfod server URL (only meaningful with enable_debuginfod);
    # None uses the resolver's default server list / DEBUGINFOD_URLS env var.
    debuginfod_url: str | None = None
    # ADR-039: clear context-free header-parse false positives using the build's
    # active preprocessor defines (a conditional field's phantom add/remove/size
    # delta the build proves never changed). Opt-in; a no-op unless the snapshots
    # carry ``build_context_defines`` + per-field ``guard`` annotations.
    reconcile_build_context: bool = False
    # ADR-020b: declared deployment constraints (EnvironmentMatrix YAML). When
    # its ``runtime_floors`` are set, new symbol-version requirements classify
    # against the declared floors (≤ floor → COMPATIBLE, > floor → BREAKING)
    # instead of the default deployment-RISK verdict.
    env_matrix_path: Path | None = None
    # ADR-050 D2: force a tentative diff through a genuine comparability-
    # contract mismatch (scope/profile fingerprint drift) instead of the
    # default hard ``ProfileMismatchError``/``ScopeMismatchError``. Opt-in;
    # the resulting ``DiffResult.assurance`` is stamped ``"none"`` so a
    # caller can still see a result but knows not to trust it. Forwarded
    # to ``checker.compare``'s own ``diagnostic_comparison`` parameter.
    diagnostic_comparison: bool = False
    # ADR-049 Phase 3: stamp each finding's shadow, non-authoritative
    # contract-relevance decision (``Change.contract_relevance``/
    # ``contract_reason_code``/``contract_assurance``). Opt-in; changes no
    # verdict, severity, or exit code. Forwarded to ``checker.compare``'s
    # own ``contract_evaluation`` parameter -- previously reachable only by
    # calling the Tier-1 core directly, which no front-end may do
    # (``cli-contract`` AI-readiness gate, ADR-037 D10.1), so this field is
    # what makes the shadow evaluator reachable through the real Tier-2
    # chokepoint at all (Codex review, fresh evidence).
    contract_evaluation: bool = False
    # ADR-049 Phase 6: which evidence domain `contract_evaluation` judges
    # against -- "public" (header-derived declared surface), "exports" (the
    # binary's own export table plus the raw type closure from it), or "all"
    # (no root/closure evidence required). `None` keeps the legacy derivation
    # from `scope_public`; an explicit value outranks it per ADR-049 D7
    # (`explicit_cli` > `legacy_alias`). Selects the domain only -- like
    # `contract_evaluation` itself, non-authoritative for verdict/exit code.
    contract_mode: str | None = None
    # ADR-055 D1: the friendly evidence-depth dial (`--depth`, same vocabulary
    # as `dump`/`scan`: binary/headers/build/source). `None` (the default)
    # infers the collect mode from whether either side sets `sources`/
    # `build_info` instead of defaulting to "off" -- matching the CLI's own
    # `--depth`-omitted inference (P1 fix in `cli_compare_helpers.py`), not
    # a fixed default. `"binary"` also clears both sides' `headers` before
    # resolving (matching the CLI's identical `depth == "binary"` handling),
    # since a binary-only depth request that still carries headers would
    # otherwise silently keep running L2.
    depth: str | None = None
    # ADR-055 D1, second slice: the last four concepts `compare`'s own
    # resolution (`cli_resolve._resolve_compare_snapshots`) could express and
    # this request could not, so a Python/MCP caller had to drop to loose
    # kwargs on `resolve_input` to reach them. All both-sides, mirroring the
    # CLI flags they come from, which are single-valued too.
    #
    # `--dwarf-only` / `--debug-format`: restrict a side's debug-info parse to
    # DWARF, or pin which debug format is read, instead of auto-detecting.
    dwarf_only: bool = False
    # Validated against SUPPORTED_DEBUG_FORMATS and lowercased before use, so a
    # typo fails through this module's ValidationError contract rather than a
    # raw ValueError deep in extraction, and "DWARF" works here exactly as it
    # does for the CLI's case-insensitive choice.
    debug_format: str | None = None
    # ADR-050 D1's resolved `path -> label` map for a labeled include set.
    # A tuple of pairs rather than a `dict` so the request stays hashable, the
    # property `InputSpec`'s own docstring calls out; `run_compare_request`
    # converts it back for `resolve_input`.
    include_labels: tuple[tuple[Path, str], ...] = ()
    # `--follow-deps` / `--search-path` / `--ld-library-path`: after both
    # sides resolve, populate each ELF side's transitive `DependencyInfo`.
    # Off by default, matching the CLI flag: it costs a full dependency-graph
    # resolution per side, so it stays opt-in rather than becoming a silent
    # cost for every typed caller.
    follow_dependencies: bool = False
    dependency_search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""
    # ADR-055 D1 / ADR-050: request-level default for `CompileContext.
    # frontend_context` (`--frontend-context`, host|device), applied to a
    # side whose own `InputSpec.compile.frontend_context` reads as the class
    # default ("host") -- `CompileContext.frontend_context` has no "unset"
    # representation, so this can't distinguish a side that explicitly wants
    # "host" from one that just never touched the field; see
    # `service_compare_evidence._compile_context`'s own docstring for the
    # accepted limitation and how to work around it.
    frontend_context: str = "host"

    def validation_errors(self) -> list[str]:
        """Return a list of human-readable validation problems (empty == valid).

        Lives here (Tier 2) so the CLI and MCP front-ends surface *identical*
        error text for the same bad request (ADR-037 D9 / goal AC 8): value
        validation (language / AST frontend enums) and the cross-flag
        feasibility rules (an ``android`` frontend has no header-AST path, so it
        needs source inputs).
        """
        errors: list[str] = []
        if self.lang.lower() not in SUPPORTED_LANGS:
            allowed = ", ".join(sorted(SUPPORTED_LANGS))
            errors.append(f"unsupported language {self.lang!r}: choose from {allowed}")
        frontend = self.frontend.lower()
        if frontend not in SUPPORTED_FRONTENDS:
            allowed = ", ".join(sorted(SUPPORTED_FRONTENDS))
            errors.append(
                f"unsupported AST frontend {self.frontend!r}: choose from {allowed}"
            )
        elif frontend == "android" and not (
            self.has_sources
            or self.old.sources
            or self.new.sources
            or self.old.build_info
            or self.new.build_info
        ):
            # D8/D9: 'android' reuses a pre-captured header-abi dump; it has no
            # header-AST path, so a header-only run can't use it. ADR-055 D1
            # (Codex review): either side's own `InputSpec.sources` also
            # satisfies this -- not just the legacy `has_sources` flag, which
            # a typed caller using the new field alone would otherwise have to
            # redundantly set too. A second review round: `InputSpec.
            # build_info` alone must count too -- `embed_build_source`
            # auto-detects a pack directory in *either* `sources` or
            # `build_info` (`cli_buildsource.py`'s own `bi_is_pack`/
            # `src_is_pack`), so a prebuilt evidence pack passed via
            # `build_info` is exactly the same "already have a pre-captured
            # header-abi dump" case this rule exists to allow.
            errors.append(
                "the 'android' AST frontend is source-ABI only (it has no "
                "header-AST path); supply source inputs (--sources) to use it"
            )
        # ADR-055 D1 (Codex review, two rounds): whether `InputSpec.sources`
        # is compatible with `frontend == "android"` depends on whether it's
        # a genuine raw source tree (run_compare_request's inline
        # embed_build_source has no real Android extractor -- rejected) or a
        # prebuilt evidence pack (loaded as pre-captured facts, no extractor
        # ever runs -- valid). That distinction needs filesystem access plus
        # helpers from the CLI/service import-cycle-allowlisted cluster this
        # leaf module deliberately stays out of, so it's checked at runtime
        # in service.run_compare_request instead of here.
        if (
            self.debug_format is not None
            and self.debug_format.lower() not in SUPPORTED_DEBUG_FORMATS
        ):
            allowed = ", ".join(sorted(SUPPORTED_DEBUG_FORMATS))
            errors.append(
                f"unsupported debug format {self.debug_format!r}: choose from {allowed}"
            )
        if not self.policy:
            errors.append("policy profile name must not be empty")
        # D9 pre-flight: a --policy-file path that doesn't exist is a hard error
        # here (Tier 2), so CLI and MCP surface the same message before any work.
        if (
            self.policy_file_path is not None
            and not Path(self.policy_file_path).exists()
        ):
            errors.append(f"policy file not found: {self.policy_file_path}")
        if self.env_matrix_path is not None and not Path(self.env_matrix_path).exists():
            errors.append(f"environment matrix file not found: {self.env_matrix_path}")
        # ADR-049 Phase 6 (Codex review): the same two rules the CLI applies
        # to --contract, so a typed caller fails fast and with identical text
        # instead of having the mode silently ignored (contract_evaluation
        # off) or raising a raw ValueError deep in the pipeline after input
        # resolution (bad value).
        if self.contract_mode is not None:
            from .contract_relevance_types import ContractMode

            allowed_modes = {mode.value for mode in ContractMode}
            if self.contract_mode not in allowed_modes:
                errors.append(
                    f"unsupported contract mode {self.contract_mode!r}: "
                    f"choose from {', '.join(sorted(allowed_modes))}"
                )
            if not self.contract_evaluation:
                errors.append(
                    "contract_mode requires contract_evaluation: it selects "
                    "which evidence domain the shadow contract evaluator "
                    "judges against, and without that flag no contract "
                    "decision is computed at all"
                )
        if self.depth is not None:
            from .buildsource.scan_levels import USER_DEPTHS

            if self.depth.lower() not in USER_DEPTHS:
                allowed = ", ".join(sorted(USER_DEPTHS))
                errors.append(
                    f"unsupported depth {self.depth!r}: choose from {allowed}"
                )
        # ADR-055 D1 (Codex review): validated case-insensitively like the
        # other enums above -- an unvalidated value (e.g. "DEVICE") would
        # pass here but then compare unequal to the lowercase "host"/"device"
        # literals every actual consumer checks against, silently behaving as
        # neither.
        if self.frontend_context.lower() not in ("host", "device"):
            errors.append(
                f"unsupported frontend context {self.frontend_context!r}: "
                "choose from device, host"
            )
        # Codex: a per-side InputSpec.compile.frontend_context bypassed this enum check entirely -- validate it here too, same message shape.
        for label, side in (("old", self.old), ("new", self.new)):
            if side.compile is not None and side.compile.frontend_context.lower() not in ("host", "device"):
                errors.append(f"unsupported {label} frontend context {side.compile.frontend_context!r}: choose from device, host")
        # CodeRabbit review: dump_manifest replaces headers for the primary AST; forwarding both mixes two declared surfaces into one snapshot's provenance/dialect detection. Belongs in this Tier-2 pre-flight validate() (mirrors the CLI's --dump-manifest/-H UsageError), not only checked at runtime in run_compare_request -- moved here so a caller using validation_errors()/validate() alone also catches it.
        for label, side in (("old", self.old), ("new", self.new)):
            if side.dump_manifest is not None and side.headers:
                errors.append(f"dump_manifest and a header for the {label} side (InputSpec.headers) are mutually exclusive -- declare the {label} side's public surface in the manifest's own base profile instead.")
        return errors

    def validate(self) -> CompareRequest:
        """Validate fail-fast; raise :class:`ValidationError` on the first batch.

        Returns ``self`` so callers can write ``request.validate()`` inline.
        """
        errors = self.validation_errors()
        if errors:
            raise ValidationError("; ".join(errors))
        return self

    def replace(self, **changes: Any) -> CompareRequest:
        """Return a copy with *changes* applied (frozen-dataclass ``replace``)."""
        return replace(self, **changes)


@dataclass(frozen=True)
class CompareResult:
    """What one :class:`CompareRequest` produced — the typed result (ADR-055 D2).

    :func:`abicheck.service.run_compare_request` returns a bare
    ``tuple[DiffResult, AbiSnapshot, AbiSnapshot]``, so every new thing a
    comparison resolves has nowhere to land except a fourth tuple slot — a
    break for every positional caller. This wraps that same tuple (``diff``/
    ``old_snapshot``/``new_snapshot`` are exactly its three elements, in
    order) so a future field (a resolved-depth record, ADR-049's evaluation
    receipt, a coverage summary) is an additive attribute instead. The same
    reasoning ADR-035 applied to ``ScanRequest``/``ScanResult``, generalized
    to ``compare``.

    ``suppression`` is the one field beyond that rename, and it is not
    speculative: it is what ADR-055 D4 needed to exist. ``run_compare_request``
    resolves the suppression list internally from
    ``CompareRequest.suppress``, but a front end applying a *post*-
    classification concern still needs the resolved object — ``appcompat``'s
    ``scope_diff_to_app(..., suppression=...)`` is the concrete case. Without
    it here, the MCP server would have had to keep its own
    ``SuppressionList.load`` call purely to re-derive a value the service had
    already loaded, which is precisely the duplication D4 removes. The
    resolved policy file needs no equivalent field — ``DiffResult.policy_file``
    already carries it.

    Placed here rather than in ``service.py`` (where ADR-055's own file sketch
    put it) so the request and its result live in one leaf module: this one is
    already "typed request/response structs", imports nothing at runtime from
    the service layer, and keeps a caller able to type-annotate a result
    without importing ``service``'s much heavier graph.
    """

    diff: DiffResult
    old_snapshot: AbiSnapshot
    new_snapshot: AbiSnapshot
    suppression: SuppressionList | None = None

    def as_tuple(self) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
        """Return the legacy ``run_compare_request`` 3-tuple shape.

        The tuple-returning entry points are implemented in terms of this, so
        the two shapes cannot drift apart into two orderings.
        """
        return self.diff, self.old_snapshot, self.new_snapshot


__all__ = [
    "HEADER_AST_FRONTENDS",
    "SUPPORTED_DEBUG_FORMATS",
    "SUPPORTED_FRONTENDS",
    "SUPPORTED_LANGS",
    "CompareRequest",
    "CompareResult",
    "InputSpec",
    "OutputSpec",
]

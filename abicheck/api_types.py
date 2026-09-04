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
gap against the CLI's own, then separately-maintained
``cli_resolve._resolve_compare_snapshots`` — ``dwarf_only``,
``debug_format``, ``include_labels``, and ``--follow-deps``. A third slice
then removed that second implementation outright: ``run_compare_request``
was split into its two phases (``service_compare_pipeline``), which gave the
CLI's Click-dependent ADR-049 ``resolve_and_apply`` step a seam to run in —
the thing that had made a shared resolution look impossible — and
``_resolve_compare_snapshots`` now builds one of these requests and
delegates. So this really is the one resolution every front end uses.
ADR-055's "Two-resolution-path finding", first answered as option (b), is
recorded as settled the other way in D1's "Structural half" note.

ADR-055 D2 adds :class:`CompareResult`, the result side of the same pair, and
D4 adds ``InputSpec.follow_linker_scripts`` — see each one's own docstring.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .change_registry_types import Verdict
from .checker_policy import VALID_BASE_POLICIES, ChangeKind
from .checker_types import DiffResult
from .errors import ValidationError
from .model import AbiSnapshot
from .policy.exit_decision import ExitDecision
from .policy.severity import SeverityConfig
from .suppression import SuppressionList

if TYPE_CHECKING:
    # ``CompileContext``/``DumpManifest`` are used only by ``InputSpec``,
    # never referenced by ``CompareResult`` -- unlike the five imports above,
    # nothing here needs them resolvable at runtime by ``typing.get_type_
    # hints()`` (CodeRabbit review, fresh evidence, PR #1032: that call
    # raised ``NameError`` for ``CompareResult`` because its own fields --
    # ``diff: DiffResult``, ``old_snapshot``/``new_snapshot: AbiSnapshot``,
    # ``suppression: SuppressionList | None``, ``exit_decision:
    # ExitDecision | None``, ``severity_config: SeverityConfig | None`` --
    # directly reference six of what were seven ``TYPE_CHECKING``-only
    # names; importing only ``DiffResult``, as first suggested, would have
    # just moved the same failure to the next unresolved name. No import
    # cycle: none of ``checker_types``/``model``/``suppression``/
    # ``policy.exit_decision``/``policy.severity`` imports this module,
    # directly or transitively -- the same check
    # ``TestCompareRequestRuntimeResolvableAnnotations`` already documents
    # for ``CompareRequest``'s own two runtime imports below).
    from .compile_context import CompileContext
    from .dump_manifest import DumpManifest

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

    # `None` means "no native artifact on this side" -- the source-only dump
    # shape (`abicheck dump --sources ./tree` with no SO_PATH), which
    # `DumpRequest` accepts and `CompareRequest` does not (a comparison always
    # has two artifacts/snapshots to compare). Widened from a required `Path`
    # by CLI cleanup phase two's PR 3A blocker 5: `dump_cmd` cannot build one
    # `DumpRequest` covering *both* of its branches while this field cannot
    # express the branch it dispatches on. A pure widening -- `Path | None`
    # accepts everything `Path` did -- so no existing caller changes; which
    # requests may leave it `None` is enforced per request type, in
    # `_path_required_errors` (called from both `validation_errors`), not at
    # each of the ~7 call sites that dereference it.
    path: Path | None
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    version: str = ""
    pdb: Path | None = None
    debug_roots: tuple[Path, ...] = ()
    # Mirrors `dump --include-system-declarations`/`compare --include-system-declarations`
    # (dumper_scoping.py): default True preserves the historical unfiltered
    # behavior for any caller that doesn't opt in; `run_compare` (and the
    # CLI's `--include-system-declarations` default False) sets it explicitly.
    include_dependencies: bool = True
    # ADR-055 D1: this side's inline build/source evidence (mirrors
    # `--sources`/`--build-info`, side-scoped like the CLI's own
    # `old=`/`new=` sided values) -- `run_compare_request` embeds them via
    # `cli_buildsource.embed_build_source` when set, at `CompareRequest.depth`'s
    # resolved collect mode. `None` on both sides is a no-op (unchanged
    # behavior from before this field existed).
    sources: Path | None = None
    build_info: Path | None = None
    # P0.2: explicit build-system root target(s) to scope this side's L3
    # evidence collection to, instead of a workspace-wide query -- mirrors
    # `dump --build-target`/`.abicheck.yml`'s `build.targets`. Bazel only so
    # far. Empty (the default) reproduces the historical unscoped behavior.
    build_targets: tuple[str, ...] = ()
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
    # (`split_public_header_inputs`) -- mirrors `scan --public-header-dir`.
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
    # Mirrors `dump --compile-db-filter` (PR 3A, dump/scan resolver
    # convergence -- see the plan doc's PR C status notes and the root
    # AGENTS.md "PR C" known-gap entry for the two review rounds that shaped
    # this). `None` (the default) is the pre-existing, unfiltered behavior
    # for every caller -- `compare`'s implicit-dump path, `dump`'s typed
    # pipeline via any caller that doesn't set this. `resolve_header_
    # compile_context`/`l2_seed.seed_includes_and_fold_compile_context`
    # already accept a `source_filter` and narrow the P0.3 L3->L2 fold by
    # it (landed alongside the ELF `dump` CLI's own `--compile-db-filter`
    # threading); `service_dump_pipeline.resolve_dump_request` mirrors the
    # CLI's own `compile_db_filter_scope_error` refusal (a filter combined
    # with a resolved collect mode that also embeds L3 evidence is a usage
    # error, not a silent unfiltered L3 collection) using the resolved
    # collect mode it alone knows; `service_input_resolution` forwards this
    # field into both `_seeded_includes_and_compile_context` (the fold) and
    # `attach_build_context_for_parsed_headers` (the ADR-039 collector), so
    # the header parse and the collector agree on which translation units
    # the filter selects, exactly as the three CLI-side layers already do
    # via `build_context.source_matches_filter`.
    compile_db_filter: str | None = None

    @classmethod
    def of(
        cls,
        path: Path | str | None = None,
        *,
        headers: Iterable[Path | str] | None = None,
        includes: Iterable[Path | str] | None = None,
        version: str = "",
        pdb: Path | str | None = None,
        debug_roots: Iterable[Path | str] | None = None,
        include_dependencies: bool = True,
        sources: Path | str | None = None,
        build_info: Path | str | None = None,
        build_targets: Iterable[str] | None = None,
        dump_manifest: DumpManifest | None = None,
        compile: CompileContext | None = None,
        public_header_dirs: Iterable[Path | str] | None = None,
        follow_linker_scripts: bool = True,
        compile_db_filter: str | None = None,
    ) -> InputSpec:
        """Build an :class:`InputSpec`, coercing loose front-end values."""
        return cls(
            path=Path(path) if path is not None else None,
            headers=_path_tuple(headers),
            includes=_path_tuple(includes),
            version=version,
            pdb=Path(pdb) if pdb is not None else None,
            debug_roots=_path_tuple(debug_roots),
            include_dependencies=include_dependencies,
            sources=Path(sources) if sources is not None else None,
            build_info=Path(build_info) if build_info is not None else None,
            build_targets=tuple(build_targets) if build_targets is not None else (),
            dump_manifest=dump_manifest,
            compile=compile,
            public_header_dirs=_path_tuple(public_header_dirs),
            follow_linker_scripts=follow_linker_scripts,
            compile_db_filter=compile_db_filter,
        )


def _lang_errors(lang: str) -> list[str]:
    """``lang`` must name one of the C/C++ frontends' language modes."""
    if lang.lower() in SUPPORTED_LANGS:
        return []
    allowed = ", ".join(sorted(SUPPORTED_LANGS))
    return [f"unsupported language {lang!r}: choose from {allowed}"]


def frontend_value_errors(frontend: str) -> list[str]:
    """``frontend`` must name a known AST frontend (value check only).

    The cross-flag ``android``-needs-source-inputs rule is left to each request
    type: what counts as "has source inputs" differs between a two-sided
    comparison and a single dump.

    Public (no leading underscore): unlike this module's other per-field
    validators, ``mcp_server_inputs._compile_context_from_args`` imports this
    one across the module boundary, so its own MCP-argument validation
    matches ``DumpRequest.validate()``'s wording exactly instead of
    restating the rule (a fresh review's own suggestion — a shared-vocabulary
    function reads clearer as a declared public name than a private one used
    from outside its module).
    """
    if frontend.lower() in SUPPORTED_FRONTENDS:
        return []
    allowed = ", ".join(sorted(SUPPORTED_FRONTENDS))
    return [f"unsupported AST frontend {frontend!r}: choose from {allowed}"]


_ANDROID_NEEDS_SOURCES = (
    "the 'android' AST frontend is source-ABI only (it has no "
    "header-AST path); supply source inputs (--sources) to use it"
)


def _debug_format_errors(debug_format: str | None) -> list[str]:
    if debug_format is None or debug_format.lower() in SUPPORTED_DEBUG_FORMATS:
        return []
    allowed = ", ".join(sorted(SUPPORTED_DEBUG_FORMATS))
    return [f"unsupported debug format {debug_format!r}: choose from {allowed}"]


def _depth_errors(depth: str | None) -> list[str]:
    if depth is None:
        return []
    from .buildsource.scan_levels import USER_DEPTHS

    if depth.lower() in USER_DEPTHS:
        return []
    allowed = ", ".join(sorted(USER_DEPTHS))
    return [f"unsupported depth {depth!r}: choose from {allowed}"]


def _resolved_collect_mode_errors(resolved_collect_mode: str | None) -> list[str]:
    """Validate :attr:`DumpRequest.resolved_collect_mode` against the real
    ADR-033 CI-mode vocabulary (Codex review).

    Left unchecked, an unrecognized value (a typo, wrong casing, or an empty
    string) would silently reach ``buildsource.source_replay.
    collection_for_ci_mode``, whose own `.get(mode, ())` fallback treats *any*
    unknown spelling as ``"off"`` -- omitting every requested build/source
    evidence layer with no error at all, exactly the "reports invalid input
    as if it were a deliberate no-op" failure mode this repo's own validation
    convention (`_depth_errors`/`_debug_format_errors` above) exists to
    prevent. Case-sensitive, deliberately unlike `_depth_errors`/
    `_debug_format_errors`: this field is never user-typed on a command line
    (it only ever carries a value another resolver already computed
    verbatim, e.g. `service_compare_evidence.collect_mode_for`'s own return
    value), so silently lowercasing it would paper over a real bug in the
    caller rather than surface it.
    """
    if resolved_collect_mode is None:
        return []
    from .buildsource.source_replay import CI_MODE_TO_SCOPE

    if resolved_collect_mode in CI_MODE_TO_SCOPE:
        return []
    allowed = ", ".join(sorted(CI_MODE_TO_SCOPE))
    return [
        f"unsupported resolved_collect_mode {resolved_collect_mode!r}: "
        f"choose from {allowed}"
    ]


#: The two ``--frontend-context`` values (ADR-050 D3/D5). One tuple, so the
#: request-level check and the per-side one below cannot drift -- and so a
#: third caller inherits both the vocabulary and the message wording rather
#: than restating them (CodeRabbit).
FRONTEND_CONTEXTS = ("host", "device")


def _frontend_context_message(value: str, label: str = "") -> str:
    """The one wording for an out-of-vocabulary frontend context.

    *label* names the side when the value came from an ``InputSpec.compile``
    rather than the request itself.
    """
    scope = f"{label} " if label else ""
    allowed = ", ".join(sorted(FRONTEND_CONTEXTS))
    return f"unsupported {scope}frontend context {value!r}: choose from {allowed}"


def frontend_context_errors(frontend_context: str) -> list[str]:
    """``frontend_context`` must be one of :data:`FRONTEND_CONTEXTS`.

    Public for the same reason as :func:`frontend_value_errors`:
    ``mcp_server_inputs._compile_context_from_args`` imports it across the
    module boundary.
    """
    # Validated case-insensitively like the other enums -- an unvalidated value
    # (e.g. "DEVICE") would pass but then compare unequal to the lowercase
    # "host"/"device" literals every actual consumer checks against, silently
    # behaving as neither.
    if frontend_context.lower() in FRONTEND_CONTEXTS:
        return []
    return [_frontend_context_message(frontend_context)]


def required_path(side: InputSpec, label: str) -> Path:
    """*side*'s ``path``, narrowed — the accessor for a code path that needs one.

    ``InputSpec.path`` is ``Path | None`` (PR 3A blocker 5, so a source-only
    ``dump`` is expressible), but most consumers run only after a
    ``validate()`` that already rejected ``None`` for their request type. This
    is the one place that narrowing is spelled, so a genuinely-unreachable
    ``None`` surfaces as this module's own ``ValidationError`` rather than an
    ``AttributeError`` from deep inside extraction.
    """
    if side.path is None:
        raise ValidationError(
            f"the {label} side needs a path (a binary or a snapshot file)"
        )
    return side.path


def _path_required_errors(
    label: str, side: InputSpec, *, source_only_allowed: bool
) -> list[str]:
    """``InputSpec.path`` is optional in the type, but not in every request.

    CLI cleanup phase two, PR 3A blocker 5. ``path`` was widened to
    ``Path | None`` so a source-only ``dump`` (``--sources ./tree`` with no
    SO_PATH) can be expressed as a real :class:`DumpRequest`; that shape is
    meaningless for a two-sided :class:`CompareRequest`, which always has two
    artifacts/snapshots to compare. Rather than let every consumer defend
    itself, the rule is stated once here and applied from both request types'
    ``validation_errors()`` — so a ``None`` path that is *not* a legitimate
    source-only dump fails as a usage error up front, before anything
    dereferences it.

    *source_only_allowed* is the per-request-type half: ``True`` for
    :class:`DumpRequest` (which still requires *some* declared evidence to
    make a binary-less snapshot out of — mirroring ``cli_buildsource.
    dump_source_only``'s own "a bare dump errors clearly here"), ``False`` for
    :class:`CompareRequest`.

    ``dump_manifest`` counts as that evidence alongside ``sources``/
    ``build_info``: ``abicheck dump --dump-manifest m.yaml`` with no SO_PATH
    is a real, tested CLI shape (the manifest's own ``roots``/translation
    units declare the surface), and an earlier revision of this check that
    named only ``sources``/``build_info`` rejected it — caught by
    ``tests/test_cli_dump_manifest.py``'s dry-run cases, exactly the "the model
    can't say what the CLI accepts" gap this widening exists to close.
    """
    if side.path is not None:
        return []
    if not source_only_allowed:
        return [f"the {label} side needs a path (a binary or a snapshot file)"]
    if not (side.sources or side.build_info or side.dump_manifest is not None):
        return [
            f"the {label} side has no path and no sources/build_info/"
            "dump_manifest: a binary-less dump needs at least one of them to "
            "have anything to extract -- pass a binary (SO_PATH), or "
            "--sources/--build-info for a source-only snapshot"
        ]
    return []


def _source_only_binary_depth_errors(side: InputSpec, depth: str | None) -> list[str]:
    """Mirror ``dump_cmd``'s own source-only + ``--depth binary`` rejection.

    Codex review on #814: a source-only :class:`DumpRequest` (``path is
    None``, allowed by ``_path_required_errors`` above) has no binary at all,
    so ``--depth binary`` -- rank 0, the floor every other depth exceeds --
    would be trivially "satisfied" for a completely empty snapshot
    (``--depth binary`` resolves ``collect_mode`` to ``"off"``, skipping
    L3-L5 embedding too). The CLI (``cli.py``'s ``so_path is None and depth
    == "binary"`` check, right before its own ``--dry-run`` branch) already
    raises a ``UsageError`` for this shape; without this check the typed
    preflight silently approved an invocation the CLI treats as a hard
    error, and ``resolve_dump_request()`` would go on to build a request
    with nothing to report at all. Only fires for a genuinely path-less
    side -- a binary dump with ``depth="binary"`` is the ordinary, valid
    case this must not touch.

    Compared case-insensitively (Codex review, fresh evidence): ``depth`` is
    accepted case-insensitively everywhere else (``_depth_errors`` above,
    ``resolve_dump_request_evidence``'s own ``.lower()``), so a caller
    spelling ``depth="BINARY"`` previously slipped past this exact-string
    comparison even though it resolves to the identical, still-illegal
    source-only-binary shape once normalized.
    """
    if side.path is not None or (depth or "").lower() != "binary":
        return []
    return [
        "--depth binary requires a native artifact (SO_PATH); a "
        "source-only dump (--sources/--build-info with no SO_PATH) has "
        "no binary to report and needs at least --depth build or "
        "--depth source to produce any evidence."
    ]


def _side_errors(label: str, side: InputSpec) -> list[str]:
    """The per-:class:`InputSpec` rules both request types apply.

    * a per-side ``compile.frontend_context`` bypassed the request-level enum
      check entirely, so it is validated here too, same message shape;
    * ``dump_manifest`` replaces ``headers``/``includes``/``public_header_dirs``
      for the primary AST, so forwarding any of them alongside it mixes two
      declared surfaces into one snapshot's provenance/dialect detection
      (mirrors the CLI's ``--dump-manifest``/``-H``/``--public-header-dir``
      ``UsageError``, and ``dumper.dump()``'s own runtime check of the
      identical field set — ``extra_includes``/``public_header_dirs`` there
      are this dataclass's ``includes``/``public_header_dirs``). Checked in
      this Tier-2 pre-flight, not only at runtime, so a caller using
      ``validation_errors()``/``validate()`` alone also catches it — without
      this, a ``dump_manifest`` set alongside ``public_header_dirs`` or
      ``includes`` passed ``validate()`` and failed late, deep inside
      extraction, as a generic ``SnapshotError`` rather than a usage error
      (Codex review named ``public_header_dirs``; ``includes`` has the
      identical gap, confirmed against ``dumper.dump()``'s own check, which
      this pre-flight is front-running).
    """
    errors: list[str] = []
    # The per-side `compile.frontend` was unvalidated: the request-level
    # `frontend` is checked, but a typo in `InputSpec.compile` reached the
    # extraction layer and (once the source-ABI-only downgrade existed) was
    # rewritten to "auto", turning a typo into a successful default-backend
    # run instead of the `Unknown AST frontend` error it used to raise
    # (Codex review). Validated here so it fails like every other bad value.
    if (
        side.compile is not None
        and side.compile.frontend.lower() not in SUPPORTED_FRONTENDS
    ):
        allowed = ", ".join(sorted(SUPPORTED_FRONTENDS))
        errors.append(
            f"unsupported {label} AST frontend {side.compile.frontend!r}: "
            f"choose from {allowed}"
        )
    if (
        side.compile is not None
        and side.compile.frontend_context.lower() not in FRONTEND_CONTEXTS
    ):
        errors.append(
            _frontend_context_message(side.compile.frontend_context, label)
        )
    if side.dump_manifest is not None:
        # Same field set `dumper.dump()` itself rejects (its `extra_includes`/
        # `public_header_dirs` params are this dataclass's `includes`/
        # `public_header_dirs`) -- excludes `scope_header_dirs`, which no
        # typed-request field ever populates, so there is nothing live to
        # conflict there.
        _manifest_conflicts = {
            "headers": side.headers,
            "includes": side.includes,
            "public_header_dirs": side.public_header_dirs,
        }
        given = sorted(name for name, value in _manifest_conflicts.items() if value)
        if given:
            errors.append(
                f"dump_manifest and the {label} side's {', '.join(given)} "
                "are mutually exclusive -- declare the equivalent in the "
                "manifest's own base profile instead."
            )
    return errors


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
    # G31 Phase C follow-up: `lang` alone cannot say whether the caller
    # genuinely wants this language forced or is just leaving the field at
    # its dataclass default (the CLI has the identical problem with Click's
    # `--lang` default — see `cli.dump_cmd`'s `lang_explicit` and AGENTS.md's
    # "dump --lang c++ is silently discarded ..." known gap). `False` (the
    # default) preserves the pre-existing behavior exactly: `resolve_input`
    # auto-detects unless `lang == "c"`. Set `True` when `lang` reflects a
    # real, deliberate request (e.g. forwarded from a genuine CLI `--lang`)
    # so the header-AST pass honors it even on a language-ambiguous header.
    # `kw_only=True` (not a positional field, and appended at the true end
    # rather than inserted mid-list) so an existing positional caller of this
    # documented public request type — `CompareRequest(old, new, "c++",
    # "clang", ...)` — keeps binding every field to what it always did,
    # instead of silently shifting onto this new one (Codex review; the
    # identical PR #582 lesson AGENTS.md's `Change`-dataclass entry already
    # documents for exactly this mistake).
    lang_explicit: bool = field(default=False, kw_only=True)
    # CLI cleanup phase two, PR B slice 1: a caller that has already resolved
    # a ``--pack``'s ``policy.overrides``/``surface.internal_namespaces``
    # contributions (ADR-049 D8's ``pack_application.PackApplication``) can
    # hand them over here instead of dropping to Tier-1 directly. Before this
    # field existed, ``policy_file_path`` was the *only* channel this typed
    # request had for policy configuration, and it names a file on disk —
    # there was no way to say "apply these already-resolved overrides on top
    # of it" without either writing a synthetic policy file to disk or
    # calling ``checker.compare``/``compare_snapshots`` directly, which the
    # ``cli-contract`` AI-readiness gate forbids for any ``cli*.py`` module.
    # That gap is exactly why the directory/package release fan-out rejected
    # ``--pack`` outright (see ``cli_compare_options._reject_set_input_flags``)
    # while the single-pair ``compare`` CLI could already apply one: the
    # single-pair path never went through this typed request for that step,
    # calling ``compare_snapshots`` directly with an already pack-folded
    # ``PolicyFile`` object. Folded into the loaded ``PolicyFile`` by
    # ``service_compare_pipeline.classify_compare_pair`` via
    # ``pack_application.policy_file_with_packs`` — the exact same function
    # the single-pair CLI path already uses — so every caller that reaches
    # ``classify_compare_pair`` (which is every ``CompareRequest`` consumer,
    # ``run_compare_request``'s own two phases included) applies a pack's
    # policy/contract-surface contributions identically, rather than each
    # front end re-deriving its own application. ``None``/empty is a no-op:
    # every pre-existing caller that never sets this field is unaffected.
    # Deliberately narrower than the full ``PackApplication`` (no
    # ``exit_code_scheme``/``severity_levels``): those gate-shaped fields
    # need their own resolved gate-options wiring the release fan-out does
    # not have yet — see the plan's "PR B" section.
    # A tuple of pairs, not a `dict` -- the same reason `include_labels`
    # above is a tuple of pairs rather than a `dict`: this dataclass is
    # frozen and Python derives `__hash__` from its fields, which a `dict`
    # field would silently break the moment one was actually populated
    # (verified: `CompareRequest(...)` hashes fine with every pre-existing
    # field, and stops the moment this one carries a real `dict`).
    pack_policy_overrides: tuple[tuple[ChangeKind, Verdict], ...] | None = field(
        default=None, kw_only=True
    )
    #: ``surface.internal_namespaces`` when a pack supplied it — see
    #: ``pack_policy_overrides`` above for why this field exists and how it
    #: is applied. ``None`` means "no pack stated this"; distinct from an
    #: empty tuple, which is a pack's explicit "this project has none".
    pack_internal_namespaces: tuple[str, ...] | None = field(
        default=None, kw_only=True
    )
    #: The one automatic gate algorithm (ADR-064/CLI cleanup phase two PR
    #: G2) is fully determined by whether a severity setting is in effect
    #: -- there is no ``exit_code_scheme`` field here to force one direction
    #: or the other (removed in PR G2 along with the CLI flag, the
    #: ``.abicheck.yml`` key, and the pack field it used to mirror).
    severity_preset: str | None = field(default=None, kw_only=True)  # ADR-064/PR G2

    def validation_errors(self) -> list[str]:
        """Return a list of human-readable validation problems (empty == valid).

        Lives here (Tier 2) so the CLI and MCP front-ends surface *identical*
        error text for the same bad request (ADR-037 D9 / goal AC 8): value
        validation (language / AST frontend enums) and the cross-flag
        feasibility rules (an ``android`` frontend has no header-AST path, so it
        needs source inputs).
        """
        errors: list[str] = []
        errors += _lang_errors(self.lang)
        frontend = self.frontend.lower()
        frontend_errors = frontend_value_errors(self.frontend)
        errors += frontend_errors
        if not frontend_errors and frontend == "android" and not (
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
            errors.append(_ANDROID_NEEDS_SOURCES)
        # ADR-055 D1 (Codex review, two rounds): whether `InputSpec.sources`
        # is compatible with `frontend == "android"` depends on whether it's
        # a genuine raw source tree (run_compare_request's inline
        # embed_build_source has no real Android extractor -- rejected) or a
        # prebuilt evidence pack (loaded as pre-captured facts, no extractor
        # ever runs -- valid). That distinction needs filesystem access plus
        # helpers from the CLI/service import-cycle-allowlisted cluster this
        # leaf module deliberately stays out of, so it's checked at runtime
        # in service.run_compare_request instead of here.
        errors += _debug_format_errors(self.debug_format)
        if not self.policy:
            errors.append("policy profile name must not be empty")
        elif self.policy_file_path is None and self.policy not in VALID_BASE_POLICIES:
            # Only checked with no policy_file_path (Codex review, Round 11):
            # a file overrides the base name -- stated_policy_base's own
            # logic, applied here too -- so an unknown name paired with a
            # valid file is a legitimate request the file already resolves
            # for; only a name with nothing to override it is a real error,
            # and needs to fail here before any extraction runs.
            errors.append(
                f"unknown policy {self.policy!r}: choose from "
                f"{sorted(VALID_BASE_POLICIES)}"
            )
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
        errors += _depth_errors(self.depth)
        errors += frontend_context_errors(self.frontend_context)
        # Fail fast on a misspelled severity_preset (Codex review, fresh
        # evidence): `resolve_release_gate_options` already rejects an
        # unknown preset, but `classify_compare_pair` only calls it *after*
        # `resolve_compare_request` has already run extraction — a project-
        # controlled build/source step that can be slow or side-effecting.
        # Checking here means a bad value is a Tier-2 ValidationError before
        # any of that runs, for every front end that calls
        # `validate()`/`validation_errors()` (native `compare` CLI
        # included, via `cli_compare_receipt.py`). `SEVERITY_PRESETS` is
        # `resolve_severity_config`'s own lookup table, checked here without
        # calling it (this method must stay side-effect-free; resolving
        # would also require a real `SeverityConfig` this validation has no
        # use for).
        if self.severity_preset is not None:
            from .policy.severity import SEVERITY_PRESETS

            if self.severity_preset not in SEVERITY_PRESETS:
                errors.append(
                    f"invalid severity_preset {self.severity_preset!r}; "
                    f"must be one of {sorted(SEVERITY_PRESETS)} or None"
                )
        for label, side in (("old", self.old), ("new", self.new)):
            errors += _path_required_errors(label, side, source_only_allowed=False)
            errors += _side_errors(label, side)
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
        """Return a copy with *changes* applied (frozen-dataclass ``replace``).

        ``**changes: Any`` is deliberate, not an oversight (a fresh review's
        own question): a per-field ``TypedDict``/overload set would need to be
        kept in sync with every field this dataclass gains, which is exactly
        the maintenance burden this module's own docstring says a request
        dataclass exists to avoid ("a new feature becomes a new field with a
        default, never a signature break"). A typo'd kwarg still surfaces —
        as ``dataclasses.replace``'s own ``TypeError`` at the call site,
        rather than a mypy error.
        """
        return replace(self, **changes)


@dataclass(frozen=True)
class DumpRequest:
    """A fully-specified snapshot-extraction request — the input to ``run_dump_request``.

    ``compare``'s counterpart to this (:class:`CompareRequest`) has existed
    since ADR-037 D2; ``dump`` had no typed request at all, so the only way to
    ask for a snapshot through the service layer was
    :func:`abicheck.service.resolve_input`'s twenty-odd loose keyword arguments
    — and a front end that wanted ``--depth``/``--sources``/``--build-info``
    had to add the inline build-source embedding, the depth gate and the
    dependency walk itself. That is what kept the MCP ``abi_dump`` tool at a
    fixed five-argument subset of what ``abicheck dump`` accepts (G33 Phase 5).

    One side, so it reuses :class:`InputSpec` verbatim: everything about *what*
    to extract (path, headers, includes, ``sources``/``build_info``,
    ``dump_manifest``, per-input ``compile`` context, public-header dirs,
    dependency scoping) already lives there. The fields here are the ones
    :class:`CompareRequest` also keeps at request level because they describe
    *how* the extraction runs rather than which input it runs on.

    Deliberately **not** carried over from :class:`CompareRequest`: anything
    about classification (policy, suppression, scope, severity, contract
    evaluation). A dump produces evidence; it renders no verdict.
    """

    input: InputSpec
    lang: str = "c++"
    frontend: str = "auto"
    # Mirrors `CompareRequest.has_sources`: the legacy "this run has source
    # evidence" flag, which alone satisfies the `android` frontend's rule even
    # when the evidence is not `InputSpec.sources`/`build_info`.
    has_sources: bool = False
    # The friendly evidence-depth dial (`--depth`: binary/headers/build/source).
    # `None` infers the collect mode from whether the input sets `sources`/
    # `build_info`, exactly as `CompareRequest.depth` does. An explicit value is
    # a floor `run_dump_request` enforces: a depth that was requested but not
    # reached raises rather than silently returning a weaker snapshot (the same
    # contract `dump --depth` has via `check_requested_depth_satisfied`).
    depth: str | None = None
    dwarf_only: bool = False
    debug_format: str | None = None
    enable_debuginfod: bool = False
    debuginfod_url: str | None = None
    include_labels: tuple[tuple[Path, str], ...] = ()
    # `--follow-deps` / `--search-path` / `--ld-library-path`: populate the
    # snapshot's transitive `DependencyInfo`. Off by default, matching the CLI
    # flag and `CompareRequest.follow_dependencies` — it costs a full
    # dependency-graph resolution, so it stays opt-in.
    follow_dependencies: bool = False
    dependency_search_paths: tuple[Path, ...] = ()
    ld_library_path: str = ""
    # Request-level default for `CompileContext.frontend_context`
    # (`--frontend-context`, host|device), applied when the input's own
    # `InputSpec.compile.frontend_context` reads as the class default — see
    # `service_compare_evidence._compile_context` for the accepted limitation
    # that shared merge rule carries.
    frontend_context: str = "host"
    # See `CompareRequest.lang_explicit` — the identical default-vs-explicit
    # ambiguity and the same conservative default (`False`: auto-detect
    # unless `lang == "c"`, unchanged from before this field existed).
    # `kw_only=True`, appended at the true end (not inserted mid-list), for
    # the same positional-caller-safety reason as `CompareRequest`'s own
    # field (Codex review).
    lang_explicit: bool = field(default=False, kw_only=True)
    # An already-resolved collect mode, overriding what
    # `service_compare_evidence.dump_collect_mode_for` would otherwise derive
    # from `depth` alone (Codex review, PR 3A blocker 5). `compare`'s own
    # implicit-dump path (`cli_compare_helpers._embed_inline_source_side`)
    # resolves collect mode from the *pair* (`collect_mode_for`, a materially
    # different rule from `dump`'s own default — see that function's own
    # docstring) and forwards it into `dump_cmd`'s private
    # `_resolved_collect_mode` hook so the real run doesn't re-derive a
    # possibly-different mode from `depth` in isolation. Without this field,
    # a `DumpRequest` built for that invocation (e.g. by `--dry-run`) could
    # only record `depth`, and `resolve_dump_request()` would silently
    # recompute a different collect mode than the one the real run actually
    # uses — the exact "preview describes a run that never happened" hazard
    # this whole request object exists to foreclose. `None` (the default)
    # means "derive from `depth`", unchanged for every other caller.
    resolved_collect_mode: str | None = field(default=None, kw_only=True)

    def validation_errors(self) -> list[str]:
        """Return a list of human-readable validation problems (empty == valid).

        The same value and cross-flag rules :meth:`CompareRequest.validation_errors`
        applies, through the same module-level helpers, so ``dump`` and
        ``compare`` reject an identical mistake with identical text (ADR-037 D9
        front-end parity, extended across the two commands).
        """
        errors: list[str] = []
        errors += _lang_errors(self.lang)
        frontend_errors = frontend_value_errors(self.frontend)
        errors += frontend_errors
        if (
            not frontend_errors
            and self.frontend.lower() == "android"
            and not (self.has_sources or self.input.sources or self.input.build_info)
        ):
            errors.append(_ANDROID_NEEDS_SOURCES)
        errors += _debug_format_errors(self.debug_format)
        errors += _depth_errors(self.depth)
        errors += _resolved_collect_mode_errors(self.resolved_collect_mode)
        errors += frontend_context_errors(self.frontend_context)
        errors += _path_required_errors("input", self.input, source_only_allowed=True)
        errors += _source_only_binary_depth_errors(self.input, self.depth)
        errors += _side_errors("input", self.input)
        return errors

    def validate(self) -> DumpRequest:
        """Validate fail-fast; raise :class:`ValidationError` on the first batch.

        Returns ``self`` so callers can write ``request.validate()`` inline.
        """
        errors = self.validation_errors()
        if errors:
            raise ValidationError("; ".join(errors))
        return self

    def replace(self, **changes: Any) -> DumpRequest:
        """Return a copy with *changes* applied (frozen-dataclass ``replace``).

        See :meth:`CompareRequest.replace` for why ``**changes: Any`` is
        deliberate rather than an oversight.
        """
        return replace(self, **changes)


@dataclass(frozen=True)
class CompareResult:
    """What one :class:`CompareRequest` produced — the typed result (ADR-055 D2).

    Returned by :func:`abicheck.service.run_compare_request` and by the
    ``run_compare`` kwargs shim. Both returned a bare
    ``tuple[DiffResult, AbiSnapshot, AbiSnapshot]`` before 0.6, which left
    every new thing a comparison resolves nowhere to land but a fourth tuple
    slot — a break for every positional caller. As a struct, a future field
    (a resolved-depth record, ADR-049's evaluation receipt, a coverage
    summary) is an additive attribute instead. The same reasoning ADR-035
    applied to ``ScanRequest``/``ScanResult``, generalized to ``compare``.
    :meth:`as_tuple` reproduces the pre-0.6 shape in one line.

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
    # ADR-064/PR G2, see classify_compare_pair.
    exit_decision: ExitDecision | None = None
    # ADR-064/PR G2 rendering parity (Codex review): the same resolved
    # `GateOptions.severity` `exit_decision` above was scored under, so a
    # caller can pass `severity_config=result.severity_config` into
    # `reporter.render_output`/`to_json` and get a rendered `severity` block
    # and exit code that agree with `exit_decision`, instead of silently
    # recomputing a `None`-severity legacy exit that contradicts it. `None`
    # under the legacy scheme, where there is nothing to disagree with.
    severity_config: SeverityConfig | None = None

    def as_tuple(self) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
        """Return ``(diff, old_snapshot, new_snapshot)`` — the pre-0.6 shape.

        A one-line migration for a caller that unpacked the tuple
        ``run_compare``/``run_compare_request`` used to return::

            result, old, new = run_compare(...).as_tuple()

        Nothing in abicheck itself returns that shape any more; this exists
        only so a caller need not restructure to adopt the typed result.
        """
        return self.diff, self.old_snapshot, self.new_snapshot


__all__ = [
    "FRONTEND_CONTEXTS",
    "HEADER_AST_FRONTENDS",
    "SUPPORTED_DEBUG_FORMATS",
    "SUPPORTED_FRONTENDS",
    "SUPPORTED_LANGS",
    "CompareRequest",
    "CompareResult",
    "DumpRequest",
    "InputSpec",
    "OutputSpec",
    "frontend_context_errors",
    "frontend_value_errors",
    "required_path",
]

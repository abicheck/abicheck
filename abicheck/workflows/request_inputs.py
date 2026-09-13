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

"""``InputSpec`` -- the per-side typed operand of a comparison/dump request
(ADR-037 D2; ADR-061 gap B).

The real owner behind the flat ``abicheck.workflows.contracts`` compatibility
facade's input-side half: the supported-choice constants
(``SUPPORTED_LANGS``/``SUPPORTED_FRONTENDS``/``SUPPORTED_DEBUG_FORMATS``),
``InputSpec`` itself, and its validation helpers. ``CompareRequest``/
``DumpRequest``/``CompareResult`` -- the pair/result half that builds on
this one -- live in the sibling module :mod:`abicheck.workflows.contracts`,
split out purely to keep each module under the 800-line new-file ceiling.

:data:`~abicheck.model.header_ast_frontends.HEADER_AST_FRONTENDS` (the
subset of :data:`SUPPORTED_FRONTENDS` valid for header-AST parsing) moved to
`model` instead of staying here -- see that module's own docstring for the
second, independent `extract`-layer need that motivated the move; it is
re-exported here unchanged for every existing
``from abicheck.workflows.contracts import HEADER_AST_FRONTENDS`` caller.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ValidationError
from ..model.header_ast_frontends import HEADER_AST_FRONTENDS as HEADER_AST_FRONTENDS

if TYPE_CHECKING:
    from ..compile_context import CompileContext
    from ..dump_manifest import DumpManifest

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
    # Mirrors `dump --include-system-declarations`/`compare
    # --include-system-declarations` (dumper_scoping.py), **including its
    # default**: toolchain/system-header declarations are excluded unless a
    # caller opts in, exactly as the CLI flag's own `default=False` does.
    #
    # This default used to be `True`, to preserve the historical unfiltered
    # behavior for a caller that omits it. That made the two front ends
    # disagree about what "I didn't ask for anything" means, and the
    # disagreement was not cosmetic: dumping `liba.so` with one C++ header
    # yields 10 functions / `dependency_scope="filtered"` through the CLI and
    # 5,597 functions / `"full"` through `DumpRequest`, from identical inputs.
    # Since `comparability.check_contracts_comparable` refuses to compare a
    # `filtered` side against a `full` one, a user who dumped a baseline with
    # the CLI and a candidate with the typed API -- passing the flag on
    # neither, as the mismatch error itself advises -- got `scope_mismatch`
    # and no verdict. Aligning the default is what makes that advice true.
    include_dependencies: bool = False
    # ADR-055 D1: this side's inline build/source evidence (mirrors
    # `--sources`/`--build-info`, side-scoped like the CLI's own
    # `old=`/`new=` sided values) -- `run_compare_request` embeds them via
    # `cli_buildsource.embed_build_source` when set, at `CompareRequest.depth`'s
    # resolved collect mode. `None` on both sides is a no-op (unchanged
    # behavior from before this field existed).
    sources: Path | None = None
    build_info: Path | None = None
    # P0.2: explicit build-system root target(s) to scope this side's L3
    # evidence collection to, instead of a workspace-wide query -- a
    # programmatic-API-only knob since the CLI's own `dump --build-target`
    # flag was removed (`.abicheck.yml`'s `build.targets` is the only
    # front-end-driven source now). Bazel only so far. Empty (the default)
    # falls back to a discovered/explicit `.abicheck.yml`'s `build.targets`
    # (`workflows.plan._discovered_config_build_targets`,
    # `buildsource.embed.embed_build_source`'s own fallback), which also
    # reproduces the historical unscoped behavior when no config declares any.
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
    # CLI cleanup phase two, Block 7 (PR C's tail): this side's explicit
    # ``--config`` (mirrors ``ScanRequest.build_config``). `None` preserves
    # prior behavior. Lets `workflows.plan`'s pre-flight bazel-scoping check
    # see the same explicit config `embed_build_source` already honors at
    # real-execution time -- see `docs/contribute/known-gaps.md`'s "PR C".
    build_config: Path | None = None

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
        include_dependencies: bool = False,
        sources: Path | str | None = None,
        build_info: Path | str | None = None,
        build_targets: Iterable[str] | None = None,
        dump_manifest: DumpManifest | None = None,
        compile: CompileContext | None = None,
        public_header_dirs: Iterable[Path | str] | None = None,
        follow_linker_scripts: bool = True,
        compile_db_filter: str | None = None,
        build_config: Path | str | None = None,
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
            build_config=Path(build_config) if build_config is not None else None,
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
    from ..model.evidence_depth_levels import USER_DEPTHS

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
    from ..buildsource.source_replay import CI_MODE_TO_SCOPE

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

    ``headers`` counts too (workstream F S1, ``vision-api-abi-evolution.md``
    "F. Header-only comparison"): a bare ``dump -H api.h`` with no SO_PATH
    previously validated only because ``dump_source_only``'s CLI branch
    silently ignored ``-H`` and wrote an empty snapshot -- this widening is
    what makes the real, typed header-AST execution path
    (``workflows.artifact.execute_header_only.execute_header_only_dump_request``)
    reachable at all: without it, a genuinely headers-only request failed
    this check before that path ever ran.

    ``public_header_dirs`` deliberately does **not** count on its own
    (CodeRabbit review on this PR) -- it is a pure declaration-provenance
    classifier (public-vs-internal, consumed by ``cli_resolve.py`` and
    ``export_surface.py``'s scope classification), never a source of
    headers to parse: :func:`~abicheck.workflows.artifact.
    execute_header_only.is_header_only_evidence` only recognizes
    :attr:`ResolvedDumpRequest.headers`\\ /a real ``dump_manifest``, so a
    request naming only ``public_header_dirs`` reached this far and then
    fell through to the source-only path with nothing to embed, failing
    later with a confusing "needs sources and/or build_info" error instead
    of failing here with the right guidance.
    """
    if side.path is not None:
        return []
    if not source_only_allowed:
        return [f"the {label} side needs a path (a binary or a snapshot file)"]
    if not (
        side.sources
        or side.build_info
        or side.dump_manifest is not None
        or side.headers
    ):
        return [
            f"the {label} side has no path and no sources/build_info/"
            "dump_manifest/headers: a binary-less dump needs at least one of "
            "them to have anything to extract -- pass a binary (SO_PATH), "
            "-H/--header for a header-only snapshot, or --sources/"
            "--build-info for a source-only snapshot"
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
        errors.append(_frontend_context_message(side.compile.frontend_context, label))
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

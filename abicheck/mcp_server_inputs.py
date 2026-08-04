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

"""Turning caller-supplied MCP arguments into validated, contained values.

The layer between an agent's JSON object and a typed request: the public
depth ladder, the write-path policy, input-format detection, and the two
argument families G33 Phase 5 gave more than one tool (a public-header
directory set, and the ``compile_context_options`` family ``dump`` and
``scan`` share via one CLI decorator under ADR-037 D3). Split out of
``mcp_server.py`` when Phase 5's new parameters pushed that module past the
2000-line AI-readiness hard cap — the whole block moved verbatim, and
``mcp_server`` re-exports every name so ``abicheck.mcp_server._safe_write_path``
and friends keep resolving for existing callers and tests.

Constraining untrusted input is deliberately the *front end's* job, not the
shared service layer's (ADR-055 D4's own split), which is why these live
beside the tools rather than in ``service.py``. Kept a leaf so a sibling tool
module can use them without importing ``mcp_server`` back.
"""

from __future__ import annotations

import platform
from pathlib import Path

from . import mcp_shared
from .compile_context import CompileContext
from .mcp_shared import _safe_read_path
from .model import AbiSnapshot

#: Frontends that really drive header parsing, for the one MCP tool that has
#: nowhere else to put a source-ABI-only value. Mirrors
#: ``api_types.HEADER_AST_FRONTENDS`` -- imported lazily there to keep this a
#: leaf, restated here as a module constant so the check is readable.
HEADER_AST_FRONTENDS_MCP = frozenset({"auto", "castxml", "clang", "hybrid"})

__all__ = [
    "_ALLOWED_BINARY_SUFFIXES",
    "_ALLOWED_OUTPUT_SUFFIXES",
    "_PUBLIC_DEPTHS",
    "_compile_context_from_args",
    "_contract_mode_error",
    "_detect_binary_format",
    "_existing_path",
    "_public_header_dir_paths",
    "_resolve_input",
    "_safe_write_path",
    "_source_abi_only_frontend_error",
    "_validate_public_depth",
]


#: The public depth ladder (ADR-043 D2): exactly the four user-facing rungs.
#: ``full``/``symbols``/``graph`` are internal-only vocabulary and must not
#: leak into the MCP tool surface, matching the public CLI's ``--depth``.
_PUBLIC_DEPTHS = frozenset({"binary", "headers", "build", "source"})


def _validate_public_depth(depth: str | None) -> str | None:
    """Reject any depth spelling outside the public ladder, or ``None``.

    Note this only validates the *spelling*, not that the requested depth
    was actually *reached* — but that's not a gap on this surface (re-
    investigated for G30, AGENTS.md "Known gaps"/CLAUDE.md "M1-6", closed as
    stale). ``abi_scan`` builds a ``service.py`` ``ScanRequest`` and goes
    through ``service_scan.run_scan``, which calls the same
    ``scan_engine.run_scan_core`` the CLI ``scan`` command
    (``cli_scan.py``) calls directly — both already share one pinned-depth
    evidence contract (``_check_scan_evidence_contract``'s
    ``_EvidenceContractError``, ADR-037 D5), so there is no CLI-vs-MCP
    disparity to close for ``abi_scan``. ``abi_estimate`` also accepts
    ``depth=`` but is a separate, cost-only path: it builds its own
    ``ScanRequest`` and calls ``estimate_scan`` (never
    ``run_scan``/``run_scan_core``), so it never actually parses a binary or
    header for the estimate to be "unsatisfied" against — this validator's
    spelling check is all that applies to it.

    ``abi_dump`` is the one caller where the "spelling only" caveat no longer
    tells the whole story (G33 Phase 5). It now accepts ``depth``/``sources``/
    ``build_info`` and builds a ``DumpRequest``, so
    ``service_dump_pipeline.run_dump_request`` enforces the *reached* depth
    too — the same floor ``dump --depth`` gets from
    ``cli_dump_helpers.check_requested_depth_satisfied``, raising the Tier-2
    ``ValidationError`` in place of that path's ``DumpDepthNotSatisfiedError``.
    This validator still runs first, so a misspelled rung is rejected as a
    usage error before any extraction starts.
    """
    if depth is not None and depth not in _PUBLIC_DEPTHS:
        raise ValueError(
            f"Unknown depth: {depth!r}. Valid depths: {sorted(_PUBLIC_DEPTHS)}"
        )
    return depth


# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------
#
# ``_safe_read_path``/``_sanitize_error``/``mcp`` itself now live in the leaf
# module ``mcp_shared`` (imported above) so a sibling tool module
# (``mcp_server_project``) can depend on them without this module depending
# back on that sibling for shared state — see ``mcp_shared``'s own docstring.
# ``_safe_write_path`` stays here: no tool outside this module writes an
# output file.

# Allowed extensions for output files written by abi_dump
_ALLOWED_OUTPUT_SUFFIXES = frozenset({".json"})

# Allowed extensions for input binary files
_ALLOWED_BINARY_SUFFIXES = frozenset({".so", ".dll", ".dylib", ".json", ".dump", ""})


def _safe_write_path(raw: str, *, label: str = "output_path") -> Path:
    """Resolve and validate a path for writing.

    Enforces:
    - Must have an allowed suffix (.json only)
    - Must not be a system-sensitive location

    Raises ValueError on policy violation.
    """
    if not raw or raw.strip() == "":
        raise ValueError(f"Empty {label} is not allowed")

    try:
        p = Path(raw).resolve()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {exc!s}") from exc

    if p.suffix.lower() not in _ALLOWED_OUTPUT_SUFFIXES:
        raise ValueError(f"{label} must have a .json extension, got: {p.suffix!r}")

    # Block writes to sensitive system locations.
    # Use resolved Path objects to handle symlinks (/etc -> /private/etc on macOS)
    # and canonicalize traversal sequences (../../etc bypasses raw-string checks).
    _os = platform.system()
    if _os in ("Linux", "Darwin"):
        sensitive_system_dirs = [
            Path("/etc"),
            Path("/bin"),
            Path("/sbin"),
            Path("/usr/bin"),
            Path("/usr/sbin"),
            Path("/boot"),
            Path("/sys"),
            Path("/proc"),
            Path("/dev"),
        ]
        for sys_dir in sensitive_system_dirs:
            try:
                p.relative_to(sys_dir.resolve())
                raise ValueError(
                    f"{label} points to a sensitive system path: {sys_dir}..."
                )
            except ValueError as e:
                if "sensitive system path" in str(e):
                    raise
    elif _os == "Windows":
        p_str = str(p)
        # Normalize NT extended paths so checks also catch forms like:
        #   \\?\C:\Windows\...
        #   \\?\UNC\localhost\c$\Windows\...
        if p_str.startswith("\\\\?\\"):
            p_str = p_str[4:]
            if p_str.upper().startswith("UNC\\"):
                p_str = "\\\\" + p_str[4:]

        norm = p_str.replace("\\", "/").casefold()
        sensitive_prefixes = (
            "c:/windows/",
            "c:/windows/system32/",
            "c:/program files/",
            "c:/program files (x86)/",
            "c:/programdata/",
            "//localhost/c$/windows/",
            "//127.0.0.1/c$/windows/",
        )
        if norm.startswith(sensitive_prefixes):
            raise ValueError(f"{label} points to a sensitive system path")

    # Block writes to SSH/credential directories.
    # Resolve both sides to handle symlinks (e.g. ~/.ssh → /private/home/user/.ssh).
    home = Path.home().resolve()
    for sensitive_dir in [
        (home / ".ssh").resolve(),
        (home / ".aws").resolve(),
        (home / ".gnupg").resolve(),
    ]:
        try:
            p.relative_to(sensitive_dir)
            raise ValueError(f"{label} points to a sensitive credential directory")
        except ValueError as e:
            if "credential" in str(e):
                raise

    return p


# ---------------------------------------------------------------------------
# Helpers — reuse CLI logic without Click dependency
# ---------------------------------------------------------------------------


def _detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes — single file open."""
    from .binary_utils import detect_binary_format

    return detect_binary_format(path)


def _resolve_input(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Auto-detect input type and return an AbiSnapshot.

    Only ``abi_audit`` still calls this: ``abi_compare`` builds a
    ``CompareRequest`` (ADR-055 D4) and ``abi_dump`` a ``DumpRequest`` (G33
    Phase 5), both of which carry these same guards as request fields. An
    audit reads one snapshot and runs no comparison, so it has no typed
    request of its own to route through yet.

    Thin wrapper over :func:`abicheck.service.resolve_input` — the single source
    of truth for format detection, raw BTF/CTF blobs, and native (ELF/PE/Mach-O)
    dumping. The MCP surface is framework-free, so the service's
    ``SnapshotError`` / ``ValidationError`` (both ``AbicheckError`` subclasses)
    propagate unchanged for the tool handlers to convert into structured error
    payloads.

    ``follow_linker_scripts=False``: the MCP tools enforce ``MCP_MAX_FILE_SIZE``
    via :func:`_check_file_size` on the *caller-supplied* path before resolving.
    Following a GNU ld linker script would parse an ``INPUT()``/``GROUP()``
    target that never went through that guard, so a tiny script pointing at a
    huge library could defeat the resource limit. Disabling the follow keeps the
    size check authoritative (and matches the MCP server's pre-unification
    behaviour, which never followed linker scripts).

    ``public_headers`` / ``public_header_dirs``: the MCP tools document their
    plain ``headers`` parameter as
    "Public header files" — they have no separate opt-in provenance flag the way
    ``dump``'s CLI ``-H``/``--public-header`` split does. Callers making that same
    claim should pass the same paths here so declaration provenance is actually
    classified (ADR-024), matching the CLI ``compare --header`` fix.
    """
    from . import service

    return service.resolve_input(
        path,
        headers,
        includes,
        version,
        lang,
        follow_linker_scripts=False,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
    )


def _existing_path(raw: str, *, label: str) -> Path:
    """Resolve a caller-supplied evidence path, requiring it to exist.

    :func:`_safe_read_path` contains a path but does not check existence, and
    for the *evidence* inputs that is not enough: ``sources``/``build_info``
    infer a collect mode from being set at all, so a typo produced a run that
    collected nothing and still answered ``status: "ok"`` — a silently weaker
    baseline, where the CLI's ``click.Path(exists=True)`` rejects it outright
    (Codex review, P1). The depth floor does not catch this either: it only
    runs for an *explicit* depth, and the whole point of these arguments is
    that they infer one.

    The ``MCP_MAX_FILE_SIZE`` guard is applied here too, for whichever of these
    resolve to a *file*: ``build_info`` accepts a ``compile_commands.json`` (or
    a Bazel jsonproto), not only a directory, and the build-source loader
    parses it — so without this an oversized build-info artifact bypassed the
    limit every other file-shaped input is held to (Codex review, second
    round). Folding it in here rather than leaving it to each call site is what
    makes that impossible to forget again; a directory is skipped, since a
    size limit means nothing for one.

    Raises ValueError with the message the tool returns as its error payload.
    """
    path = _safe_read_path(raw, label=label)
    if not path.exists():
        raise ValueError(f"{label} not found: {path}")
    if path.is_file():
        # Module-qualified, per ``mcp_shared``'s own documented rule: these
        # names are runtime-mutable state (``main()`` overrides the limit from
        # ``--max-file-size``), so a reader must resolve them through the
        # module object rather than bind them at import time.
        mcp_shared._check_file_size(path, label=label)
    return path


def _public_header_dir_paths(raw_dirs: list[str] | None) -> list[Path]:
    """Validate a ``public_header_dirs`` argument into contained directories.

    Matches the CLI option (``exists=True, file_okay=False``): a public-header
    boundary must be a real directory, else ``apply_provenance`` would tag
    every declaration INTERNAL against a path that matches nothing, producing
    misleading origin classification (CodeRabbit). Shared by ``abi_dump`` and
    ``abi_scan`` so the two cannot drift on what they accept.

    Raises ValueError with the message the tool returns as its error payload.
    """
    paths: list[Path] = []
    for raw in raw_dirs or []:
        p = _safe_read_path(raw, label="public_header_dir")
        if not p.is_dir():
            raise ValueError(f"public_header_dir must be an existing directory: {raw}")
        paths.append(p)
    return paths


def _contract_mode_error(
    contract_mode: str | None, contract_evaluation: bool
) -> str | None:
    """The ``contract_mode`` usage error, or ``None`` when the pair is valid.

    The two rules the CLI's ``--contract`` applies, and the same two
    :meth:`CompareRequest.validation_errors` states — reproduced here so
    ``abi_scan`` (whose ``ScanRequest`` has no ``validate()``) rejects the same
    mistake with the same text ``abi_compare`` does.
    """
    if contract_mode is None:
        return None
    from .contract_relevance_types import ContractMode

    allowed = sorted(mode.value for mode in ContractMode)
    if contract_mode not in allowed:
        return (
            f"unsupported contract mode {contract_mode!r}: "
            f"choose from {', '.join(allowed)}"
        )
    if not contract_evaluation:
        return (
            "contract_mode requires contract_evaluation: it selects which "
            "evidence domain the shadow contract evaluator judges against, and "
            "without that flag no contract decision is computed at all"
        )
    return None


def _source_abi_only_frontend_error(ast_frontend: str) -> str | None:
    """Reject a source-ABI-only frontend on a tool that cannot carry it.

    ``android`` has no header-AST path, so :func:`_compile_context_from_args`
    downgrades it to ``"auto"`` for the *compile context*. ``abi_dump`` keeps
    the caller's value on ``DumpRequest.frontend``, where the source-ABI layer
    can act on it — but ``ScanRequest`` has no request-level frontend field, so
    the compile context is the only carrier and the value would be silently
    lost: the scan would accept a documented argument and then run an ordinary
    auto/castxml pass instead (Codex review).

    Accepting an argument and quietly doing something else is worse than
    refusing it, so ``abi_scan`` refuses. Giving ``ScanRequest`` a real
    request-level frontend, and threading it into the engine's L4 replay, is
    the feature this defers to — not something to fake with a downgrade.
    """
    if ast_frontend.lower() not in HEADER_AST_FRONTENDS_MCP:
        return (
            f"ast_frontend={ast_frontend!r} is source-ABI-replay only and has no "
            "header-AST path, and abi_scan has no request-level frontend to "
            "carry it into source replay -- so it would be silently ignored. "
            "Use 'auto', 'castxml', 'clang', or 'hybrid' here, or run abi_dump, "
            "which does carry it."
        )
    return None


def _compile_context_from_args(
    *,
    ast_frontend: str = "auto",
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: str | None = None,
    nostdinc: bool = False,
    frontend_context: str = "host",
) -> CompileContext | None:
    """Build the L2 compile context an MCP tool's flat arguments describe.

    Returns ``None`` when nothing was customised, so a caller that passes none
    of these reaches exactly the resolution it did before the arguments
    existed. ``sysroot`` goes through :func:`_safe_read_path` like every other
    caller-supplied path on this surface.

    G33 Phase 5: ``abi_dump`` and ``abi_scan`` expose the same
    ``compile_context_options`` family the ``dump`` and ``scan`` CLIs share via
    one decorator (ADR-037 D3), so they assemble it through one function here
    for the same reason.

    Both values are **validated and normalized here**, not left to the request
    type (Codex review, second round). ``abi_dump`` builds a ``DumpRequest``
    whose ``validate()`` would catch a typo, but ``abi_scan`` copies straight
    into a ``ScanRequest``, which has no ``validate()`` — so a misspelled
    frontend or an accepted-but-uppercased ``"DEVICE"`` survived into the
    spawned scan worker, to be ignored when no headers are parsed or to
    resurface as a generic sanitized worker failure. Validating in the one
    place both tools assemble the context gives them the CLI's usage error,
    with the *identical* text ``DumpRequest.validate()`` produces (the message
    helpers are imported from ``api_types`` rather than restated).

    A non-header-AST frontend (``android``, which is source-ABI only) is
    downgraded to ``"auto"`` for the *compile context* specifically: it is a
    legal ``--ast-frontend`` value, but ``dumper._resolve_header_backend``
    raises on anything outside ``castxml``/``clang``/``hybrid``/``auto``, and
    a ``ScanRequest`` has no separate request-level frontend field to carry it
    for L4. ``DumpRequest.frontend`` still receives the caller's value, so the
    dump path keeps it where the source-ABI layer can act on it.

    Raises ValueError with the message the tool returns as its error payload.
    """
    from .api_types import (
        HEADER_AST_FRONTENDS,
        _frontend_context_errors,
        _frontend_value_errors,
    )

    errors = _frontend_value_errors(ast_frontend) + _frontend_context_errors(
        frontend_context
    )
    if errors:
        raise ValueError("; ".join(errors))

    frontend = ast_frontend.lower()
    context = CompileContext(
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        sysroot=_safe_read_path(sysroot, label="sysroot") if sysroot else None,
        nostdinc=nostdinc,
        frontend=frontend if frontend in HEADER_AST_FRONTENDS else "auto",
        frontend_context=frontend_context.lower(),
    )
    return None if context.is_default else context

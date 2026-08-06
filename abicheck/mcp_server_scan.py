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

"""MCP tools for the ``scan`` CLI command: ``abi_estimate``/``abi_scan``.

Split out of :mod:`abicheck.mcp_server` when that module reached the
2000-line hard cap (CLAUDE.md "Files that are large — edit carefully"),
mirroring the ``mcp_server_project.py``/``mcp_server_inputs.py`` sibling-
module split already used for the same reason. Imported for side-effect
(and re-exported) at the bottom of :mod:`abicheck.mcp_server` so the
``@mcp.tool()`` decorators below run against the shared ``mcp`` instance.

Deliberately imports its stable helpers (``mcp``/``_safe_read_path``/
``_check_file_size``/``_audit_log``/``_sanitize_error``/``_logger``) from the
leaf module ``mcp_shared``, and its argument-translation helpers
(``_validate_public_depth``/``_public_header_dir_paths``/``_existing_path``/
``_compile_context_from_args``/``_contract_mode_error``/
``_source_abi_only_frontend_error``) from the leaf module
``mcp_server_inputs``, rather than from :mod:`abicheck.mcp_server` itself:
this module is imported *by* ``mcp_server`` (for tool registration), so
importing back from it here would recreate an import cycle the split was
meant to avoid (AGENTS.md "What NOT to do" — a new cycle needs a leaf
module, not an allowlist entry).
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path

from . import mcp_shared
from .checker_policy import VALID_BASE_POLICIES
from .compile_context import CompileContext
from .errors import ValidationError
from .mcp_server_inputs import (
    _compile_context_from_args,
    _contract_mode_error,
    _existing_path,
    _public_header_dir_paths,
    _source_abi_only_frontend_error,
    _validate_public_depth,
)
from .mcp_shared import (
    _audit_log,
    _check_file_size,
    _logger,
    _safe_read_path,
    _sanitize_error,
    mcp,
)


@mcp.tool()
def abi_estimate(
    binary_path: str,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    sources: str | None = None,
    compile_db: str | None = None,
    depth: str | None = None,
    changed_paths: list[str] | None = None,
) -> str:
    """Dry-run scan cost estimate for a project (ADR-035 D10 / ADR-043).

    Probes the project (TU count from the compile DB or source tree, public-header
    fan-out) and returns the projected per-layer cost of the chosen level WITHOUT
    running any compiler or parsing any binary — so a maintainer/agent can pick a
    depth/budget on measured cost. Scans nothing.

    Args:
        binary_path: Library/artifact the scan would target (existence checked).
        headers: Public header files (for the L2 header-AST fan-out estimate).
        include_dirs: Extra include directories.
        sources: Source tree (compile DB auto-discovered within it).
        compile_db: Explicit compile_commands.json (else discovered in sources).
        depth: Coarse evidence-depth selector: "binary", "headers", "build", or
            "source" (None = inferred from inputs, escalating with the
            changed-path risk score once seeded).
        changed_paths: Changed-path set for the focused (D7) replay-scope estimate.
    """
    t0 = _time.monotonic()
    try:
        from .service import Budget, ScanRequest, estimate_scan

        bin_path = _safe_read_path(binary_path, label="binary_path")
        if not bin_path.exists():
            return json.dumps({"status": "error", "error": "Binary file not found"})
        try:
            depth = _validate_public_depth(depth)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        hdr_paths = [_safe_read_path(h, label="header") for h in (headers or [])]
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]
        src_path = _safe_read_path(sources, label="sources") if sources else None
        cdb_path = (
            _safe_read_path(compile_db, label="compile_db") if compile_db else None
        )

        req = ScanRequest(
            binaries=[bin_path],
            headers=hdr_paths,
            includes=inc_paths,
            sources=src_path,
            compile_db=cdb_path,
            mode="pr",
            depth=depth,
            changed_paths=list(changed_paths or []),
            # Distinguish an *explicit* empty diff ([], a seeded no-op PR → s0
            # floor) from an omitted arg (None, unseeded → mode preset), matching
            # the CLI's seeded handling (Codex review).
            seeded=changed_paths is not None,
            budget=Budget(),
        )
        estimates = estimate_scan(req)
        total = sum(e.est_seconds for e in estimates)
        elapsed = _time.monotonic() - t0
        _audit_log("abi_estimate", {"binary": bin_path.name}, elapsed, "ok")
        return json.dumps(
            {
                "status": "ok",
                "estimate": [e.to_dict() for e in estimates],
                "total_est_seconds": round(total, 3),
            }
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_estimate", {"binary": Path(binary_path).name}, elapsed, "error")
        _logger.exception("abi_estimate failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_estimate")}
        )


@mcp.tool()
def abi_scan(
    binary_path: str,
    headers: list[str] | None = None,
    include_dirs: list[str] | None = None,
    public_header_dirs: list[str] | None = None,
    sources: str | None = None,
    compile_db: str | None = None,
    build_info: str | None = None,
    against: str | None = None,
    depth: str | None = None,
    changed_paths: list[str] | None = None,
    language: str = "c++",
    policy: str = "strict_abi",
    policy_file: str | None = None,
    suppression_file: str | None = None,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    ast_frontend: str = "auto",
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    sysroot: str | None = None,
    nostdinc: bool = False,
    frontend_context: str = "host",
) -> str:
    """Run a deterministic source-intelligence scan (ADR-035 D3/D10 / ADR-043).

    The typed engine behind the ``scan`` CLI: classify → always-on tier
    (compiler-free pattern pre-scan + intra-version cross-source checks) → the
    pinned evidence level (inferred from inputs, or pinned via ``depth``),
    POI-focused — and, when ``against`` is given, a ``compare`` against it.
    Returns one coverage-/confidence-annotated :class:`ScanResult`. Authority
    rule preserved: source/cross-source findings are RISK/API_BREAK only,
    never BREAKING on their own.

    Args:
        binary_path: Library/artifact (or JSON snapshot) to scan.
        headers: Public header files (provenance + pattern pre-scan).
        include_dirs: Extra include directories for the parser.
        public_header_dirs: Directories whose headers are public; establishes the
            public/internal boundary so the leakage / RTTI / exported-vs-public
            cross-checks run instead of skipping. A directory passed via ``headers``
            also counts; a lone umbrella header file cannot establish a boundary.
        sources: Source tree (compile DB auto-discovered within it).
        compile_db: Explicit compile_commands.json (else discovered in sources).
        build_info: Out-of-tree build dir / compile_commands.json / pack
            supplying build context — mirrors ``scan --build-info``.
        against: Previous build's dump/library to compare against (omit for a
            single-release audit — the always-on hygiene catalog runs either way).
        depth: Coarse evidence-depth selector: "binary", "headers", "build", or
            "source" (None = inferred from inputs, escalating with the
            changed-path risk score once seeded).
        changed_paths: Changed-path set focusing the scan (ADR-035 D7).
        language: Language mode — "c++" (default) or "c".
        policy: With ``against``: built-in policy — "strict_abi" (default),
            "sdk_vendor", or "plugin_abi". Ignored for a one-build audit, which
            renders no comparison verdict (ADR-049 Phase 5 §6.4 config-surface
            parity with ``abi_compare``).
        policy_file: With ``against``: custom YAML policy file (overrides
            ``policy``).
        suppression_file: With ``against``: YAML suppression file filtering
            known changes out of the comparison.
        contract_evaluation: With ``against``: stamp each comparison finding
            with its ADR-049 contract decision, exactly as ``abi_compare``'s own
            argument does. Authoritative since ADR-049 Phase 7 — it changes the
            verdict and the exit code, and an incomplete contract domain
            contributes the orthogonal coverage exit.
        contract_mode: With ``contract_evaluation``: which evidence domain the
            decision is judged against — "public", "exports", or "all". Omitted,
            the domain follows the scan's public-surface scoping.
        ast_frontend: L2 header-AST frontend — "auto" (default), "castxml",
            "clang", or "hybrid". Unlike ``abi_dump``, "android" is rejected
            here: it is source-ABI-replay only, and a scan has no
            request-level frontend to carry it into source replay, so it would
            be accepted and then silently ignored.
        gcc_path: Explicit compiler binary for the header frontend.
        gcc_prefix: Cross-toolchain prefix for the header frontend.
        gcc_options: Extra compiler flags for the header frontend, as one
            shell-quoted string.
        sysroot: Alternate sysroot for the header frontend.
        nostdinc: Suppress the standard include paths for the header frontend.
        frontend_context: Which AST context the header frontend targets —
            "host" (default) or "device" (SYCL/DPC++ offload target).
    """
    t0 = _time.monotonic()
    try:
        from .service import Budget, ScanRequest, run_scan_subprocess

        bin_path = _safe_read_path(binary_path, label="binary_path")
        if not bin_path.exists():
            return json.dumps({"status": "error", "error": "Binary file not found"})
        _check_file_size(bin_path, label="binary_path")
        try:
            depth = _validate_public_depth(depth)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        hdr_paths = [_safe_read_path(h, label="header") for h in (headers or [])]
        for hdr in hdr_paths:
            _check_file_size(hdr, label="header")
        inc_paths = [
            _safe_read_path(d, label="include_dir") for d in (include_dirs or [])
        ]
        # One try/except for both argument families this PR added (a fresh
        # review's own suggestion — these were two separate try/except blocks
        # doing the identical ValueError-to-JSON conversion). `depth` above
        # stays in its own, separate try: it predates this PR and sits before
        # the unrelated hdr_paths/inc_paths construction, so folding it in
        # here would reorder pre-existing statements for no benefit.
        try:
            phd_paths = _public_header_dir_paths(public_header_dirs)
            # Same existence rule as `abi_dump` (Codex review, P1): every one
            # of these infers evidence collection from being set, and the
            # `scan` CLI declares all four `click.Path(exists=True)`.
            # `sources`/`compile_db` predate this PR and carried the identical
            # hole -- fixed alongside `build_info` rather than left as a
            # knowingly-broken twin.
            src_path = _existing_path(sources, label="sources") if sources else None
            cdb_path = (
                _existing_path(compile_db, label="compile_db") if compile_db else None
            )
            bi_path = (
                _existing_path(build_info, label="build_info") if build_info else None
            )
            base_path = _existing_path(against, label="against") if against else None
            compile_ctx = _compile_context_from_args(
                ast_frontend=ast_frontend,
                gcc_path=gcc_path,
                gcc_prefix=gcc_prefix,
                gcc_options=gcc_options,
                sysroot=sysroot,
                nostdinc=nostdinc,
                frontend_context=frontend_context,
            )
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        # ADR-049 Phase 5 §6.4: a `--against` comparison is scoped/suppressed/
        # policy-classified the same way a direct `compare` is, so this tool
        # exposes the same three inputs `abi_compare` does. Same MCP-local
        # guards, applied before anything loads them (G33 Phase 5).
        if policy_file is None and policy not in VALID_BASE_POLICIES:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Unknown policy: {policy!r}. "
                    f"Valid policies: {', '.join(sorted(VALID_BASE_POLICIES))}",
                }
            )
        suppression_path = None
        if suppression_file:
            suppression_path = _safe_read_path(
                suppression_file, label="suppression_file"
            )
            _check_file_size(suppression_path, label="suppression_file")
        policy_path = None
        if policy_file:
            policy_path = _safe_read_path(policy_file, label="policy_file")
            _check_file_size(policy_path, label="policy_file")
        # `CompareRequest.validate()` states the not-found case for
        # `abi_compare`; `ScanRequest` has no `validate()`, so without this a
        # missing file reaches the loader and surfaces as a sanitized
        # FileNotFoundError instead of naming the argument at fault.
        for missing_path, label in (
            (suppression_path, "suppression_file"),
            (policy_path, "policy_file"),
        ):
            if missing_path is not None and not missing_path.exists():
                return json.dumps(
                    {"status": "error", "error": f"{label} not found: {missing_path}"}
                )
        contract_error = _contract_mode_error(contract_mode, contract_evaluation)
        if contract_error is not None:
            return json.dumps({"status": "error", "error": contract_error})
        frontend_error = _source_abi_only_frontend_error(ast_frontend)
        if frontend_error is not None:
            return json.dumps({"status": "error", "error": frontend_error})
        from .service import load_suppression_and_policy

        suppression, loaded_policy_file = load_suppression_and_policy(
            suppression_path, policy, policy_path
        )

        req = ScanRequest(
            binaries=[bin_path],
            headers=hdr_paths,
            includes=inc_paths,
            public_header_dirs=phd_paths,
            sources=src_path,
            compile_db=cdb_path,
            build_info=bi_path,
            baseline=base_path,
            # Absence of --against is a one-build audit; presence is compare-too
            # (ADR-043) — neither is a separate mode argument on the MCP surface.
            mode="audit" if base_path is None else "pr",
            depth=depth,
            changed_paths=list(changed_paths or []),
            seeded=changed_paths is not None,
            budget=Budget(),
            lang=language,
            compile=compile_ctx or CompileContext(),
            policy=policy,
            policy_file=loaded_policy_file,
            suppression=suppression,
            contract_evaluation=contract_evaluation,
            contract_mode=contract_mode,
        )

        # A one-build audit (no `against`) has nothing for the comparison-only
        # arguments to configure, and `run_scan` rejects them -- but it does so
        # *inside* the spawned worker, where `run_scan_subprocess` rethrows it
        # as a RuntimeError this tool then reports as a sanitized unexpected
        # error, after paying for a process spawn. Checked here instead, so the
        # caller gets the usage error the CLI would give them (Codex review).
        # Reuses the engine's own field list rather than restating it: a field
        # that becomes comparison-only later must not need a second edit here.
        if base_path is None:
            from .service_scan import _reject_comparison_only_fields

            try:
                _reject_comparison_only_fields(req)
            except ValidationError as exc:
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"{exc} On this tool, set `against` to compare "
                        "against a baseline, or drop these arguments.",
                    }
                )

        # Run in a killable child process so a deep/hung scan that exceeds the
        # timeout is *terminated* (process + clang subtree) instead of orphaned to
        # keep burning CPU after the timeout response is sent (Codex review).
        try:
            payload = run_scan_subprocess(req, mcp_shared.MCP_TIMEOUT)
        except TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_scan", {"binary": bin_path.name}, elapsed, "timeout")
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_scan timed out after {mcp_shared.MCP_TIMEOUT}s",
                }
            )

        elapsed = _time.monotonic() - t0
        _audit_log("abi_scan", {"binary": bin_path.name}, elapsed, "ok")
        return json.dumps({"status": "ok", **payload})
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_scan", {"binary": Path(binary_path).name}, elapsed, "error")
        _logger.exception("abi_scan failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_scan")}
        )

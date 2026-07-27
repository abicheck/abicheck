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

"""MCP tools for the ``deps``/``aggregate``/``project`` CLI groups.

Split out of :mod:`abicheck.mcp_server` to keep that module under the
AI-readiness file-size cap, mirroring the ``cli_<name>.py`` sibling-module
pattern (e.g. ``cli_stack.py``/``cli_aggregate.py``/``cli_project.py``) used
for the CLI itself. Imported for side-effect (and re-exported) at the bottom
of :mod:`abicheck.mcp_server` so the ``@mcp.tool()`` decorators below run
against the shared ``mcp`` instance.

Each tool below reuses the exact same non-Click logic its matching CLI
command calls (``stack_checker.check_single_env``,
``aggregate.aggregate_reports_dir``,
``buildsource.project_targets.validate_project_targets``,
``buildsource.run_plan.generate_run_plan``) rather than reimplementing it, so
behavior can't drift between the CLI and MCP surfaces.

Deliberately imports its stable helpers (``mcp``/``_safe_read_path``/
``_sanitize_error``) from the leaf module ``mcp_shared`` rather than from
:mod:`abicheck.mcp_server` itself: this module is imported *by*
``mcp_server`` (for tool registration), so importing back from it here would
recreate an import cycle the split was meant to avoid (AGENTS.md "What NOT to
do" — a new cycle needs a leaf module, not an allowlist entry). The
``_check_file_size``/``_audit_log``/``_MCP_TIMEOUT`` guards below are
therefore small local copies with their own module-level config (ADR-021b
D2/D3 apply here the same as every other tool), not shared with
``mcp_server.py``'s mutable ``MCP_TIMEOUT``/``MCP_MAX_FILE_SIZE``/
``--log-format`` state — the ``ABICHECK_MCP_TIMEOUT``/
``ABICHECK_MCP_MAX_FILE_SIZE`` env vars still apply to both, only the
``--timeout``/``--max-file-size``/``--log-format`` CLI-flag overrides on a
running server don't retroactively reach these four tools.
"""

from __future__ import annotations

import concurrent.futures as _futures
import json
import os as _os
import time as _time
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

from .binary_utils import detect_binary_format as _detect_binary_format
from .mcp_shared import _logger, _safe_read_path, _sanitize_error, mcp

_P = ParamSpec("_P")
_R = TypeVar("_R")

#: Local copies of mcp_server.py's guard defaults (see module docstring for
#: why these aren't shared mutable state with that module).
_MAX_FILE_SIZE = int(
    _os.environ.get("ABICHECK_MCP_MAX_FILE_SIZE", str(500 * 1024 * 1024))
)
_MCP_TIMEOUT = int(_os.environ.get("ABICHECK_MCP_TIMEOUT", "120"))


def _check_file_size(path: Path, *, label: str = "input") -> None:
    """Raise ValueError if *path* exceeds ``_MAX_FILE_SIZE`` (ADR-021b D3)."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return  # let downstream handle missing files
    except OSError as exc:
        raise ValueError(f"Cannot check {label} file size: {exc}") from exc
    if size > _MAX_FILE_SIZE:
        raise ValueError(
            f"{label} is {size / (1024 * 1024):.1f} MB, "
            f"exceeds limit of {_MAX_FILE_SIZE / (1024 * 1024):.0f} MB"
        )


def _check_dir_json_file_sizes(directory: Path, *, label: str = "input") -> None:
    """Apply ``_check_file_size`` to every ``*.json`` file directly under
    *directory* (ADR-021b D3) -- for tools that read a whole directory of
    per-target reports rather than one path a caller named explicitly."""
    for entry in directory.glob("*.json"):
        _check_file_size(entry, label=f"{label} ({entry.name})")


def _audit_log(
    tool: str,
    inputs: dict[str, str],
    duration_s: float,
    status: str,
) -> None:
    """Log a tool invocation for audit purposes (plain-text format)."""
    parts = [f"tool={tool}"]
    for k, v in inputs.items():
        parts.append(f"{k}={v}")
    parts.append(f"duration={duration_s:.3f}s")
    parts.append(f"status={status}")
    _logger.info(" ".join(parts))


def _call_with_timeout(fn: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
    """Run ``fn(*args, **kwargs)`` in a thread bounded by ``_MCP_TIMEOUT``.

    ADR-021b D2: every tool invocation must have a configurable timeout
    rather than blocking the MCP stdio server indefinitely. Raises
    ``concurrent.futures.TimeoutError`` on expiry; any exception *fn* itself
    raises propagates unchanged (re-raised by ``future.result()``) so callers
    can catch their own domain exceptions the same way they would a direct
    call.
    """
    with _futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args, **kwargs)
        return future.result(timeout=_MCP_TIMEOUT)


@mcp.tool()
def abi_deps(
    binary_path: str,
    search_paths: list[str] | None = None,
    sysroot: str | None = None,
    ld_library_path: str = "",
) -> str:
    """Resolve a binary's shared-library dependency stack and symbol bindings.

    Wraps the same single-environment check the ``deps tree`` CLI command
    runs: the loader-accurate transitive closure of ``DT_NEEDED`` dependencies
    (RPATH/RUNPATH/``LD_LIBRARY_PATH``/default search order) plus per-symbol
    binding status, so an agent can answer "will this binary load, and are all
    its required symbols bound?" without a shell.

    Args:
        binary_path: Root ELF binary to resolve dependencies for.
        search_paths: Additional directories to search for shared libraries.
        sysroot: Sysroot prefix for cross/container analysis.
        ld_library_path: Simulated ``LD_LIBRARY_PATH`` (colon-separated).
    """
    t0 = _time.monotonic()
    try:
        from .stack_checker import check_single_env
        from .stack_report import stack_to_json

        bin_path = _safe_read_path(binary_path, label="binary_path")
        if not bin_path.exists():
            return json.dumps({"status": "error", "error": "Binary file not found"})
        fmt = _detect_binary_format(bin_path)
        if fmt != "elf":
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_deps requires an ELF binary; got {fmt or 'unknown format'}",
                }
            )
        _check_file_size(bin_path, label="binary_path")
        sp_paths = [
            _safe_read_path(p, label="search_path") for p in (search_paths or [])
        ]
        sysroot_path = _safe_read_path(sysroot, label="sysroot") if sysroot else None

        try:
            result = _call_with_timeout(
                check_single_env,
                bin_path,
                search_paths=sp_paths or None,
                sysroot=sysroot_path,
                ld_library_path=ld_library_path,
            )
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_deps", {"binary": bin_path.name}, elapsed, "timeout")
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_deps timed out after {_MCP_TIMEOUT}s",
                }
            )

        elapsed = _time.monotonic() - t0
        _audit_log("abi_deps", {"binary": bin_path.name}, elapsed, "ok")
        return json.dumps({"status": "ok", "result": json.loads(stack_to_json(result))})
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_deps", {"binary": Path(binary_path).name}, elapsed, "error")
        _logger.exception("abi_deps failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_deps")}
        )


@mcp.tool()
def abi_aggregate(
    reports_dir: str,
    manifest: str | None = None,
    run_plan: str | None = None,
    expect: list[str] | None = None,
    optional: list[str] | None = None,
    discovered_only: bool = False,
    on_missing_required: str = "fail",
    on_unexpected_target: str = "include",
) -> str:
    """Fold per-target ``compare``/``scan`` reports into one CI gate decision.

    Wraps the same fan-in logic the ``aggregate`` CLI command runs (ADR-042):
    three axes stay separate — compatibility (worst verdict, for reporting),
    gate (each report's own recorded severity decision, combined — never
    recomputed from the verdict), and coverage (did every required target
    report?). A required target with no report is unavailable and fails the
    coverage gate rather than being treated as compatible.

    Args:
        reports_dir: Directory holding the per-target report JSON files.
        manifest: Expected-target manifest path (mutually exclusive with
            run_plan/expect/discovered_only).
        run_plan: A ``project plan``-generated ``run-plan.json`` path, used as
            the expected-target set (mutually exclusive with the others).
        expect: Required target id(s) (mutually exclusive with manifest/run_plan).
        optional: Optional target id(s) — only meaningful together with expect.
        discovered_only: Aggregate whatever reports are present with no
            coverage gate (mutually exclusive with the other four).
        on_missing_required: ``"fail"`` (default) or ``"warn"`` — how an
            unavailable required target affects the exit code.
        on_unexpected_target: ``"include"`` (default), ``"warn"``, ``"fail"``,
            or ``"ignore"`` — how a report for an unexpected target is handled.
    """
    t0 = _time.monotonic()
    try:
        import click

        from .aggregate import (
            AggregateError,
            OnMissingRequired,
            OnUnexpectedTarget,
            aggregate_reports_dir,
        )
        from .cli_aggregate import _resolve_expected

        reports_path = _safe_read_path(reports_dir, label="reports_dir")
        if not reports_path.is_dir():
            return json.dumps(
                {
                    "status": "error",
                    "error": f"reports_dir is not a directory: {reports_dir}",
                }
            )
        _check_dir_json_file_sizes(reports_path, label="report")
        manifest_path = (
            _safe_read_path(manifest, label="manifest") if manifest else None
        )
        if manifest_path is not None:
            _check_file_size(manifest_path, label="manifest")
        run_plan_path = (
            _safe_read_path(run_plan, label="run_plan") if run_plan else None
        )
        if run_plan_path is not None:
            _check_file_size(run_plan_path, label="run_plan")

        try:
            expected = _resolve_expected(
                manifest_path,
                run_plan_path,
                tuple(expect or ()),
                tuple(optional or ()),
                discovered_only,
            )
        except (click.UsageError, AggregateError) as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        try:
            result = _call_with_timeout(
                aggregate_reports_dir,
                reports_path,
                expected=expected,
                discovered_only=discovered_only,
                on_missing_required=OnMissingRequired(on_missing_required),
                on_unexpected_target=OnUnexpectedTarget(on_unexpected_target),
            )
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_aggregate", {"reports_dir": reports_path.name}, elapsed, "timeout"
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_aggregate timed out after {_MCP_TIMEOUT}s",
                }
            )
        except (AggregateError, ValueError) as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        elapsed = _time.monotonic() - t0
        _audit_log("abi_aggregate", {"reports_dir": reports_path.name}, elapsed, "ok")
        return json.dumps(
            {
                "status": "ok",
                "exit_code": result.exit_code(),
                "result": result.to_dict(),
            }
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log(
            "abi_aggregate", {"reports_dir": Path(reports_dir).name}, elapsed, "error"
        )
        _logger.exception("abi_aggregate failed")
        return json.dumps(
            {"status": "error", "error": _sanitize_error(exc, context="abi_aggregate")}
        )


@mcp.tool()
def abi_project_validate(
    config: str = ".abicheck.yml",
    toolchain_bindings: str | None = None,
) -> str:
    """Validate a project config's ``targets:``/``bundles:``/``profiles:`` block.

    Wraps the same checks the ``project validate`` CLI command runs (ADR-047
    §3): every target's kind-specific required fields, bundle references and
    membership agreement, every ``checks[].channel``/``depth``/``gate_mode``/
    ``profiles`` resolution, and id validity. Structural/type errors in the
    YAML itself surface as a plain error result rather than a validation
    finding — this only covers cross-reference/semantic issues on an
    already-well-formed block.

    Args:
        config: Path to the project config (default ``.abicheck.yml``).
        toolchain_bindings: Optional trusted toolchain-bindings file path
            (schema ``abicheck.toolchain-bindings/v1``) to additionally check
            every declared ``profiles.<id>.compile.binding`` against.
    """
    t0 = _time.monotonic()
    try:
        import click

        from .buildsource.project_targets import (
            ProjectTargetsValidationReport,
            validate_project_targets,
        )
        from .buildsource.toolchain_bindings import (
            BindingsFileError,
            check_profile_bindings_resolve,
            load_bindings_file,
        )
        from .cli_project import _load_project_targets_config

        config_path = _safe_read_path(config, label="config")
        if not config_path.exists():
            return json.dumps({"status": "error", "error": "config file not found"})
        _check_file_size(config_path, label="config")

        try:
            parsed = _load_project_targets_config(config_path)
        except click.UsageError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        if toolchain_bindings is not None:
            bindings_path = _safe_read_path(
                toolchain_bindings, label="toolchain_bindings"
            )
            _check_file_size(bindings_path, label="toolchain_bindings")
            try:
                bindings_file = load_bindings_file(bindings_path)
            except BindingsFileError as exc:
                return json.dumps({"status": "error", "error": str(exc)})
        else:
            bindings_file = None

        def _do_validate() -> ProjectTargetsValidationReport:
            report = validate_project_targets(parsed)
            if bindings_file is not None:
                report.errors.extend(
                    check_profile_bindings_resolve(parsed.profiles, bindings_file)
                )
            return report

        try:
            report = _call_with_timeout(_do_validate)
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_project_validate", {"config": config_path.name}, elapsed,
                "timeout",
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_project_validate timed out after {_MCP_TIMEOUT}s",
                }
            )

        elapsed = _time.monotonic() - t0
        _audit_log("abi_project_validate", {"config": config_path.name}, elapsed, "ok")
        return json.dumps({"status": "ok", "result": report.to_dict()})
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log(
            "abi_project_validate", {"config": Path(config).name}, elapsed, "error"
        )
        _logger.exception("abi_project_validate failed")
        return json.dumps(
            {
                "status": "error",
                "error": _sanitize_error(exc, context="abi_project_validate"),
            }
        )


@mcp.tool()
def abi_project_plan(
    config: str = ".abicheck.yml",
    build_outputs: list[str] | None = None,
    project: str = "",
    head_sha: str = "",
    toolchain_bindings: str | None = None,
    allow_empty: bool = False,
) -> str:
    """Generate a run-plan from a project config's contract profiles.

    Wraps the same resolution the ``project plan`` CLI command runs (ADR-047
    §5/§7): for every ``checks[]`` entry (per target or per bundle), resolves
    which ``(target, profile)`` cells actually apply against each profile's
    supplied build-output, and returns the ordered check list plus generation
    errors/warnings.

    Args:
        config: Path to the project config (default ``.abicheck.yml``).
        build_outputs: One entry per contract profile referenced by config's
            checks, each formatted ``PROFILE=DIR`` (a directory containing
            ``build-output.json``).
        project: Project identifier recorded in the run-plan, e.g. ``owner/repo``.
        head_sha: Candidate commit SHA recorded in the run-plan.
        toolchain_bindings: Optional trusted toolchain-bindings file path; each
            resolved cell's profile ``compile.binding`` (if declared) is
            checked against it and resolved into that cell's ``compile_gcc_path``.
        allow_empty: Accept a run-plan that resolves to zero checks (else that
            is reported as a generation error).
    """
    t0 = _time.monotonic()
    try:
        import click

        from .buildsource.project_targets import validate_project_targets
        from .buildsource.run_plan import (
            RunPlan,
            RunPlanGenerationReport,
            generate_run_plan,
        )
        from .buildsource.toolchain_bindings import (
            BindingsFileError,
            check_profile_bindings_resolve,
            load_bindings_file,
        )
        from .cli_project import _load_project_targets_config, _parse_build_output_specs

        config_path = _safe_read_path(config, label="config")
        if not config_path.exists():
            return json.dumps({"status": "error", "error": "config file not found"})
        _check_file_size(config_path, label="config")

        try:
            parsed = _load_project_targets_config(config_path)
        except click.UsageError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        validation = validate_project_targets(parsed)
        if not validation.ok:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        "cannot generate a run-plan from an invalid project "
                        f"config ({len(validation.errors)} error(s)): "
                        + "; ".join(validation.errors)
                    ),
                }
            )

        # _parse_build_output_specs reads each PROFILE=DIR's build-output.json
        # itself; check every one's size *before* that parse (ADR-021b D3) --
        # a best-effort pre-check keyed on the same "=" split it uses, so a
        # malformed spec here just falls through to that function's own error.
        for spec in build_outputs or ():
            _, sep, dir_str = spec.partition("=")
            if sep:
                _check_file_size(
                    Path(dir_str) / "build-output.json", label="build_output"
                )
        try:
            resolved_build_outputs = _parse_build_output_specs(
                tuple(build_outputs or ())
            )
        except click.UsageError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        resolved_bindings: dict[str, str] | None = None
        binding_errors: list[str] = []
        if toolchain_bindings is not None:
            bindings_path = _safe_read_path(
                toolchain_bindings, label="toolchain_bindings"
            )
            _check_file_size(bindings_path, label="toolchain_bindings")
            try:
                bindings_file = load_bindings_file(bindings_path)
            except BindingsFileError as exc:
                return json.dumps({"status": "error", "error": str(exc)})
            resolved_bindings = bindings_file.bindings
            binding_errors = check_profile_bindings_resolve(
                parsed.profiles, bindings_file
            )

        def _do_plan() -> tuple[RunPlan, RunPlanGenerationReport]:
            plan, report = generate_run_plan(
                parsed,
                resolved_build_outputs,
                project=project,
                head_sha=head_sha,
                resolved_bindings=resolved_bindings,
            )
            report.errors.extend(binding_errors)
            if not plan.checks and not allow_empty:
                report.errors.append(
                    "run-plan resolved to zero checks -- pass allow_empty=true to "
                    "accept this (e.g. bootstrapping .abicheck.yml before any "
                    "targets:/bundles: checks[] are declared yet)."
                )
            return plan, report

        try:
            plan, report = _call_with_timeout(_do_plan)
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_project_plan", {"config": config_path.name}, elapsed, "timeout"
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_project_plan timed out after {_MCP_TIMEOUT}s",
                }
            )

        elapsed = _time.monotonic() - t0
        _audit_log("abi_project_plan", {"config": config_path.name}, elapsed, "ok")
        return json.dumps(
            {
                "status": "ok",
                "plan": plan.to_dict(),
                "report": report.to_dict(),
            }
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _audit_log("abi_project_plan", {"config": Path(config).name}, elapsed, "error")
        _logger.exception("abi_project_plan failed")
        return json.dumps(
            {
                "status": "error",
                "error": _sanitize_error(exc, context="abi_project_plan"),
            }
        )

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
``_sanitize_error``/``_check_file_size``/``_audit_log``) from the leaf module
``mcp_shared`` rather than from :mod:`abicheck.mcp_server` itself: this
module is imported *by* ``mcp_server`` (for tool registration), so importing
back from it here would recreate an import cycle the split was meant to
avoid (AGENTS.md "What NOT to do" — a new cycle needs a leaf module, not an
allowlist entry). ``_check_file_size``/``_audit_log`` are pure functions, so
importing them as bare names is safe — a function's global lookups always
resolve against its *defining* module (``mcp_shared``), regardless of which
module calls it. The mutable ``MCP_TIMEOUT``/``MCP_MAX_FILE_SIZE``/
``--log-format`` config is different: it's read via module-qualified
``mcp_shared.MCP_TIMEOUT`` (never a bare imported name) so this module's
tools observe the same ``--timeout``/``--max-file-size``/``--log-format``
override :func:`abicheck.mcp_server.main` applies to
``mcp_shared`` at startup — see that module's own docstring for why a bare
``from .mcp_shared import MCP_TIMEOUT`` would bind a stale snapshot instead.
"""

from __future__ import annotations

import concurrent.futures as _futures
import json
import time as _time
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

from . import mcp_shared
from .aggregate import DEFAULT_REPORT_PREFIX
from .binary_utils import detect_binary_format as _detect_binary_format
from .mcp_shared import (
    _audit_log,
    _check_file_size,
    _logger,
    _safe_read_path,
    _sanitize_error,
    mcp,
)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _resolve_sysroot_path(binary: Path, sysroot: Path | None) -> Path:
    """Mirror ``resolver._seed_root``'s sysroot rebasing so the existence/
    format/size pre-checks below validate the file that will actually be
    parsed, not the (possibly unrelated) host-absolute path — an absolute
    ``binary`` under an active ``sysroot`` is looked up beneath the sysroot,
    exactly as ``_seed_root`` does before calling ``parse_elf_metadata``."""
    if (
        sysroot is not None
        and binary.is_absolute()
        and not binary.is_relative_to(sysroot)
    ):
        return (sysroot / str(binary).lstrip("/")).resolve()
    return binary


def _check_dir_json_file_sizes(directory: Path, *, label: str = "input") -> None:
    """Apply ``_check_file_size`` to every ``*.json`` file directly under
    *directory* (ADR-021b D3) -- for tools that read a whole directory of
    per-target reports rather than one path a caller named explicitly."""
    for entry in directory.glob("*.json"):
        _check_file_size(entry, label=f"{label} ({entry.name})")


def _effective_search_path(entry: Path, sysroot: Path | None) -> Path:
    """Mirror ``resolver._build_search_order``'s ``extra_dirs`` join exactly,
    so an existence pre-check validates the same location
    ``resolve_dependencies`` will actually search there. Unlike the root
    binary's sysroot rebase (``_resolve_sysroot_path``, only rebased when not
    already under sysroot), every search-path entry -- relative or absolute
    -- is unconditionally joined onto an active sysroot (``os.path.join(prefix,
    d.lstrip("/"))`` whenever ``prefix`` is truthy); there is no
    already-under-sysroot guard for this one, so this helper must not reuse
    ``_resolve_sysroot_path`` (Codex review)."""
    if sysroot is not None:
        return sysroot / str(entry).lstrip("/")
    return entry


def _redact_paths(message: str, *paths: str | Path) -> str:
    """Replace any occurrence of a known local path with its basename.

    ``click.UsageError``s raised by the shared CLI parsing helpers this
    module reuses (``_load_project_targets_config``, ``_parse_build_output_
    specs``, ``_resolve_expected``) embed the full path for a human terminal
    reader; an MCP response must not leak local filesystem structure the
    same way ``_check_file_size``'s own errors already avoid it (ADR-021b,
    Codex review) -- every path this tool itself resolved is substituted
    with just its final component before the message reaches the caller.

    Substitutes longest paths first: if one path is a prefix of another
    (e.g. a bindings dir nested under the config dir), replacing the shorter
    one first would rewrite it *inside* the longer path's own text too,
    corrupting the longer substitution that runs after it (CodeRabbit
    review).
    """
    sorted_paths = sorted((str(p) for p in paths), key=len, reverse=True)
    for p_str in sorted_paths:
        if p_str:
            message = message.replace(p_str, Path(p_str).name)
    return message


def _call_with_timeout(
    fn: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs
) -> _R:
    """Run ``fn(*args, **kwargs)`` in a thread bounded by ``mcp_shared.MCP_TIMEOUT``.

    ADR-021b D2: every tool invocation must have a configurable timeout
    rather than blocking the MCP stdio server indefinitely. Raises
    ``concurrent.futures.TimeoutError`` on expiry; any exception *fn* itself
    raises propagates unchanged (re-raised by ``future.result()``) so callers
    can catch their own domain exceptions the same way they would a direct
    call.

    Uses an explicit ``pool.shutdown(wait=False)`` in a ``finally`` rather
    than ``with ThreadPoolExecutor(...) as pool:`` — the ``with`` form calls
    ``shutdown(wait=True)`` on exit, which blocks until the still-running
    worker finishes even after ``future.result(timeout=...)`` has already
    raised ``TimeoutError``, defeating the point of the timeout for a
    genuinely stuck call.
    """
    pool = _futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn, *args, **kwargs)
        return future.result(timeout=mcp_shared.MCP_TIMEOUT)
    finally:
        pool.shutdown(wait=False)


class _ToolPreflightError(Exception):
    """Raised inside a tool's timed worker for a preflight failure that is
    not itself a domain error from the wrapped operation (missing input /
    wrong format) -- kept distinct from ``ValueError``/``FileNotFoundError``
    so it can't be conflated with an unrelated exception the wrapped
    operation itself might raise for those same built-in types. Every
    preflight check (existence/format/size) runs inside the timed worker
    alongside the wrapped operation, not before it, so a stalled filesystem
    can't defeat the tool's advertised ``--timeout`` (ADR-021b D2, Codex
    review)."""


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
        from .stack_checker import StackCheckResult, check_single_env
        from .stack_report import stack_to_json

        bin_path = Path(binary_path) if binary_path else Path()

        def _do_deps() -> StackCheckResult:
            # Path resolution (_safe_read_path's symlink-following .resolve()
            # call for sysroot), existence/format/size preflight, and
            # search_path validation all run inside the same bounded worker
            # as check_single_env: a stalled filesystem or a blocking
            # symlink lookup could block on any one of these, not just
            # check_single_env itself (Codex/CodeRabbit review).
            #
            # _safe_read_path always resolves to an absolute path
            # (path-safety), so its output can't tell us whether the
            # caller's own input was relative or absolute -- capture that
            # from the raw string first. resolver._seed_root only rebases
            # an *absolute* binary under sysroot; a relative one is always
            # resolved against cwd regardless of sysroot, matching the
            # `deps tree` CLI's Click-based (non-resolving) Path handling.
            # Without this, a relative binary_path + sysroot combination
            # (the CLI's own documented `deps tree ./app --sysroot ...`
            # example) would get wrongly rebased under sysroot at the
            # absolutized cwd, rather than left alone (Codex review).
            binary_was_absolute = (
                Path(binary_path).is_absolute() if binary_path else False
            )
            # Validate (empty-string/type errors) via _safe_read_path, but do
            # NOT use its resolved return value for an absolute path:
            # .resolve() follows host symlinks (e.g. a merged-/usr host's
            # "/lib" -> "/usr/lib"), so a sysroot-rebase computed from that
            # resolved value lands at "<sysroot>/usr/lib/app" instead of the
            # resolver/loader-semantic "<sysroot>/lib/app" -- the sysroot
            # rebase must apply to the raw, symbolic path the caller asked
            # for, the same way resolver._seed_root rebases the CLI's own
            # un-resolved Click Path (Codex review).
            _safe_read_path(binary_path, label="binary_path")
            raw_bin_path = Path(binary_path)
            sysroot_path = (
                _safe_read_path(sysroot, label="sysroot") if sysroot else None
            )
            # `deps tree --sysroot` is declared click.Path(exists=True); a
            # nonexistent sysroot must be rejected the same way, not silently
            # accepted and searched under (which could make a binary appear
            # loadable when its dependencies were never actually resolved
            # anywhere real) (Codex review).
            if sysroot_path is not None and not sysroot_path.exists():
                raise _ToolPreflightError("sysroot does not exist")
            resolve_target = raw_bin_path
            effective_path = (
                _resolve_sysroot_path(raw_bin_path, sysroot_path)
                if binary_was_absolute
                else raw_bin_path
            )
            if not effective_path.exists():
                raise _ToolPreflightError("Binary file not found")
            fmt = _detect_binary_format(effective_path)
            if fmt != "elf":
                raise _ToolPreflightError(
                    f"abi_deps requires an ELF binary; got {fmt or 'unknown format'}"
                )
            _check_file_size(effective_path, label="binary_path")
            # Same relative/absolute preservation as binary_path above -- and
            # the same host-symlink preservation for the absolute case:
            # resolver._build_search_order joins a search_path directly onto
            # sysroot (`<sysroot>/lib`), so an absolute entry must reach it
            # un-resolved, exactly like binary_path above (Codex review).
            # _safe_read_path is still called on an absolute entry for its
            # validation side effect; its resolved return value is
            # intentionally discarded. Existence is validated the same way
            # `deps tree`'s `click.Path(exists=True)` --search-path option
            # does, so a typo'd/missing search directory is a clear error
            # instead of a falsely-unresolved dependency (Codex review).
            sp_paths = []
            for p in search_paths or []:
                if not p or not p.strip():
                    raise _ToolPreflightError("Empty search_path is not allowed")
                p_path = Path(p)
                if p_path.is_absolute():
                    _safe_read_path(p, label="search_path")
                if not _effective_search_path(p_path, sysroot_path).exists():
                    raise _ToolPreflightError(
                        f"search_path does not exist: {p_path.name}"
                    )
                sp_paths.append(p_path)
            return check_single_env(
                resolve_target,
                search_paths=sp_paths or None,
                sysroot=sysroot_path,
                ld_library_path=ld_library_path,
                max_file_size=mcp_shared.MCP_MAX_FILE_SIZE,
            )

        try:
            result = _call_with_timeout(_do_deps)
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_deps", {"binary": bin_path.name}, elapsed, "timeout")
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_deps timed out after {mcp_shared.MCP_TIMEOUT}s",
                }
            )
        except _ToolPreflightError as exc:
            elapsed = _time.monotonic() - t0
            _audit_log("abi_deps", {"binary": bin_path.name}, elapsed, "error")
            return json.dumps({"status": "error", "error": str(exc)})

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
    report_prefix: str = DEFAULT_REPORT_PREFIX,
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
        report_prefix: Filename prefix stripped when deriving a target id
            from a report file that does not self-identify a ``target_id``
            (e.g. ``"abi-report-linux.json"`` -> ``"linux"``).
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
            AggregateResult,
            OnMissingRequired,
            OnUnexpectedTarget,
            aggregate_reports_dir,
        )
        from .cli_aggregate import _resolve_expected

        def _do_aggregate() -> AggregateResult:
            # reports_dir/manifest/run_plan path resolution (each a
            # symlink-following .resolve() call), the exists()/is_dir() type
            # check, report-directory discovery (an unbounded glob+stat over
            # every *.json entry), the manifest/run-plan size probes, and
            # expected-set parsing all run inside the same bounded worker as
            # aggregate_reports_dir (ADR-021b D2): a stalled NFS/FUSE mount
            # blocking any one of these filesystem calls must count against
            # --timeout too, not just the aggregate call that follows them
            # (Codex review -- manifest/run_plan resolution originally ran
            # before this closure started, same gap already closed for
            # reports_dir/config/toolchain_bindings elsewhere).
            reports_path = _safe_read_path(reports_dir, label="reports_dir")
            manifest_path = (
                _safe_read_path(manifest, label="manifest") if manifest else None
            )
            run_plan_path = (
                _safe_read_path(run_plan, label="run_plan") if run_plan else None
            )
            # A *missing* reports_dir is deliberately not an error here --
            # aggregate.collect_reports treats it as zero reports so a full
            # build-matrix outage still produces a structured required-coverage
            # failure (exit code 1) instead of a generic tool error. An
            # *existing-but-not-a-directory* reports_dir is different:
            # collect_reports treats that identically to "missing" too (its
            # own `if not reports_dir.is_dir(): return found` doesn't
            # distinguish the two), but the CLI's `aggregate --reports-dir`
            # has no Click-level type check either (`click.Path(path_type=
            # Path)`, no `exists=True`/`file_okay=False`) -- so silently
            # reporting "zero reports found" for a typo'd file path would be
            # confusing for an MCP caller with no terminal to notice the
            # coverage-gate wording. This check is therefore an intentional
            # MCP-specific improvement over the CLI's behavior for this one
            # input shape, not a divergence "matching" some existing
            # contract -- there is no existing contract to match here
            # (code-review finding on an earlier version of this comment).
            if reports_path.exists() and not reports_path.is_dir():
                raise _ToolPreflightError(
                    f"reports_dir is not a directory: {reports_path.name}"
                )
            _check_dir_json_file_sizes(reports_path, label="report")
            if manifest_path is not None:
                _check_file_size(manifest_path, label="manifest")
            if run_plan_path is not None:
                _check_file_size(run_plan_path, label="run_plan")
            expected = _resolve_expected(
                manifest_path,
                run_plan_path,
                tuple(expect or ()),
                tuple(optional or ()),
                discovered_only,
            )
            return aggregate_reports_dir(
                reports_path,
                expected=expected,
                discovered_only=discovered_only,
                on_missing_required=OnMissingRequired(on_missing_required),
                on_unexpected_target=OnUnexpectedTarget(on_unexpected_target),
                prefix=report_prefix,
            )

        try:
            result = _call_with_timeout(_do_aggregate)
        except _futures.TimeoutError:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_aggregate",
                {"reports_dir": Path(reports_dir).name},
                elapsed,
                "timeout",
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_aggregate timed out after {mcp_shared.MCP_TIMEOUT}s",
                }
            )
        except _ToolPreflightError as exc:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_aggregate",
                {"reports_dir": Path(reports_dir).name},
                elapsed,
                "error",
            )
            return json.dumps({"status": "error", "error": str(exc)})
        except (click.UsageError, AggregateError, ValueError) as exc:
            redact_args: list[str | Path] = [Path(reports_dir)]
            # aggregate_reports_dir/_resolve_expected embed the *resolved*
            # reports_dir in some error text, not the raw caller-supplied
            # string -- redact both forms. Best-effort: an error here just
            # means one fewer path substituted, not a failure to respond.
            try:
                redact_args.append(_safe_read_path(reports_dir, label="reports_dir"))
            except ValueError:
                pass
            if manifest is not None:
                redact_args.append(Path(manifest))
                try:
                    redact_args.append(_safe_read_path(manifest, label="manifest"))
                except ValueError:
                    pass
            if run_plan is not None:
                redact_args.append(Path(run_plan))
                try:
                    redact_args.append(_safe_read_path(run_plan, label="run_plan"))
                except ValueError:
                    pass
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_aggregate",
                {"reports_dir": Path(reports_dir).name},
                elapsed,
                "error",
            )
            return json.dumps(
                {"status": "error", "error": _redact_paths(str(exc), *redact_args)}
            )

        elapsed = _time.monotonic() - t0
        _audit_log(
            "abi_aggregate", {"reports_dir": Path(reports_dir).name}, elapsed, "ok"
        )
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

        def _do_validate() -> ProjectTargetsValidationReport:
            # Path resolution (_safe_read_path's symlink-following .resolve()
            # call), existence/size preflight, and config/bindings parsing
            # all run inside the same bounded worker as
            # validate_project_targets (ADR-021b D2): a stalled NFS/FUSE
            # mount or a blocking symlink lookup could block on any one of
            # these filesystem calls, not just the validation call that
            # follows them (Codex review).
            config_path = _safe_read_path(config, label="config")
            bindings_path = (
                _safe_read_path(toolchain_bindings, label="toolchain_bindings")
                if toolchain_bindings is not None
                else None
            )
            if not config_path.exists():
                raise _ToolPreflightError("config file not found")
            _check_file_size(config_path, label="config")
            if bindings_path is not None:
                _check_file_size(bindings_path, label="toolchain_bindings")
            parsed = _load_project_targets_config(config_path)
            bindings_file = (
                load_bindings_file(bindings_path) if bindings_path is not None else None
            )
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
                "abi_project_validate",
                {"config": Path(config).name},
                elapsed,
                "timeout",
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_project_validate timed out after {mcp_shared.MCP_TIMEOUT}s",
                }
            )
        except _ToolPreflightError as exc:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_project_validate", {"config": Path(config).name}, elapsed, "error"
            )
            return json.dumps({"status": "error", "error": str(exc)})
        except (click.UsageError, BindingsFileError) as exc:
            redact_args: list[str | Path] = [Path(config)]
            if toolchain_bindings is not None:
                redact_args.append(Path(toolchain_bindings))
            # Best-effort: also redact the resolved forms, since some errors
            # embed the resolved path rather than the raw caller-supplied
            # string. One fewer path substituted is not a failure to respond.
            try:
                redact_args.append(_safe_read_path(config, label="config"))
            except ValueError:
                pass
            if toolchain_bindings is not None:
                try:
                    redact_args.append(
                        _safe_read_path(toolchain_bindings, label="toolchain_bindings")
                    )
                except ValueError:
                    pass
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_project_validate", {"config": Path(config).name}, elapsed, "error"
            )
            return json.dumps(
                {"status": "error", "error": _redact_paths(str(exc), *redact_args)}
            )

        elapsed = _time.monotonic() - t0
        _audit_log("abi_project_validate", {"config": Path(config).name}, elapsed, "ok")
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


class _ProjectPlanValidationError(Exception):
    """Raised inside the timeout-bounded worker when the project config
    fails ``validate_project_targets`` (ADR-021b D2: validation over a large
    config must count against the same timeout as run-plan generation)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("invalid project config")
        self.errors = errors


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

        # _parse_build_output_specs reads each PROFILE=DIR's build-output.json
        # itself; the "=" split here is a best-effort pre-parse (ADR-021b D3)
        # just to collect each dir for the size probe below and for error
        # redaction -- a malformed spec still falls through to that
        # function's own error.
        build_output_dirs: list[str] = []
        for spec in build_outputs or ():
            _, sep, dir_str = spec.partition("=")
            if sep:
                build_output_dirs.append(dir_str)

        def _do_plan() -> tuple[RunPlan, RunPlanGenerationReport]:
            # Path resolution (_safe_read_path's symlink-following .resolve()
            # call), existence/size preflight, config/build-output/bindings
            # parsing, validation, and binding resolution all run inside the
            # same bounded worker as run-plan generation (ADR-021b D2): a
            # stalled NFS/FUSE mount or a blocking symlink lookup could block
            # on any one of these filesystem calls, not just the
            # generate_run_plan call that follows them (Codex review).
            config_path = _safe_read_path(config, label="config")
            bindings_path = (
                _safe_read_path(toolchain_bindings, label="toolchain_bindings")
                if toolchain_bindings is not None
                else None
            )
            if not config_path.exists():
                raise _ToolPreflightError("config file not found")
            _check_file_size(config_path, label="config")
            for dir_str in build_output_dirs:
                _check_file_size(
                    Path(dir_str) / "build-output.json", label="build_output"
                )
            if bindings_path is not None:
                _check_file_size(bindings_path, label="toolchain_bindings")
            parsed = _load_project_targets_config(config_path)
            resolved_build_outputs = _parse_build_output_specs(
                tuple(build_outputs or ())
            )
            bindings_file = (
                load_bindings_file(bindings_path) if bindings_path is not None else None
            )
            resolved_bindings = (
                bindings_file.bindings if bindings_file is not None else None
            )
            validation = validate_project_targets(parsed)
            if not validation.ok:
                raise _ProjectPlanValidationError(validation.errors)
            binding_errors = (
                check_profile_bindings_resolve(parsed.profiles, bindings_file)
                if bindings_file is not None
                else []
            )
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
                "abi_project_plan", {"config": Path(config).name}, elapsed, "timeout"
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": f"abi_project_plan timed out after {mcp_shared.MCP_TIMEOUT}s",
                }
            )
        except _ProjectPlanValidationError as exc:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_project_plan", {"config": Path(config).name}, elapsed, "error"
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        "cannot generate a run-plan from an invalid project "
                        f"config ({len(exc.errors)} error(s)): " + "; ".join(exc.errors)
                    ),
                }
            )
        except _ToolPreflightError as exc:
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_project_plan", {"config": Path(config).name}, elapsed, "error"
            )
            return json.dumps({"status": "error", "error": str(exc)})
        except (click.UsageError, BindingsFileError) as exc:
            redact_args: list[str | Path] = [Path(config), *build_output_dirs]
            if toolchain_bindings is not None:
                redact_args.append(Path(toolchain_bindings))
            # Best-effort: also redact the resolved forms, since some errors
            # embed the resolved path rather than the raw caller-supplied
            # string. One fewer path substituted is not a failure to respond.
            try:
                redact_args.append(_safe_read_path(config, label="config"))
            except ValueError:
                pass
            if toolchain_bindings is not None:
                try:
                    redact_args.append(
                        _safe_read_path(toolchain_bindings, label="toolchain_bindings")
                    )
                except ValueError:
                    pass
            elapsed = _time.monotonic() - t0
            _audit_log(
                "abi_project_plan", {"config": Path(config).name}, elapsed, "error"
            )
            return json.dumps(
                {"status": "error", "error": _redact_paths(str(exc), *redact_args)}
            )

        elapsed = _time.monotonic() - t0
        _audit_log("abi_project_plan", {"config": Path(config).name}, elapsed, "ok")
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

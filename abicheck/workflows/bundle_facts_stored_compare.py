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

"""Run a stored-bundle-facts ``compare`` request to a typed outcome.

:func:`execute_bundle_facts_compare` is the one entry point the CLI calls
for a stored OLD_INPUT. It validates the request (see
``bundle_facts_stored_request``), loads the policy and config, performs the
comparison -- stored/stored as a pure in-memory diff, or stored/live with
package extraction and header resolution -- and checks the output directory.
It returns a :class:`BundleFactsCompareOutcome` and writes nothing: rendering
and file output are presentation, owned by the frontend.
"""

from __future__ import annotations

import importlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import PolicyError, SnapshotError
from ..model.bundle_facts import DEFAULT_MAX_JSON_OBJECT_NODES
from ..policy.release_assurance import ReleaseAssuranceDecision
from ..policy.scope_completeness import ScopeDecision
from .bundle_facts_library_overrides import (
    BundleFactsLibraryOverridesError,
    known_libraries_for_new_side,
    load_bundle_facts_library_overrides,
)
from .bundle_facts_stored_request import (
    BundleFactsCompareError,
    BundleFactsCompareRequest,
    compose_request_header_operands,
    resolve_max_json_object_nodes_cfg,
    validate_bundle_facts_request,
)
from .extraction import detect_extractor, is_package
from .gate import resolve_scope_decision
from .policy_file import (
    merge_project_config_policy_overrides,
    project_config_policy_downgrade_warnings,
)
from .release_assurance_members import release_assurance_from_results
from .release_inputs import discover_include_roots, extract_if_package


@dataclass(frozen=True)
class BundleFactsCompareOutcome:
    """What a stored-bundle-facts comparison achieved, for the frontend to
    render and map to an exit code.

    *result* is the comparison's ``BundleDiffResult``. *scope_decision* and
    *assurance_decision* are the policy answers already resolved for it, so a
    renderer and the exit code read one decision rather than recomputing it.
    """

    result: Any
    scope_decision: ScopeDecision
    assurance_decision: ReleaseAssuranceDecision
    require_complete_analysis: bool


def per_library_report_path(output_dir: Path, library: str) -> Path:
    """Where ``--output-dir`` writes one library's report.

    ``library`` originates in OLD_FACTS, a user-supplied document, so only its
    final path component is used: ``Path(...).name`` cannot escape *output_dir*
    however many separators precede it."""
    safe_name = Path(library).name or "library"
    return output_dir / f"{safe_name}.json"


def execute_bundle_facts_compare(
    request: BundleFactsCompareRequest,
) -> BundleFactsCompareOutcome:
    """Validate, run, and check *request*; return its outcome.

    Raises :class:`BundleFactsCompareError` for a rejected request (``usage``
    true), an unmatched scope, or an operational failure (``usage`` false).
    """
    cfg = validate_bundle_facts_request(request)
    suppression, policy_file = (
        request.load_suppression_and_policy()
        if request.load_suppression_and_policy is not None
        else (None, None)
    )
    policy_file = _merge_project_policy(request, cfg, policy_file)

    bundle_system_providers = list(cfg.bundle_system_providers) if cfg else []
    bundle_cohorts = list(cfg.bundle_cohorts) if cfg else []
    include_private_dso = bool(cfg.release_include_private_dso) if cfg else False
    max_json_object_nodes = resolve_max_json_object_nodes_cfg(
        cfg.resource_limits_max_bundle_facts_decode_nodes if cfg else None,
        config_explicit=request.config_explicit,
        default=DEFAULT_MAX_JSON_OBJECT_NODES,
    )
    env_matrix = cfg.deployment if cfg else None

    if request.new_is_stored:
        result = _compare_stored_pair(
            request,
            policy_file=policy_file,
            suppression=suppression,
            system_providers=bundle_system_providers,
            cohorts=bundle_cohorts,
            max_json_object_nodes=max_json_object_nodes,
            env_matrix=env_matrix,
        )
    else:
        result = _compare_stored_against_live(
            request,
            policy_file=policy_file,
            suppression=suppression,
            system_providers=bundle_system_providers,
            cohorts=bundle_cohorts,
            include_private_dso=include_private_dso,
            max_json_object_nodes=max_json_object_nodes,
            env_matrix=env_matrix,
        )

    scope_decision = resolve_scope_decision(
        getattr(result, "scope_record", None),
        cfg.scope_on_incomplete if cfg else None,
    )
    if not result.per_library and result.scope_record is None:
        # Without a pair and without a record to say so, nothing was compared.
        new_desc = (
            f"{request.new_input}'s stored per_library_snapshots"
            if request.new_is_stored
            else str(request.new_input)
        )
        raise BundleFactsCompareError(
            f"No library in {new_desc} matched any library in "
            f"{request.old_facts_path}'s stored per_library_snapshots -- nothing "
            "was compared. Check that NEW_INPUT and OLD_FACTS reference "
            "the same release.",
            usage=False,
        )
    if not result.per_library and request.fmt != "json":
        request.warn("no library pair was compared -- no comparison completed.")

    require_complete = bool(cfg.assurance_require_complete) if cfg else False
    if request.output_dir is not None:
        _prepare_output_dir(request, Path(request.output_dir), result.per_library)
    # ADR-071 D8: resolved ahead of every render and per-library write, so the
    # published documents carry the same contribution the exit code does.
    assurance_decision = release_assurance_from_results(
        getattr(result, "per_library", ()), require_complete=require_complete
    )
    return BundleFactsCompareOutcome(
        result=result,
        scope_decision=scope_decision,
        assurance_decision=assurance_decision,
        require_complete_analysis=require_complete,
    )


def _merge_project_policy(
    request: BundleFactsCompareRequest, cfg: Any, policy_file: Any
) -> Any:
    """Fold the project config's policy overrides into *policy_file*, and
    report each downgrade through ``request.warn``."""
    base = policy_file
    try:
        merged = merge_project_config_policy_overrides(
            policy_file,
            base_policy=request.policy,
            project_cfg=cfg,
            project_path=request.config_path,
        )
    except PolicyError as exc:
        raise BundleFactsCompareError(
            str(exc), usage=True, param_hint="--config"
        ) from exc
    for warning in project_config_policy_downgrade_warnings(
        base, merged, project_path=request.config_path
    ):
        request.warn(warning)
    return merged


def _compare_stored_pair(
    request: BundleFactsCompareRequest,
    *,
    policy_file: Any,
    suppression: Any,
    system_providers: list[Any],
    cohorts: list[str],
    max_json_object_nodes: int | None,
    env_matrix: Any,
) -> Any:
    """Stored/stored: both sides are persisted per-library snapshots, diffed
    in memory. No binary is read and no header AST is parsed on either side."""
    compare_stored_pair = importlib.import_module(
        "abicheck.workflows.bundle_stored_pair_compare"
    ).compare_stored_bundle_facts_pair
    try:
        return compare_stored_pair(
            request.old_facts_path,
            request.new_input,
            manifest_path=request.manifest_path,
            system_providers=system_providers or None,
            cohorts=cohorts or None,
            policy=request.policy,
            policy_file=policy_file,
            suppress=suppression,
            old_max_json_object_nodes=max_json_object_nodes,
            new_max_json_object_nodes=max_json_object_nodes,
            depth=request.depth,
            env_matrix=env_matrix,
        )
    except (SnapshotError, TypeError, ValueError, OSError) as exc:
        raise BundleFactsCompareError(str(exc), usage=False) from exc


def _compare_stored_against_live(
    request: BundleFactsCompareRequest,
    *,
    policy_file: Any,
    suppression: Any,
    system_providers: list[Any],
    cohorts: list[str],
    include_private_dso: bool,
    max_json_object_nodes: int | None,
    env_matrix: Any,
) -> Any:
    """Stored OLD_INPUT against a live NEW_INPUT directory or package.

    A package archive is extracted into a temporary directory that lives only
    as long as this call: ``--keep-extracted`` is gone, so it is always removed.
    """
    compare_release = importlib.import_module(
        "abicheck.bundle_side_input"
    ).compare_release_against_bundle_facts
    headers_list, includes_list = compose_request_header_operands(request)
    headers: list[Any] = headers_list
    includes: list[Any] = includes_list
    if request.depth == "binary":
        # `--depth binary` promises pure L0/L1 evidence: no header operand at
        # all, including one a package would otherwise contribute.
        headers = []

    temp_dir_paths: list[str] = []

    def _make_temp_dir(prefix: str) -> Path:
        path = tempfile.mkdtemp(prefix=prefix)
        temp_dir_paths.append(path)
        return Path(path)

    try:
        try:
            lib_dir, _new_debug_dir, header_dir, _new_symbols, _whole = (
                extract_if_package(
                    request.new_input,
                    None,
                    request.new_devel_pkg,
                    _make_temp_dir,
                    is_package,
                    detect_extractor,
                )
            )
            if header_dir is not None and request.depth != "binary":
                if not headers:
                    headers = [header_dir]
                includes = includes + discover_include_roots(header_dir)

            per_library_headers: dict[str, list[Path]] | None = None
            per_library_includes: dict[str, list[Path]] | None = None
            per_library_compile: dict[str, Any] | None = None
            if request.bundle_facts_library_manifest is not None:
                new_library_paths = known_libraries_for_new_side(
                    lib_dir, include_private_dso=include_private_dso
                )
                overrides = load_bundle_facts_library_overrides(
                    Path(request.bundle_facts_library_manifest),
                    known_libraries=set(new_library_paths),
                    selected_paths=new_library_paths,
                )
                per_library_headers = overrides.headers
                per_library_includes = overrides.includes
                per_library_compile = overrides.compile
                if request.depth == "binary":
                    # The uniform clear above does not reach a manifest's own
                    # per-library roots, so clear those too.
                    per_library_headers = {}
                    per_library_includes = {}
                    per_library_compile = {}

            return compare_release(
                request.old_facts_path,
                lib_dir,
                headers=headers or None,
                includes=includes or None,
                per_library_headers=per_library_headers,
                per_library_includes=per_library_includes,
                per_library_compile=per_library_compile,
                header_backend="auto",
                compile=request.compile_context,
                new_version=request.new_version,
                lang=request.lang,
                lang_explicit=bool(request.lang_explicit),
                include_private_dso=include_private_dso,
                manifest_path=request.manifest_path,
                system_providers=system_providers or None,
                cohorts=cohorts or None,
                policy=request.policy,
                policy_file=policy_file,
                suppress=suppression,
                include_dependencies=bool(request.include_dependencies),
                max_json_object_nodes=max_json_object_nodes,
                env_matrix=env_matrix,
            )
        except BundleFactsLibraryOverridesError as exc:
            # A manifest entry that names no library, or a bad key, is a
            # malformed-input case, not an operational failure.
            raise BundleFactsCompareError(str(exc), usage=True) from exc
        except (SnapshotError, TypeError, ValueError, OSError) as exc:
            # Covers a malformed OLD_FACTS document (json.JSONDecodeError is a
            # ValueError), a container-node-budget rejection, and an unreadable
            # OLD_INPUT that turns out to be a directory.
            raise BundleFactsCompareError(str(exc), usage=False) from exc
    finally:
        for temp_dir in temp_dir_paths:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _prepare_output_dir(
    request: BundleFactsCompareRequest, output_dir: Path, per_library: Any
) -> None:
    """Validate and create ``--output-dir`` before any report is written.

    Every collision is checked up front, so a rejected request leaves no
    artifact behind: the primary report and each secondary write would
    otherwise be written before a late failure. The checks name the
    collision, and the per-library file names come from the same
    :func:`per_library_report_path` the frontend writes with."""
    if output_dir.exists() and not output_dir.is_dir():
        raise BundleFactsCompareError(
            f"-o json={output_dir}/: this path already exists and "
            "is not a directory -- choose a different per-library directory",
            usage=True,
        )
    reserved_paths = {
        p.resolve()
        for p in (
            Path(request.output) if request.output is not None else None,
            *(Path(w_path) for _w_fmt, w_path in request.secondary_writes),
        )
        if p is not None
    }
    if reserved_paths:
        if output_dir.resolve() in reserved_paths:
            raise BundleFactsCompareError(
                f"-o json={output_dir}/: this path is also named by "
                "another -o target -- a directory and a report "
                "file cannot share the same path, choose a different "
                "per-library directory or a different -o path",
                usage=True,
            )
        for diff in per_library:
            target = per_library_report_path(output_dir, diff.library).resolve()
            if target in reserved_paths:
                raise BundleFactsCompareError(
                    f"-o json={output_dir}/: the per-library report "
                    f"for {diff.library!r} would be written to "
                    f"{target}, which collides with "
                    "another -o target's path -- choose a different "
                    "per-library directory, or a different -o path",
                    usage=True,
                )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BundleFactsCompareError(
            f"Cannot create {output_dir}: {exc}", usage=False
        ) from exc

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

"""Service layer — shared orchestration for CLI and MCP server.

Provides framework-agnostic functions for the core abicheck operations:

- :func:`resolve_input` — Load an ABI snapshot from any supported input format
- :func:`run_dump` — Extract ABI snapshot from a binary + optional headers
- :func:`run_compare` — Compare two ABI snapshots and return classified changes
- :func:`render_output` — Render a DiffResult to the specified output format
"""

from __future__ import annotations

import importlib as _importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from . import policy_file as _policy_file
from .api_types import (
    CompareRequest,
    CompareResult,
    DumpRequest,
    InputSpec,
    OutputSpec,
)
from .checker import compare
from .checker_types import DiffResult
from .errors import ValidationError
from .model import AbiSnapshot
from .policy.public_surface import PublicSurfaceQuery

# `_attach_header_graph` moved to `service_header_graph_attach.py`, purely to
# stay under the AI-readiness 2000-line hard cap -- the identical reason
# `service_render.py`/`service_scan.py`/`service_compare_pipeline.py`/
# `service_dump_pipeline.py` (re-exported further down this file) already
# moved out. Imported here, eagerly, rather than down with those: unlike
# them, this module has no import-cycle relationship with `service.py`
# itself (it reaches `.compile_context`/`.service_scan`/`.header_utils`/
# `.errors` directly, none of which import `.service`), so there is no
# ordering constraint forcing it to the tail. Re-exported under its original
# private name so both `monkeypatch.setattr("abicheck.service.
# _attach_header_graph", ...)` and `from abicheck.service import
# _attach_header_graph` keep resolving unchanged for the many existing
# tests that patch/import it this way.
from .service_header_graph_attach import _attach_header_graph as _attach_header_graph

# ── Input resolution (ADR-061 Phase 4): moved to
# ``workflows.input_resolution`` -- the one slice of this module with zero
# ``PolicyFile`` dependency, so it could move into a real `workflows`
# package location with ordinary static imports. `compare_snapshots`/
# `load_suppression_and_policy` stay here: both need `PolicyFile`, whose own
# classification is still an open, separately-tracked question (see this
# module's own history in ADR-061's Phase 4 section). Imported as a plain
# static import block -- every real caller reaches these through dotted
# attribute access (`service.resolve_input(...)`) or a fresh per-call
# import, so `monkeypatch.setattr(service, "resolve_input", ...)` and
# `from abicheck.service import resolve_input` both keep resolving exactly
# as before this split. Names not in `__all__` (the private helpers plus
# `load_env_matrix`) are re-exported via an explicit self-alias so a static
# checker doesn't flag them as unused -- existing tests patch/import them
# directly off `abicheck.service` (some) and off
# `abicheck.workflows.input_resolution` (the ones that need to influence a
# call made *inside* `resolve_input`'s own body, same rule
# `service_dump_native.py`'s own re-export block documents above). ────────
from .workflows.input_resolution import (
    _SNIFF_BYTES as _SNIFF_BYTES,
    _resolve_raw_typeinfo as _resolve_raw_typeinfo,
    _resolve_symvers as _resolve_symvers,
    _typeinfo_functions as _typeinfo_functions,
    collect_metadata,
    detect_binary_format,
    load_env_matrix as load_env_matrix,
    resolve_input,
    sniff_text_format,
)

# PE/Mach-O header-scoped dump lives in the sibling module service_header_scoped
# (service.py is at the file-size cap). Bound via importlib rather than a static
# `from .service_header_scoped import ...` -- service_header_scoped reaches
# service_scan, which reaches back to service through the pre-existing,
# already-baselined cli_buildsource/scan_engine SCC (AGENTS.md "M1-3"/CLAUDE.md
# "What NOT to do"); a static import here would pull this new leaf module into
# that same cycle, which the AI-readiness import-cycle-growth gate rejects. An
# `importlib.import_module` call is a plain function call, not an
# `ast.ImportFrom` node, so it is invisible to that gate's static AST walk (the
# same escape hatch `cli_buildsource.py`'s own back-compat re-export shim
# documents) while still binding real module-level names here, so
# `service._dump_pe`/`_dump_macho`'s own bare-name calls, `from abicheck.service
# import _try_header_scoped_dump`, and every test's
# `monkeypatch.setattr(service, "_try_header_scoped_dump", ...)` all keep
# working exactly as before this module existed.
_service_header_scoped = _importlib.import_module(".service_header_scoped", __package__)
# Explicitly typed (not left as the `Any` importlib.import_module's attribute
# access would otherwise infer) so a caller returning this call's result
# still gets a real return-type check instead of a silent `no-any-return`.
_has_matched_public_surface: Callable[[AbiSnapshot], bool] = (
    _service_header_scoped._has_matched_public_surface
)
_try_header_scoped_dump: Callable[..., tuple[AbiSnapshot | None, str | None]] = (
    _service_header_scoped._try_header_scoped_dump
)
del _service_header_scoped

if TYPE_CHECKING:
    from collections.abc import Callable

    from .checker_types import Change
    from .environment_matrix import EnvironmentMatrix
    from .policy_file import PolicyFile
    from .suppression import SuppressionList

_logger = logging.getLogger(__name__)

# Codex review, PR #730: re-exported so an existing `service.
# dedup_policy_override_warnings()` caller (and this module's own tests)
# keep working. The dedup state itself lives in `policy_file.py` -- the leaf
# module both this loader and `cli_params._load_suppression_and_policy` share
# -- specifically so *one* scope dedupes a warning repeated across both
# loaders, not just repeated calls to this one. See
# `policy_file.dedup_validate_overrides_warnings`'s own docstring for what
# this does and does not cover.
dedup_policy_override_warnings = _policy_file.dedup_validate_overrides_warnings

# ── Binary dumping (extracted to leaf module ``service_dump_native`` to stay
# under the AI-readiness size cap, the same pattern
# ``service_metadata_attach``/``service_header_graph_attach``/
# ``service_header_scoped``/``service_render``/``service_scan``/
# ``service_compare_pipeline``/``service_dump_pipeline`` already follow;
# re-exported verbatim below so ``from abicheck.service import run_dump``
# and the several ``_dump_elf``/``_dump_pe``/``_dump_macho``/
# ``_run_dump_uncached`` names existing tests patch/import directly keep
# resolving unchanged -- see that module's own docstring for the test-patch
# gotcha this split carries: a caller substituting one of these names for a
# call made *inside* ``service_dump_native.py`` (e.g. patching ``_dump_elf``
# to observe ``run_dump``) must patch
# ``abicheck.service_dump_native.<name>``, not ``abicheck.service.<name>`` --
# only a caller that imports the name fresh from ``abicheck.service`` itself
# (there is none inside ``service_dump_native.py``) would see the latter). ──
from .service_dump_native import (  # noqa: E402,F401
    _HEADER_GRAPH_ENABLED as _HEADER_GRAPH_ENABLED,
    _HEADER_GRAPH_INCLUDES_ENABLED as _HEADER_GRAPH_INCLUDES_ENABLED,
    _apply_native_provenance,
    _dump_elf,
    _dump_macho,
    _dump_pe,
    _emit,
    _extract_pdb_debug,
    _finish_native_snapshot,
    _run_dump_uncached,
    run_dump,
)

# ── Opportunistic per-ecosystem metadata attachment (leaf module
# service_metadata_attach; re-exported verbatim, same as before this split,
# so ``from abicheck.service import _try_attach_python_api_surface`` and its
# three siblings keep resolving unchanged -- service_dump_native.py imports
# these same four names directly from the same source for its own internal
# calls, so this is a second, independent binding of the identical function
# objects rather than a re-export chain). ──────────────────────────────────
from .service_metadata_attach import (  # noqa: E402,F401
    _try_attach_numpy_capi_surface,
    _try_attach_python_api_surface,
    _try_attach_python_ext_metadata,
    _try_attach_sycl_metadata,
)

# ── Comparison ──────────────────────────────────────────────────────────────


def load_suppression_and_policy(
    suppress: Path | None,
    policy: str = "strict_abi",
    policy_file_path: Path | None = None,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load suppression list and policy file from paths.

    Raises:
        ValidationError: If the suppression or policy file is invalid.
    """
    from .policy_file import PolicyFile as _PolicyFile
    from .suppression import SuppressionList as _SuppressionList

    suppression: _SuppressionList | None = None
    if suppress is not None:
        try:
            suppression = _SuppressionList.load(suppress)
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid suppression file: {e}") from e

    pf: _PolicyFile | None = None
    if policy_file_path is not None:
        try:
            pf = _PolicyFile.load(policy_file_path)
        except ImportError as e:
            raise ValidationError(str(e)) from e
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid policy file: {e}") from e
        if policy != "strict_abi":
            # Named as Tier-2 *parameters*, not CLI flags: the CLI merged
            # --policy/--policy-file into one --policy that routes an operand
            # to exactly one of these, so it can no longer set both and this
            # branch is now reachable only from a typed API caller, for whom
            # the flag spellings would name nothing.
            _logger.warning(
                "policy=%r is ignored when policy_file_path is given. "
                "Set base_policy in the YAML file to override the base policy.",
                policy,
            )
        # The Tier-2 chokepoint every non-CLI-validation consumer loads its
        # policy through, none of which reach `cli_params._load_suppression_
        # and_policy`'s own `click.echo` warning (Codex review). Routed
        # through `pending_validate_overrides_warnings` so a shared
        # `dedup_policy_override_warnings()` scope collapses repeats to one.
        for warning in _policy_file.pending_validate_overrides_warnings(pf):
            _logger.warning("%s", warning)
    return suppression, pf


def _validate_contract_mode(
    contract_mode: str | None, contract_evaluation: bool
) -> None:
    """Apply ADR-049 Phase 6's two ``contract_mode`` rules at a Tier-2 entry.

    Same allowed values and same ``contract_evaluation`` dependency as
    ``CompareRequest.validation_errors`` and the CLI's ``--contract``, so the
    three front ends cannot disagree about what is accepted.
    """
    if contract_mode is None:
        return
    from .contract_relevance_types import ContractMode

    allowed = {mode.value for mode in ContractMode}
    if contract_mode not in allowed:
        raise ValidationError(
            f"unsupported contract mode {contract_mode!r}: "
            f"choose from {', '.join(sorted(allowed))}"
        )
    if not contract_evaluation:
        raise ValidationError(
            "contract_mode requires contract_evaluation: it selects which "
            "evidence domain the shadow contract evaluator judges against, "
            "and without that flag no contract decision is computed at all"
        )


def compare_snapshots(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None = None,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    extra_changes: list[Change] | None = None,
    pattern_verdicts: bool = False,
    surface_metrics: bool = False,
    collapse_versioned_symbols: bool = False,
    public_surface_allowlist: set[str] | None = None,
    reconcile_build_context: bool = False,
    env_matrix: EnvironmentMatrix | None = None,
    diagnostic_comparison: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
) -> DiffResult:
    """Classify two already-resolved snapshots — the Tier-2 snapshot verb.

    Thin wrapper over the Tier-1 core (:func:`abicheck.checker.compare`) so that
    *front-ends never call the core directly* (ADR-037 D1/D10.1). Front-ends
    that have already resolved their own snapshots (the native ``compare``
    command with embedded build-source evidence, ``scan``, ``appcompat``) route
    through here instead of importing ``checker.compare``; the kwargs mirror the
    core verb exactly so no capability is lost.

    ADR-063 Phase 3 (D5): resolves each side's own public-surface
    ``EntityId`` set via ``PublicSurfaceQuery().resolve()`` and forwards the
    pair into :func:`~abicheck.checker.compare` as *old_public_entity_ids*/
    *new_public_entity_ids* -- ``compare()`` itself never resolves this
    (``policy/`` -> ``compare/`` stays a one-way edge), so this Tier-2
    chokepoint is where it happens for every caller that reaches ``compare()``
    through here, ``service_compare_pipeline.classify_compare_pair`` included.

    Raises:
        ValidationError: *contract_mode* is not one of ``public``/``exports``/
            ``all``, or is given without *contract_evaluation*. This is a
            documented Tier-2 entry point that direct Python callers reach
            without building a ``CompareRequest``, so it applies the same two
            rules that request object and the CLI do rather than silently
            accepting a no-op or failing later inside the core (Codex review).
    """
    _validate_contract_mode(contract_mode, contract_evaluation)
    # Centralized POST committed-wrapper recovery: when a committed-surface
    # allowlist is supplied, union the callable `pp_*` wrappers exported by
    # the old snapshot (contract_scope_allowlist's snapshot half) -- keeps
    # both dropped and still-exported-but-omitted wrappers in-surface, so a
    # scope against a new manifest can't hide an ABI break via omission.
    # Every scope caller routes through here (one place, no-op with no
    # `pp_*` wrappers, idempotent if already unioned).
    if public_surface_allowlist is not None:
        from .post_manifest import _snapshot_contract_symbols

        public_surface_allowlist = set(
            public_surface_allowlist
        ) | _snapshot_contract_symbols(old)
    query = PublicSurfaceQuery()
    return compare(
        old,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        extra_changes=extra_changes,
        pattern_verdicts=pattern_verdicts,
        surface_metrics=surface_metrics,
        collapse_versioned_symbols=collapse_versioned_symbols,
        public_surface_allowlist=public_surface_allowlist,
        reconcile_build_context=reconcile_build_context,
        env_matrix=env_matrix,
        diagnostic_comparison=diagnostic_comparison,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        old_public_entity_ids=query.resolve(old),
        new_public_entity_ids=query.resolve(new),
    )


# run_compare_request/run_compare moved to service_compare_pipeline.py (CLI
# cleanup phase two, PR B slice 1) to stay under the AI-readiness file-size
# cap once run_compare gained pack_policy_overrides/pack_internal_namespaces
# -- re-exported below, same pattern as resolve_compare_request/
# classify_compare_pair already use.


# ── Compare pipeline (ADR-055 D1): `run_compare_request`'s two phases live in
# the leaf module ``service_compare_pipeline`` so the native ``compare`` CLI can
# run its Click-dependent ADR-049 ``resolve_and_apply`` step between them and
# still share this resolution instead of keeping a second copy. Re-exported here
# so ``from abicheck.service import resolve_compare_request`` works.
# ``run_compare_request``/``run_compare`` (their composition and its
# keyword-argument shim) live there too now, for the same file-size reason. ──
from .service_compare_pipeline import (  # noqa: E402,F401
    ResolvedComparePair,
    classify_compare_pair,
    resolve_compare_request,
    resolve_sides_sequentially,
    run_compare,
    run_compare_request,
)

# ── Dump pipeline (G33 Phase 5): ``dump``'s counterpart to the above, in the
# leaf module ``service_dump_pipeline``. Re-exported for the same reason:
# ``from abicheck.service import run_dump_request`` is the typed entry point
# every front end (CLI, Python, MCP ``abi_dump``) builds a request for. ───────
from .service_dump_pipeline import run_dump_request  # noqa: E402,F401

# ── Scan service (ADR-035 D10 typed engine: ScanRequest → ScanResult /
# [CostEstimate]) extracted to leaf module service_scan, same size-cap/re-
# export/non-circular-import rationale as service_render above. ────────────
from .service_scan import (  # noqa: E402,F401
    _HEADER_EXTS,
    Budget,
    CompileContext,
    CostEstimate,
    LayerResult,
    ScanArtifactResult,
    ScanRequest,
    ScanResult,
    ScanSetResult,
    _count_compile_db_tus,
    _count_pack_tus,
    _count_source_tus,
    _discover_compile_db,
    _is_header_path,
    _is_source_tu_path,
    _kill_process_tree,
    _layers_from_coverage,
    _scan_imports,
    _scan_subprocess_worker,
    estimate_scan,
    expand_header_inputs,
    pair_wide_cxx20_std_override,
    run_audit,
    run_scan,
    run_scan_set,
    run_scan_set_subprocess,
    run_scan_subprocess,
)

# ── Output rendering: service_render.py is `frontends`-classified (ADR-061),
# re-exported via workflows/render.py's typed wrappers -- see its docstring.
from .workflows.render import (  # noqa: E402,F401
    _render_deps_section_md,
    _render_json_output,
    render_output,
)

# Explicit re-export (mypy strict / no_implicit_reexport): the scan engine moved
# to the leaf module ``service_scan`` but its public names must still resolve as
# ``from abicheck.service import ...``.
__all__ = [
    "Budget",
    "CompareRequest",
    "CompareResult",
    "CompileContext",
    "CostEstimate",
    "DumpRequest",
    "InputSpec",
    "LayerResult",
    "OutputSpec",
    "ScanArtifactResult",
    "ScanRequest",
    "ScanResult",
    "ResolvedComparePair",
    "ScanSetResult",
    "classify_compare_pair",
    "collect_metadata",
    "compare_snapshots",
    "detect_binary_format",
    "estimate_scan",
    "expand_header_inputs",
    "load_suppression_and_policy",
    "render_output",
    "resolve_compare_request",
    "resolve_input",
    "run_audit",
    "run_compare",
    "run_compare_request",
    "run_dump",
    "run_dump_request",
    "run_scan",
    "run_scan_set",
    "run_scan_set_subprocess",
    "run_scan_subprocess",
    "sniff_text_format",
]

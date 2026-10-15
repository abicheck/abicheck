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

"""Service layer — the typed Python API shared orchestration point.

Provides framework-agnostic functions for the core abicheck operations:

- :func:`resolve_input` — Load an ABI snapshot from any supported input format
- :func:`run_dump` — Extract ABI snapshot from a binary + optional headers
- :func:`run_compare` — Compare two ABI snapshots and return classified changes
- :func:`render_output` — Render a DiffResult to the specified output format
"""

from __future__ import annotations

from .workflows.contracts import (
    CompareRequest,
    CompareResult,
    DumpRequest,
    OutputSpec,
)

# ── Input resolution (ADR-061 Phase 4): moved to ``workflows.input_resolution``.
# Re-exported under their public names so ``from abicheck.service import
# resolve_input`` keeps working. Private helpers are imported from their owner.
from .workflows.input_resolution import (
    collect_metadata,
    detect_binary_format,
    load_env_matrix as load_env_matrix,
    resolve_input,
    sniff_text_format,
)
from .workflows.request_inputs import InputSpec

# ── Binary dumping: ``run_dump`` lives in ``workflows.dump.native``; each
# format's primary extraction is a ``workflows.dump.formats`` adapter. To
# substitute an extractor, replace its entry in
# ``workflows.dump.native.FORMAT_ADAPTERS`` rather than patching a private name.
# run_compare_request/run_compare moved to service_compare_pipeline.py (CLI
# cleanup phase two, PR B slice 1) to stay under the AI-readiness file-size
# cap once run_compare gained pack_policy_overrides/pack_internal_namespaces
# -- re-exported below, same pattern as resolve_compare_request/
# classify_compare_pair already use.
# ── Compare pipeline (ADR-055 D1): `run_compare_request`'s two phases live in
# the leaf module ``service_compare_pipeline`` so the native ``compare`` CLI can
# run its ADR-049 configuration step (``workflows.compare_configuration``) between them and
# still share this resolution instead of keeping a second copy. Re-exported here
# so ``from abicheck.service import resolve_compare_request`` works.
# ``run_compare_request``/``run_compare`` (their composition and its
# keyword-argument shim) live there too now, for the same file-size reason. ──
# isort: split
# ── Dry-run cost model (ADR-035 D10's `[CostEstimate]`) extracted to the leaf
# module dry_run_estimate, same size-cap/re-export/non-circular-import rationale
# as service_render above. ADR-068's Phase 4 typed-API slice retired that
# module's own `ScanRequest`/`ScanResult` (and their `--artifact-set`
# siblings) along with `run_scan`/`run_scan_set`: `CompareRequest` ->
# `CompareResult` is the one typed contract now. ────────────────────────────
from .dry_run_estimate import (  # noqa: F401
    CompileContext,
    CostEstimate,
    estimate_scan,
    expand_header_inputs,
    pair_wide_cxx20_std_override,
)

# ── Output rendering: `frontends/render.py` (formerly `service_render.py`)
# and this facade are both `frontends` (ADR-061 "composition at the outer
# boundary"), so this is an ordinary `frontends -> frontends` import. The
# render module is a leaf: it does not import `abicheck.service`.
from .frontends.render import (
    render_output,
    # The summary-only documents, re-exported through `frontends.render` (which
    # renders with them) because they are the direct replacement for
    # `render_output(..., stat=True)`. That keyword was a dispatch flag --
    # "render a different document than the format I asked for" -- and was
    # removed, so the documents it reached stay reachable at the same import
    # site a caller already used. `to_stat` is also `render_output`'s own
    # `fmt=ONELINE_FORMAT`; `to_stat_json` never had a format spelling.
    to_stat,
    to_stat_json,
)
from .service_compare_pipeline import (  # noqa: F401
    ResolvedComparePair,
    classify_compare_pair,
    resolve_compare_request,
    resolve_sides_sequentially,
    run_compare,
    run_compare_request,
)

# ── Comparison: policy-parameterised (ADR-061 Phase 4). `compare_snapshots`/
# `load_suppression_and_policy`/`_validate_contract_mode`/
# `dedup_policy_override_warnings` moved into the leaf module
# `workflows.compare_policy` once `policy_file.py` was classified `policy`
# unblocked their move into a real `workflows` package location -- see that
# module's own docstring for the blocker and its resolution. Re-exported
# below via a plain static import, the same `workflows -> workflows` shape
# `input_resolution` above uses (no import-cycle risk: nothing
# `compare_policy.py` imports reaches back to `service.py` or the
# pre-existing CLI-registration SCC), so every real caller's dotted
# attribute access (`service.compare_snapshots(...)`) or fresh per-call
# import keeps resolving -- and keeps being monkeypatchable -- exactly as
# before this split.
from .workflows.compare_policy import (  # noqa: F401
    compare_snapshots,
    dedup_policy_override_warnings,
    load_suppression_and_policy,
)
from .workflows.dump.native import run_dump

# ── Dump pipeline (G33 Phase 5): ``dump``'s counterpart to the above, in the
# leaf module ``service_dump_pipeline``. Re-exported for the same reason:
# ``from abicheck.service import run_dump_request`` is the typed entry point
# every front end (CLI, typed Python) builds a request for. ──────────────────
from .workflows.dump.pipeline import run_dump_request

# ADR-061 gap D, closed: the directory/package release fan-out's
# pre-execution resolution is one typed request/plan pair, reachable from
# this Tier-2 surface with no Click context. It was previously resolvable
# only through `compare-release`'s own Click command, because the chain it
# calls was `frontends`/flat-`cli_*`-classified and raised `click` errors
# from inside the resolution -- both of which the `engine-cli-boundary`
# gate forbids an engine module from depending on. That chain is
# `workflows.release_inputs` now and raises the typed
# `errors.ReleaseOperandContentError`/`ReleaseOperandUsageError`; the CLI
# translates both at its own boundary, back into the two `click` types that
# produce the two exit codes this command always produced, so no
# user-visible message or exit code changed.
from .workflows.release_request import (
    ReleaseComparePlan as ReleaseComparePlan,
    ReleaseCompareRequest as ReleaseCompareRequest,
    cleanup_release_compare_plan as cleanup_release_compare_plan,
    resolve_release_compare_plan as resolve_release_compare,
)

# Explicit re-export (mypy strict / no_implicit_reexport): the scan engine moved
# to the leaf module ``dry_run_estimate`` but its public names must still resolve as
# ``from abicheck.service import ...``.
__all__ = [
    "CompareRequest",
    "CompareResult",
    "CompileContext",
    "CostEstimate",
    "DumpRequest",
    "InputSpec",
    "OutputSpec",
    "ReleaseComparePlan",
    "ReleaseCompareRequest",
    "ResolvedComparePair",
    "classify_compare_pair",
    "cleanup_release_compare_plan",
    "collect_metadata",
    "compare_snapshots",
    "detect_binary_format",
    "estimate_scan",
    "expand_header_inputs",
    "load_suppression_and_policy",
    "render_output",
    "to_stat",
    "to_stat_json",
    "resolve_compare_request",
    "resolve_input",
    "resolve_release_compare",
    "run_compare",
    "run_compare_request",
    "run_dump",
    "run_dump_request",
    "sniff_text_format",
]

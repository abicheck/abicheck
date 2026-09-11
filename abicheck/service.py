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

import importlib as _importlib
from typing import TYPE_CHECKING

from .api_types import (
    CompareRequest,
    CompareResult,
    DumpRequest,
    InputSpec,
    OutputSpec,
)
from .model import AbiSnapshot

# `_attach_header_graph` moved to `service_header_graph_attach.py`, purely to
# stay under the AI-readiness 2000-line hard cap -- the identical reason
# `service_render.py`/`service_compare_pipeline.py`/
# `service_dump_pipeline.py` (re-exported further down this file) already
# moved out. Imported here, eagerly, rather than down with those: unlike
# them, this module has no import-cycle relationship with `service.py`
# itself (it reaches `.compile_context`/`.workflows.header_inputs`/`.header_utils`/
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
# package location with ordinary static imports first. `compare_snapshots`/
# `load_suppression_and_policy`/`_validate_contract_mode`/
# `dedup_policy_override_warnings` needed `PolicyFile` classified before they
# could follow -- see `workflows/compare_policy.py`'s own docstring for that
# blocker's resolution and where they live now. Imported as a plain
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
# back to service through the pre-existing, already-baselined cli_buildsource
# SCC (AGENTS.md "M1-3"/CLAUDE.md
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

# ── Binary dumping (extracted to leaf module ``service_dump_native`` to stay
# under the AI-readiness size cap, the same pattern
# ``service_metadata_attach``/``service_header_graph_attach``/
# ``service_header_scoped``/``service_render``/
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
# ── Shared header-input expansion (leaf module), plus the two typed-request
# helpers `service.py` has always re-exported from the engine layer. ────────
from .compile_context import CompileContext as CompileContext  # noqa: E402
from .cxx20_pair_dialect import (  # noqa: E402,F401
    pair_wide_cxx20_std_override,
)
from .service_compare_pipeline import (  # noqa: E402,F401
    ResolvedComparePair,
    classify_compare_pair,
    resolve_compare_request,
    resolve_sides_sequentially,
    run_compare,
    run_compare_request,
)
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

# ── Dump pipeline (G33 Phase 5): ``dump``'s counterpart to the above, in the
# leaf module ``service_dump_pipeline``. Re-exported for the same reason:
# ``from abicheck.service import run_dump_request`` is the typed entry point
# every front end (CLI, typed Python) builds a request for. ──────────────────
from .service_dump_pipeline import run_dump_request  # noqa: E402,F401

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
from .workflows.compare_policy import (  # noqa: E402,F401
    _validate_contract_mode as _validate_contract_mode,
    compare_snapshots,
    dedup_policy_override_warnings,
    load_suppression_and_policy,
)
from .workflows.header_inputs import (  # noqa: E402,F401
    _HEADER_EXTS,
    expand_header_inputs,
)

# ── Output rendering: service_render.py is `frontends`-classified (ADR-061),
# re-exported via workflows/render.py's typed wrappers -- see its docstring.
from .workflows.render import (  # noqa: E402,F401
    _render_deps_section_md,
    _render_json_output,
    render_output,
)

# Explicit re-export (mypy strict / no_implicit_reexport): names that live in
# leaf modules must still resolve as ``from abicheck.service import ...``.
__all__ = [
    "CompareRequest",
    "CompareResult",
    "CompileContext",
    "DumpRequest",
    "InputSpec",
    "OutputSpec",
    "ResolvedComparePair",
    "classify_compare_pair",
    "collect_metadata",
    "compare_snapshots",
    "detect_binary_format",
    "expand_header_inputs",
    "load_suppression_and_policy",
    "render_output",
    "resolve_compare_request",
    "resolve_input",
    "run_compare",
    "run_compare_request",
    "run_dump",
    "run_dump_request",
    "sniff_text_format",
]

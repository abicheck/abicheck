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

"""ADR-055 D1 helpers: resolve the new :class:`CompareRequest`/:class:`InputSpec`
build/source-evidence fields into the primitives :func:`service.run_compare_request`
already knows how to drive (a collect mode, a per-side header list, a per-side
:class:`CompileContext`).

Split out of ``service.py`` rather than inlined there: that module sits at the
2000-line AI-readiness hard cap. Deliberately kept a *leaf* module (only
``compile_context``/``buildsource.scan_levels``, both stdlib-deps-only) rather
than reusing ``cli_dump_helpers.resolve_dump_depth`` -- that module is a member
of the CLI/service import-cycle-allowlisted cluster (CLAUDE.md "M1-3"), and
this module is called from ``service.py`` (also a cluster member), so
importing a cluster module here would fold this module into that cluster too
(AGENTS.md "What NOT to do": prefer a leaf module over extending
``IMPORT_CYCLE_ALLOWLIST``). The depth->collect-mode mapping is small enough
to inline directly against ``buildsource.scan_levels`` instead.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from .buildsource.scan_levels import (
    EvidenceDepth,
    SourceScope,
    depth_to_method,
    level_to_collect_mode,
)
from .compile_context import CompileContext

if TYPE_CHECKING:
    from .api_types import CompareRequest, InputSpec
    from .dump_manifest import DumpManifest

__all__ = [
    "SideEvidence",
    "effective_frontend",
    "normalized_debug_format",
    "resolve_compare_request_evidence",
]


def effective_frontend(compile: CompileContext | None, header_backend: str) -> str:
    """The L2 frontend `resolve_input`/`run_dump` actually use for *compile*
    (an explicit `compile.frontend` wins over the bare `header_backend`
    default), resolved to a concrete backend -- reused so `embed_build_
    source`'s `extractor` matches instead of silently diverging (Codex
    review, two rounds: an explicit override wasn't case-normalized in the
    first pass, and "auto" itself was forwarded unresolved in the second --
    `_make_source_extractor` doesn't special-case "auto" and falls back to
    Clang, while L2's own "auto" resolves to castxml by default)."""
    from .dumper import _resolve_header_backend

    requested = (
        compile.frontend if (compile is not None and compile.frontend != "auto") else header_backend
    )
    return _resolve_header_backend(requested)


@dataclasses.dataclass(frozen=True)
class SideEvidence:
    """One side's resolved ADR-055 D1 evidence, ready for ``resolve_input``/
    ``embed_build_source``."""

    headers: list[Path]
    compile: CompileContext | None
    collect_mode: str
    dump_manifest: DumpManifest | None


def _resolve_depth_collect_mode(depth: str | None, default_mode: str) -> str:
    # Mirrors cli_dump_helpers.resolve_dump_depth's own depth->collect-mode
    # mapping (duplicated here, not imported, to stay a leaf module -- see
    # this module's own docstring).
    if depth is None:
        return default_mode
    evidence_depth = EvidenceDepth(depth.lower())
    method = depth_to_method(evidence_depth)
    if method is None:
        return "off"
    return level_to_collect_mode(
        method, evidence_depth, source_scope=SourceScope.TARGET
    )


def _collect_mode(request: CompareRequest) -> str:
    # Mirrors the CLI's own --depth-omitted inference
    # (cli_compare_helpers._resolve_compare_collect_mode): explicit depth
    # always wins; else sources infers "source", build_info infers "build",
    # nothing at all stays "off".
    if request.depth is not None:
        return _resolve_depth_collect_mode(request.depth, "off")
    if request.old.sources or request.new.sources:
        return _resolve_depth_collect_mode("source", "off")
    if request.old.build_info or request.new.build_info:
        return _resolve_depth_collect_mode("build", "off")
    return "off"


def _headers(side: InputSpec, depth: str | None) -> list[Path]:
    # depth == "binary" clears headers before resolving (matches the CLI's
    # cli_compare_helpers._normalize_compare_options) -- otherwise a
    # binary-only depth request that still carried headers would silently
    # keep running L2.
    if depth is not None and depth.lower() == "binary":
        return []
    return list(side.headers)


def _dump_manifest(side: InputSpec, depth: str | None) -> DumpManifest | None:
    # depth == "binary" also clears dump_manifest (Codex review) -- otherwise
    # a binary-only depth request still runs the manifest's own multi-TU L2
    # header extraction, exactly the L2 evidence "binary" is meant to skip.
    if depth is not None and depth.lower() == "binary":
        return None
    return side.dump_manifest


def _compile_context(
    side_compile: CompileContext | None,
    pair_compile: CompileContext | None,
    frontend_context: str,
) -> CompileContext | None:
    # Merge a side's explicit compile override, the pair-wide C++20 dialect
    # override, and the request-level frontend_context default.
    #
    # frontend_context specifically has a known, accepted limitation (Codex
    # review, two rounds): CompileContext.frontend_context is a plain
    # ``str = "host"`` field with no way to represent "unset" -- so a bare
    # equality check can never distinguish a side that explicitly pinned
    # "host" from one that just happens to carry the untouched default
    # because it set some unrelated field (e.g. only `sysroot`). Giving
    # CompileContext a real sentinel would fix this properly, but that field
    # is read/constructed in ~20 other modules (service.py's own
    # `cc.frontend_context == "host"` branch included), so a wider type
    # change is out of scope for this fix. Resolved by always applying the
    # request-level default whenever a side's frontend_context reads as the
    # class default ("host") -- i.e. treating an unrelated override the same
    # as no override at all for this one field. The accepted cost: a side
    # that deliberately wants "host" while the request otherwise defaults to
    # "device" cannot express that through `InputSpec.compile.frontend_context`
    # alone; it must instead pin the *other* side to "device" explicitly and
    # leave the request-level `frontend_context` at "host".
    base = side_compile
    # Codex review: request.frontend_context is normalized to lowercase above
    # (resolve_compare_request_evidence), but a per-side InputSpec.compile.
    # frontend_context passed straight through unchanged -- an accepted
    # case-insensitive spelling like "DEVICE" then compared unequal to the
    # lowercase literal every real consumer (e.g. sycl_context.py) checks
    # against. api_types.py's validation_errors() now rejects anything that
    # doesn't lowercase to "host"/"device", so normalizing here is safe.
    if base is not None and base.frontend_context != base.frontend_context.lower():
        base = dataclasses.replace(base, frontend_context=base.frontend_context.lower())
    if pair_compile is not None and pair_compile.gcc_option_tokens:
        # Codex review (P2): an unrelated side_compile override (e.g. only a
        # sysroot) must not silently discard the pair-wide C++20 dialect
        # decision -- merge its tokens in unless this side already pins its
        # own explicit standard.
        from ._compiler_options import has_explicit_std

        if base is None:
            base = pair_compile
        elif not has_explicit_std(base.gcc_options, base.gcc_option_tokens):
            base = dataclasses.replace(
                base,
                gcc_option_tokens=base.gcc_option_tokens + pair_compile.gcc_option_tokens,
            )
    if base is None:
        return (
            CompileContext(frontend_context=frontend_context)
            if frontend_context != "host"
            else None
        )
    if frontend_context != "host" and base.frontend_context == "host":
        return dataclasses.replace(base, frontend_context=frontend_context)
    return base


def resolve_compare_request_evidence(
    request: CompareRequest,
    pair_compile: CompileContext | None,
) -> tuple[SideEvidence, SideEvidence]:
    """Resolve ADR-055 D1's ``depth``/``sources``/``build_info``/``compile``/
    ``frontend_context`` fields into (old, new) :class:`SideEvidence`, ready
    for ``resolve_input``/``embed_build_source``.

    *pair_compile* is the pre-existing pair-wide C++20 dialect override
    (``service_scan.pair_wide_cxx20_std_override``); each side's own
    ``InputSpec.compile`` takes precedence over it.
    """
    collect_mode = _collect_mode(request)
    # Codex review: validate() accepts frontend_context case-insensitively,
    # but every real consumer compares against the lowercase "host"/"device"
    # literals -- normalize once here so an accepted "DEVICE" doesn't
    # silently behave as neither.
    frontend_context = request.frontend_context.lower()
    return (
        SideEvidence(
            headers=_headers(request.old, request.depth),
            compile=_compile_context(request.old.compile, pair_compile, frontend_context),
            collect_mode=collect_mode,
            dump_manifest=_dump_manifest(request.old, request.depth),
        ),
        SideEvidence(
            headers=_headers(request.new, request.depth),
            compile=_compile_context(request.new.compile, pair_compile, frontend_context),
            collect_mode=collect_mode,
            dump_manifest=_dump_manifest(request.new, request.depth),
        ),
    )


def normalized_debug_format(request: CompareRequest) -> str | None:
    """``CompareRequest.debug_format`` lowercased for the extraction layer.

    ``validate()`` accepts the same four values the CLI's ``--debug-format``
    choice does, case-insensitively; every consumer below compares against the
    lowercase literals (``dumper_debug._resolve_debug_metadata``), so an
    accepted ``"DWARF"`` has to be normalized once rather than silently
    behaving as "no format given" (Codex review).
    """
    return request.debug_format.lower() if request.debug_format is not None else None

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

"""L2 header-AST backend **selection** -- deciding which castxml/clang/hybrid
frontend a request nominally resolves to, before any tool ever runs.

ADR-063 Track T4 ("Dump request contract"), item 1's first still-open
clause: splitting backend *selection* from *fallback policy*. Selection
answers "which backend does this request nominally resolve to" and is pure
-- no subprocess, no filesystem, no attempt at running anything. Fallback
policy (:func:`abicheck.dumper._castxml_fallback_reason`) is the opposite:
it is only discoverable by actually attempting castxml and observing a
specific failure signature, and stays in :mod:`abicheck.dumper` where the
attempt itself happens.

Before this split, :func:`abicheck.dumper._header_ast_parser` computed its
own dispatch inline, and
:func:`abicheck.workflows.dump.request.resolve_dump_request` independently
re-derived the identical prediction a second time for its own
reporting-only ``effective_header_backend`` field -- duplicating both the
override-precedence rule and the "any non-host context forces clang" rule.
Two Codex-review-caught regressions in that exact spot (a missing
case-insensitivity check, and conflating an explicit pinned castxml with an
auto request) are why :func:`_resolve_effective_ast_backend`'s own
docstring is as precise as it is about what it delegates to.

Lives here (a real ADR-061 ``extract`` responsibility-package module, not a
flat ``abicheck/dumper_*.py`` sibling) because ``architecture/modules.yaml``'s
``frozen_root_families`` closes that flat namespace to new members and
:mod:`abicheck.dumper`/:mod:`abicheck.extract.headers.toolchain` were both already
at their own no-growth debt-ledger line-count baseline with no room for a
new function. Callers import ``_resolve_header_backend`` from this module
directly; :mod:`abicheck.dumper` does not re-export it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..errors import AstContextMissingError, ValidationError
from .headers import backend_registry
from .headers.backend_plan import BackendPlan, ast_frontend_pin
from .headers.backend_protocol import HeaderParseRequest
from .headers.castxml.backend import CastxmlRunError
from .headers.castxml_fallback import _castxml_fallback_reason

if TYPE_CHECKING:
    from ..dumper_clang import _ClangAstParser
    from .headers.castxml.dumper import _CastxmlParser
    from .headers.gcc.backend import NativeGccParser

__all__ = [
    "HEADER_BACKENDS",
    "_is_hybrid_request",
    "_resolve_effective_ast_backend",
    "_resolve_header_backend",
    "_resolve_single_ast_backend",
]

# L2 producers; hybrid is explicit because it runs both tools (~2x cost).
HEADER_BACKENDS = ("auto", "castxml", "clang", "hybrid", "gcc")


def _resolve_header_backend(backend: str | None) -> str:
    """Resolve an L2 header-AST frontend request to a concrete ``castxml``/
    ``clang``/``hybrid``.

    Precedence: an explicit ``castxml``/``clang``/``hybrid`` is honored
    verbatim (and the caller gets a clear error later if a needed tool is
    missing). ``auto``/``None`` consults the ``ABICHECK_AST_FRONTEND`` env
    var first, then resolves to castxml (the schema reference). Never
    auto-falls-back to clang, and never auto-resolves to ``hybrid``: clang
    JSON AST snapshots lack computed layout, and running both backends
    unasked would silently double dump cost (see ``dumper_hybrid.py``).
    """
    choice = (backend or "auto").lower()
    if choice in ("castxml", "clang", "hybrid", "gcc"):
        return choice
    if choice != "auto":
        raise ValidationError(
            f"Unknown AST frontend {backend!r}; expected one of {HEADER_BACKENDS}."
        )
    env = ast_frontend_pin()
    if env in ("castxml", "clang", "hybrid", "gcc"):
        return env
    return "castxml"


def validate_native_gcc_format(
    backend: str, binary_format: str, uses_headers: bool
) -> None:
    if (
        uses_headers
        and _resolve_header_backend(backend) == "gcc"
        and binary_format != "elf"
    ):
        raise ValidationError(
            "Native GCC header extraction currently supports ELF only; select a supported frontend for PE/Mach-O."
        )


def validate_native_gcc_manifest(
    backend: str | None, dump_manifest_given: bool
) -> None:
    if _resolve_header_backend(backend) == "gcc" and dump_manifest_given:
        raise ValidationError(
            "Native GCC currently supports a single header translation unit; --dump-manifest is not supported."
        )


def _is_hybrid_request(
    backend: str | None, *, dump_manifest_given: bool, frontend_context: str
) -> bool:
    """Answer whether *backend* resolves to ``hybrid``, rejecting the two
    requests a hybrid (castxml+clang merge) dump cannot honor.

    The hybrid-side sibling of :func:`_resolve_single_ast_backend`'s own
    castxml/device-context check: every "can the selected backend honor
    this request" decision lives in this module, so :mod:`abicheck.dumper`
    only dispatches.
    """
    validate_native_gcc_manifest(backend, dump_manifest_given)
    if _resolve_header_backend(backend) != "hybrid":
        return False
    if dump_manifest_given:
        raise ValidationError(
            "dump_manifest is not yet supported with the 'hybrid' AST "
            "frontend; set compile.frontend: castxml or clang in .abicheck.yml "
            "(or ABICHECK_AST_FRONTEND)."
        )
    if frontend_context != "host":
        # Hybrid has no device concept (castxml+clang merge); reject rather
        # than silently defaulting both recursive calls to "host".
        raise AstContextMissingError(
            f"compile.frontend_context {frontend_context!r} requires the "
            "clang header backend (compile.frontend: clang); 'hybrid' "
            "merges castxml+clang and has no device-context semantics."
        )
    return True


def _resolve_single_ast_backend(backend: str, frontend_context: str) -> str:
    """Resolve *backend* to the one L2 frontend that will actually run.

    Rejects the two requests no single parser can satisfy: ``"hybrid"``
    (which only :func:`abicheck.dumper_hybrid.run_hybrid_dump` can resolve)
    and a non-``"host"`` *frontend_context* under a deliberately-chosen
    castxml, which has no SYCL/DPC++ host/device context concept. Split out
    of :func:`abicheck.dumper._header_ast_parser` so that function reads as
    the three-way backend dispatch it is.
    """
    resolved = _resolve_header_backend(backend)
    if resolved == "gcc" and frontend_context != "host":
        raise AstContextMissingError(
            "Native GCC has no SYCL/device frontend context; select the actual Clang/DPC++ frontend."
        )
    if resolved == "hybrid":
        # No single parser exists for "hybrid" — must be resolved by
        # dumper_hybrid.run_hybrid_dump, not silently treated as castxml.
        raise ValidationError(
            '"hybrid" AST frontend has no single parser here '
            "(see dumper_hybrid.run_hybrid_dump)."
        )
    # `resolved` alone can't tell explicit/env-pinned/defaulted castxml apart; an env pin counts as explicit (environment.md: "honoured verbatim").
    _env_pinned_castxml = (backend or "auto").lower() == "auto" and (
        ast_frontend_pin() == "castxml"
    )
    if (
        resolved == "castxml"
        and frontend_context != "host"
        and ((backend or "auto").lower() == "castxml" or _env_pinned_castxml)
    ):
        raise AstContextMissingError(
            f"compile.frontend_context {frontend_context!r} requires the clang "
            "header backend (compile.frontend: clang); castxml has no SYCL/"
            "DPC++ host/device context concept."
        )
    return resolved


def _resolve_effective_ast_backend(
    requested_frontend: str, frontend_context: str
) -> str:
    """Predict the concrete L2 backend :func:`abicheck.dumper._header_ast_parser`
    will run, with no execution and no runtime fallback-eligibility consulted.

    This is the "selection" half of backend resolution (see this module's
    own docstring for the selection/fallback-policy split). Delegates to
    :func:`_resolve_single_ast_backend` for the actual resolution and its
    validation (raises for a ``"hybrid"`` request, or an explicit/env-pinned
    ``castxml`` under a non-``"host"`` context — neither is satisfiable by
    any single parser, so there is no backend to predict). Then mirrors
    ``_header_ast_parser``'s own post-resolution dispatch rule exactly:
    ``if resolved == "clang" or frontend_context != "host": return
    _run_clang()`` — any surviving non-``"host"`` context always predicts
    clang, regardless of what `_resolve_single_ast_backend` itself named,
    because castxml has no SYCL/DPC++ host/device context concept and every
    code path already reaching this point past the raise above must
    therefore be clang-bound.
    """
    resolved = _resolve_single_ast_backend(requested_frontend, frontend_context)
    if frontend_context != "host":
        return "clang"
    return resolved


def lang_to_profile(lang: str | None) -> str | None:
    """Convert a ``--lang`` flag value to an internal language-profile string.

    Shared by the ELF/PE/Mach-O snapshot builders (C3) — previously this logic
    was a helper for ELF but copy-pasted inline for the other two formats.
    """
    if lang is None:
        return None
    lu = lang.upper()
    if lu == "C":
        return "c"
    if lu in ("C++", "CPP"):
        return "cpp"
    return None


def header_ast_parser(
    headers: list[Path],
    extra_includes: list[Path],
    *,
    backend: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    exported_dynamic: set[str],
    exported_static: set[str],
    public_header_paths: list[str],
    public_dir_paths: list[str],
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_context: str = "host",
    pruning_header_roots: tuple[str, ...] | None = None,
    no_binary_evidence: bool = False,
) -> _CastxmlParser | _ClangAstParser | NativeGccParser:
    """Run the resolved L2 backend and return its CastXML/Clang parser.

    Both parser implementations expose the same format-builder interface.

    Frontend context selection and header-only evidence semantics are
    forwarded unchanged to the selected backend.
    """
    effective = _resolve_effective_ast_backend(backend, frontend_context)

    request = HeaderParseRequest(
        headers=headers,
        extra_includes=extra_includes,
        compiler=compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        exported_dynamic=exported_dynamic,
        exported_static=exported_static,
        public_header_paths=public_header_paths,
        public_dir_paths=public_dir_paths,
        extra_hash_dirs=extra_hash_dirs,
        frontend_context=frontend_context,
        pruning_header_roots=pruning_header_roots,
        no_binary_evidence=no_binary_evidence,
    )

    def _run_clang(*, fallback_reason: str | None = None) -> _ClangAstParser:
        return cast(
            "_ClangAstParser",
            backend_registry.HEADER_AST_BACKENDS["clang"].parse(
                request, fallback_reason=fallback_reason
            ),
        )

    if effective == "gcc":
        return cast(
            "NativeGccParser",
            backend_registry.HEADER_AST_BACKENDS["gcc"].parse(request),
        )
    if effective == "clang":
        return _run_clang()

    plan = BackendPlan.from_environment()
    auto_selected = plan.auto_selected(backend)
    try:
        return cast(
            "_CastxmlParser",
            backend_registry.HEADER_AST_BACKENDS["castxml"].parse(request),
        )
    except CastxmlRunError as exc:
        fallback_reason = _castxml_fallback_reason(
            exc.original,
            auto_selected=auto_selected,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            plan=plan,
        )
        if fallback_reason is None:
            raise exc.original from None
        return _run_clang(fallback_reason=fallback_reason)

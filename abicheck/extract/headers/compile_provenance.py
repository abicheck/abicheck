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

"""Structured compile-context provenance for an ``AbiSnapshot`` built from a
header-AST parse (schema v15, ADR-085 D3 header scan). Split out of
``toolchain.py`` so that module stays under its no-growth baseline."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from ...buildsource.redaction import DEFAULT_REDACTION
from .template_default_scan import template_default_header_scan
from .toolchain import (
    _combined_option_tokens,
    _cplusplus_macro_for_standard,
    _resolve_standard_provenance,
)

__all__ = ["_AstCompileProvenance", "_ast_compile_provenance"]


class _AstCompileProvenance(TypedDict):
    ast_resolved_standard: str | None
    ast_cplusplus_macro: str | None
    ast_compile_args: tuple[str, ...]
    ast_sysroot: str | None
    template_default_header_scan: bool | None


def _ast_compile_provenance(
    headers: list[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    *,
    ast_toolchain: dict[str, str] | None = None,
    lang: str | None = None,
) -> _AstCompileProvenance:
    """Structured compile-context provenance kwargs for an ``AbiSnapshot``
    built from a header-AST parse (schema v15). A single call site shared by
    ``dumper.py``'s ELF/PE/Mach-O snapshot constructors so the four fields
    can never drift between them.

    ``ast_compile_args``/``ast_sysroot`` are redacted via the same
    :class:`~abicheck.buildsource.redaction.RedactionPolicy` every L3
    build-evidence adapter (``compile_db.py``, ``make.py``, ``bazel.py``,
    ``ninja.py``, ``cmake_file_api.py``) already applies before persisting a
    command line or path — a raw ``--compiler-option`` token can
    carry a secret-looking ``-DTOKEN=...`` define or an absolute home-prefixed
    path, and this is the one place such tokens reach a persisted snapshot
    without going through that established convention.

    ``ast_toolchain``/``lang`` (ADR-050 D1/D2 follow-up): the just-resolved
    header-AST toolchain identity (``dumper_toolchain._parser_ast_toolchain``'s
    result — the same dict :func:`_compiler_family_from_toolchain` already
    reads ``compiler_selected``/``selected`` from) and the caller's raw
    ``lang``, forwarded to :func:`_resolve_standard_provenance` so it can
    probe the resolved compiler's own unpinned default standard when no
    explicit one was given — see that function's own docstring. ``None``
    for either (the default) preserves the exact prior behavior: no probe
    attempted, ``ast_resolved_standard`` stays ``None`` for an unpinned dump.
    """
    resolved_standard = _resolve_standard_provenance(
        headers,
        gcc_options,
        gcc_option_tokens,
        probe_compiler_bin=(
            (ast_toolchain or {}).get("compiler_selected")
            or (ast_toolchain or {}).get("selected")
        ),
        lang=lang,
        resolved_lang_mode=(ast_toolchain or {}).get("resolved_lang_mode"),
        abi_dialect=(ast_toolchain or {}).get("abi_dialect"),
    )
    args = _combined_option_tokens(gcc_options, gcc_option_tokens)
    return {
        "ast_resolved_standard": resolved_standard,
        "ast_cplusplus_macro": _cplusplus_macro_for_standard(resolved_standard),
        "ast_compile_args": tuple(DEFAULT_REDACTION.argv(args)),
        "ast_sysroot": DEFAULT_REDACTION.path(str(sysroot)) if sysroot else None,
        # ADR-085 D3: castxml cannot record class-template parameter defaults,
        # so its run scans the headers it parsed for one. Other producers record
        # them (clang) or are hybrid, and get no scan.
        "template_default_header_scan": (
            template_default_header_scan(headers)
            if (ast_toolchain or {}).get("producer") == "castxml"
            else None
        ),
    }

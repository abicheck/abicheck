# SPDX-License-Identifier: Apache-2.0
"""Native GCC frontend implementing the existing header-AST parser seam."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ...._compiler_options import split_command_line
from ....errors import SnapshotError
from ....model import Fact
from ...header_ast_fields import HeaderAstFields
from ..backend_protocol import HeaderParseRequest
from .acquisition import acquire
from .discovery import find_plugin, resolve_compiler
from .projection import project


class NativeGccParser:
    """A normalized native result; no Clang AST/XML or synthetic AST nodes."""

    def __init__(self, fields: HeaderAstFields, metadata: dict[str, str]) -> None:
        self.normalized_fields: HeaderAstFields = fields
        self._abicheck_ast_toolchain = metadata

    def parse_functions(self) -> Any:
        return self.normalized_fields.functions

    def parse_variables(self) -> Any:
        return self.normalized_fields.variables

    def parse_types(self) -> Any:
        return self.normalized_fields.types

    def parse_enums(self) -> Any:
        return self.normalized_fields.enums

    def parse_typedefs(self) -> Any:
        return self.normalized_fields.typedefs

    def parse_typedefs_qualified(self) -> Any:
        return self.normalized_fields.typedefs_qualified

    def parse_constants(self) -> Any:
        return self.normalized_fields.constants

    def parse_typedef_entity_ids(self) -> Any:
        return self.normalized_fields.typedef_entity_ids

    def parse_constant_entity_ids(self) -> Any:
        return self.normalized_fields.constant_entity_ids

    def parse_typedef_abi_facts(self) -> Any:
        return self.normalized_fields.typedef_abi_facts


class GccBackend:
    name = "gcc"

    def parse(
        self, request: HeaderParseRequest, *, fallback_reason: str | None = None
    ) -> NativeGccParser:
        if fallback_reason is not None:
            # Native GCC is only ever an explicit choice (ADR-092): no other
            # backend falls back into it, so a fallback reason here is a
            # dispatch bug, not provenance to record.
            raise SnapshotError(
                f"Native GCC is never a fallback backend (asked to run because: {fallback_reason})"
            )
        if request.frontend_context != "host":
            raise SnapshotError("Native GCC does not support SYCL/device contexts")
        if not request.headers:
            raise SnapshotError("Native GCC requires at least one public header")
        # Honor an explicit language; never retry a failed C parse as C++.
        language = "c" if request.lang == "c" else "c++"
        cc = request.gcc_path or (
            (request.gcc_prefix or "") + ("gcc" if language == "c" else "g++")
        )
        compiler = resolve_compiler(cc)
        plugin = find_plugin(compiler, language)
        flags = [
            *split_command_line(request.gcc_options or "", cl_style=False),
            *request.gcc_option_tokens,
        ]
        for inc in request.extra_includes:
            flags.extend(["-I", str(inc.resolve())])
        if request.sysroot:
            flags.append("--sysroot=" + str(request.sysroot))
        if request.nostdinc:
            flags.append("-nostdinc")
        try:
            with tempfile.TemporaryDirectory(prefix="abicheck-gcc-headers-") as tmp:
                wrapper = Path(tmp) / "aggregate.h"
                wrapper.write_text(
                    "".join(
                        "#include " + json.dumps(str(p.resolve())) + "\n"
                        for p in request.headers
                    )
                )
                rows = acquire(wrapper, compiler, plugin, flags, language=language)
                rows[0]["includes"] = [
                    p for p in rows[0]["includes"] if p != str(wrapper)
                ]
                rows[0]["headers"] = [str(p.resolve()) for p in request.headers]
            fields = project(
                rows,
                request.headers[0],
                language=language,
                public_headers=[
                    *request.headers[1:],
                    *(Path(p) for p in request.public_header_paths),
                ],
                public_roots=[Path(p) for p in request.public_dir_paths],
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise SnapshotError(
                f"Native GCC extraction failed: {exc}. Verify the plugin matches this GCC build. No Clang fallback was attempted."
            ) from exc
        for declaration in (*fields.functions, *fields.variables):
            declaration.declared_in_headers_fact = Fact.present(True)
            declaration.binary_exported_fact = (
                Fact.not_collected()
                if request.no_binary_evidence
                else Fact.present(
                    declaration.mangled in request.exported_dynamic
                    or declaration.mangled in request.exported_static
                )
            )
        info = rows[0]
        metadata = {
            "producer": "gcc-experimental",
            "executable": compiler,
            "version": info["version"],
            "compiler_executable": compiler,
            "compiler_family": "gcc",
            "compiler_version": info["full_version"],
            "target_triple": info["target"],
            "abi_dialect": "gnu",
            "resolved_lang_mode": language,
            "plugin_sha256": info["plugin_sha256"],
            "language_standard_explicit": "1"
            if any(f.startswith("-std=") for f in flags)
            else "0",
        }
        return NativeGccParser(fields, metadata)

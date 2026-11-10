# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Ask the selected compiler for non-fixed enum representations, without running code."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....dumper_ast_config_cpp20 import _detect_cpp20_headers
from ...path_aliases import absolutize_include_roots
from ..ast_config import _build_clang_header_command, clang_aggregate_text
from ..backend_protocol import HeaderParseRequest
from ..sysinc import _resolve_clang_system_includes
from .context import _Decl, is_builtin_file

_TYPES = (
    "char",
    "signed char",
    "unsigned char",
    "short",
    "unsigned short",
    "int",
    "unsigned int",
    "long",
    "unsigned long",
    "long long",
    "unsigned long long",
)
_PREFIX = "__abicheck_enum_representation_"
log = logging.getLogger(__name__)


def probe_enum_types(
    request: HeaderParseRequest,
    enums: list[_Decl],
    *,
    clang_bin: str,
    force_cpp: bool,
    run_ast: Callable[..., Any],
) -> dict[str, str]:
    """Return AST declaration id -> compiler-confirmed underlying integer type.

    C's generic selection matches an enum's compatible integer type; C++'s
    underlying-type builtin supplies the exact type. The same target, language,
    includes and ABI flags as the original parse apply. No host executable is
    built or run, so cross compilation is supported. One batched pass per dump.
    """
    selected = [
        e
        for e in enums
        if e.node.get("name")
        and not e.node.get("fixedUnderlyingType")
        and not is_builtin_file(e.file)
    ]
    if not selected:
        return {}
    probes = []
    for i, entry in enumerate(selected):
        name = (
            "::".join((*entry.scope, entry.node["name"]))
            if force_cpp
            else entry.node["name"]
        )
        if force_cpp:
            expr = (
                "".join(
                    f"__is_same(__underlying_type(::{name}), {t}) ? {j} : "
                    for j, t in enumerate(_TYPES, 1)
                )
                + "0"
            )
        else:
            expr = (
                "_Generic((enum "
                + name
                + ")0, "
                + ", ".join(f"{t}: {j}" for j, t in enumerate(_TYPES, 1))
                + ", default: 0)"
            )
        probes.append(f"enum {{ {_PREFIX}{i} = {expr} }};\n")
    system_includes = _resolve_clang_system_includes(
        request.compiler,
        gcc_path=request.gcc_path,
        gcc_prefix=request.gcc_prefix,
        sysroot=request.sysroot,
        nostdinc=request.nostdinc,
        force_cpp=force_cpp,
        gcc_options=request.gcc_options,
        gcc_option_tokens=request.gcc_option_tokens,
    )
    paths: list[Path] = []
    with tempfile.NamedTemporaryFile(
        suffix=".cpp" if force_cpp else ".c", mode="w", delete=False
    ) as source:
        source.write(clang_aggregate_text(request.headers) + "".join(probes))
        path = Path(source.name)
    try:
        cmd = _build_clang_header_command(
            clang_bin,
            "gnu",
            absolutize_include_roots(list(request.extra_includes)),
            path,
            sysroot=request.sysroot,
            nostdinc=request.nostdinc,
            gcc_options=request.gcc_options,
            gcc_option_tokens=request.gcc_option_tokens,
            force_cpp=force_cpp,
            force_cpp20=force_cpp and _detect_cpp20_headers(request.headers),
            system_includes=system_includes,
        )
        cmd.extend(["-Xclang", "-ast-dump-filter", "-Xclang", _PREFIX])
        run_ast(cmd, timeout=120, on_created=paths.append)
        if not paths:
            return {}
        text = paths[-1].read_text(encoding="utf-8")
        decoder = json.JSONDecoder()
        answer = {}
        offset = 0
        while offset < len(text):
            while offset < len(text) and text[offset].isspace():
                offset += 1
            if offset == len(text):
                break
            node, offset = decoder.raw_decode(text, offset)
            name = node.get("name", "")
            if not name.startswith(_PREFIX):
                continue
            i = int(name.removeprefix(_PREFIX))
            expr = node
            while expr.get("kind") in (
                "EnumConstantDecl",
                "ImplicitCastExpr",
                "ParenExpr",
            ):
                children = expr.get("inner", [])
                if len(children) != 1:
                    break
                expr = children[0]
            value = expr.get("value") if expr.get("kind") == "ConstantExpr" else None
            if value is not None and 0 < int(value) <= len(_TYPES):
                answer[str(selected[i].node["id"])] = _TYPES[int(value) - 1]
        return answer
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        IndexError,
    ) as exc:
        log.debug("Enum representation probe unavailable: %s", exc)
        return {}
    finally:
        path.unlink(missing_ok=True)
        for output in paths:
            output.unlink(missing_ok=True)

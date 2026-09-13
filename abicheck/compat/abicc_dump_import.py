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

"""Minimal ABICC (abi-dumper) Perl dump importer.

This module is intentionally isolated from main compat logic so ABICC dump parsing
can evolve independently while keeping the CLI flow simple.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from ..errors import SnapshotError, ValidationError
from ..extract.surface_fact_producers import header_ast_surface_facts
from ..model import AbiSnapshot, Function, Param, RecordType, Variable, Visibility


def looks_like_perl_dump(text: str) -> bool:
    """Heuristic detection for Data::Dumper ABI dumps."""
    head = text.lstrip()
    return head.startswith("$VAR1")


def import_abicc_perl_dump(path: Path) -> AbiSnapshot:
    """Convert abi-dumper Perl Data::Dumper output into a minimal AbiSnapshot.

    This importer is deliberately lenient and migration-focused:
    - parses symbols and a minimal subset of type names,
    - tolerates unknown sections,
    - preserves compatibility verdict signal over full-fidelity metadata.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SnapshotError(f"Failed to read ABICC Perl dump: {exc}") from exc

    if not looks_like_perl_dump(text):
        raise ValidationError(
            "Invalid ABICC Perl dump: expected Data::Dumper content starting with $VAR1"
        )

    data = _parse_perl_dumper_subset(text)

    if not isinstance(data, dict):
        raise ValidationError(
            "Invalid ABICC Perl dump: top-level structure is not a hash/dict"
        )

    return _snapshot_from_abicc_dict(data, path)


def _parse_perl_dumper_subset(text: str) -> object:
    """Parse a strict, safe subset of Perl Data::Dumper syntax.

    Accepted shape: ``$VAR1 = <perl-literal>;`` where perl-literal is composed of
    dict/list/scalar literals (single-quoted strings, numbers, undef, hashes,
    arrays). No code execution is performed.
    """
    stripped = text.lstrip()
    if not stripped.startswith("$VAR1"):
        raise ValidationError("Invalid ABICC Perl dump: missing $VAR1 assignment")

    if "=" not in stripped:
        raise ValidationError("Invalid ABICC Perl dump: malformed assignment")

    _, rhs = stripped.split("=", 1)
    rhs = rhs.strip()
    if rhs.endswith(";"):
        rhs = rhs[:-1].strip()

    py_expr = _perl_expr_to_python_literal(rhs)

    try:
        obj = ast.literal_eval(py_expr)
    except (SyntaxError, ValueError) as exc:
        raise ValidationError(f"Failed to parse ABICC Perl dump safely: {exc}") from exc

    # Round-trip through JSON-compatible form for predictable primitive types.
    try:
        return json.loads(json.dumps(obj))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"Failed to normalize ABICC Perl dump structure: {exc}"
        ) from exc


def _perl_expr_to_python_literal(expr: str) -> str:
    """Translate a safe Perl-literal subset to Python literal syntax.

    Only performs syntax-token conversion outside single-quoted strings:
    - ``=>`` -> ``:``
    - bareword ``undef`` -> ``None``
    """
    out: list[str] = []
    i = 0
    in_single = False
    n = len(expr)

    while i < n:
        ch = expr[i]

        if in_single:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                i += 1
                out.append(expr[i])
            elif ch == "'":
                in_single = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue

        if ch == "=" and i + 1 < n and expr[i + 1] == ">":
            out.append(":")
            i += 2
            continue

        if expr.startswith("undef", i):
            prev_ok = i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_")
            j = i + 5
            next_ok = j >= n or not (expr[j].isalnum() or expr[j] == "_")
            if prev_ok and next_ok:
                out.append("None")
                i = j
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def _snapshot_from_abicc_dict(data: dict[str, object], path: Path) -> AbiSnapshot:
    type_info = data.get("TypeInfo")
    symbol_info = data.get("SymbolInfo")

    type_map: dict[str, dict[str, object]] = (
        type_info if isinstance(type_info, dict) else {}
    )
    sym_map: dict[str, dict[str, object]] = (
        symbol_info if isinstance(symbol_info, dict) else {}
    )

    library = str(data.get("LibraryName") or path.stem)
    version = str(data.get("LibraryVersion") or "unknown")

    functions: list[Function] = []
    variables: list[Variable] = []

    for _, sym in sym_map.items():
        if not isinstance(sym, dict):
            continue

        mangled = str(sym.get("MnglName") or "")
        if not mangled:
            continue

        short_name = str(sym.get("ShortName") or mangled)

        is_function = any(
            k in sym for k in ("Param", "Return", "Constructor", "Destructor")
        )
        if is_function:
            params = _parse_params(sym, type_map)
            return_type = _resolve_type_name(sym.get("Return"), type_map)
            functions.append(
                Function(
                    name=short_name,
                    mangled=mangled,
                    return_type=return_type,
                    params=params,
                    visibility=Visibility.PUBLIC,
                    # An ABICC dump is a header-derived description of the
                    # promised surface; it carries no export table of its
                    # own, so (c) stays unknown.
                    **header_ast_surface_facts(exported=None, producer="abicc"),
                )
            )
        else:
            var_type = _resolve_type_name(sym.get("Type"), type_map)
            variables.append(
                Variable(
                    name=short_name,
                    mangled=mangled,
                    type=var_type,
                    visibility=Visibility.PUBLIC,
                    **header_ast_surface_facts(exported=None, producer="abicc"),
                )
            )

    types = _extract_record_types(type_map)

    return AbiSnapshot(
        library=library,
        version=version,
        functions=functions,
        variables=variables,
        types=types,
    )


def _parse_params(
    sym: dict[str, object], type_map: dict[str, dict[str, object]]
) -> list[Param]:
    raw = sym.get("Param")
    if not isinstance(raw, dict):
        return []

    params: list[tuple[int, Param]] = []
    for pos, pinfo in raw.items():
        if not isinstance(pinfo, dict):
            continue
        try:
            idx = int(str(pos))
        except ValueError:
            idx = 9999

        ptype = _resolve_type_name(pinfo.get("type"), type_map)
        pname = str(pinfo.get("name") or f"arg{idx}")
        params.append((idx, Param(name=pname, type=ptype)))

    params.sort(key=lambda x: x[0])
    return [p for _, p in params]


def _resolve_type_name(type_id: object, type_map: dict[str, dict[str, object]]) -> str:
    if type_id is None:
        return "unknown"

    tid = str(type_id)
    tinfo = type_map.get(tid)
    if not isinstance(tinfo, dict):
        return "unknown"

    name = tinfo.get("Name")
    if isinstance(name, str) and name.strip():
        return name

    return "unknown"


def _extract_record_types(type_map: dict[str, dict[str, object]]) -> list[RecordType]:
    out: list[RecordType] = []
    for _, tinfo in type_map.items():
        if not isinstance(tinfo, dict):
            continue
        kind_raw = str(tinfo.get("Type") or "").lower()
        if kind_raw not in ("struct", "class", "union"):
            continue

        name = tinfo.get("Name")
        if not isinstance(name, str) or not name.strip():
            continue

        out.append(
            RecordType(
                name=name,
                kind=kind_raw,
                is_union=(kind_raw == "union"),
            )
        )
    return out


def is_abicc_perl_dump_file(path: Path) -> bool:
    """Return True if path looks like an ABICC Perl Data::Dumper dump."""
    if path.suffix == ".dump":
        return True
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:512]
    except OSError:
        return False
    return looks_like_perl_dump(head)

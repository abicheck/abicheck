# SPDX-License-Identifier: Apache-2.0
"""Validated native compiler acquisition and explicit public header scope."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from ....deadline import run_bounded
from ....model.vocabulary import ScopeOrigin


class HeaderScope:
    def __init__(
        self, header: Path, public_headers: Any = (), public_roots: Any = ()
    ) -> None:
        self.headers = {Path(p).resolve() for p in (header, *public_headers)}
        self.roots = tuple(Path(p).resolve() for p in public_roots)
        self._public_cache: dict[str, bool] = {}

    def public(self, source: Any) -> Any:
        if not source or source.startswith("<"):
            return False
        if source not in self._public_cache:
            path = Path(source).resolve()
            self._public_cache[source] = path in self.headers or any(
                path.is_relative_to(root) for root in self.roots
            )
        return self._public_cache[source]

    def origin(self, row: Any) -> Any:
        if self.public(row.get("source")):
            return ScopeOrigin.PUBLIC_HEADER
        if row.get("system_header"):
            return ScopeOrigin.SYSTEM_HEADER
        return ScopeOrigin.PRIVATE_HEADER if row.get("source") else ScopeOrigin.UNKNOWN


def checked(command: Any) -> Any:
    result = run_bounded(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(
            f"GCC command failed ({result.returncode}): {command}\n{result.stderr}"
        )
    return result


def preprocess(compiler: Any, source: Any, flags: Any, language: Any) -> Any:
    result = checked([compiler, *flags, "-x", language, "-E", "-dD", str(source)])
    filename_decoder = json.JSONDecoder()
    current = ""
    line = 0
    system = False
    macros = {}
    includes = []
    seen_includes: set[str] = set()
    for text in result.stdout.splitlines():
        marker = re.match('^#\\s+(\\d+)\\s+("(?:[^"\\\\]|\\\\.)*")(.*)$', text)
        if marker:
            line = int(marker[1])
            current = filename_decoder.decode(marker[2])
            system = "3" in marker[3].split()
            if (
                current
                and (not current.startswith("<"))
                and (current not in seen_includes)
            ):
                includes.append(current)
                seen_includes.add(current)
            continue
        define = re.match("^#define\\s+([A-Za-z_]\\w*)(\\([^)]*\\))?\\s*(.*)$", text)
        undef = re.match("^#undef\\s+(\\w+)", text)
        if define:
            macros[define[1]] = {
                "kind": "macro",
                "name": define[1],
                "parameters": define[2],
                "replacement": define[3],
                "source": current,
                "line": line,
                "system_header": system,
            }
        elif undef:
            macros.pop(undef[1], None)
        line += 1
    return (list(macros.values()), includes)


def acquire(
    header: Path,
    compiler: str,
    plugin: Path,
    flags: Any = (),
    *,
    language: Any = "c++",
    raw_output: Path | None = None,
    preprocessing: Any = True,
) -> Any:
    header = header.resolve()
    plugin = plugin.resolve()
    flags = tuple(flags)
    if not header.is_file():
        raise ValueError(f"Header does not exist: {header}")
    if not plugin.is_file():
        raise ValueError(f"Plugin does not exist: {plugin}")
    for flag in flags:
        if flag in ("-c", "-S", "-E", "-o", "-x", "-fsyntax-only") or flag.startswith(
            ("-fplugin", "-o=")
        ):
            raise ValueError(f"Use only compilation-context flags, got {flag}")
    with tempfile.TemporaryDirectory(prefix="gcc-facts-") as temp_name:
        tmp = Path(temp_name)
        source = tmp / ("probe.cpp" if language == "c++" else "probe.c")
        output = tmp / "facts.jsonl"
        source.write_text("#include " + json.dumps(str(header)) + "\n")
        checked(
            [
                compiler,
                *flags,
                "-x",
                language,
                "-fsyntax-only",
                "-fplugin=" + str(plugin),
                f"-fplugin-arg-{plugin.stem}-output={output}",
                str(source),
            ]
        )
        if not output.exists():
            raise RuntimeError("GCC plugin produced no facts")
        with output.open(encoding="utf-8") as stream:
            rows = list(map(json.loads, stream))
        if not rows or rows[-1] != {"kind": "end", "errors": 0}:
            raise RuntimeError("Incomplete GCC extraction")
        if rows[0].get("kind") != "compiler" or rows[0].get("schema") != 7:
            raise RuntimeError("Unsupported GCC fact protocol")
        macros, includes = (
            preprocess(compiler, source, flags, language) if preprocessing else ([], [])
        )
        includes = [p for p in includes if Path(p).resolve() != source]
        macros = [m for m in macros if m["source"] != str(source)]
        rows[0].update(
            {
                "driver": str(compiler),
                "target": checked([compiler, "-dumpmachine"]).stdout.strip(),
                "full_version": checked([compiler, "-dumpfullversion"]).stdout.strip(),
                "language": language,
                "flags": list(flags),
                "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
                "preprocessing": preprocessing,
                "includes": includes,
            }
        )
        rows[-1:-1] = macros
        if raw_output:
            raw_output.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            )
        return rows

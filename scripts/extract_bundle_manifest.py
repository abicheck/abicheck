#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Extract a baseline bundle manifest from a release directory.

A bundle manifest (see ADR-023) is the file you pass to ``abicheck
compare-release --manifest manifest.yaml`` to assert that specific
symbols / instantiation patterns survive between releases. Hand-writing
one is impractical for libraries with thousands of exported symbols;
this script emits a starting point that a human then curates.

Usage::

    python scripts/extract_bundle_manifest.py release-2.0/lib/ > manifest.yaml

Output shape (always pattern entries — see ADR-023 manifest format):

    version: 1
    # AUTO-GENERATED baseline manifest. EDIT before committing —
    # patterns below are deliberately coarse so the diff is readable.
    provides:
      - pattern: "oneapi::dal::*"        # all symbols under that namespace
        library: libonedal_core.so.1
        optional_provider: false

The script's grouping heuristic:

1. For each library in the release, list every exported symbol.
2. Demangle each name (via cxxfilt / c++filt).
3. Group by top-level C++ namespace (the substring up to the first
   ``::`` in the demangled form). ``extern "C"`` symbols (no
   demangling) are grouped by common prefix.
4. Emit one ``pattern:`` entry per (namespace, library) pair, pinned
   to that library with ``optional_provider: false``.

The result is intentionally over-broad: every symbol the bundle
currently exports is promised. A curator narrows it (drop ``detail::``
namespaces, switch generic patterns to specific template forms, mark
unstable namespaces ``optional_provider: true``).
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from abicheck.bundle import build_bundle_snapshot
from abicheck.demangle import demangle


def _common_prefix(names: list[str], min_len: int = 4) -> str | None:
    """Longest common prefix shared by *every* name. Returns None if
    the prefix would be shorter than *min_len* characters."""
    if not names:
        return None
    prefix = names[0]
    for n in names[1:]:
        i = 0
        while i < len(prefix) and i < len(n) and prefix[i] == n[i]:
            i += 1
        prefix = prefix[:i]
        if len(prefix) < min_len:
            return None
    return prefix or None


def _group_symbols(symbols: list[str]) -> list[tuple[str, str]]:
    """Group symbols by namespace (C++) or common prefix (C).

    Returns ``[(pattern, label)]`` tuples sorted by pattern.
    """
    by_namespace: dict[str, list[str]] = defaultdict(list)
    c_symbols: list[str] = []
    for sym in symbols:
        d = demangle(sym)
        if d is None or d == sym or "::" not in d:
            # extern "C" or non-C++ symbol — bucket separately for prefix grouping.
            c_symbols.append(sym)
            continue
        ns = d.split("::", 1)[0]
        by_namespace[ns].append(d)

    patterns: list[tuple[str, str]] = []
    for ns, names in sorted(by_namespace.items()):
        patterns.append((f"{ns}::*", f"{len(names)} symbol(s) under {ns}::"))

    if c_symbols:
        # If extern "C" symbols share an obvious prefix, emit one pattern;
        # otherwise emit a wildcard fallback the curator narrows by hand.
        prefix = _common_prefix(c_symbols)
        if prefix:
            patterns.append(
                (f"{prefix}*", f"{len(c_symbols)} C symbol(s) starting with {prefix!r}"),
            )
        else:
            patterns.append(
                ("*", f"{len(c_symbols)} unrelated C symbol(s); narrow this pattern by hand"),
            )

    return patterns


def _yaml_escape(value: str) -> str:
    """Minimal YAML quoting for symbol patterns (preserves ``<>``, ``::``, ``*``)."""
    if any(c in value for c in ":#@`{}[],&*!|>'\"%"):
        # Wrap in double quotes; escape embedded double quotes.
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a baseline bundle manifest (ADR-023) from a release "
            "directory's exported symbols. Output goes to stdout; pipe "
            "into a file and then curate by hand."
        ),
    )
    parser.add_argument(
        "release_dir",
        type=Path,
        help="Directory containing the release's .so files.",
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
        help=(
            "Include libraries that look private (hidden visibility "
            "dominant). Off by default to keep the baseline focused on "
            "the public surface."
        ),
    )
    args = parser.parse_args()

    if not args.release_dir.is_dir():
        print(
            f"error: {args.release_dir} is not a directory",
            file=sys.stderr,
        )
        return 2

    libraries = {p.name: p for p in sorted(args.release_dir.rglob("*.so*"))
                 if p.is_file()}
    if not libraries:
        print(
            f"error: no .so files under {args.release_dir}",
            file=sys.stderr,
        )
        return 2

    snapshot = build_bundle_snapshot(libraries)
    if not snapshot.metadata:
        print(
            f"error: no ELF libraries parsed under {args.release_dir}",
            file=sys.stderr,
        )
        return 2

    out: list[str] = []
    out.append("# Auto-generated baseline bundle manifest (ADR-023).")
    out.append(f"# Source: {args.release_dir}")
    out.append("# IMPORTANT: this is a starting point. Curate before committing —")
    out.append("# patterns are deliberately coarse so the public-API diff stays")
    out.append("# readable. Drop internal namespaces (detail::, impl::), narrow")
    out.append("# wildcards to specific template instantiations, mark unstable")
    out.append("# surface optional_provider: true.")
    out.append("version: 1")
    out.append("provides:")

    for lib_name in sorted(snapshot.metadata):
        meta = snapshot.metadata[lib_name]
        symbols = [
            s.name for s in meta.symbols
            if s.visibility in ("default", "protected")
        ]
        if not symbols:
            continue
        out.append(f"  # {lib_name}")
        for pattern, comment in _group_symbols(symbols):
            soname = meta.soname or lib_name
            out.append(f"  - pattern: {_yaml_escape(pattern)}")
            out.append(f"    library: {_yaml_escape(soname)}")
            out.append("    optional_provider: false")
            out.append(f"    # {comment}")

    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

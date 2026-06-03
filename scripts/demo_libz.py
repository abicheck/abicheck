#!/usr/bin/env python3
"""End-to-end demo: dump libz.so, simulate v1.4 changes, compare.

Run:
    python scripts/demo_libz.py

Requires: castxml, zlib1g-dev
  apt install castxml zlib1g-dev
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make sure we can run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from abicheck.checker import Verdict, compare
from abicheck.dumper import dump
from abicheck.model import Function, Visibility
from abicheck.reporter import to_markdown
from abicheck.serialization import load_snapshot, save_snapshot

ZLIB_SO = Path("/usr/lib/x86_64-linux-gnu/libz.so.1.3")
ZLIB_SO_ALT = Path("/usr/lib/x86_64-linux-gnu/libz.so.1")
ZLIB_H = Path("/usr/include/zlib.h")

if not ZLIB_SO.exists():
    ZLIB_SO = ZLIB_SO_ALT
if not ZLIB_SO.exists():
    print("ERROR: libz.so not found. Install: apt install zlib1g-dev", file=sys.stderr)
    sys.exit(1)
if not ZLIB_H.exists():
    print("ERROR: zlib.h not found. Install: apt install zlib1g-dev", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # ── Step 1: Dump real libz snapshot ──────────────────────────────────
        print("=== Step 1: Dump libz (real .so + headers) ===")
        snap_v1 = dump(
            so_path=ZLIB_SO,
            headers=[ZLIB_H],
            version="1.3.0",
        )
        pub = [f for f in snap_v1.functions if f.visibility == Visibility.PUBLIC]
        print(f"  Library : {snap_v1.library}")
        print(f"  Functions total / public : {len(snap_v1.functions)} / {len(pub)}")
        print(f"  Types   : {len(snap_v1.types)}")
        print(f"  Variables: {len(snap_v1.variables)}")
        print(f"  Sample public funcs: {', '.join(f.name for f in pub[:5])}")

        snap1_path = tmp / "libz-1.3.json"
        save_snapshot(snap_v1, snap1_path)
        print(f"  Saved → {snap1_path}\n")

        # ── Step 2: Simulate libz 1.4.0 ABI changes ──────────────────────────
        print("=== Step 2: Simulate libz 1.4.0 (ABI changes) ===")
        snap_v2 = load_snapshot(snap1_path)
        snap_v2.version = "1.4.0"
        snap_v2.library = "libz.so.1.4"

        # Breaking: remove gzgetc_
        removed = [f.name for f in snap_v2.functions if f.name == "gzgetc_"]
        snap_v2.functions = [f for f in snap_v2.functions if f.name != "gzgetc_"]

        # Breaking: change return type of zlibCompileFlags
        for f in snap_v2.functions:
            if f.name == "zlibCompileFlags":
                f.return_type = "unsigned long"  # was uLong (typedef for unsigned long, but new name)

        # Compatible: add new function
        snap_v2.functions.append(Function(
            name="deflate2", mangled="deflate2",
            return_type="int", visibility=Visibility.PUBLIC,
        ))

        # The simulated future snapshot mutates headers/functions in memory, not
        # a real rebuilt .so. Drop stale ELF metadata so the comparison uses the
        # synthetic header surface consistently.
        snap_v1.elf = None
        snap_v2.elf = None

        snap2_path = tmp / "libz-1.4.json"
        save_snapshot(snap_v2, snap2_path)
        print(f"  Removed : {removed}")
        print("  Changed return type: zlibCompileFlags uLong → unsigned long")
        print("  Added   : deflate2")
        print(f"  Saved → {snap2_path}\n")

        # ── Step 3: Compare ───────────────────────────────────────────────────
        print("=== Step 3: Compare 1.3.0 → 1.4.0 ===")
        result = compare(snap_v1, snap_v2)
        print(f"  Verdict : {result.verdict.value}")
        print(f"  Breaking: {len(result.breaking)}")
        print(f"  Compatible changes: {len(result.compatible)}")
        for c in result.changes:
            print(f"  [{c.kind.value}] {c.description}")

        print()
        print("=== Markdown Report ===")
        print(to_markdown(result))

        # ── Step 4: Assert expected verdict ──────────────────────────────────
        assert result.verdict == Verdict.BREAKING, f"Expected BREAKING, got {result.verdict}"
        assert any(c.kind.value == "func_removed" for c in result.changes)
        assert any(c.kind.value == "func_return_changed" for c in result.changes)
        assert any(c.kind.value == "func_added" for c in result.changes)
        print("✅ All assertions passed")


if __name__ == "__main__":
    main()

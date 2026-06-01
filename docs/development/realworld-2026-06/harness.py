#!/usr/bin/env python3
"""Real-world abicheck validation harness.

Runs `abicheck compare` over matched library pairs, captures verdict,
summary counts, change-kind histogram, timing, and stderr warnings.
Writes a machine-readable results.jsonl plus per-pair JSON reports.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

# Output directory is overridable so the harness is reproducible outside the
# original container. Defaults to the layout used for the committed artifacts.
REPORTS = Path(os.environ.get("ABICHECK_VAL_OUT", "/tmp/val/reports"))

# Default places to look for extracted `daal-*` wheel directories when no roots
# are passed on the command line.
DEFAULT_SEARCH_DIRS = [Path.cwd(), Path("/tmp/val/work")]

_VERSION_RE = re.compile(r"daal-(\d+(?:\.\d+)+)")


def soname_stem(p: Path) -> str:
    """libonedal_core.so.4 -> libonedal_core ; libfoo-1.2.so.0.0 -> libfoo-1.2."""
    name = p.name
    name = re.sub(r"\.so.*$", "", name)
    return name


def find_libs(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in Path(root).rglob("*.so*"):
        if p.is_file():
            out.setdefault(soname_stem(p), p)
    return out


def discover_versions(roots: list[Path]) -> dict[str, Path]:
    """Map oneDAL version string -> extracted-wheel root.

    Each root may itself be a ``daal-<version>...`` directory, or a parent
    directory that contains one or more such directories. Version is parsed
    from the ``daal-<version>`` directory name.
    """
    found: dict[str, Path] = {}
    for root in roots:
        candidates = [root, *root.glob("daal-*"), *root.glob("*/daal-*")]
        for c in candidates:
            if not c.is_dir():
                continue
            m = _VERSION_RE.search(c.name)
            if m:
                found.setdefault(m.group(1), c)
    return found


def build_pairs(versions: list[str]) -> list[tuple[str, str, str]]:
    """Adjacent pairs plus first->last ('far') for >=3 versions."""
    ordered = sorted(versions, key=lambda v: [int(x) for x in v.split(".")])
    pairs = [(ordered[i], ordered[i + 1], "adjacent" if i + 2 == len(ordered)
              else f"step{i}") for i in range(len(ordered) - 1)]
    if len(ordered) >= 3:
        pairs.append((ordered[0], ordered[-1], "far"))
    return pairs


def run_compare(old: Path, new: Path, tag: str, out_dir: Path,
                extra: list[str] | None = None) -> dict:
    out_json = out_dir / f"{tag}.json"
    cmd = [
        "abicheck", "compare", str(old), str(new),
        "--format", "json", "-o", str(out_json),
    ] + (extra or [])
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    rec: dict = {
        "tag": tag,
        "old": str(old),
        "new": str(new),
        "rc": proc.returncode,
        "elapsed_s": round(elapsed, 2),
        "cmd": " ".join(cmd),
    }
    # capture distinctive stderr warnings (dedup)
    warns = sorted({ln.strip() for ln in proc.stderr.splitlines()
                    if "Warning" in ln or "warning" in ln or "Error" in ln})
    rec["warnings"] = warns
    if out_json.exists():
        try:
            d = json.loads(out_json.read_text())
            rec["verdict"] = d.get("verdict")
            rec["summary"] = d.get("summary")
            kinds = Counter(c.get("kind") for c in d.get("changes", []))
            rec["kind_histogram"] = dict(kinds)
            rec["surface_scope"] = {k: d.get("surface_scope", {}).get(k)
                                     for k in ("enabled", "confidence")}
            rec["release_recommendation"] = d.get("release_recommendation")
        except Exception as e:  # noqa: BLE001
            rec["parse_error"] = repr(e)
    else:
        rec["stderr_tail"] = proc.stderr.splitlines()[-5:]
    return rec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots", nargs="*", type=Path,
        help="Extracted `daal-<version>...` wheel directories, or parent "
             "directories containing them. Defaults to the current directory "
             "and /tmp/val/work.")
    parser.add_argument(
        "-o", "--out", type=Path, default=REPORTS,
        help="Output directory for reports (default: $ABICHECK_VAL_OUT or "
             "/tmp/val/reports).")
    args = parser.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    search = args.roots or DEFAULT_SEARCH_DIRS
    versions = discover_versions(search)
    if len(versions) < 2:
        sys.exit(
            "error: need at least 2 extracted `daal-<version>` wheel "
            f"directories, found {sorted(versions)} under "
            f"{[str(p) for p in search]}. Download and unzip the wheels first, "
            "e.g. `pip download --no-deps daal==2025.11.0 daal==2026.0.0` then "
            "`unzip` each, and pass the resulting directories as arguments.")

    libs = {v: find_libs(root) for v, root in versions.items()}
    for v, m in sorted(libs.items()):
        if not m:
            print(f"warning: no .so libraries under {versions[v]} (version {v})",
                  file=sys.stderr)
        print(f"# {v} ({versions[v]}): {sorted(m)}", file=sys.stderr)

    pairs = build_pairs(list(versions))
    results = []
    for old_v, new_v, label in pairs:
        common = sorted(set(libs[old_v]) & set(libs[new_v]))
        for stem in common:
            tag = f"onedal_{label}_{stem}"
            rec = run_compare(libs[old_v][stem], libs[new_v][stem], tag, out_dir)
            rec["pair"] = f"{old_v}->{new_v}"
            rec["stem"] = stem
            results.append(rec)
            print(f"{tag}: verdict={rec.get('verdict')} "
                  f"changes={rec.get('summary',{}).get('total_changes')} "
                  f"t={rec['elapsed_s']}s", file=sys.stderr)
        # report removed libs
        removed = sorted(set(libs[old_v]) - set(libs[new_v]))
        added = sorted(set(libs[new_v]) - set(libs[old_v]))
        results.append({"tag": f"onedal_{label}_inventory", "pair": f"{old_v}->{new_v}",
                        "removed_libs": removed, "added_libs": added})

    out = out_dir / "onedal_results.jsonl"
    with out.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"\nWrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()

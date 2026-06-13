#!/usr/bin/env python3
"""abicheck real-world validation harness.

For each curated version pair, find every shared object present in BOTH the old
and new package (matched by logical library name, e.g. libtbb / libcrypto), then
run `abicheck compare` capturing JSON, exit code, wall time, and stderr.
Aggregates into results.json for the report.

Paths resolve from the checkout: the manifest is read from and results written to
``validation/data/`` next to this script. The extracted-libraries directory is NOT
committed (binaries are large); point the harness at it via the
``ABICHECK_VALIDATION_LIBS`` environment variable (default: ``validation/libs/ex``
relative to the repo). Each package extracts to ``<libs>/<pkg_stem>/lib/*.so*`` —
see ``data/manifest.json`` for the conda-forge files to fetch and extract.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

VALID_DIR = Path(__file__).resolve().parent.parent  # validation/
DATA = VALID_DIR / "data"
EX = os.environ.get("ABICHECK_VALIDATION_LIBS", str(VALID_DIR / "libs" / "ex"))
OUT = str(DATA)
MANIFEST = DATA / "manifest.json"
SCHEMA_VERSION = "run_matrix.v2"
BREAKING_VERDICTS = {"BREAKING", "API_BREAK"}
COMPATIBLE_VERDICTS = {"COMPATIBLE", "COMPATIBLE_WITH_RISK", "NO_CHANGE"}

# Share the logical-library-name helper with the unified harness (validate.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conda_harness import logical_name  # noqa: E402


def platform_tag() -> str:
    """Return the platform tag used in remeasurement records."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def real_sos(pkgdir: str, ex_root: str = EX) -> dict[str, str]:
    """logical_name -> path for every real (non-symlink) ELF .so in pkgdir/lib."""
    out = {}
    for p in glob.glob(f"{ex_root}/{pkgdir}/lib/*.so*"):
        if os.path.islink(p):
            continue
        try:
            with open(p, "rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        out[logical_name(p)] = p
    return out


def has_dwarf(path: str) -> bool:
    """Return whether an ELF binary appears to contain DWARF debug info."""
    r = subprocess.run(["readelf", "-S", path], capture_output=True, text=True)
    return "debug_info" in r.stdout


def run(cmd: list[str]) -> tuple[int, str, str, float]:
    """Run a subprocess and return exit code, stdout, stderr, and seconds."""
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr, time.time() - t


def side_layers(side: str) -> list[str]:
    """Map a side evidence mode to the available source layers."""
    layers = ["L0"]
    if side == "dwarf":
        layers.append("L1")
    return layers


def evidence_asymmetry(mode: str) -> str:
    """Classify old/new evidence richness for a mode string."""
    old_side, new_side = mode.split("->", 1)
    old_layers = side_layers(old_side)
    new_layers = side_layers(new_side)
    if old_layers == new_layers:
        return "symmetric"
    if len(old_layers) > len(new_layers):
        return "old-rich/new-poor"
    return "old-poor/new-rich"


def normalize_verdict(verdict: str | None) -> str:
    """Collapse abicheck verdicts to the manifest's compatibility axis."""
    normalized = (verdict or "").strip().upper()
    if normalized in BREAKING_VERDICTS:
        return "BREAKING"
    if normalized in COMPATIBLE_VERDICTS:
        return "COMPATIBLE"
    return "UNKNOWN"


def comparison_status(expected: str | None, actual: str | None) -> str:
    """Score a measured verdict against the curated manifest expectation."""
    expected_norm = normalize_verdict(expected)
    actual_norm = normalize_verdict(actual)
    if expected_norm == "UNKNOWN" or actual_norm == "UNKNOWN":
        return "UNCOMPARABLE"
    if expected_norm == actual_norm:
        return "MATCH"
    if actual_norm == "BREAKING" and expected_norm == "COMPATIBLE":
        return "ABICHECK_STRICTER"
    return "ABICHECK_WEAKER"


def make_record(
    manifest_row: dict,
    *,
    logical: str,
    old_path: str,
    new_path: str,
    mode: str,
    exit_code: int,
    seconds: float,
    stderr: str,
    data: dict | None,
) -> dict:
    """Build a versioned per-comparison remeasurement record."""
    old_side, new_side = mode.split("->", 1)
    rec = {
        "schema_version": SCHEMA_VERSION,
        "component": "real-world-matrix",
        "case_id": f"{manifest_row['pair']}__{logical}",
        "tag": f"{manifest_row['pair']}__{logical}",
        "pair": manifest_row["pair"],
        "library": manifest_row["library"],
        "logical": logical,
        "platform": platform_tag(),
        "old_ver": manifest_row["old_ver"],
        "new_ver": manifest_row["new_ver"],
        "expectation": manifest_row["expectation"],
        "expected": manifest_row["expectation"],
        "note": manifest_row["note"],
        "mode": mode,
        "source_layers": sorted(set(side_layers(old_side)) | set(side_layers(new_side))),
        "old_source_layers": side_layers(old_side),
        "new_source_layers": side_layers(new_side),
        "evidence_asymmetry": evidence_asymmetry(mode),
        "old_path": old_path,
        "new_path": new_path,
        "exit_code": exit_code,
        "seconds": round(seconds, 2),
        "stderr": stderr.strip(),
    }
    if data:
        summ = data.get("summary", {}) if isinstance(data, dict) else {}
        rec["verdict"] = data.get("verdict") or summ.get("verdict")
        rec["got"] = rec["verdict"]
        rec["counts"] = {
            k: summ.get(k)
            for k in (
                "total",
                "breaking",
                "api_break",
                "compatible",
                "risk",
                "added",
                "removed",
                "changed",
            )
            if k in summ
        }
        rec["release_recommendation"] = data.get("release_recommendation")
        if "layer_coverage" in data:
            rec["layer_coverage"] = data["layer_coverage"]
    rec["normalized_expected"] = normalize_verdict(rec.get("expected"))
    rec["normalized_got"] = normalize_verdict(rec.get("got"))
    rec["comparison_status"] = comparison_status(rec.get("expected"), rec.get("got"))
    return rec


def run_matrix(manifest: list[dict], *, ex_root: str = EX, out_dir: str = OUT) -> list[dict]:
    """Run all manifest comparisons and return per-library records."""
    results = []
    os.makedirs(f"{out_dir}/runs", exist_ok=True)  # per-library JSONs (gitignored)
    for m in manifest:
        old_dir = m["old_file"].replace(".conda", "").replace(".tar.bz2", "")
        new_dir = m["new_file"].replace(".conda", "").replace(".tar.bz2", "")
        old = real_sos(old_dir, ex_root)
        new = real_sos(new_dir, ex_root)
        common = sorted(set(old) & set(new))
        for lname in common:
            op, np = old[lname], new[lname]
            mode = (
                ("dwarf" if has_dwarf(op) else "sym")
                + "->"
                + ("dwarf" if has_dwarf(np) else "sym")
            )
            tag = f"{m['pair']}__{lname}"
            jpath = f"{out_dir}/runs/{tag}.json"
            cmd = [
                "abicheck",
                "compare",
                op,
                np,
                "--old-version",
                m["old_ver"],
                "--new-version",
                m["new_ver"],
                "--format",
                "json",
                "-o",
                jpath,
                "--recommend",
            ]
            rc, _stdout, se, dt = run(cmd)
            data = None
            if os.path.exists(jpath):
                try:
                    with open(jpath, encoding="utf-8") as fh:
                        data = json.load(fh)
                except Exception as e:
                    se += f"\n[harness] json parse failed: {e}"
            rec = make_record(
                m,
                logical=lname,
                old_path=op,
                new_path=np,
                mode=mode,
                exit_code=rc,
                seconds=dt,
                stderr=se,
                data=data,
            )
            results.append(rec)
            print(
                f"{tag:48} {mode:11} exit={rc} {dt:5.1f}s "
                f"verdict={rec.get('verdict')}"
            )
    return results


def make_run_metadata(results: list[dict], manifest: list[dict]) -> dict:
    """Build run-level metadata for a real-world matrix run."""
    return {
        "schema_version": SCHEMA_VERSION,
        "runner": "validation/scripts/run_matrix.py",
        "platform": platform_tag(),
        "command": [sys.executable, *sys.argv],
        "manifest_pairs": len(manifest),
        "comparisons": len(results),
        "modes": sorted({str(r.get("mode", "")) for r in results}),
        "comparison_status_counts": {
            status: sum(
                1
                for record in results
                if record.get("comparison_status") == status
            )
            for status in sorted({str(r.get("comparison_status", "")) for r in results})
        },
        "results_file": "validation/data/results.json",
    }


def main() -> int:
    """CLI entrypoint for the curated real-world matrix runner."""
    if not MANIFEST.is_file():
        print(f"manifest not found: {MANIFEST}", file=sys.stderr)
        return 2
    if not os.path.isdir(EX):
        print(
            f"extracted-libraries dir not found: {EX}\n"
            "Fetch+extract the packages in data/manifest.json (binaries are not "
            "committed), then set ABICHECK_VALIDATION_LIBS to their parent directory.",
            file=sys.stderr,
        )
        return 2

    with open(MANIFEST, encoding="utf-8") as fh:
        manifest = json.load(fh)
    results = run_matrix(manifest)
    with open(f"{OUT}/results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    with open(f"{OUT}/results.meta.json", "w", encoding="utf-8") as fh:
        json.dump(make_run_metadata(results, manifest), fh, indent=2)
    print(f"\n{len(results)} comparisons -> {OUT}/results.json")
    print(f"Run metadata -> {OUT}/results.meta.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

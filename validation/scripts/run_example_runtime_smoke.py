# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""Build and run example consumer apps without abicheck analysis.

This runtime smoke layer is intentionally separate from
``tests/validate_examples.py``.  The validate runner answers "what verdict does
abicheck produce for built artifacts?".  This script answers the simpler runtime
question: "does the old consumer app still run when libv2 is substituted for
libv1?".
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_DIR / "examples"
GROUND_TRUTH = EXAMPLES_DIR / "ground_truth.json"
SCHEMA_VERSION = "example_runtime_smoke.v1"


def _platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def _lib_suffix() -> str:
    if sys.platform == "darwin":
        return ".dylib"
    if sys.platform == "win32":
        return ".dll"
    return ".so"


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _configure(build_dir: Path, build_type: str) -> str | None:
    cmake = shutil.which("cmake")
    if not cmake:
        return "cmake not found"
    result = _run(
        [
            cmake,
            "-S",
            str(EXAMPLES_DIR),
            "-B",
            str(build_dir),
            f"-DCMAKE_BUILD_TYPE={build_type}",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ],
        timeout=90,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[:800]
        return f"cmake configure failed: {detail}"
    return None


def _case_out(build_dir: Path, case_name: str) -> Path:
    return build_dir / case_name


def _app_path(case_out: Path) -> Path | None:
    names = ("app_v1.exe", "app_v1") if sys.platform == "win32" else ("app_v1",)
    for name in names:
        candidate = case_out / name
        if candidate.exists():
            return candidate
    return None


def _lib_paths(case_out: Path) -> tuple[Path | None, Path | None]:
    suffix = _lib_suffix()
    v1 = case_out / f"libv1{suffix}"
    v2 = case_out / f"libv2{suffix}"
    return v1 if v1.exists() else None, v2 if v2.exists() else None


def _soname(lib: Path) -> str | None:
    readelf = shutil.which("readelf")
    if not readelf or sys.platform != "linux":
        return None
    result = _run([readelf, "-d", str(lib)], timeout=10)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "(SONAME)" not in line:
            continue
        start = line.find("[")
        end = line.find("]", start + 1)
        if start != -1 and end != -1:
            return line[start + 1:end]
    return None


def _ensure_soname_aliases(run_dir: Path) -> None:
    """Create local SONAME aliases so old apps load their linked library."""
    for lib in run_dir.glob("libv*.*"):
        if not lib.is_file():
            continue
        soname = _soname(lib)
        if not soname:
            continue
        alias = run_dir / soname
        if not alias.exists():
            try:
                alias.symlink_to(lib.name)
            except OSError:
                shutil.copy2(lib, alias)


def _runtime_env(run_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    if sys.platform == "darwin":
        key = "DYLD_LIBRARY_PATH"
    elif sys.platform == "win32":
        key = "PATH"
    else:
        key = "LD_LIBRARY_PATH"
    existing = env.get(key)
    env[key] = str(run_dir) if not existing else f"{run_dir}{os.pathsep}{existing}"
    return env


def _run_app(app: Path, run_dir: Path) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result = _run(
            [str(app)],
            cwd=run_dir,
            env=_runtime_env(run_dir),
            timeout=10,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "seconds": round(time.perf_counter() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "seconds": round(time.perf_counter() - started, 3),
            "timeout": True,
        }


def _copy_runtime_tree(src_dir: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, dst_dir / item.name)


def _classify_runtime_signal(baseline: dict[str, object], swapped: dict[str, object]) -> str:
    if swapped.get("timeout"):
        return "timeout"
    if swapped.get("returncode") not in (0, None):
        return "nonzero"
    if baseline.get("stdout") != swapped.get("stdout"):
        return "stdout_changed"
    if baseline.get("stderr") != swapped.get("stderr"):
        return "stderr_changed"
    return "no_runtime_signal"


def _build_case(build_dir: Path, build_type: str, case_name: str) -> str | None:
    cmake = shutil.which("cmake")
    if not cmake:
        return "cmake not found"
    result = _run(
        [
            cmake,
            "--build",
            str(build_dir),
            "--target",
            f"{case_name}_app",
            f"{case_name}_v2",
            "--config",
            build_type,
        ],
        timeout=180,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[:800]
        return f"cmake build failed: {detail}"
    return None


def _skip_reason(case_name: str, entry: dict[str, object]) -> str | None:
    if entry.get("skip"):
        return str(entry.get("reason", "skip=true"))
    if entry.get("category") == "bundle" or entry.get("bundle") is True:
        return "bundle case — runtime smoke uses single-library app/v1/v2 layout"
    platforms = entry.get("platforms", ["linux", "macos", "windows"])
    if _platform() not in platforms:
        return f"not supported on {_platform()} (requires {platforms})"
    if not (EXAMPLES_DIR / case_name / "CMakeLists.txt").exists():
        return "no CMakeLists.txt"
    if entry.get("requires_feature") == "_BitInt":
        return "compiler lacks required feature '_BitInt'"
    return None


def run_case(
    *,
    build_dir: Path,
    build_type: str,
    case_name: str,
    entry: dict[str, object],
    tmp_root: Path,
) -> dict[str, object]:
    expected = entry.get("expected")
    started = time.perf_counter()
    skip = _skip_reason(case_name, entry)
    if skip:
        return {
            "case_id": case_name,
            "status": "SKIP",
            "expected": expected,
            "message": skip,
            "seconds": round(time.perf_counter() - started, 3),
        }

    build_err = _build_case(build_dir, build_type, case_name)
    if build_err:
        return {
            "case_id": case_name,
            "status": "BUILD_ERROR",
            "expected": expected,
            "message": build_err,
            "seconds": round(time.perf_counter() - started, 3),
        }

    out = _case_out(build_dir, case_name)
    _ensure_soname_aliases(out)
    app = _app_path(out)
    v1, v2 = _lib_paths(out)
    if app is None or v1 is None or v2 is None:
        return {
            "case_id": case_name,
            "status": "BUILD_ERROR",
            "expected": expected,
            "message": f"missing runtime artifacts in {out}",
            "seconds": round(time.perf_counter() - started, 3),
        }

    baseline = _run_app(app, out)
    if baseline.get("returncode") != 0:
        return {
            "case_id": case_name,
            "status": "BASELINE_SIGNAL",
            "expected": expected,
            "message": "old app exited non-zero with libv1",
            "baseline": baseline,
            "seconds": round(time.perf_counter() - started, 3),
        }

    swap_dir = tmp_root / case_name
    _copy_runtime_tree(out, swap_dir)
    _ensure_soname_aliases(swap_dir)
    swap_v1, swap_v2 = _lib_paths(swap_dir)
    if swap_v1 is None or swap_v2 is None:
        return {
            "case_id": case_name,
            "status": "BUILD_ERROR",
            "expected": expected,
            "message": f"missing copied runtime libraries in {swap_dir}",
            "seconds": round(time.perf_counter() - started, 3),
        }
    shutil.copy2(swap_v2, swap_v1)
    swapped_app = _app_path(swap_dir)
    assert swapped_app is not None
    swapped = _run_app(swapped_app, swap_dir)
    signal = _classify_runtime_signal(baseline, swapped)
    status = "DEMONSTRATED" if signal != "no_runtime_signal" else "NO_RUNTIME_SIGNAL"
    return {
        "case_id": case_name,
        "status": status,
        "expected": expected,
        "runtime_signal": signal,
        "baseline": baseline,
        "swapped": swapped,
        "seconds": round(time.perf_counter() - started, 3),
    }


def _counts(results: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filters", nargs="*", help="Case-name substrings to run")
    parser.add_argument("--build-type", default="Debug", choices=("Debug", "Release"))
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args(argv)

    with GROUND_TRUTH.open(encoding="utf-8") as f:
        truth = json.load(f)["verdicts"]
    names = sorted(truth)
    if args.filters:
        names = [n for n in names if any(token in n for token in args.filters)]

    with tempfile.TemporaryDirectory(prefix="example_runtime_smoke_") as tmp:
        tmp_root = Path(tmp)
        build_dir = tmp_root / "build"
        swap_root = tmp_root / "swap"
        configure_err = _configure(build_dir, args.build_type)
        if configure_err:
            print(f"ERROR: {configure_err}", file=sys.stderr)
            return 2
        results = [
            run_case(
                build_dir=build_dir,
                build_type=args.build_type,
                case_name=name,
                entry=truth[name],
                tmp_root=swap_root,
            )
            for name in names
        ]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "runner": "validation/scripts/run_example_runtime_smoke.py",
        "platform": _platform(),
        "command": [sys.executable, "validation/scripts/run_example_runtime_smoke.py", *(argv or sys.argv[1:])],
        "build_type": args.build_type,
        "selected_cases": len(names),
        "ground_truth_cases": len(truth),
        "summary": _counts(results),
        "results": results,
    }
    if args.json_out:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload["summary"], indent=2))
        for result in results:
            if result["status"] not in {"DEMONSTRATED", "NO_RUNTIME_SIGNAL", "SKIP"}:
                print(f"{result['status']}: {result['case_id']} {result.get('message', '')}", file=sys.stderr)

    return 1 if any(r["status"] == "BUILD_ERROR" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

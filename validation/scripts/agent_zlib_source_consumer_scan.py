#!/usr/bin/env python3
"""One-off real zlib source/consumer scan for main 5465a861.

This file lives only on an isolated ops branch.  It does not modify product code
and writes all evidence below scan-results-zlib-source-v3/.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
OUT = ROOT / "scan-results-zlib-source-v3"
WORK = ROOT / ".scan-inputs" / "zlib-source-v3"
LOGS = OUT / "logs"
SNAPSHOTS = OUT / "snapshots"
REPORTS = OUT / "reports"
RUNTIME = OUT / "runtime"
BASE_SHA = "5465a8610e2a01f07abcd9f18ff93ecf2256114f"

for directory in (OUT, WORK, LOGS, SNAPSHOTS, REPORTS, RUNTIME):
    directory.mkdir(parents=True, exist_ok=True)

records: list[dict[str, Any]] = []


def run(
    label: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    started = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    elapsed = time.perf_counter() - started
    (LOGS / f"{label}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (LOGS / f"{label}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    record = {
        "label": label,
        "command": command,
        "cwd": str(cwd) if cwd else str(ROOT),
        "return_code": proc.returncode,
        "seconds": round(elapsed, 3),
        "stdout_bytes": len(proc.stdout.encode()),
        "stderr_bytes": len(proc.stderr.encode()),
    }
    records.append(record)
    print(f"[{label}] rc={proc.returncode} seconds={elapsed:.3f}", flush=True)
    if proc.stdout:
        print(proc.stdout[-2000:], flush=True)
    if proc.stderr:
        print(proc.stderr[-2000:], file=sys.stderr, flush=True)
    return proc


def download(url: str, target: Path) -> str:
    if not target.is_file():
        with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return digest


def find_real_library(build_dir: Path, version: str) -> Path:
    exact = build_dir / f"libz.so.{version}"
    if exact.is_file():
        return exact.resolve()
    candidates = [
        path.resolve()
        for path in build_dir.rglob("libz.so.*")
        if path.is_file() and not path.is_symlink()
    ]
    if not candidates:
        raise RuntimeError(f"No real libz shared object found below {build_dir}")
    return max(candidates, key=lambda path: path.stat().st_size)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def walk(value: Any, path: str = ""):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def graph_inventory(snapshot: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path, value in walk(snapshot):
        if not isinstance(value, dict):
            continue
        nodes = value.get("nodes")
        edges = value.get("edges")
        if not isinstance(nodes, (list, dict)) or not isinstance(edges, list):
            continue
        kinds: collections.Counter[str] = collections.Counter()
        for edge in edges:
            if isinstance(edge, dict):
                kinds[str(edge.get("kind") or edge.get("edge_kind"))] += 1
        found.append(
            {
                "path": path,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "edge_kinds": dict(kinds),
                "graph_id": value.get("graph_id"),
                "coverage": value.get("coverage"),
                "extractor_passes": value.get("extractor_passes"),
                "degraded_passes": value.get("degraded_passes"),
            }
        )
    return found


def report_summary(path: Path) -> dict[str, Any]:
    report = load_json(path)
    if not isinstance(report, dict):
        return {"present": False}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    changes = report.get("changes") or report.get("findings") or []
    if not isinstance(changes, list):
        changes = []
    changes = [item for item in changes if isinstance(item, dict)]
    gzwrite = [item for item in changes if "gzwrite" in json.dumps(item, sort_keys=True)]
    return {
        "present": True,
        "verdict": report.get("verdict") or summary.get("verdict"),
        "full_verdict": report.get("full_verdict"),
        "finding_count": len(changes),
        "by_kind": dict(collections.Counter(str(item.get("kind")) for item in changes)),
        "by_severity": dict(collections.Counter(str(item.get("severity")) for item in changes)),
        "used_by": report.get("used_by"),
        "gzwrite_findings": gzwrite,
        "top_level_keys": sorted(report),
    }


try:
    provenance = run("abicheck-version", ["abicheck", "--version"])
    run("clang-version", ["clang", "--version"])
    run("castxml-version", ["castxml", "--version"])
    (OUT / "provenance.json").write_text(
        json.dumps(
            {
                "base_main_sha": BASE_SHA,
                "workflow_head_sha": os.environ.get("GITHUB_SHA"),
                "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "abicheck_version": provenance.stdout.strip(),
                "python": sys.version,
                "platform": sys.platform,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    archives = {
        "1.3.1": (
            "https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz",
            WORK / "zlib-1.3.1.tar.gz",
        ),
        "1.3.2": (
            "https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz",
            WORK / "zlib-1.3.2.tar.gz",
        ),
    }
    hashes: dict[str, str] = {}
    for version, (url, archive) in archives.items():
        hashes[version] = download(url, archive)
        source_dir = WORK / f"zlib-{version}"
        if not source_dir.is_dir():
            with tarfile.open(archive, "r:gz") as handle:
                handle.extractall(WORK, filter="data")
    (OUT / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")

    old_src = WORK / "zlib-1.3.1"
    new_src = WORK / "zlib-1.3.2"
    fault_src = WORK / "zlib-1.3.2-fault"
    if fault_src.exists():
        shutil.rmtree(fault_src)
    shutil.copytree(new_src, fault_src)

    fault_file = fault_src / "gzwrite.c"
    text = fault_file.read_text(encoding="utf-8")
    old_definition = "int ZEXPORT gzwrite(gzFile file, voidpc buf, unsigned len) {"
    new_definition = "int ZEXPORT gzwrite_abicheck_control(gzFile file, voidpc buf, unsigned len) {"
    if text.count(old_definition) != 1:
        raise RuntimeError(f"Unexpected gzwrite definition count: {text.count(old_definition)}")
    fault_file.write_text(text.replace(old_definition, new_definition, 1), encoding="utf-8")
    (OUT / "control-fault.txt").write_text(
        "Official zlib 1.3.2 with only the gzwrite implementation renamed to "
        "gzwrite_abicheck_control; zlib.h is unchanged.\n",
        encoding="utf-8",
    )

    old_build = old_src / "build"
    new_build = new_src / "build"
    fault_build = fault_src / "build"
    for build_dir in (old_build, new_build, fault_build):
        if build_dir.exists():
            shutil.rmtree(build_dir)

    configure_old = run(
        "configure-old",
        [
            "cmake",
            "-S",
            str(old_src),
            "-B",
            str(old_build),
            "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            "-DBUILD_SHARED_LIBS=ON",
        ],
    )
    if configure_old.returncode != 0:
        raise RuntimeError("zlib 1.3.1 configure failed")
    if run("build-old", ["cmake", "--build", str(old_build), "-j2"]).returncode != 0:
        raise RuntimeError("zlib 1.3.1 build failed")

    for label, source, build in (
        ("new", new_src, new_build),
        ("fault", fault_src, fault_build),
    ):
        configured = run(
            f"configure-{label}",
            [
                "cmake",
                "-S",
                str(source),
                "-B",
                str(build),
                "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                "-DZLIB_BUILD_SHARED=ON",
                "-DZLIB_BUILD_STATIC=OFF",
                "-DZLIB_BUILD_TESTING=OFF",
            ],
        )
        if configured.returncode != 0:
            raise RuntimeError(f"zlib {label} configure failed")
        if run(f"build-{label}", ["cmake", "--build", str(build), "-j2"]).returncode != 0:
            raise RuntimeError(f"zlib {label} build failed")

    old_lib = find_real_library(old_build, "1.3.1")
    new_lib = find_real_library(new_build, "1.3.2")
    fault_lib = find_real_library(fault_build, "1.3.2")
    libraries = {"old": str(old_lib), "new": str(new_lib), "fault": str(fault_lib)}
    (OUT / "libraries.json").write_text(json.dumps(libraries, indent=2) + "\n")

    dynamic_symbols: dict[str, dict[str, Any]] = {}
    for label, library in (("old", old_lib), ("new", new_lib), ("fault", fault_lib)):
        proc = run(f"readelf-{label}", ["readelf", "--dyn-syms", "-W", str(library)])
        symbols = proc.stdout
        (RUNTIME / f"{label}-dynsyms.txt").write_text(symbols, encoding="utf-8")
        dynamic_symbols[label] = {
            "has_gzwrite": any(token.split("@", 1)[0] == "gzwrite" for token in symbols.split()),
            "has_control_symbol": any(
                token.split("@", 1)[0] == "gzwrite_abicheck_control"
                for token in symbols.split()
            ),
        }
    if not dynamic_symbols["old"]["has_gzwrite"] or not dynamic_symbols["new"]["has_gzwrite"]:
        raise RuntimeError("official old/new library does not export gzwrite")
    if dynamic_symbols["fault"]["has_gzwrite"]:
        raise RuntimeError("control fault still exports gzwrite")
    (OUT / "dynamic-symbol-check.json").write_text(
        json.dumps(dynamic_symbols, indent=2) + "\n"
    )

    consumer_source = WORK / "zlib-consumer.c"
    consumer = WORK / "zlib-consumer"
    consumer_source.write_text(
        """#include <zlib.h>\n#include <string.h>\n\n"
        "int main(void) {\n"
        "  const char payload[] = \"abicheck consumer graph validation\";\n"
        "  gzFile stream = gzopen(\"/tmp/abicheck-zlib-consumer.gz\", \"wb\");\n"
        "  if (stream == 0) return 10;\n"
        "  int written = gzwrite(stream, payload, (unsigned)strlen(payload));\n"
        "  int closed = gzclose(stream);\n"
        "  return (written == (int)strlen(payload) && closed == Z_OK) ? 0 : 11;\n"
        "}\n""",
        encoding="utf-8",
    )
    compile_consumer = run(
        "compile-consumer",
        [
            "cc",
            str(consumer_source),
            f"-I{old_src}",
            f"-I{old_build}",
            f"-L{old_lib.parent}",
            f"-Wl,-rpath,{old_lib.parent}",
            "-Wl,-z,now",
            "-lz",
            "-o",
            str(consumer),
        ],
    )
    if compile_consumer.returncode != 0:
        raise RuntimeError("consumer compile failed")
    if run("consumer-old-runtime", [str(consumer)]).returncode != 0:
        raise RuntimeError("consumer did not run against official old zlib")
    consumer_symbols = run(
        "readelf-consumer", ["readelf", "--dyn-syms", "-W", str(consumer)]
    )
    (RUNTIME / "consumer-dynsyms.txt").write_text(
        consumer_symbols.stdout, encoding="utf-8"
    )
    if "gzwrite" not in consumer_symbols.stdout:
        raise RuntimeError("consumer binary does not import gzwrite")

    fault_env = os.environ.copy()
    fault_env["LD_LIBRARY_PATH"] = str(fault_lib.parent)
    fault_runtime = run("consumer-fault-runtime", [str(consumer)], env=fault_env)
    (OUT / "fault-runtime-resolution.json").write_text(
        json.dumps(
            {
                "return_code": fault_runtime.returncode,
                "note": (
                    "Recorded diagnostically only. The consumer graph is proven by its imported "
                    "gzwrite symbol and the fault library's missing export; loader path precedence "
                    "may still resolve the original build-RPATH library."
                ),
            },
            indent=2,
        )
        + "\n"
    )

    dump_specs = (
        ("old", old_src, old_build, old_lib, "1.3.1"),
        ("new", new_src, new_build, new_lib, "1.3.2"),
        ("fault", fault_src, fault_build, fault_lib, "1.3.2-control"),
    )
    for label, source, build, library, version in dump_specs:
        run(
            f"dump-{label}",
            [
                "abicheck",
                "dump",
                str(library),
                "-H",
                str(source / "zlib.h"),
                "-H",
                str(build / "zconf.h"),
                "--sources",
                str(source),
                "--compile-db",
                str(build / "compile_commands.json"),
                "--depth",
                "source",
                "--lang",
                "c",
                "--ast-frontend",
                "clang",
                "--version",
                version,
                "-o",
                str(SNAPSHOTS / f"{label}.json"),
            ],
        )

    if (SNAPSHOTS / "old.json").is_file() and (SNAPSHOTS / "new.json").is_file():
        run(
            "compare-real-release",
            [
                "abicheck",
                "compare",
                str(SNAPSHOTS / "old.json"),
                str(SNAPSHOTS / "new.json"),
                "--format",
                "json",
                "-o",
                str(REPORTS / "zlib-1.3.1-to-1.3.2-source.json"),
            ],
        )

    if (SNAPSHOTS / "old.json").is_file() and (SNAPSHOTS / "fault.json").is_file():
        run(
            "compare-fault-snapshot-used-by",
            [
                "abicheck",
                "compare",
                str(SNAPSHOTS / "old.json"),
                str(SNAPSHOTS / "fault.json"),
                "--used-by",
                str(consumer),
                "--verify-runtime",
                "--format",
                "json",
                "-o",
                str(REPORTS / "zlib-consumer-fault-snapshot.json"),
            ],
        )

    run(
        "compare-fault-real-binaries-used-by",
        [
            "abicheck",
            "compare",
            str(old_lib),
            str(fault_lib),
            "--header",
            f"old={old_src / 'zlib.h'}",
            "--header",
            f"old={old_build / 'zconf.h'}",
            "--header",
            f"new={fault_src / 'zlib.h'}",
            "--header",
            f"new={fault_build / 'zconf.h'}",
            "--sources",
            f"old={old_src}",
            "--sources",
            f"new={fault_src}",
            "--depth",
            "source",
            "--lang",
            "c",
            "--used-by",
            str(consumer),
            "--verify-runtime",
            "--format",
            "json",
            "-o",
            str(REPORTS / "zlib-consumer-fault-binary.json"),
        ],
    )

    snapshots = {
        label: {
            "present": (SNAPSHOTS / f"{label}.json").is_file(),
            "bytes": (SNAPSHOTS / f"{label}.json").stat().st_size
            if (SNAPSHOTS / f"{label}.json").is_file()
            else 0,
            "graphs": graph_inventory(load_json(SNAPSHOTS / f"{label}.json")),
        }
        for label in ("old", "new", "fault")
    }
    comparisons = {
        "real_release": report_summary(REPORTS / "zlib-1.3.1-to-1.3.2-source.json"),
        "fault_snapshot_used_by": report_summary(
            REPORTS / "zlib-consumer-fault-snapshot.json"
        ),
        "fault_real_binaries_used_by": report_summary(
            REPORTS / "zlib-consumer-fault-binary.json"
        ),
    }
    final = {
        "schema": "agent-zlib-source-consumer-scan.v3",
        "base_sha": BASE_SHA,
        "source_pair": "official zlib 1.3.1 -> 1.3.2",
        "source_hashes": hashes,
        "control_fault": (
            "official zlib 1.3.2 implementation renamed from gzwrite to "
            "gzwrite_abicheck_control while the public zlib.h declaration remains unchanged"
        ),
        "dynamic_symbols": dynamic_symbols,
        "consumer_imports_gzwrite": "gzwrite" in consumer_symbols.stdout,
        "fault_runtime_return_code": fault_runtime.returncode,
        "snapshots": snapshots,
        "comparisons": comparisons,
        "commands": records,
    }
    (OUT / "zlib-source-results.json").write_text(
        json.dumps(final, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# zlib real source + consumer graph scan",
        "",
        f"- Base main SHA: `{BASE_SHA}`",
        "- Source pair: official zlib `1.3.1 -> 1.3.2`",
        f"- Consumer imports gzwrite: `{final['consumer_imports_gzwrite']}`",
        f"- Fault library exports gzwrite: `{dynamic_symbols['fault']['has_gzwrite']}`",
        "",
        "## Source snapshots",
        "",
        "| Side | Present | Bytes | Graphs | Max nodes | Max edges |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, item in snapshots.items():
        graphs = item["graphs"]
        lines.append(
            f"| {label} | {item['present']} | {item['bytes']} | {len(graphs)} | "
            f"{max((graph['node_count'] for graph in graphs), default=0)} | "
            f"{max((graph['edge_count'] for graph in graphs), default=0)} |"
        )
    lines.extend(
        [
            "",
            "## Comparisons",
            "",
            "| Scenario | Present | Verdict | Full verdict | Findings | gzwrite findings |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, item in comparisons.items():
        lines.append(
            f"| {name} | {item.get('present')} | {item.get('verdict')} | "
            f"{item.get('full_verdict')} | {item.get('finding_count')} | "
            f"{len(item.get('gzwrite_findings', []))} |"
        )
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
except BaseException:
    import traceback

    failure = traceback.format_exc()
    (OUT / "fatal-error.txt").write_text(failure, encoding="utf-8")
    (OUT / "commands.json").write_text(json.dumps(records, indent=2) + "\n")
    print(failure, file=sys.stderr)
    raise
finally:
    (OUT / "commands.json").write_text(json.dumps(records, indent=2) + "\n")

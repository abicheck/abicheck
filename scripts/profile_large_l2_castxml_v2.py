#!/usr/bin/env python3
"""Temporary large-L2/CastXML profiler for abicheck current main."""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PHASES = ("functions", "variables", "types", "enums", "typedefs", "constants")
TIMEOUT = 240


@dataclass
class Subject:
    name: str
    family: str
    n: int | None
    lang: str
    compiler: str
    old_h: Path
    new_h: Path
    old_so: Path
    new_so: Path
    includes: list[Path] = field(default_factory=list)
    public_dirs: list[Path] = field(default_factory=list)
    starts: list[str] = field(default_factory=list)


def med(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def call(fn):
    t = time.perf_counter()
    try:
        return fn(), time.perf_counter() - t, None
    except Exception as e:
        return None, time.perf_counter() - t, f"{type(e).__name__}: {e}"


def run(cmd: list[str], *, env=None, timeout=TIMEOUT) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(delete=False) as f:
        tf = Path(f.name)
    full = ["/usr/bin/time", "-f", "wall=%e\nuser=%U\nsys=%S\nrss=%M", "-o", str(tf), *cmd]
    t = time.perf_counter()
    try:
        p = subprocess.run(full, capture_output=True, env=env, timeout=timeout, check=False)
        meta: dict[str, Any] = {}
        for line in tf.read_text(errors="replace").splitlines():
            k, _, v = line.partition("=")
            try:
                meta[k] = float(v)
            except ValueError:
                meta[k] = v
        return {
            "rc": p.returncode,
            "elapsed": time.perf_counter() - t,
            "time": meta,
            "stderr": p.stderr.decode(errors="replace")[-2000:],
        }
    except subprocess.TimeoutExpired as e:
        return {"rc": None, "elapsed": time.perf_counter() - t, "timeout": True, "stderr": str(e)}
    finally:
        tf.unlink(missing_ok=True)


def compile_so(src: Path, out: Path, cpp: bool, includes: list[Path]) -> dict[str, Any]:
    cmd = ["g++" if cpp else "gcc", "-shared", "-fPIC", "-g", "-O0"]
    for inc in includes:
        cmd += ["-I", str(inc)]
    cmd += ["-o", str(out), str(src)]
    return run(cmd)


def flat_c(root: Path, n: int) -> Subject:
    d = root / f"flat_c_{n}"
    d.mkdir()
    oh, nh, oc, nc = d / "old.h", d / "new.h", d / "old.c", d / "new.c"
    oso, nso = d / "old.so", d / "new.so"

    def hdr(new: bool) -> str:
        a = ["#pragma once"]
        for i in range(n):
            ret = "long" if new and i == n - 1 else "int"
            arg = "long" if new and i == n - 1 else "int"
            a.append(f"{ret} api_{i}({arg} x);")
        for i in range(max(1, n // 10)):
            a.append(f"typedef struct R{i} {{ int a; long b; char x[{i % 31 + 1}]; }} R{i};")
        return "\n".join(a) + "\n"

    oh.write_text(hdr(False))
    nh.write_text(hdr(True))
    oc.write_text('#include "old.h"\n' + "\n".join(f"int api_{i}(int x){{return x+{i};}}" for i in range(n)))
    nc.write_text(
        '#include "new.h"\n'
        + "\n".join(
            f"long api_{i}(long x){{return x+{i};}}"
            if i == n - 1
            else f"int api_{i}(int x){{return x+{i};}}"
            for i in range(n)
        )
    )
    compile_so(oc, oso, False, [d])
    compile_so(nc, nso, False, [d])
    return Subject(f"flat_c_{n}", "flat_c", n, "c", "cc", oh, nh, oso, nso, [d], [d])


def cpp_records(root: Path, n: int) -> Subject:
    d = root / f"cpp_records_{n}"
    d.mkdir()
    oh, nh, oc, nc = d / "old.hpp", d / "new.hpp", d / "old.cpp", d / "new.cpp"
    oso, nso = d / "old.so", d / "new.so"

    def hdr(new: bool) -> str:
        a = [
            "#pragma once",
            "#include <array>",
            "#include <optional>",
            "#include <string>",
            "#include <vector>",
            "namespace bench {",
        ]
        for i in range(n):
            scalar = "long" if new and i == n - 1 else "int"
            a += [
                f"struct API_{i} {{",
                f"std::array<int,{i % 16 + 1}> fixed;",
                "std::vector<int> values;",
                "std::optional<std::string> label;",
                f"{scalar} state;",
                "};",
            ]
        a += ["}", 'extern "C" int bench_anchor();']
        return "\n".join(a) + "\n"

    oh.write_text(hdr(False))
    nh.write_text(hdr(True))
    oc.write_text('#include "old.hpp"\nextern "C" int bench_anchor(){return 1;}\n')
    nc.write_text('#include "new.hpp"\nextern "C" int bench_anchor(){return 2;}\n')
    compile_so(oc, oso, True, [d])
    compile_so(nc, nso, True, [d])
    return Subject(
        f"cpp_records_{n}",
        "cpp_records",
        n,
        "c++",
        "c++",
        oh,
        nh,
        oso,
        nso,
        [d],
        [d],
        ["bench", "bench_anchor"],
    )


def first_file(patterns: list[str]) -> Path | None:
    for pat in patterns:
        for p in sorted(Path("/").glob(pat.lstrip("/"))):
            if p.is_file():
                return p
    return None


def umbrella(d: Path, include: str, ext: str = "hpp") -> tuple[Path, Path]:
    d.mkdir()
    a, b = d / f"old.{ext}", d / f"new.{ext}"
    a.write_text(f"#pragma once\n#include <{include}>\n")
    b.write_text(a.read_text())
    return a, b


def real_subjects(root: Path) -> list[Subject]:
    out: list[Subject] = []
    ssl = first_file(["/usr/lib/x86_64-linux-gnu/libssl.so", "/usr/lib/*/libssl.so"])
    if ssl and Path("/usr/include/openssl/ssl.h").exists():
        d = root / "openssl"
        oh, nh = umbrella(d, "openssl/ssl.h", "h")
        out.append(
            Subject(
                "openssl_ssl",
                "real",
                None,
                "c",
                "cc",
                oh,
                nh,
                ssl,
                ssl,
                [],
                [Path("/usr/include/openssl")],
                ["SSL", "SSL_CTX"],
            )
        )
    tbb = first_file(["/usr/lib/x86_64-linux-gnu/libtbb.so", "/usr/lib/*/libtbb.so"])
    if tbb and Path("/usr/include/oneapi/tbb.h").exists():
        d = root / "tbb"
        oh, nh = umbrella(d, "oneapi/tbb.h")
        out.append(
            Subject(
                "onetbb",
                "real",
                None,
                "c++",
                "c++",
                oh,
                nh,
                tbb,
                tbb,
                [],
                [Path("/usr/include/oneapi")],
                ["oneapi::tbb", "tbb"],
            )
        )
    er = Path("/usr/include/eigen3")
    if (er / "Eigen/Dense").exists():
        d = root / "eigen"
        oh, nh = umbrella(d, "Eigen/Dense")
        oc, nc = d / "old.cpp", d / "new.cpp"
        oso, nso = d / "old.so", d / "new.so"
        oc.write_text(
            '#include "old.hpp"\nextern "C" int eigen_anchor(){Eigen::Matrix2d m=Eigen::Matrix2d::Identity();return (int)m(0,0);}\n'
        )
        nc.write_text(
            '#include "new.hpp"\nextern "C" int eigen_anchor(){Eigen::Matrix2d m=Eigen::Matrix2d::Identity();return (int)m(1,1);}\n'
        )
        compile_so(oc, oso, True, [d, er])
        compile_so(nc, nso, True, [d, er])
        out.append(
            Subject(
                "eigen_dense",
                "real",
                None,
                "c++",
                "c++",
                oh,
                nh,
                oso,
                nso,
                [er],
                [er / "Eigen"],
                ["Eigen", "eigen_anchor"],
            )
        )
    return out


def dump_pair(s: Subject, root: Path):
    from abicheck import checker, dumper

    cache = root / "cache"
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir()
    old = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(cache)

    def one(lib: Path, h: Path, ver: str):
        return dumper.dump(
            lib,
            [h],
            s.includes,
            ver,
            s.compiler,
            lang="C++" if s.lang == "c++" else "C",
            public_headers=[h],
            public_header_dirs=s.public_dirs,
            header_backend="castxml",
        )

    try:
        osnap, co, eo = call(lambda: one(s.old_so, s.old_h, "old"))
        _, wo, ewo = call(lambda: one(s.old_so, s.old_h, "old"))
        nsnap, cn, en = call(lambda: one(s.new_so, s.new_h, "new"))
        _, wn, ewn = call(lambda: one(s.new_so, s.new_h, "new"))
    finally:
        if old is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = old
    result = {"cold_pair": co + cn, "warm_pair": wo + wn, "errors": [x for x in (eo, ewo, en, ewn) if x]}
    if osnap is not None and nsnap is not None:
        vals = []
        first, fs, _ = call(lambda: checker.compare(osnap, nsnap, scope_to_public_surface=True))
        for _ in range(3):
            _, x, e = call(lambda: checker.compare(osnap, nsnap, scope_to_public_surface=True))
            if not e:
                vals.append(x)
        result.update(
            compare_first=fs,
            compare_steady=med(vals),
            verdict=str(first.verdict) if first else None,
            changes=len(first.changes) if first else None,
            old_counts={p: len(getattr(osnap, p)) for p in PHASES},
        )
    return result, osnap


def castxml_cmd(s: Subject, agg: Path, out: Path, binary: str, starts=None):
    from abicheck import dumper

    cc, cid = dumper._resolve_compiler_binary(s.compiler, None, None)
    cmd = dumper._build_castxml_command(
        cc,
        cid,
        s.includes,
        out,
        agg,
        force_cpp=s.lang == "c++",
        force_cpp20=False,
    )
    cmd[0] = binary
    if starts:
        cmd[1:1] = ["--castxml-start", ",".join(starts)]
    return cmd


def parse_model(s: Subject, xml: Path, baseline):
    from abicheck import checker, dumper
    from defusedxml import ElementTree as ET

    root, xs, xe = call(lambda: ET.parse(str(xml)).getroot())
    result = {"xml_seconds": xs, "xml_bytes": xml.stat().st_size if xml.exists() else None, "error": xe}
    if root is None:
        return result
    ed, es = dumper._pyelftools_exported_symbols(s.old_so)
    parser = dumper._CastxmlParser(
        root,
        ed,
        es,
        public_header_paths=[str(s.old_h)],
        public_dir_paths=[str(x) for x in s.public_dirs],
    )
    parsed = {}
    times = {}
    errors = {}
    for name in PHASES:
        value, elapsed, error = call(getattr(parser, f"parse_{name}"))
        times[name] = elapsed
        if error:
            errors[name] = error
        else:
            parsed[name] = value
    result.update(
        model_seconds=sum(times.values()),
        model_phases=times,
        counts={k: len(v) for k, v in parsed.items()},
        errors=errors,
    )
    if baseline is not None and not errors:
        candidate = copy.deepcopy(baseline)
        for key, value in parsed.items():
            setattr(candidate, key, value)
        diff, elapsed, error = call(lambda: checker.compare(baseline, candidate, scope_to_public_surface=True))
        result.update(
            semantic_seconds=elapsed,
            semantic_changes=len(diff.changes) if diff else None,
            semantic_verdict=str(diff.verdict) if diff else None,
            semantic_error=error,
        )
    return result


def variant(
    s: Subject,
    agg: Path,
    root: Path,
    label: str,
    binary: str,
    baseline,
    starts=None,
    tmpfs=False,
    jemalloc=False,
    reps=2,
):
    samples = []
    keep = None
    for i in range(reps):
        out = (Path("/dev/shm") if tmpfs else root) / f"{label}-{i}.xml"
        env = os.environ.copy()
        if jemalloc:
            library = first_file(["/usr/lib/x86_64-linux-gnu/libjemalloc.so.2", "/usr/lib/*/libjemalloc.so.2"])
            if library:
                env["LD_PRELOAD"] = str(library)
        sample = run(castxml_cmd(s, agg, out, binary, starts), env=env)
        sample["bytes"] = out.stat().st_size if out.exists() else None
        samples.append(sample)
        if keep is None and sample.get("rc") == 0 and out.exists():
            keep = root / f"{label}-keep.xml"
            shutil.copy2(out, keep)
        out.unlink(missing_ok=True)
    good = [x for x in samples if x.get("rc") == 0]
    result = {
        "samples": samples,
        "median": med([x["elapsed"] for x in good]),
        "rss_kb": max([x.get("time", {}).get("rss", 0) for x in good], default=None),
    }
    if keep:
        result["model"] = parse_model(s, keep, baseline)
        keep.unlink(missing_ok=True)
    return result


def parallel_pair(s: Subject, a: Path, b: Path, root: Path, starts=None):
    def one(label, agg):
        out = root / f"par-{label}.xml"
        value = run(castxml_cmd(s, agg, out, "castxml", starts))
        out.unlink(missing_ok=True)
        return value

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(lambda x: one(*x), [("old", a), ("new", b)]))
    return {
        "wall": time.perf_counter() - started,
        "sum_children": sum(x["elapsed"] for x in values),
        "children": values,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--main-sha")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    latest = os.environ.get("CASTXML_LATEST_BIN", "")
    report = {
        "environment": {
            "main": args.main_sha,
            "head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
            "platform": platform.platform(),
            "cpu": os.cpu_count(),
            "castxml": subprocess.run(["castxml", "--version"], capture_output=True, text=True).stdout.splitlines()[:2],
            "latest": subprocess.run([latest, "--version"], capture_output=True, text=True).stdout.splitlines()[:2]
            if latest and Path(latest).exists()
            else None,
        },
        "subjects": [],
        "errors": [],
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subjects = [flat_c(root, n) for n in (1000, 5000, 10000)]
        subjects += [cpp_records(root, n) for n in (100, 300, 600)]
        subjects += real_subjects(root)
        for index, subject in enumerate(subjects, 1):
            print(f"[{index}/{len(subjects)}] {subject.name}", flush=True)
            sr = out / subject.name
            sr.mkdir()
            row = {"name": subject.name, "family": subject.family, "n": subject.n, "header_bytes": subject.old_h.stat().st_size}
            try:
                row["pipeline"], baseline = dump_pair(subject, sr)
                a = sr / ("agg.hpp" if subject.lang == "c++" else "agg.h")
                a.write_text(f'#include "{subject.old_h.resolve()}"\n')
                b = sr / ("agg-new.hpp" if subject.lang == "c++" else "agg-new.h")
                b.write_text(f'#include "{subject.new_h.resolve()}"\n')
                row["baseline"] = variant(subject, a, sr, "base", "castxml", baseline)
                if subject.starts:
                    row["start"] = variant(subject, a, sr, "start", "castxml", baseline, subject.starts)
                if subject.name in {"flat_c_10000", "cpp_records_600", "openssl_ssl", "onetbb", "eigen_dense"}:
                    row["tmpfs"] = variant(subject, a, sr, "tmpfs", "castxml", baseline, tmpfs=True, reps=1)
                    row["jemalloc"] = variant(subject, a, sr, "jemalloc", "castxml", baseline, jemalloc=True, reps=1)
                    row["parallel"] = parallel_pair(subject, a, b, sr)
                    if subject.starts:
                        row["parallel_start"] = parallel_pair(subject, a, b, sr, subject.starts)
                    if latest and Path(latest).exists():
                        row["latest"] = variant(subject, a, sr, "latest", latest, baseline, reps=1)
                        if subject.starts:
                            row["latest_start"] = variant(subject, a, sr, "latest-start", latest, baseline, subject.starts, reps=1)
            except Exception as error:
                row["fatal"] = f"{type(error).__name__}: {error}"
                row["traceback"] = traceback.format_exc()
                report["errors"].append(row["fatal"])
            report["subjects"].append(row)
            (out / "partial.json").write_text(json.dumps(report, indent=2, default=str))

    def exponent(family: str, key: str):
        points = sorted(
            (x["n"], x.get(key, {}).get("median"))
            for x in report["subjects"]
            if x["family"] == family and x["n"] and x.get(key, {}).get("median")
        )
        if len(points) < 2:
            return None
        return math.log(points[-1][1] / points[-2][1]) / math.log(points[-1][0] / points[-2][0])

    report["scaling"] = {"flat_c": exponent("flat_c", "baseline"), "cpp_records": exponent("cpp_records", "baseline")}
    (out / "large-l2-castxml-profile.json").write_text(json.dumps(report, indent=2, default=str))
    lines = [
        "# Large L2 / CastXML profile",
        "",
        f"Main: `{args.main_sha}`",
        "",
        "|subject|cold pair|warm pair|CastXML|XML MiB|model|compare|RSS MiB|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    fmt = lambda value: f"{value:.3f}" if isinstance(value, (int, float)) else "n/a"
    for row in report["subjects"]:
        pipeline = row.get("pipeline", {})
        baseline = row.get("baseline", {})
        model = baseline.get("model", {})
        rss = baseline.get("rss_kb")
        lines.append(
            f"|{row['name']}|{fmt(pipeline.get('cold_pair'))}|{fmt(pipeline.get('warm_pair'))}|"
            f"{fmt(baseline.get('median'))}|{fmt((model.get('xml_bytes') or 0) / 1048576)}|"
            f"{fmt(model.get('model_seconds'))}|{fmt(pipeline.get('compare_steady'))}|"
            f"{fmt(rss / 1024 if rss else None)}|"
        )
    lines += ["", f"Scaling: `{report['scaling']}`", ""]
    (out / "large-l2-castxml-profile.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()

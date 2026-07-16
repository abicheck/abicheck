#!/usr/bin/env python3
"""Temporary targeted profiler: large current-main L2, phase split, CastXML knobs."""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import copy
import json
import math
import os
import shutil
import statistics
import subprocess
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

TIMEOUT = 300
MODEL_PARTS = ("functions", "variables", "types", "enums", "typedefs", "constants")


@dataclass
class Subject:
    name: str
    lang: str
    compiler: str
    old_h: Path
    new_h: Path
    old_so: Path
    new_so: Path
    includes: list[Path]
    public_dirs: list[Path]
    starts: list[str]
    size: int | None = None


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def timed(fn):
    started = time.perf_counter()
    try:
        return fn(), time.perf_counter() - started, None
    except Exception as exc:
        return None, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"


def run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = TIMEOUT) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        timing_file = Path(tf.name)
    wrapped = ["/usr/bin/time", "-f", "wall=%e\nuser=%U\nsys=%S\nrss=%M", "-o", str(timing_file), *cmd]
    started = time.perf_counter()
    try:
        proc = subprocess.run(wrapped, capture_output=True, check=False, env=env, timeout=timeout)
        meta: dict[str, float | str] = {}
        for line in timing_file.read_text(errors="replace").splitlines():
            key, _, raw = line.partition("=")
            try:
                meta[key] = float(raw)
            except ValueError:
                meta[key] = raw
        return {
            "rc": proc.returncode,
            "elapsed": time.perf_counter() - started,
            "time": meta,
            "stderr": proc.stderr.decode(errors="replace")[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"rc": None, "timeout": True, "elapsed": time.perf_counter() - started, "stderr": str(exc)}
    finally:
        timing_file.unlink(missing_ok=True)


def compile_so(source: Path, output: Path, cpp: bool, includes: list[Path]) -> dict[str, Any]:
    command = ["g++" if cpp else "gcc", "-shared", "-fPIC", "-g", "-O0"]
    for include in includes:
        command += ["-I", str(include)]
    command += ["-o", str(output), str(source)]
    return run(command)


def make_wide_c(root: Path, count: int = 10000) -> Subject:
    directory = root / f"wide_c_{count}"
    directory.mkdir()
    old_h, new_h = directory / "old.h", directory / "new.h"
    old_c, new_c = directory / "old.c", directory / "new.c"
    old_so, new_so = directory / "old.so", directory / "new.so"

    def header(new: bool) -> str:
        lines = ["#pragma once"]
        for index in range(count):
            arg = "long" if new and index == count - 1 else "int"
            lines.append(f"int api_{index}({arg} value);")
        for index in range(count // 10):
            lines.append(f"typedef struct Record_{index} {{ int a; long b; char data[{index % 31 + 1}]; }} Record_{index};")
        return "\n".join(lines) + "\n"

    old_h.write_text(header(False))
    new_h.write_text(header(True))
    old_c.write_text('#include "old.h"\n' + "\n".join(f"int api_{i}(int value){{return value+{i};}}" for i in range(count)))
    new_c.write_text(
        '#include "new.h"\n'
        + "\n".join(
            f"int api_{i}(long value){{return (int)value+{i};}}"
            if i == count - 1
            else f"int api_{i}(int value){{return value+{i};}}"
            for i in range(count)
        )
    )
    compile_so(old_c, old_so, False, [directory])
    compile_so(new_c, new_so, False, [directory])
    return Subject("wide_c_10000", "c", "cc", old_h, new_h, old_so, new_so, [directory], [directory], [], count)


def make_plain_cpp(root: Path, count: int) -> Subject:
    directory = root / f"plain_cpp_{count}"
    directory.mkdir()
    old_h, new_h = directory / "old.hpp", directory / "new.hpp"
    old_cpp, new_cpp = directory / "old.cpp", directory / "new.cpp"
    old_so, new_so = directory / "old.so", directory / "new.so"

    def header(new: bool) -> str:
        lines = [
            "#pragma once",
            "namespace bench {",
            "template<class T, unsigned N> struct Box { T value[N]; T sum() const; };",
            "struct Root { virtual int kind() const; virtual ~Root(); };",
        ]
        for index in range(count):
            scalar = "long" if new and index == count - 1 else "int"
            lines += [
                f"struct API_{index} : Root {{",
                f"  Box<long, {index % 17 + 1}> payload;",
                f"  {scalar} state;",
                f"  virtual int method_{index}(int value) const;",
                "};",
            ]
        lines += ["}", 'extern "C" int bench_anchor();']
        return "\n".join(lines) + "\n"

    old_h.write_text(header(False))
    new_h.write_text(header(True))
    old_cpp.write_text('#include "old.hpp"\nextern "C" int bench_anchor(){return 1;}\n')
    new_cpp.write_text('#include "new.hpp"\nextern "C" int bench_anchor(){return 2;}\n')
    compile_so(old_cpp, old_so, True, [directory])
    compile_so(new_cpp, new_so, True, [directory])
    return Subject(
        f"plain_cpp_{count}", "c++", "c++", old_h, new_h, old_so, new_so,
        [directory], [directory], ["bench", "bench_anchor"], count,
    )


def first_file(patterns: list[str]) -> Path | None:
    for pattern in patterns:
        for candidate in sorted(Path("/").glob(pattern.lstrip("/"))):
            if candidate.is_file():
                return candidate
    return None


def umbrella(directory: Path, include: str, extension: str) -> tuple[Path, Path]:
    directory.mkdir()
    old_h, new_h = directory / f"old.{extension}", directory / f"new.{extension}"
    old_h.write_text(f"#pragma once\n#include <{include}>\n")
    new_h.write_text(old_h.read_text())
    return old_h, new_h


def make_real(root: Path) -> list[Subject]:
    subjects: list[Subject] = []
    ssl = first_file(["/usr/lib/x86_64-linux-gnu/libssl.so", "/usr/lib/*/libssl.so"])
    if ssl and Path("/usr/include/openssl/ssl.h").exists():
        directory = root / "openssl"
        old_h, new_h = umbrella(directory, "openssl/ssl.h", "h")
        subjects.append(Subject("openssl_ssl", "c", "cc", old_h, new_h, ssl, ssl, [], [Path("/usr/include/openssl")], []))
    tbb = first_file(["/usr/lib/x86_64-linux-gnu/libtbb.so", "/usr/lib/*/libtbb.so"])
    if tbb and Path("/usr/include/oneapi/tbb.h").exists():
        directory = root / "onetbb"
        old_h, new_h = umbrella(directory, "oneapi/tbb.h", "hpp")
        subjects.append(Subject("onetbb", "c++", "c++", old_h, new_h, tbb, tbb, [], [Path("/usr/include/oneapi")], ["oneapi::tbb", "tbb"]))
    eigen_root = Path("/usr/include/eigen3")
    if (eigen_root / "Eigen/Dense").exists():
        directory = root / "eigen"
        old_h, new_h = umbrella(directory, "Eigen/Dense", "hpp")
        old_cpp, new_cpp = directory / "old.cpp", directory / "new.cpp"
        old_so, new_so = directory / "old.so", directory / "new.so"
        old_cpp.write_text('#include "old.hpp"\nextern "C" int eigen_anchor(){Eigen::Matrix2d m=Eigen::Matrix2d::Identity();return (int)m(0,0);}\n')
        new_cpp.write_text('#include "new.hpp"\nextern "C" int eigen_anchor(){Eigen::Matrix2d m=Eigen::Matrix2d::Identity();return (int)m(1,1);}\n')
        compile_so(old_cpp, old_so, True, [directory, eigen_root])
        compile_so(new_cpp, new_so, True, [directory, eigen_root])
        subjects.append(Subject("eigen_dense", "c++", "c++", old_h, new_h, old_so, new_so, [eigen_root], [eigen_root / "Eigen"], ["Eigen", "eigen_anchor"]))
    return subjects


@contextlib.contextmanager
def use_castxml(binary: str | None) -> Iterator[None]:
    if not binary:
        yield
        return
    original = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as directory:
        link = Path(directory) / "castxml"
        link.symlink_to(Path(binary).resolve())
        os.environ["PATH"] = f"{directory}:{original}"
        try:
            yield
        finally:
            os.environ["PATH"] = original


def dump_one(subject: Subject, side: str, backend: str, cache: Path, binary: str | None = None, debug_presence_only: bool = False):
    from abicheck import dumper

    library = subject.old_so if side == "old" else subject.new_so
    header = subject.old_h if side == "old" else subject.new_h
    previous = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(cache)
    try:
        with use_castxml(binary):
            return dumper.dump(
                library,
                [header],
                subject.includes,
                side,
                subject.compiler,
                lang="C++" if subject.lang == "c++" else "C",
                public_headers=[header],
                public_header_dirs=subject.public_dirs,
                header_backend=backend,
                debug_presence_only=debug_presence_only,
            )
    finally:
        if previous is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = previous


def pipeline(subject: Subject, root: Path, backend: str, binary: str | None = None, debug_presence_only: bool = False) -> dict[str, Any]:
    from abicheck import checker

    cache = root / f"cache-{backend}-{'new' if binary else 'system'}-{'presence' if debug_presence_only else 'full'}"
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    old_snapshot, old_seconds, old_error = timed(lambda: dump_one(subject, "old", backend, cache, binary, debug_presence_only))
    new_snapshot, new_seconds, new_error = timed(lambda: dump_one(subject, "new", backend, cache, binary, debug_presence_only))
    result: dict[str, Any] = {
        "dump_pair": old_seconds + new_seconds,
        "old": old_seconds,
        "new": new_seconds,
        "errors": [error for error in (old_error, new_error) if error],
    }
    if old_snapshot is not None and new_snapshot is not None:
        comparison, first, compare_error = timed(lambda: checker.compare(old_snapshot, new_snapshot, scope_to_public_surface=True))
        steady = []
        for _ in range(3):
            _, seconds, error = timed(lambda: checker.compare(old_snapshot, new_snapshot, scope_to_public_surface=True))
            if error is None:
                steady.append(seconds)
        result.update(
            compare_first=first,
            compare_steady=median(steady),
            compare_error=compare_error,
            verdict=str(comparison.verdict) if comparison else None,
            changes=len(comparison.changes) if comparison else None,
            counts={part: len(getattr(old_snapshot, part)) for part in MODEL_PARTS},
            from_headers=old_snapshot.from_headers,
            elf_only_mode=old_snapshot.elf_only_mode,
        )
    return result


def phase_profile(subject: Subject, root: Path, binary: str | None = None, debug_presence_only: bool = False) -> dict[str, Any]:
    from abicheck import dumper
    from abicheck import elf_metadata, provenance

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    originals: list[tuple[Any, str, Any]] = []

    def patch(module: Any, name: str, key: str, predicate=None):
        if not hasattr(module, name):
            return
        original = getattr(module, name)
        originals.append((module, name, original))

        def wrapped(*args, **kwargs):
            if predicate is not None and not predicate(args, kwargs):
                return original(*args, **kwargs)
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                totals[key] = totals.get(key, 0.0) + time.perf_counter() - started
                counts[key] = counts.get(key, 0) + 1

        setattr(module, name, wrapped)

    patch(dumper, "_pyelftools_exported_symbols", "elf_symbols")
    patch(elf_metadata, "parse_elf_metadata", "elf_metadata")
    patch(dumper, "_resolve_debug_metadata", "debug_metadata")
    patch(dumper, "_cache_key", "cache_key")
    patch(dumper, "_header_ast_parser", "header_ast_total")
    patch(dumper, "_populate_elf_visibility", "visibility")
    patch(dumper, "backfill_dwarf_layout", "layout_backfill")
    patch(provenance, "apply_provenance", "provenance")
    patch(dumper.DefusedET, "parse", "xml_parse")
    patch(
        dumper.subprocess,
        "run",
        "castxml_process",
        lambda args, kwargs: bool(args and isinstance(args[0], (list, tuple)) and args[0] and Path(str(args[0][0])).name == "castxml"),
    )
    for part in MODEL_PARTS:
        patch(dumper._CastxmlParser, f"parse_{part}", f"model_{part}")
    cache = root / f"phase-cache-{'new' if binary else 'system'}-{'presence' if debug_presence_only else 'full'}"
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    try:
        snapshot, wall, error = timed(lambda: dump_one(subject, "old", "castxml", cache, binary, debug_presence_only))
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)
    model_total = sum(value for key, value in totals.items() if key.startswith("model_"))
    non_overlap = sum(
        totals.get(key, 0.0)
        for key in ("elf_symbols", "elf_metadata", "debug_metadata", "header_ast_total", "visibility", "layout_backfill", "provenance")
    ) + model_total
    return {
        "wall": wall,
        "error": error,
        "phases": totals,
        "calls": counts,
        "model_total": model_total,
        "other": max(0.0, wall - non_overlap),
        "snapshot_counts": {part: len(getattr(snapshot, part)) for part in MODEL_PARTS} if snapshot else None,
    }


def castxml_command(subject: Subject, aggregate: Path, output: Path, binary: str, starts: list[str] | None = None) -> list[str]:
    from abicheck import dumper

    compiler, compiler_id = dumper._resolve_compiler_binary(subject.compiler, None, None)
    command = dumper._build_castxml_command(
        compiler,
        compiler_id,
        subject.includes,
        output,
        aggregate,
        force_cpp=subject.lang == "c++",
        force_cpp20=False,
    )
    command[0] = binary
    if starts:
        command[1:1] = ["--castxml-start", ",".join(starts)]
    return command


def direct_variant(subject: Subject, root: Path, label: str, binary: str, starts: list[str] | None = None, tmpfs: bool = False, jemalloc: bool = False, repetitions: int = 2) -> dict[str, Any]:
    aggregate = root / (f"{label}.hpp" if subject.lang == "c++" else f"{label}.h")
    aggregate.write_text(f'#include "{subject.old_h.resolve()}"\n')
    samples = []
    for index in range(repetitions):
        output = (Path("/dev/shm") if tmpfs else root) / f"{label}-{index}.xml"
        env = os.environ.copy()
        if jemalloc:
            library = first_file(["/usr/lib/x86_64-linux-gnu/libjemalloc.so.2", "/usr/lib/*/libjemalloc.so.2"])
            if library:
                env["LD_PRELOAD"] = str(library)
        sample = run(castxml_command(subject, aggregate, output, binary, starts), env=env)
        sample["bytes"] = output.stat().st_size if output.exists() else None
        samples.append(sample)
        output.unlink(missing_ok=True)
    good = [sample for sample in samples if sample.get("rc") == 0]
    return {
        "median": median([sample["elapsed"] for sample in good]),
        "rss_mb": max([sample.get("time", {}).get("rss", 0.0) / 1024 for sample in good], default=None),
        "bytes": max([sample.get("bytes") or 0 for sample in good], default=None),
        "samples": samples,
    }


def parallel_direct(subject: Subject, root: Path, binary: str, starts: list[str] | None = None) -> dict[str, Any]:
    def one(side: str):
        header = subject.old_h if side == "old" else subject.new_h
        aggregate = root / f"parallel-{side}.{'hpp' if subject.lang == 'c++' else 'h'}"
        output = root / f"parallel-{side}.xml"
        aggregate.write_text(f'#include "{header.resolve()}"\n')
        value = run(castxml_command(subject, aggregate, output, binary, starts))
        output.unlink(missing_ok=True)
        return value

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        children = list(executor.map(one, ("old", "new")))
    return {"wall": time.perf_counter() - started, "sum": sum(child["elapsed"] for child in children), "children": children}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--main-sha", required=True)
    args = parser.parse_args()
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    new_binary = os.environ.get("CASTXML_NEW_BIN")
    result: dict[str, Any] = {
        "main": args.main_sha,
        "new_castxml": new_binary,
        "system_version": subprocess.run(["castxml", "--version"], capture_output=True, text=True).stdout.splitlines()[:3],
        "new_version": subprocess.run([new_binary, "--version"], capture_output=True, text=True).stdout.splitlines()[:3] if new_binary else None,
        "subjects": [],
    }
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subjects = [make_wide_c(root), make_plain_cpp(root, 500), make_plain_cpp(root, 2000), make_plain_cpp(root, 5000), *make_real(root)]
        for index, subject in enumerate(subjects, 1):
            print(f"[{index}/{len(subjects)}] {subject.name}", flush=True)
            subject_output = output / subject.name
            subject_output.mkdir()
            row: dict[str, Any] = {"name": subject.name, "size": subject.size, "header_bytes": subject.old_h.stat().st_size}
            try:
                row["system_forced"] = pipeline(subject, subject_output, "castxml")
                row["system_auto"] = pipeline(subject, subject_output, "auto")
                if subject.name in {"wide_c_10000", "plain_cpp_5000"}:
                    row["phase_full"] = phase_profile(subject, subject_output)
                    row["phase_presence"] = phase_profile(subject, subject_output, debug_presence_only=True)
                row["direct_system"] = direct_variant(subject, subject_output, "system", "castxml")
                if subject.starts:
                    row["direct_system_start"] = direct_variant(subject, subject_output, "system-start", "castxml", subject.starts)
                if subject.name in {"wide_c_10000", "plain_cpp_5000", "onetbb", "eigen_dense", "openssl_ssl"}:
                    row["direct_tmpfs"] = direct_variant(subject, subject_output, "tmpfs", "castxml", tmpfs=True, repetitions=1)
                    row["direct_jemalloc"] = direct_variant(subject, subject_output, "jemalloc", "castxml", jemalloc=True, repetitions=1)
                    row["parallel_system"] = parallel_direct(subject, subject_output, "castxml")
                if new_binary:
                    row["new_forced"] = pipeline(subject, subject_output, "castxml", new_binary)
                    row["direct_new"] = direct_variant(subject, subject_output, "new", new_binary)
                    if subject.starts:
                        row["direct_new_start"] = direct_variant(subject, subject_output, "new-start", new_binary, subject.starts)
                    if subject.name in {"wide_c_10000", "plain_cpp_5000", "onetbb", "eigen_dense", "openssl_ssl"}:
                        row["parallel_new"] = parallel_direct(subject, subject_output, new_binary)
                    if subject.name in {"wide_c_10000", "plain_cpp_5000"}:
                        row["phase_new"] = phase_profile(subject, subject_output, new_binary)
            except Exception as exc:
                row["fatal"] = f"{type(exc).__name__}: {exc}"
                row["traceback"] = traceback.format_exc()
            result["subjects"].append(row)
            (output / "partial-v3.json").write_text(json.dumps(result, indent=2, default=str))
    (output / "large-l2-castxml-v3.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({"subjects": [row["name"] for row in result["subjects"]], "new_castxml": new_binary}, indent=2))


if __name__ == "__main__":
    main()

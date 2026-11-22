"""Generate isolated oneDAL header variants and verify compile and runtime witnesses."""

import argparse
import os
import subprocess
from pathlib import Path

HEADER = Path("oneapi/dal/table/homogen.hpp")
CONSUMER = Path(__file__).with_name("dependency_consumer.cpp")


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError(f"expected one occurrence of {before!r}")
    return source.replace(before, after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    sdk = args.prefix.resolve()
    env = dict(
        os.environ, LD_LIBRARY_PATH=f"{sdk}/lib:" + os.getenv("LD_LIBRARY_PATH", "")
    )
    env.setdefault("OCL_ICD_VENDORS", f"{sdk}/etc/OpenCL/vendors")
    original = (sdk / "include" / HEADER).read_text()
    # Limit edits to the queue overload to avoid changing its sibling constructor.
    start = original.index("static homogen_table wrap(const sycl::queue&")
    end = original.index("#endif", start)
    overload = original[start:end]
    dropped = replace_once(overload, "dependencies,", "{},")
    removed = replace_once(
        dropped, "const std::vector<sycl::event>& dependencies = {},", ""
    )
    for lane, body in [
        ("baseline", overload),
        (
            "forward_copy",
            replace_once(
                overload, "dependencies,", "std::vector<sycl::event>(dependencies),"
            ),
        ),
        ("drop_event", dropped),
        ("remove_parameter", removed),
    ]:
        root = (args.out / lane).resolve()
        header = root / HEADER
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(original[:start] + body + original[end:])
        exe = root / "consumer"
        command = [
            str(sdk / "bin/icpx"),
            "-std=c++17",
            "-fsycl",
            "-DONEDAL_DATA_PARALLEL",
            f"-I{root}",
            f"-I{sdk}/include",
            str(CONSUMER),
            f"-L{sdk}/lib",
            "-Wl,--no-as-needed",
            "-l:libonedal_dpc.so.3",
            "-l:libonedal_parameters_dpc.so.3",
            "-l:libonedal_core.so.3",
            "-l:libonedal_thread.so.3",
            "-o",
            str(exe),
        ]
        build = subprocess.run(command, env=env, capture_output=True, text=True)
        if lane == "remove_parameter":
            if build.returncode and "no matching function" in build.stderr:
                print(lane, "explicit event argument rejected")
                continue
            raise RuntimeError(
                f"unexpected build result: {build.returncode} {build.stderr}"
            )
        if build.returncode:
            raise RuntimeError(build.stderr)
        run = subprocess.run(
            [str(exe)], env=env, capture_output=True, text=True, timeout=60
        )
        print(lane, run.returncode, run.stdout.strip(), flush=True)
        if (run.returncode, run.stdout.strip()) != (
            (2, "0") if lane == "drop_event" else (0, "42")
        ):
            raise RuntimeError(f"unexpected observation: {run.stderr}")


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: Apache-2.0
"""Observe executable calls to frontend imports absent from an earlier release."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from .onedal_covariance_parameters import build_client
from .onedal_selector_relocations import image


def run(matrix, sdk, earlier, work):
    major = int(image(sdk, "libonedal").suffix[1:])
    env = {
        "LC_ALL": "C",
        "LD_LIBRARY_PATH": str(sdk.resolve() / "lib"),
        "LD_BIND_NOW": "1",
    }
    wrapper = work / "probe.cpp"
    main_marker = work / "main_marker.cpp"
    main_marker.write_text(
        '#include <unistd.h>\nextern "C" int __real_main();\n'
        'extern "C" int __wrap_main() {\n'
        '    if (write(2, "entered-main\\n", 13) != 13) _exit(43);\n'
        "    return __real_main();\n}\n"
    )
    marker_inputs = (main_marker, "-Wl,--wrap=main")
    rows = {}
    for algorithm, row in matrix[sdk.name].items():
        names = row["missing_frontend_imports_by_candidate"][earlier]
        exe = build_client(sdk, work, algorithm, major)
        subprocess.run([exe], env=env, check=True)
        marked = build_client(sdk, work, algorithm, major, link_inputs=marker_inputs)
        control = subprocess.run(
            [marked], env=env, capture_output=True, text=True, check=True
        )
        if control.stderr.splitlines().count("entered-main") != 1:
            raise ValueError(f"main entry marker missing for {algorithm}")
        exits = {}
        phases = {}
        for name in names:
            if not name.isascii() or not name.isidentifier():
                raise ValueError(f"unsupported linker symbol: {name}")
            wrapper.write_text(
                '#include <stdlib.h>\nextern "C" void probe() __asm__("__wrap_'
                + name
                + '");\nvoid probe() { _Exit(42); }\n'
            )
            exe = build_client(
                sdk,
                work,
                algorithm,
                major,
                link_inputs=(*marker_inputs, wrapper, f"-Wl,--wrap={name}"),
            )
            observed = subprocess.run([exe], env=env, capture_output=True, text=True)
            code = observed.returncode
            if code == 42:
                phases[name] = (
                    "after_main_entry"
                    if "entered-main" in observed.stderr.splitlines()
                    else "before_main"
                )
            if code not in (0, 42):
                raise ValueError(f"unexpected exit {code} for {algorithm}: {name}")
            exits[name] = code
        rows[algorithm] = {
            "baseline_exit": 0,
            "wrapped_call_exits": exits,
            "first_call_phase": phases,
        }
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("sdk", type=Path)
    parser.add_argument("earlier", help="earlier release label in the matrix")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        rows = run(
            json.loads(args.matrix.read_text()), args.sdk, args.earlier, Path(tmp)
        )
    args.out.write_text(
        json.dumps(
            {"sdk": args.sdk.name, "earlier": args.earlier, "clients": rows}, indent=2
        )
        + "\n"
    )

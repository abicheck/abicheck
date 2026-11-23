# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Generate Linux PI/UR plugin inventory edits and check the exact SYCL rows.

python -m abiforge.run.sycl_plugins OUT
Uses a C compiler; these ELF fixtures model export lookup, not device execution.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from abiforge.checkers import abicheck_cli

_PI = ("piPluginInit", "piextDeviceSelect")
_UR = ("urGetAdapterProcAddrTable", "urGetDeviceProcAddrTable")
_LOOKUP = """import ctypes, sys
so = ctypes.CDLL(sys.argv[1])
for name in sys.argv[2:]:
    assert getattr(so, name)() == 7
"""


def build(library: Path, exports: tuple[str, ...]) -> None:
    source = "\n".join(f"int {name}(void) {{ return 7; }}" for name in exports)
    subprocess.run(
        ["cc", "-shared", "-fPIC", "-x", "c", "-", "-o", str(library)],
        input=source,
        text=True,
        check=True,
    )
    # Resolve names in a fresh process (dlopen caches handles by path).
    subprocess.run([sys.executable, "-c", _LOOKUP, str(library), *exports], check=True)


def dump(binary: Path, target: Path) -> None:
    # Ambient plugin directories can add unrelated adapters to this fixture.
    env = os.environ.copy()
    env.pop("SYCL_PI_PLUGINS_DIR", None)
    env.pop("SYCL_UR_ADAPTERS_DIR", None)
    subprocess.run(
        [*abicheck_cli.ABICHECK, "dump", str(binary), "-o", str(target)],
        env=env,
        check=True,
    )


def compare(
    out: Path, stem: str, before: Path, after: Path, expected: list[str]
) -> dict:
    target = out / f"{stem}.json"
    target.unlink(missing_ok=True)
    check = abicheck_cli.run(before, after, out, headers=None, stem=stem)
    if check.steps[-1].exit not in abicheck_cli.ANSWER_EXITS:
        raise RuntimeError(check.steps[-1].stderr_tail)
    report = abicheck_cli.parse(target.read_text())
    actual = sorted(
        c["kind"] for c in report["changes"] if c["kind"].startswith("sycl_")
    )
    return {
        "expected": sorted(expected),
        "actual": actual,
        "matches": actual == sorted(expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    out = parser.parse_args().out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    with TemporaryDirectory(dir=out) as path:
        root = Path(path)
        runtime = root / "libsycl.so"
        build(runtime, ("runtimeAnchor",))
        for iface, filename, entries in (
            ("pi", "libpi_opencl.so", _PI),
            ("ur", "libur_adapter_opencl.so", _UR),
            (
                "ur-global",
                "libur_adapter_opencl.so",
                ("urGetGlobalProcAddrTable", "urGetDeviceProcAddrTable"),
            ),
        ):
            plugin = root / filename
            snaps = {}
            for state, names in (
                ("absent", ()),
                ("base", entries[:1]),
                ("extended", entries),
            ):
                if names:
                    build(plugin, names)
                else:
                    plugin.unlink(missing_ok=True)
                snap = out / f"{iface}-{state}.json"
                dump(runtime, snap)
                snaps[state] = snap
            for before, after, kind in (
                ("absent", "base", "plugin_added"),
                ("base", "absent", "plugin_removed"),
                ("base", "extended", "pi_entrypoint_added"),
                ("extended", "base", "pi_entrypoint_removed"),
                ("extended", "extended", ""),
            ):
                stem = f"{iface}-{before}-{after}"
                expected = [f"sycl_{kind}"] if kind else []
                if iface == "pi" and "entrypoint" in kind:
                    expected.append("sycl_pi_version_changed")
                rows[stem] = compare(out, stem, snaps[before], snaps[after], expected)
            if iface == "ur-global":
                plugin.write_bytes(b"invalid ELF")
                incomplete = out / "unreadable.json"
                dump(runtime, incomplete)
                rows["unreadable"] = compare(
                    out, "extended-unreadable", snaps["extended"], incomplete, []
                )
            plugin.unlink()
        # Same consumer and exports, first with an additional search directory,
        # then with the implementation marker changed at that same location.
        consumer = root / "libconsumer.so"
        build(consumer, ("consumerAnchor",))
        original = out / "runtime-original.json"
        nested = out / "runtime-extra-dir.json"
        alternative = out / "runtime-alternative.json"
        dump(consumer, original)
        (root / "sycl").mkdir()
        dump(consumer, nested)
        rows["search-dir"] = compare(
            out, "search-dir", original, nested, ["sycl_plugin_search_path_changed"]
        )
        runtime.rename(root / "libacpp-rt.so")
        dump(consumer, alternative)
        rows["implementation"] = compare(
            out, "implementation", nested, alternative, ["sycl_implementation_changed"]
        )
    (out / "receipt.json").write_text(json.dumps(rows, indent=2) + "\n")
    return int(not all(row["matches"] for row in rows.values()))


if __name__ == "__main__":
    raise SystemExit(main())

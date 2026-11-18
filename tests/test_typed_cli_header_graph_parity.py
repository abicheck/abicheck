"""The typed API and the CLI must agree on header-graph findings.

The CLI's dump and the typed ``dumper.dump()`` attach the default header-only
(L2) graph through one owner, ``workflows.dump.default_header_graph``. On a
vanished class specialization the graph decides which findings exist, so the
two routes must report the same kind set for the same pair, except for the one
known public-header-default difference recorded in the plan.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability, skip_reason

# Route difference tracked in the plan (public-header default), not a graph kind.
KNOWN_ROUTE_DIFFERENCE = {"inline_function_removed"}

OLD_HEADER = (
    "template <class T> struct S { static int v; };\n"
    "template <class T> int S<T>::v = 1;\n"
    "inline int use() { return S<int>::v; }\n"
)
NEW_HEADER = (
    "template <class T> struct S { static int v; };\n"
    "template <class T> int S<T>::v = 1;\n"
)


def _build_pair(tmp_path: Path, prefix: list[str]) -> dict[str, tuple[Path, Path]]:
    pairs: dict[str, tuple[Path, Path]] = {}
    for name, header in (("old", OLD_HEADER), ("new", NEW_HEADER)):
        src = tmp_path / name
        src.mkdir()
        (src / "api.h").write_text(header)
        cpp = src / f"{name}.cpp"
        cpp.write_text(
            '#include "api.h"\n'
            + ("template int S<int>::v;\n" if name == "old" else "")
        )
        so = tmp_path / f"{name}.so"
        subprocess.run(
            [
                *prefix,
                "-g",
                "-O0",
                "-fPIC",
                "-shared",
                f"-I{src}",
                str(cpp),
                "-o",
                str(so),
            ],
            check=True,
        )
        pairs[name] = (so, src / "api.h")
    return pairs


def _cli_snapshots(tmp_path: Path, pairs: dict[str, tuple[Path, Path]]):
    """The CLI's own dump of each side, loaded back as snapshots.

    Comparing these with ``checker.compare`` isolates the dump routes: the CLI
    report adds report-level findings the typed ``compare()`` never produces,
    so the report text is not the comparison surface here.
    """
    loaded = {}
    for name, (so, header) in pairs.items():
        out = tmp_path / f"cli-{name}.json"
        subprocess.run(
            abicheck_command("dump", str(so), "-H", str(header), "-o", str(out)),
            check=True,
            capture_output=True,
        )
        loaded[name] = load_snapshot(out)
    return loaded


@pytest.mark.integration
def test_typed_api_and_cli_agree_on_header_graph_findings(tmp_path: Path) -> None:
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    pairs = _build_pair(tmp_path, prefix)
    (old_so, old_h), (new_so, new_h) = pairs["old"], pairs["new"]
    typed = {
        c.kind.value
        for c in compare(dump(old_so, [old_h]), dump(new_so, [new_h])).changes
    }
    cli_snaps = _cli_snapshots(tmp_path, pairs)
    cli = {c.kind.value for c in compare(cli_snaps["old"], cli_snaps["new"]).changes}
    assert typed - KNOWN_ROUTE_DIFFERENCE == cli - KNOWN_ROUTE_DIFFERENCE, (typed, cli)
    assert "var_visibility_changed" in typed, typed


@pytest.mark.integration
def test_typed_and_cli_agree_on_a_plain_removal_without_the_graph(
    tmp_path: Path,
) -> None:
    # Negative control: a plain removed function involves no vanished
    # specialization, so both routes must report exactly the same kinds.
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    headers = {
        "old": 'extern "C" int keep(int);\nextern "C" int gone(int);\n',
        "new": 'extern "C" int keep(int);\n',
    }
    pairs: dict[str, tuple[Path, Path]] = {}
    for name, header in headers.items():
        src = tmp_path / name
        src.mkdir()
        (src / "api.h").write_text(header)
        cpp = src / f"{name}.cpp"
        cpp.write_text(
            '#include "api.h"\nint keep(int x) { return x; }\n'
            + ("int gone(int x) { return x; }\n" if name == "old" else "")
        )
        so = tmp_path / f"{name}.so"
        subprocess.run(
            [
                *prefix,
                "-g",
                "-O0",
                "-fPIC",
                "-shared",
                f"-I{src}",
                str(cpp),
                "-o",
                str(so),
            ],
            check=True,
        )
        pairs[name] = (so, src / "api.h")
    (old_so, old_h), (new_so, new_h) = pairs["old"], pairs["new"]
    typed = {
        c.kind.value
        for c in compare(dump(old_so, [old_h]), dump(new_so, [new_h])).changes
    }
    cli_snaps = _cli_snapshots(tmp_path, pairs)
    cli = {c.kind.value for c in compare(cli_snaps["old"], cli_snaps["new"]).changes}
    assert "func_removed" in typed, typed
    assert "func_removed" in cli, cli
    assert typed - KNOWN_ROUTE_DIFFERENCE == cli - KNOWN_ROUTE_DIFFERENCE, (typed, cli)

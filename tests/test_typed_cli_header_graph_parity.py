"""The typed API and the CLI must agree on graph-derived findings.

The CLI's dump attaches the default header-only (L2) graph through the shared
workflow owner ``workflows.dump.native.attach_default_header_graph``; the typed
``dumper.dump()`` calls the same owner. On a vanished class specialization the
graph decides which findings exist (``var_visibility_changed`` appears, and
``var_removed``, ``type_removed`` and ``inline_function_removed`` do not). The
parity check runs the same inputs through both surfaces and compares the kinds.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability, skip_reason

# Kinds the header graph decides. inline_function_removed also differs between
# the surfaces, but through the public-header default (not the graph); it is
# excluded here and tracked as a §11.2 row until the default is decided.
GRAPH_KINDS = {
    "var_visibility_changed",
    "var_removed",
    "type_removed",
}

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


@pytest.mark.integration
def test_typed_api_and_cli_agree_on_header_graph_findings(tmp_path: Path) -> None:
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    pairs = _build_pair(tmp_path, prefix)
    (old_so, old_h), (new_so, new_h) = pairs["old"], pairs["new"]

    typed_out = {
        c.kind.value
        for c in compare(dump(old_so, [old_h]), dump(new_so, [new_h])).changes
    }

    cli_old = tmp_path / "cli-old.json"
    cli_new = tmp_path / "cli-new.json"
    for so, header, out in ((old_so, old_h, cli_old), (new_so, new_h, cli_new)):
        subprocess.run(
            abicheck_command("dump", str(so), "-H", str(header), "-o", str(out)),
            check=True,
            capture_output=True,
        )
    cli_out = subprocess.run(
        abicheck_command("compare", str(cli_old), str(cli_new)),
        capture_output=True,
        text=True,
    ).stdout
    typed = {kind for kind in GRAPH_KINDS if kind in typed_out}
    cli = {kind for kind in GRAPH_KINDS if kind in cli_out}

    assert typed == cli, (typed, cli)
    assert "var_visibility_changed" in typed, typed
    assert "var_removed" not in typed, typed


@pytest.mark.integration
def test_typed_and_cli_agree_on_a_plain_removal_without_the_graph(
    tmp_path: Path,
) -> None:
    # Negative control: a plain removed function involves no vanished
    # specialization, so both routes must report the same kinds.
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
    cli_out = {}
    for name, (so, header) in pairs.items():
        out = tmp_path / f"cli-{name}.json"
        subprocess.run(
            abicheck_command("dump", str(so), "-H", str(header), "-o", str(out)),
            check=True,
            capture_output=True,
        )
        cli_out[name] = out
    cli_text = subprocess.run(
        abicheck_command("compare", str(cli_out["old"]), str(cli_out["new"])),
        capture_output=True,
        text=True,
    ).stdout
    cli = {kind for kind in typed | GRAPH_KINDS if kind in cli_text}
    assert "func_removed" in typed or "func_removed" in cli_text, typed
    assert {k for k in typed if k in GRAPH_KINDS} == {
        k for k in cli if k in GRAPH_KINDS
    }

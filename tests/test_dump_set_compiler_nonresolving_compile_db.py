"""``dump --set compile.compiler`` over a discovered ``build.compile_db`` that resolves nothing.

Pinned after ADR-091 Phase 5 (#1869 recorded it as unpinned): the Action used to
synthesize a ``--config`` overlay for a compiler input and had to strip a
discovered ``build.compile_db`` so the overlay did not promote it to an
explicit, must-resolve path. The Action now forwards ``--set compile.compiler``
and the CLI's own discovery reads the ``--sources`` tree's config. The run must
still warn that no compile database was found, write the snapshot from the
headers, and exit 0, exactly as the same run without ``--set`` does.

Real gcc and castxml: the header parse and the L3 collection attempt both run.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

#: ``build.compile_db`` values that name no file under the tree.
COMPILE_DBS = [
    "missing/compile_commands.json",
    "build/compile_commands.json",
    "*/nothing_here.json",
]


def _gcc() -> str:
    gcc = shutil.which("gcc")
    if gcc is None or shutil.which("castxml") is None:
        pytest.skip("gcc and castxml are required")
    return gcc


def _project(tmp_path: Path, compile_db: str) -> tuple[Path, Path, Path]:
    tree = tmp_path / "tree"
    tree.mkdir()
    header = tree / "foo.h"
    header.write_text("int foo(int);\nint bar(void);\n", encoding="utf-8")
    source = tree / "foo.c"
    source.write_text(
        '#include "foo.h"\nint foo(int x) { return x; }\nint bar(void) { return 1; }\n',
        encoding="utf-8",
    )
    (tree / ".abicheck.yml").write_text(
        f"build:\n  compile_db: '{compile_db}'\n", encoding="utf-8"
    )
    lib = tmp_path / "libfoo.so"
    result = subprocess.run(
        [_gcc(), "-shared", "-fPIC", "-g", "-o", str(lib), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return lib, header, tree


def _dump(
    tmp_path: Path, lib: Path, header: Path, tree: Path, extra: list[str], out: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / out
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "dump",
            str(lib),
            "-H",
            str(header),
            "--sources",
            str(tree),
            *extra,
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    return result, output


def _function_names(snapshot: Path) -> set[str]:
    from abicheck.serialization import load_snapshot

    return {f.name for f in load_snapshot(snapshot).declarations.functions}


@pytest.mark.parametrize("compile_db", COMPILE_DBS)
@pytest.mark.parametrize("compiler", ["gcc", "path"])
def test_set_compiler_with_a_nonresolving_discovered_compile_db_warns_and_exits_0(
    tmp_path: Path, compile_db: str, compiler: str
) -> None:
    lib, header, tree = _project(tmp_path, compile_db)
    value = _gcc() if compiler == "path" else "gcc"

    with_set, with_set_out = _dump(
        tmp_path, lib, header, tree, ["--set", f"compile.compiler={value}"], "a.json"
    )
    without, without_out = _dump(tmp_path, lib, header, tree, [], "b.json")

    assert with_set.returncode == 0, with_set.stderr
    assert "no compile_commands.json found" in with_set.stderr
    assert "L3/L4/L5 not collected" in with_set.stderr
    assert with_set_out.is_file()
    assert {"foo", "bar"} <= _function_names(with_set_out)
    # The override changes nothing about how the discovered compile_db is read:
    # the same run without --set warns, exits and extracts identically.
    assert without.returncode == with_set.returncode
    assert "no compile_commands.json found" in without.stderr
    assert _function_names(without_out) == _function_names(with_set_out)

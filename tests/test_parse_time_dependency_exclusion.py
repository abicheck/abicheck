"""Parse-time dependency exclusion changes cost, never output.

`extract.dependency_exclusion` lets the clang parser skip dependency-header
functions/variables that dependency scoping would drop anyway. The claim is
exact equivalence: each dump below runs twice -- once as shipped, once with
the parse-time predicate forced off -- and the written snapshots must be
identical apart from fields that differ between any two runs by
construction, while the skip demonstrably fired.

With a binary the skip is off by design: the pre-scoping surface graph
records dependency declarations too, so skipping them changed it (measured:
5,335 vs 1,881 `SOURCE_DECLARES` edges). The binary case pins both halves --
nothing is skipped, and the output is still identical.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import abicheck.dumper_clang as dumper_clang
from abicheck.cli import main

pytestmark = pytest.mark.integration

_HEADER = """\
#include <map>
#include <string>
#include <vector>
namespace mylib {
struct Item { int v; std::string tag; };
int count(const std::vector<Item>& xs);
std::map<int, Item> index(const std::vector<Item>& xs);
extern int mylib_global;
}
"""

_SOURCE = """\
#include "api.hpp"
template class std::vector<int>;
template class std::basic_string<char>;
namespace mylib {
int mylib_global = 1;
int count(const std::vector<Item>& xs) { return (int)xs.size(); }
std::map<int, Item> index(const std::vector<Item>& xs) { return {}; }
}
"""


def _dump(
    tmp_path: Path, name: str, args: list[str], *, skip: bool, monkeypatch
) -> dict:
    """Dump via the real CLI; *skip* False forces the parse-time predicate off.

    Both runs read the same inputs, each with its own empty AST and
    whole-snapshot cache, so neither can be served the other's result and
    only the timestamp differs by construction."""
    from abicheck import snapshot_cache

    with monkeypatch.context() as m:
        if not skip:
            m.setattr(dumper_clang, "active_dependency_predicate", lambda: None)
        m.setenv("XDG_CACHE_HOME", str(tmp_path / f"cache-{name}"))
        m.setattr(snapshot_cache, "_CACHE_DIR", tmp_path / f"snapcache-{name}")
        config = tmp_path / "cfg.yml"
        config.write_text(
            "compile:\n  frontend: clang\n  std: c++17\n", encoding="utf-8"
        )
        out = tmp_path / f"{name}.json"
        result = CliRunner().invoke(
            main, ["dump", "--config", str(config), *args, "-o", str(out)]
        )
    assert result.exit_code == 0, result.output
    text = re.sub(
        r'"created_at": "[^"]*"', '"created_at": ""', out.read_text(encoding="utf-8")
    )
    return json.loads(text)


def _first_difference(a, b, path: str = "") -> str | None:
    """The path of the first differing value -- a structural diff, since
    pytest's own diff of two multi-MB documents outruns the test timeout."""
    if type(a) is not type(b):
        return path
    if isinstance(a, dict):
        for key in sorted(a.keys() | b.keys()):
            if key not in a or key not in b:
                return f"{path}/{key}"
            found = _first_difference(a[key], b[key], f"{path}/{key}")
            if found is not None:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path} (len {len(a)} vs {len(b)})"
        for i, (x, y) in enumerate(zip(a, b)):
            found = _first_difference(x, y, f"{path}[{i}]")
            if found is not None:
                return found
        return None
    return None if a == b else f"{path}: {str(a)[:80]!r} vs {str(b)[:80]!r}"


@pytest.fixture
def skip_counter(monkeypatch):
    fired: list[bool] = []
    real = dumper_clang._ClangAstParser._skips_dependency_decl

    def counting(self, *a):
        hit = real(self, *a)
        fired.append(hit)
        return hit

    monkeypatch.setattr(
        dumper_clang._ClangAstParser, "_skips_dependency_decl", counting
    )
    return fired


@pytest.mark.parametrize("with_binary", [False, True], ids=["header-only", "binary"])
def test_skipping_dependency_decls_at_parse_time_changes_no_output(
    tmp_path: Path, monkeypatch, skip_counter, with_binary: bool
):
    if shutil.which("clang") is None or shutil.which("g++") is None:
        pytest.skip("needs clang and g++")

    header = tmp_path / "api.hpp"
    header.write_text(_HEADER, encoding="utf-8")
    args = ["-H", str(header)]
    if with_binary:
        (tmp_path / "lib.cpp").write_text(_SOURCE, encoding="utf-8")
        so = tmp_path / "libmylib.so"
        cmd = ["g++", "-std=c++17", "-shared", "-fPIC", "-I", str(tmp_path)]
        subprocess.run([*cmd, "-o", str(so), str(tmp_path / "lib.cpp")], check=True)
        args = [str(so), *args]

    shipped = _dump(tmp_path, "shipped", args, skip=True, monkeypatch=monkeypatch)
    if with_binary:
        assert skip_counter and not any(skip_counter)  # consulted, never skipped
    else:
        assert any(skip_counter), "the skip never fired -- nothing was compared"
    skip_counter.clear()
    baseline = _dump(tmp_path, "baseline", args, skip=False, monkeypatch=monkeypatch)
    assert _first_difference(shipped, baseline) is None
    assert shipped["sections"]["declarations"]["payload"]["functions"]

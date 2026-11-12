# SPDX-License-Identifier: Apache-2.0
"""Native GCC reports each change's declared ChangeKinds, as the other backends do.

Every cell is an old/new public header pair whose expected ChangeKinds and
verdict are stated here, from what the edit means for a consumer, never from
another backend's output. Each runs through the public CLI (``abicheck dump``
then ``abicheck compare``) with Clang and CastXML blocked, so a pass cannot
come from a fallback. The cells are the native GCC gaps the edit corpus
found (docs/contribute/plans/header-backend-parity-and-client-matrix.md, E1):
facts the plugin did not emit (record/enum/field deprecation, ``_Atomic``,
completeness, hidden friends, a vptr, ``restrict`` on a parameter),
spellings no detector read (``atomic int``, ``int[0:]``), a const array read
as non-const, a vtable built from the translation unit's initializer, and a
C header parsed as C++ when no language was configured.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import abicheck_command, abicheck_module_command

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.native_gcc


#: The same install name / SONAME on both sides, so neither reads as changed.
_SAME_LIBRARY_NAME = (
    ["-Wl,-install_name,@rpath/libparity.so"]
    if sys.platform == "darwin"
    else ["-Wl,-soname,libparity.so"]
)


@dataclass(frozen=True)
class Cell:
    name: str
    lang: str  # "c" or "c++"
    old_header: str
    new_header: str
    source: str  # the library's own definitions (shared by both versions)
    expected: frozenset[str]  # every one of these must be reported
    forbidden: frozenset[str]  # none of these may be reported
    verdict: str
    new_source: str | None = None  # when the edit is in the definitions


CELLS = (
    Cell(
        "c_field_became_atomic",
        "c",
        "struct flags { int a; int b; };\nint flags_b(struct flags *f);\n",
        "struct flags { int a; _Atomic int b; };\nint flags_b(struct flags *f);\n",
        "int flags_b(struct flags *f) { return f->a; }\n",
        frozenset({"atomic_qualifier_changed"}),
        frozenset(),
        "BREAKING",
    ),
    Cell(
        "c_flexible_array_member",
        "c",
        "struct buf { int n; int d[1]; };\nint buf_n(struct buf *b);\n",
        "struct buf { int n; int d[]; };\nint buf_n(struct buf *b);\n",
        "int buf_n(struct buf *b) { return b->n; }\n",
        frozenset({"flexible_array_member_changed", "type_size_changed"}),
        frozenset(),
        "BREAKING",
    ),
    Cell(
        "c_record_became_opaque",
        "c",
        "struct ctx { char a; int b; };\nint ctx_use(struct ctx *c);\n",
        "struct ctx;\nint ctx_use(struct ctx *c);\n",
        "int ctx_use(struct ctx *c) { return c != 0; }\n",
        frozenset({"type_became_opaque"}),
        frozenset({"type_field_removed", "type_alignment_changed"}),
        "BREAKING",
    ),
    Cell(
        "c_deprecation_added",
        "c",
        "struct flags { int a; };\nunion num { int i; float f; };\n"
        "enum level { LOW, HIGH };\n"
        "int use(struct flags *s, union num *n, enum level l);\n",
        "struct __attribute__((deprecated)) flags { int a; };\n"
        "union num { int i; float f __attribute__((deprecated)); };\n"
        "enum __attribute__((deprecated)) level { LOW, HIGH };\n"
        "int use(struct flags *s, union num *n, enum level l);\n",
        "int use(struct flags *s, union num *n, enum level l)"
        " { return s->a + n->i + (int)l; }\n",
        frozenset(
            {"type_deprecated_added", "field_deprecated_added", "enum_deprecated_added"}
        ),
        frozenset(),
        "COMPATIBLE",
    ),
    Cell(
        "c_deprecation_removed",
        "c",
        "struct __attribute__((deprecated)) flags { int a; };\n"
        "enum __attribute__((deprecated)) level { LOW, HIGH };\n"
        "int use(struct flags *s, enum level l);\n",
        "struct flags { int a; };\nenum level { LOW, HIGH };\n"
        "int use(struct flags *s, enum level l);\n",
        "int use(struct flags *s, enum level l) { return s->a + (int)l; }\n",
        frozenset({"type_deprecated_removed", "enum_deprecated_removed"}),
        frozenset(),
        "COMPATIBLE",
    ),
    Cell(
        "c_const_array_object_grew",
        "c",
        "extern const char banner[];\n",
        "extern const char banner[];\n",
        'const char banner[] = "abc";\n',
        frozenset({"symbol_size_changed_const_object"}),
        frozenset({"symbol_size_changed"}),
        "BREAKING",
        new_source='const char banner[] = "abcdefgh";\n',
    ),
    Cell(
        "cxx_static_member_access_narrowed",
        "c++",
        "class Q { public: int x; static int count; };\nint use(Q *q);\n",
        "class Q { public: int x; private: static int count; };\nint use(Q *q);\n",
        "int Q::count = 1;\nint use(Q *q) { return q->x; }\n",
        frozenset({"var_access_changed"}),
        frozenset(),
        "API_BREAK",
    ),
    Cell(
        "cxx_hidden_friend_added",
        "c++",
        "struct E { int v; };\nint use(E *e);\n",
        "struct E { int v;\n"
        "  friend bool operator==(E a, E b) { return a.v == b.v; } };\n"
        "int use(E *e);\n",
        "int use(E *e) { return e->v; }\n",
        frozenset({"hidden_friend_added"}),
        frozenset(),
        "COMPATIBLE",
    ),
    Cell(
        "cxx_hidden_friend_removed",
        "c++",
        "struct E { int v;\n"
        "  friend bool operator==(E a, E b) { return a.v == b.v; } };\n"
        "int use(E *e);\n",
        "struct E { int v; };\nint use(E *e);\n",
        "int use(E *e) { return e->v; }\n",
        frozenset({"hidden_friend_removed"}),
        frozenset(),
        "API_BREAK",
    ),
    Cell(
        "cxx_vptr_introduced",
        "c++",
        "struct V { int a; int f(); };\nint use(V *v);\n",
        "struct V { int a; virtual int f(); };\nint use(V *v);\n",
        "int V::f() { return a; }\nint use(V *v) { return v->a; }\n",
        frozenset({"vptr_introduced", "type_size_changed"}),
        frozenset(),
        "BREAKING",
    ),
    Cell(
        "cxx_type_lost_abstract",
        "c++",
        "struct Shape { virtual int area() = 0; virtual ~Shape(); };\n"
        "int measure(Shape *s);\n",
        "struct Shape { virtual int area(); virtual ~Shape(); };\n"
        "int measure(Shape *s);\n",
        "Shape::~Shape() {}\nint measure(Shape *s) { return s->area(); }\n",
        frozenset({"type_lost_abstract"}),
        frozenset({"type_vtable_changed"}),
        "COMPATIBLE",
        new_source="Shape::~Shape() {}\nint Shape::area() { return 0; }\n"
        "int measure(Shape *s) { return s->area(); }\n",
    ),
    Cell(
        "cxx_param_restrict_removed",
        "c++",
        "void fill(int *__restrict p, int n);\n",
        "void fill(int *p, int n);\n",
        "void fill(int *p, int n) { while (n--) *p++ = 0; }\n",
        frozenset({"param_restrict_changed"}),
        frozenset(),
        "COMPATIBLE",
    ),
)


@pytest.fixture(scope="module")
def native_plugins(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    compiler = shutil.which(os.environ.get("ABICHECK_NATIVE_GCC", "g++"))
    if not compiler:
        pytest.skip("native GCC unavailable")
    sdk = os.environ.get("ABICHECK_GCC_PLUGIN_HEADERS")
    if not sdk:
        probe = subprocess.run(
            [compiler, "-print-file-name=plugin"],
            text=True,
            capture_output=True,
            check=True,
        )
        sdk = str(Path(probe.stdout.strip()) / "include")
    if not (Path(sdk) / "gcc-plugin.h").is_file():
        pytest.skip("matching GCC plugin-dev headers unavailable")
    output = tmp_path_factory.mktemp("native-plugins")
    subprocess.run(
        abicheck_module_command(
            "abicheck.extract.headers.gcc.build",
            "--compiler",
            compiler,
            "--headers",
            sdk,
            "--output",
            output,
        ),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return compiler, output


def _cli(work: Path, args: list[str], plugin: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "ABICHECK_GCC_PLUGIN": str(plugin),
        "ABICHECK_AST_FRONTEND": "gcc",
    }
    blocked = work / "blocked-tools"
    blocked.mkdir(exist_ok=True)
    marker = work / "non-native-frontend-called"
    for name in ("clang", "clang++", "castxml"):
        executable = blocked / name
        executable.write_text(
            "#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexit 91\n"
        )
        executable.chmod(0o755)
    env["PATH"] = str(blocked) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        abicheck_command(*args, "--config", work / ".abicheck.yml"),
        cwd=work,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )


def _gcc_driver(cxx: str, lang: str) -> str:
    """The C driver next to a configured C++ driver (``g++-14`` -> ``gcc-14``)."""
    if lang == "c++":
        return cxx
    name = Path(cxx).name.replace("g++", "gcc")
    return shutil.which(name) or shutil.which("gcc") or cxx


def _dump(
    work: Path,
    side: str,
    lang: str,
    header_text: str,
    source_text: str,
    native_plugins: tuple[str, Path],
    *,
    configure_lang: bool = True,
) -> Path:
    cxx, plugin = native_plugins
    cc = _gcc_driver(cxx, lang)
    config = f"compile:\n  frontend: gcc\n  compiler: {cc}\n"
    if configure_lang:
        config += f"  lang: {lang}\n"
    (work / ".abicheck.yml").write_text(config)
    ext = "h" if lang == "c" else "hpp"
    header = work / f"{side}.{ext}"
    header.write_text(header_text)
    source = work / f"{side}.{'c' if lang == 'c' else 'cpp'}"
    source.write_text(f'#include "{header.name}"\n{source_text}')
    # One library name for both sides: the default Mach-O install name is the
    # output path, so give it (and the ELF SONAME) explicitly.
    (work / side).mkdir(exist_ok=True)
    lib = work / side / "libparity.so"
    subprocess.run(
        [
            cc,
            "-x",
            lang,
            "-shared",
            "-fPIC",
            "-g0",
            str(source),
            "-o",
            str(lib),
            *_SAME_LIBRARY_NAME,
        ],
        check=True,
        capture_output=True,
    )
    out = work / f"{side}.json"
    result = _cli(work, ["dump", str(lib), "-H", str(header), "-o", str(out)], plugin)
    assert result.returncode == 0, result.stderr
    return out


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.name)
def test_native_gcc_reports_the_declared_change(
    cell: Cell, native_plugins: tuple[str, Path], tmp_path: Path
) -> None:
    old = _dump(
        tmp_path, "old", cell.lang, cell.old_header, cell.source, native_plugins
    )
    new = _dump(
        tmp_path,
        "new",
        cell.lang,
        cell.new_header,
        cell.new_source if cell.new_source is not None else cell.source,
        native_plugins,
    )
    assert load_snapshot(old).ast_producer == "gcc-experimental"
    report = tmp_path / "report.json"
    result = _cli(
        tmp_path,
        ["compare", str(old), str(new), "-o", f"json={report}"],
        native_plugins[1],
    )
    payload = json.loads(report.read_text())
    kinds = {c["kind"] for c in payload["changes"]}
    assert cell.expected <= kinds, (cell.name, sorted(kinds), result.stderr)
    assert not (cell.forbidden & kinds), (cell.name, sorted(kinds))
    assert payload["verdict"] == cell.verdict, (cell.name, sorted(kinds))
    assert not (tmp_path / "non-native-frontend-called").exists()


def test_unconfigured_c_header_is_parsed_as_c(
    native_plugins: tuple[str, Path], tmp_path: Path
) -> None:
    # `restrict` and `_Atomic` are C keywords; a C++ parse rejects them, so
    # a C header dumped without a configured language must be parsed as C,
    # the same way CastXML and Clang decide (one shared rule).
    header = "int scan(int *restrict p, _Atomic int *q);\n"
    source = "int scan(int *restrict p, _Atomic int *q) { return *p + *q; }\n"
    out = _dump(
        tmp_path, "old", "c", header, source, native_plugins, configure_lang=False
    )
    snap = load_snapshot(out)
    assert snap.ast_producer == "gcc-experimental"
    assert [f.name for f in snap.declarations.functions] == ["scan"]
    assert [p.is_restrict for p in snap.declarations.functions[0].params] == [
        True,
        False,
    ]

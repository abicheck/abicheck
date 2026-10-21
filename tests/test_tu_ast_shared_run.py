"""One clang AST dump per translation unit, shared by L4 replay and the L5 passes.

Phase 2 of ``docs/contribute/plans/l4-l2-extraction-convergence.md``: the L4
``ClangSourceExtractor`` and the L5 AST passes read one ``clang -ast-dump=json``
per TU, from one argv (``clang_command.build_clang_command``) and one bounded
runner (``clang_ast_run.dump_tu_ast``).

What is pinned here:

- **Differential (integration).** The L5 parsers produce identical edges under
  the shared argv and under the parse-only allowlist the L5 passes replayed
  before. The allowlist is re-stated in this file as the reference, so the test
  does not depend on the code it checks. Both configurations are asserted to
  have actually run, and to be genuinely different argv.
- **Call count (integration).** With L4 and L5 both running, each TU is dumped
  once. The same scope run standalone dumps every TU twice, so the counter is
  shown to discriminate.
- **Unchanged L4 output (integration).** The L4 surface from the shared run
  equals the standalone L4 surface, timing fields aside.
- **Code-loading guard (property).** A carried ABI flag can never admit a
  code-loading token (``-fplugin=``, ``-load``) into the shared argv.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from abicheck._compiler_options import CLANG_GCC_HEADER_COMPAT_DEFINES
from abicheck.buildsource import clang_ast_run
from abicheck.buildsource.adapters.compile_db import CompileDbAdapter
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.clang_ast_run import (
    STAGE_DEADLINE_AFTER_PARSE,
    STAGE_EMPTY,
    STAGE_JSON,
    STAGE_RUN,
)
from abicheck.buildsource.l5_ast_pass import (
    L5_AST_PASSES,
    consume_tu_dump,
    plan_l5_run,
    plan_shared_l5_run,
    run_ast_passes,
)
from abicheck.buildsource.source_extractors.base import SourceExtractionError
from abicheck.buildsource.source_extractors.clang import ClangSourceExtractor
from abicheck.buildsource.source_extractors.clang_command import (
    build_clang_command,
    compile_unit_invocation,
)
from abicheck.buildsource.source_extractors.clang_dump_errors import ast_or_raise
from abicheck.buildsource.source_replay import run_source_replay
from abicheck.buildsource.source_replay_shared import shared_tu_work

#: The parse-only flag prefixes the L5 passes replayed before the shared argv
#: (``call_graph._SAFE_REPLAY_FLAG_PREFIXES``). Restated here as the reference.
_ALLOWLIST_PREFIXES = (
    "-fvisibility",
    "-fvisibility-inlines-hidden",
    "-fpack-struct",
    "/Zp",
    "-fshort-enums",
    "-fshort-wchar",
    "-fabi-version",
    "-fno-rtti",
    "-frtti",
    "-fno-exceptions",
    "-fexceptions",
    "-flto",
    "-fno-lto",
    "-fwhole-program-vtables",
    "-mabi=",
    "-m32",
    "-m64",
    "/arch:",
)

_HEADER = """\
#pragma once
#define SHAPE_SIDES 4
struct Base { virtual ~Base() {} virtual int area() const { return 0; } int id = 0; };
struct Shape : Base { int w = 1; int h = 2; int area() const override { return w * h; } };
typedef Shape ShapeAlias;
template <typename T> struct Box { T value; T get() const { return value; } };
int apply(int (*fn)(int), int x);
int twice(int x);
int use_shape(const Base &b);
"""

_SOURCE_A = """\
#include "shapes.h"
#include "sysdep.h"
#define LOCAL_FLAG 3
static int square(int x) { return x * x; }
int twice(int x) { return square(x) + sys_helper(x) + LOCAL_FLAG; }
int apply(int (*fn)(int), int x) { return fn(x); }
int use_shape(const Base &b) { return b.area() + apply(twice, 1); }
Box<int> make_box() { Box<int> b{5}; return b; }
int run_all() { Shape s; ShapeAlias a; use_shape(s); return make_box().get() + a.area(); }
"""

_SOURCE_B = """\
#include "shapes.h"
int other_entry(int v) { Shape s; s.w = v; return s.area() + twice(v) + SHAPE_SIDES; }
"""

_SYSDEP = """\
#pragma once
inline int sys_helper(int x) { return x + 1; }
"""


def _fixture_build(root: Path) -> BuildEvidence:
    """A two-TU project written under *root*, built from a real compile DB."""
    inc = root / "inc"
    sysdir = root / "sys"
    src = root / "src"
    for d in (inc, sysdir, src):
        d.mkdir()
    (inc / "shapes.h").write_text(_HEADER)
    (sysdir / "sysdep.h").write_text(_SYSDEP)
    (src / "a.cpp").write_text(_SOURCE_A)
    (src / "b.cpp").write_text(_SOURCE_B)
    entries = [
        {
            "directory": str(root),
            "file": "src/a.cpp",
            "arguments": [
                "g++",
                "-std=c++17",
                "-DFOO=1",
                "-Iinc",
                "-isystem",
                "sys",
                "-fvisibility=hidden",
                "-fno-exceptions",
                "-fpack-struct",
                "-O2",
                "-g",
                "-c",
                "src/a.cpp",
                "-o",
                "a.o",
            ],
        },
        {
            "directory": str(root),
            "file": "src/b.cpp",
            "arguments": [
                "g++",
                "-std=c++17",
                "-Iinc",
                "-fvisibility=hidden",
                "-c",
                "src/b.cpp",
                "-o",
                "b.o",
            ],
        },
    ]
    db = root / "compile_commands.json"
    db.write_text(json.dumps(entries))
    return CompileDbAdapter(db).collect()


def _allowlisted_argv(cu: CompileUnit) -> list[str]:
    """The parse-only argv the L5 passes replayed before the shared builder."""
    out: list[str] = []
    cxx = cu.language.lower() in ("cxx", "c++", "cpp")
    out += ["-x", "c++" if cxx else "c"]
    if cu.standard:
        out.append(f"-std={cu.standard}")
    if cu.target_triple:
        out.append(f"--target={cu.target_triple}")
    if cu.sysroot:
        out.append(f"--sysroot={cu.sysroot}")
    for name, value in sorted(cu.defines.items()):
        out.append(f"-D{name}={value}" if value not in (None, "") else f"-D{name}")
    for name in sorted(cu.undefines):
        out.append(f"-U{name}")
    for inc in cu.include_paths:
        out += ["-I", inc]
    for inc in cu.system_include_paths:
        out += ["-isystem", inc]
    out += [f for f in cu.abi_relevant_flags if f.startswith(_ALLOWLIST_PREFIXES)]
    return out


def _allowlisted_dump_cmd(cu: CompileUnit, clang_bin: str) -> list[str]:
    """The reference command: the same dump tail, the allowlisted argv."""
    return [
        clang_bin,
        *CLANG_GCC_HEADER_COMPAT_DEFINES,
        "-fsyntax-only",
        "-ferror-limit=0",
        "-Xclang",
        "-ast-dump=json",
        *_allowlisted_argv(cu),
        "--",
        cu.source,
    ]


def _dump_both_ways(
    cu: CompileUnit,
) -> tuple[list[str], clang_ast_run.TuAstDump, list[str], clang_ast_run.TuAstDump]:
    """One TU dumped under the shared argv and under the allowlisted argv."""
    _source, run_directory = compile_unit_invocation(cu)
    shared_cmd = build_clang_command(cu, Path(cu.source), clang_bin="clang++")
    allow_cmd = _allowlisted_dump_cmd(cu, "clang++")
    shared = clang_ast_run.dump_tu_ast(
        shared_cmd, cwd=run_directory, timeout=120, unit_label=cu.source
    )
    allow = clang_ast_run.dump_tu_ast(
        allow_cmd, cwd=run_directory, timeout=120, unit_label=cu.source
    )
    return shared_cmd, shared, allow_cmd, allow


@pytest.mark.integration
def test_l5_parsers_read_the_same_edges_under_both_argvs(tmp_path: Path) -> None:
    build = _fixture_build(tmp_path)
    units = [cu for cu in build.compile_units if cu.source]
    assert len(units) == 2
    shared_edges: dict[str, list[Any]] = {p.name: [] for p in L5_AST_PASSES}
    allow_edges: dict[str, list[Any]] = {p.name: [] for p in L5_AST_PASSES}
    for cu in units:
        shared_cmd, shared, allow_cmd, allow = _dump_both_ways(cu)
        # Both configurations ran to a tree, and they are genuinely different.
        assert shared.ast is not None, shared.error
        assert allow.ast is not None, allow.error
        # The argvs differ: the allowlisted one ends in a ``--`` separator.
        assert shared_cmd != allow_cmd
        assert "--" in allow_cmd and "--" not in shared_cmd
        shared_results, shared_diags = consume_tu_dump(shared, cu, L5_AST_PASSES)
        allow_results, allow_diags = consume_tu_dump(allow, cu, L5_AST_PASSES)
        for i, p in enumerate(L5_AST_PASSES):
            shared_edges[p.name].append((shared_results[i], shared_diags[i]))
            allow_edges[p.name].append((allow_results[i], allow_diags[i]))
    # The parsers did real work, so equality below is not vacuous.
    assert any(r for r, _d in shared_edges["call_graph"] if r)
    assert any(r for r, _d in shared_edges["type_graph"] if r)
    for p in L5_AST_PASSES:
        assert shared_edges[p.name] == allow_edges[p.name], p.name


@pytest.mark.integration
def test_l4_and_l5_share_one_dump_per_tu(tmp_path: Path, monkeypatch) -> None:
    build = _fixture_build(tmp_path)
    units = [cu for cu in build.compile_units if cu.source]
    dumped: list[Path] = []
    real_dump = clang_ast_run.dump_tu_ast

    def counting(cmd, **kwargs):
        dumped.append(Path(cmd[-1]))
        return real_dump(cmd, **kwargs)

    monkeypatch.setattr(clang_ast_run, "dump_tu_ast", counting)
    extractor = ClangSourceExtractor(clang_bin="clang")
    roots = [str(tmp_path / "inc")]
    run = plan_l5_run(build, "clang")
    surface, _diags = run_source_replay(
        build, extractor, scope="target", public_header_roots=roots, l5_run=run
    )
    # Both tiers ran: L4 produced declarations and L5 produced edges.
    assert surface.reachable_declarations, "L4 produced no declarations"
    assert run.outcomes["call_graph"].result
    # One dump per TU, however many tiers read it. Paths compare as Path: the
    # dump spells a Windows path with backslashes, the compile DB with slashes.
    assert sorted(dumped) == sorted(Path(cu.source) for cu in units)


@pytest.mark.integration
def test_standalone_passes_dump_each_tu_again(tmp_path: Path, monkeypatch) -> None:
    """The discriminator for the count above: without sharing, every TU is
    dumped by the L4 replay and again by the L5 passes."""
    build = _fixture_build(tmp_path)
    units = [cu for cu in build.compile_units if cu.source]
    dumped: list[Path] = []
    real_dump = clang_ast_run.dump_tu_ast

    def counting(cmd, **kwargs):
        dumped.append(Path(cmd[-1]))
        return real_dump(cmd, **kwargs)

    monkeypatch.setattr(clang_ast_run, "dump_tu_ast", counting)
    ClangSourceExtractor(clang_bin="clang").extract(
        units[0], public_header_roots=[str(tmp_path / "inc")]
    )
    run_ast_passes(BuildEvidence(compile_units=units), "clang++")
    # extract() dumps its TU; the standalone pass then dumps every TU once more.
    assert dumped.count(Path(units[0].source)) == 2
    assert len(dumped) == 1 + len(units)


def _strip_timing(payload: Any) -> Any:
    """Drop wall-clock fields (``*_s``) so two runs compare on content only."""
    if isinstance(payload, dict):
        return {
            k: _strip_timing(v)
            for k, v in payload.items()
            if not (isinstance(k, str) and k.endswith("_s"))
        }
    if isinstance(payload, list):
        return [_strip_timing(v) for v in payload]
    return payload


@pytest.mark.integration
def test_l4_surface_is_unchanged_by_the_shared_run(tmp_path: Path) -> None:
    build = _fixture_build(tmp_path)
    roots = [str(tmp_path / "inc")]
    shared = run_source_replay(
        build,
        ClangSourceExtractor(clang_bin="clang"),
        scope="target",
        public_header_roots=roots,
        l5_run=plan_l5_run(build, "clang"),
    )[0]
    standalone = run_source_replay(
        build,
        ClangSourceExtractor(clang_bin="clang"),
        scope="target",
        public_header_roots=roots,
    )[0]
    assert _strip_timing(shared.to_dict()) == _strip_timing(standalone.to_dict())


#: Each entry is one way a pack can spell a code-loading option, as the tokens
#: it would occupy in ``abi_relevant_flags``.
_CODE_LOADING_FORMS: tuple[tuple[str, ...], ...] = (
    ("-fplugin=/tmp/evil.so",),
    ("-fpass-plugin=/tmp/evil.so",),
    ("-Xclang", "-load", "-Xclang", "/tmp/evil.so"),
    ("-Xclang", "-add-plugin", "-Xclang", "evil_action"),
)
_BENIGN_FORMS: tuple[tuple[str, ...], ...] = (
    ("-fvisibility=hidden",),
    ("-fPIC",),
    ("-fno-rtti",),
    ("-m32",),
)


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.sampled_from(_CODE_LOADING_FORMS + _BENIGN_FORMS),
        max_size=8,
    )
)
def test_shared_argv_never_admits_a_code_loading_option(
    forms: list[tuple[str, ...]],
) -> None:
    flags = [tok for form in forms for tok in form]
    cu = CompileUnit(
        id="cu://p",
        source="p.cpp",
        language="CXX",
        standard="c++17",
        abi_relevant_flags=flags,
    )
    cmd = build_clang_command(cu, Path("p.cpp"), clang_bin="clang++")
    # ``-Xclang`` itself is always in the fixed dump tail, so the forbidden
    # operands and flag names are checked, not the bare ``-Xclang`` token.
    forbidden = {tok for form in _CODE_LOADING_FORMS for tok in form} - {"-Xclang"}
    for token in forbidden:
        assert token not in cmd, token
    # Every benign flag survives, each as often as the build recorded it.
    benign = {tok for form in _BENIGN_FORMS for tok in form}
    assert [t for t in cmd if t in benign] == [t for t in flags if t in benign]


@pytest.mark.parametrize(
    ("argv", "kept"),
    [
        (["-fplugin=/x.so", "-DFOO=1"], ["-DFOO=1"]),
        (["-Xclang", "-load", "-Xclang", "/x.so", "-O2"], ["-O2"]),
        (
            ["-Xclang", "-target-abi", "-Xclang", "aapcs"],
            ["-Xclang", "-target-abi", "-Xclang", "aapcs"],
        ),
        (["-include", "config.h", "-fPIC"], ["-include", "config.h", "-fPIC"]),
        (["-load", "/x.so"], []),
    ],
)
def test_drop_code_loading_options_removes_only_the_loading_forms(
    argv: list[str], kept: list[str]
) -> None:
    from abicheck.buildsource.abi_flags import drop_code_loading_options

    assert drop_code_loading_options(argv) == kept


def test_union_argv_is_the_l4_context_plus_the_dump_tail() -> None:
    """The shared argv is the L4 builder's output, unchanged in shape: a
    consumer can rely on the tail (``-Xclang -ast-dump=json``) and the source
    being last."""
    cu = CompileUnit(id="cu://q", source="/p/q.cpp", language="CXX")
    cmd = build_clang_command(cu, Path("/p/q.cpp"), clang_bin="clang++")
    assert cmd[0] == "clang++"
    assert "-ast-dump=json" in cmd
    assert cmd[-1] == "/p/q.cpp"
    assert set(CLANG_GCC_HEADER_COMPAT_DEFINES) <= set(cmd)


_C_SOURCE = """\
#include <stdint.h>
typedef struct point { int32_t x; int32_t y; } point_t;
int add_points(point_t a, point_t b) { return a.x + b.x + a.y + b.y; }
static int helper(int v) { return v * 2; }
int scale(int v) { return helper(v) + (int)sizeof(point_t); }
"""


@pytest.mark.integration
def test_c_unit_extracts_exactly_as_under_the_pre_change_driver(
    tmp_path: Path, monkeypatch
) -> None:
    """A C unit's L4 surface must not depend on the driver choice.

    Before this change L4 dumped every unit under plain ``clang``. The pinned
    behaviour: forcing that driver (what the old code always ran) gives the same
    ``SourceAbiTu`` as the default path, for a C translation unit.
    """
    from abicheck.buildsource import clang_ast_run

    src = tmp_path / "src" / "c.c"
    src.parent.mkdir()
    src.write_text(_C_SOURCE)
    cu = CompileUnit(
        id="cu://c",
        source=str(src),
        directory=str(tmp_path),
        argv=["cc", "-std=c11", "-c", str(src), "-o", "c.o"],
        language="C",
        standard="c11",
    )
    roots = [str(src.parent)]
    default = ClangSourceExtractor(clang_bin="clang").extract(
        cu, public_header_roots=roots
    )
    monkeypatch.setattr(clang_ast_run, "ast_clang_binary", lambda b, _lang="": b)
    pre_change = ClangSourceExtractor(clang_bin="clang").extract(
        cu, public_header_roots=roots
    )
    assert default.to_dict() == pre_change.to_dict()
    assert default.functions, "the C fixture produced no declarations"


def test_c_units_keep_plain_clang_and_c_plus_plus_units_take_clang_plus_plus(
    monkeypatch,
) -> None:
    from abicheck.buildsource.source_extractors import clang_command

    monkeypatch.setattr(clang_command.shutil, "which", lambda b: f"/usr/bin/{b}")
    assert clang_command.ast_clang_binary("clang", "C") == "clang"
    assert clang_command.ast_clang_binary("clang", "OBJC") == "clang"
    assert clang_command.ast_clang_binary("clang", "CXX") == "clang++"
    assert clang_command.ast_clang_binary("clang", "") == "clang++"
    # An explicit binary is never rewritten, whatever the language.
    assert clang_command.ast_clang_binary("/opt/clang-18", "CXX") == "/opt/clang-18"


@pytest.mark.integration
def test_one_bad_tu_is_its_own_diagnostic_not_a_pool_failure(tmp_path: Path) -> None:
    """A TU whose dump cannot even be commanded degrades alone.

    The recorded flags pin a SYCL pass a stock clang cannot honour, so the
    command for that TU cannot be built. Before the per-TU guard this raised
    out of the worker and discarded every other TU's result.
    """
    build = _fixture_build(tmp_path)
    bad = tmp_path / "src" / "bad.cpp"
    bad.write_text("int bad_entry() { return 0; }\n")
    build.compile_units.append(
        CompileUnit(
            id="cu://bad",
            source=str(bad),
            directory=str(tmp_path),
            argv=["g++", "-std=c++17", "-fsycl-device-only", "-c", str(bad)],
            language="CXX",
            standard="c++17",
            abi_relevant_flags=["-fsycl-device-only"],
        )
    )
    roots = [str(tmp_path / "inc")]
    run = plan_l5_run(build, "clang")
    surface, diagnostics = run_source_replay(
        build,
        ClangSourceExtractor(clang_bin="clang"),
        scope="target",
        public_header_roots=roots,
        l5_run=run,
    )
    # The good TUs still produced their surface and their edges.
    assert surface.reachable_declarations, "the good TUs produced no declarations"
    assert run.outcomes["call_graph"].result
    # The bad TU is named in its own L4 diagnostic, and only there.
    assert any("bad.cpp" in d for d in diagnostics), diagnostics
    assert not any("c.c" in d or "a.cpp" in d for d in diagnostics), diagnostics


class _FinishFails(ClangSourceExtractor):
    """An extractor whose tree is read but whose macro pass fails."""

    def __init__(self, tree: dict[str, Any]) -> None:
        super().__init__()
        self._tree = tree

    def dump_for(self, cu):
        return clang_ast_run.TuAstDump(self._tree, 0)

    def source_abi_from_dump(self, cu, dump, *, public_header_roots):
        return "raw-tu"

    def finish_source_abi(self, cu, tu, *, public_header_roots):
        raise SourceExtractionError("macro pass failed")


def test_a_failing_macro_pass_is_that_tu_s_l4_diagnostic(tmp_path: Path) -> None:
    """The macro pass runs after the tree is read; its failure is one TU's.

    The L4 result is the diagnostic with no TU, and the L5 hook still sees the
    dump (the L5 side of the same TU is unaffected by the L4 macro failure).
    """
    build = _fixture_build(tmp_path)
    cu = build.compile_units[0]
    dump_tree = {"kind": "TranslationUnitDecl", "inner": []}
    extractor = _FinishFails(dump_tree)
    seen: list[Any] = []

    def consume(dump, unit):
        seen.append(dump.ast)
        return [], []

    item = (cu, True, True)
    (tu, l4_error), l5 = shared_tu_work(extractor, [], consume, item)
    assert tu is None
    assert l4_error == f"{cu.source or cu.id}: macro pass failed"
    assert seen == [dump_tree]
    assert l5 == ([], [])


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        (STAGE_DEADLINE_AFTER_PARSE, "scan deadline exceeded after loading AST"),
        (STAGE_EMPTY, "clang produced no AST for"),
    ],
)
def test_ast_or_raise_maps_each_failed_stage_to_its_l4_text(
    tmp_path: Path, stage: str, message: str
) -> None:
    """Each failed stage raises the L4 SourceExtractionError text it always had."""
    cu = _fixture_build(tmp_path).compile_units[0]
    dump = clang_ast_run.TuAstDump(None, 0, stage=stage, error="")
    with pytest.raises(SourceExtractionError, match=message):
        ast_or_raise(cu, dump)


def test_ast_or_raise_returns_the_tree_of_a_clean_dump(tmp_path: Path) -> None:
    """A read tree passes straight through."""
    cu = _fixture_build(tmp_path).compile_units[0]
    tree = {"kind": "TranslationUnitDecl", "inner": []}
    assert ast_or_raise(cu, clang_ast_run.TuAstDump(tree, 0)) == tree


def test_planner_shares_only_for_a_clang_extractor_that_is_wanted(
    tmp_path: Path,
) -> None:
    """The shared L5 plan exists only when L5 wants the TU and L4 is clang."""
    build = _fixture_build(tmp_path)
    units = list(build.compile_units)
    assert (
        plan_shared_l5_run(
            ClangSourceExtractor(), False, build, "clang", (), units, "full"
        )
        is None
    )
    assert plan_shared_l5_run(object(), True, build, "clang", (), units, "full") is None
    assert (
        plan_shared_l5_run(
            ClangSourceExtractor(), True, build, "clang", (), units, "headers-only"
        )
        is not None
    )


class _DumpFails(ClangSourceExtractor):
    """An extractor whose command for the TU cannot be built."""

    def dump_for(self, cu):
        raise SourceExtractionError("no command for this unit")


class _ReadFails(ClangSourceExtractor):
    """An extractor whose dumped tree cannot be read as L4 facts."""

    def dump_for(self, cu):
        return clang_ast_run.TuAstDump({"kind": "TranslationUnitDecl", "inner": []}, 0)

    def source_abi_from_dump(self, cu, dump, *, public_header_roots):
        raise SourceExtractionError("tree is unreadable")


def test_a_unit_without_a_command_is_its_own_l4_diagnostic(tmp_path: Path) -> None:
    """A TU whose dump command cannot be built reports that reason as its diagnostic."""
    cu = _fixture_build(tmp_path).compile_units[0]
    seen: list[Any] = []

    def consume(dump, unit):
        seen.append(dump.stage)
        return [], []

    (tu, l4_error), l5 = shared_tu_work(_DumpFails(), [], consume, (cu, True, True))
    assert tu is None
    assert l4_error == f"{cu.source or cu.id}: no command for this unit"
    assert seen == [STAGE_RUN]
    assert l5 == ([], [])


def test_an_unreadable_tree_is_that_tu_s_l4_diagnostic(tmp_path: Path) -> None:
    """A tree L4 cannot read is the TU's diagnostic and no L4 surface."""
    cu = _fixture_build(tmp_path).compile_units[0]
    extractor = _ReadFails()
    (tu, l4_error), l5 = shared_tu_work(extractor, [], None, (cu, True, False))
    assert tu is None
    assert l4_error == f"{cu.source or cu.id}: tree is unreadable"
    assert l5 is None


def test_a_shared_run_without_l5_hooks_is_refused(tmp_path: Path) -> None:
    """The shared run cannot fold L5 results it has no hooks for."""
    from types import SimpleNamespace

    from abicheck.buildsource.source_replay_shared import run_shared_dumps

    cu = _fixture_build(tmp_path).compile_units[0]
    with pytest.raises(ValueError, match="needs its L5 hooks"):
        run_shared_dumps(
            ClangSourceExtractor(),
            [],
            [cu],
            [0],
            SimpleNamespace(consume=None, fold=None),
            jobs_for=lambda n: 1,
            use_process_pool=False,
        )


def test_ast_or_raise_reports_a_json_failure_as_an_l4_failure() -> None:
    """A tree clang wrote but JSON cannot read is an L4 failure, not an empty surface."""
    cu = CompileUnit(id="cu://x", source="x.cpp")
    with pytest.raises(SourceExtractionError, match="was not valid JSON"):
        ast_or_raise(cu, clang_ast_run.TuAstDump(None, 1, stage=STAGE_JSON))

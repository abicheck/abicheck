# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Make adapter coverage (ADR-029 D7)."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.buildsource.adapters import MakeAdapter
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.cli import main

DRY_RUN = """\
make: Entering directory '/home/user/proj'
gcc -std=c11 -D_GLIBCXX_USE_CXX11_ABI=0 -Iinclude -c src/a.c -o build/a.o
@g++ -std=c++20 -c src/b.cpp -o build/b.o
cc -shared -o libfoo.so build/a.o build/b.o
ar rcs libbar.a build/a.o
make: Leaving directory '/home/user/proj'
"""


def _has_response_arg(argv: list[str], path: Path) -> bool:
    return any(arg.startswith("@") and Path(arg[1:]).expanduser() == path for arg in argv)


def test_make_dry_run_extracts_compile_units():
    ev = MakeAdapter(dry_run=DRY_RUN).collect()
    assert ev.generators[0].kind == "make"
    units = {c.source: c for c in ev.compile_units}
    # Only the two `-c` compile recipes become units; link/ar/info lines are skipped.
    assert set(units) == {"src/a.c", "src/b.cpp"}
    assert units["src/a.c"].standard == "c11"
    assert units["src/b.cpp"].standard == "c++20"


def test_make_reduced_confidence_diagnostic_and_options():
    ev = MakeAdapter(dry_run=DRY_RUN).collect()
    assert any("reduced confidence" in d for d in ev.diagnostics)
    opts = {(o.key, o.value) for o in ev.build_options}
    assert ("std:C", "c11") in opts
    assert ("define:_GLIBCXX_USE_CXX11_ABI", "0") in opts


def test_make_forced_include_not_mistaken_for_source():
    # `-include config.hpp` is a forced header, not the TU; foo.cc must win.
    ev = MakeAdapter(dry_run="g++ -include config.hpp -std=c++17 -c src/foo.cc -o foo.o").collect()
    assert [c.source for c in ev.compile_units] == ["src/foo.cc"]


def test_make_absolute_posix_source_path():
    # An absolute Unix source path must be recognized (not mistaken for an option).
    ev = MakeAdapter(dry_run="gcc -std=c11 -c /work/src/foo.c -o /work/build/foo.o").collect()
    assert [c.source for c in ev.compile_units] == ["/work/src/foo.c"]


def test_make_msvc_slash_c_compile_marker():
    # MSVC/clang-cl recipes use `/c` (and `/Fo<obj>`) rather than `-c`.
    ev = MakeAdapter(dry_run="cl.exe /std:c++17 /c foo.cc /Fofoo.obj").collect()
    assert [c.source for c in ev.compile_units] == ["foo.cc"]


def test_make_cd_prefixed_recipe_resolves_in_subdir():
    # `cd sub && …` makes the source and -I paths relative to sub/, not the parent.
    ev = MakeAdapter(build_dir="/proj/build",
                     dry_run="cd sub && gcc -Iinclude -std=c17 -c foo.c -o foo.o").collect()
    cu = ev.compile_units[0]
    assert cu.source == "foo.c"
    assert cu.standard == "c17"
    # Path separators differ across OSes (sub\include on Windows); normalize.
    assert cu.directory.replace("\\", "/").endswith("sub")    # advanced into cd target
    assert any(p.replace("\\", "/").endswith("sub/include") for p in cu.include_paths)


def test_make_entering_directory_sets_compile_cwd(tmp_path):
    src = tmp_path / "src" / "foo.cc"
    src.parent.mkdir()
    src.write_text("int f() { return 0; }\n")
    dry = (
        f"make: Entering directory '{tmp_path}'\n"
        "g++ -std=c++17 -c src/foo.cc -o build/foo.o\n"
        f"make: Leaving directory '{tmp_path}'\n"
    )
    cu = MakeAdapter(dry_run=dry).collect().compile_units[0]
    assert Path(cu.directory).expanduser() == tmp_path
    assert (Path(cu.directory).expanduser() / cu.source).is_file()


def test_make_expands_response_file_and_truncates_shell_suffix(tmp_path):
    src = tmp_path / "src" / "foo.cc"
    inc = tmp_path / "include"
    build = tmp_path / "build"
    src.parent.mkdir()
    inc.mkdir()
    build.mkdir()
    src.write_text("int f() { return 0; }\n")
    rsp = build / "inc.rsp"
    rsp.write_text("-Iinclude -DMODE=1\n")
    dry = (
        f"make: Entering directory '{tmp_path}'\n"
        "g++ @build/inc.rsp -std=c++17 -c src/foo.cc "
        "-obuild/foo.o && sed -n ignored.d\n"
        f"make: Leaving directory '{tmp_path}'\n"
    )
    cu = MakeAdapter(build_dir=tmp_path, dry_run=dry).collect().compile_units[0]
    assert "&&" not in cu.argv
    assert "@build/inc.rsp" not in cu.argv
    assert cu.defines["MODE"] == "1"
    assert cu.output == "build/foo.o"
    assert any(Path(p).expanduser() == inc for p in cu.include_paths)


def test_make_does_not_expand_response_file_without_trusted_build_dir(tmp_path):
    src = tmp_path / "foo.cc"
    rsp = tmp_path / "leak.rsp"
    src.write_text("int f() { return 0; }\n")
    rsp.write_text("LEAKED_TOKEN\n")

    cu = MakeAdapter(dry_run=f"g++ @{rsp} -c {src} -o foo.o").collect().compile_units[0]

    assert _has_response_arg(cu.argv, rsp)
    assert "LEAKED_TOKEN" not in cu.argv


def test_make_does_not_expand_response_file_outside_build_dir(tmp_path):
    build = tmp_path / "build"
    outside = tmp_path / "outside.rsp"
    src = build / "foo.cc"
    build.mkdir()
    src.write_text("int f() { return 0; }\n")
    outside.write_text("LEAKED_TOKEN\n")

    cu = MakeAdapter(build_dir=build, dry_run=f"g++ @{outside} -c foo.cc -o foo.o").collect().compile_units[0]

    assert _has_response_arg(cu.argv, outside)
    assert "LEAKED_TOKEN" not in cu.argv


def test_make_does_not_expand_response_file_from_forged_directory(tmp_path):
    build = tmp_path / "build"
    forged = tmp_path / "forged"
    build.mkdir()
    forged.mkdir()
    (build / "foo.cc").write_text("int f() { return 0; }\n")
    (forged / "rel.rsp").write_text("LEAKED_TOKEN\n")
    dry = (
        f"make: Entering directory '{forged}'\n"
        "g++ @rel.rsp -c foo.cc -o foo.o\n"
        f"make: Leaving directory '{forged}'\n"
    )

    cu = MakeAdapter(build_dir=build, dry_run=dry).collect().compile_units[0]

    assert "@rel.rsp" in cu.argv
    assert "LEAKED_TOKEN" not in cu.argv


def test_make_does_not_expand_large_response_file(tmp_path):
    src = tmp_path / "foo.cc"
    rsp = tmp_path / "large.rsp"
    src.write_text("int f() { return 0; }\n")
    rsp.write_text("A" * (1024 * 1024 + 1))

    cu = MakeAdapter(build_dir=tmp_path, dry_run="g++ @large.rsp -c foo.cc -o foo.o").collect().compile_units[0]

    assert "@large.rsp" in cu.argv


def test_make_does_not_expand_response_file_directory(tmp_path):
    src = tmp_path / "foo.cc"
    rsp_dir = tmp_path / "not-a-file.rsp"
    src.write_text("int f() { return 0; }\n")
    rsp_dir.mkdir()

    cu = MakeAdapter(build_dir=tmp_path, dry_run="g++ @not-a-file.rsp -c foo.cc -o foo.o").collect().compile_units[0]

    assert "@not-a-file.rsp" in cu.argv


def test_make_msvc_combined_forced_include_not_source():
    # `/FIsrc/config.hpp` is a combined MSVC forced-include with an embedded
    # path; despite the `.hpp` it must not be read as the TU — foo.cc wins.
    ev = MakeAdapter(dry_run="cl.exe /FIsrc/config.hpp /std:c++17 /c foo.cc /Fofoo.obj").collect()
    assert [c.source for c in ev.compile_units] == ["foo.cc"]


def test_make_msvc_tp_explicit_source():
    # MSVC/clang-cl name the TU via /Tp<file> (C++) / /Tc<file> (C).
    ev = MakeAdapter(dry_run="cl.exe /c /TpSrc/foo.cc /Fofoo.obj").collect()
    assert [c.source for c in ev.compile_units] == ["Src/foo.cc"]
    ev2 = MakeAdapter(dry_run="cl.exe /c /Tcfoo.c").collect()
    assert [c.source for c in ev2.compile_units] == ["foo.c"]


def test_make_no_compile_lines_yields_no_units():
    ev = MakeAdapter(dry_run="echo hello\nrm -f *.o\nmake[1]: Nothing to be done").collect()
    assert not ev.compile_units
    assert not any("reduced confidence" in d for d in ev.diagnostics)


def test_make_unbalanced_quotes_line_skipped():
    ev = MakeAdapter(dry_run='gcc -c "unterminated.c').collect()
    assert not ev.compile_units  # the malformed line is skipped, not a crash


def test_make_missing_dry_run_file_diagnostic(tmp_path):
    ev = MakeAdapter(dry_run=tmp_path / "nope.txt").collect()
    assert any("not found or unreadable" in d for d in ev.diagnostics)


def test_make_never_runs_make_without_transcript():
    # The adapter must NOT execute make (make -n still runs `+` recipes and
    # $(shell ...)); with no transcript it just records a diagnostic.
    import abicheck.buildsource.adapters.make as make_mod

    assert not hasattr(make_mod, "subprocess")  # no exec machinery imported at all
    ev = MakeAdapter(build_dir="/some/build").collect()
    assert not ev.compile_units
    assert any("never runs make" in d for d in ev.diagnostics)


def test_make_force_recipe_prefix_is_tokenized():
    # `+`-prefixed recipe lines are still parsed for facts (we never run them).
    ev = MakeAdapter(dry_run="+gcc -std=c++17 -c forced.cpp -o forced.o").collect()
    assert [c.source for c in ev.compile_units] == ["forced.cpp"]


def test_make_compile_recipe_without_source_skipped():
    # A `-c` line with no source token (e.g. a bare preprocessor probe).
    ev = MakeAdapter(dry_run="gcc -c -x c -").collect()
    assert not ev.compile_units


def test_collect_evidence_make_dry_run_cli(tmp_path):
    dr = tmp_path / "dry.txt"
    dr.write_text(DRY_RUN)
    out = tmp_path / "e"
    result = CliRunner().invoke(main, ["collect", "--from", f"make={dr}", "-o", str(out)])
    assert result.exit_code == 0, result.output
    pack = BuildSourcePack.load(out)
    assert pack.build_evidence is not None
    assert len(pack.build_evidence.compile_units) == 2
    assert any(e.name == "make" and e.status == "ok" for e in pack.manifest.extractors)

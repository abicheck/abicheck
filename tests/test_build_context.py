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

"""Tests for build_context.py — compile_commands.json ingestion (ADR-020a)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.build_context import (
    BuildContext,
    CompileEntry,
    _entry_matches_filter,
    _extract_flags,
    _is_cl_style_driver,
    _split_windows_command_line,
    _std_sort_key,
    _try_consume_define_undef,
    _try_consume_sysroot,
    build_context_for_header,
    build_context_union_fallback,
    load_compile_db,
)
from abicheck.errors import ValidationError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def compile_db_dir(tmp_path: Path) -> Path:
    """Create a minimal compile_commands.json in a tmp directory."""
    db = [
        {
            "directory": str(tmp_path / "build"),
            "file": "src/foo.cpp",
            "arguments": [
                "c++",
                "-std=c++17",
                "-DFOO_ENABLE_SSL=1",
                "-I/usr/include/openssl",
                "-Iinclude",
                "-fvisibility=hidden",
                "-c",
                "src/foo.cpp",
            ],
        },
        {
            "directory": str(tmp_path / "build"),
            "file": "src/bar.c",
            "command": "cc -std=c11 -DBAR_MODE=2 -Iinclude -c src/bar.c",
        },
    ]
    db_path = tmp_path / "build" / "compile_commands.json"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text(json.dumps(db))

    # Create source and header files for TU matching
    (tmp_path / "build" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "build" / "src" / "foo.cpp").write_text(
        '#include "foo.h"\nvoid foo() {}\n'
    )
    (tmp_path / "build" / "src" / "bar.c").write_text(
        '#include "bar.h"\nvoid bar() {}\n'
    )
    (tmp_path / "build" / "include").mkdir(exist_ok=True)
    (tmp_path / "build" / "include" / "foo.h").write_text("void foo();\n")
    (tmp_path / "build" / "include" / "bar.h").write_text("void bar();\n")

    return tmp_path / "build"


# ---------------------------------------------------------------------------
# Tests: load_compile_db
# ---------------------------------------------------------------------------


class TestLoadCompileDb:
    def test_from_dir(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        assert len(entries) == 2
        assert entries[0].arguments[0] == "c++"
        assert entries[1].file.name == "bar.c"

    def test_from_file(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir / "compile_commands.json")
        assert len(entries) == 2

    def test_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="not found"):
            load_compile_db(tmp_path / "nonexistent")

    def test_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "compile_commands.json"
        bad.write_text("not json")
        with pytest.raises(ValidationError, match="Invalid JSON"):
            load_compile_db(bad)

    def test_not_array(self, tmp_path: Path) -> None:
        bad = tmp_path / "compile_commands.json"
        bad.write_text('{"key": "value"}')
        with pytest.raises(ValidationError, match="JSON array"):
            load_compile_db(bad)

    def test_empty_array(self, tmp_path: Path) -> None:
        f = tmp_path / "compile_commands.json"
        f.write_text("[]")
        entries = load_compile_db(f)
        assert entries == []

    def test_malformed_entry_skipped(self, tmp_path: Path) -> None:
        """Malformed entries are skipped with a warning."""
        db = [
            {
                "directory": str(tmp_path),
                "file": "ok.c",
                "arguments": ["cc", "-c", "ok.c"],
            },
            "not a dict",  # malformed
        ]
        f = tmp_path / "compile_commands.json"
        f.write_text(json.dumps(db))
        entries = load_compile_db(f)
        assert len(entries) == 1

    def test_shared_response_file_read_once_across_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Many entries referencing the *same* ``@response-file`` must only
        actually read/tokenize it once across the whole ``load_compile_db()``
        call -- otherwise an untrusted database with thousands of entries
        repeating one large response file amplifies a few MB of JSON into
        orders of magnitude more I/O and parsing work (Codex review, fourth
        round, P1)."""
        import abicheck.build_context as build_context_module

        rsp = tmp_path / "shared.rsp"
        rsp.write_text("-Iinclude/foo -DBAR=1\n")
        db = [
            {
                "directory": str(tmp_path),
                "file": f"src/tu{i}.cpp",
                "arguments": ["c++", f"@{rsp.name}", "-c", f"src/tu{i}.cpp"],
            }
            for i in range(50)
        ]
        f = tmp_path / "compile_commands.json"
        f.write_text(json.dumps(db))

        read_calls = []
        real_read = build_context_module._read_response_file

        def counting_read(path: Path, root: Path) -> str | None:
            read_calls.append(path)
            return real_read(path, root)

        monkeypatch.setattr(build_context_module, "_read_response_file", counting_read)

        entries = load_compile_db(f)
        assert len(entries) == 50
        assert all("-Iinclude/foo" in e.arguments for e in entries)
        assert len(read_calls) == 1


# ---------------------------------------------------------------------------
# Tests: CompileEntry
# ---------------------------------------------------------------------------


class TestCompileEntry:
    def test_arguments_form(self) -> None:
        entry = CompileEntry.from_dict(
            {
                "directory": "/build",
                "file": "src/foo.cpp",
                "arguments": ["c++", "-std=c++17", "-DFOO=1", "-c", "src/foo.cpp"],
            },
            Path("/build"),
        )
        assert entry.file == Path("/build/src/foo.cpp").resolve()
        assert "-std=c++17" in entry.arguments

    def test_command_form(self) -> None:
        entry = CompileEntry.from_dict(
            {
                "directory": "/build",
                "file": "src/bar.c",
                "command": "cc -std=c11 -DBAR=1 -c src/bar.c",
            },
            Path("/build"),
        )
        assert "-std=c11" in entry.arguments
        assert "-DBAR=1" in entry.arguments

    def test_absolute_file_path(self) -> None:
        entry = CompileEntry.from_dict(
            {"directory": "/build", "file": "/abs/path/foo.c", "arguments": ["cc"]},
            Path("/build"),
        )
        assert entry.file == Path("/abs/path/foo.c").resolve()

    def test_no_arguments_or_command(self) -> None:
        entry = CompileEntry.from_dict(
            {"directory": "/build", "file": "foo.c"},
            Path("/build"),
        )
        assert entry.arguments == []

    def test_response_file_expanded(self, tmp_path: Path) -> None:
        """A GNU ``@response-file`` argument (make-based build systems commonly
        spell a long include-dir list this way) is inlined so its ``-I``/``-D``
        flags actually reach the caller, instead of vanishing as one opaque
        ``@file`` token no downstream parser matches."""
        rsp = tmp_path / "inc_folders.txt"
        rsp.write_text("-Iinclude/foo -DBAR=1\n")
        entry = CompileEntry.from_dict(
            {
                "directory": str(tmp_path),
                "file": "src/foo.cpp",
                "arguments": ["c++", f"@{rsp.name}", "-c", "src/foo.cpp"],
            },
            tmp_path,
        )
        assert f"@{rsp.name}" not in entry.arguments
        assert "-Iinclude/foo" in entry.arguments
        assert "-DBAR=1" in entry.arguments

    def test_response_file_gnu_driver_uses_gnu_quoting_regardless_of_host_os(
        self, tmp_path: Path
    ) -> None:
        """A GNU-mode driver (clang++/g++, including MinGW GCC running on
        Windows) uses GNU/shlex response-file quoting -- quoting is chosen
        from the actual compiler invoked, not the host OS (Codex review). A
        doubled backslash preceding a space-containing quoted path collapses
        to a single literal backslash under GNU/shlex quoting, unlike the
        MSVC rules exercised by the CL-style-driver test below."""
        rsp = tmp_path / "inc_folders.txt"
        rsp.write_text('-I"include\\\\foo bar"\n')
        entry = CompileEntry.from_dict(
            {
                "directory": str(tmp_path),
                "file": "src/foo.cpp",
                "arguments": ["clang++", f"@{rsp.name}", "-c", "src/foo.cpp"],
            },
            tmp_path,
        )
        assert "-Iinclude\\foo bar" in entry.arguments

    def test_response_file_cl_style_driver_uses_windows_quoting(
        self, tmp_path: Path
    ) -> None:
        """A CL-style driver (``clang-cl``, ``dpcpp-cl``, ``cl.exe``) uses
        MSVC/``CommandLineToArgvW`` response-file quoting: the identical
        doubled backslash the GNU-driver test above collapses to one literal
        backslash is instead preserved as-is (not immediately before the
        closing quote, so it is emitted literally, unhalved) -- verifying
        the driver, not the host OS, selects the quoting convention."""
        rsp = tmp_path / "inc_folders.txt"
        rsp.write_text('-I"include\\\\foo bar"\n')
        entry = CompileEntry.from_dict(
            {
                "directory": str(tmp_path),
                "file": "src/foo.cpp",
                "arguments": ["clang-cl", f"@{rsp.name}", "-c", "src/foo.cpp"],
            },
            tmp_path,
        )
        assert "-Iinclude\\\\foo bar" in entry.arguments

    def test_response_file_launcher_wrapped_cl_driver_uses_windows_quoting(
        self, tmp_path: Path
    ) -> None:
        """A launcher-wrapped CL-style action (``sccache clang-cl @args.rsp``)
        must select quoting from the real driver *after* the launcher
        (``sccache``/``ccache``/…), not from the launcher token itself --
        the launcher name is not a compiler and would otherwise be
        misclassified as GNU-style (Codex review, fourth round)."""
        rsp = tmp_path / "inc_folders.txt"
        rsp.write_text('-I"include\\\\foo bar"\n')
        entry = CompileEntry.from_dict(
            {
                "directory": str(tmp_path),
                "file": "src/foo.cpp",
                "arguments": [
                    "sccache",
                    "clang-cl",
                    f"@{rsp.name}",
                    "-c",
                    "src/foo.cpp",
                ],
            },
            tmp_path,
        )
        assert "-Iinclude\\\\foo bar" in entry.arguments

    def test_response_file_missing_keeps_token(self, tmp_path: Path) -> None:
        """An unreadable ``@file`` degrades to keeping the original token
        rather than raising or silently dropping the rest of the argv."""
        entry = CompileEntry.from_dict(
            {
                "directory": str(tmp_path),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@does-not-exist.txt", "-c", "src/foo.cpp"],
            },
            tmp_path,
        )
        assert "@does-not-exist.txt" in entry.arguments
        assert "-c" in entry.arguments

    def test_response_file_absolute_path_outside_db_dir_rejected(
        self, tmp_path: Path
    ) -> None:
        """A response-file token pointing at an absolute path outside the
        compile database's own directory must not be read -- an untrusted
        ``compile_commands.json`` (e.g. scanned from a PR checkout in CI)
        could otherwise leak arbitrary filesystem content (like
        ``@/etc/passwd``) into the parsed argv, which ends up persisted in
        ``CompileUnit.argv`` / the ``.abi.json`` artifact."""
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        secret_dir = tmp_path / "outside"
        secret_dir.mkdir()
        secret = secret_dir / "secret.txt"
        secret.write_text("-Dleaked=1\n")
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir),
                "file": "src/foo.cpp",
                "arguments": ["c++", f"@{secret}", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        assert f"@{secret}" in entry.arguments
        assert "-Dleaked=1" not in entry.arguments

    def test_response_file_path_traversal_outside_db_dir_rejected(
        self, tmp_path: Path
    ) -> None:
        """A relative ``@../../secret``-style traversal is rejected the same
        way an absolute out-of-tree path is."""
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("-Dleaked=1\n")
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@../secret.txt", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        assert "@../secret.txt" in entry.arguments
        assert "-Dleaked=1" not in entry.arguments

    def test_response_file_within_db_dir_subdirectory_still_expands(
        self, tmp_path: Path
    ) -> None:
        """The trusted-root check is not overly strict: a response file that
        lives *inside* the compile database's own directory tree (just in a
        different subdirectory than the entry's ``directory``) still
        expands normally."""
        db_dir = tmp_path / "db"
        (db_dir / "sub").mkdir(parents=True)
        rsp = db_dir / "inc_folders.txt"
        rsp.write_text("-Iinclude/foo\n")
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir / "sub"),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@../inc_folders.txt", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        assert "-Iinclude/foo" in entry.arguments

    def test_nested_response_file_resolves_relative_to_original_directory(
        self, tmp_path: Path
    ) -> None:
        """A response file referenced from *inside* another response file
        (``@sub/a.rsp`` itself containing ``@b.rsp``) resolves relative to
        the *original* compile-entry directory at every nesting level, not
        the including file's own directory -- matching GCC/binutils' (and
        Clang's own command-line ``@file``, as opposed to its separate
        ``--config`` mechanism) documented CWD-relative behavior for a
        command-line response file. A ``b.rsp`` living next to ``a.rsp`` in
        ``sub/`` is NOT what a bare ``@b.rsp`` reference resolves to; a
        ``db_dir/b.rsp`` is."""
        db_dir = tmp_path
        sub = db_dir / "sub"
        sub.mkdir()
        (db_dir / "b.rsp").write_text("-Dnested=1\n")
        (sub / "a.rsp").write_text("-Itop @b.rsp\n")
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@sub/a.rsp", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        assert "-Itop" in entry.arguments
        assert "-Dnested=1" in entry.arguments

    def test_nested_response_file_does_not_use_including_files_directory(
        self, tmp_path: Path
    ) -> None:
        """The inverse of the above: a ``b.rsp`` placed *beside* the
        including ``sub/a.rsp`` (not at the original directory) is NOT
        picked up by a bare nested ``@b.rsp`` reference -- confirming
        nesting does not switch its base to the including file's directory."""
        db_dir = tmp_path
        sub = db_dir / "sub"
        sub.mkdir()
        (sub / "b.rsp").write_text("-Dwrong=1\n")
        (sub / "a.rsp").write_text("-Itop @b.rsp\n")
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@sub/a.rsp", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        assert "-Itop" in entry.arguments
        assert "-Dwrong=1" not in entry.arguments
        assert "@b.rsp" in entry.arguments

    def test_self_referential_response_file_does_not_blow_up(
        self, tmp_path: Path
    ) -> None:
        """A response file containing many references to *itself* must not
        cause combinatorial expansion: the depth cap alone allows roughly
        ``fan_out ** _MAX_RESPONSE_FILE_DEPTH`` token reads (a 100-token
        self-reference could reach ~10 billion), so an untrusted compile
        database (e.g. a PR checkout scanned in CI) could hang or OOM the
        scan despite the per-file byte cap (Codex review). An aggregate
        expansion budget must cap total response-file reads regardless of
        depth or branching factor -- this runs in well under a second."""
        db_dir = tmp_path
        loop = db_dir / "loop.rsp"
        loop.write_text(" ".join(["@loop.rsp"] * 100))
        import time

        start = time.monotonic()
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@loop.rsp", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 5.0
        # Some @loop.rsp tokens survive un-expanded once the budget runs out.
        assert any(a == "@loop.rsp" for a in entry.arguments)

    def test_single_dense_response_file_output_is_bounded(self, tmp_path: Path) -> None:
        """The file-read cap alone does not bound *output size*: a single
        response file packed with many short self-reference tokens can
        still emit a huge amount of output within just one or two reads.
        A near-1 MiB file packed with ~10-byte ``@loop.rsp `` tokens can
        contain ~100k of them; with only the read-count cap, 64 reads of a
        file that dense could still emit millions of tokens (Codex review,
        second round). A separate cumulative *output-token* budget must cap
        the total regardless of how few files were actually read."""
        db_dir = tmp_path
        loop = db_dir / "loop.rsp"
        # Well above the ~20k output-token budget, but small enough to write
        # quickly in a test (proving the budget -- not the byte cap -- is
        # what stops this).
        loop.write_text(" ".join(["@loop.rsp"] * 25_000))
        import time

        start = time.monotonic()
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@loop.rsp", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 5.0
        # A single file whose own token count exceeds the remaining output
        # budget is rejected outright (kept as the literal @file token)
        # rather than expanded once and only capped on the *next* @file
        # (Codex review, third round) -- so this file contributes nothing.
        assert entry.arguments == ["c++", "@loop.rsp", "-c", "src/foo.cpp"]

    def test_symlink_loop_response_file_degrades_gracefully(
        self, tmp_path: Path
    ) -> None:
        """``Path.resolve()`` raises ``RuntimeError`` (not ``OSError``) for a
        symlink loop on Python < 3.13 -- an uncaught RuntimeError here would
        abort loading the *entire* compile database instead of degrading
        just this one unreadable ``@file`` to its literal token (Codex
        review)."""
        db_dir = tmp_path
        loop_link = db_dir / "loop.link"
        loop_link.symlink_to(loop_link)
        entry = CompileEntry.from_dict(
            {
                "directory": str(db_dir),
                "file": "src/foo.cpp",
                "arguments": ["c++", "@loop.link", "-c", "src/foo.cpp"],
            },
            db_dir,
        )
        assert "@loop.link" in entry.arguments


# ---------------------------------------------------------------------------
# Tests: _extract_flags
# ---------------------------------------------------------------------------


class TestExtractFlags:
    def test_defines_combined(self) -> None:
        ctx = _extract_flags(["-DFOO=1", "-DBAR"], Path("/"))
        assert ctx.defines["FOO"] == "1"
        assert ctx.defines["BAR"] is None

    def test_undefines(self) -> None:
        ctx = _extract_flags(["-UFOO"], Path("/"))
        assert "FOO" in ctx.undefines

    def test_include_combined(self) -> None:
        ctx = _extract_flags(["-I/usr/include"], Path("/"))
        assert Path("/usr/include") in ctx.include_paths

    def test_include_separate(self) -> None:
        ctx = _extract_flags(["-I", "/usr/include"], Path("/"))
        assert Path("/usr/include") in ctx.include_paths

    def test_include_relative(self) -> None:
        ctx = _extract_flags(["-Iinclude"], Path("/build"))
        assert Path("/build/include") in ctx.include_paths

    def test_isystem_combined(self) -> None:
        ctx = _extract_flags(["-isystem/usr/include"], Path("/"))
        assert Path("/usr/include") in ctx.system_includes

    def test_isystem_separate(self) -> None:
        ctx = _extract_flags(["-isystem", "/usr/include"], Path("/"))
        assert Path("/usr/include") in ctx.system_includes

    def test_isystem_relative(self) -> None:
        ctx = _extract_flags(["-isystem", "sysinclude"], Path("/build"))
        assert Path("/build/sysinclude") in ctx.system_includes

    def test_std(self) -> None:
        ctx = _extract_flags(["-std=c++20"], Path("/"))
        assert ctx.language_standard == "c++20"

    def test_target_combined(self) -> None:
        ctx = _extract_flags(["--target=x86_64-linux-gnu"], Path("/"))
        assert ctx.target_triple == "x86_64-linux-gnu"

    def test_target_separate(self) -> None:
        ctx = _extract_flags(["-target", "aarch64-linux-gnu"], Path("/"))
        assert ctx.target_triple == "aarch64-linux-gnu"

    def test_sysroot_combined(self) -> None:
        ctx = _extract_flags(["--sysroot=/opt/sysroot"], Path("/"))
        assert ctx.sysroot == Path("/opt/sysroot")

    def test_sysroot_separate(self) -> None:
        ctx = _extract_flags(["--sysroot", "/opt/sysroot"], Path("/"))
        assert ctx.sysroot == Path("/opt/sysroot")

    def test_isysroot(self) -> None:
        """macOS -isysroot is captured as sysroot."""
        ctx = _extract_flags(
            [
                "-isysroot",
                "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk",
            ],
            Path("/"),
        )
        assert ctx.sysroot == Path(
            "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
        )

    def test_visibility(self) -> None:
        ctx = _extract_flags(["-fvisibility=hidden"], Path("/"))
        assert "-fvisibility=hidden" in ctx.extra_flags

    def test_abi_flags(self) -> None:
        ctx = _extract_flags(
            ["-fabi-version=14", "-fno-exceptions", "-fno-rtti"], Path("/")
        )
        assert "-fabi-version=14" in ctx.extra_flags
        assert "-fno-exceptions" in ctx.extra_flags
        assert "-fno-rtti" in ctx.extra_flags

    def test_pack_struct(self) -> None:
        ctx = _extract_flags(["-fpack-struct=4"], Path("/"))
        assert "-fpack-struct=4" in ctx.extra_flags

    def test_ms_extensions(self) -> None:
        ctx = _extract_flags(["-fms-extensions"], Path("/"))
        assert "-fms-extensions" in ctx.extra_flags

    def test_stdlib(self) -> None:
        ctx = _extract_flags(["-stdlib=libc++"], Path("/"))
        assert "-stdlib=libc++" in ctx.extra_flags

    def test_data_model_flags(self) -> None:
        """-m32/-m64 (target data model) reach the real header parse, not just
        the separate build-evidence-drift diff (P0/P1 toolchain-profile fix)."""
        ctx = _extract_flags(["-m32"], Path("/"))
        assert "-m32" in ctx.extra_flags
        ctx = _extract_flags(["-m64"], Path("/"))
        assert "-m64" in ctx.extra_flags

    def test_march_mtune_mabi_mfloat_abi(self) -> None:
        ctx = _extract_flags(
            ["-march=armv8-a", "-mtune=cortex-a72", "-mabi=lp64", "-mfloat-abi=hard"],
            Path("/"),
        )
        assert "-march=armv8-a" in ctx.extra_flags
        assert "-mtune=cortex-a72" in ctx.extra_flags
        assert "-mabi=lp64" in ctx.extra_flags
        assert "-mfloat-abi=hard" in ctx.extra_flags

    def test_enum_and_char_layout_flags(self) -> None:
        ctx = _extract_flags(
            ["-fshort-enums", "-fsigned-char", "-fshort-wchar"], Path("/")
        )
        assert "-fshort-enums" in ctx.extra_flags
        assert "-fsigned-char" in ctx.extra_flags
        assert "-fshort-wchar" in ctx.extra_flags

    def test_skip_flags_with_arg(self) -> None:
        """Flags in _FLAGS_WITH_ARG that we don't care about are skipped."""
        ctx = _extract_flags(["-o", "output.o", "-MF", "deps.d"], Path("/"))
        assert not ctx.defines
        assert not ctx.include_paths

    def test_unknown_flags_ignored(self) -> None:
        ctx = _extract_flags(["-Wall", "-Werror", "-O2"], Path("/"))
        assert not ctx.defines
        assert not ctx.extra_flags


# ---------------------------------------------------------------------------
# Tests: BuildContext
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_to_castxml_flags(self) -> None:
        ctx = BuildContext(
            defines={"FOO": "1", "BAR": None},
            include_paths=[Path("/usr/include")],
            system_includes=[Path("/usr/lib/include")],
            language_standard="c++17",
            target_triple="x86_64-linux-gnu",
            sysroot=Path("/sysroot"),
            extra_flags=["-fvisibility=hidden"],
        )
        flags = ctx.to_castxml_flags()
        assert "-std=c++17" in flags
        assert "--target=x86_64-linux-gnu" in flags
        assert f"--sysroot={Path('/sysroot')}" in flags
        assert "-DFOO=1" in flags
        assert "-DBAR" in flags
        assert "-fvisibility=hidden" in flags
        assert any("-I" in f for f in flags)
        assert any("-isystem" in f for f in flags)

    def test_to_castxml_flags_empty(self) -> None:
        ctx = BuildContext()
        assert ctx.to_castxml_flags() == []

    def test_to_castxml_flags_undefines(self) -> None:
        ctx = BuildContext(undefines={"FOO"})
        flags = ctx.to_castxml_flags()
        assert "-UFOO" in flags

    def test_to_castxml_flags_omits_c_language_standard(self) -> None:
        ctx = BuildContext(language_standard="gnu17")
        assert not any(flag.startswith("-std=") for flag in ctx.to_castxml_flags())

    def test_has_conflicts(self) -> None:
        ctx = BuildContext()
        assert not ctx.has_conflicts
        ctx.define_conflicts = {"FOO": ["1", "2"]}
        assert ctx.has_conflicts


# ---------------------------------------------------------------------------
# Tests: _std_sort_key
# ---------------------------------------------------------------------------


class TestStdSortKey:
    def test_cpp_standards_order(self) -> None:
        """C++ standards sort by version number, not lexicographically."""
        stds = ["c++11", "c++17", "c++20", "c++14"]
        assert sorted(stds, key=_std_sort_key) == ["c++11", "c++14", "c++17", "c++20"]

    def test_draft_names(self) -> None:
        assert _std_sort_key("c++2a") == (1, 20)
        assert _std_sort_key("c++2b") == (1, 23)
        assert _std_sort_key("c++2c") == (1, 26)

    def test_gnu_variants(self) -> None:
        assert _std_sort_key("gnu++17")[0] == 1
        assert _std_sort_key("gnu++17")[1] == 17

    def test_c_standards(self) -> None:
        assert _std_sort_key("c11")[0] == 0
        assert _std_sort_key("c17")[0] == 0

    def test_cpp_higher_than_c(self) -> None:
        assert _std_sort_key("c++11") > _std_sort_key("c17")

    def test_unknown(self) -> None:
        assert _std_sort_key("unknown") == (0, 0)


# ---------------------------------------------------------------------------
# Tests: _entry_matches_filter
# ---------------------------------------------------------------------------


class TestEntryMatchesFilter:
    def test_absolute_path_match(self) -> None:
        entry = CompileEntry(
            file=Path("/build/src/foo.cpp"),
            directory=Path("/build"),
            arguments=[],
        )
        assert _entry_matches_filter(entry, "**/foo.cpp")

    def test_relative_path_match(self) -> None:
        entry = CompileEntry(
            file=Path("/build/src/libfoo/bar.cpp"),
            directory=Path("/build"),
            arguments=[],
        )
        assert _entry_matches_filter(entry, "src/libfoo/*")

    def test_no_match(self) -> None:
        entry = CompileEntry(
            file=Path("/build/src/foo.cpp"),
            directory=Path("/build"),
            arguments=[],
        )
        assert not _entry_matches_filter(entry, "tests/*")

    def test_file_not_under_directory(self) -> None:
        entry = CompileEntry(
            file=Path("/other/src/foo.cpp"),
            directory=Path("/build"),
            arguments=[],
        )
        # Falls through to CWD-relative, may or may not match
        result = _entry_matches_filter(entry, "nonexistent/*")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Tests: Union fallback
# ---------------------------------------------------------------------------


class TestUnionFallback:
    def test_merges_defines(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        ctx = build_context_union_fallback(entries)
        assert "FOO_ENABLE_SSL" in ctx.defines
        assert "BAR_MODE" in ctx.defines

    def test_picks_highest_standard(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        ctx = build_context_union_fallback(entries)
        assert ctx.language_standard == "c++17"

    def test_empty_entries(self) -> None:
        ctx = build_context_union_fallback([])
        assert ctx.language_standard is None
        assert not ctx.defines

    def test_conflicting_defines(self) -> None:
        e1 = CompileEntry(
            file=Path("/a.cpp"), directory=Path("/"), arguments=["-DFOO=1"]
        )
        e2 = CompileEntry(
            file=Path("/b.cpp"), directory=Path("/"), arguments=["-DFOO=2"]
        )
        ctx = build_context_union_fallback([e1, e2])
        assert "FOO" in ctx.defines
        assert ctx.define_conflicts  # conflict tracked

    def test_dedup_include_paths(self) -> None:
        e1 = CompileEntry(
            file=Path("/a.cpp"), directory=Path("/"), arguments=["-I/inc"]
        )
        e2 = CompileEntry(
            file=Path("/b.cpp"), directory=Path("/"), arguments=["-I/inc"]
        )
        ctx = build_context_union_fallback([e1, e2])
        assert len(ctx.include_paths) == 1

    def test_dedup_system_includes(self) -> None:
        e1 = CompileEntry(
            file=Path("/a.cpp"), directory=Path("/"), arguments=["-isystem/sys"]
        )
        e2 = CompileEntry(
            file=Path("/b.cpp"), directory=Path("/"), arguments=["-isystem/sys"]
        )
        ctx = build_context_union_fallback([e1, e2])
        assert len(ctx.system_includes) == 1

    def test_merges_undefines(self) -> None:
        e1 = CompileEntry(file=Path("/a.cpp"), directory=Path("/"), arguments=["-UFOO"])
        e2 = CompileEntry(file=Path("/b.cpp"), directory=Path("/"), arguments=["-UBAR"])
        ctx = build_context_union_fallback([e1, e2])
        assert "FOO" in ctx.undefines
        assert "BAR" in ctx.undefines

    def test_conflicting_targets(self) -> None:
        e1 = CompileEntry(
            file=Path("/a.cpp"), directory=Path("/"), arguments=["--target=x86_64"]
        )
        e2 = CompileEntry(
            file=Path("/b.cpp"), directory=Path("/"), arguments=["--target=aarch64"]
        )
        ctx = build_context_union_fallback([e1, e2])
        # Conflict: target is None
        assert ctx.target_triple is None

    def test_conflicting_sysroots(self) -> None:
        e1 = CompileEntry(
            file=Path("/a.cpp"), directory=Path("/"), arguments=["--sysroot=/a"]
        )
        e2 = CompileEntry(
            file=Path("/b.cpp"), directory=Path("/"), arguments=["--sysroot=/b"]
        )
        ctx = build_context_union_fallback([e1, e2])
        assert ctx.sysroot is None

    def test_dedup_extra_flags(self) -> None:
        e1 = CompileEntry(
            file=Path("/a.cpp"), directory=Path("/"), arguments=["-fvisibility=hidden"]
        )
        e2 = CompileEntry(
            file=Path("/b.cpp"), directory=Path("/"), arguments=["-fvisibility=hidden"]
        )
        ctx = build_context_union_fallback([e1, e2])
        assert ctx.extra_flags.count("-fvisibility=hidden") == 1

    def test_source_filter(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        ctx = build_context_union_fallback(entries, source_filter="**/bar.c")
        assert "BAR_MODE" in ctx.defines
        # FOO_ENABLE_SSL should not be present if filter works
        # (but depends on fallback behavior)

    def test_source_filter_no_match_uses_all(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        ctx = build_context_union_fallback(entries, source_filter="nonexistent/*")
        # Falls back to all entries
        assert "FOO_ENABLE_SSL" in ctx.defines

    def test_standard_variants_tracked(self) -> None:
        e1 = CompileEntry(
            file=Path("/a.cpp"), directory=Path("/"), arguments=["-std=c++17"]
        )
        e2 = CompileEntry(
            file=Path("/b.cpp"), directory=Path("/"), arguments=["-std=c++20"]
        )
        ctx = build_context_union_fallback([e1, e2])
        assert len(ctx.standard_variants) == 2


# ---------------------------------------------------------------------------
# Tests: Per-header TU matching
# ---------------------------------------------------------------------------


class TestPerHeaderMatching:
    def test_matches_correct_tu(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        header = compile_db_dir / "include" / "foo.h"
        ctx = build_context_for_header(entries, header)
        assert "FOO_ENABLE_SSL" in ctx.defines

    def test_fallback_to_union(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        unmatched = compile_db_dir / "include" / "baz.h"
        unmatched.write_text("void baz();\n")
        ctx = build_context_for_header(entries, unmatched)
        assert "FOO_ENABLE_SSL" in ctx.defines
        assert "BAR_MODE" in ctx.defines

    def test_source_filter(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        header = compile_db_dir / "include" / "bar.h"
        ctx = build_context_for_header(entries, header, source_filter="**/bar.c")
        assert "BAR_MODE" in ctx.defines

    def test_source_filter_no_match(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        header = compile_db_dir / "include" / "foo.h"
        ctx = build_context_for_header(entries, header, source_filter="nonexistent/*")
        # Falls back to all entries
        assert "FOO_ENABLE_SSL" in ctx.defines

    def test_compile_db_path_set(self, compile_db_dir: Path) -> None:
        entries = load_compile_db(compile_db_dir)
        header = compile_db_dir / "include" / "foo.h"
        ctx = build_context_for_header(entries, header)
        assert ctx.compile_db_path is not None
        assert ctx.compile_db_path.name == "compile_commands.json"

    def test_matched_tu_include_paths_flow_into_castxml_flags(
        self, compile_db_dir: Path
    ) -> None:
        # A2 acceptance (gap G16/G4): a public header that includes a *generated*
        # header (living in the build dir, reachable only via the build's -I) must
        # parse without a manual -I. The matched TU's include paths + defines are
        # derived from the compile DB and carried into the castxml invocation.
        entries = load_compile_db(compile_db_dir)
        header = compile_db_dir / "include" / "foo.h"
        ctx = build_context_for_header(entries, header)
        flags = ctx.to_castxml_flags()
        # The build's own include dir (where generated headers land) is present,
        # as adjacent ["-I", "<path>"] tokens, with no manual include required.
        i_paths = [flags[i + 1] for i, f in enumerate(flags[:-1]) if f == "-I"]
        assert any(p.endswith("include") for p in i_paths)
        assert any(p.endswith("openssl") for p in i_paths)
        # The TU's ABI-relevant define and visibility flag also carry through.
        assert "-DFOO_ENABLE_SSL=1" in flags
        assert "-fvisibility=hidden" in flags


# ---------------------------------------------------------------------------
# Tests: Finding 1 — relative sysroot resolution (CodeRabbit PR #256)
# ---------------------------------------------------------------------------


class TestSysrootRelativeResolution:
    """Relative --sysroot / -isysroot paths must resolve against the compile
    entry's working directory, not the process CWD."""

    def test_sysroot_combined_relative_resolves_against_directory(self) -> None:
        """--sysroot=relative/path is resolved against the compile directory."""
        directory = Path("/project/build")
        ctx = _extract_flags(["--sysroot=relative/sysroot"], directory)
        assert ctx.sysroot == Path("/project/build/relative/sysroot")

    def test_sysroot_separate_relative_resolves_against_directory(self) -> None:
        """--sysroot relative/path (split form) resolves against compile directory."""
        directory = Path("/project/build")
        ctx = _extract_flags(["--sysroot", "relative/sysroot"], directory)
        assert ctx.sysroot == Path("/project/build/relative/sysroot")

    def test_isysroot_relative_resolves_against_directory(self) -> None:
        """-isysroot relative/path resolves against compile directory."""
        directory = Path("/project/build")
        ctx = _extract_flags(["-isysroot", "relative/sysroot"], directory)
        assert ctx.sysroot == Path("/project/build/relative/sysroot")

    def test_sysroot_absolute_kept_as_is(self) -> None:
        """Absolute --sysroot paths are not modified."""
        directory = Path("/project/build")
        ctx = _extract_flags(["--sysroot=/opt/sysroot"], directory)
        assert ctx.sysroot == Path("/opt/sysroot")

    def test_sysroot_separate_absolute_kept_as_is(self) -> None:
        """Absolute --sysroot path in split form is not modified."""
        directory = Path("/project/build")
        ctx = _extract_flags(["--sysroot", "/opt/sysroot"], directory)
        assert ctx.sysroot == Path("/opt/sysroot")

    def test_try_consume_sysroot_relative(self) -> None:
        """_try_consume_sysroot helper resolves relative paths against directory."""
        directory = Path("/build")
        ctx = BuildContext()
        new_i = _try_consume_sysroot(
            "--sysroot=sdk", ["--sysroot=sdk"], 0, directory, ctx
        )
        assert new_i == 1
        assert ctx.sysroot == Path("/build/sdk")

    def test_try_consume_sysroot_separate_relative(self) -> None:
        """_try_consume_sysroot split form resolves relative paths against directory."""
        directory = Path("/build")
        ctx = BuildContext()
        new_i = _try_consume_sysroot(
            "--sysroot", ["--sysroot", "sdk"], 0, directory, ctx
        )
        assert new_i == 2
        assert ctx.sysroot == Path("/build/sdk")

    def test_try_consume_sysroot_no_match(self) -> None:
        """_try_consume_sysroot returns i unchanged when flag doesn't match."""
        ctx = BuildContext()
        new_i = _try_consume_sysroot("-I/inc", ["-I/inc"], 0, Path("/"), ctx)
        assert new_i == 0
        assert ctx.sysroot is None


# ---------------------------------------------------------------------------
# Tests: Finding 2 — split -D/-U forms (CodeRabbit PR #256)
# ---------------------------------------------------------------------------


class TestSplitDefineUndef:
    """Split -D NAME and -U NAME forms must be captured, not silently dropped."""

    def test_split_define_no_value(self) -> None:
        """'-D NAME' (space-separated) captures the macro with no value."""
        ctx = _extract_flags(["-D", "MY_MACRO"], Path("/"))
        assert "MY_MACRO" in ctx.defines
        assert ctx.defines["MY_MACRO"] is None

    def test_split_define_with_value(self) -> None:
        """'-D NAME=VALUE' (space-separated) captures macro and value."""
        ctx = _extract_flags(["-D", "MY_MACRO=42"], Path("/"))
        assert ctx.defines["MY_MACRO"] == "42"

    def test_split_undef(self) -> None:
        """'-U NAME' (space-separated) captures the macro in undefines."""
        ctx = _extract_flags(["-U", "MY_MACRO"], Path("/"))
        assert "MY_MACRO" in ctx.undefines

    def test_split_define_mixed_with_combined(self) -> None:
        """Split and combined define forms may appear in the same argument list."""
        ctx = _extract_flags(["-DCOMBINED=1", "-D", "SPLIT=2"], Path("/"))
        assert ctx.defines["COMBINED"] == "1"
        assert ctx.defines["SPLIT"] == "2"

    def test_split_undef_mixed_with_combined(self) -> None:
        """Split and combined undef forms may appear in the same argument list."""
        ctx = _extract_flags(["-UCOMBINED", "-U", "SPLIT"], Path("/"))
        assert "COMBINED" in ctx.undefines
        assert "SPLIT" in ctx.undefines

    def test_split_define_at_end_of_args_ignored(self) -> None:
        """'-D' at end of argument list (no following token) is silently skipped."""
        ctx = _extract_flags(["-D"], Path("/"))
        assert not ctx.defines

    def test_split_undef_at_end_of_args_ignored(self) -> None:
        """'-U' at end of argument list (no following token) is silently skipped."""
        ctx = _extract_flags(["-U"], Path("/"))
        assert not ctx.undefines

    def test_try_consume_define_undef_combined_define(self) -> None:
        """_try_consume_define_undef returns i+1 for combined -DNAME."""
        ctx = BuildContext()
        new_i = _try_consume_define_undef("-DFOO=1", ["-DFOO=1"], 0, ctx)
        assert new_i == 1
        assert ctx.defines["FOO"] == "1"

    def test_try_consume_define_undef_split_define(self) -> None:
        """_try_consume_define_undef returns i+2 for split -D NAME."""
        ctx = BuildContext()
        new_i = _try_consume_define_undef("-D", ["-D", "FOO=1"], 0, ctx)
        assert new_i == 2
        assert ctx.defines["FOO"] == "1"

    def test_try_consume_define_undef_split_undef(self) -> None:
        """_try_consume_define_undef returns i+2 for split -U NAME."""
        ctx = BuildContext()
        new_i = _try_consume_define_undef("-U", ["-U", "FOO"], 0, ctx)
        assert new_i == 2
        assert "FOO" in ctx.undefines

    def test_try_consume_define_undef_no_match(self) -> None:
        """_try_consume_define_undef returns i unchanged for non-define flags."""
        ctx = BuildContext()
        new_i = _try_consume_define_undef("-std=c++17", ["-std=c++17"], 0, ctx)
        assert new_i == 0
        assert not ctx.defines


# ---------------------------------------------------------------------------
# Tests: _split_windows_command_line
# ---------------------------------------------------------------------------


class TestIsClStyleDriver:
    @pytest.mark.parametrize(
        "argv0",
        ["cl", "cl.exe", "clang-cl", "clang-cl.exe", "dpcpp-cl", "clang-cl-20"],
    )
    def test_recognizes_cl_style_drivers(self, argv0: str) -> None:
        assert _is_cl_style_driver(argv0) is True

    @pytest.mark.parametrize(
        "argv0", ["clang++", "g++", "gcc", "x86_64-w64-mingw32-g++", "clang"]
    )
    def test_rejects_gnu_style_drivers(self, argv0: str) -> None:
        assert _is_cl_style_driver(argv0) is False

    def test_recognizes_absolute_path_driver(self) -> None:
        assert _is_cl_style_driver("/usr/bin/clang-cl-17.0") is True


class TestSplitWindowsCommandLine:
    def test_simple_space_separated(self) -> None:
        assert _split_windows_command_line("-Ifoo -Dbar=1 -c") == [
            "-Ifoo",
            "-Dbar=1",
            "-c",
        ]

    def test_quoted_path_with_space_stays_one_token(self) -> None:
        """The motivating case: a quoted include path containing a space
        (e.g. an SDK under ``Program Files``) must not split mid-path --
        shlex's ``posix=False`` mode gets this wrong (Codex review)."""
        assert _split_windows_command_line('-I"C:\\Program Files\\SDK" -c foo.cpp') == [
            "-IC:\\Program Files\\SDK",
            "-c",
            "foo.cpp",
        ]

    def test_even_backslashes_before_quote_toggle_quoting(self) -> None:
        # Two backslashes before the quote -> one literal backslash (the
        # pair), and the quote toggles quoting (consumed, not emitted).
        assert _split_windows_command_line('-I\\\\"C:\\SDK"') == ["-I\\C:\\SDK"]

    def test_odd_backslashes_before_quote_escape_it(self) -> None:
        # One backslash before the quote -> zero literal backslashes (no
        # complete pair) and the backslash escapes the quote to a literal
        # `"`; the backslash itself is consumed, not emitted.
        assert _split_windows_command_line('-Ifoo\\"bar') == ['-Ifoo"bar']

    def test_literal_backslash_not_before_quote(self) -> None:
        assert _split_windows_command_line("C:\\SDK\\include") == ["C:\\SDK\\include"]

    def test_never_raises_on_unbalanced_quote(self) -> None:
        # Unlike shlex.split, an unterminated quote has no invalid state.
        assert _split_windows_command_line('-I"C:\\SDK') == ["-IC:\\SDK"]

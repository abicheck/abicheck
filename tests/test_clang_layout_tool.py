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

"""Unit tests for G28 Phase 4: clang_layout_tool.py.

Covers the optional/opt-in binary resolution, the ast-dump-command ->
compile-flags slicing, the subprocess invocation (mocked, no real compiler
needed), and the fact-application merge logic. No test here requires the
real compiled companion tool or a real clang -- that lives in
tools/clang-layout-tool/tests/ (built + run only when explicitly requested).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from abicheck.clang_layout_tool import (
    LAYOUT_TOOL_ENV_VAR,
    _apply_record_facts,
    _bare_base_name,
    _compile_flags_from_ast_dump_command,
    _expand_header_inputs,
    apply_layout_facts,
    attach_clang_layout,
    find_layout_tool_bin,
    run_layout_tool,
)
from abicheck.errors import ValidationError
from abicheck.model import AbiSnapshot, RecordType, TypeField


class TestFindLayoutToolBin:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv(LAYOUT_TOOL_ENV_VAR, "/custom/path/tool")
        assert find_layout_tool_bin() == "/custom/path/tool"

    def test_falls_back_to_path(self, monkeypatch):
        monkeypatch.delenv(LAYOUT_TOOL_ENV_VAR, raising=False)
        with patch("shutil.which", return_value="/usr/bin/abicheck-clang-layout-tool"):
            assert find_layout_tool_bin() == "/usr/bin/abicheck-clang-layout-tool"

    def test_none_when_unavailable(self, monkeypatch):
        monkeypatch.delenv(LAYOUT_TOOL_ENV_VAR, raising=False)
        with patch("shutil.which", return_value=None):
            assert find_layout_tool_bin() is None


class TestCompileFlagsSlicing:
    def test_strips_cc_bin_and_ast_dump_tail(self):
        cmd = [
            "clang++", "-I", "/inc", "-std=gnu++17",
            "-fsyntax-only", "-ferror-limit=0", "-Xclang", "-ast-dump=json",
            "/tmp/agg.hpp",
        ]
        flags = _compile_flags_from_ast_dump_command(cmd)
        assert flags == [
            "-I", "/inc", "-std=gnu++17", "-fsyntax-only", "-ferror-limit=0",
        ]

    def test_defensive_fallback_when_no_xclang(self):
        cmd = ["clang++", "-I", "/inc"]
        assert _compile_flags_from_ast_dump_command(cmd) == ["-I", "/inc"]

    def test_user_supplied_earlier_xclang_is_preserved(self):
        # Codex review: a user's own "-Xclang <arg>" passed through
        # --gcc-options/--gcc-option sits BEFORE abicheck's own appended
        # "-Xclang -ast-dump=json" tail. The first bare "-Xclang" in the
        # command is the user's, not ours -- stopping there would drop the
        # user's own flag/value plus every later shared flag (system
        # includes, language mode), not just abicheck's dump-mode tail.
        cmd = [
            "clang++", "-I", "/inc", "-Xclang", "-some-user-flag",
            "-isystem", "/usr/include/probed",
            "-fsyntax-only", "-ferror-limit=0", "-Xclang", "-ast-dump=json",
            "/tmp/agg.hpp",
        ]
        flags = _compile_flags_from_ast_dump_command(cmd)
        assert flags == [
            "-I", "/inc", "-Xclang", "-some-user-flag",
            "-isystem", "/usr/include/probed",
            "-fsyntax-only", "-ferror-limit=0",
        ]


class TestRunLayoutTool:
    def test_no_headers_returns_none(self):
        assert run_layout_tool("some-binary", [], []) is None

    def test_missing_clang_driver_returns_none(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo {};")
        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin",
            side_effect=Exception("no clang"),
        ):
            assert run_layout_tool("some-binary", [header], []) is None

    def test_subprocess_timeout_returns_none(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo {};")
        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(True, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1),
        ):
            assert run_layout_tool("some-binary", [header], []) is None

    def test_malformed_json_returns_none(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo {};")
        fake_result = subprocess.CompletedProcess(
            args=["x"], returncode=0, stdout="not json", stderr=""
        )
        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(True, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool.subprocess.run", return_value=fake_result
        ):
            assert run_layout_tool("some-binary", [header], []) is None

    def test_records_missing_or_wrong_type_returns_none(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo {};")
        fake_result = subprocess.CompletedProcess(
            args=["x"], returncode=0, stdout='{"ok": true, "records": "oops"}', stderr=""
        )
        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(True, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool.subprocess.run", return_value=fake_result
        ):
            assert run_layout_tool("some-binary", [header], []) is None

    def test_failed_parse_rejects_partial_records(self, tmp_path):
        # Codex review: main.cpp's own comment says a recoverable-error
        # parse still emits "ok": false plus whatever partial records it
        # produced for the declarations it did visit -- trusting those
        # anyway would let a hybrid/clang snapshot carry layout facts for an
        # arbitrary, silently-incomplete subset of records instead of
        # cleanly degrading to no enrichment, mirroring
        # dumper_clang_errors._parse_clang_ast_result's own "the L2 header
        # AST must be complete to be authoritative" contract for the main
        # clang dump.
        header = tmp_path / "a.h"
        header.write_text("struct Foo { int a; };")
        fake_result = subprocess.CompletedProcess(
            args=["x"], returncode=0,
            stdout='{"ok": false, "records": [{"qualified_name": "Foo", "size_bits": 32}]}',
            stderr="",
        )
        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(True, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool.subprocess.run", return_value=fake_result
        ):
            assert run_layout_tool("some-binary", [header], []) is None

    def test_successful_run_returns_records_and_cleans_up_agg_file(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo { int a; };")
        fake_records = [{"qualified_name": "Foo", "size_bits": 32}]
        captured_cmd = {}

        def _fake_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            agg_path = Path(cmd[1])
            assert agg_path.exists()
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=__import__("json").dumps({"ok": True, "records": fake_records}),
                stderr="",
            )

        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(True, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_system_includes",
            return_value=(),
        ), patch(
            "abicheck.clang_layout_tool.subprocess.run", side_effect=_fake_run
        ):
            result = run_layout_tool("/path/to/tool", [header], [])

        assert result == fake_records
        assert captured_cmd["cmd"][0] == "/path/to/tool"
        assert captured_cmd["cmd"][2] == "--"
        # The aggregate temp file must be cleaned up after the run.
        agg_path = Path(captured_cmd["cmd"][1])
        assert not agg_path.exists()

    def test_probed_system_includes_are_threaded_into_compile_flags(self, tmp_path):
        # Codex review: without this, a header set that only parses because
        # of dumper._clang_header_dump's own libstdc++/libc auto-probe
        # succeeds for the original direct-clang dump but fails here,
        # silently losing the whole layout enrichment.
        header = tmp_path / "a.h"
        header.write_text("struct Foo { int a; };")
        captured_cmd = {}

        def _fake_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"ok": true, "records": []}', stderr="",
            )

        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(True, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_system_includes",
            return_value=("/usr/include/probed-libstdcxx",),
        ) as mock_probe, patch(
            "abicheck.clang_layout_tool.subprocess.run", side_effect=_fake_run
        ):
            run_layout_tool(
                "/path/to/tool", [header], [], gcc_options="--sysroot=/x"
            )

        # The probe itself must be called with the caller's compile context.
        mock_probe.assert_called_once()
        assert mock_probe.call_args.kwargs["gcc_options"] == "--sysroot=/x"
        assert mock_probe.call_args.kwargs["force_cpp"] is True
        # And its result must land in the actual compile command as -isystem.
        cmd = captured_cmd["cmd"]
        idx = cmd.index("-isystem")
        assert cmd[idx + 1] == "/usr/include/probed-libstdcxx"

    def test_cpp_selfheal_retry_on_missing_cpp_stdlib_header(self, tmp_path):
        # Codex review: this second, independent clang pass re-derives its own
        # initial force_cpp guess via _resolve_clang_langmode -- the SAME
        # content-based heuristic dumper._clang_header_dump used BEFORE ITS
        # OWN C->C++ self-heal retry. If that initial guess was wrong (e.g. a
        # pure-#include C++ umbrella header), this tool has to self-heal the
        # exact same way or it silently loses ALL enrichment on an otherwise-
        # valid dump.
        header = tmp_path / "a.h"
        header.write_text("#include <vector>\n")
        attempt = {"n": 0}

        def _fake_run(cmd, **kwargs):
            attempt["n"] += 1
            if attempt["n"] == 1:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout='{"ok": false, "records": []}',
                    stderr="fatal error: 'cstddef' file not found\n",
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"ok": true, "records": [{"qualified_name": "Foo", "size_bits": 32}]}',
                stderr="",
            )

        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(False, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_system_includes",
            return_value=(),
        ), patch(
            "abicheck.clang_layout_tool.subprocess.run", side_effect=_fake_run
        ):
            result = run_layout_tool("/path/to/tool", [header], [])

        assert attempt["n"] == 2
        assert result == [{"qualified_name": "Foo", "size_bits": 32}]

    def test_error_header_exclusion_retry(self, tmp_path):
        # Codex review: mirrors dumper._clang_header_dump's own graceful
        # #error handling -- a header not meant for direct inclusion must be
        # dropped and the rest re-parsed, not silently lose the whole
        # enrichment pass.
        h_ok = tmp_path / "a.h"
        h_ok.write_text("struct Foo { int a; };\n")
        h_bad = tmp_path / "bad.h"
        h_bad.write_text("#error do not #include this internal header directly\n")
        attempt = {"n": 0}

        def _fake_run(cmd, **kwargs):
            agg_path = Path(cmd[1])
            attempt["n"] += 1
            if attempt["n"] == 1:
                stderr = (
                    f"In file included from {agg_path}:2:\n"
                    f"{h_bad}:1:1: error: do not #include this internal "
                    "header directly\n"
                    "    1 | #error do not #include this internal header "
                    "directly\n"
                )
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout='{"ok": false, "records": []}', stderr=stderr,
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"ok": true, "records": [{"qualified_name": "Foo", "size_bits": 32}]}',
                stderr="",
            )

        with patch(
            "abicheck.clang_layout_tool._resolve_clang_bin", return_value="clang++"
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_langmode",
            return_value=(True, False, False, "gnu"),
        ), patch(
            "abicheck.clang_layout_tool._resolve_clang_system_includes",
            return_value=(),
        ), patch(
            "abicheck.clang_layout_tool.subprocess.run", side_effect=_fake_run
        ):
            result = run_layout_tool("/path/to/tool", [h_ok, h_bad], [])

        assert attempt["n"] == 2
        assert result == [{"qualified_name": "Foo", "size_bits": 32}]


class TestBareBaseName:
    def test_strips_namespace_qualifier(self):
        assert _bare_base_name("ns::Base") == "Base"

    def test_strips_nested_namespace_qualifier(self):
        assert _bare_base_name("outer::inner::Base") == "Base"

    def test_already_bare_name_unchanged(self):
        assert _bare_base_name("Base") == "Base"

    def test_preserves_template_args_containing_scope_operator(self):
        # The "::" inside the template argument (std::vector<int>) must not be
        # mistaken for a scope separator -- only the bracket-depth-0 "::"
        # right before "Widget" is the real scope boundary.
        assert (
            _bare_base_name("ns::Widget<std::vector<int>>")
            == "Widget<std::vector<int>>"
        )


class TestApplyRecordFacts:
    def test_backfills_only_none_scalar_fields(self):
        t = RecordType(
            name="Foo", kind="struct", size_bits=None, alignment_bits=64,
        )
        facts = {"size_bits": 192, "alignment_bits": 128, "data_size_bits": 192}
        updated = _apply_record_facts(t, facts)
        assert updated.size_bits == 192
        # alignment_bits was already set (64) -- must NOT be overridden by the
        # tool's value (128), even though the tool reported one.
        assert updated.alignment_bits == 64
        assert updated.data_size_bits == 192

    def test_backfills_base_offsets_only_when_empty(self):
        t = RecordType(name="Derived", kind="class", bases=["Base"], base_offsets={})
        facts = {"bases": [{"name": "Base", "offset_bits": 0, "is_virtual": False}]}
        updated = _apply_record_facts(t, facts)
        assert updated.base_offsets == {"Base": 0}

    def test_base_offsets_keyed_bare_not_qualified(self):
        # Codex review: the tool emits Clang's fully-qualified
        # getQualifiedNameAsString() for a base ("ns::Base"), but castxml/DWARF
        # both key base_offsets by the BARE name only ("Base") -- storing the
        # qualified spelling here would make a namespaced base's offset
        # incomparable against a castxml/DWARF baseline's base_offsets dict
        # (_check_base_offsets does an exact key lookup).
        t = RecordType(name="Derived", kind="class", bases=["ns::Base"], base_offsets={})
        facts = {"bases": [{"name": "ns::Base", "offset_bits": 64, "is_virtual": False}]}
        updated = _apply_record_facts(t, facts)
        assert updated.base_offsets == {"Base": 64}

    def test_does_not_override_existing_base_offsets(self):
        t = RecordType(
            name="Derived", kind="class", bases=["Base"], base_offsets={"Base": 64}
        )
        facts = {"bases": [{"name": "Base", "offset_bits": 0, "is_virtual": False}]}
        updated = _apply_record_facts(t, facts)
        assert updated.base_offsets == {"Base": 64}

    def test_backfills_field_offsets_only_when_none(self):
        f1 = TypeField(name="a", type="int", offset_bits=None)
        f2 = TypeField(name="b", type="int", offset_bits=32)
        t = RecordType(name="Foo", kind="struct", fields=[f1, f2])
        facts = {
            "fields": [
                {"name": "a", "offset_bits": 0},
                {"name": "b", "offset_bits": 999},
            ]
        }
        updated = _apply_record_facts(t, facts)
        assert updated.fields[0].offset_bits == 0
        # b already had a real offset -- must not be overridden.
        assert updated.fields[1].offset_bits == 32

    def test_no_matching_facts_returns_same_object(self):
        t = RecordType(name="Foo", kind="struct")
        updated = _apply_record_facts(t, {})
        assert updated is t


class TestApplyLayoutFacts:
    def test_none_records_is_noop(self):
        snap = AbiSnapshot(library="lib", version="1.0")
        assert apply_layout_facts(snap, None) is snap

    def test_empty_records_is_noop(self):
        snap = AbiSnapshot(library="lib", version="1.0")
        assert apply_layout_facts(snap, []) is snap

    def test_matches_by_qualified_name(self):
        t = RecordType(
            name="Foo", kind="struct", qualified_name="ns::Foo", size_bits=None
        )
        snap = AbiSnapshot(library="lib", version="1.0", types=[t])
        records = [{"qualified_name": "ns::Foo", "size_bits": 64}]
        updated = apply_layout_facts(snap, records)
        assert updated.type_by_name("Foo").size_bits == 64

    def test_matches_by_bare_name_when_no_qualified_name(self):
        t = RecordType(name="Foo", kind="struct", size_bits=None)
        snap = AbiSnapshot(library="lib", version="1.0", types=[t])
        records = [{"qualified_name": "Foo", "size_bits": 64}]
        updated = apply_layout_facts(snap, records)
        assert updated.type_by_name("Foo").size_bits == 64

    def test_no_match_returns_snapshot_unchanged(self):
        t = RecordType(name="Foo", kind="struct", size_bits=None)
        snap = AbiSnapshot(library="lib", version="1.0", types=[t])
        records = [{"qualified_name": "Bar", "size_bits": 64}]
        assert apply_layout_facts(snap, records) is snap

    def test_type_lookup_cache_invalidated_after_update(self):
        t = RecordType(name="Foo", kind="struct", size_bits=None)
        snap = AbiSnapshot(library="lib", version="1.0", types=[t])
        # Warm the lazy cache before enrichment.
        assert snap.type_by_name("Foo").size_bits is None
        records = [{"qualified_name": "Foo", "size_bits": 64}]
        updated = apply_layout_facts(snap, records)
        assert updated.type_by_name("Foo").size_bits == 64


class TestExpandHeaderInputs:
    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(ValidationError):
            _expand_header_inputs([tmp_path / "nope.h"])

    def test_file_passes_through(self, tmp_path):
        h = tmp_path / "a.h"
        h.write_text("")
        assert _expand_header_inputs([h]) == [h]

    def test_directory_expands_and_dedupes(self, tmp_path):
        (tmp_path / "a.h").write_text("")
        (tmp_path / "b.hpp").write_text("")
        result = _expand_header_inputs([tmp_path, tmp_path / "a.h"])
        names = sorted(p.name for p in result)
        assert names == ["a.h", "b.hpp"]

    def test_empty_directory_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValidationError):
            _expand_header_inputs([empty])


class TestAttachClangLayout:
    def test_noop_when_pure_castxml_producer(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo {};")
        snap = AbiSnapshot(library="lib", version="1.0", ast_producer="castxml")
        result = attach_clang_layout(snap, [header], [], lang=None, compile=None)
        assert result is snap

    def test_noop_when_no_headers(self):
        snap = AbiSnapshot(library="lib", version="1.0", ast_producer="clang")
        result = attach_clang_layout(snap, [], [], lang=None, compile=None)
        assert result is snap

    def test_noop_when_tool_unavailable(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo {};")
        snap = AbiSnapshot(library="lib", version="1.0", ast_producer="clang")
        with patch(
            "abicheck.clang_layout_tool.find_layout_tool_bin", return_value=None
        ):
            result = attach_clang_layout(snap, [header], [], lang=None, compile=None)
        assert result is snap

    def test_noop_on_bad_header_path(self, tmp_path):
        snap = AbiSnapshot(library="lib", version="1.0", ast_producer="clang")
        with patch(
            "abicheck.clang_layout_tool.find_layout_tool_bin",
            return_value="/fake/tool",
        ):
            result = attach_clang_layout(
                snap, [tmp_path / "missing.h"], [], lang=None, compile=None
            )
        assert result is snap

    def test_enriches_when_tool_available(self, tmp_path):
        header = tmp_path / "a.h"
        header.write_text("struct Foo { int a; };")
        t = RecordType(name="Foo", kind="struct", size_bits=None)
        snap = AbiSnapshot(
            library="lib", version="1.0", ast_producer="clang", types=[t]
        )
        fake_records = [{"qualified_name": "Foo", "size_bits": 32}]
        with patch(
            "abicheck.clang_layout_tool.find_layout_tool_bin",
            return_value="/fake/tool",
        ), patch(
            "abicheck.clang_layout_tool.run_layout_tool", return_value=fake_records
        ) as mock_run:
            result = attach_clang_layout(snap, [header], [], lang=None, compile=None)
        assert result.type_by_name("Foo").size_bits == 32
        mock_run.assert_called_once()
        # binary is the first positional arg
        assert mock_run.call_args[0][0] == "/fake/tool"
        # Codex review: lang=None (not "c") must resolve to the c++ driver.
        assert mock_run.call_args.kwargs["compiler"] == "c++"

    def test_c_lang_uses_c_compiler_not_cxx(self, tmp_path):
        # Codex review: the main clang dump resolves "cc" (not "c++") for a
        # --lang c dump (cli_dump_helpers.perform_elf_dump /
        # service._attach_header_graph's own convention). Without mirroring
        # that here, a C-only toolchain with no clang++ at all would fail to
        # resolve any driver for this second pass and silently lose every C
        # struct's layout enrichment, even though the main dump succeeded.
        header = tmp_path / "a.h"
        header.write_text("struct Foo { int a; };")
        snap = AbiSnapshot(library="lib", version="1.0", ast_producer="clang")
        with patch(
            "abicheck.clang_layout_tool.find_layout_tool_bin",
            return_value="/fake/tool",
        ), patch(
            "abicheck.clang_layout_tool.run_layout_tool", return_value=None
        ) as mock_run:
            attach_clang_layout(snap, [header], [], lang="c", compile=None)
        assert mock_run.call_args.kwargs["compiler"] == "cc"

    def test_enriches_clang_only_records_in_a_hybrid_snapshot(self, tmp_path):
        # Codex review: cli_dump_helpers.perform_elf_dump's --ast-frontend
        # hybrid path goes through dumper.dump() -> dumper_hybrid.run_hybrid_dump,
        # which never enriches either sub-dump (importing this module from
        # dumper_hybrid.py would close a real cycle back through dumper.py).
        # The merged "hybrid" snapshot must still get enriched here, or a
        # saved JSON baseline from `abicheck dump --ast-frontend hybrid`
        # never carries layout facts for the clang-only records the merge
        # appended.
        header = tmp_path / "a.h"
        header.write_text("struct Foo { int a; }; struct Bar { int b; };")
        castxml_backed = RecordType(
            name="Foo", kind="struct", size_bits=192, alignment_bits=32,
        )
        clang_only = RecordType(name="Bar", kind="struct", size_bits=None)
        snap = AbiSnapshot(
            library="lib", version="1.0", ast_producer="hybrid",
            types=[castxml_backed, clang_only],
        )
        fake_records = [
            {"qualified_name": "Foo", "size_bits": 999},  # must NOT be applied
            {"qualified_name": "Bar", "size_bits": 32},
        ]
        with patch(
            "abicheck.clang_layout_tool.find_layout_tool_bin",
            return_value="/fake/tool",
        ), patch(
            "abicheck.clang_layout_tool.run_layout_tool", return_value=fake_records
        ):
            result = attach_clang_layout(snap, [header], [], lang=None, compile=None)
        # The castxml-sourced record already had real layout -- untouched.
        assert result.type_by_name("Foo").size_bits == 192
        # The clang-only record had no layout at all -- backfilled.
        assert result.type_by_name("Bar").size_bits == 32

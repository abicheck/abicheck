"""Unit tests for abicheck.service — targeting ≥80% coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.checker_types import DiffResult
from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot, DependencyInfo, Function, Visibility
from abicheck.service import (
    _render_deps_section_md,
    collect_metadata,
    detect_binary_format,
    expand_header_inputs,
    load_suppression_and_policy,
    render_output,
    resolve_input,
    run_compare,
    run_dump,
    sniff_text_format,
)

# ── detect_binary_format() ──────────────────────────────────────────────────


class TestDetectBinaryFormat:
    def test_delegates_to_binary_utils(self, tmp_path):
        p = tmp_path / "test.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        result = detect_binary_format(p)
        assert result == "elf"

    def test_non_binary_returns_none(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("hello world")
        result = detect_binary_format(p)
        assert result is None


# ── sniff_text_format() ─────────────────────────────────────────────────────


class TestSniffTextFormat:
    def test_json_format(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text('{"library": "test"}')
        assert sniff_text_format(p) == "json"

    def test_perl_format(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = { 'Headers' => {} };")
        assert sniff_text_format(p) == "perl"

    def test_unknown_format(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("Some random text content")
        assert sniff_text_format(p) == "unknown"

    def test_oserror_returns_unknown(self, tmp_path):
        p = tmp_path / "nonexistent"
        assert sniff_text_format(p) == "unknown"

    def test_json_with_whitespace(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text("   \n  {}")
        assert sniff_text_format(p) == "json"


# ── expand_header_inputs() ──────────────────────────────────────────────────


class TestExpandHeaderInputs:
    def test_single_file(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("#pragma once")
        result = expand_header_inputs([h])
        assert result == [h]

    def test_directory_expansion(self, tmp_path):
        d = tmp_path / "include"
        d.mkdir()
        (d / "a.h").write_text("")
        (d / "b.hpp").write_text("")
        (d / "c.txt").write_text("")  # not a header
        result = expand_header_inputs([d])
        names = {p.name for p in result}
        assert "a.h" in names
        assert "b.hpp" in names
        assert "c.txt" not in names

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(ValidationError, match="not found"):
            expand_header_inputs([tmp_path / "missing.h"])

    def test_empty_directory_raises(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(ValidationError, match="no supported header"):
            expand_header_inputs([d])

    def test_deduplication(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("")
        result = expand_header_inputs([h, h])
        assert len(result) == 1

    def test_directory_with_subdirs(self, tmp_path):
        d = tmp_path / "include"
        d.mkdir()
        sub = d / "sub"
        sub.mkdir()
        (sub / "deep.h").write_text("")
        result = expand_header_inputs([d])
        assert len(result) == 1
        assert result[0].name == "deep.h"

    @pytest.mark.parametrize("noise_dir", [".abicheck-build", ".git"])
    def test_prunes_abicheck_build_and_vcs_dirs(self, tmp_path, noise_dir):
        # Generated headers under abicheck's own cmake build dir (and VCS dirs)
        # must never inflate the L2 header surface (CodeRabbit).
        d = tmp_path / "include"
        d.mkdir()
        (d / "public.h").write_text("int api(void);\n")
        sub = d / noise_dir
        sub.mkdir()
        (sub / "config.h").write_text("#define GENERATED 1\n")
        result = expand_header_inputs([d])
        names = {p.name for p in result}
        assert names == {"public.h"}  # generated config.h pruned

    def test_various_extensions(self, tmp_path):
        d = tmp_path / "hdrs"
        d.mkdir()
        for ext in (".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc"):
            (d / f"test{ext}").write_text("")
        result = expand_header_inputs([d])
        assert len(result) == 8


# ── resolve_input() ─────────────────────────────────────────────────────────


class TestResolveInput:
    def test_is_elf_true_calls_run_dump(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.run_dump", return_value=snap) as mock:
            result = resolve_input(p, is_elf=True)
        assert result is snap
        mock.assert_called_once()

    def test_binary_detection_elf(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.run_dump", return_value=snap):
            result = resolve_input(p)
        assert result is snap

    def test_elf_forwards_provenance_to_dumper(self, tmp_path):
        # P1 regression: the ELF service path (used by `scan`) must thread
        # public_headers / public_header_dirs into dumper.dump, which runs
        # apply_provenance. Without this the ELF origins stay UNKNOWN and the
        # provenance-gated cross-checks silently skip — even with
        # --public-header-dir given. The `dump` CLI always forwarded them; this
        # path did not.
        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 100)
        hdr = tmp_path / "pub.h"
        hdr.write_text("int f();")
        pubdir = tmp_path / "include"
        pubdir.mkdir()
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            resolve_input(
                so,
                headers=[hdr],
                includes=[],
                is_elf=True,
                public_headers=[hdr],
                public_header_dirs=[pubdir],
            )
        kwargs = mock.call_args.kwargs
        assert kwargs["public_headers"] == [hdr]
        assert kwargs["public_header_dirs"] == [pubdir]

    def test_symvers_by_filename(self, tmp_path):
        p = tmp_path / "Module.symvers"
        p.write_text("0x1\tkmalloc\tvmlinux\tEXPORT_SYMBOL_GPL\tCORE\n")
        result = resolve_input(p, is_elf=False)
        assert result.kabi is not None
        assert result.kabi.entries["kmalloc"].namespace == "CORE"

    def test_symvers_by_content_generic_name(self, tmp_path):
        # A generically-named file still resolves as kABI via content sniffing.
        p = tmp_path / "syms.txt"
        p.write_text("0x2\tkfree\tvmlinux\tEXPORT_SYMBOL\t\n")
        result = resolve_input(p, is_elf=False)
        assert result.kabi is not None
        assert "kfree" in result.kabi.entries

    def test_symvers_empty_falls_through(self, tmp_path):
        # A .symvers file with no valid records is not treated as kABI.
        from abicheck.service import _resolve_symvers

        p = tmp_path / "empty.symvers"
        p.write_text("# only a comment\n")
        assert _resolve_symvers(p, "1.0") is None

    def test_symvers_unreadable_returns_none(self, tmp_path):
        from abicheck.service import _resolve_symvers

        # A directory named like a manifest cannot be read as text → None.
        d = tmp_path / "Module.symvers"
        d.mkdir()
        assert _resolve_symvers(d, "1.0") is None

    def test_json_text_format(self, tmp_path):
        p = tmp_path / "snap.json"
        snap = AbiSnapshot(library="test", version="1.0")
        p.write_text('{"library": "test"}')
        with patch("abicheck.service.load_snapshot", return_value=snap):
            result = resolve_input(p, is_elf=False)
        assert result is snap

    def test_json_load_error_wraps_in_snapshot_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        with patch("abicheck.service.load_snapshot", side_effect=ValueError("bad")):
            with pytest.raises(SnapshotError, match="Failed to load JSON"):
                resolve_input(p, is_elf=False)

    def test_perl_format(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.detect_binary_format", return_value=None):
            with patch("abicheck.service.sniff_text_format", return_value="perl"):
                with patch(
                    "abicheck.compat.abicc_dump_import.import_abicc_perl_dump",
                    return_value=snap,
                ):
                    result = resolve_input(p, is_elf=False)
        assert result is snap

    def test_perl_import_error(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        with patch("abicheck.service.detect_binary_format", return_value=None):
            with patch("abicheck.service.sniff_text_format", return_value="perl"):
                with patch(
                    "abicheck.compat.abicc_dump_import.import_abicc_perl_dump",
                    side_effect=ValueError("parse fail"),
                ):
                    with pytest.raises(SnapshotError, match="ABICC Perl"):
                        resolve_input(p, is_elf=False)

    def test_unknown_format_raises(self, tmp_path):
        p = tmp_path / "mystery"
        p.write_text("???")
        with patch("abicheck.service.detect_binary_format", return_value=None):
            with patch("abicheck.service.sniff_text_format", return_value="unknown"):
                with pytest.raises(ValidationError, match="Cannot detect format"):
                    resolve_input(p, is_elf=False)

    def test_static_archive_raises_with_guidance(self, tmp_path):
        """A `.a`/`.lib` ar archive fails deliberately with actionable guidance
        (G8 — static libraries are a by-design non-goal), not a generic
        'Cannot detect format' error."""
        p = tmp_path / "libfoo.a"
        # Minimal ar archive: magic + an (empty) member header is not required —
        # the magic alone is what resolve_input branches on.
        p.write_bytes(b"!<arch>\n" + b"\x00" * 16)
        with pytest.raises(ValidationError, match="static/import library archive"):
            resolve_input(p)


# ── run_dump() ──────────────────────────────────────────────────────────────


class TestRunDump:
    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "lib.xyz"
        p.write_bytes(b"\x00" * 100)
        with pytest.raises(ValidationError, match="Unsupported binary format"):
            run_dump(p, "webasm")

    def test_elf_format_delegates(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service._dump_elf", return_value=snap):
            result = run_dump(p, "elf")
        assert result is snap

    def test_pe_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service._dump_pe", return_value=snap):
            result = run_dump(p, "pe")
        assert result is snap

    def test_macho_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service._dump_macho", return_value=snap):
            result = run_dump(p, "macho")
        assert result is snap


# ── _implicit_header_includes() (P3: -H umbrella resolves without -I) ────────


class TestImplicitHeaderIncludes:
    def test_directory_input_is_its_own_root(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        assert _implicit_header_includes([inc]) == [inc]

    def test_file_at_root_adds_parent(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        umb = inc / "dnnl.hpp"
        umb.write_text("// umbrella")
        assert _implicit_header_includes([umb]) == [inc]

    def test_nested_umbrella_adds_include_root_ancestor(self, tmp_path):
        # include/oneapi/tbb.h → both its parent (include/oneapi) and the
        # conventional include root (include/) must be on the search path so
        # `#include "oneapi/tbb/..."` resolves.
        from abicheck.header_utils import _implicit_header_includes

        root = tmp_path / "include"
        nested = root / "oneapi"
        nested.mkdir(parents=True)
        umb = nested / "tbb.h"
        umb.write_text("// umbrella")
        dirs = _implicit_header_includes([umb])
        assert nested in dirs
        assert root in dirs

    def test_namespace_directory_adds_include_root_ancestor(self, tmp_path):
        # A -H *directory* nested under a conventional root, e.g.
        # `-H include/oneapi`, must add BOTH itself and the include root —
        # headers inside still `#include "oneapi/..."` relative to include/.
        from abicheck.header_utils import _implicit_header_includes

        root = tmp_path / "include"
        nested = root / "oneapi"
        nested.mkdir(parents=True)
        dirs = _implicit_header_includes([nested])
        assert nested in dirs
        assert root in dirs

    def test_deduplicates(self, tmp_path):
        from abicheck.header_utils import _implicit_header_includes

        inc = tmp_path / "include"
        inc.mkdir()
        (inc / "a.h").write_text("")
        (inc / "b.h").write_text("")
        # Two files in the same dir → the root appears once.
        assert _implicit_header_includes([inc / "a.h", inc / "b.h"]) == [inc]

    def test_skips_nonexistent_parent(self, tmp_path):
        # A -H file whose parent dir does not exist contributes nothing.
        from abicheck.header_utils import _implicit_header_includes

        ghost = tmp_path / "absent" / "x.h"
        assert _implicit_header_includes([ghost]) == []


class TestResolveInferredHeaderRoots:
    def _umbrella(self, tmp_path):
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        return root, umb

    def test_no_build_context_uses_plain_I(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [])
        assert root in inc and toks == []

    def test_isystem_context_defers_via_isystem(self, tmp_path):
        # A build-context -isystem makes the inferred root defer — emitted as
        # -isystem (below build context, above standard system dirs), not -I.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("-isystem", "/gen")
        )
        assert inc == []
        # every inferred root is emitted as -isystem (not -I, not -idirafter)
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"
        assert "-idirafter" not in toks and "-I" not in toks

    def test_gcc_options_include_string_detected(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_options="-I /build/include -O2"
        )
        assert inc == [] and str(root) in toks

    @pytest.mark.parametrize(
        ("tok", "want"),
        [
            ("/Ibuild\\generated", "/I"),
            ("/external:Igen", "/external:I"),
            ("/imsvc", "/imsvc"),
        ],
    )
    def test_msvc_slash_I_context_detected(self, tmp_path, tok, want):
        # An MSVC/clang-cl build context (/I, /external:I, /imsvc) must also count
        # as build context so the inferred root defers instead of shadowing it,
        # and in the MSVC dialect (never GNU -isystem, which cl.exe/clang-cl
        # would ignore). The deferred bucket mirrors the context's own lowest
        # bucket so the root can't shadow /external:I//imsvc system dirs (#454):
        # a plain /I context stays /I; a system-bucket context echoes it.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_option_tokens=(tok,))
        assert inc == []  # detected as build context → deferred
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == want
        assert "-isystem" not in toks

    def test_msvc_system_bucket_root_does_not_shadow(self, tmp_path):
        # #454 item 3: when the MSVC context uses a system bucket, the deferred
        # root must echo that bucket (not collapse to /I, which clang-cl lowers
        # to -I and searches *above* the /external:I//imsvc system dirs). With
        # both a plain /I and a system bucket present, the system bucket wins so
        # the root sits below every build-context include dir.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, ext = resolve_inferred_header_roots(
            [umb], [], gcc_options="/I build\\gen /external:I third_party"
        )
        assert ext[ext.index(str(root)) - 1] == "/external:I"
        _, imsvc = resolve_inferred_header_roots(
            [umb], [], gcc_options="/I build\\gen /imsvc clang_sys"
        )
        assert imsvc[imsvc.index(str(root)) - 1] == "/imsvc"
        # When both appear, the *lowest-searched* bucket wins: clang-cl searches
        # /imsvc (%INCLUDE%-style) dirs after /external:I, so deferring into
        # /imsvc keeps the root below the build's /imsvc dirs too (Codex review).
        # A context using /imsvc is necessarily clang-cl, so /imsvc is supported.
        _, both = resolve_inferred_header_roots(
            [umb], [], gcc_options="/imsvc a /external:I b"
        )
        assert both[both.index(str(root)) - 1] == "/imsvc"

    def test_msvc_bucket_not_fooled_by_include_operand(self, tmp_path):
        # A spaced /I operand that merely *starts with* a bucket name (a dir
        # literally called /imsvc-sdk) must NOT be read as an /imsvc flag — the
        # only real flag here is /I, so the deferred root stays /I (CodeRabbit
        # review). Picking /imsvc would emit a flag cl.exe rejects.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("/I", "/imsvc-sdk")
        )
        assert toks[toks.index(str(root)) - 1] == "/I"

        # The mirror case: a *GNU* -I context whose operand dir starts with a
        # slash spelling must not be misclassified as MSVC (dialect detection
        # filters operands too) — it stays the GNU -isystem bucket.
        _, gnu_toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=("-I", "/imsvc-sdk")
        )
        assert gnu_toks[gnu_toks.index(str(root)) - 1] == "-isystem"

    def test_deferred_flag_dialect_matches_build_context(self, tmp_path):
        # The deferred flag matches the build context's lowest include bucket:
        # above-system GNU (-I/-isystem) → -isystem; MSVC /I → /I; an
        # -idirafter-only context → -idirafter (so its below-system fallback
        # keeps priority instead of being shadowed by an -isystem root).
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, gnu = resolve_inferred_header_roots([umb], [], gcc_options="-I /build/gen")
        assert gnu[gnu.index(str(root)) - 1] == "-isystem"
        _, msvc = resolve_inferred_header_roots([umb], [], gcc_options="/I build\\gen")
        assert msvc[msvc.index(str(root)) - 1] == "/I"
        _, after = resolve_inferred_header_roots(
            [umb], [], gcc_options="-idirafter /build/gen"
        )
        assert after[after.index(str(root)) - 1] == "-idirafter"
        assert "-isystem" not in after

    def test_mixed_above_system_and_idirafter_defaults_to_isystem(self, tmp_path):
        # #454 item 2: a mixed GNU context (-I + -idirafter) is unsatisfiable
        # with a single flag — the root can't be both above the system dirs
        # (to win the -I-context basename collision) and below -idirafter.
        # -isystem is the documented default (favors the common collision
        # case; compile DBs essentially never emit -idirafter). This locks
        # that choice in so a refactor can't silently flip it to -idirafter.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        _, mixed = resolve_inferred_header_roots(
            [umb], [], gcc_options="-I /build/primary -idirafter /build/generated"
        )
        assert mixed[mixed.index(str(root)) - 1] == "-isystem"
        assert "-idirafter" not in mixed

    def test_root_already_in_build_context_is_skipped(self, tmp_path):
        # A root the build context already supplies as -I must NOT be re-added as
        # -isystem (GCC would then ignore the build's -I). Here the build provides
        # the include root; only the *other* inferred ancestor (oneapi) defers.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)  # include/, umbrella at include/oneapi
        nested = root / "oneapi"
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_options=f"-I {root}")
        assert inc == []
        # the include root is in the build context → not re-emitted at all
        assert str(root) not in toks
        # the nested ancestor is not in the build context → deferred via -isystem
        assert str(nested) in toks
        assert toks[toks.index(str(nested)) - 1] == "-isystem"

    def test_build_context_dir_attached_form_deduped(self, tmp_path):
        # The attached spelling (-I<dir>) is parsed too, so the root is skipped.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_option_tokens=(f"-I{root}",)
        )
        assert str(root) not in toks

    def test_deferred_token_dirs_extracts_isystem_paths(self):
        from pathlib import Path

        from abicheck.header_utils import deferred_token_dirs

        toks = ["-isystem", "/a/include", "-isystem", "/b/oneapi"]
        assert deferred_token_dirs(toks) == [Path("/a/include"), Path("/b/oneapi")]
        assert deferred_token_dirs([]) == []

    def test_dangling_include_flag_no_operand(self, tmp_path):
        # A bare -I with no following dir (build context present but supplies no
        # parsable dir) still defers the inferred roots via -isystem, no crash.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_option_tokens=("-I",))
        assert inc == []
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"

    def test_non_include_options_are_not_build_context(self, tmp_path):
        # -O2/-DNDEBUG add no include dir, so the inferred root stays a plain -I.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots(
            [umb], [], gcc_options="-O2 -DNDEBUG", gcc_option_tokens=("-Wall",)
        )
        assert root in inc and toks == []

    def test_user_include_deduped(self, tmp_path):
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        nested = root / "oneapi"
        inc, toks = resolve_inferred_header_roots([umb], [nested])
        # nested came from the user -I; only the include root is inferred-added.
        assert nested not in inc and root in inc

    def test_no_inferred_roots_returns_empty(self, tmp_path):
        # A -H file with a nonexistent parent yields no inferred roots → no flags
        # of either kind (and no spurious -isystem even with a build context).
        from abicheck.header_utils import resolve_inferred_header_roots

        ghost = tmp_path / "absent" / "x.h"
        assert resolve_inferred_header_roots(
            [ghost], [], gcc_option_tokens=("-isystem", "/x")
        ) == ([], [])

    def test_malformed_gcc_options_falls_back_to_plain_split(self, tmp_path):
        # An unbalanced quote makes shlex.split raise; we fall back to str.split
        # so an -I in a malformed --gcc-options string is still detected.
        from abicheck.header_utils import resolve_inferred_header_roots

        root, umb = self._umbrella(tmp_path)
        inc, toks = resolve_inferred_header_roots([umb], [], gcc_options='-I "/broken')
        assert inc == [] and str(root) in toks


# ── _dump_elf() ─────────────────────────────────────────────────────────────


class TestDumpElf:
    def test_implicit_header_root_passed_to_dumper(self, tmp_path):
        # P3 regression: a -H umbrella nested under include/ must reach the
        # frontend with the include root on extra_includes, with no explicit -I.
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        snap = AbiSnapshot(library="t", version="1.0")
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            _dump_elf(p, [umb], [], "1.0", "c++")
        passed = mock.call_args.kwargs["extra_includes"]
        assert root in passed  # the include root was auto-added (plain -I)

    def test_implicit_root_defers_to_isystem_build_context(self, tmp_path):
        # Codex: when the caller's CompileContext supplies includes via -isystem,
        # the inferred -H root must defer — emitted as its own -isystem token
        # *after* the build's (build's is emitted first, so it wins), not jumping
        # ahead as -I. -isystem also keeps it above the standard system dirs.
        from abicheck.service import _dump_elf
        from abicheck.service_scan import CompileContext

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("// umbrella")
        gen = str(tmp_path / "gen")
        snap = AbiSnapshot(library="t", version="1.0")
        cc = CompileContext(gcc_option_tokens=("-isystem", gen))
        with patch("abicheck.dumper.dump", return_value=snap) as mock:
            _dump_elf(p, [umb], [], "1.0", "c++", compile=cc)
        kwargs = mock.call_args.kwargs
        assert root not in kwargs["extra_includes"]  # not promoted to -I
        toks = list(kwargs["gcc_option_tokens"])
        assert str(root) in toks
        assert toks[toks.index(str(root)) - 1] == "-isystem"
        # the build's -isystem dir stays ahead of the inferred root (wins)
        assert toks.index(gen) < toks.index(str(root))

    def test_no_headers_warning(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = _dump_elf(p, [], [], "1.0", "c++")
        assert result is snap

    def test_invalid_include_dir_raises(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        h = tmp_path / "foo.h"
        h.write_text("")
        bad_inc = tmp_path / "nonexistent"
        with patch("abicheck.service.expand_header_inputs", return_value=[h]):
            with pytest.raises(ValidationError, match="Include directory"):
                _dump_elf(p, [h], [bad_inc], "1.0", "c++")

    def test_dump_error_wraps(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x00" * 10)
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", side_effect=RuntimeError("bad elf")):
                with pytest.raises(SnapshotError, match="Failed to dump"):
                    _dump_elf(p, [], [], "1.0", "c++")

    def test_includes_without_headers_warns(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        inc = tmp_path / "inc"
        inc.mkdir()
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = _dump_elf(p, [], [inc], "1.0", "c++")
        assert result is snap

    def test_lang_c_sets_compiler(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap) as mock_dump:
                _dump_elf(p, [], [], "1.0", "c")
        call_kwargs = mock_dump.call_args
        assert (
            call_kwargs.kwargs.get("compiler") == "cc"
            or call_kwargs[1].get("compiler") == "cc"
        )


# ── _dump_pe() ──────────────────────────────────────────────────────────────


class TestHeaderScopedInferredRoots:
    """P3 parity: the PE/Mach-O header-scoped path also adds inferred -H roots."""

    def _umbrella(self, tmp_path):
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("int f(void);\n", encoding="utf-8")
        return root, umb

    def test_pe_no_build_context_adds_root_as_I(self, tmp_path):
        from abicheck.service import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)
        captured = {}

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            captured["extra_includes"] = extra_includes
            captured.update(k)
            return AbiSnapshot(library="x", version="1.0")

        with patch("abicheck.dumper._dump_pe", fake_pe):
            _try_header_scoped_dump("pe", tmp_path / "x.dll", [umb], [], "1.0", "c++")
        # no build context → inferred include root rides in extra_includes
        assert root in captured["extra_includes"]

    def test_no_headers_skips_inferred_derivation(self, tmp_path):
        # With no -H headers the derivation is skipped: the original includes pass
        # through unchanged and nothing is deferred/hashed.
        from abicheck.service import _try_header_scoped_dump

        captured = {}

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            captured["extra_includes"] = extra_includes
            captured.update(k)
            return AbiSnapshot(library="x", version="1.0")

        inc = [tmp_path / "inc"]
        with patch("abicheck.dumper._dump_pe", fake_pe):
            _try_header_scoped_dump("pe", tmp_path / "x.dll", [], inc, "1.0", "c++")
        assert captured["extra_includes"] == inc  # unchanged, no inferred roots
        assert captured["extra_hash_dirs"] == ()

    def test_macho_build_context_defers_and_hashes(self, tmp_path):
        from abicheck.service import _try_header_scoped_dump
        from abicheck.service_scan import CompileContext

        root, umb = self._umbrella(tmp_path)
        captured = {}

        def fake_macho(path, headers, extra_includes, version, compiler, **k):
            captured["extra_includes"] = extra_includes
            captured.update(k)
            return AbiSnapshot(library="x", version="1.0")

        cc = CompileContext(gcc_option_tokens=("-isystem", str(tmp_path / "gen")))
        with patch("abicheck.dumper._dump_macho", fake_macho):
            _try_header_scoped_dump(
                "macho", tmp_path / "x.dylib", [umb], [], "1.0", "c++", compile=cc
            )
        # build context → root defers to -isystem (gcc_option_tokens), not -I,
        # and its dir is hashed into the cache key (extra_hash_dirs)
        assert root not in captured["extra_includes"]
        toks = list(captured["gcc_option_tokens"])
        assert str(root) in toks and toks[toks.index(str(root)) - 1] == "-isystem"
        assert root in captured["extra_hash_dirs"]


class TestDumpPe:
    def test_no_machine_raises(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = None
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(SnapshotError, match="Failed to extract PE metadata"):
                _dump_pe(p, "1.0")

    def test_no_exports_raises(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(ValidationError, match="no exports"):
                _dump_pe(p, "1.0")

    def test_successful_pe_dump(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "MyFunc"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.platform == "pe"
        assert len(result.functions) == 1
        assert result.functions[0].name == "MyFunc"

    def test_pe_import_error(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch(
            "abicheck.pe_metadata.parse_pe_metadata",
            side_effect=ImportError("no pefile"),
        ):
            with pytest.raises(SnapshotError, match="no pefile"):
                _dump_pe(p, "1.0")

    def test_pe_runtime_error(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch(
            "abicheck.pe_metadata.parse_pe_metadata",
            side_effect=RuntimeError("corrupt"),
        ):
            with pytest.raises(SnapshotError, match="Failed to parse PE"):
                _dump_pe(p, "1.0")

    def test_ordinal_export(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = None
        export.ordinal = 42
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.functions[0].name == "ordinal:42"

    def test_pdb_found_and_parsed(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "Func"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        mock_dwarf = MagicMock()
        mock_adv = MagicMock()
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=Path("/fake.pdb")):
                with patch(
                    "abicheck.pdb_metadata.parse_pdb_debug_info",
                    return_value=(mock_dwarf, mock_adv),
                ):
                    result = _dump_pe(p, "1.0")
        assert result.dwarf is mock_dwarf
        assert result.dwarf_advanced is mock_adv

    def test_pdb_parsing_exception_handled(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "Func"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch(
                "abicheck.pdb_utils.locate_pdb", side_effect=RuntimeError("pdb error")
            ):
                result = _dump_pe(p, "1.0")
        assert result.dwarf is None

    def test_cpp_name_not_extern_c(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "?MyFunc@@YAXXZ"  # MSVC mangled
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.functions[0].is_extern_c is False


# ── _dump_macho() ───────────────────────────────────────────────────────────


class TestDumpMacho:
    def test_successful_macho_dump(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_myFunc"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            result = _dump_macho(p, "1.0")
        assert result.platform == "macho"
        assert len(result.functions) == 1

    def test_no_exports_no_metadata_raises(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        macho_meta = MagicMock()
        macho_meta.exports = []
        macho_meta.install_name = None
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            with pytest.raises(SnapshotError, match="no exports"):
                _dump_macho(p, "1.0")

    def test_parse_error(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata",
            side_effect=RuntimeError("bad macho"),
        ):
            with pytest.raises(SnapshotError, match="Failed to parse Mach-O"):
                _dump_macho(p, "1.0")

    def test_export_without_name_skipped(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        exp_named = MagicMock()
        exp_named.name = "_func"
        exp_empty = MagicMock()
        exp_empty.name = ""
        macho_meta = MagicMock()
        macho_meta.exports = [exp_named, exp_empty]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            result = _dump_macho(p, "1.0")
        assert len(result.functions) == 1

    def test_cpp_symbol_not_extern_c(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        export = MagicMock()
        export.name = "_ZN3foo3barEv"  # C++ mangled
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
        ):
            result = _dump_macho(p, "1.0")
        assert result.functions[0].is_extern_c is False


# ── collect_metadata() ──────────────────────────────────────────────────────


class TestCollectMetadata:
    def test_binary_file(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        with patch("abicheck.service.sniff_text_format", return_value="unknown"):
            meta = collect_metadata(p)
        assert meta is not None
        assert meta.path == str(p)
        assert len(meta.sha256) == 64
        assert meta.size_bytes == 104

    def test_json_snapshot_returns_none(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text('{"library": "test"}')
        meta = collect_metadata(p)
        assert meta is None

    def test_perl_dump_returns_none(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        meta = collect_metadata(p)
        assert meta is None


# ── load_suppression_and_policy() ───────────────────────────────────────────


class TestLoadSuppressionAndPolicy:
    def test_no_suppress_no_policy(self):
        s, p = load_suppression_and_policy(None)
        assert s is None
        assert p is None

    def test_invalid_suppression_file(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("not: [valid: suppression")
        with pytest.raises(ValidationError, match="Invalid suppression"):
            load_suppression_and_policy(f)

    def test_valid_suppression_file(self, tmp_path):
        f = tmp_path / "suppress.yaml"
        f.write_text(
            "version: 1\nsuppressions:\n  - symbol: 'foo'\n    change_kind: func_removed\n"
        )
        s, p = load_suppression_and_policy(f)
        assert s is not None
        assert p is None

    def test_policy_file_with_non_default_policy_warns(self, tmp_path, caplog):
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("overrides: {}\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            _, p = load_suppression_and_policy(
                None, policy="permissive", policy_file_path=pf
            )
        assert p is not None
        assert "ignored" in caplog.text.lower()

    def test_invalid_policy_file(self, tmp_path):
        pf = tmp_path / "bad_policy.yaml"
        pf.write_text("- this is a list not a mapping\n")
        with pytest.raises(ValidationError):
            load_suppression_and_policy(None, policy_file_path=pf)


# ── run_compare() ───────────────────────────────────────────────────────────


class TestRunCompare:
    def _make_snap_file(self, tmp_path, name, version="1.0"):
        """Create a minimal JSON snapshot file."""
        snap = AbiSnapshot(
            library=name,
            version=version,
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    is_extern_c=True,
                ),
            ],
        )
        from abicheck.serialization import save_snapshot

        p = tmp_path / f"{name}_{version}.json"
        save_snapshot(snap, p)
        return p

    def test_compare_two_snapshots(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        result, old, new = run_compare(old_p, new_p)
        assert isinstance(result, DiffResult)
        assert isinstance(old, AbiSnapshot)
        assert isinstance(new, AbiSnapshot)

    def test_compare_with_suppression(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        sf = tmp_path / "suppress.yaml"
        sf.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    change_kind: func_removed\n"
        )
        result, _, _ = run_compare(old_p, new_p, suppress=sf)
        assert isinstance(result, DiffResult)

    def test_headers_passed_as_public_headers(self, tmp_path, monkeypatch):
        """run_compare_request (the CompareRequest chokepoint used by the
        compare-release/directory-package fan-out) must thread each side's
        headers through as its public-header set for provenance tagging —
        same rule as the single-pair CLI's compare --header fix. Regression:
        this was silently dropped, unlike the single-pair path."""
        from abicheck import service as service_mod

        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        old_h = tmp_path / "old.h"
        new_h = tmp_path / "new.h"

        calls: list[dict] = []
        original_resolve = service_mod.resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            calls.append({"path": path, "version": version, **kwargs})
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(service_mod, "resolve_input", _spy)

        run_compare(
            old_p,
            new_p,
            old_headers=[old_h],
            new_headers=[new_h],
        )
        assert len(calls) == 2
        old_call, new_call = calls
        assert old_call["public_headers"] == [old_h]
        assert new_call["public_headers"] == [new_h]


# ── render_output() ─────────────────────────────────────────────────────────


class TestRenderOutput:
    @pytest.fixture
    def snap(self):
        return AbiSnapshot(
            library="libtest",
            version="1.0",
            functions=[Function(name="foo", mangled="foo", return_type="int")],
        )

    @pytest.fixture
    def diff_result(self):
        return DiffResult(old_version="1.0", new_version="2.0", library="libtest")

    def test_json_format(self, diff_result, snap):
        out = render_output("json", diff_result, snap)
        d = json.loads(out)
        assert "library" in d or "verdict" in d or isinstance(d, dict)

    def test_markdown_format(self, diff_result, snap):
        out = render_output("markdown", diff_result, snap)
        assert isinstance(out, str)

    def test_md_format(self, diff_result, snap):
        out = render_output("md", diff_result, snap)
        assert isinstance(out, str)

    def test_sarif_format(self, diff_result, snap):
        out = render_output("sarif", diff_result, snap)
        d = json.loads(out)
        assert "$schema" in d or "runs" in d

    def test_html_format(self, diff_result, snap):
        out = render_output("html", diff_result, snap)
        assert (
            "<html" in out.lower()
            or "<!doctype" in out.lower()
            or "<div" in out.lower()
        )

    def test_unsupported_format_raises(self, diff_result, snap):
        with pytest.raises(ValidationError, match="Unsupported output format"):
            render_output("xml", diff_result, snap)

    def test_stat_json(self, diff_result, snap):
        out = render_output("json", diff_result, snap, stat=True)
        d = json.loads(out)
        assert isinstance(d, dict)

    def test_stat_text(self, diff_result, snap):
        out = render_output("markdown", diff_result, snap, stat=True)
        assert isinstance(out, str)

    def test_json_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("json", diff_result, snap, follow_deps=True)
        d = json.loads(out)
        assert "old_dependency_info" in d

    def test_markdown_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("markdown", diff_result, snap, follow_deps=True)
        assert "Dependency" in out

    def test_html_with_new_snap(self, snap):
        new_snap = AbiSnapshot(library="libtest", version="2.0")
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("html", diff_result, snap, new=new_snap)
        assert isinstance(out, str)


# ── _render_deps_section_md() ──────────────────────────────────────────────


class TestRenderDepsSection:
    def test_basic_deps(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0, "resolution_reason": "system"}],
            bindings_summary={"GLOBAL": 5},
            unresolved=[{"soname": "libmissing.so", "consumer": "lib.so"}],
            missing_symbols=[
                {"symbol": "foo", "version": "GLIBC_2.17"},
                {"symbol": "bar"},
            ],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result
        assert "GLOBAL" in result
        assert "libmissing.so" in result
        assert "foo" in result
        assert "bar" in result

    def test_no_dep_info(self):
        old = AbiSnapshot(library="lib", version="1.0")
        result = _render_deps_section_md(old, None)
        assert "Dependency" in result
        # Should still have the header

    def test_missing_symbols_truncated(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            missing_symbols=[{"symbol": f"sym{i}"} for i in range(15)],
        )
        result = _render_deps_section_md(old, None)
        assert "+5 more" in result

    def test_non_int_depth(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": "invalid"}],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result

    def test_both_old_and_new(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(nodes=[{"soname": "old.so", "depth": 0}])
        new = AbiSnapshot(library="lib", version="2.0")
        new.dependency_info = DependencyInfo(nodes=[{"soname": "new.so", "depth": 0}])
        result = _render_deps_section_md(old, new)
        assert "old.so" in result
        assert "new.so" in result


# ── Header-scoped PE/Mach-O dumps (issue #235) ───────────────────────────────


def _scoped_snapshot(platform: str, *funcs: tuple[str, Visibility]) -> AbiSnapshot:
    """Build a fake header-scoped snapshot as ``dumper._dump_*`` would return."""
    from abicheck.model import RecordType

    snap = AbiSnapshot(library="lib", version="1.0", platform=platform)
    snap.functions = [
        Function(name=n, mangled=n, return_type="int", visibility=v) for n, v in funcs
    ]
    # A header-scoped dump carries real type info (so layout diffs still fire).
    snap.types = [RecordType(name="PublicStruct", kind="struct")]
    return snap


def _pe_meta(*export_names: str) -> MagicMock:
    meta = MagicMock()
    meta.machine = "AMD64"
    exports = []
    for i, name in enumerate(export_names, start=1):
        exp = MagicMock()
        exp.name = name
        exp.ordinal = i
        exports.append(exp)
    meta.exports = exports
    return meta


def _mk_header(tmp_path: Path, name: str = "api.h") -> Path:
    """Create a real public header file so expand_header_inputs accepts it."""
    h = tmp_path / name
    h.write_text("int PublicApiFunc(void);\n")
    return h


class TestPeHeaderScoping:
    """Issue #235: --header/--include must scope the PE ABI surface."""

    def test_headers_route_to_castxml_scoped_dump(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        # Export table has a public API symbol AND a private/internal export.
        pe_meta = _pe_meta("PublicApiFunc", "InternalPrivateFunc")
        # Header-scoped dump only sees the symbol declared in the public header.
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe", return_value=scoped) as mock_dump,
        ):
            result = _dump_pe(
                p, "1.0", headers=[_mk_header(tmp_path)], includes=[Path("inc")]
            )

        # The header-aware dumper was actually invoked with the (expanded) headers.
        assert mock_dump.called
        called_headers = mock_dump.call_args.args[1]
        assert called_headers == [tmp_path / "api.h"]
        # Surface is scoped: private export absent, public symbol present.
        names = [f.name for f in result.functions]
        assert "PublicApiFunc" in names
        assert "InternalPrivateFunc" not in names
        # Type info preserved so reachable layout changes still diff.
        assert any(t.name == "PublicStruct" for t in result.types)

    def test_private_export_absent_from_headers_not_compared(self, tmp_path):
        """An exported-but-private symbol removed in 'new' must not surface."""
        from abicheck.checker import compare
        from abicheck.service import _dump_pe

        old_p = tmp_path / "old.dll"
        new_p = tmp_path / "new.dll"
        old_p.write_bytes(b"MZ" + b"\x00" * 100)
        new_p.write_bytes(b"MZ" + b"\x00" * 100)

        old_pe = _pe_meta("PublicApiFunc", "InternalPrivateFunc")
        new_pe = _pe_meta("PublicApiFunc")  # private export dropped in new
        old_scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))
        new_scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
            with (
                patch("abicheck.pe_metadata.parse_pe_metadata", return_value=old_pe),
                patch("abicheck.dumper._dump_pe", return_value=old_scoped),
            ):
                old_snap = _dump_pe(old_p, "1.0", headers=[_mk_header(tmp_path)])
            with (
                patch("abicheck.pe_metadata.parse_pe_metadata", return_value=new_pe),
                patch("abicheck.dumper._dump_pe", return_value=new_scoped),
            ):
                new_snap = _dump_pe(new_p, "2.0", headers=[_mk_header(tmp_path)])

        result = compare(old_snap, new_snap)
        removed = [
            c for c in result.changes if "InternalPrivateFunc" in (c.symbol or "")
        ]
        assert removed == [], f"private export must not be reported: {removed}"

    def test_fallback_when_no_header_match(self, tmp_path):
        """MSVC-mangled C++ exports won't match Itanium names → warn + fallback."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("?realFunc@@YAHXZ")
        # castxml parsed headers but nothing matched the export table.
        scoped = _scoped_snapshot("pe", ("someDecl", Visibility.HIDDEN))

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe", return_value=scoped),
        ):
            with pytest.warns(
                UserWarning, match="None of the provided headers matched"
            ):
                result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        # Fell back to the full export table.
        names = [f.name for f in result.functions]
        assert "?realFunc@@YAHXZ" in names

    def test_fallback_when_castxml_unavailable(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch(
                "abicheck.dumper._dump_pe",
                side_effect=RuntimeError("castxml not found"),
            ),
        ):
            with pytest.warns(
                UserWarning, match="Header-based ABI scoping unavailable"
            ):
                result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        names = [f.name for f in result.functions]
        assert "PublicApiFunc" in names

    def test_no_headers_uses_export_table(self, tmp_path):
        """Without headers, behaviour is unchanged: full export table, PUBLIC."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc", "InternalPrivateFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe") as mock_dump,
        ):
            result = _dump_pe(p, "1.0")

        assert not mock_dump.called  # castxml path never taken
        names = {f.name for f in result.functions}
        assert names == {"PublicApiFunc", "InternalPrivateFunc"}
        assert all(f.visibility == Visibility.PUBLIC for f in result.functions)

    def test_pdb_debug_preserved_on_scoped_snapshot(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))
        dwarf_meta = MagicMock()
        dwarf_adv = MagicMock()

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.dumper._dump_pe", return_value=scoped),
            patch(
                "abicheck.service._extract_pdb_debug",
                return_value=(dwarf_meta, dwarf_adv),
            ),
        ):
            result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        assert result.dwarf is dwarf_meta
        assert result.dwarf_advanced is dwarf_adv

    def test_header_directory_is_expanded(self, tmp_path):
        """`--header <dir>` must expand to files, not feed a dir to castxml."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        hdr_dir = tmp_path / "include"
        hdr_dir.mkdir()
        (hdr_dir / "a.h").write_text("int PublicApiFunc(void);\n")
        (hdr_dir / "b.hpp").write_text("int Other(void);\n")
        pe_meta = _pe_meta("PublicApiFunc")
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe", return_value=scoped) as mock_dump,
        ):
            _dump_pe(p, "1.0", headers=[hdr_dir])

        # The dumper received the individual header files, not the directory.
        called_headers = mock_dump.call_args.args[1]
        assert hdr_dir not in called_headers
        assert {h.name for h in called_headers} == {"a.h", "b.hpp"}

    def test_bad_header_path_raises_not_silent_fallback(self, tmp_path):
        """A nonexistent header must raise, not silently fall back to exports."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
        ):
            with pytest.raises(ValidationError, match="not found"):
                _dump_pe(p, "1.0", headers=[tmp_path / "missing.h"])


class TestMachoHeaderScoping:
    def test_headers_route_to_castxml_scoped_dump(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_publicFn"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        scoped = _scoped_snapshot("macho", ("publicFn", Visibility.PUBLIC))

        with (
            patch(
                "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
            ),
            patch("abicheck.dumper._dump_macho", return_value=scoped) as mock_dump,
        ):
            result = _dump_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert mock_dump.called
        assert [f.name for f in result.functions] == ["publicFn"]

    def test_fallback_when_no_header_match(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_publicFn"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        scoped = _scoped_snapshot("macho", ("other", Visibility.HIDDEN))

        with (
            patch(
                "abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta
            ),
            patch("abicheck.dumper._dump_macho", return_value=scoped),
        ):
            with pytest.warns(
                UserWarning, match="None of the provided headers matched"
            ):
                result = _dump_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert [f.name for f in result.functions] == ["_publicFn"]


class TestRunDumpHeaderWiring:
    """run_dump must forward headers/includes to the PE/Mach-O dumpers."""

    def test_run_dump_pe_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service._dump_pe", return_value=snap) as mock_pe:
            run_dump(p, "pe", [Path("api.h")], [Path("inc")], "1.0", "c++")
        assert mock_pe.call_args.kwargs["headers"] == [Path("api.h")]
        assert mock_pe.call_args.kwargs["includes"] == [Path("inc")]

    def test_run_dump_macho_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="macho")
        with patch("abicheck.service._dump_macho", return_value=snap) as mock_macho:
            run_dump(p, "macho", [Path("api.h")], [], "1.0", "c++")
        assert mock_macho.call_args.kwargs["headers"] == [Path("api.h")]


class TestRunDumpHeaderGraph:
    """``header_graph=True`` embeds the header-only (L2) semantic graph
    (ADR-041 addendum) uniformly across all three binary formats."""

    def test_noop_when_not_requested(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service._dump_pe", return_value=snap):
            result = run_dump(p, "pe", [Path("api.h")], [], "1.0", "c++")
        assert result.build_source is None

    def test_noop_when_no_headers_parsed(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service._dump_pe", return_value=snap):
            result = run_dump(p, "pe", [], [], "1.0", "c++", header_graph=True)
        assert result.build_source is None

    def test_embeds_graph_from_clang_ast(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(
            library="lib",
            version="1.0",
            platform="pe",
            functions=[Function(name="f", mangled="_Z1fv", return_type="void")],
        )
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with (
            patch("abicheck.service._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", return_value=ast) as mock_ast,
        ):
            result = run_dump(p, "pe", [header], [], "1.0", "c++", header_graph=True)
        mock_ast.assert_called_once()
        # The resolved (existing, expanded) header must reach the clang pass —
        # not the raw, unexpanded argument (Codex review).
        assert mock_ast.call_args.args[0] == [header]
        assert result.build_source is not None
        assert result.build_source.source_graph is not None
        node_ids = {n.id for n in result.build_source.source_graph.nodes}
        assert "decl://_Z1fv" in node_ids
        # The manifest coverage row must be populated too (Codex review) — an
        # empty default manifest would read as "L5 not collected" to
        # cli_buildsource_helpers._layer_presence/_optional_coverage even
        # though source_graph is populated.
        from abicheck.buildsource.model import CoverageStatus, DataLayer

        l5 = result.build_source.manifest.coverage_for(DataLayer.L5_SOURCE_GRAPH)
        assert l5 is not None
        assert l5.status == CoverageStatus.PARTIAL  # no edges in this empty AST
        l3 = result.build_source.manifest.coverage_for(DataLayer.L3_BUILD)
        assert l3 is not None
        assert l3.status == CoverageStatus.NOT_COLLECTED

    def test_degrades_gracefully_when_clang_unavailable(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")

        def _raise(*a, **k):
            raise SnapshotError("clang not found")

        with (
            patch("abicheck.service._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", side_effect=_raise) as mock_ast,
        ):
            result = run_dump(p, "pe", [header], [], "1.0", "c++", header_graph=True)
        mock_ast.assert_called_once()
        # Never aborts the dump (ADR-028 D3); the graph is embedded but inert.
        assert result.build_source is not None
        assert result.build_source.source_graph is not None
        assert result.build_source.source_graph.edges == []

    def test_expands_header_directory_before_clang_pass(self, tmp_path):
        # Codex review: a `headers` entry may be a directory (a supported
        # run_dump input the main dump path already expands) — the header
        # graph's own clang pass must see the expanded file list, not the
        # raw directory (which would otherwise get written into an invalid
        # `#include "<dir>"` line and silently degrade to the seed-only
        # graph).
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        hdr_dir = tmp_path / "include"
        hdr_dir.mkdir()
        header = hdr_dir / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        ast = {"kind": "TranslationUnitDecl", "inner": []}
        with (
            patch("abicheck.service._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", return_value=ast) as mock_ast,
        ):
            result = run_dump(p, "pe", [hdr_dir], [], "1.0", "c++", header_graph=True)
        mock_ast.assert_called_once()
        assert mock_ast.call_args.args[0] == [header]
        assert result.build_source is not None
        assert result.build_source.source_graph is not None

    def test_header_graph_includes_folds_include_edges(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pub = tmp_path / "pub.h"
        pub.write_text('#include "detail/impl.h"\n')
        impl_dir = tmp_path / "detail"
        impl_dir.mkdir()
        impl = impl_dir / "impl.h"
        impl.write_text("struct Impl {};\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        ast = {"kind": "TranslationUnitDecl", "inner": []}

        class _Proc:
            stdout = f"pub.o: {pub} {impl}"
            stderr = ""

        with (
            patch("abicheck.service._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", return_value=ast),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph.subprocess.run",
                lambda *a, **k: _Proc(),
            ),
        ):
            result = run_dump(
                p,
                "pe",
                [pub],
                [],
                "1.0",
                "c++",
                header_graph=True,
                header_graph_includes=True,
            )
        graph = result.build_source.source_graph
        pub_id = f"header://{pub}"
        assert any(
            e.kind == "COMPILE_UNIT_INCLUDES_FILE" and e.src == pub_id
            for e in graph.edges
        )
        assert graph.coverage["include_edges"]["collected"] is True

    def test_header_graph_includes_marks_pass_covered_when_map_is_empty(
        self, tmp_path
    ):
        """A leaf header with no #includes of its own is a genuine zero, not
        an uncollected pass — `header_include_graph` must still be stamped so
        `_include_graph_covered` doesn't mistake it for "never ran"."""
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        ast = {"kind": "TranslationUnitDecl", "inner": []}

        class _Proc:
            stdout = f"api.o: {header}"
            stderr = ""
            returncode = 0

        with (
            patch("abicheck.service._dump_pe", return_value=snap),
            patch("abicheck.dumper._clang_header_dump", return_value=ast),
            patch(
                "abicheck.buildsource.include_graph.shutil.which",
                lambda _b: "/usr/bin/clang++",
            ),
            patch(
                "abicheck.buildsource.include_graph.subprocess.run",
                lambda *a, **k: _Proc(),
            ),
        ):
            result = run_dump(
                p,
                "pe",
                [header],
                [],
                "1.0",
                "c++",
                header_graph=True,
                header_graph_includes=True,
            )
        graph = result.build_source.source_graph
        assert not any(e.kind == "COMPILE_UNIT_INCLUDES_FILE" for e in graph.edges)
        assert graph.extractor_passes.get("header_include_graph") is True

    def test_header_graph_includes_ignored_without_header_graph(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        header = tmp_path / "api.h"
        header.write_text("void f();\n")
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service._dump_pe", return_value=snap):
            result = run_dump(
                p, "pe", [header], [], "1.0", "c++", header_graph_includes=True
            )
        assert result.build_source is None


class TestCliNativeBinaryHeaderWiring:
    """CLI _dump_native_binary must forward headers to service._dump_pe/_dump_macho."""

    def test_cli_pe_forwards_headers(self, tmp_path):
        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service._dump_pe", return_value=snap) as mock_pe:
            _dump_native_binary(p, "pe", [Path("api.h")], [Path("inc")], "1.0", "c++")
        assert mock_pe.call_args.kwargs["headers"] == [Path("api.h")]
        assert mock_pe.call_args.kwargs["includes"] == [Path("inc")]

    def test_cli_macho_forwards_headers(self, tmp_path):
        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="macho")
        with patch("abicheck.service._dump_macho", return_value=snap) as mock_macho:
            _dump_native_binary(p, "macho", [Path("api.h")], [], "1.0", "c++")
        assert mock_macho.call_args.kwargs["headers"] == [Path("api.h")]

    def test_cli_pe_wraps_abicheck_error_as_click(self, tmp_path):
        import click

        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch("abicheck.service._dump_pe", side_effect=SnapshotError("boom")):
            with pytest.raises(click.ClickException, match="boom"):
                _dump_native_binary(p, "pe", [], [], "1.0", "c++")

    def test_cli_macho_wraps_abicheck_error_as_click(self, tmp_path):
        import click

        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        with patch("abicheck.service._dump_macho", side_effect=SnapshotError("nope")):
            with pytest.raises(click.ClickException, match="nope"):
                _dump_native_binary(p, "macho", [], [], "1.0", "c++")


def test_run_scan_runs_deferred_build_dir_cleanup(monkeypatch):
    # Fast-lane guard for the scan orchestrator's ownership of the inferred
    # build-dir cleanup (the real end-to-end check is the integration suite):
    # service_scan.run_scan must run the deferred cleanup thunks in its finally —
    # both on success and when run_scan_core raises — so the temp cmake build dir
    # never outlives the scan. Mirrors the same contract in cli_scan.run_scan.
    from types import SimpleNamespace

    from abicheck import service_scan as _ss

    ran = {"n": 0}

    def fake_core(**kw):
        # The orchestrator hands us the cleanup list; register a sentinel thunk the
        # way collect_inline_pack would for an inferred cmake build dir.
        kw["defer_cleanup"].append(lambda: ran.__setitem__("n", ran["n"] + 1))
        outcome = SimpleNamespace(
            verdict="COMPATIBLE",
            exit_code=0,
            coverage=[],
            crosscheck={},
            to_dict=lambda: {},
        )
        return SimpleNamespace(outcome=outcome, findings=[])

    monkeypatch.setattr(_ss, "estimate_scan", lambda req: [])
    monkeypatch.setattr("abicheck.scan_engine.run_scan_core", fake_core)

    req = _ss.ScanRequest(binaries=[Path("libfoo.so")], depth="binary")
    res = _ss.run_scan(req)
    assert res.verdict == "COMPATIBLE"
    assert ran["n"] == 1  # the finally ran the deferred cleanup on success

    # And it still runs when the core raises a budget overflow mid-scan.
    from abicheck.scan_engine import _BudgetOverflow

    ran["n"] = 0

    def raising_core(**kw):
        kw["defer_cleanup"].append(lambda: ran.__setitem__("n", ran["n"] + 1))
        raise _BudgetOverflow("over budget")

    monkeypatch.setattr("abicheck.scan_engine.run_scan_core", raising_core)
    res = _ss.run_scan(req)
    assert res.exit_code == 5  # budget overflow surfaced
    assert ran["n"] == 1  # finally still ran the cleanup on the raise path

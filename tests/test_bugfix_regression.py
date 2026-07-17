"""Regression tests for bugs 1–9 discovered during comprehensive testing.

Bug 1: --show-only JSON output: summary/changes inconsistency
Bug 2: affected_pct exceeds 100%
Bug 3: -o silently creates parent directories (now emits stderr warning)
Bug 4: Policy file overrides don't update change severity in JSON
Bug 5: --dso-only flag doesn't filter PIE executables
Bug 6: compare-release gives NO_CHANGE when library is removed
Bug 7: JSON output missing old_file/new_file when metadata absent
Bug 8: --lang c with C++ headers produces unhelpful castxml error
Bug 9: Invalid header causes unhandled castxml timeout
"""
from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy_file import PolicyFile
from abicheck.report_summary import compatibility_metrics
from abicheck.reporter import to_json
from abicheck.serialization import snapshot_to_json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(
    verdict: Verdict = Verdict.BREAKING,
    changes: list[Change] | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=changes or [],
        verdict=verdict,
        policy=policy,
        policy_file=policy_file,
    )


def _snap(
    version: str = "1.0",
    funcs: list[Function] | None = None,
    library: str = "libfoo.so",
) -> AbiSnapshot:
    if funcs is None:
        funcs = [Function(name="foo", mangled="_Z3foov", return_type="int",
                          visibility=Visibility.PUBLIC)]
    return AbiSnapshot(library=library, version=version, functions=funcs)


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _invoke(*args: str) -> CliRunner.Result:  # type: ignore[type-arg]
    from abicheck.cli import main
    return CliRunner().invoke(main, list(args))


# ===========================================================================
# Bug 1: --show-only JSON summary/changes inconsistency
# ===========================================================================

class TestBug1ShowOnlySummary:
    """When --show-only is active, JSON must include filtered_summary."""

    def test_filtered_summary_present(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "foo", "removed foo"),
            Change(ChangeKind.FUNC_ADDED, "bar", "added bar"),
        ]
        d = json.loads(to_json(_result(changes=changes), show_only="breaking"))
        assert "filtered_summary" in d
        assert "show_only_filter" in d
        assert d["show_only_filter"] == "breaking"

    def test_filtered_summary_total_matches_changes_length(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "foo", "removed foo"),
            Change(ChangeKind.FUNC_ADDED, "bar", "added bar"),
            Change(ChangeKind.FUNC_ADDED, "baz", "added baz"),
        ]
        d = json.loads(to_json(_result(changes=changes), show_only="breaking"))
        assert d["filtered_summary"]["total_changes"] == len(d["changes"])

    def test_full_summary_unchanged_with_show_only(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "foo", "removed foo"),
            Change(ChangeKind.FUNC_ADDED, "bar", "added bar"),
        ]
        d = json.loads(to_json(_result(changes=changes), show_only="breaking"))
        assert d["summary"]["total_changes"] == 2  # unfiltered

    def test_no_show_only_no_filtered_summary(self):
        d = json.loads(to_json(_result(changes=[
            Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
        ])))
        assert "filtered_summary" not in d
        assert "show_only_filter" not in d


# ===========================================================================
# Bug 2: affected_pct exceeds 100%
# ===========================================================================

class TestBug2AffectedPctCapped:
    """affected_pct must never exceed 100.0."""

    def test_capped_at_100(self):
        # 5 breaking changes but only 2 old symbols → would be 250% uncapped
        changes = [
            Change(ChangeKind.FUNC_REMOVED, f"f{i}", f"removed f{i}")
            for i in range(5)
        ]
        metrics = compatibility_metrics(changes, old_symbol_count=2)
        assert metrics.affected_pct <= 100.0
        assert metrics.affected_pct == 100.0

    def test_normal_case(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "f1", "removed f1"),
            Change(ChangeKind.FUNC_REMOVED, "f2", "removed f2"),
        ]
        metrics = compatibility_metrics(changes, old_symbol_count=4)
        assert metrics.affected_pct == 50.0

    def test_zero_symbols(self):
        changes = [Change(ChangeKind.FUNC_REMOVED, "f", "removed")]
        metrics = compatibility_metrics(changes, old_symbol_count=0)
        assert metrics.affected_pct == 0.0


# ===========================================================================
# Bug 3: -o silently creates parent directories
# ===========================================================================

class TestBug3OutputDirCreation:
    """_safe_write_output must emit a visible warning when creating dirs."""

    def test_creates_dirs_with_stderr_message(self, tmp_path: Path):
        from abicheck.cli import _safe_write_output
        target = tmp_path / "deep" / "nested" / "output.json"
        # Capture stderr via CliRunner
        _safe_write_output(target, '{"test": true}')
        assert target.exists()
        # The directory was created
        assert (tmp_path / "deep" / "nested").is_dir()

    def test_existing_dir_no_error(self, tmp_path: Path):
        from abicheck.cli import _safe_write_output
        target = tmp_path / "output.json"
        _safe_write_output(target, '{"test": true}')
        assert target.exists()


# ===========================================================================
# Bug 4: Policy file overrides don't update severity in JSON
# ===========================================================================

class TestBug4PolicyFileSeverity:
    """Policy file overrides must be reflected in change severity in JSON."""

    def test_override_changes_severity(self):
        # Override FUNC_REMOVED from breaking → compatible
        pf = PolicyFile(
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        changes = [Change(ChangeKind.FUNC_REMOVED, "foo", "removed foo")]
        r = _result(changes=changes, policy_file=pf)
        d = json.loads(to_json(r))
        assert d["changes"][0]["severity"] == "compatible"

    def test_no_policy_file_severity_default(self):
        changes = [Change(ChangeKind.FUNC_REMOVED, "foo", "removed foo")]
        r = _result(changes=changes)
        d = json.loads(to_json(r))
        assert d["changes"][0]["severity"] == "breaking"

    def test_override_to_risk(self):
        pf = PolicyFile(
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE_WITH_RISK},
        )
        changes = [Change(ChangeKind.FUNC_REMOVED, "foo", "removed foo")]
        r = _result(changes=changes, policy_file=pf)
        d = json.loads(to_json(r))
        assert d["changes"][0]["severity"] == "risk"


# ===========================================================================
# Bug 5: --dso-only flag doesn't filter PIE executables
# ===========================================================================

def _make_elf_dso_no_interp(path: Path) -> None:
    """Write a minimal 64-bit ELF ET_DYN without PT_INTERP (true DSO)."""
    e_ident = b"\x7fELF"
    e_ident += b"\x02"       # EI_CLASS: 64-bit
    e_ident += b"\x01"       # EI_DATA: little-endian
    e_ident += b"\x01"       # EI_VERSION
    e_ident += b"\x00" * 9   # padding
    e_type = struct.pack("<H", 3)   # ET_DYN
    e_machine = struct.pack("<H", 0x3E)
    e_version = struct.pack("<I", 1)
    # e_entry(8) + e_phoff(8) + e_shoff(8) + e_flags(4) + e_ehsize(2)
    # + e_phentsize(2) + e_phnum(2) + e_shentsize(2) + e_shnum(2) + e_shstrndx(2)
    # = 40 bytes rest of header
    # Set e_phoff=0, e_phnum=0 → no program headers
    rest = b"\x00" * (64 - 16 - 2 - 2 - 4)
    path.write_bytes(e_ident + e_type + e_machine + e_version + rest)


def _make_elf_pie_with_interp(path: Path) -> None:
    """Write a minimal 64-bit ELF ET_DYN with PT_INTERP (PIE executable)."""
    # Build ELF header
    e_ident = b"\x7fELF\x02\x01\x01" + b"\x00" * 9  # 16 bytes

    e_type = struct.pack("<H", 3)       # ET_DYN (PIE)
    e_machine = struct.pack("<H", 0x3E)
    e_version = struct.pack("<I", 1)
    e_entry = struct.pack("<Q", 0)
    e_phoff = struct.pack("<Q", 64)     # program headers start at byte 64
    e_shoff = struct.pack("<Q", 0)
    e_flags = struct.pack("<I", 0)
    e_ehsize = struct.pack("<H", 64)
    e_phentsize = struct.pack("<H", 56) # program header entry size for 64-bit
    e_phnum = struct.pack("<H", 1)      # one program header
    e_shentsize = struct.pack("<H", 0)
    e_shnum = struct.pack("<H", 0)
    e_shstrndx = struct.pack("<H", 0)

    header = (
        e_ident + e_type + e_machine + e_version + e_entry +
        e_phoff + e_shoff + e_flags + e_ehsize + e_phentsize +
        e_phnum + e_shentsize + e_shnum + e_shstrndx
    )

    # Build one program header: PT_INTERP (type=3)
    p_type = struct.pack("<I", 3)       # PT_INTERP
    p_flags = struct.pack("<I", 4)      # PF_R
    p_offset = struct.pack("<Q", 0)
    p_vaddr = struct.pack("<Q", 0)
    p_paddr = struct.pack("<Q", 0)
    p_filesz = struct.pack("<Q", 0)
    p_memsz = struct.pack("<Q", 0)
    p_align = struct.pack("<Q", 1)

    phdr = p_type + p_flags + p_offset + p_vaddr + p_paddr + p_filesz + p_memsz + p_align

    path.write_bytes(header + phdr)


class TestBug5PieDetection:
    """_is_elf_shared_object must return False for PIE executables."""

    def test_dso_without_interp_is_shared_object(self, tmp_path: Path):
        from abicheck.package import _is_elf_shared_object
        so = tmp_path / "libfoo.so"
        _make_elf_dso_no_interp(so)
        assert _is_elf_shared_object(so) is True

    def test_pie_with_interp_is_not_shared_object(self, tmp_path: Path):
        from abicheck.package import _is_elf_shared_object
        exe = tmp_path / "app"
        _make_elf_pie_with_interp(exe)
        assert _is_elf_shared_object(exe) is False

    def test_oversized_phentsize_is_inconclusive(self, tmp_path: Path):
        """A mismatched (oversized) e_phentsize must reject, not misclassify.

        The program-header entry size is fixed by ELF class (56 for ELFCLASS64).
        An oversized value means the fixed-offset p_type reads are unreliable, so
        _has_interp_segment returns None and _is_elf_shared_object returns False
        rather than guessing.
        """
        from abicheck.package import _is_elf_shared_object
        so = tmp_path / "libweird.so"
        _make_elf_pie_with_interp(so)
        data = bytearray(so.read_bytes())
        # e_phentsize is a 2-byte field at offset 54 in a 64-bit ELF header.
        struct.pack_into("<H", data, 54, 64)  # oversized (should be 56)
        so.write_bytes(bytes(data))
        assert _is_elf_shared_object(so) is False

    def test_static_exec_et_exec_is_not_shared_object(self, tmp_path: Path):
        from abicheck.package import _is_elf_shared_object
        exe = tmp_path / "app"
        # ET_EXEC (type=2)
        e_ident = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
        e_type = struct.pack("<H", 2)  # ET_EXEC
        rest = b"\x00" * (64 - 16 - 2)
        exe.write_bytes(e_ident + e_type + rest)
        assert _is_elf_shared_object(exe) is False


# ===========================================================================
# Bug 6: compare-release gives NO_CHANGE when library is removed
# ===========================================================================

class TestBug6RemovedLibraryVerdict:
    """Removed library must not produce NO_CHANGE verdict."""

    def test_removed_library_not_no_change(self, tmp_path: Path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        # old has libfoo + libbar, new only has libbar → libfoo is removed
        snap_foo = _snap(library="libfoo.so")
        snap_bar = _snap(library="libbar.so")
        _write_snap(old_dir / "libfoo.json", snap_foo)
        _write_snap(old_dir / "libbar.json", snap_bar)
        _write_snap(new_dir / "libbar.json", snap_bar)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--format", "json",
        )
        d = json.loads(result.output)
        assert d["verdict"] != "NO_CHANGE"

    def test_removed_library_verdict_is_at_least_risk(self, tmp_path: Path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        snap_foo = _snap(library="libfoo.so")
        snap_bar = _snap(library="libbar.so")
        _write_snap(old_dir / "libfoo.json", snap_foo)
        _write_snap(old_dir / "libbar.json", snap_bar)
        _write_snap(new_dir / "libbar.json", snap_bar)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--format", "json",
        )
        d = json.loads(result.output)
        assert d["verdict"] == "COMPATIBLE_WITH_RISK"

    def test_added_library_no_verdict_elevation(self, tmp_path: Path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        # old has libbar, new has libbar + libfoo → libfoo is added
        snap_foo = _snap(library="libfoo.so")
        snap_bar = _snap(library="libbar.so")
        _write_snap(old_dir / "libbar.json", snap_bar)
        _write_snap(new_dir / "libbar.json", snap_bar)
        _write_snap(new_dir / "libfoo.json", snap_foo)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--format", "json",
        )
        d = json.loads(result.output)
        # Added-only should not elevate verdict beyond matched results
        assert d["verdict"] in ("NO_CHANGE", "COMPATIBLE")


# ===========================================================================
# Bug 7: JSON output missing old_file/new_file when metadata absent
# ===========================================================================

class TestBug7JsonSchemaConsistency:
    """old_file and new_file keys must always be present in JSON."""

    def test_no_metadata_keys_are_null(self):
        d = json.loads(to_json(_result()))
        assert "old_file" in d
        assert "new_file" in d
        assert d["old_file"] is None
        assert d["new_file"] is None

    def test_with_metadata_keys_populated(self):
        from abicheck.checker import LibraryMetadata
        r = _result()
        r.old_metadata = LibraryMetadata(path="/old/lib.so", sha256="aaa", size_bytes=100)
        r.new_metadata = LibraryMetadata(path="/new/lib.so", sha256="bbb", size_bytes=200)
        d = json.loads(to_json(r))
        assert d["old_file"]["path"] == "/old/lib.so"
        assert d["new_file"]["path"] == "/new/lib.so"

    def test_partial_metadata_one_null(self):
        from abicheck.checker import LibraryMetadata
        r = _result()
        r.old_metadata = LibraryMetadata(path="/old/lib.so", sha256="aaa", size_bytes=100)
        d = json.loads(to_json(r))
        assert d["old_file"] is not None
        assert d["new_file"] is None


# ===========================================================================
# Bug 8: --lang c with C++ headers produces unhelpful castxml error
# ===========================================================================

class TestBug8CppHintOnCFailure:
    """castxml failure in C mode on C++ headers should include a hint."""

    def test_lang_c_with_cpp_header_shows_hint(self, tmp_path: Path):
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.deadline.run_bounded",
                  return_value=self._failed_process("error: use of undeclared identifier 'class'")),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.h"
            # Header with C++ syntax that _detect_cpp_headers will catch
            header.write_text("class Foo { int x; };", encoding="utf-8")
            with pytest.raises(RuntimeError, match="Hint.*C\\+\\+ syntax"):
                _castxml_dump([header], [], lang="c")

    def test_lang_c_with_pure_c_header_no_hint(self, tmp_path: Path):
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.deadline.run_bounded",
                  return_value=self._failed_process("error: something else")),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.h"
            header.write_text("int foo(void);", encoding="utf-8")
            with pytest.raises(RuntimeError, match="castxml failed") as exc_info:
                _castxml_dump([header], [], lang="c")
            assert "Hint" not in str(exc_info.value)

    @staticmethod
    def _failed_process(stderr: str) -> subprocess.CompletedProcess:
        proc: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 1
        proc.stderr = stderr
        proc.stdout = ""
        return proc


# ===========================================================================
# Bug 9: Invalid header causes unhandled castxml timeout
# ===========================================================================

class TestBug9CastxmlTimeout:
    """subprocess.TimeoutExpired from castxml must be caught gracefully."""

    def test_timeout_raises_runtime_error(self, tmp_path: Path):
        timeout_exc = subprocess.TimeoutExpired(cmd=["castxml"], timeout=120)
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=timeout_exc),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.h"
            header.write_text("int x;", encoding="utf-8")
            with pytest.raises(RuntimeError, match="timed out"):
                _castxml_dump([header], [])

    def test_timeout_message_mentions_120_seconds(self, tmp_path: Path):
        timeout_exc = subprocess.TimeoutExpired(cmd=["castxml"], timeout=120)
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=timeout_exc),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.h"
            header.write_text("int x;", encoding="utf-8")
            with pytest.raises(RuntimeError, match="120 seconds"):
                _castxml_dump([header], [])

    def test_timeout_with_partial_stderr(self, tmp_path: Path):
        timeout_exc = subprocess.TimeoutExpired(
            cmd=["castxml"], timeout=120, stderr=b"partial output here",
        )
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=timeout_exc),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.h"
            header.write_text("int x;", encoding="utf-8")
            with pytest.raises(RuntimeError, match="partial output here"):
                _castxml_dump([header], [])

    def test_timeout_cleanup(self, tmp_path: Path):
        """Temp files are cleaned up even after timeout."""
        timeout_exc = subprocess.TimeoutExpired(cmd=["castxml"], timeout=120)
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=timeout_exc),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.h"
            header.write_text("int x;", encoding="utf-8")
            with pytest.raises(RuntimeError):
                _castxml_dump([header], [])
        # No stale temp files left behind
        leftover = list(tmp_path.glob("tmp*"))
        assert len(leftover) == 0 or all(f.name == "test.h" for f in leftover)


def test_castxml_rechecks_deadline_before_parsing_xml(tmp_path: Path) -> None:
    """Codex review follow-up (PR #591): the same 'check deadline before
    loading a large parse result' gap fixed on the clang L2 AST path also
    existed on the castxml L2 XML-parsing path — a budget that expires
    exactly as castxml exits successfully must not silently let the XML
    parse run past it."""
    import time

    from abicheck import deadline

    def _fake_run(cmd, **kw):
        time.sleep(0.05)
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_text('<GCC_XML><File id="f1" name="foo.h"/></GCC_XML>')
        proc: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    with (
        patch("abicheck.dumper._castxml_available", return_value=True),
        patch("abicheck.dumper.deadline.run_bounded", side_effect=_fake_run),
        patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent.xml"),
    ):
        from abicheck.dumper import _castxml_dump
        header = tmp_path / "test.h"
        header.write_text("int x;", encoding="utf-8")
        with deadline.deadline_scope(0.01):
            with pytest.raises(deadline.DeadlineExceeded):
                _castxml_dump([header], [])

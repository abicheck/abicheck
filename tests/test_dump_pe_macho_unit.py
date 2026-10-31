"""Unit tests for PE and Mach-O dump extraction and header-scoped dumps (issue #235)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot, Function, Visibility

# ── extract_pe() ──────────────────────────────────────────────────────────────


class TestHeaderScopedInferredRoots:
    """P3 parity: the PE/Mach-O header-scoped path also adds inferred -H roots."""

    def _umbrella(self, tmp_path):
        root = tmp_path / "include"
        (root / "oneapi").mkdir(parents=True)
        umb = root / "oneapi" / "tbb.h"
        umb.write_text("int f(void);\n", encoding="utf-8")
        return root, umb

    def test_pe_no_build_context_adds_root_as_I(self, tmp_path):
        from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

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
        from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

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
        from abicheck.dry_run_estimate import CompileContext
        from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

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

    def test_pe_header_scoped_dump_attaches_extraction_contract(self, tmp_path):
        """ADR-050 D1 (Codex review, PR #624 follow-up): this path calls
        dumper._dump_pe directly, bypassing dumper.dump() entirely -- without
        the _attach_extraction_contract call, contract would silently stay
        None for every real PE header-scoped dump, unlike the ELF service
        path (_dump_elf), which already routes through dumper.dump()."""
        from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            return AbiSnapshot(
                library="x",
                version="1.0",
                from_headers=True,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                    )
                ],
            )

        with patch("abicheck.dumper._dump_pe", fake_pe):
            snap, reason = _try_header_scoped_dump(
                "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++"
            )
        assert reason is None
        assert snap is not None
        assert snap.contract is not None
        assert snap.contract.profile_fingerprint is not None

    def test_pe_header_scoped_dump_threads_public_headers_into_contract(self, tmp_path):
        """Codex review, PR #624 follow-up: run_dump applies public_headers/
        public_header_dirs to a PE/Mach-O snapshot separately via
        _apply_native_provenance *after* this call returns, but that same
        provenance input must also reach the scope_fingerprint here -- else
        two saved snapshots differing only in declared public-header
        provenance could share a scope_fingerprint."""
        from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)

        def fake_pe(path, headers, extra_includes, version, compiler, **k):
            return AbiSnapshot(
                library="x",
                version="1.0",
                from_headers=True,
                functions=[
                    Function(
                        name="f",
                        mangled="f",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                    )
                ],
            )

        with patch("abicheck.dumper._dump_pe", fake_pe):
            snap_no_public, _ = _try_header_scoped_dump(
                "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++"
            )
            snap_with_public, _ = _try_header_scoped_dump(
                "pe",
                tmp_path / "x.dll",
                [umb],
                [],
                "1.0",
                "c++",
                public_header_dirs=[root],
            )

        assert snap_no_public.contract is not None
        assert snap_with_public.contract is not None
        assert (
            snap_no_public.contract.scope_fingerprint
            != snap_with_public.contract.scope_fingerprint
        )

    def test_deadline_exceeded_propagates_not_swallowed_as_fallback(self, tmp_path):
        # Codex review on the P0 fix: the broad `except Exception` below (which
        # exists to fall back to export-table mode when a header backend is
        # merely unavailable) must NOT also swallow an active --budget's
        # deadline.DeadlineExceeded. Falling back on that would silently mask
        # the overflow (a degraded-but-"successful" scan instead of the
        # dedicated budget-overflow exit code) and let the scan keep doing
        # work past the point it should have aborted. It must propagate so
        # run_scan_core's except deadline.DeadlineExceeded -> _BudgetOverflow
        # mapping applies to PE/Mach-O the same way it already does for ELF.
        from abicheck import deadline
        from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

        root, umb = self._umbrella(tmp_path)

        def raises_deadline_exceeded(
            path, headers, extra_includes, version, compiler, **k
        ):
            raise deadline.DeadlineExceeded(-1.0)

        with patch("abicheck.dumper._dump_pe", raises_deadline_exceeded):
            with pytest.raises(deadline.DeadlineExceeded):
                _try_header_scoped_dump(
                    "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++"
                )

        with patch("abicheck.dumper._dump_macho", raises_deadline_exceeded):
            with pytest.raises(deadline.DeadlineExceeded):
                _try_header_scoped_dump(
                    "macho", tmp_path / "x.dylib", [umb], [], "1.0", "c++"
                )

    def test_explicit_device_context_failure_propagates_not_swallowed(self, tmp_path):
        # Codex review: AstContextMissingError/AstContextAmbiguousError only
        # ever come from a NON-"host" --frontend-context request (ADR-050
        # D5) -- there is no "device" default, so seeing either here always
        # means the user's explicit device-context request failed. The
        # broad `except Exception` below (which exists to fall back to
        # export-table mode when a header backend is merely unavailable)
        # must not also swallow this and silently succeed with --header/
        # --include ignored, exactly the same reasoning as the
        # DeadlineExceeded test above.
        from abicheck.dry_run_estimate import CompileContext
        from abicheck.errors import AstContextMissingError
        from abicheck.workflows.dump.header_scoped import _try_header_scoped_dump

        _root, umb = self._umbrella(tmp_path)

        def raises_ast_context_missing(
            path, headers, extra_includes, version, compiler, **k
        ):
            raise AstContextMissingError("no AST context with kind='device'")

        cc = CompileContext(frontend_context="device")

        with patch("abicheck.dumper._dump_pe", raises_ast_context_missing):
            with pytest.raises(AstContextMissingError):
                _try_header_scoped_dump(
                    "pe", tmp_path / "x.dll", [umb], [], "1.0", "c++", compile=cc
                )

        with patch("abicheck.dumper._dump_macho", raises_ast_context_missing):
            with pytest.raises(AstContextMissingError):
                _try_header_scoped_dump(
                    "macho",
                    tmp_path / "x.dylib",
                    [umb],
                    [],
                    "1.0",
                    "c++",
                    compile=cc,
                )


class TestDumpPe:
    def test_no_machine_raises(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = None
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(SnapshotError, match="Failed to extract PE metadata"):
                extract_pe(p, "1.0")

    def test_no_exports_raises(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(ValidationError, match="no exports"):
                extract_pe(p, "1.0")

    def test_successful_pe_dump(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

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
                result = extract_pe(p, "1.0")
        assert result.platform == "pe"
        assert len(result.declarations.functions) == 1
        assert result.declarations.functions[0].name == "MyFunc"

    def test_pe_import_error(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch(
            "abicheck.pe_metadata.parse_pe_metadata",
            side_effect=ImportError("no pefile"),
        ):
            with pytest.raises(SnapshotError, match="no pefile"):
                extract_pe(p, "1.0")

    def test_pe_runtime_error(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch(
            "abicheck.pe_metadata.parse_pe_metadata",
            side_effect=RuntimeError("corrupt"),
        ):
            with pytest.raises(SnapshotError, match="Failed to parse PE"):
                extract_pe(p, "1.0")

    def test_ordinal_export(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

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
                result = extract_pe(p, "1.0")
        assert result.declarations.functions[0].name == "ordinal:42"

    def test_pdb_found_and_parsed(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

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
                    result = extract_pe(p, "1.0")
        assert result.declarations.debug_layout is mock_dwarf
        assert result.declarations.debug_advanced is mock_adv

    def test_pdb_parsing_exception_handled(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

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
                result = extract_pe(p, "1.0")
        assert result.declarations.debug_layout is None

    def test_cpp_name_not_extern_c(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

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
                result = extract_pe(p, "1.0")
        assert result.declarations.functions[0].is_extern_c is False


# ── extract_macho() ───────────────────────────────────────────────────────────


class TestDumpMacho:
    def test_successful_macho_dump(self, tmp_path):
        from abicheck.workflows.dump.macho import extract_macho

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
            result = extract_macho(p, "1.0")
        assert result.platform == "macho"
        assert len(result.declarations.functions) == 1

    def test_no_exports_no_metadata_raises(self, tmp_path):
        from abicheck.workflows.dump.macho import extract_macho

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
                extract_macho(p, "1.0")

    def test_parse_error(self, tmp_path):
        from abicheck.workflows.dump.macho import extract_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        with patch(
            "abicheck.macho_metadata.parse_macho_metadata",
            side_effect=RuntimeError("bad macho"),
        ):
            with pytest.raises(SnapshotError, match="Failed to parse Mach-O"):
                extract_macho(p, "1.0")

    def test_export_without_name_skipped(self, tmp_path):
        from abicheck.workflows.dump.macho import extract_macho

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
            result = extract_macho(p, "1.0")
        assert len(result.declarations.functions) == 1

    def test_cpp_symbol_not_extern_c(self, tmp_path):
        from abicheck.workflows.dump.macho import extract_macho

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
            result = extract_macho(p, "1.0")
        assert result.declarations.functions[0].is_extern_c is False


# ── Header-scoped PE/Mach-O dumps (issue #235) ───────────────────────────────


def _scoped_snapshot(platform: str, *funcs: tuple[str, Visibility]) -> AbiSnapshot:
    """Build a fake header-scoped snapshot as ``dumper._dump_*`` would return."""
    from abicheck.model import RecordType

    snap = AbiSnapshot(library="lib", version="1.0", platform=platform)
    snap.declarations.functions = [
        Function(name=n, mangled=n, return_type="int", visibility=v) for n, v in funcs
    ]
    # A header-scoped dump carries real type info (so layout diffs still fire).
    snap.declarations.types = [RecordType(name="PublicStruct", kind="struct")]
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
        from abicheck.workflows.dump.pe import extract_pe

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
            result = extract_pe(
                p, "1.0", headers=[_mk_header(tmp_path)], includes=[Path("inc")]
            )

        # The header-aware dumper was actually invoked with the (expanded) headers.
        assert mock_dump.called
        called_headers = mock_dump.call_args.args[1]
        assert called_headers == [tmp_path / "api.h"]
        # Surface is scoped: private export absent, public symbol present.
        names = [f.name for f in result.declarations.functions]
        assert "PublicApiFunc" in names
        assert "InternalPrivateFunc" not in names
        # Type info preserved so reachable layout changes still diff.
        assert any(t.name == "PublicStruct" for t in result.declarations.types)

    def test_private_export_absent_from_headers_not_compared(self, tmp_path):
        """An exported-but-private symbol removed in 'new' must not surface."""
        from abicheck.checker import compare
        from abicheck.workflows.dump.pe import extract_pe

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
                old_snap = extract_pe(old_p, "1.0", headers=[_mk_header(tmp_path)])
            with (
                patch("abicheck.pe_metadata.parse_pe_metadata", return_value=new_pe),
                patch("abicheck.dumper._dump_pe", return_value=new_scoped),
            ):
                new_snap = extract_pe(new_p, "2.0", headers=[_mk_header(tmp_path)])

        result = compare(old_snap, new_snap)
        removed = [
            c for c in result.changes if "InternalPrivateFunc" in (c.symbol or "")
        ]
        assert removed == [], f"private export must not be reported: {removed}"

    def test_fallback_when_no_header_match(self, tmp_path):
        """MSVC-mangled C++ exports won't match Itanium names → warn + fallback."""
        from abicheck.workflows.dump.pe import extract_pe

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
                result = extract_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        # Fell back to the full export table.
        names = [f.name for f in result.declarations.functions]
        assert "?realFunc@@YAHXZ" in names

    def test_fallback_when_castxml_unavailable(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

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
                result = extract_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        names = [f.name for f in result.declarations.functions]
        assert "PublicApiFunc" in names

    def test_no_headers_uses_export_table(self, tmp_path):
        """Without headers, behaviour is unchanged: full export table, PUBLIC."""
        from abicheck.workflows.dump.pe import extract_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc", "InternalPrivateFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
            patch("abicheck.dumper._dump_pe") as mock_dump,
        ):
            funcs = extract_pe(p, "1.0").declarations.functions

        assert not mock_dump.called  # castxml path never taken
        names = {f.name for f in funcs}
        assert names == {"PublicApiFunc", "InternalPrivateFunc"}
        assert all(f.visibility == Visibility.PUBLIC for f in funcs)

    def test_pdb_debug_preserved_on_scoped_snapshot(self, tmp_path):
        from abicheck.workflows.dump.pe import extract_pe

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
                "abicheck.workflows.dump.pe.extract_pdb_debug_outcome",
                return_value=(dwarf_meta, dwarf_adv, None),
            ),
        ):
            result = extract_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        assert result.declarations.debug_layout is dwarf_meta
        assert result.declarations.debug_advanced is dwarf_adv

    def test_header_directory_is_expanded(self, tmp_path):
        """`--header <dir>` must expand to files, not feed a dir to castxml."""
        from abicheck.workflows.dump.pe import extract_pe

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
            extract_pe(p, "1.0", headers=[hdr_dir])

        # The dumper received the individual header files, not the directory.
        called_headers = mock_dump.call_args.args[1]
        assert hdr_dir not in called_headers
        assert {h.name for h in called_headers} == {"a.h", "b.hpp"}

    def test_bad_header_path_raises_not_silent_fallback(self, tmp_path):
        """A nonexistent header must raise, not silently fall back to exports."""
        from abicheck.workflows.dump.pe import extract_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")

        with (
            patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta),
            patch("abicheck.pdb_utils.locate_pdb", return_value=None),
        ):
            with pytest.raises(ValidationError, match="not found"):
                extract_pe(p, "1.0", headers=[tmp_path / "missing.h"])


class TestMachoHeaderScoping:
    def test_headers_route_to_castxml_scoped_dump(self, tmp_path):
        from abicheck.workflows.dump.macho import extract_macho

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
            result = extract_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert mock_dump.called
        assert [f.name for f in result.declarations.functions] == ["publicFn"]

    def test_fallback_when_no_header_match(self, tmp_path):
        from abicheck.workflows.dump.macho import extract_macho

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
                result = extract_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert [f.name for f in result.declarations.functions] == ["_publicFn"]

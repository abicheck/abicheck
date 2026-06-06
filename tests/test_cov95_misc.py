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

"""Coverage-raising unit tests for bundle, mcp_server, checker_policy,
baseline, and classify modules.

Pure-Python only — no external tools. Uses tmp_path and unittest.mock for
I/O-heavy paths. Every test asserts a meaningful invariant.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import mcp_server under a temporary `mcp` mock (mirrors the pattern in
# tests/test_mcp_server_coverage.py). We then restore sys.modules to its
# pre-import state so this module does NOT globally flip mcp-conditional skips
# in sibling test files (e.g. tests/test_mcp_hardening.py), which would change
# session-wide test ordering/state. The imported mcp_server symbols stay valid
# because the module object is already bound to the names we need.
# ---------------------------------------------------------------------------


def _import_mcp_server_symbols():  # noqa: ANN202
    """Import the mcp_server symbols we test, leaving sys.modules unperturbed."""
    injected = [
        k for k in ("mcp", "mcp.server", "mcp.server.fastmcp") if k not in sys.modules
    ]
    mcp_server_was_present = "abicheck.mcp_server" in sys.modules

    _mock_fastmcp = MagicMock()
    _mock_mcp_module = MagicMock()
    _mock_mcp_module.server.fastmcp.FastMCP = _mock_fastmcp
    _mock_instance = MagicMock()
    _mock_instance.tool.return_value = lambda fn: fn
    _mock_fastmcp.return_value = _mock_instance
    for key, mod in (
        ("mcp", _mock_mcp_module),
        ("mcp.server", _mock_mcp_module.server),
        ("mcp.server.fastmcp", _mock_mcp_module.server.fastmcp),
    ):
        sys.modules.setdefault(key, mod)

    import abicheck.mcp_server as _ms

    # Restore sys.modules to look exactly as it did before this import, so we
    # don't enable mcp for files collected after us.
    for key in injected:
        sys.modules.pop(key, None)
    if not mcp_server_was_present:
        sys.modules.pop("abicheck.mcp_server", None)
    return _ms


_ms = _import_mcp_server_symbols()
_env_int = _ms._env_int
_check_file_size = _ms._check_file_size
_audit_log = _ms._audit_log
_impact_category = _ms._impact_category
_resolve_input = _ms._resolve_input
_snapshot_summary = _ms._snapshot_summary
abi_dump = _ms.abi_dump
abi_compare = _ms.abi_compare


# ===========================================================================
# classify.py
# ===========================================================================

from abicheck.classify import (  # noqa: E402
    AbiJsonClassifier,
    FallbackSniffClassifier,
    _sniff_head,
    is_supported_compare_input,
)

_PERL_DUMP_HEAD = "$VAR1 = {\n  'TypeInfo' => {\n"


class TestClassifyErrorBranches:
    def test_sniff_head_oserror_returns_empty(self, tmp_path: Path, caplog) -> None:
        """_sniff_head logs a warning and returns '' on OSError (lines 74-76)."""
        # A directory cannot be opened for reading -> IsADirectoryError (OSError).
        d = tmp_path / "adir"
        d.mkdir()
        with caplog.at_level("WARNING"):
            result = _sniff_head(d)
        assert result == ""
        assert "cannot read" in caplog.text

    def test_abijson_classifier_oserror_returns_false(
        self, tmp_path: Path, caplog
    ) -> None:
        """AbiJsonClassifier.accepts returns False on read error (lines 176-178)."""
        d = tmp_path / "dir.json"
        d.mkdir()
        with caplog.at_level("WARNING"):
            result = AbiJsonClassifier().accepts(d)
        assert result is False
        assert "cannot read JSON candidate" in caplog.text

    def test_fallback_sniff_accepts_perl_dump(self, tmp_path: Path) -> None:
        """FallbackSniffClassifier accepts a Perl dump on odd extension (line 205)."""
        p = tmp_path / "dump.weirdext"
        p.write_text(_PERL_DUMP_HEAD, encoding="utf-8")
        assert FallbackSniffClassifier().accepts(p) is True

    def test_fallback_sniff_json_read_error_returns_false(self, caplog) -> None:
        """FallbackSniffClassifier handles OSError on the JSON re-read (213-215)."""
        clf = FallbackSniffClassifier()
        # head sniffs as JSON ('{'), but the subsequent full read raises OSError.
        with patch("abicheck.classify._sniff_head", return_value="{not really"):
            with patch("abicheck.classify.open", side_effect=OSError("boom")):
                with caplog.at_level("WARNING"):
                    result = clf.accepts(Path("/whatever.bin"))
        assert result is False
        assert "fallback JSON candidate" in caplog.text

    def test_pipeline_all_abstain_returns_false(self, tmp_path: Path) -> None:
        """is_supported_compare_input returns False when nothing matches (line 256)."""
        p = tmp_path / "plain.txt"
        p.write_text("just some text, not a binary or snapshot", encoding="utf-8")
        assert is_supported_compare_input(p) is False

    def test_pipeline_rejects_nonexistent(self, tmp_path: Path) -> None:
        assert is_supported_compare_input(tmp_path / "nope") is False


# ===========================================================================
# checker_policy.py — reachable policy functions
# ===========================================================================

from abicheck.checker_policy import (  # noqa: E402
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    PLUGIN_ABI_DOWNGRADED_KINDS,
    RISK_KINDS,
    SDK_VENDOR_COMPAT_KINDS,
    ChangeKind,
    EvidenceTier,
    Verdict,
    compute_verdict,
    impact_for,
    policy_for,
    policy_kind_sets,
    policy_registry_markdown,
)
from abicheck.checker_types import Change  # noqa: E402


def _change(kind: ChangeKind) -> Change:
    return Change(kind=kind, symbol="sym", description="d")


class TestEvidenceTierRank:
    def test_rank_ordering(self) -> None:
        """EvidenceTier.rank returns increasing depth (line 508)."""
        assert EvidenceTier.ELF_ONLY.rank == 0
        assert EvidenceTier.DWARF_AWARE.rank == 1
        assert EvidenceTier.HEADER_AWARE.rank == 2
        assert EvidenceTier.HEADER_AWARE.rank > EvidenceTier.ELF_ONLY.rank


class TestPolicyLookups:
    def test_policy_for_known_breaking(self) -> None:
        """policy_for returns the registered entry for a breaking kind (line 695)."""
        kind = next(iter(BREAKING_KINDS))
        entry = policy_for(kind)
        assert entry.default_verdict == Verdict.BREAKING
        assert entry.severity == "error"

    def test_policy_for_unknown_defaults_breaking(self) -> None:
        """Unknown kinds are treated as BREAKING (fail-safe, line 695)."""

        class _Fake:
            value = "totally-unknown-kind"

        entry = policy_for(_Fake())  # type: ignore[arg-type]
        assert entry.default_verdict == Verdict.BREAKING
        assert entry.severity == "error"

    def test_impact_for_returns_string(self) -> None:
        """impact_for returns a (possibly empty) string for every kind (line 700)."""
        for kind in ChangeKind:
            assert isinstance(impact_for(kind), str)

    def test_policy_registry_markdown(self) -> None:
        """policy_registry_markdown emits a row per ChangeKind (lines 705-715)."""
        md = policy_registry_markdown()
        assert "| ChangeKind | Default verdict | Severity | Doc slug |" in md
        # Header (2 lines) + one row per kind.
        assert md.count("\n") + 1 == len(ChangeKind) + 2
        sample = next(iter(ChangeKind))
        assert f"`{sample.value}`" in md


class TestPolicyKindSets:
    def test_sdk_vendor_downgrades_api_break(self) -> None:
        """sdk_vendor moves SDK_VENDOR_COMPAT_KINDS out of api_break (line 739)."""
        breaking, api_break, compatible, risk = policy_kind_sets("sdk_vendor")
        assert SDK_VENDOR_COMPAT_KINDS <= compatible
        assert SDK_VENDOR_COMPAT_KINDS.isdisjoint(api_break)
        assert breaking == frozenset(BREAKING_KINDS)

    def test_plugin_abi_downgrades_breaking(self) -> None:
        """plugin_abi moves PLUGIN_ABI_DOWNGRADED_KINDS to compatible (line 750)."""
        breaking, api_break, compatible, risk = policy_kind_sets("plugin_abi")
        assert PLUGIN_ABI_DOWNGRADED_KINDS <= compatible
        assert PLUGIN_ABI_DOWNGRADED_KINDS.isdisjoint(breaking)
        # plugin_abi folds risk kinds into breaking and empties the risk set.
        assert risk == frozenset()

    def test_unknown_policy_falls_back_to_strict(self) -> None:
        sets_unknown = policy_kind_sets("not-a-real-policy")
        sets_strict = policy_kind_sets("strict_abi")
        assert sets_unknown == sets_strict


class TestComputeVerdict:
    def test_no_changes_is_no_change(self) -> None:
        assert compute_verdict([]) == Verdict.NO_CHANGE

    def test_breaking_wins(self) -> None:
        kind = next(iter(BREAKING_KINDS))
        assert compute_verdict([_change(kind)]) == Verdict.BREAKING

    def test_api_break(self) -> None:
        kind = next(iter(API_BREAK_KINDS))
        assert compute_verdict([_change(kind)]) == Verdict.API_BREAK

    def test_compatible(self) -> None:
        kind = next(iter(COMPATIBLE_KINDS))
        assert compute_verdict([_change(kind)]) == Verdict.COMPATIBLE

    def test_risk_only_is_compatible_with_risk(self) -> None:
        kind = next(iter(RISK_KINDS))
        assert compute_verdict([_change(kind)]) == Verdict.COMPATIBLE_WITH_RISK


# ===========================================================================
# baseline.py
# ===========================================================================

from abicheck.baseline import (  # noqa: E402
    BaselineKey,
    FilesystemRegistry,
    _atomic_write,
    detect_platform_from_binary,
)
from abicheck.errors import ValidationError  # noqa: E402
from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402


def _sample_snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0.0",
        functions=[
            Function(
                name="foo",
                mangled="foo",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


class TestKeyDirEscape:
    def test_key_dir_escape_raises(self, tmp_path: Path) -> None:
        """_key_dir raises when the resolved path escapes root (lines 294-295)."""
        registry = FilesystemRegistry(tmp_path / "root")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        # Force relative_to to fail by patching the resolved path containment check.
        with patch.object(Path, "relative_to", side_effect=ValueError("escape")):
            with pytest.raises(ValidationError, match="escapes registry root"):
                registry._key_dir(key)


class TestListSkipsNonDirs:
    def test_list_skips_stray_files(self, tmp_path: Path) -> None:
        """list() skips non-directory entries at each level (367, 373, 377)."""
        root = tmp_path / "baselines"
        registry = FilesystemRegistry(root)
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, _sample_snapshot())
        # Stray files at the library, version and platform levels.
        (root / "stray_lib.txt").write_text("x")
        (root / "libfoo" / "stray_ver.txt").write_text("x")
        (root / "libfoo" / "1.0.0" / "stray_plat.txt").write_text("x")
        keys = registry.list()
        # Only the one real baseline survives the non-dir filtering.
        assert len(keys) == 1
        assert keys[0].library == "libfoo"

    def test_list_platform_without_snapshot(self, tmp_path: Path) -> None:
        """A platform dir lacking snapshot.json yields no key (line 380->387)."""
        root = tmp_path / "baselines"
        registry = FilesystemRegistry(root)
        # Create the directory structure but no snapshot.json at the platform level.
        (root / "libfoo" / "1.0.0" / "linux-x86_64").mkdir(parents=True)
        assert registry.list() == []


class TestDeleteParentCleanupError:
    def test_delete_handles_rmdir_oserror(self, tmp_path: Path) -> None:
        """delete() stops parent cleanup gracefully on OSError (lines 416-417)."""
        root = tmp_path / "baselines"
        registry = FilesystemRegistry(root)
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, _sample_snapshot())

        real_iterdir = Path.iterdir

        def _boom_iterdir(self):  # noqa: ANN001
            # Raise only while walking up empty parents during cleanup.
            if self.name in ("1.0.0", "libfoo"):
                raise OSError("cannot scan")
            return real_iterdir(self)

        with patch.object(Path, "iterdir", _boom_iterdir):
            assert registry.delete(key) is True
        # The leaf snapshot dir was removed even though parent cleanup aborted.
        assert registry.pull(key) is None


class TestAtomicWriteErrorCleanup:
    def test_atomic_write_cleans_temp_on_error(self, tmp_path: Path) -> None:
        """_atomic_write removes its temp file and re-raises on failure (435-440)."""
        target = tmp_path / "out.txt"
        before = set(tmp_path.iterdir())
        with patch("abicheck.baseline.os.replace", side_effect=RuntimeError("nope")):
            with pytest.raises(RuntimeError, match="nope"):
                _atomic_write(target, "content")
        after = set(tmp_path.iterdir())
        # No leftover .tmp files: the temp file was cleaned up in the handler.
        assert before == after


class TestDetectPlatformArch:
    def test_pe_arch_detection(self, tmp_path: Path) -> None:
        """PE branch resolves arch via a mocked pefile module (499-506, 514)."""
        binary = tmp_path / "foo.dll"
        binary.write_bytes(b"MZ" + b"\x00" * 200)

        class _FakeHeader:
            Machine = 0x8664  # IMAGE_FILE_MACHINE_AMD64

        class _FakePE:
            def __init__(self, *_a, **_k) -> None:
                self.FILE_HEADER = _FakeHeader()

            def close(self) -> None:
                pass

        import types

        with patch("abicheck.binary_utils.detect_binary_format", return_value="pe"):
            with patch.dict(sys.modules, {"pefile": types.SimpleNamespace(PE=_FakePE)}):
                result = detect_platform_from_binary(binary)
        assert result == "windows-x86_64"

    @pytest.mark.parametrize(
        "machine,expected",
        [
            (0x14C, "windows-x86"),
            (0xAA64, "windows-aarch64"),
            (0x1234, "windows-unknown"),
        ],
    )
    def test_pe_arch_variants(
        self, tmp_path: Path, machine: int, expected: str
    ) -> None:
        """PE x86 / aarch64 / fallback machine codes (lines 502-505)."""
        binary = tmp_path / "foo.dll"
        binary.write_bytes(b"MZ" + b"\x00" * 200)

        class _FakeHeader:
            Machine = machine

        class _FakePE:
            def __init__(self, *_a, **_k) -> None:
                self.FILE_HEADER = _FakeHeader()

            def close(self) -> None:
                pass

        import types

        with patch("abicheck.binary_utils.detect_binary_format", return_value="pe"):
            with patch.dict(sys.modules, {"pefile": types.SimpleNamespace(PE=_FakePE)}):
                result = detect_platform_from_binary(binary)
        assert result == expected

    def test_macho_arch_detection(self, tmp_path: Path) -> None:
        """Mach-O branch resolves arch via a mocked macholib (520-525, 533)."""
        binary = tmp_path / "libfoo.dylib"
        binary.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 64)

        class _Inner:
            cputype = 16777223  # CPU_TYPE_X86_64

        class _HeaderWrap:
            header = _Inner()

        class _FakeMachO:
            def __init__(self, *_a, **_k) -> None:
                self.headers = [_HeaderWrap()]

        import types

        with patch("abicheck.binary_utils.detect_binary_format", return_value="macho"):
            with patch.dict(
                sys.modules, {"macholib.MachO": types.SimpleNamespace(MachO=_FakeMachO)}
            ):
                result = detect_platform_from_binary(binary)
        assert result == "macos-x86_64"

    def test_unknown_format_default_arch(self, tmp_path: Path) -> None:
        """An unrecognised-but-detected format hits the trailing return (line 535)."""
        binary = tmp_path / "weird.bin"
        binary.write_bytes(b"\x00" * 32)
        with patch("abicheck.binary_utils.detect_binary_format", return_value="wasm"):
            result = detect_platform_from_binary(binary)
        assert result == "unknown-unknown"


# ===========================================================================
# bundle.py
# ===========================================================================

from abicheck.bundle import (  # noqa: E402
    BundleSnapshot,
    InstantiationManifest,
    ManifestEntry,
    _build_demangled_index,
    _compute_resolution_graph,
    _detect_provider_changed,
    _detect_soname_skew,
    _detect_version_drift,
    _looks_system_symbol,
    _match_entry,
    _strip_namespace_prefix,
    load_manifest,
)
from abicheck.checker_types import DiffResult  # noqa: E402
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol  # noqa: E402


def _bundle_meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
    export_versions: dict[str, str] | None = None,
) -> ElfMetadata:
    syms = [
        ElfSymbol(
            name=n,
            visibility="default",
            version=(export_versions or {}).get(n, ""),
        )
        for n in exports or []
    ]
    imps = [ElfImport(name=n) for n in imports or []]
    return ElfMetadata(
        soname=soname or "",
        needed=needed or [],
        symbols=syms,
        imports=imps,
    )


def _bundle(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"),
        libraries=libs,
        metadata=libraries,
        resolution=graph,
    )


def _bundle_diff(library: str, *changes, verdict=Verdict.BREAKING) -> DiffResult:
    return DiffResult(
        old_version="old",
        new_version="new",
        library=library,
        changes=list(changes),
        verdict=verdict,
    )


class TestManifestEntryDisplayName:
    def test_display_name_symbol(self) -> None:
        """ManifestEntry.display_name returns the literal symbol (line 298-299)."""
        entry = ManifestEntry(symbol="acme_version")
        assert entry.kind() == "symbol"
        assert entry.display_name() == "acme_version"

    def test_display_name_pattern(self) -> None:
        entry = ManifestEntry(pattern="acme::*")
        assert entry.kind() == "pattern"
        assert entry.display_name() == "acme::*"

    def test_display_name_template_expands(self) -> None:
        """Template entries expand their instantiations (lines 302-304, 323)."""
        entry = ManifestEntry(
            template="acme::ops",
            instantiations=({"T": "float"}, {"T": "double"}),
        )
        assert entry.kind() == "template"
        name = entry.display_name()
        assert "acme::ops<float>" in name
        assert "acme::ops<double>" in name

    def test_display_name_bare_template(self) -> None:
        entry = ManifestEntry(template="acme::ops")
        assert entry.display_name() == "acme::ops"

    def test_symbols_property_filters_literals(self) -> None:
        """InstantiationManifest.symbols returns only literal-symbol entries (323)."""
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(symbol="lit_a"),
                ManifestEntry(pattern="p::*"),
                ManifestEntry(symbol="lit_b"),
            )
        )
        assert manifest.symbols == frozenset({"lit_a", "lit_b"})


class TestManifestParsing:
    def test_parse_template_entry_roundtrip(self, tmp_path: Path) -> None:
        """load_manifest parses a template entry (exercises lines 385, 399)."""
        path = tmp_path / "manifest.json"
        path.write_text(
            '{"version": 1, "provides": ['
            '{"template": "acme::ops", "instantiations": ['
            '{"T": "float"}, {"T": "double"}], "library": "libcore.so.1",'
            ' "optional_provider": false}]}',
            encoding="utf-8",
        )
        manifest = load_manifest(path)
        assert len(manifest.entries) == 1
        entry = manifest.entries[0]
        assert entry.template == "acme::ops"
        assert entry.library == "libcore.so.1"
        assert entry.optional_provider is False
        assert len(entry.instantiations) == 2


class TestProviderMigration:
    def test_provider_change_detected(self) -> None:
        """Symbol removed in libA and added in libB -> BUNDLE_PROVIDER_CHANGED."""
        new = _bundle(
            {
                "liba.so": _bundle_meta(soname="liba.so.1", exports=[]),
                "libb.so": _bundle_meta(soname="libb.so.1", exports=["moved_sym"]),
            }
        )
        diff_by_library = {
            "liba.so": _bundle_diff(
                "liba.so",
                Change(
                    kind=ChangeKind.FUNC_REMOVED, symbol="moved_sym", description="r"
                ),
            ),
            "libb.so": _bundle_diff(
                "libb.so",
                Change(kind=ChangeKind.FUNC_ADDED, symbol="moved_sym", description="a"),
            ),
        }
        findings = _detect_provider_changed(new, diff_by_library)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_PROVIDER_CHANGED
        assert findings[0].old_value == "liba.so"
        assert findings[0].new_value == "libb.so"

    def test_provider_change_skipped_when_not_in_new(self) -> None:
        """No finding when the new provider does not actually export it (line 938)."""
        new = _bundle(
            {
                "liba.so": _bundle_meta(soname="liba.so.1", exports=[]),
                "libb.so": _bundle_meta(
                    soname="libb.so.1", exports=[]
                ),  # does NOT export
            }
        )
        diff_by_library = {
            "liba.so": _bundle_diff(
                "liba.so",
                Change(
                    kind=ChangeKind.FUNC_REMOVED, symbol="moved_sym", description="r"
                ),
            ),
            "libb.so": _bundle_diff(
                "libb.so",
                Change(kind=ChangeKind.FUNC_ADDED, symbol="moved_sym", description="a"),
            ),
        }
        assert _detect_provider_changed(new, diff_by_library) == []


class TestVersionDrift:
    def test_version_drift_detected(self) -> None:
        """Provider version change with consumers -> BUNDLE_INTRA_DEP_VERSION_DRIFT."""
        old = _bundle(
            {
                "libcore.so": _bundle_meta(
                    soname="libcore.so.1",
                    exports=["sym"],
                    export_versions={"sym": "V_1.0"},
                ),
                "libuser.so": _bundle_meta(
                    soname="libuser.so.1",
                    needed=["libcore.so.1"],
                    imports=["sym"],
                ),
            }
        )
        new = _bundle(
            {
                "libcore.so": _bundle_meta(
                    soname="libcore.so.1",
                    exports=["sym"],
                    export_versions={"sym": "V_2.0"},
                ),
                "libuser.so": _bundle_meta(
                    soname="libuser.so.1",
                    needed=["libcore.so.1"],
                    imports=["sym"],
                ),
            }
        )
        findings = _detect_version_drift(old, new)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_INTRA_DEP_VERSION_DRIFT
        assert findings[0].old_value == "V_1.0"
        assert findings[0].new_value == "V_2.0"

    def test_version_unchanged_no_finding(self) -> None:
        """Identical versions skip the drift branch (line 991)."""
        meta = {
            "libcore.so": _bundle_meta(
                soname="libcore.so.1",
                exports=["sym"],
                export_versions={"sym": "V_1.0"},
            ),
        }
        snap = _bundle(meta)
        assert _detect_version_drift(snap, snap) == []

    def test_version_drift_no_consumers_skipped(self) -> None:
        """Version drift with no importing siblings is skipped (lines 994-995)."""
        old = _bundle(
            {
                "libcore.so": _bundle_meta(
                    soname="libcore.so.1",
                    exports=["sym"],
                    export_versions={"sym": "V_1.0"},
                ),
            }
        )
        new = _bundle(
            {
                "libcore.so": _bundle_meta(
                    soname="libcore.so.1",
                    exports=["sym"],
                    export_versions={"sym": "V_2.0"},
                ),
            }
        )
        assert _detect_version_drift(old, new) == []


class TestSonameSkewSnapshots:
    def test_no_cohorts_returns_empty(self) -> None:
        """Empty cohort list disables the skew check (lines 1079-1080)."""
        snap = _bundle({"liba.so.1": _bundle_meta(soname="liba.so.1")})
        assert _detect_soname_skew(snap, snap, None) == []
        assert _detect_soname_skew(snap, snap, [" "]) == []

    def test_unversioned_members_dropped(self) -> None:
        """Libraries with no derivable major produce no members (1090-1092, 1101)."""
        # Unversioned filename + unversioned soname -> no major -> dropped.
        snap = _bundle({"libfoo.so": _bundle_meta(soname="libfoo.so")})
        result = _detect_soname_skew(snap, snap, ["libfoo"])
        assert result == []


class TestDemangledIndex:
    def test_skips_hidden_visibility(self) -> None:
        """_build_demangled_index keeps only default/protected exports (line 1150)."""
        meta = ElfMetadata(
            soname="libx.so.1",
            symbols=[
                ElfSymbol(name="visible_sym", visibility="default"),
                ElfSymbol(name="hidden_sym", visibility="hidden"),
            ],
        )
        snap = _bundle({"libx.so": meta})
        index = _build_demangled_index(snap)
        names = {name for name, _lib in index}
        assert "visible_sym" in names
        assert "hidden_sym" not in names


class TestMatchEntry:
    def test_symbol_entry_no_index_needed(self) -> None:
        """A pure-symbol entry never builds the demangled index (line 1235-1236 skip)."""
        snap = _bundle({"libx.so": _bundle_meta(soname="libx.so.1", exports=["sym_a"])})
        entry = ManifestEntry(symbol="sym_a")
        results = _match_entry(entry, snap)
        assert len(results) == 1
        target, kind, matched, providers = results[0]
        assert target == "sym_a"
        assert kind == "symbol"
        assert matched == ["sym_a"]
        assert any(p.library == "libx.so" for p in providers)

    def test_pattern_entry_builds_index(self) -> None:
        """A pattern entry forces index construction (line 1236)."""
        meta = ElfMetadata(
            soname="libx.so.1",
            symbols=[ElfSymbol(name="acme_train_v1", visibility="default")],
        )
        snap = _bundle({"libx.so": meta})
        entry = ManifestEntry(pattern="acme_train_*")
        results = _match_entry(entry, snap)
        assert len(results) == 1
        _t, kind, matched, providers = results[0]
        assert kind == "pattern"
        assert matched == ["acme_train_v1"]
        assert providers and providers[0].library == "libx.so"


class TestLooksSystemSymbol:
    def test_std_mangled_is_system(self) -> None:
        """_ZNSt / _ZSt prefixes are flagged system (lines 1367-1368)."""
        assert _looks_system_symbol("_ZNSt6vectorIiEC1Ev") is True
        assert _looks_system_symbol("_ZSt4cout") is True

    def test_const_std_method_is_system(self) -> None:
        """_ZNK with St in prefix is system (lines 1369-1370)."""
        assert _looks_system_symbol("_ZNKSt6vector4sizeEv") is True

    def test_non_system_symbol(self) -> None:
        assert _looks_system_symbol("acme_do_thing") is False


class TestStripNamespacePrefix:
    def test_strips_qualified(self) -> None:
        """Qualified names lose their namespace prefix (lines 1395-1396)."""
        assert _strip_namespace_prefix("acme::lib::Widget") == "Widget"

    def test_unqualified_unchanged(self) -> None:
        assert _strip_namespace_prefix("Widget") == "Widget"


# ===========================================================================
# mcp_server.py — pure helpers (no real mcp dependency needed)
# ===========================================================================


class TestMcpEnvInt:
    def test_valid_int(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_TEST_ENVINT", "42")
        assert _env_int("ABICHECK_TEST_ENVINT", "7") == 42

    def test_default_used(self, monkeypatch) -> None:
        monkeypatch.delenv("ABICHECK_TEST_ENVINT", raising=False)
        assert _env_int("ABICHECK_TEST_ENVINT", "9") == 9

    def test_invalid_raises(self, monkeypatch) -> None:
        """Non-integer env value raises a clear ValueError (lines 82-83)."""
        monkeypatch.setenv("ABICHECK_TEST_ENVINT", "not-an-int")
        with pytest.raises(ValueError, match="not a valid integer"):
            _env_int("ABICHECK_TEST_ENVINT", "5")


class TestMcpCheckFileSize:
    def test_missing_file_returns(self, tmp_path: Path) -> None:
        """Missing file is a no-op (lines 102-103)."""
        _check_file_size(tmp_path / "nope.so")  # must not raise

    def test_oversize_raises(self, tmp_path: Path) -> None:
        """Oversized file raises (line 107)."""
        p = tmp_path / "big.so"
        p.write_bytes(b"\x00" * 16)
        with patch.object(_ms, "MCP_MAX_FILE_SIZE", 4):
            with pytest.raises(ValueError, match="exceeds limit"):
                _check_file_size(p, label="input")

    def test_oserror_raises(self) -> None:
        """A stat OSError surfaces as a ValueError (lines 104-105)."""
        with patch.object(_ms.Path, "stat", side_effect=OSError("io")):
            with pytest.raises(ValueError, match="Cannot check"):
                _check_file_size(Path("/whatever.so"), label="input")


class TestMcpAuditLog:
    def test_structured_logging(self, caplog) -> None:
        """Structured logging emits a JSON record (line 130)."""
        import json as _json

        with patch.object(_ms, "_structured_logging", True):
            with caplog.at_level("INFO", logger="abicheck.mcp"):
                _audit_log("t", {"a": "b"}, 1.5, "ok", verdict="BREAKING")
        # Last record is valid JSON carrying our fields.
        payload = _json.loads(caplog.records[-1].getMessage())
        assert payload["tool"] == "t"
        assert payload["verdict"] == "BREAKING"

    def test_text_logging(self, caplog) -> None:
        with patch.object(_ms, "_structured_logging", False):
            with caplog.at_level("INFO", logger="abicheck.mcp"):
                _audit_log("t", {"a": "b"}, 1.5, "ok")
        assert "tool=t" in caplog.text
        assert "status=ok" in caplog.text


class TestMcpImpactCategory:
    def test_breaking(self) -> None:
        kind = next(iter(BREAKING_KINDS))
        assert _impact_category(kind) == "breaking"

    def test_api_break(self) -> None:
        kind = next(iter(API_BREAK_KINDS))
        assert _impact_category(kind) == "api_break"

    def test_risk(self) -> None:
        kind = next(iter(RISK_KINDS))
        assert _impact_category(kind) == "risk"

    def test_compatible(self) -> None:
        kind = next(iter(COMPATIBLE_KINDS))
        assert _impact_category(kind) == "compatible"


class TestMcpResolveInputText:
    def test_json_snapshot_loaded(self, tmp_path: Path) -> None:
        """A JSON snapshot input is loaded via load_snapshot (lines 390-391)."""
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        path = tmp_path / "snap.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        with patch.object(_ms, "_detect_binary_format", return_value=None):
            result = _resolve_input(path, [], [], "v", "c++")
        assert result.library == "libfoo.so"

    def test_archive_rejected(self, tmp_path: Path) -> None:
        """A static archive input is rejected with guidance (lines 397-404)."""
        from abicheck.errors import AbicheckError

        path = tmp_path / "lib.a"
        path.write_bytes(b"!<arch>\nsome members")
        with patch.object(_ms, "_detect_binary_format", return_value=None):
            with patch("abicheck.binary_utils.detect_archive", return_value=True):
                with pytest.raises(AbicheckError, match="archive"):
                    _resolve_input(path, [], [], "v", "c++")

    def test_unknown_format_rejected(self, tmp_path: Path) -> None:
        """Unknown text content is rejected (lines 406-409)."""
        from abicheck.errors import AbicheckError

        path = tmp_path / "mystery.dat"
        path.write_text("plain text that is neither json nor perl", encoding="utf-8")
        with patch.object(_ms, "_detect_binary_format", return_value=None):
            with patch("abicheck.binary_utils.detect_archive", return_value=False):
                with pytest.raises(AbicheckError, match="Cannot detect input format"):
                    _resolve_input(path, [], [], "v", "c++")


class TestMcpSnapshotSummary:
    def test_summary_counts(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="2.0",
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        summary = _snapshot_summary(snap)
        assert summary["library"] == "libfoo.so"
        assert summary["functions"] == 1
        assert summary["variables"] == 0


# ---------------------------------------------------------------------------
# mcp_server.py — tool entry points (timeout / not-found branches)
# ---------------------------------------------------------------------------

import concurrent.futures as _futures  # noqa: E402
import json as _json  # noqa: E402

# abi_compare / abi_dump are already imported at the top of this module via
# _import_mcp_server_symbols().


class _TimeoutFuture:
    """A future stub whose .result() always times out."""

    def result(self, timeout=None):  # noqa: ANN001, ANN201
        raise _futures.TimeoutError()


class _TimeoutPool:
    """A ThreadPoolExecutor stub that returns a timing-out future."""

    def __init__(self, *_a, **_k) -> None:
        pass

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_a) -> None:
        return None

    def submit(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
        return _TimeoutFuture()


class TestMcpDumpTool:
    def test_missing_library_reports_error(self, tmp_path: Path) -> None:
        out = _json.loads(abi_dump(str(tmp_path / "nope.so")))
        assert out["status"] == "error"
        assert "not found" in out["error"]

    def test_dump_timeout(self, tmp_path: Path) -> None:
        """abi_dump returns a timeout error when the worker times out (535-538)."""
        lib = tmp_path / "libfoo.so"
        lib.write_bytes(b"\x7fELF" + b"\x00" * 60)
        with patch.object(_ms._futures, "ThreadPoolExecutor", _TimeoutPool):
            out = _json.loads(abi_dump(str(lib)))
        assert out["status"] == "error"
        assert "timed out" in out["error"]

    def test_dump_success_inline(self, tmp_path: Path) -> None:
        """A JSON snapshot input flows through abi_dump successfully (553-558)."""
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        path = tmp_path / "snap.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        out = _json.loads(abi_dump(str(path)))
        assert out["status"] == "ok"
        assert out["summary"]["library"] == "libfoo.so"


class TestMcpCompareTool:
    def test_missing_input_reports_error(self, tmp_path: Path) -> None:
        existing = tmp_path / "snap.json"
        existing.write_text("{}", encoding="utf-8")
        out = _json.loads(abi_compare(str(tmp_path / "nope.so"), str(existing)))
        assert out["status"] == "error"
        assert "not found" in out["error"].lower()

    def test_unknown_policy_rejected(self, tmp_path: Path) -> None:
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        a.write_text("{}", encoding="utf-8")
        b.write_text("{}", encoding="utf-8")
        out = _json.loads(abi_compare(str(a), str(b), policy="bogus-policy"))
        assert out["status"] == "error"
        assert "Unknown policy" in out["error"]

    def test_compare_timeout(self, tmp_path: Path) -> None:
        """abi_compare reports a timeout when comparison times out (676-679)."""
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        a.write_text(snapshot_to_json(snap), encoding="utf-8")
        b.write_text(snapshot_to_json(snap), encoding="utf-8")
        with patch.object(_ms._futures, "ThreadPoolExecutor", _TimeoutPool):
            out = _json.loads(abi_compare(str(a), str(b)))
        assert out["status"] == "error"
        assert "timed out" in out["error"]

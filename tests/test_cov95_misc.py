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

"""Coverage-raising unit tests for bundle, checker_policy, and classify
modules.

Pure-Python only — no external tools. Uses tmp_path and unittest.mock for
I/O-heavy paths. Every test asserts a meaningful invariant.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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

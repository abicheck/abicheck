"""Tests for the compare-release command (multi-binary directory comparison)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from abicheck.cli import (
    _build_match_map,
    _canonical_library_key,
    _is_supported_compare_input,
    _version_sort_key,
    main,
)
from abicheck.cli_compare_release import (
    _discover_include_roots,
    _extract_if_package,
    _format_release_json,
    _prepare_compare_release_inputs,
)
from abicheck.cli_helpers_compare import strip_vendor_hash
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.package import ExtractResult
from abicheck.serialization import snapshot_to_json

# ── helpers ──────────────────────────────────────────────────────────────────

def _snap(
    version: str = "1.0",
    funcs: list[Function] | None = None,
    library: str = "libfoo.so",
) -> AbiSnapshot:
    if funcs is None:
        funcs = [Function(name="foo", mangled="_Z3foov", return_type="int",
                          visibility=Visibility.PUBLIC)]
    return AbiSnapshot(library=library, version=version, functions=funcs, from_headers=True)


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    """Remove a function — always produces BREAKING verdict."""
    old = _snap("1.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
        Function(name="bar", mangled="_Z3barv", return_type="void", visibility=Visibility.PUBLIC),
    ], library=lib)
    new = _snap("2.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
    ], library=lib)
    return old, new


def _api_break_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    """Signature change that requires recompilation but is not a binary break."""
    old = _snap("1.0", [
        Function(
            name="foo",
            mangled="_Z3foov",
            return_type="int",
            params=[Param(name="x", type="int", default="0")],
            visibility=Visibility.PUBLIC,
        ),
    ], library=lib)
    new = _snap("2.0", [
        Function(
            name="foo",
            mangled="_Z3foov",
            return_type="int",
            params=[Param(name="x", type="int")],
            visibility=Visibility.PUBLIC,
        ),
    ], library=lib)
    return old, new


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


# ── canonical key helpers ─────────────────────────────────────────────────────

class TestCanonicalLibraryKey:
    def test_so_versioned(self, tmp_path: Path) -> None:
        assert _canonical_library_key(Path("libfoo.so.1.2")) == "libfoo.so"

    def test_so_no_version(self, tmp_path: Path) -> None:
        assert _canonical_library_key(Path("libfoo.so")) == "libfoo.so"

    def test_json_snapshot(self, tmp_path: Path) -> None:
        # No .so in name — returns as-is
        assert _canonical_library_key(Path("libfoo.json")) == "libfoo.json"

    def test_so_json_snapshot(self, tmp_path: Path) -> None:
        # .so in name + .json suffix
        assert _canonical_library_key(Path("libfoo.so.1.2.json")) == "libfoo.so"

    def test_so_in_stem_not_confused(self, tmp_path: Path) -> None:
        # "libsome.so" — ".so" is real extension, not inside stem
        assert _canonical_library_key(Path("libsome.so")) == "libsome.so"

    def test_dll(self, tmp_path: Path) -> None:
        assert _canonical_library_key(Path("libfoo.dll")) == "libfoo.dll"


class TestStripVendorHash:
    """G9: auditwheel/delocate rewrite vendored libs to lib<name>-<hash>.so.<ver>,
    with a content-derived hash that changes every rebuild. strip_vendor_hash
    normalizes that away so compare-release can pair the same dependency across
    releases instead of reporting every one as removed+added noise."""

    def test_auditwheel_hex_before_so_stripped(self) -> None:
        assert strip_vendor_hash("libpng16-a746ad4a.so.16.43.0") == "libpng16.so.16.43.0"

    def test_different_hash_same_stem(self) -> None:
        # The whole point: two different rebuild hashes must normalize identically.
        a = strip_vendor_hash("libpng16-a746ad4a.so.16.43.0")
        b = strip_vendor_hash("libpng16-b8f31c2e.so.16.56.0")
        assert a == "libpng16.so.16.43.0"
        assert b == "libpng16.so.16.56.0"

    def test_16char_hash_stripped(self) -> None:
        assert strip_vendor_hash("libsodium-1234567890abcdef.so.23.3.0") == "libsodium.so.23.3.0"

    def test_purely_decimal_suffix_untouched(self) -> None:
        # A purely-decimal 6-16-digit hyphenated suffix is a plausible real
        # embedded build/version number (e.g. libfoo-100200.so.1), not a
        # content hash — real auditwheel/delocate hashes are hex digest
        # fragments and essentially never come out purely decimal. Stripping
        # this would collapse two libraries with genuinely different version
        # suffixes to the same key, silently hiding a real SONAME/dependency
        # change (self-review finding, was a false negative before the
        # at-least-one-hex-letter lookahead was added).
        assert strip_vendor_hash("libfoo-100200.so.1") == "libfoo-100200.so.1"
        different = strip_vendor_hash("libfoo-100300.so.1")
        assert different == "libfoo-100300.so.1"
        assert different != strip_vendor_hash("libfoo-100200.so.1")

    def test_no_hyphen_untouched(self) -> None:
        # Real-world names with no hyphen at all — nothing to strip.
        assert strip_vendor_hash("libwebpdemux.so.2.0.14") == "libwebpdemux.so.2.0.14"
        assert strip_vendor_hash("libbrotlicommon.so.1") == "libbrotlicommon.so.1"

    def test_short_hex_lookalike_untouched(self) -> None:
        # "cafe" is valid hex but below the 6-char minimum — a real short
        # hyphenated suffix must not be mistaken for a vendor hash.
        assert strip_vendor_hash("libfoo-cafe.so") == "libfoo-cafe.so"

    def test_non_hex_suffix_untouched(self) -> None:
        # "utils" is not a hex string — an ordinary hyphenated name.
        assert strip_vendor_hash("libfoo-utils.so") == "libfoo-utils.so"

    def test_canonical_key_pairs_across_hash_rebuild(self) -> None:
        # The integration point: _canonical_library_key must collapse both
        # rebuild hashes of the same vendored dependency to one key.
        old_key = _canonical_library_key(Path("libpng16-a746ad4a.so.16.43.0"))
        new_key = _canonical_library_key(Path("libpng16-b8f31c2e.so.16.56.0"))
        assert old_key == new_key == "libpng16.so"


class TestVersionSortKey:
    def test_1_9_vs_1_10(self) -> None:
        k9 = _version_sort_key(Path("libfoo.so.1.9"), "libfoo.so")
        k10 = _version_sort_key(Path("libfoo.so.1.10"), "libfoo.so")
        assert k9 < k10, "1.10 should sort after 1.9"

    def test_1_2_vs_1_3(self) -> None:
        k2 = _version_sort_key(Path("libfoo.so.1.2"), "libfoo.so")
        k3 = _version_sort_key(Path("libfoo.so.1.3"), "libfoo.so")
        assert k2 < k3

    def test_vendor_hash_does_not_perturb_version_order(self) -> None:
        # Codex (PR #551): an auditwheel/delocate content hash embedded in the
        # filename must not out-rank the real SONAME version tokens — the
        # hex hash's own digits/letters would otherwise corrupt the ordering.
        # Uses hex-letter-bearing hashes (real auditwheel/delocate hashes are
        # hex digest fragments, never purely decimal) so strip_vendor_hash's
        # at-least-one-hex-letter requirement (self-review finding: a purely
        # decimal 6-16-digit run is a plausible real version/build number,
        # not a hash) still recognizes both as vendor hashes.
        older = _version_sort_key(Path("libfoo-a00000.so.1"), "libfoo.so")
        newer = _version_sort_key(Path("libfoo-ffffff.so.2"), "libfoo.so")
        assert older < newer, "hash content must not outrank the .so version"


class TestBuildMatchMapVendorHash:
    def test_picks_higher_soname_version_despite_hash(self, tmp_path: Path) -> None:
        # Same underlying bug as TestVersionSortKey.
        # test_vendor_hash_does_not_perturb_version_order, exercised at the
        # _build_match_map level that compare-release actually calls.
        old = tmp_path / "libfoo-a00000.so.1"
        new = tmp_path / "libfoo-ffffff.so.2"
        old.write_bytes(b"\x7fELF")
        new.write_bytes(b"\x7fELF")
        mapping, warnings = _build_match_map([old, new])
        assert mapping["libfoo.so"] == new
        assert warnings


# ── file vs file ─────────────────────────────────────────────────────────────

class TestFileVsFile:
    def test_no_change(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "libfoo.json", snap)
        new_f = _write_snap(tmp_path / "libfoo_new.json", snap)
        code, out = _invoke("compare", str(old_f), str(new_f))
        assert code == 0
        assert "NO_CHANGE" in out

    def test_breaking(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "libfoo.json", old)
        new_f = _write_snap(tmp_path / "libfoo_new.json", new)
        code, out = _invoke("compare", str(old_f), str(new_f))
        assert code == 4
        assert "BREAKING" in out

    def test_api_break(self, tmp_path: Path) -> None:
        old, new = _api_break_pair()
        old_f = _write_snap(tmp_path / "libfoo.json", old)
        new_f = _write_snap(tmp_path / "libfoo_new.json", new)
        code, out = _invoke("compare", str(old_f), str(new_f))
        assert code == 2

    def test_json_output(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "libfoo.json", snap)
        new_f = _write_snap(tmp_path / "libfoo2.json", snap)
        code, out = _invoke("compare", str(old_f), str(new_f), "--format", "json")
        assert code == 0
        data = json.loads(out)
        # Two *file* operands are a single-pair compare (no release fan-out), so
        # the JSON is the single-pair report — not the release `libraries` summary
        # the removed compare-release produced for file inputs. Directory operands
        # still produce that summary (covered by the dir-vs-dir tests below).
        assert data["verdict"] == "NO_CHANGE"
        assert "libraries" not in data


# ── dir vs dir ───────────────────────────────────────────────────────────────

class TestDirVsDir:
    def test_matching_by_name_no_change(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 0
        assert "NO_CHANGE" in out

    def test_matching_multi_library_all_ok(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json", "libbaz.json"):
            snap = _snap()
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 0
        assert "NO_CHANGE" in out

    def test_breaking_in_one_library(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 4
        assert "BREAKING" in out

    def test_api_break_in_one_library(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _api_break_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 2

    def test_json_output_multi(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json"):
            snap = _snap()
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0
        data = json.loads(out)
        assert data["verdict"] == "NO_CHANGE"
        assert len(data["libraries"]) == 2

    def test_json_output_embeds_findings_not_just_counts(self, tmp_path: Path) -> None:
        """A breaking library entry must embed which symbols broke, not just
        a bare count — verified defect: release JSON was count-centric
        (breaking/source_breaks/risk_changes as ints) with no way to identify
        the actual findings without a separate `compare` run or
        `--output-dir` (mirroring the `scan --baseline` / stack-check fix)."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old, new = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code != 0
        data = json.loads(out)
        lib = data["libraries"][0]
        assert lib["breaking"] == 1
        assert "findings" in lib
        assert lib["findings"][0]["symbol"] == "_Z3barv"
        assert lib["findings"][0]["bucket"] == "breaking"
        assert "findings_truncated" not in lib
        assert "_diff_result" not in lib

    def test_breaking_overrides_api_break(self, tmp_path: Path) -> None:
        """Aggregate verdict is BREAKING even when another lib has API_BREAK."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        old_bar, new_bar = _api_break_pair("libbar.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", old_bar)
        _write_snap(new_dir / "libbar.json", new_bar)
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 4
        assert "BREAKING" in out

    def test_fully_disjoint_dirs_warns_and_exits_0(self, tmp_path: Path) -> None:
        """Dirs with no matching lib names: warn, empty libraries list, exit 0."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 0
        assert "no matching" in out.lower() or "warning" in out.lower()


# ── unmatched / missing ───────────────────────────────────────────────────────

class TestUnmatched:
    def test_removed_library_no_flag(self, tmp_path: Path) -> None:
        """Removed library warns but does not fail by default."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 0

    def test_removed_library_with_flag(self, tmp_path: Path) -> None:
        """--fail-on-removed-library exits 8 when library disappears."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, _ = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--fail-on-removed-library",
        )
        assert code == 8

    def test_removed_and_breaking_exits_4_not_8(self, tmp_path: Path) -> None:
        """BREAKING (4) takes priority over removed-library (8)."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libremoved.json", _snap())  # removed
        code, _ = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--fail-on-removed-library",
        )
        assert code == 4

    def test_added_library_ok(self, tmp_path: Path) -> None:
        """New library in new_dir not in old_dir is fine."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir))
        assert code == 0

    def test_unmatched_reported_in_json(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libremoved.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libadded.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0
        data = json.loads(out)
        assert isinstance(data["unmatched_old"], list)
        assert "libremoved.json" in data["unmatched_old"]
        assert isinstance(data["unmatched_new"], list)
        assert "libadded.json" in data["unmatched_new"]


# ── vendored wheel hash pairing (G9) ────────────────────────────────────────

class TestVendoredWheelPairing:
    """G9 workflow proof: two synthetic 'wheels' with auditwheel/delocate-style
    hashed vendored libraries must be paired by unhashed stem — not reported
    as removed+added noise every rebuild — while a real ABI break in the
    paired dependency (the pyzmq libsodium-style regression anchor) still
    surfaces rather than being absorbed by the normalization."""

    def test_hashed_vendored_lib_pairs_across_rebuild(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        snap = _snap(library="libpng16.so.16")
        # Same dependency, different auditwheel rebuild hash each side.
        _write_snap(old_dir / "libpng16-a746ad4a.so.16.43.0.json", snap)
        _write_snap(new_dir / "libpng16-b8f31c2e.so.16.56.0.json", snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0
        data = json.loads(out)
        # Paired, not phantom removed+added.
        assert data["unmatched_old"] == []
        assert data["unmatched_new"] == []
        assert len(data["libraries"]) == 1

    def test_real_break_in_hashed_vendored_lib_still_surfaces(self, tmp_path: Path) -> None:
        """Regression anchor (pyzmq libsodium 23->26-shaped case): a real ABI
        break in a hash-paired vendored library must not be absorbed by the
        stem normalization — pairing only changes matching, never the diff."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_lib, new_lib = _breaking_pair("libsodium.so.26")
        _write_snap(old_dir / "libsodium-1234567890abcdef.so.23.3.0.json", old_lib)
        _write_snap(new_dir / "libsodium-fedcba0987654321.so.26.1.0.json", new_lib)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 4
        data = json.loads(out)
        assert data["verdict"] == "BREAKING"
        # Still paired (matched), not reported as removed+added.
        assert data["unmatched_old"] == []
        assert data["unmatched_new"] == []

    def test_non_vendored_names_unaffected(self, tmp_path: Path) -> None:
        """Ordinary (non-hashed) library names must not be spuriously paired
        or otherwise affected by the vendor-hash normalization."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libwebpdemux.so.2.0.14.json", _snap(library="libwebpdemux.so.2"))
        _write_snap(new_dir / "libwebpdemux.so.2.0.14.json", _snap(library="libwebpdemux.so.2"))
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0
        data = json.loads(out)
        assert data["unmatched_old"] == []
        assert data["unmatched_new"] == []


# ── output-dir ────────────────────────────────────────────────────────────────

class TestOutputDir:
    def test_per_library_reports_written(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        out_dir = tmp_path / "reports"
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, _ = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--output-dir", str(out_dir),
        )
        assert code == 0
        assert (out_dir / "libfoo.json").exists()
        assert (out_dir / "summary.json").exists()

    def test_summary_json_structure(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        out_dir = tmp_path / "reports"
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, _ = _invoke("compare", str(old_dir), str(new_dir), "--output-dir", str(out_dir))
        assert code == 0
        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary["verdict"] == "NO_CHANGE"
        assert len(summary["libraries"]) == 1


def _rec(name: str, size: int) -> RecordType:
    return RecordType(name=name, kind="struct", size_bits=size,
                      fields=[TypeField(name="x", type="int")])


class TestCompareReleaseScopeAndChangedLibraries:
    """compare-release: changed_libraries + public-header scoping rollup (#235)."""

    def test_changed_libraries_lists_only_changed(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        # libfoo breaks; libbar is identical (NO_CHANGE).
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libbar.json", _snap(library="libbar.so"))
        _write_snap(new_dir / "libbar.json", _snap(library="libbar.so"))
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 4
        data = json.loads(out)
        assert data["changed_libraries"] == ["libfoo.json"]

    def test_scope_block_resolved_filters_internal(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        # Public api_call(Config*) -> int; InternalCache is private (unreachable).
        # New side adds a public function and shrinks InternalCache: the private
        # break is filtered, the public addition is reported.
        pub_old = Function(name="api_call", mangled="api_call", return_type="int",
                           params=[Param(name="c", type="Config *")], visibility=Visibility.PUBLIC)
        pub_new = Function(name="new_api", mangled="new_api", return_type="int",
                           visibility=Visibility.PUBLIC)
        old = AbiSnapshot(library="libfoo.so", version="1", functions=[pub_old],
                          types=[_rec("Config", 32), _rec("InternalCache", 64)])
        new = AbiSnapshot(library="libfoo.so", version="2", functions=[pub_old, pub_new],
                          types=[_rec("Config", 32), _rec("InternalCache", 128)])
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        code, out = _invoke("compare", str(old_dir), str(new_dir),
                            "--scope-public-headers", "--format", "json")
        data = json.loads(out)
        assert data["scope"]["public_headers_applied"] is True
        assert data["scope"]["manual_review_required"] is False
        assert data["scope"]["filtered_internal_changes"] >= 1
        assert data["scope"]["public_additions"] >= 1

    def test_scope_fallback_flags_manual_review(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        # No public symbols -> surface unresolvable -> fall back to full export
        # table and flag manual review (issue #235's "don't overclaim" half).
        old = AbiSnapshot(library="libfoo.so", version="1", functions=[],
                          types=[_rec("InternalCache", 64)])
        new = AbiSnapshot(library="libfoo.so", version="2", functions=[],
                          types=[_rec("InternalCache", 128)])
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        code, out = _invoke("compare", str(old_dir), str(new_dir),
                            "--scope-public-headers", "--format", "json")
        data = json.loads(out)
        assert data["scope"]["manual_review_required"] is True
        # The break is kept (fallback), so the library still shows as changed.
        assert "libfoo.json" in data["changed_libraries"]


# ── version-aware name matching ───────────────────────────────────────────────

class TestMixedInputs:
    def test_so_versioned_name_matching(self, tmp_path: Path) -> None:
        """libfoo.so.1.2 in old should match libfoo.so.1.3 in new via stem."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.so.1.2.json", snap)
        _write_snap(new_dir / "libfoo.so.1.3.json", snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0
        data = json.loads(out)
        assert len(data["libraries"]) == 1
        assert data["verdict"] == "NO_CHANGE"

    def test_version_aware_picks_1_10_over_1_9(self, tmp_path: Path) -> None:
        """Version-aware sort must pick 1.10 as latest, not 1.9."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        # Two old candidates for same stem — 1.10 is newer
        snap = _snap()
        _write_snap(old_dir / "libfoo.so.1.9.json", snap)
        _write_snap(old_dir / "libfoo.so.1.10.json", snap)
        _write_snap(new_dir / "libfoo.so.2.0.json", snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0
        data = json.loads(out)
        # Only 1 comparison, and warnings should mention 1.10 as selected
        assert len(data["libraries"]) == 1
        assert any("1.10" in w for w in data.get("warnings", []))


class TestFilterOutNonABIFiles:
    """Regression tests for false-positive detection of non‑ABI files."""

    def test_ignore_json_without_library_key(self, tmp_path: Path) -> None:
        """JSON files without "library" field should be ignored, not cause ERROR."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        # Real ABI snapshot (accepted)
        snap = _snap()
        _write_snap(old_dir / "libfoo.so.json", snap)
        _write_snap(new_dir / "libfoo.so.json", snap)

        # False‑positive JSON files (should be ignored)
        (old_dir / "auditwheel.cdx.json").write_text(
            '{"bomFormat": "CycloneDX", "specVersion": "1.4", "metadata": {"component": {"type": "library"}}, "components": []}'
        )
        (new_dir / "auditwheel.cdx.json").write_text(
            '{"bomFormat": "CycloneDX", "specVersion": "1.4", "metadata": {"component": {"type": "library"}}, "components": []}'
        )
        (old_dir / "studentized_range_mpmath_ref.json").write_text(
            '{"data": [[1, 2, 3], [4, 5, 6]]}'
        )
        (new_dir / "studentized_range_mpmath_ref.json").write_text(
            '{"data": [[1, 2, 3], [4, 5, 6]]}'
        )
        # Template file that starts with '{' (Jinja, etc.)
        (old_dir / "html.tpl").write_text("{% extends 'base.tpl' %}\n{% block content %}\n...\n{% endblock %}")
        (new_dir / "html.tpl").write_text("{% extends 'base.tpl' %}\n{% block content %}\n...\n{% endblock %}")

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0, f"Should pass (only one real library). Output: {out}"
        data = json.loads(out)
        # Only the real ABI snapshot is compared
        assert len(data["libraries"]) == 1
        assert data["libraries"][0]["library"] == "libfoo.so.json"
        assert data["libraries"][0]["verdict"] == "NO_CHANGE"
        # No ERROR verdict from the incidental JSON/template files
        assert not any(lib.get("verdict") == "ERROR" for lib in data["libraries"])

    def test_ignore_parquet_and_other_non_so_extensions(self, tmp_path: Path) -> None:
        """Files like *.parquet, *.csv, etc. should not be mistaken for .so candidates."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        # Real library (accepted)
        snap = _snap()
        _write_snap(old_dir / "libfoo.so.json", snap)
        _write_snap(new_dir / "libfoo.so.json", snap)

        # Files that contain the substring "so" but are not .so libraries
        (old_dir / "v0.7.1.some-named-index.parquet").write_bytes(b"PAR1" + b"fake data" * 100)
        (new_dir / "v0.7.1.some-named-index.parquet").write_bytes(b"PAR1" + b"fake data" * 100)
        (old_dir / "solution.json").write_text('{"answer": 42}')
        (new_dir / "solution.json").write_text('{"answer": 42}')
        (old_dir / "something.dll.txt").write_text("Not a DLL")
        (new_dir / "something.dll.txt").write_text("Not a DLL")

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0, f"Should pass (only one real library). Output: {out}"
        data = json.loads(out)
        # Only the real ABI snapshot is compared
        assert len(data["libraries"]) == 1
        assert data["libraries"][0]["library"] == "libfoo.so.json"
        assert not any(lib.get("verdict") == "ERROR" for lib in data["libraries"])

    def test_accept_real_abi_snapshots(self, tmp_path: Path) -> None:
        """Legitimate ABI snapshots (JSON with "library" key) should still be accepted."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        # ABI snapshot with "library" field
        snap1 = _snap("1.0", library="libfoo.so")
        snap2 = _snap("2.0", library="libfoo.so")
        _write_snap(old_dir / "libfoo.json", snap1)
        _write_snap(new_dir / "libfoo.json", snap2)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--format", "json")
        assert code == 0, f"Should compare the two snapshots. Output: {out}"
        data = json.loads(out)
        assert len(data["libraries"]) == 1
        assert data["libraries"][0]["library"] == "libfoo.json"
        assert data["libraries"][0]["verdict"] == "NO_CHANGE"

    def test_pyd_extension_accepted(self, tmp_path: Path) -> None:
        """Python Windows extension .pyd (a PE DLL) should be accepted."""
        pyd = tmp_path / "module.pyd"
        pyd.write_bytes(b"not-a-real-pe")
        assert _is_supported_compare_input(pyd)


# ── _extract_if_package unit tests ───────────────────────────────────────────

def _make_mock_extractor(lib_dir: Path, debug_dir: Path | None = None, header_dir: Path | None = None) -> MagicMock:
    """Return a mock PackageExtractor whose extract() returns the given paths."""
    result = ExtractResult(lib_dir=lib_dir, debug_dir=debug_dir, header_dir=header_dir)
    extractor = MagicMock()
    extractor.extract.return_value = result
    return extractor


class TestExtractIfPackage:
    """Unit tests for _extract_if_package covering the directory-input bug fix."""

    def _make_temp_dir(self, tmp_path: Path) -> Path:
        """Simple make_temp_dir stub that returns a subdirectory."""
        d = tmp_path / f"tmp_{id(self)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_directory_input_no_side_pkgs_returns_path_unchanged(self, tmp_path: Path) -> None:
        """Plain directory with no side packages: lib_dir == input, debug/header None."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()

        lib_out, debug_out, header_out = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=None,
            devel_pkg=None,
            make_temp_dir=lambda p: tmp_path / p,
            is_package=lambda _: False,
            detect_extractor=lambda _: None,
        )

        assert lib_out == lib_dir
        assert debug_out is None
        assert header_out is None

    def test_directory_input_with_debug_pkg_yields_debug_dir(self, tmp_path: Path) -> None:
        """Directory input + standalone debug package: debug_dir must be non-None."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()
        extracted_debug = tmp_path / "extracted_debug"
        extracted_debug.mkdir()

        counter = [0]
        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        dbg_extractor = _make_mock_extractor(lib_dir=extracted_debug, debug_dir=extracted_debug)

        lib_out, debug_out, header_out = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=dbg_pkg,
            devel_pkg=None,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,   # dir is not a package; debug pkg is
            detect_extractor=lambda p: dbg_extractor if p == dbg_pkg else None,
        )

        assert lib_out == lib_dir
        assert debug_out == extracted_debug   # debug_dir from ExtractResult
        assert header_out is None

    def test_directory_input_with_devel_pkg_yields_header_dir(self, tmp_path: Path) -> None:
        """Directory input + standalone devel package: header_dir must be non-None."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dev_pkg = tmp_path / "devel.rpm"
        dev_pkg.touch()
        extracted_headers = tmp_path / "extracted_headers"
        extracted_headers.mkdir()

        counter = [0]
        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        dev_extractor = _make_mock_extractor(lib_dir=extracted_headers, header_dir=extracted_headers)

        lib_out, debug_out, header_out = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=None,
            devel_pkg=dev_pkg,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,
            detect_extractor=lambda p: dev_extractor if p == dev_pkg else None,
        )

        assert lib_out == lib_dir
        assert debug_out is None
        assert header_out == extracted_headers   # header_dir from ExtractResult

    def test_directory_input_with_both_side_pkgs(self, tmp_path: Path) -> None:
        """Directory input + both debug and devel packages: both are returned."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()
        dev_pkg = tmp_path / "devel.rpm"
        dev_pkg.touch()
        extracted_debug = tmp_path / "dbg"
        extracted_debug.mkdir()
        extracted_headers = tmp_path / "hdr"
        extracted_headers.mkdir()

        counter = [0]
        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        dbg_extractor = _make_mock_extractor(lib_dir=extracted_debug, debug_dir=extracted_debug)
        dev_extractor = _make_mock_extractor(lib_dir=extracted_headers, header_dir=extracted_headers)

        def _detect(p: Path) -> MagicMock | None:
            if p == dbg_pkg:
                return dbg_extractor
            if p == dev_pkg:
                return dev_extractor
            return None

        lib_out, debug_out, header_out = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=dbg_pkg,
            devel_pkg=dev_pkg,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,
            detect_extractor=_detect,
        )

        assert lib_out == lib_dir
        assert debug_out == extracted_debug
        assert header_out == extracted_headers

    def test_debug_pkg_fallback_to_lib_dir_when_no_debug_dir(self, tmp_path: Path) -> None:
        """When ExtractResult.debug_dir is None, fall back to lib_dir."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()
        extracted = tmp_path / "extracted"
        extracted.mkdir()

        counter = [0]
        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        # debug_dir=None in ExtractResult: fallback must use lib_dir
        dbg_extractor = _make_mock_extractor(lib_dir=extracted, debug_dir=None)

        lib_out, debug_out, header_out = _extract_if_package(
            input_path=lib_dir,
            debug_pkg=dbg_pkg,
            devel_pkg=None,
            make_temp_dir=_make_temp,
            is_package=lambda p: p != lib_dir,
            detect_extractor=lambda p: dbg_extractor if p == dbg_pkg else None,
        )

        assert lib_out == lib_dir
        assert debug_out == extracted  # fallback to lib_dir

    def test_package_input_uses_result_debug_dir_not_lib_dir(self, tmp_path: Path) -> None:
        """When input is a package, side-pkg debug_dir uses .debug_dir, not .lib_dir."""
        pkg = tmp_path / "main.rpm"
        pkg.touch()
        dbg_pkg = tmp_path / "debuginfo.rpm"
        dbg_pkg.touch()

        main_lib = tmp_path / "main_lib"
        main_lib.mkdir()
        extracted_debug = tmp_path / "real_debug"
        extracted_debug.mkdir()
        extracted_lib_in_dbg = tmp_path / "dbg_lib"
        extracted_lib_in_dbg.mkdir()

        counter = [0]
        def _make_temp(prefix: str) -> Path:
            d = tmp_path / f"{prefix}{counter[0]}"
            counter[0] += 1
            d.mkdir(exist_ok=True)
            return d

        main_extractor = _make_mock_extractor(lib_dir=main_lib)
        # debug_dir differs from lib_dir — must pick debug_dir
        dbg_extractor = _make_mock_extractor(lib_dir=extracted_lib_in_dbg, debug_dir=extracted_debug)

        def _detect(p: Path) -> MagicMock | None:
            if p == pkg:
                return main_extractor
            if p == dbg_pkg:
                return dbg_extractor
            return None

        lib_out, debug_out, header_out = _extract_if_package(
            input_path=pkg,
            debug_pkg=dbg_pkg,
            devel_pkg=None,
            make_temp_dir=_make_temp,
            is_package=lambda _: True,
            detect_extractor=_detect,
        )

        assert lib_out == main_lib
        assert debug_out == extracted_debug  # .debug_dir preferred over .lib_dir
        assert header_out is None


class TestCompareReleaseIncludes:
    def test_discover_include_roots_for_debian_layout(self, tmp_path: Path) -> None:
        """Devel package roots include common distro include subroots."""
        root = tmp_path / "dev"
        (root / "usr" / "include" / "x86_64-linux-gnu").mkdir(parents=True)
        (root / "usr" / "include" / "libxml2").mkdir()
        roots = _discover_include_roots(root)
        assert root in roots
        assert root / "usr" / "include" in roots
        assert root / "usr" / "include" / "x86_64-linux-gnu" in roots
        assert root / "usr" / "include" / "libxml2" in roots

    def test_prepare_inputs_accepts_side_specific_includes(self, tmp_path: Path) -> None:
        """compare-release has compare-like --old-include/--new-include plumbing."""
        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        old_lib = old / "libfoo.so"
        new_lib = new / "libfoo.so"
        old_lib.write_text("old")
        new_lib.write_text("new")
        old_inc_only = tmp_path / "old-include"
        new_inc_only = tmp_path / "new-include"
        old_inc_only.mkdir()
        new_inc_only.mkdir()

        result = _prepare_compare_release_inputs(
            old, new,
            None, None, None, None,
            False, False,
            (), (), (),
            (),
            (old_inc_only,), (new_inc_only,),
            lambda p, _dbg, _dev: (p, None, None),
            lambda *_args, **_kwargs: [],
            lambda _p: False,
            lambda _p: True,
        )

        old_inc = result[4]
        new_inc = result[5]
        assert old_inc == [old_inc_only]
        assert new_inc == [new_inc_only]


class TestLockstepSonameCoupling:
    """A coordinated lockstep SONAME bump across a multi-library release should
    not be flagged 'unnecessary' on members that had no break of their own when
    a sibling/dependency genuinely broke (real-world: oneDAL bumps every
    libonedal* SONAME together because libonedal_core breaks)."""

    @staticmethod
    def _entry(lib, changes, verdict):
        from abicheck.checker import DiffResult
        result = DiffResult(
            old_version="1", new_version="2", library=lib,
            changes=changes, verdict=verdict,
        )
        return {
            "library": lib, "verdict": verdict.value,
            "breaking": len(result.breaking),
            "source_breaks": len(result.source_breaks),
            "risk_changes": len(result.risk),
            "compatible_additions": len(result.compatible),
            "_diff_result": result,
        }

    def test_suppressed_when_sibling_breaks(self):
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so", [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ], Verdict.COMPATIBLE,
        )
        core = self._entry(
            "libonedal_core.so",
            [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            Verdict.BREAKING,
        )
        n = _suppress_lockstep_soname_findings([umbrella, core], "BREAKING", None)
        assert n == 1
        kinds = [c.kind for c in umbrella["_diff_result"].changes]
        assert ChangeKind.SONAME_BUMP_UNNECESSARY not in kinds
        assert umbrella["compatible_additions"] == 0

    def test_kept_when_no_real_break_in_release(self):
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so", [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ], Verdict.COMPATIBLE,
        )
        n = _suppress_lockstep_soname_findings([umbrella], "COMPATIBLE", None)
        assert n == 0
        kinds = [c.kind for c in umbrella["_diff_result"].changes]
        assert ChangeKind.SONAME_BUMP_UNNECESSARY in kinds

    def test_kept_when_release_worst_is_source_only_api_break(self):
        # A SONAME bump is only justified by a *binary* ABI break; a source-only
        # API_BREAK elsewhere must NOT suppress the relink-forcing warning.
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so", [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ], Verdict.COMPATIBLE,
        )
        n = _suppress_lockstep_soname_findings([umbrella], "API_BREAK", None)
        assert n == 0
        kinds = [c.kind for c in umbrella["_diff_result"].changes]
        assert ChangeKind.SONAME_BUMP_UNNECESSARY in kinds

    def test_rewrites_per_library_json(self, tmp_path):
        from abicheck.checker import Change, ChangeKind, Verdict
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        umbrella = self._entry(
            "libonedal.so.3", [
                Change(ChangeKind.SONAME_BUMP_UNNECESSARY, "DT_SONAME", "bump"),
            ], Verdict.COMPATIBLE,
        )
        core = self._entry(
            "libonedal_core.so.3",
            [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            Verdict.BREAKING,
        )
        _suppress_lockstep_soname_findings([umbrella, core], "BREAKING", tmp_path)
        data = json.loads((tmp_path / "libonedal.so.json").read_text())
        assert all(c["kind"] != "soname_bump_unnecessary" for c in data["changes"])


def test_release_json_emits_severity_block() -> None:
    """compare-release JSON carries a severity config block when --severity-* is
    active, so the PR-comment renderer can mirror the gate (issue #342 follow-up).
    """
    from abicheck.severity import resolve_severity_config

    cfg = resolve_severity_config("default", addition="error")
    out = _format_release_json(
        "COMPATIBLE", Path("/o"), Path("/n"), [], [], [], {}, {}, [], None, None,
        severity_config=cfg, severity_exit_code=1,
    )
    data = json.loads(out)
    assert data["severity"]["config"]["addition"] == "error"
    assert data["severity"]["exit_code"] == 1


def test_release_json_omits_severity_block_without_config() -> None:
    out = _format_release_json(
        "COMPATIBLE", Path("/o"), Path("/n"), [], [], [], {}, {}, [], None, None,
    )
    assert "severity" not in json.loads(out)

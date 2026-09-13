"""Tests for the compare-release command (multi-binary directory comparison)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_compare_release import (
    _discover_include_roots,
    _format_release_json,
    _prepare_compare_release_inputs,
)
from abicheck.cli_helpers_compare import (
    _build_match_map,
    _canonical_library_key,
    _version_sort_key,
    strip_vendor_hash,
)
from abicheck.cli_resolve import _is_supported_compare_input
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import snapshot_to_json

# ── helpers ──────────────────────────────────────────────────────────────────


def _snap(
    version: str = "1.0",
    funcs: list[Function] | None = None,
    library: str = "libfoo.so",
) -> AbiSnapshot:
    if funcs is None:
        funcs = [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ]
    return AbiSnapshot(
        library=library, version=version, functions=funcs, from_headers=True
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    """Remove a function — always produces BREAKING verdict."""
    old = _snap(
        "1.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="bar",
                mangled="_Z3barv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    new = _snap(
        "2.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    return old, new


def _api_break_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    """Signature change that requires recompilation but is not a binary break."""
    old = _snap(
        "1.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                params=[Param(name="x", type="int", default="0")],
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    new = _snap(
        "2.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    return old, new


def _invoke(*args: str) -> tuple[int, str]:
    """Run the CLI and return ``(exit_code, stdout)``.

    **stdout, not ``result.output``.** Click >= 8.2's ``Result.output`` is the
    *combined* stream, and the ledgers plan slice 7o made unconditional
    (pattern modulations, the suppression audit, the public-surface scope
    ledger) are written to stderr by design -- a real shell keeps them out of
    a redirected `-o json=-` document, but the combined capture splices them
    in ahead of the JSON, so 23 callers here that do ``json.loads(out)`` were
    parsing a document with a human ledger prepended. Reading the real stdout
    stream is both the fix and the more faithful harness: it is what a
    consumer redirecting the CLI's stdout actually receives.
    """
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.stdout


def _invoke_combined(*args: str) -> tuple[int, str]:
    """Run the CLI and return ``(exit_code, stdout + stderr)``.

    The sibling of :func:`_invoke` for the handful of tests whose claim is
    about a *message* (a usage error, a stranded-library warning) rather than
    a rendered document. Those live on stderr, which is exactly why
    :func:`_invoke` does not return them.
    """
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

    def test_compressed_snapshot_matches_plain(self, tmp_path: Path) -> None:
        # Codex review, PR #699: a compressed snapshot (ADR-059) must key-match
        # the same release's plain JSON -- the storage envelope is not part of
        # the release identity, so switching only the compression must not
        # turn a matched pair into an unrelated removal+addition.
        plain = _canonical_library_key(Path("libfoo.abicheck.json"))
        gz = _canonical_library_key(Path("libfoo.abicheck.json.gz"))
        zst = _canonical_library_key(Path("libfoo.abicheck.json.zst"))
        assert plain == gz == zst == "libfoo.abicheck.json"

    def test_compressed_snapshot_with_so_version(self, tmp_path: Path) -> None:
        assert (
            _canonical_library_key(Path("libfoo.so.1.2.zst"))
            == _canonical_library_key(Path("libfoo.so.1.2"))
            == "libfoo.so"
        )


class TestStripVendorHash:
    """G9: auditwheel/delocate rewrite vendored libs to lib<name>-<hash>.so.<ver>,
    with a content-derived hash that changes every rebuild. strip_vendor_hash
    normalizes that away so compare-release can pair the same dependency across
    releases instead of reporting every one as removed+added noise."""

    def test_auditwheel_hex_before_so_stripped(self) -> None:
        assert (
            strip_vendor_hash("libpng16-a746ad4a.so.16.43.0") == "libpng16.so.16.43.0"
        )

    def test_different_hash_same_stem(self) -> None:
        # The whole point: two different rebuild hashes must normalize identically.
        a = strip_vendor_hash("libpng16-a746ad4a.so.16.43.0")
        b = strip_vendor_hash("libpng16-b8f31c2e.so.16.56.0")
        assert a == "libpng16.so.16.43.0"
        assert b == "libpng16.so.16.56.0"

    def test_16char_hash_stripped(self) -> None:
        assert (
            strip_vendor_hash("libsodium-1234567890abcdef.so.23.3.0")
            == "libsodium.so.23.3.0"
        )

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

    def test_compression_suffix_does_not_perturb_version_order(self) -> None:
        """Codex review, PR #699: an unstripped ".gz"/".zst" storage suffix
        (ADR-059) becomes an extra alphabetic sort token that always
        outranks a plain ".json" and always outranks each other
        alphabetically (".zst" > ".gz") -- regardless of which snapshot is
        actually the newer version. A real version difference (v1 vs v2)
        must still order correctly no matter which side happens to carry
        which compression suffix, including the "wrong direction" case
        where the naive bug would have picked the numerically older file
        just because it's the more-compressed one."""
        older_plain = _version_sort_key(Path("libfoo.so.1.abicheck.json"), "libfoo.so")
        newer_zst = _version_sort_key(
            Path("libfoo.so.2.abicheck.json.zst"), "libfoo.so"
        )
        assert older_plain < newer_zst

        # The bug's exact failure direction: a numerically *older* file
        # compressed with zstd must not outrank a numerically *newer* file
        # left plain, just because ".zst" sorts alphabetically last.
        older_zst = _version_sort_key(
            Path("libfoo.so.1.abicheck.json.zst"), "libfoo.so"
        )
        newer_plain = _version_sort_key(Path("libfoo.so.2.abicheck.json"), "libfoo.so")
        assert older_zst < newer_plain

    def test_encoding_only_duplicates_reduce_to_the_same_sort_key(self) -> None:
        """Two snapshots differing *only* by storage encoding (same
        version) carry no information in the filename to break the tie by
        -- confirm they now reduce to an identical sort key (surfaced as
        an honest ambiguous-match warning by _build_match_map, rather than
        silently, deterministically always preferring zstd)."""
        plain = _version_sort_key(Path("libfoo.so.1.abicheck.json"), "libfoo.so")
        gz = _version_sort_key(Path("libfoo.so.1.abicheck.json.gz"), "libfoo.so")
        zst = _version_sort_key(Path("libfoo.so.1.abicheck.json.zst"), "libfoo.so")
        assert plain == gz == zst


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


class TestBuildMatchMapEncodingOnlyDuplicates:
    """Codex review, PR #699 (second round on the same fix): stripping a
    compressed snapshot's storage suffix from the sort key makes two
    candidates differing *only* by encoding reduce to an identical sort
    key -- sorted()'s stability then means the winner is decided by
    original list order, itself alphabetically biased toward whichever
    compression suffix sorts last, so silently picking one and only
    warning would deterministically prefer a stale compressed sibling
    over a fresh plain one every time. Confirm this is now a hard error
    instead."""

    def test_plain_and_zstd_duplicate_is_rejected(self, tmp_path: Path) -> None:
        plain = tmp_path / "libfoo.so.1.abicheck.json"
        zst = tmp_path / "libfoo.so.1.abicheck.json.zst"
        plain.write_text("{}")
        zst.write_bytes(b"\x28\xb5\x2f\xfd")
        import click.exceptions

        with pytest.raises(click.exceptions.ClickException, match="indistinguishable"):
            _build_match_map([plain, zst])

    def test_all_three_encodings_duplicate_is_rejected(self, tmp_path: Path) -> None:
        plain = tmp_path / "libfoo.so.1.abicheck.json"
        gz = tmp_path / "libfoo.so.1.abicheck.json.gz"
        zst = tmp_path / "libfoo.so.1.abicheck.json.zst"
        for p in (plain, gz, zst):
            p.write_bytes(b"{}")
        import click.exceptions

        with pytest.raises(click.exceptions.ClickException, match="indistinguishable"):
            _build_match_map([plain, gz, zst])

    def test_genuinely_different_versions_still_only_warn(self, tmp_path: Path) -> None:
        """Sanity: a real multi-version bucket (distinct sort keys) must
        not be caught by the new tie check -- only an exact tie is a hard
        error."""
        v1 = tmp_path / "libfoo.so.1.abicheck.json"
        v2 = tmp_path / "libfoo.so.2.abicheck.json.zst"
        v1.write_text("{}")
        v2.write_bytes(b"\x28\xb5\x2f\xfd")
        mapping, warnings = _build_match_map([v1, v2])
        assert mapping["libfoo.so"] == v2
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
        code, out = _invoke("compare", str(old_f), str(new_f), "-o", "json=-")
        assert code == 0
        data = json.loads(out)
        # Two *file* operands are a single-pair compare (no release fan-out), so
        # the JSON is the single-pair report — not the release `libraries` summary
        # the removed compare-release produced for file inputs. Directory operands
        # still produce that summary (covered by the dir-vs-dir tests below).
        assert data["verdict"] == "NO_CHANGE"
        assert "libraries" not in data

    def test_bundle_facts_out_is_rejected_for_a_single_pair(
        self, tmp_path: Path
    ) -> None:
        """G38 Phase 2 (Codex review, fresh evidence): a single-file/
        snapshot compare has no OLD-side library map to persist, so
        `--bundle-facts-out` was silently ignored -- reporting success
        while leaving automation believing a baseline was written when
        none was. Reject it outright instead."""
        snap = _snap()
        old_f = _write_snap(tmp_path / "libfoo.json", snap)
        new_f = _write_snap(tmp_path / "libfoo2.json", snap)
        code, out = _invoke_combined(
            "compare",
            str(old_f),
            str(new_f),
            "--bundle-facts-out",
            str(tmp_path / "baseline.json"),
        )
        assert code == 64
        assert "Usage:" in out
        assert "--bundle-facts-out is only supported for directory/package" in out
        assert not (tmp_path / "baseline.json").exists()


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

    def test_usage_error_from_release_engine_has_usage_header(
        self, tmp_path: Path
    ) -> None:
        """CLI-audit P2: `_dispatch_release_compare` now calls
        `compare_release_cmd.callback` directly instead of
        `ctx.invoke(compare_release_cmd, ...)` (no more Click-to-Click
        orchestration). `ctx.invoke` used to backfill `UsageError.ctx` via
        its `augment_usage_errors` wrapper so the formatted CLI error got a
        "Usage: ..." header; `_dispatch_release_compare` now does that
        backfill by hand -- this proves a usage error on a directory compare
        still exits 64 with the same "Usage:" header, not a degraded
        one-line "Error: ..." message.

        Since plan slice 7m the trigger is the export request's own
        collision check ("two exports to one destination"), raised while the
        operand is parsed rather than inside the release engine -- an
        earlier `--annotate-additions` trigger, and the `--write`-vs-`-o`
        one after it, were each removed with the flag they used."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)
        same_path = tmp_path / "out.json"
        code, out = _invoke_combined(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            f"markdown={same_path}",
            "-o",
            f"json={same_path}",
        )
        assert code == 64
        assert "Usage:" in out
        assert "both export to" in out

    @pytest.mark.parametrize(
        "extra_args,facts_name,expected_substr",
        [
            (("-o", "json={p}"), "out.json", "must differ from --output/-o"),
            (("-o", "markdown={p}"), "out.json", "must differ from --output/-o"),
            (("-o", "json={d}/"), "summary.json", "summary.json"),
            (("-o", "json={d}/"), "libfoo.json", "'libfoo.json'"),
        ],
        ids=[
            "export",
            "export_other_format",
            "directory_summary",
            "directory_per_library",
        ],
    )
    def test_bundle_facts_out_rejects_output_collisions(
        self,
        tmp_path: Path,
        extra_args: tuple[str, str],
        facts_name: str,
        expected_substr: str,
    ) -> None:
        """G38 Phase 2 (Codex review, two rounds): `--bundle-facts-out`
        naming the same path as any export -- a file export, a
        per-component export's own `summary.json`, or one of its
        per-library `<stem>.json` files -- would silently overwrite the
        requested baseline with the report, so each is rejected up front,
        before the per-component directory is even created.

        `--bundle-facts-out` is not part of the export set (plan slice 7m),
        which is exactly why it needs its own check: the export request's
        own collision detection cannot see a destination that is not an
        export."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = _snap()
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)
        report_dir = tmp_path / "reports"
        same_path = tmp_path / "out.json"
        resolved_extra = [a.format(p=same_path, d=report_dir) for a in extra_args]
        code, out = _invoke_combined(
            "compare",
            str(old_dir),
            str(new_dir),
            *resolved_extra,
            "--bundle-facts-out",
            str(tmp_path / facts_name)
            if facts_name == "out.json"
            else str(report_dir / facts_name),
        )
        assert code == 64
        assert "Usage:" in out
        assert expected_substr in out
        assert not report_dir.exists()

    def test_json_output_multi(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        for name in ("libfoo.json", "libbar.json"):
            snap = _snap()
            _write_snap(old_dir / name, snap)
            _write_snap(new_dir / name, snap)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 0
        data = json.loads(out)
        assert data["verdict"] == "NO_CHANGE"
        assert len(data["libraries"]) == 2

    def test_json_output_carries_release_schema_version(self, tmp_path: Path) -> None:
        """Codex review, findings-fixes round 11: the release JSON envelope
        previously had no schema-version field at all, so a consumer could
        not distinguish a document predating the `compatible_additions`
        semantic correction from one written after it. A present, non-empty
        `release_schema_version` closes that gap."""
        from abicheck.schemas import RELEASE_SCHEMA_VERSION

        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 0
        data = json.loads(out)
        assert data["release_schema_version"] == RELEASE_SCHEMA_VERSION

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
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 4
        data = json.loads(out)
        lib = data["libraries"][0]
        assert lib["breaking"] == 1
        assert "findings" in lib
        assert lib["findings"][0]["symbol"] == "_Z3barv"
        assert lib["findings"][0]["bucket"] == "breaking"
        assert "findings_truncated" not in lib
        assert "_diff_result" not in lib

    def test_json_output_is_complete_when_no_cap_was_requested(
        self, tmp_path: Path
    ) -> None:
        """The machine document carries every finding by default.

        It used to carry the same 10-entry presentation projection the
        Markdown summary renders, flagged ``findings_truncated`` -- so a
        consumer of ``--format json`` got a lossy document nobody asked to
        truncate, and (per ``_release_md_library_findings``'s own note at
        the time) no complete source to fall back to unless the run also
        passed ``--output-dir``. A truncated *human* summary is fine; an
        implicitly truncated machine document is not. The explicit-cap
        counterpart is ``test_max_findings_per_library_overrides_the_
        default_cap`` below, which still truncates and still flags it.
        """
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_funcs = [
            Function(
                name=f"foo{i}",
                mangled=f"_Z4foo{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(15)
        ]
        old = _snap("1.0", old_funcs, library="libfoo.so")
        new = _snap("2.0", [], library="libfoo.so")
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 4
        lib = json.loads(out)["libraries"][0]
        assert lib["breaking"] == 15
        # Every gating finding: the 15 removals plus the library's own
        # `public_surface_shrank` quality finding, which the old default cap
        # cut outright (the 10 slots were already spent on removals).
        assert len(lib["findings"]) == 16
        assert "findings_truncated" not in lib
        assert "findings_truncated_kinds" not in lib
        kinds = {f["kind"] for f in lib["findings"]}
        assert "func_removed" in kinds and "public_surface_shrank" in kinds

    def test_max_findings_per_library_overrides_the_default_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``compare --max-findings-per-library`` (Codex review: the release
        cap was a hardcoded 10 with no override, unlike `scan --max-findings`)
        raises the per-library findings cap; the env var does the same when no
        explicit flag is given."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_funcs = [
            Function(
                name=f"foo{i}",
                mangled=f"_Z4foo{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(15)
        ]
        old = _snap("1.0", old_funcs, library="libfoo.so")
        new = _snap("2.0", [], library="libfoo.so")
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        # 16 real findings total: 15 `func_removed` plus the library's own
        # `public_surface_shrank` quality finding -- a cap of 16 is the
        # smallest one that leaves nothing truncated.

        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "json=-",
        )
        assert code == 4
        lib = json.loads(out)["libraries"][0]
        assert len(lib["findings"]) == 16
        assert "findings_truncated" not in lib
        assert "findings_truncated_kinds" not in lib

        monkeypatch.setenv("ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY", "16")
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 4
        lib = json.loads(out)["libraries"][0]
        assert len(lib["findings"]) == 16
        assert "findings_truncated" not in lib

    def test_json_findings_include_severity_gated_addition(
        self, tmp_path: Path
    ) -> None:
        """Codex review on #557: when --severity-addition error promotes a
        compatible-additions-only library to a nonzero severity exit code,
        the per-library findings list — which only walked the legacy
        breaking/api_break/risk buckets — must still include the addition
        finding that's actually responsible for the gate, not report an
        empty findings list next to severity.exit_code != 0."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old = _snap("1.0", [], library="libfoo.so")
        new_funcs = [
            Function(
                name="new_api",
                mangled="_Z6new_apiv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ]
        new = _snap("2.0", new_funcs, library="libfoo.so")
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        # --severity-addition duplicated `severity.addition` and was removed;
        # the release fan-out reads the resolved severity config either way,
        # which is the point of driving it from .abicheck.yml here.
        cfg = tmp_path / "addition-error.abicheck.yml"
        cfg.write_text("severity:\n  addition: error\n", encoding="utf-8")
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "json=-",
            "--config",
            str(cfg),
        )
        assert code == 1
        data = json.loads(out)
        lib = data["libraries"][0]
        assert data["severity"]["exit_code"] == 1
        assert "findings" in lib
        # Two findings now, not one: `_release_display_buckets` (Codex
        # review, PR #1154 follow-up: "Filter the complete release finding
        # set") walks every category (not only the ones actually blocking),
        # so the unconditional surface-metrics `public_surface_grew`
        # quality-issue finding is present alongside the addition that
        # actually promotes the severity exit code -- matching what a
        # single-pair `compare` on the identical pair displays.
        findings_by_kind = {f["kind"]: f for f in lib["findings"]}
        assert findings_by_kind["func_added"]["symbol"] == "_Z6new_apiv"
        assert findings_by_kind["func_added"]["bucket"] == "addition"
        assert findings_by_kind["public_surface_grew"]["bucket"] == "quality_issues"

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

    def test_fully_disjoint_dirs_is_no_comparison_completed(
        self, tmp_path: Path
    ) -> None:
        """ADR-065 D7: exit 1, never 0 (see test_release_scope_completeness.py)."""
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libbar.json", _snap())
        code, out = _invoke_combined("compare", str(old_dir), str(new_dir))
        assert code == 1
        assert "no matching" in out.lower() or "warning" in out.lower()


class TestReleaseFindingsDisplayCap:
    """The per-library display cap is one constant with no way to override it.

    Plan slice 7m retired both overrides (`--max-findings-per-library` and
    the `ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY` environment variable),
    so the resolver they fed is gone too -- what this class used to test was
    that resolver's edge cases (invalid explicit value, malformed or
    non-positive env var, nothing set), every one of which was a way for a
    caller to influence the cap. Stated the other way round now, because
    "there is no resolver" is a property worth pinning: a machine export
    carries every finding, and a human summary is bounded automatically at
    one value.
    """

    def test_the_cap_is_a_single_positive_constant(self) -> None:
        from abicheck.report.release_display_limits import (
            MAX_RELEASE_FINDINGS_PER_LIBRARY,
        )

        assert isinstance(MAX_RELEASE_FINDINGS_PER_LIBRARY, int)
        assert MAX_RELEASE_FINDINGS_PER_LIBRARY >= 1

    def test_the_leaf_exposes_nothing_that_resolves_a_cap(self) -> None:
        """No resolver, no env-var name, no "is it explicit" predicate --
        an override reintroduced anywhere would need one of these back, so
        their absence is the executable form of "the cap is automatic"."""
        from abicheck.report import release_display_limits

        assert release_display_limits.__all__ == ["MAX_RELEASE_FINDINGS_PER_LIBRARY"]
        for retired in (
            "resolve_max_release_findings_per_library",
            "release_findings_cap_is_explicit",
            "MAX_RELEASE_FINDINGS_PER_LIBRARY_ENV_VAR",
        ):
            assert not hasattr(release_display_limits, retired), retired

    def test_no_environment_variable_changes_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The env var retired with the flag rather than surviving it --
        Phase 7l's standing constraint that a demoted flag lands in
        `.abicheck.yml`, never in an undocumented variable. Asserted by
        setting it and observing that nothing reads it."""
        from abicheck.cli_compare_release_matrix import (
            _MAX_RELEASE_FINDINGS_PER_LIBRARY,
        )

        monkeypatch.setenv("ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY", "999")
        from abicheck.report.release_display_limits import (
            MAX_RELEASE_FINDINGS_PER_LIBRARY,
        )

        assert MAX_RELEASE_FINDINGS_PER_LIBRARY == _MAX_RELEASE_FINDINGS_PER_LIBRARY
        assert MAX_RELEASE_FINDINGS_PER_LIBRARY != 999


class TestAccumulateReleaseKindCounts:
    def test_empty_kinds_and_no_existing_entry_is_a_no_op(self) -> None:
        """The `if counter:` guard must skip setting the field at all when
        there is nothing to accumulate -- a real branch, since every other
        call site always passes at least one cut kind."""
        from abicheck.cli_compare_release_matrix import (
            _accumulate_release_kind_counts,
        )

        entry: dict[str, object] = {}
        _accumulate_release_kind_counts(entry, "findings_truncated_kinds", [])
        assert "findings_truncated_kinds" not in entry

    def test_accumulates_onto_an_existing_running_dict(self) -> None:
        """A second call must add to (never replace) a prior call's counts,
        and the result is always sorted by kind name."""
        from abicheck.cli_compare_release_matrix import (
            _accumulate_release_kind_counts,
        )

        entry: dict[str, object] = {"findings_truncated_kinds": {"func_removed": 2}}
        _accumulate_release_kind_counts(
            entry, "findings_truncated_kinds", ["var_removed", "func_removed"]
        )
        assert entry["findings_truncated_kinds"] == {
            "func_removed": 3,
            "var_removed": 1,
        }


class TestBundleFactsOutStrandedLibraryWarning:
    """The `--bundle-facts-out` stranded-library fallback warns (rather than
    silently persisting a lossy entry) when the real resolve itself fails
    (Codex review, fresh evidence) -- end-to-end through the real CLI, since
    the warning lives in `compare_release_cmd`'s own nested closure, not in
    the independently-testable `write_bundle_facts_out()` helper.
    """

    def test_warns_when_a_stranded_library_cannot_be_fully_resolved(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = _snap(library="libfoo.so")
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)
        # Old-only, and not a real ELF/JSON/etc. -- service.resolve_input()
        # raises on it, so the fallback's own except-and-degrade path (and
        # its warning) is what gets exercised.
        (old_dir / "libbroken.so").write_bytes(b"")

        out_path = tmp_path / "old.bundlefacts.json"
        code, out = _invoke_combined(
            "compare", str(old_dir), str(new_dir), "--bundle-facts-out", str(out_path)
        )

        assert code == 0
        assert "libbroken.so" in out
        assert "ELF-only" in out
        assert out_path.exists()


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
        """ADR-065 D2: a plain directory cannot prove a removal, so no exit 8
        (see test_release_scope_completeness.py for the proven case). Phase
        7d: gate.fail_on_removed_library config key (--fail-on-removed-
        library is gone from the CLI)."""
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libbar.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("gate:\n  fail_on_removed_library: true\n")
        code, out = _invoke(
            "compare", str(old_dir), str(new_dir), "--config", str(cfg), "-o", "json=-"
        )
        assert code == 0
        assert json.loads(out)["comparison_scope"]["proven_removed"] == []

    def test_removed_and_breaking_exits_4_not_8(self, tmp_path: Path) -> None:
        """BREAKING (4) takes priority over removed-library (8).

        Phase 7d: gate.fail_on_removed_library config key, not the removed
        --fail-on-removed-library flag."""
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        _write_snap(old_dir / "libremoved.json", _snap())  # removed
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("gate:\n  fail_on_removed_library: true\n")
        code, _ = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(cfg),
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
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
        assert code == 0
        data = json.loads(out)
        # Paired, not phantom removed+added.
        assert data["unmatched_old"] == []
        assert data["unmatched_new"] == []
        assert len(data["libraries"]) == 1

    def test_real_break_in_hashed_vendored_lib_still_surfaces(
        self, tmp_path: Path
    ) -> None:
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
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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
        _write_snap(
            old_dir / "libwebpdemux.so.2.0.14.json", _snap(library="libwebpdemux.so.2")
        )
        _write_snap(
            new_dir / "libwebpdemux.so.2.0.14.json", _snap(library="libwebpdemux.so.2")
        )
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            f"json={out_dir}/",
        )
        assert code == 0
        assert (out_dir / "libfoo.json").exists()
        assert (out_dir / "summary.json").exists()

    def test_per_library_report_exit_block_honors_release_severity(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR G1 follow-up (fresh evidence): ``_compare_one_
        library`` wrote each per-library ``--output-dir`` report via
        ``to_json(result)`` with no ``severity_config``, so the persisted
        ``exit`` block (schema 2.41, ``exit_decision.ExitDecision``) always
        used the legacy verdict-based scheme regardless of whether the
        release itself resolved and gated on a severity configuration.
        Reproduced with ``--severity-preset strict`` (additions are error):
        the release process exits ``1``, but the per-library report's own
        ``exit.code`` read ``0``/``reasons: ["clean"]`` before this fix --
        disagreeing with the process it was supposedly explaining.
        """
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        out_dir = tmp_path / "reports"
        old = _snap(
            "1.0",
            [
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        new = _snap(
            "2.0",
            [
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
                Function(
                    name="bar",
                    mangled="_Z3barv",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        code, _ = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            f"json={out_dir}/",
            "--severity-preset",
            "strict",
        )
        assert code == 1
        report = json.loads((out_dir / "libfoo.json").read_text())
        # The addition-error gate is severity-scheme-only -- a report built
        # under the legacy scheme's ExitDecision would read code 0, reasons
        # ["clean"], disagreeing with the real (severity-gated) release exit.
        assert report["exit"]["code"] == 1
        assert "compatibility_gate" in report["exit"]["reasons"]

    def test_summary_json_structure(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        out_dir = tmp_path / "reports"
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        code, _ = _invoke(
            "compare", str(old_dir), str(new_dir), "-o", f"json={out_dir}/"
        )
        assert code == 0
        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary["verdict"] == "NO_CHANGE"
        assert len(summary["libraries"]) == 1


def _rec(name: str, size: int) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size,
        fields=[TypeField(name="x", type="int")],
    )


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
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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
        pub_old = Function(
            name="api_call",
            mangled="api_call",
            return_type="int",
            params=[Param(name="c", type="Config *")],
            visibility=Visibility.PUBLIC,
        )
        pub_new = Function(
            name="new_api",
            mangled="new_api",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
        old = AbiSnapshot(
            library="libfoo.so",
            version="1",
            functions=[pub_old],
            types=[_rec("Config", 32), _rec("InternalCache", 64)],
        )
        new = AbiSnapshot(
            library="libfoo.so",
            version="2",
            functions=[pub_old, pub_new],
            types=[_rec("Config", 32), _rec("InternalCache", 128)],
        )
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--scope-public-headers",
            "-o",
            "json=-",
        )
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
        old = AbiSnapshot(
            library="libfoo.so",
            version="1",
            functions=[],
            types=[_rec("InternalCache", 64)],
        )
        new = AbiSnapshot(
            library="libfoo.so",
            version="2",
            functions=[],
            types=[_rec("InternalCache", 128)],
        )
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--scope-public-headers",
            "-o",
            "json=-",
        )
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
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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
        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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
        (old_dir / "html.tpl").write_text(
            "{% extends 'base.tpl' %}\n{% block content %}\n...\n{% endblock %}"
        )
        (new_dir / "html.tpl").write_text(
            "{% extends 'base.tpl' %}\n{% block content %}\n...\n{% endblock %}"
        )

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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
        (old_dir / "v0.7.1.some-named-index.parquet").write_bytes(
            b"PAR1" + b"fake data" * 100
        )
        (new_dir / "v0.7.1.some-named-index.parquet").write_bytes(
            b"PAR1" + b"fake data" * 100
        )
        (old_dir / "solution.json").write_text('{"answer": 42}')
        (new_dir / "solution.json").write_text('{"answer": 42}')
        (old_dir / "something.dll.txt").write_text("Not a DLL")
        (new_dir / "something.dll.txt").write_text("Not a DLL")

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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

        code, out = _invoke("compare", str(old_dir), str(new_dir), "-o", "json=-")
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


class TestDebianSymbolsWarning:
    """CLI-audit P2: the extracted .symbols contract must actually
    participate in a package compare, not just be extracted and ignored."""

    def test_none_when_either_side_missing(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release_helpers import _debian_symbols_warning

        symbols = tmp_path / "symbols"
        symbols.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 1.0\n")
        assert _debian_symbols_warning(None, None) is None
        assert _debian_symbols_warning(symbols, None) is None
        assert _debian_symbols_warning(None, symbols) is None

    def test_none_when_contracts_identical(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release_helpers import _debian_symbols_warning

        text = "libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 1.0\n"
        old = tmp_path / "old_symbols"
        new = tmp_path / "new_symbols"
        old.write_text(text)
        new.write_text(text)
        assert _debian_symbols_warning(old, new) is None

    def test_reports_removed_and_added_symbols(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release_helpers import _debian_symbols_warning

        old = tmp_path / "old_symbols"
        new = tmp_path / "new_symbols"
        old.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 1.0\n _bar@Base 1.0\n")
        new.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 1.0\n _baz@Base 1.0\n")
        note = _debian_symbols_warning(old, new)
        assert note is not None
        assert "Debian symbols contract changed" in note
        assert "_bar@Base" in note
        assert "_baz@Base" in note

    def test_reports_version_regression(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release_helpers import _debian_symbols_warning

        old = tmp_path / "old_symbols"
        new = tmp_path / "new_symbols"
        old.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 2.0\n")
        new.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 1.0\n")
        note = _debian_symbols_warning(old, new)
        assert note is not None
        assert "_foo" in note
        assert "2.0 -> 1.0" in note

    def test_unparseable_file_reported_not_raised(self, tmp_path: Path) -> None:
        from abicheck.cli_compare_release_helpers import _debian_symbols_warning

        old = tmp_path / "old_symbols"
        new = tmp_path / "new_symbols"
        old.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 1.0\n")
        # Missing entirely -- load_symbols_file must raise OSError, not crash
        # the whole release compare.
        note = _debian_symbols_warning(old, new)
        assert note is not None
        assert "could not be parsed" in note

    def test_prepare_inputs_folds_symbols_note_into_warnings(
        self, tmp_path: Path
    ) -> None:
        """End-to-end through _prepare_compare_release_inputs: a real
        extract_if_package callable returning differing symbols_file paths
        must land its note in the returned warning_msgs."""
        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        (old / "libfoo.so").write_text("old")
        (new / "libfoo.so").write_text("new")
        old_symbols = tmp_path / "old_symbols"
        new_symbols = tmp_path / "new_symbols"
        old_symbols.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 1.0\n")
        new_symbols.write_text("libfoo.so.1 libfoo1 #MINVER#\n _foo@Base 2.0\n")

        def _fake_extract(p, _dbg, _dev):
            symbols = old_symbols if p == old else new_symbols
            return p, None, None, symbols, False

        result = _prepare_compare_release_inputs(
            old,
            new,
            None,
            None,
            None,
            None,
            False,
            False,
            (),
            (),
            (),
            (),
            (),
            (),
            (),
            _fake_extract,
            lambda *_args, **_kwargs: [],
            lambda _p: False,
            lambda _p: True,
        )
        warning_msgs = result[8]
        assert any("Debian symbols contract changed" in m for m in warning_msgs)


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

    def test_prepare_inputs_accepts_side_specific_includes(
        self, tmp_path: Path
    ) -> None:
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
            old,
            new,
            None,
            None,
            None,
            None,
            False,
            False,
            (),
            (),
            (),
            (),
            (old_inc_only,),
            (new_inc_only,),
            (),
            lambda p, _dbg, _dev: (p, None, None, None, False),
            lambda *_args, **_kwargs: [],
            lambda _p: False,
            lambda _p: True,
        )

        old_inc = result[4]
        new_inc = result[5]
        assert old_inc == [old_inc_only]
        assert new_inc == [new_inc_only]

    def test_prepare_inputs_config_includes_survive_side_specific_override(
        self, tmp_path: Path
    ) -> None:
        """Regression (Codex review): a project .abicheck.yml
        compile.include_dirs root must reach BOTH sides even when one side
        also has a --old-include/--new-include override -- a prior revision
        let the override fully replace `includes` (which is where the
        config-appended dirs live), silently dropping them for the
        overridden side.
        """
        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        (old / "libfoo.so").write_text("old")
        (new / "libfoo.so").write_text("new")
        old_inc_only = tmp_path / "old-include"
        old_inc_only.mkdir()
        config_dir = tmp_path / "vendor_include"
        config_dir.mkdir()

        result = _prepare_compare_release_inputs(
            old,
            new,
            None,
            None,
            None,
            None,
            False,
            False,
            (),
            (),
            (),
            (config_dir,),  # includes: the caller already folds
            # config_includes into this merged tuple, matching what
            # resolve_directory_compile_context's real return looks like.
            (old_inc_only,),  # old side overridden
            (),  # new side not overridden -- uses `includes` directly
            (config_dir,),  # config_includes: same dir, passed separately
            lambda p, _dbg, _dev: (p, None, None, None, False),
            lambda *_args, **_kwargs: [],
            lambda _p: False,
            lambda _p: True,
        )

        old_inc = result[4]
        new_inc = result[5]
        # Overridden side: override root + config root, both present.
        assert old_inc_only in old_inc
        assert config_dir in old_inc
        # Non-overridden side: reaches the config root via `includes` itself.
        assert config_dir in new_inc


def test_release_json_emits_severity_block() -> None:
    """compare-release JSON carries a severity config block when --severity-* is
    active, so the PR-comment renderer can mirror the gate (issue #342 follow-up).
    """
    from abicheck.severity import resolve_severity_config

    cfg = resolve_severity_config("default", addition="error")
    out = _format_release_json(
        "COMPATIBLE",
        Path("/o"),
        Path("/n"),
        [],
        [],
        [],
        {},
        {},
        [],
        None,
        None,
        severity_config=cfg,
        severity_exit_code=1,
    )
    data = json.loads(out)
    assert data["severity"]["config"]["addition"] == "error"
    assert data["severity"]["exit_code"] == 1


def test_release_json_omits_severity_block_without_config() -> None:
    out = _format_release_json(
        "COMPATIBLE",
        Path("/o"),
        Path("/n"),
        [],
        [],
        [],
        {},
        {},
        [],
        None,
        None,
    )
    assert "severity" not in json.loads(out)

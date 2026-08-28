"""Tests for library metadata and report enrichments across all formats.

Covers:
- LibraryMetadata in DiffResult
- JSON: old_file / new_file sections, impact, source_location, affected_symbols
- Markdown: Library Files table, impact blockquote, affected symbols, source location
- SARIF: run properties with file metadata, impact in fullDescription, source locations
- HTML: file metadata table in both standard and compat modes
- JSON: zero-count detector trimming
- Deduplication of AST/DWARF findings
"""

import hashlib
import json
import tempfile
from pathlib import Path

from abicheck.checker import Change, ChangeKind, DiffResult, LibraryMetadata, Verdict
from abicheck.reporter import to_json, to_markdown

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta(path: str = "/lib/libfoo.so", sha: str = "abc123def456", size: int = 4096) -> LibraryMetadata:
    return LibraryMetadata(path=path, sha256=sha, size_bytes=size)


def _result(
    verdict: Verdict = Verdict.BREAKING,
    changes: list[Change] | None = None,
    old_meta: LibraryMetadata | None = None,
    new_meta: LibraryMetadata | None = None,
) -> DiffResult:
    r = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=changes or [],
        verdict=verdict,
    )
    r.old_metadata = old_meta
    r.new_metadata = new_meta
    return r


# ---------------------------------------------------------------------------
# LibraryMetadata dataclass
# ---------------------------------------------------------------------------

class TestLibraryMetadata:
    def test_fields(self):
        m = _meta("/usr/lib/libbar.so", "deadbeef", 8192)
        assert m.path == "/usr/lib/libbar.so"
        assert m.sha256 == "deadbeef"
        assert m.size_bytes == 8192

    def test_default_on_diff_result(self):
        r = DiffResult(old_version="1", new_version="2", library="lib.so")
        assert r.old_metadata is None
        assert r.new_metadata is None


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------

class TestJsonMetadata:
    def test_no_metadata_null_values(self):
        d = json.loads(to_json(_result()))
        assert d["old_file"] is None
        assert d["new_file"] is None

    def test_metadata_present(self):
        r = _result(
            old_meta=_meta("/old/lib.so", "aaa111", 1000),
            new_meta=_meta("/new/lib.so", "bbb222", 2000),
        )
        d = json.loads(to_json(r))
        assert d["old_file"]["path"] == "/old/lib.so"
        assert d["old_file"]["sha256"] == "aaa111"
        assert d["old_file"]["size_bytes"] == 1000
        assert d["new_file"]["path"] == "/new/lib.so"
        assert d["new_file"]["sha256"] == "bbb222"
        assert d["new_file"]["size_bytes"] == 2000

    def test_only_old_metadata(self):
        r = _result(old_meta=_meta())
        d = json.loads(to_json(r))
        assert d["old_file"] is not None
        assert d["new_file"] is None

    def test_metadata_position_in_json(self):
        """old_file/new_file appear before summary in key order."""
        r = _result(
            old_meta=_meta(),
            new_meta=_meta(),
        )
        text = to_json(r)
        d = json.loads(text)
        keys = list(d.keys())
        assert keys.index("old_file") < keys.index("summary")
        assert keys.index("new_file") < keys.index("summary")


class TestJsonImpact:
    def test_impact_in_change(self):
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "Public function removed: foo")
        d = json.loads(to_json(_result(changes=[c])))
        change = d["changes"][0]
        assert "impact" in change
        assert len(change["impact"]) > 0

    def test_source_location_in_change(self):
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed", source_location="foo.h:42")
        d = json.loads(to_json(_result(changes=[c])))
        assert d["changes"][0]["source_location"] == "foo.h:42"

    def test_affected_symbols_in_change(self):
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "MyStruct", "size changed",
                   affected_symbols=["func_a", "func_b"])
        d = json.loads(to_json(_result(changes=[c])))
        assert d["changes"][0]["affected_symbols"] == ["func_a", "func_b"]

    def test_no_source_location_omitted(self):
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        d = json.loads(to_json(_result(changes=[c])))
        assert "source_location" not in d["changes"][0]

    def test_no_affected_symbols_omitted(self):
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        d = json.loads(to_json(_result(changes=[c])))
        assert "affected_symbols" not in d["changes"][0]


class TestJsonDetectorTrimming:
    def test_zero_count_detectors_excluded(self):
        from abicheck.detectors import DetectorResult
        r = _result(verdict=Verdict.NO_CHANGE)
        r.detector_results = [
            DetectorResult(name="functions", changes_count=0, enabled=True),
            DetectorResult(name="types", changes_count=3, enabled=True),
            DetectorResult(name="elf", changes_count=0, enabled=True),
            DetectorResult(name="dwarf", changes_count=0, enabled=False, coverage_gap="missing"),
        ]
        d = json.loads(to_json(r))
        names = [det["name"] for det in d["detectors"]]
        assert "types" in names
        assert "dwarf" in names  # has coverage_gap
        assert "functions" not in names  # zero count, no gap
        assert "elf" not in names


# ---------------------------------------------------------------------------
# Markdown format
# ---------------------------------------------------------------------------

class TestMarkdownMetadata:
    def test_no_metadata_no_section(self):
        md = to_markdown(_result())
        assert "Library Files" not in md

    def test_metadata_table_present(self):
        r = _result(
            old_meta=_meta("/old.so", "a" * 64, 10240),
            new_meta=_meta("/new.so", "b" * 64, 20480),
        )
        md = to_markdown(r)
        assert "## Library Files" in md
        assert "/old.so" in md
        assert "/new.so" in md
        assert "aaaaaaaaaaaa" in md  # truncated SHA
        assert "bbbbbbbbbbbb" in md

    def test_size_formatting_bytes(self):
        r = _result(old_meta=_meta(size=500), new_meta=_meta(size=500))
        md = to_markdown(r)
        assert "500 B" in md

    def test_size_formatting_kb(self):
        r = _result(old_meta=_meta(size=2048), new_meta=_meta(size=2048))
        md = to_markdown(r)
        assert "2.0 KB" in md

    def test_size_formatting_mb(self):
        r = _result(old_meta=_meta(size=2 * 1024 * 1024), new_meta=_meta(size=2 * 1024 * 1024))
        md = to_markdown(r)
        assert "2.0 MB" in md


class TestMarkdownEnrichments:
    def test_impact_blockquote(self):
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "Public function removed: foo")
        md = to_markdown(_result(changes=[c]))
        # Impact should appear as a blockquote
        assert "> " in md

    def test_source_location_shown(self):
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed", source_location="header.h:10")
        md = to_markdown(_result(changes=[c]))
        assert "`header.h:10`" in md

    def test_affected_symbols_shown(self):
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "MyStruct", "size changed",
                   old_value="8", new_value="16",
                   affected_symbols=["api_create", "api_destroy"])
        md = to_markdown(_result(changes=[c]))
        assert "Affected symbols" in md
        assert "`api_create`" in md
        assert "`api_destroy`" in md

    def test_affected_symbols_truncated(self):
        syms = [f"func_{i}" for i in range(10)]
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "S", "changed", affected_symbols=syms)
        md = to_markdown(_result(changes=[c]))
        assert "+5 more" in md


# ---------------------------------------------------------------------------
# SARIF format
# ---------------------------------------------------------------------------

class TestSarifMetadata:
    def test_no_metadata_no_keys(self):
        from abicheck.sarif import to_sarif
        r = _result()
        sarif = to_sarif(r)
        props = sarif["runs"][0]["properties"]
        assert "oldFile" not in props
        assert "newFile" not in props

    def test_metadata_in_properties(self):
        from abicheck.sarif import to_sarif
        r = _result(
            old_meta=_meta("/old.so", "sha_old", 1024),
            new_meta=_meta("/new.so", "sha_new", 2048),
        )
        sarif = to_sarif(r)
        props = sarif["runs"][0]["properties"]
        assert props["oldFile"]["path"] == "/old.so"
        assert props["oldFile"]["sha256"] == "sha_old"
        assert props["oldFile"]["sizeBytes"] == 1024
        assert props["newFile"]["path"] == "/new.so"
        assert props["newFile"]["sha256"] == "sha_new"
        assert props["newFile"]["sizeBytes"] == 2048

    def test_impact_in_rule_description(self):
        from abicheck.sarif import to_sarif
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
        r = _result(changes=[c])
        sarif = to_sarif(r)
        rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
        # Should have a meaningful description, not generic
        assert len(rule["fullDescription"]["text"]) > 10

    def test_source_location_in_result(self):
        from abicheck.sarif import to_sarif
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed", source_location="api.h:99")
        r = _result(changes=[c])
        sarif = to_sarif(r)
        loc = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "api.h"
        assert loc["region"]["startLine"] == 99

    def test_affected_symbols_in_result_properties(self):
        from abicheck.sarif import to_sarif
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "S", "changed",
                   affected_symbols=["fn_a", "fn_b"])
        r = _result(changes=[c])
        sarif = to_sarif(r)
        props = sarif["runs"][0]["results"][0]["properties"]
        assert props["affectedSymbols"] == ["fn_a", "fn_b"]


# ---------------------------------------------------------------------------
# HTML format
# ---------------------------------------------------------------------------

class TestHtmlMetadata:
    def test_no_metadata_no_table(self):
        from abicheck.html_report import generate_html_report
        r = _result(verdict=Verdict.NO_CHANGE, changes=[])
        html = generate_html_report(r)
        assert "Library Files" not in html

    def test_metadata_table_present(self):
        from abicheck.html_report import generate_html_report
        r = _result(
            verdict=Verdict.NO_CHANGE,
            changes=[],
            old_meta=_meta("/old.so", "a" * 64, 4096),
            new_meta=_meta("/new.so", "b" * 64, 8192),
        )
        html = generate_html_report(r)
        assert "Library Files" in html
        assert "/old.so" in html
        assert "/new.so" in html
        assert "a" * 16 in html  # 16-char SHA truncation
        assert "4096" in html
        assert "8192" in html

    def test_compat_html_metadata(self):
        from abicheck.html_report import generate_html_report
        r = _result(
            verdict=Verdict.NO_CHANGE,
            changes=[],
            old_meta=_meta("/old.so", "aabbccdd", 1024),
            new_meta=_meta("/new.so", "eeff0011", 2048),
        )
        html = generate_html_report(r, compat_html=True)
        assert "Library Files" in html
        assert "/old.so" in html
        assert "/new.so" in html


class TestHtmlEnrichments:
    def test_impact_in_html(self):
        from abicheck.html_report import generate_html_report
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "Public function removed: foo")
        r = _result(changes=[c])
        html = generate_html_report(r)
        # Impact text is shown with lightbulb
        assert "💡" in html

    def test_affected_symbols_in_html(self):
        from abicheck.html_report import generate_html_report
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "S", "size changed",
                   affected_symbols=["api_call"])
        r = _result(changes=[c])
        html = generate_html_report(r)
        assert "📎" in html
        assert "api_call" in html

    def test_source_location_in_html(self):
        from abicheck.html_report import generate_html_report
        c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed", source_location="foo.h:42")
        r = _result(changes=[c])
        html = generate_html_report(r)
        assert "📍" in html
        assert "foo.h:42" in html


# ---------------------------------------------------------------------------
# CLI metadata collection
# ---------------------------------------------------------------------------

class TestCliMetadataCollection:
    def test_collect_metadata(self):
        from abicheck.cli import _collect_metadata
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as f:
            f.write(b"fake ELF content for testing")
            f.flush()
            path = Path(f.name)

        try:
            meta = _collect_metadata(path)
            assert meta.path == str(path)
            expected_sha = hashlib.sha256(b"fake ELF content for testing").hexdigest()
            assert meta.sha256 == expected_sha
            assert meta.size_bytes == len(b"fake ELF content for testing")
        finally:
            path.unlink()

    def test_collect_metadata_large_file(self):
        from abicheck.cli import _collect_metadata
        content = b"x" * 100_000
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as f:
            f.write(content)
            f.flush()
            path = Path(f.name)

        try:
            meta = _collect_metadata(path)
            assert meta.size_bytes == 100_000
            assert len(meta.sha256) == 64  # SHA-256 hex digest length
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_ast_dwarf_dedup(self):
        """DWARF findings that duplicate AST findings are removed."""
        from abicheck.checker import _deduplicate_ast_dwarf

        # TYPE_SIZE_CHANGED (AST tier) is bit-based (RecordType.size_bits);
        # STRUCT_SIZE_CHANGED (DWARF tier) is byte-based (DW_AT_byte_size) --
        # 8/16 bytes == 64/128 bits, a genuinely identical transition.
        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "MyStruct", "Type size changed: MyStruct (64 → 128)",
                   old_value="64", new_value="128"),
            Change(ChangeKind.STRUCT_SIZE_CHANGED, "MyStruct", "Type size changed: MyStruct (8 → 16)",
                   old_value="8", new_value="16"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.TYPE_SIZE_CHANGED

    def test_ast_dwarf_conflicting_transition_not_deduped(self):
        """A DWARF finding whose own transition disagrees with the AST
        finding's must survive, not be silently dropped (Codex review): a
        stale header or inconsistent extractor evidence can make the two
        tiers report genuinely different old/new values for the same kind
        and symbol, and hiding one is worse than showing both."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(ChangeKind.TYPE_SIZE_CHANGED, "MyStruct", "Type size changed: MyStruct (64 → 128)",
                   old_value="64", new_value="128"),
            # DWARF reports 64 -> 96 bits (8 -> 12 bytes) -- a real
            # disagreement with the AST finding's 64 -> 128, not a
            # duplicate.
            Change(ChangeKind.STRUCT_SIZE_CHANGED, "MyStruct", "Type size changed: MyStruct (8 → 12)",
                   old_value="8", new_value="12"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 2

    def test_field_type_transition_disagreeing_on_indirection_not_deduped(self):
        """Codex review: comparing a field-type transition via
        `_normalize_type_name` (which strips trailing `*`/`&`) made
        `Foo * -> Bar *` (DWARF) and `Foo -> Bar` (header) compare equal,
        hiding a genuine indirection-level disagreement between the two
        tiers. `cross_tier_transition` must preserve `*`/`&` for this
        comparison even though it still bridges tag-keyword spelling."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(ChangeKind.TYPE_FIELD_TYPE_CHANGED, "S::a",
                   "Field type changed: S::a", old_value="Foo", new_value="Bar"),
            # DWARF says both sides are pointers -- a real disagreement
            # with the header's by-value Foo -> Bar, not a duplicate.
            Change(ChangeKind.STRUCT_FIELD_TYPE_CHANGED, "S::a",
                   "Field type changed: S::a", old_value="Foo *", new_value="Bar *"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 2

    def test_field_type_transition_agreeing_modulo_tag_keyword_still_deduped(self):
        """The bridge this whole comparison exists for must still work:
        a tag-keyword spelling difference ("struct Foo" vs "Foo") alone
        is not a real disagreement and still collapses to one finding."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(ChangeKind.TYPE_FIELD_TYPE_CHANGED, "S::a",
                   "Field type changed: S::a", old_value="Foo", new_value="Bar"),
            Change(ChangeKind.STRUCT_FIELD_TYPE_CHANGED, "S::a",
                   "Field type changed: S::a",
                   old_value="struct Foo", new_value="struct Bar"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 1

    def test_field_type_transition_agreeing_modulo_indirection_whitespace_still_deduped(self):
        """Codex review, fresh evidence: preserving `*`/`&` for the
        indirection fix above still needs their surrounding whitespace
        normalized, or a pure extractor-spelling difference ("Foo*" vs
        "Foo *" vs "struct Foo * ") reads as a false indirection-level
        disagreement and blocks a dedup that should still happen."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(ChangeKind.TYPE_FIELD_TYPE_CHANGED, "S::a",
                   "Field type changed: S::a", old_value="Foo*", new_value="Bar*"),
            Change(ChangeKind.STRUCT_FIELD_TYPE_CHANGED, "S::a",
                   "Field type changed: S::a",
                   old_value="struct Foo * ", new_value="struct Bar * "),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 1

    def test_a_kind_carrying_list_valued_old_new_does_not_crash(self):
        """Regression: `_dedup_cross_kind` used to index every change's
        `cross_tier_transition()` unconditionally, including kinds outside
        `_DWARF_TO_AST_EQUIV` entirely (e.g. `PYTHON_STABLE_ABI_VIOLATION`,
        whose `new_value` is a `list[str]` despite `Change.old_value`/
        `new_value`'s declared `str | None` type) -- a list is unhashable,
        so building the lookup set raised `TypeError: unhashable type:
        'list'` for any comparison containing such a finding. Only the
        AST-tier kinds this dedup actually looks up need indexing at all."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(
                ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
                "python:foo",
                "Unstable ABI import",
                new_value=["PyObject_Foo"],
            ),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 1

    def test_exact_dedup_same_kind(self):
        """Exact duplicates by (kind, description) are removed."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(ChangeKind.FUNC_REMOVED, "foo", "Public function removed: foo"),
            Change(ChangeKind.FUNC_REMOVED, "foo", "Public function removed: foo"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 1

    def test_different_descriptions_kept(self):
        """Changes with same kind but different descriptions are kept."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(ChangeKind.FUNC_REMOVED, "foo", "Public function removed: foo"),
            Change(ChangeKind.FUNC_REMOVED, "bar", "Public function removed: bar"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 2

    def test_different_fields_not_collapsed(self):
        """DWARF findings for different fields of the same struct must not be collapsed."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            # AST found offset change for field S::a
            Change(ChangeKind.TYPE_FIELD_OFFSET_CHANGED, "S::a",
                   "Field offset changed: S::a (0 → 8)", old_value="0", new_value="8"),
            # DWARF found offset change for field S::b — different field, must keep
            Change(ChangeKind.STRUCT_FIELD_OFFSET_CHANGED, "S::b",
                   "Field offset changed: S::b (8 → 16)", old_value="8", new_value="16"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 2
        symbols = {c.symbol for c in result}
        assert "S::a" in symbols
        assert "S::b" in symbols

    def test_same_field_ast_dwarf_collapsed(self):
        """DWARF finding for same field as AST should be collapsed."""
        from abicheck.checker import _deduplicate_ast_dwarf

        # TYPE_FIELD_OFFSET_CHANGED (AST tier) is bit-based (offset_bits);
        # STRUCT_FIELD_OFFSET_CHANGED (DWARF tier) is byte-based
        # (byte_offset) -- 0/8 bytes == 0/64 bits.
        changes = [
            Change(ChangeKind.TYPE_FIELD_OFFSET_CHANGED, "S::a",
                   "Field offset changed: S::a (0 → 64)", old_value="0", new_value="64"),
            Change(ChangeKind.STRUCT_FIELD_OFFSET_CHANGED, "S::a",
                   "Field offset changed: S::a (0 → 8)", old_value="0", new_value="8"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 1
        assert result[0].kind == ChangeKind.TYPE_FIELD_OFFSET_CHANGED

    def test_conflicting_field_offset_transition_not_deduped(self):
        """Same field-offset symbol, disagreeing transitions -> keep both
        (Codex review), mirroring the whole-struct-size case above."""
        from abicheck.checker import _deduplicate_ast_dwarf

        changes = [
            Change(ChangeKind.TYPE_FIELD_OFFSET_CHANGED, "S::a",
                   "Field offset changed: S::a (0 → 64)", old_value="0", new_value="64"),
            # DWARF reports 0 -> 4 bytes (32 bits), not 0 -> 8 bytes (64 bits).
            Change(ChangeKind.STRUCT_FIELD_OFFSET_CHANGED, "S::a",
                   "Field offset changed: S::a (0 → 4)", old_value="0", new_value="4"),
        ]
        result = _deduplicate_ast_dwarf(changes)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Markdown falsey value handling
# ---------------------------------------------------------------------------

class TestMarkdownFalseyValues:
    def test_zero_old_value_shown(self):
        """old_value='0' (falsey but not None) should be displayed."""
        c = Change(ChangeKind.TYPE_FIELD_OFFSET_CHANGED, "S::x",
                   "offset changed", old_value="0", new_value="8")
        md = to_markdown(_result(changes=[c]))
        assert "`0`" in md
        assert "`8`" in md

    def test_empty_string_old_value_shown(self):
        """old_value='' (falsey but not None) should be displayed."""
        c = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo",
                   "params changed", old_value="", new_value="int")
        md = to_markdown(_result(changes=[c]))
        assert "→" in md
        assert "`int`" in md

    def test_new_only_value_shown(self):
        """When old_value is None but new_value has a value, new_value should appear."""
        c = Change(ChangeKind.FUNC_RETURN_CHANGED, "foo",
                   "return type changed", new_value="int")
        md = to_markdown(_result(changes=[c]))
        assert "`int`" in md


# ---------------------------------------------------------------------------
# PolicyEntry.impact populated
# ---------------------------------------------------------------------------

class TestPolicyEntryImpact:
    def test_impact_populated_for_breaking_kinds(self):
        from abicheck.checker_policy import policy_for
        entry = policy_for(ChangeKind.FUNC_REMOVED)
        assert entry.impact != ""
        assert "symbol" in entry.impact.lower() or "crash" in entry.impact.lower() or "linker" in entry.impact.lower()

    def test_impact_populated_for_type_changes(self):
        from abicheck.checker_policy import policy_for
        entry = policy_for(ChangeKind.TYPE_SIZE_CHANGED)
        assert entry.impact != ""

    def test_impact_matches_impact_for(self):
        from abicheck.checker_policy import impact_for, policy_for
        for kind in [ChangeKind.FUNC_REMOVED, ChangeKind.TYPE_SIZE_CHANGED,
                     ChangeKind.ENUM_MEMBER_VALUE_CHANGED]:
            assert policy_for(kind).impact == impact_for(kind)

    def test_compatible_kind_impact(self):
        from abicheck.checker_policy import policy_for
        entry = policy_for(ChangeKind.FUNC_ADDED)
        assert entry.impact != ""
        assert "unaffected" in entry.impact.lower()

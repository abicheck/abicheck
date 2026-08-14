"""Direct unit tests for suppression_yaml.py's raw-scalar YAML helpers.

Complements the end-to-end SuppressionList.load() regression tests in
test_canonical_finding_id.py (TestFindingIdSuppressionSelector) by
exercising raw_finding_ids_by_index's own edge cases directly -- malformed
input, non-mapping documents, and structural shapes that don't reach the
happy path.
"""

from __future__ import annotations

from abicheck.suppression_yaml import parse_finding_id, raw_finding_ids_by_index


class TestRawFindingIdsByIndex:
    def test_malformed_yaml_returns_empty(self):
        assert raw_finding_ids_by_index("version: 1\n  bad: [unterminated") == {}

    def test_non_mapping_document_returns_empty(self):
        assert raw_finding_ids_by_index("- just\n- a\n- list\n") == {}

    def test_no_suppressions_key_returns_empty(self):
        assert raw_finding_ids_by_index("version: 1\n") == {}

    def test_suppressions_not_a_sequence_returns_empty(self):
        assert raw_finding_ids_by_index("version: 1\nsuppressions: not-a-list\n") == {}

    def test_non_mapping_entry_is_skipped(self):
        text = "version: 1\nsuppressions:\n  - just-a-string\n  - finding_id: '123'\n"
        assert raw_finding_ids_by_index(text) == {1: "123"}

    def test_entry_without_finding_id_is_absent_from_result(self):
        text = "version: 1\nsuppressions:\n  - symbol: foo\n    reason: r\n"
        assert raw_finding_ids_by_index(text) == {}

    def test_duplicate_direct_key_last_one_wins(self):
        # Regression (Codex review, PR #753, round 7): yaml.safe_load()
        # itself resolves a duplicate mapping key to the LAST value --
        # returning on the first direct match here would silently disagree
        # with the already-loaded, safe_load-produced mapping this result
        # gets merged into in SuppressionList.load.
        text = (
            "version: 1\nsuppressions:\n"
            "  - finding_id: '111'\n    finding_id: '222'\n    reason: dup\n"
        )
        assert raw_finding_ids_by_index(text) == {0: "222"}

    def test_sequence_merge_first_source_wins(self):
        # YAML merge spec: for `<<: [*a, *b]`, an earlier source in the
        # sequence takes precedence over a later one for a duplicate key.
        text = (
            "a: &a {finding_id: '111'}\n"
            "b: &b {finding_id: '222'}\n"
            "suppressions:\n"
            "  - <<: [*a, *b]\n"
            "    reason: r\n"
        )
        assert raw_finding_ids_by_index(text) == {0: "111"}

    def test_two_separate_merge_key_occurrences_last_one_wins(self):
        # Regression (Codex review, PR #753, round 9): `<<: *a` followed by
        # a SEPARATE `<<: *b` (not one `<<: [*a, *b]` sequence) mirrors
        # real dict-update semantics -- the later merge overwrites the
        # earlier one for a key both define (verified directly against
        # yaml.safe_load). An earlier revision's `merged is None` guard
        # kept only the first occurrence's resolution.
        text = (
            "version: 1\n"
            "a: &a\n  finding_id: '111'\n"
            "b: &b\n  finding_id: '222'\n"
            "suppressions:\n"
            "  - <<: *a\n    <<: *b\n    reason: r\n"
        )
        assert raw_finding_ids_by_index(text) == {0: "222"}

    def test_second_merge_without_the_key_does_not_clear_the_first(self):
        # A later merge whose own source never defines finding_id at all
        # must not overwrite an earlier merge's real value -- a dict
        # update only touches keys the update actually contains.
        text = (
            "version: 1\n"
            "a: &a\n  finding_id: '111'\n"
            "b: &b\n  reason: unrelated\n"
            "suppressions:\n"
            "  - <<: *a\n    <<: *b\n    reason: r\n"
        )
        assert raw_finding_ids_by_index(text) == {0: "111"}

    def test_second_merge_explicitly_nulling_the_key_clears_the_first(self):
        # Regression (Codex review, PR #753, round 10): a LATER merge
        # source that explicitly sets finding_id: null must clear an
        # earlier merge's real value, matching real yaml.safe_load
        # dict-update semantics (verified directly against PyYAML) -- an
        # earlier revision couldn't tell "this source resolves to None
        # because it explicitly nulls the key" apart from "this source
        # doesn't mention the key at all", so it wrongly kept the earlier
        # value in place instead of clearing it.
        text = (
            "version: 1\n"
            "o: &o\n  finding_id: '123'\n"
            "d: &d\n  finding_id: null\n"
            "suppressions:\n"
            "  - <<: *o\n    <<: *d\n    reason: r\n"
        )
        assert raw_finding_ids_by_index(text) == {}

    def test_duplicate_top_level_suppressions_key_last_one_wins(self):
        # Regression (Codex review, PR #753, round 8): yaml.safe_load()
        # resolves a duplicate top-level key to the LAST value too -- an
        # earlier revision picked the FIRST `suppressions:` sequence via
        # next(...), which could raw-extract from a different sequence
        # than the one safe_load actually used, mismatching indices.
        text = (
            "version: 1\nsuppressions:\n  - finding_id: '111'\n"
            "suppressions:\n  - finding_id: '222'\n"
        )
        assert raw_finding_ids_by_index(text) == {0: "222"}

    def test_explicit_null_finding_id_returns_none_not_the_literal_text(self):
        # Regression (Codex review, PR #753, round 8): `finding_id: null`/
        # `~`/a bare `finding_id:` all resolve to YAML's null tag, but the
        # raw *written* text is "null"/"~"/"" respectively -- reading
        # ScalarNode.value unconditionally would turn an explicit null
        # back into a non-empty string, passing selector validation as a
        # real finding_id that can never match anything.
        text = (
            "version: 1\nsuppressions:\n"
            "  - finding_id: null\n    reason: a\n"
            "  - finding_id: ~\n    reason: b\n"
            "  - finding_id:\n    reason: c\n"
        )
        assert raw_finding_ids_by_index(text) == {}

    def test_explicit_null_overrides_a_merged_value(self):
        # A direct key -- even an explicit null -- always overrides a
        # merged default, matching real yaml.safe_load dict construction
        # (verified directly against PyYAML while designing this fix).
        text = (
            "d: &d {finding_id: '111'}\n"
            "suppressions:\n"
            "  - <<: *d\n"
            "    finding_id: null\n"
            "    reason: r\n"
        )
        assert raw_finding_ids_by_index(text) == {}


class TestParseFindingId:
    def test_none_stays_none(self):
        assert parse_finding_id(None) is None

    def test_string_passes_through(self):
        assert parse_finding_id("abc123") == "abc123"

    def test_non_string_is_coerced(self):
        assert parse_finding_id(12345) == "12345"

    def test_empty_string_normalizes_to_none(self):
        # Regression (Codex review, PR #753, round 7): an explicit
        # finding_id: "" would otherwise pass Suppression.__post_init__'s
        # `is not None` selector check as a real, standalone-sufficient
        # selector that can never match any real finding -- a rule that
        # loads successfully but is permanently dead.
        assert parse_finding_id("") is None

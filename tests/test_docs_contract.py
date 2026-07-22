# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Fast-lane wrapper + unit tests for scripts/check_docs_contract.py.

The gate logic lives in the script so it's runnable standalone in CI; this
mirrors it into the pytest suite (matching tests/test_usecase_docs_sync.py's
pattern) so a broken docs/AGENTS.md ownership contract fails the ordinary
unit-test lane too, not just a separate CI step. The unit tests below
monkeypatch the script's module-level ``DOCS`` constant to point at a
tmp_path fixture, since the ownership/front-matter checks take their topic
registry as a plain parameter but resolve page paths against that global.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_docs_contract.py"
)
_spec = importlib.util.spec_from_file_location("check_docs_contract", _GATE_PATH)
assert _spec and _spec.loader
dc = importlib.util.module_from_spec(_spec)
sys.modules["check_docs_contract"] = dc
_spec.loader.exec_module(dc)


# --- real-repo smoke test -------------------------------------------------


def test_docs_contract_has_no_ownership_errors_on_real_repo() -> None:
    """The ownership/front-matter checks (ERROR-level) must pass against the
    actual docs/_meta/topics.yaml and docs/ tree. The advisory duplicate-block
    scan is intentionally not asserted here — it's WARN-only by design."""
    f = dc.Findings()
    topics = dc._load_topics(f)
    assert topics is not None, f.errors
    dc._check_referenced_paths_exist(f, topics)
    dc._check_canonical_page_uniqueness(f, topics)
    dc._check_front_matter_schema(f, topics)
    dc._check_canonical_pages_declare_ownership(f, topics)
    assert f.errors == [], "\n".join(f"{c}: {m}" for c, m in f.errors)


# --- _check_canonical_page_uniqueness (pure, no filesystem) ---------------


def test_canonical_page_uniqueness_flags_two_topics_claiming_one_page() -> None:
    topics = {
        "topic-a": {"canonical_page": "x.md"},
        "topic-b": {"canonical_page": "x.md"},
    }
    f = dc.Findings()
    dc._check_canonical_page_uniqueness(f, topics)
    assert len(f.errors) == 1
    assert "topic-a" in f.errors[0][1] and "topic-b" in f.errors[0][1]


def test_canonical_page_uniqueness_allows_distinct_pages() -> None:
    topics = {
        "topic-a": {"canonical_page": "x.md"},
        "topic-b": {"canonical_page": "y.md"},
    }
    f = dc.Findings()
    dc._check_canonical_page_uniqueness(f, topics)
    assert f.errors == []


def test_canonical_page_uniqueness_flags_equivalent_spellings() -> None:
    """`concepts/x.md` and `./concepts/x.md` name the same file — the
    uniqueness check must normalize before comparing, or two differently
    spelled entries silently bypass the one-owner rule (regression test for
    the gap flagged in PR #619 review)."""
    topics = {
        "topic-a": {"canonical_page": "concepts/x.md"},
        "topic-b": {"canonical_page": "./concepts/x.md"},
    }
    f = dc.Findings()
    dc._check_canonical_page_uniqueness(f, topics)
    assert len(f.errors) == 1
    assert "topic-a" in f.errors[0][1] and "topic-b" in f.errors[0][1]


def test_docs_relative_key_normalizes_equivalent_spellings() -> None:
    assert dc._docs_relative_key("./concepts/x.md") == dc._docs_relative_key(
        "concepts/x.md"
    )


# --- _check_referenced_paths_exist ----------------------------------------


def test_referenced_paths_exist_flags_missing_canonical_page() -> None:
    topics = {"topic-a": {"canonical_page": "does/not/exist.md"}}
    f = dc.Findings()
    dc._check_referenced_paths_exist(f, topics)
    assert len(f.errors) == 1
    assert "does/not/exist.md" in f.errors[0][1]


def test_referenced_paths_exist_flags_missing_fact_source() -> None:
    topics = {
        "topic-a": {
            "canonical_page": "index.md",  # docs/index.md is real
            "fact_sources": ["abicheck/definitely_not_a_real_module.py"],
        }
    }
    f = dc.Findings()
    dc._check_referenced_paths_exist(f, topics)
    assert len(f.errors) == 1
    assert "definitely_not_a_real_module.py" in f.errors[0][1]


def test_referenced_paths_exist_rejects_canonical_page_escaping_docs_via_dotdot() -> (
    None
):
    """A topic can't claim a canonical_page outside docs/ via '../' even if
    the escaped file happens to exist (regression test for the traversal gap
    flagged in PR #619 review — `DOCS / "../README.md"` used to resolve to
    the real repo-root README.md and pass the existence check)."""
    topics = {"topic-a": {"canonical_page": "../README.md"}}
    f = dc.Findings()
    dc._check_referenced_paths_exist(f, topics)
    assert len(f.errors) == 1
    assert "escapes it" in f.errors[0][1]


def test_referenced_paths_exist_rejects_absolute_path() -> None:
    """pathlib's `/` operator honors an absolute right-hand side outright
    (`DOCS / "/etc/passwd" == Path("/etc/passwd")`), silently discarding
    DOCS — must be rejected regardless of whether the absolute target
    exists, not treated as a valid in-tree path."""
    topics = {"topic-a": {"canonical_page": "/definitely-not-a-real-path-xyz"}}
    f = dc.Findings()
    dc._check_referenced_paths_exist(f, topics)
    assert len(f.errors) == 1
    assert "escapes it" in f.errors[0][1]


def test_resolves_under_accepts_a_real_in_tree_path(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "page.md").write_text("x", encoding="utf-8")
    assert (
        dc._resolves_under(tmp_path, "sub/page.md")
        == (tmp_path / "sub" / "page.md").resolve()
    )


def test_resolves_under_rejects_dotdot_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "definitely-not-under-tmp_path.md"
    rel = str(Path("..") / outside.name)
    assert dc._resolves_under(tmp_path, rel) is None


def test_resolves_under_rejects_absolute_value_even_if_it_resolves_inside_base(
    tmp_path: Path,
) -> None:
    """An absolute path is rejected outright, even when it happens to
    resolve under `base` on this machine/checkout — the topics.yaml schema
    is relative paths only, and an absolute one would silently stop
    resolving under `base` on any other checkout (regression test for the
    gap flagged in PR #619 review)."""
    (tmp_path / "page.md").write_text("x", encoding="utf-8")
    machine_local_absolute = str(tmp_path / "page.md")
    assert dc._resolves_under(tmp_path, machine_local_absolute) is None


# --- front matter: parsing --------------------------------------------------


def test_load_front_matter_returns_none_without_a_block(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("# Title\n\nBody text.\n", encoding="utf-8")
    assert dc.load_front_matter(page) is None


def test_load_front_matter_parses_yaml_block(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text(
        "---\ndoc_type: how-to\ncanonical_for:\n  - foo\n---\n\n# Title\n",
        encoding="utf-8",
    )
    fm = dc.load_front_matter(page)
    assert fm == {"doc_type": "how-to", "canonical_for": ["foo"]}


def test_load_front_matter_raises_on_malformed_yaml(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("---\n[unterminated\n---\n\n# Title\n", encoding="utf-8")
    with pytest.raises(Exception):  # yaml.YAMLError  # noqa: B017
        dc.load_front_matter(page)


# --- front matter: schema + ownership cross-checks (monkeypatched DOCS) ---


def test_front_matter_schema_flags_unknown_doc_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\ndoc_type: not-a-real-type\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})
    assert any("doc_type" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_canonical_for_pointing_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\ndoc_type: explanation\ncanonical_for:\n  - topic-a\n---\n\n# Title\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "other.md"}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert any("canonical_for" in msg for _, msg in f.errors)


def test_front_matter_schema_accepts_canonical_for_via_equivalent_spelling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The registry's canonical_page can be spelled `./page.md`; the page's
    own front matter names itself as plain `page.md` — these must compare
    equal, not fail the round-trip check on a cosmetic spelling mismatch."""
    (tmp_path / "page.md").write_text(
        "---\ndoc_type: explanation\ncanonical_for:\n  - topic-a\n---\n\n# Title\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "./page.md"}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert f.errors == []


def test_front_matter_schema_flags_unknown_summarizes_topic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\nsummarizes:\n  - nonexistent-topic\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})
    assert any("summarizes" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_summarizes_page_not_registered_for_topic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A page can't grant itself permission to restate a topic just by
    adding `summarizes` — it must actually be registered as that topic's
    worked_example/task_pages/reference_page/allowed_summaries (regression
    test for the gap flagged in PR #619 review)."""
    (tmp_path / "page.md").write_text(
        "---\nsummarizes:\n  - topic-a\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {
        "topic-a": {
            "canonical_page": "owner.md",
            "task_pages": ["some-other-page.md"],
        }
    }
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert any("not registered as that topic's" in msg for _, msg in f.errors)


@pytest.mark.parametrize(
    "role_key,role_value",
    [
        ("worked_example", "page.md"),
        ("reference_page", "page.md"),
        ("task_pages", ["page.md"]),
        ("allowed_summaries", ["page.md"]),
    ],
)
def test_front_matter_schema_accepts_summarizes_for_each_permitted_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role_key: str,
    role_value: object,
) -> None:
    (tmp_path / "page.md").write_text(
        "---\nsummarizes:\n  - topic-a\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md", role_key: role_value}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert f.errors == []


def test_front_matter_schema_accepts_valid_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\ndoc_type: explanation\nlevel: intermediate\nlifecycle: active\n"
        "canonical_for:\n  - topic-a\n---\n\n# Title\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "page.md"}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert f.errors == []


def test_canonical_pages_declare_ownership_warns_on_missing_front_matter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "owner.md").write_text(
        "# Title\n\nNo front matter here.\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md"}}
    f = dc.Findings()
    dc._check_canonical_pages_declare_ownership(f, topics)
    assert f.errors == []
    assert len(f.warnings) == 1


def test_canonical_pages_declare_ownership_errors_when_front_matter_omits_topic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "owner.md").write_text(
        "---\ndoc_type: explanation\ncanonical_for: []\n---\n\n# Title\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md"}}
    f = dc.Findings()
    dc._check_canonical_pages_declare_ownership(f, topics)
    assert len(f.errors) == 1
    assert "topic-a" in f.errors[0][1]


# --- duplicate-block scan (advisory) ---------------------------------------


def test_extract_blocks_drops_headings_and_fenced_code() -> None:
    text = (
        "# Heading\n\n"
        "```python\nsome code that should not count as prose\n```\n\n"
        "A real paragraph with enough words to matter for the scan.\n"
    )
    blocks = dc._extract_blocks(text)
    assert blocks == ["A real paragraph with enough words to matter for the scan."]


def test_extract_blocks_strips_front_matter() -> None:
    text = "---\ndoc_type: how-to\n---\n\nBody paragraph.\n"
    blocks = dc._extract_blocks(text)
    assert blocks == ["Body paragraph."]


def test_duplicate_paragraph_scan_flags_identical_long_block_in_two_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    long_block = " ".join(f"word{i}" for i in range(45))
    (tmp_path / "a.md").write_text(f"# A\n\n{long_block}\n", encoding="utf-8")
    (tmp_path / "b.md").write_text(f"# B\n\n{long_block}\n", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_duplicate_paragraphs(f)
    assert f.errors == []
    assert len(f.warnings) == 1
    assert "a.md" in f.warnings[0][1] and "b.md" in f.warnings[0][1]


def test_duplicate_paragraph_scan_ignores_short_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    short_block = "word0 word1 word2"
    (tmp_path / "a.md").write_text(f"# A\n\n{short_block}\n", encoding="utf-8")
    (tmp_path / "b.md").write_text(f"# B\n\n{short_block}\n", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_duplicate_paragraphs(f)
    assert f.warnings == []


def test_duplicate_paragraph_scan_excludes_generated_case_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    long_block = " ".join(f"word{i}" for i in range(45))
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "case01_foo.md").write_text(
        f"# Case\n\n{long_block}\n", encoding="utf-8"
    )
    (tmp_path / "other.md").write_text(f"# Other\n\n{long_block}\n", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_duplicate_paragraphs(f)
    assert f.warnings == []

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


def test_canonical_page_uniqueness_handles_non_string_topic_ids() -> None:
    """A topics.yaml mapping key need not be a str (e.g. a malformed `123:`
    entry) -- sorted()/join() on the raw topic ids would crash with a
    TypeError instead of reporting the duplicate-owner error (regression
    test for the gap flagged in PR #619 review)."""
    topics = {
        123: {"canonical_page": "x.md"},
        "topic-b": {"canonical_page": "x.md"},
    }
    f = dc.Findings()
    dc._check_canonical_page_uniqueness(f, topics)  # must not raise
    assert len(f.errors) == 1
    assert "123" in f.errors[0][1] and "topic-b" in f.errors[0][1]


def test_canonical_page_uniqueness_allows_distinct_pages() -> None:
    topics = {
        "topic-a": {"canonical_page": "x.md"},
        "topic-b": {"canonical_page": "y.md"},
    }
    f = dc.Findings()
    dc._check_canonical_page_uniqueness(f, topics)
    assert f.errors == []


def test_canonical_page_uniqueness_flags_equivalent_spellings() -> None:
    """`index.md` and `./index.md` name the same file — the uniqueness check
    must normalize before comparing, or two differently spelled entries
    silently bypass the one-owner rule (regression test for the gap flagged
    in PR #619 review). Uses a real docs/ file (index.md) since normalization
    requires the path to actually exist (a later fix rejected phantom
    nonexistent-component paths — see test_resolves_under_rejects_phantom_
    intermediate_component below)."""
    topics = {
        "topic-a": {"canonical_page": "index.md"},
        "topic-b": {"canonical_page": "./index.md"},
    }
    f = dc.Findings()
    dc._check_canonical_page_uniqueness(f, topics)
    assert len(f.errors) == 1
    assert "topic-a" in f.errors[0][1] and "topic-b" in f.errors[0][1]


def test_docs_relative_key_normalizes_equivalent_spellings() -> None:
    assert dc._docs_relative_key("./index.md") == dc._docs_relative_key("index.md")


# --- _check_referenced_paths_exist ----------------------------------------


def test_referenced_paths_exist_flags_missing_canonical_page() -> None:
    topics = {"topic-a": {"canonical_page": "does/not/exist.md"}}
    f = dc.Findings()
    dc._check_referenced_paths_exist(f, topics)
    assert len(f.errors) == 1
    assert "does/not/exist.md" in f.errors[0][1]


def test_referenced_paths_exist_flags_null_canonical_page() -> None:
    """`canonical_page:` with no value parses as YAML null — the required-
    field check must treat that as missing, not as "key present, optional
    value absent, skip validation" (regression test for the gap flagged in
    PR #619 review: a topic with a null canonical_page previously passed
    silently with no narrative owner at all)."""
    topics = {"topic-a": {"canonical_page": None}}
    f = dc.Findings()
    dc._check_referenced_paths_exist(f, topics)
    assert len(f.errors) == 1
    assert "missing required 'canonical_page'" in f.errors[0][1]


def test_canonical_page_uniqueness_ignores_topic_with_null_canonical_page() -> None:
    topics = {"topic-a": {"canonical_page": None}, "topic-b": {"canonical_page": None}}
    f = dc.Findings()
    dc._check_canonical_page_uniqueness(f, topics)
    # Both null -- not a real duplicate-ownership conflict (that's reported
    # separately, as two missing-required-field errors by
    # _check_referenced_paths_exist).
    assert f.errors == []


def test_canonical_pages_declare_ownership_ignores_null_canonical_page() -> None:
    topics = {"topic-a": {"canonical_page": None}}
    f = dc.Findings()
    dc._check_canonical_pages_declare_ownership(f, topics)
    assert f.errors == []
    assert f.warnings == []


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
    # Actually create the escaped target so this tests the escape-boundary
    # check specifically, not mere nonexistence (which _resolves_under also
    # rejects, but for an unrelated reason — see the strict-resolution test
    # below).
    outside = tmp_path.parent / "definitely-not-under-tmp_path.md"
    outside.write_text("x", encoding="utf-8")
    rel = str(Path("..") / outside.name)
    assert dc._resolves_under(tmp_path, rel) is None


def test_resolves_under_rejects_phantom_intermediate_component(
    tmp_path: Path,
) -> None:
    """A non-strict Path.resolve() lexically collapses '..' even through a
    directory component that was never created — e.g. `missing/../index.md`
    resolves straight to the real `index.md` even though `missing/` doesn't
    exist. That would let a broken topics.yaml entry pass the existence
    check and then crash a downstream caller that opens the raw (unresolved)
    path directly instead (regression test for the gap flagged in PR #619
    review)."""
    (tmp_path / "index.md").write_text("x", encoding="utf-8")
    assert dc._resolves_under(tmp_path, "missing/../index.md") is None


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


def test_load_front_matter_raises_value_error_on_non_mapping(tmp_path: Path) -> None:
    """Front matter that's valid YAML but not a mapping (e.g. a bare list)
    must not be silently treated as an empty-but-valid block — that would
    let malformed front matter pass the gate with no error (regression test
    for the gap flagged in PR #619 review)."""
    page = tmp_path / "page.md"
    page.write_text("---\n- a\n- b\n---\n\n# Title\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        dc.load_front_matter(page)


def test_load_front_matter_treats_empty_block_as_empty_dict(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("---\n\n---\n\n# Title\n", encoding="utf-8")
    assert dc.load_front_matter(page) == {}


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


def test_front_matter_schema_flags_non_scalar_doc_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`doc_type: [how-to]` (a list, not a scalar) is unhashable -- checking
    membership in the allowed-values frozenset would crash with a TypeError
    before the gate can report anything (regression test for the gap flagged
    in PR #619 review)."""
    (tmp_path / "page.md").write_text(
        "---\ndoc_type:\n  - how-to\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})  # must not raise
    assert any("doc_type must be a string" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_non_scalar_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\nlevel:\n  beginner: true\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})  # must not raise
    assert any("level must be a string" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_non_scalar_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\nlifecycle:\n  - active\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})  # must not raise
    assert any("lifecycle must be a string" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_scalar_audience(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`audience` is documented as list-valued; a bare scalar must be
    flagged, not silently accepted (regression test for the gap flagged in
    PR #619 review)."""
    (tmp_path / "page.md").write_text(
        "---\naudience: library-maintainer\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})
    assert any("audience" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_scalar_depends_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\ndepends_on: abicheck/model.py\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})
    assert any("depends_on" in msg for _, msg in f.errors)


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


def test_front_matter_schema_flags_canonical_for_referencing_malformed_topic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A topics.yaml entry that's a scalar/list instead of a mapping (e.g.
    `verdicts: concepts/verdicts.md`) must not crash the gate with an
    AttributeError when a page's canonical_for references it — it should
    report a clean error instead (regression test for the gap flagged in
    PR #619 review)."""
    (tmp_path / "page.md").write_text(
        "---\ncanonical_for:\n  - topic-a\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": "concepts/verdicts.md"}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)  # must not raise
    assert any("not a mapping" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_summarizes_referencing_malformed_topic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\nsummarizes:\n  - topic-a\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": ["not", "a", "mapping"]}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)  # must not raise
    assert any("not a mapping" in msg for _, msg in f.errors)


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


def test_front_matter_schema_flags_non_string_canonical_for_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unhashable canonical_for entry (e.g. an accidental YAML mapping
    item, "- {bad: value}") must not crash topics.get() with a TypeError --
    it should report a clean schema error instead (regression test for the
    gap flagged in PR #619 review)."""
    (tmp_path / "page.md").write_text(
        "---\ncanonical_for:\n  - bad: value\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})  # must not raise
    assert any("must be a topic-id string" in msg for _, msg in f.errors)


def test_front_matter_schema_flags_non_string_summarizes_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\nsummarizes:\n  - [nested, list]\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})  # must not raise
    assert any("must be a topic-id string" in msg for _, msg in f.errors)


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


def test_front_matter_schema_accepts_summarizes_via_equivalent_spelling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The registry can spell a task_pages entry as `./page.md`; the page
    itself is just `page.md` — these must compare equal for the summarizes
    round-trip too, the same way canonical_page comparisons already do
    (regression test for the gap flagged in PR #619 review)."""
    (tmp_path / "page.md").write_text(
        "---\nsummarizes:\n  - topic-a\n---\n\nSee [owner](owner.md).\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md", "task_pages": ["./page.md"]}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert f.errors == []


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
        "---\nsummarizes:\n  - topic-a\n---\n\nSee [owner](owner.md).\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md", role_key: role_value}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert f.errors == []


def test_front_matter_schema_flags_summarizes_without_a_link_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Being a permitted summarizer (registered in topics.yaml) isn't the
    same as actually linking back -- a page can't restate a topic and
    satisfy the contract just by declaring the front-matter claim
    (regression test for the gap flagged in PR #619 review: real pages in
    this repo had exactly this problem)."""
    (tmp_path / "page.md").write_text(
        "---\nsummarizes:\n  - topic-a\n---\n\nNo link to the owner here.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md", "task_pages": ["page.md"]}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert any("no Markdown link" in msg for _, msg in f.errors)


def test_front_matter_schema_accepts_link_with_anchor_and_title(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A link may carry an anchor and/or a title suffix
    (`owner.md#section "title"`) -- both must be stripped before comparing
    against the target path."""
    (tmp_path / "page.md").write_text(
        '---\nsummarizes:\n  - topic-a\n---\n\nSee [owner](owner.md#section "the owner page").\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md", "task_pages": ["page.md"]}}
    f = dc.Findings()
    dc._check_front_matter_schema(f, topics)
    assert f.errors == []


def test_page_links_to_ignores_external_links(tmp_path: Path) -> None:
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "[external](https://example.com/owner.md)\n[mail](mailto:a@b.com)\n",
        encoding="utf-8",
    )
    assert dc._page_links_to(page, "owner.md") is False


def test_page_links_to_ignores_links_inside_fenced_code_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A link shown only inside a ``` fence (e.g. example Markdown syntax) is
    rendered by MkDocs as literal code, not a navigable backlink -- it must
    not satisfy the summarizes-must-link-back contract (regression test for
    the gap flagged in PR #619 review)."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "Example syntax:\n\n```\n[owner](owner.md)\n```\n",
        encoding="utf-8",
    )
    assert dc._page_links_to(page, "owner.md") is False


def test_page_links_to_ignores_links_inside_tilde_fenced_code_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MkDocs (Python-Markdown's fenced_code extension) treats a `~~~` fence
    identically to a ``` fence -- a link shown only inside one is still
    rendered as literal code, not a navigable backlink (regression test for
    the gap flagged in PR #619 review: the fence-stripping regex originally
    only recognised backtick fences)."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "Example syntax:\n\n~~~\n[owner](owner.md)\n~~~\n",
        encoding="utf-8",
    )
    assert dc._page_links_to(page, "owner.md") is False


def test_page_links_to_recognises_reference_style_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Standard CommonMark reference-style links (`[text][label]` plus a
    `[label]: url` definition elsewhere in the document) are a valid
    backlink too -- real pages in this repo (docs/user-guide/annotations.md)
    use this style, and mkdocs renders it identically to an inline link
    (regression test for the gap flagged in PR #619 review)."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "See [the owner][owner-ref] for details.\n\n[owner-ref]: owner.md\n",
        encoding="utf-8",
    )
    assert dc._page_links_to(page, "owner.md") is True


def test_page_links_to_recognises_collapsed_reference_style_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The collapsed `[label][]` form reuses `label` itself as the
    reference-definition key."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "See [owner-ref][] for details.\n\n[owner-ref]: owner.md\n",
        encoding="utf-8",
    )
    assert dc._page_links_to(page, "owner.md") is True


def test_page_links_to_reference_style_label_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CommonMark matches reference labels case-insensitively."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "See [the owner][Owner-Ref] for details.\n\n[owner-ref]: owner.md\n",
        encoding="utf-8",
    )
    assert dc._page_links_to(page, "owner.md") is True


def test_page_links_to_reference_style_ignores_unresolved_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `[text][label]` with no matching `[label]: url` definition is not a
    link -- must not crash, must not count as a backlink."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text("See [the owner][missing-ref] for details.\n", encoding="utf-8")
    assert dc._page_links_to(page, "owner.md") is False


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


def test_front_matter_schema_flags_non_mapping_front_matter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "page.md").write_text(
        "---\n- a\n- b\n---\n\n# Title\n", encoding="utf-8"
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})
    assert any("invalid front matter" in msg for _, msg in f.errors)


def test_front_matter_schema_skips_generated_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A page marked generated: true must skip the doc_type/canonical_for
    schema checks entirely, per docs/AGENTS.md's documented contract --
    otherwise a generated page could fail the gate for content it doesn't
    hand-author (regression test for the gap flagged in PR #619 review)."""
    (tmp_path / "page.md").write_text(
        "---\ndoc_type: not-a-real-type\ncanonical_for:\n  - unknown-topic\n"
        "generated: true\n---\n\n# Title\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_front_matter_schema(f, {})
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


def test_canonical_pages_declare_ownership_errors_on_generated_canonical_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A topic's canonical_page can't be marked generated: true -- the
    canonical_page is the hand-authored narrative owner by definition, so
    this is a real registry misconfiguration, not something to silently
    wave through the way _check_front_matter_schema's blanket generated
    skip does for ordinary schema enforcement (regression test for the gap
    flagged in PR #619 review)."""
    (tmp_path / "owner.md").write_text(
        "---\ndoc_type: reference\ncanonical_for:\n  - topic-a\ngenerated: true\n"
        "---\n\n# Title\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    topics = {"topic-a": {"canonical_page": "owner.md"}}
    f = dc.Findings()
    dc._check_canonical_pages_declare_ownership(f, topics)
    assert len(f.errors) == 1
    assert "generated: true" in f.errors[0][1]


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


def test_duplicate_paragraph_scan_flags_short_duplicate_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A copy-pasted table is exactly the accidental-duplication pattern this
    scan targets, even well under the 40-word prose threshold -- tables get
    their own, much lower floor (regression test for the gap noted while
    addressing PR #619 feedback: a short table could previously slip past
    the flat word-count check)."""
    table = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    (tmp_path / "a.md").write_text(f"# A\n\n{table}\n", encoding="utf-8")
    (tmp_path / "b.md").write_text(f"# B\n\n{table}\n", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_duplicate_paragraphs(f)
    assert f.errors == []
    assert len(f.warnings) == 1
    assert "a.md" in f.warnings[0][1] and "b.md" in f.warnings[0][1]


def test_duplicate_paragraph_scan_still_ignores_tiny_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even the lower table floor shouldn't fire on a trivial 1x2 table --
    it needs the same _MIN_DUPLICATE_TABLE_WORDS floor as any other block."""
    tiny_table = "| A | B |\n"
    (tmp_path / "a.md").write_text(f"# A\n\n{tiny_table}\n", encoding="utf-8")
    (tmp_path / "b.md").write_text(f"# B\n\n{tiny_table}\n", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_duplicate_paragraphs(f)
    assert f.warnings == []


# ---------------------------------------------------------------------------
# Fenced-code stripping (line-aware)
# ---------------------------------------------------------------------------


def test_strip_fenced_code_ignores_inline_backticks_in_code_content() -> None:
    """A closing fence must be alone on its own line per CommonMark -- an
    inline backtick run embedded within a code line (e.g. a code sample that
    itself shows ``` fence syntax) must not be mistaken for the real closer
    (regression test for the gap flagged in PR #619 review: the previous
    regex-based stripper closed early on any 3+-backtick run anywhere in the
    text, corrupting both the removed code and what stayed visible)."""
    text = (
        "```markdown\n"
        "Use ```python for a code fence, like this: ```\n"
        "This line should still be code, containing [fake](./hidden.md)\n"
        "```\n"
        "Real prose here: [real](./real-link.md)\n"
    )
    assert dc._strip_fenced_code(text) == "Real prose here: [real](./real-link.md)\n"


def test_strip_fenced_code_handles_tilde_fences() -> None:
    text = "~~~\n[hidden](./hidden.md)\n~~~\nProse: [real](./real-link.md)\n"
    assert dc._strip_fenced_code(text) == "Prose: [real](./real-link.md)\n"


def test_strip_fenced_code_requires_matching_or_longer_closer() -> None:
    """A 4-backtick opener needs at least 4 backticks to close -- a nested
    3-backtick run (e.g. showing a fence example inside a longer fence)
    must not close it early."""
    text = "````\n```\nstill code\n```\n````\nReal prose.\n"
    assert dc._strip_fenced_code(text) == "Real prose.\n"


def test_strip_fenced_code_leaves_prose_untouched() -> None:
    text = "Just a paragraph with no fences at all.\n"
    assert dc._strip_fenced_code(text) == text


# ---------------------------------------------------------------------------
# Terminology malformed-type guards
# ---------------------------------------------------------------------------


def test_check_terminology_entries_flags_non_string_term_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-string YAML mapping key (e.g. `123:`) must be reported cleanly,
    not crash re.escape() downstream (regression test for PR #619 review)."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_terminology_entries(
        f, {123: {"canonical_page": "owner.md", "short_definition": "x"}}
    )
    assert any("must be a string" in msg for _, msg in f.errors)


def test_check_terminology_entries_flags_list_valued_canonical_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_terminology_entries(
        f, {"ABI": {"canonical_page": ["a.md", "b.md"], "short_definition": "x"}}
    )
    assert any("canonical_page must be a string" in msg for _, msg in f.errors)


def test_check_duplicate_term_definitions_skips_non_string_term_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Must not raise -- a malformed term id is already reported by
    _check_terminology_entries; this pass just skips it defensively."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    f = dc.Findings()
    dc._check_duplicate_term_definitions(
        f, {123: {"canonical_page": "owner.md", "short_definition": "x"}}
    )
    assert f.warnings == []


def test_check_duplicate_term_definitions_skips_list_valued_canonical_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_duplicate_term_definitions(
        f, {"ABI": {"canonical_page": ["a.md"], "short_definition": "x"}}
    )
    assert f.warnings == []


# ---------------------------------------------------------------------------
# Terminology registry
# ---------------------------------------------------------------------------


def test_load_terminology_missing_file_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "TERMINOLOGY_FILE", tmp_path / "terminology.yaml")
    f = dc.Findings()
    assert dc._load_terminology(f) is None
    assert f.errors == []


def test_load_terminology_invalid_yaml_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "terminology.yaml"
    bad.write_text("terms: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(dc, "TERMINOLOGY_FILE", bad)
    f = dc.Findings()
    assert dc._load_terminology(f) is None
    assert any("invalid YAML" in msg for _, msg in f.errors)


def test_check_terminology_entries_flags_missing_canonical_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_terminology_entries(f, {"ABI": {"short_definition": "x"}})
    assert any("missing required 'canonical_page'" in msg for _, msg in f.errors)


def test_check_terminology_entries_flags_nonexistent_canonical_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_terminology_entries(
        f, {"ABI": {"canonical_page": "missing.md", "short_definition": "x"}}
    )
    assert any("does not exist" in msg for _, msg in f.errors)


def test_check_terminology_entries_flags_missing_short_definition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_terminology_entries(f, {"ABI": {"canonical_page": "owner.md"}})
    assert any("missing required 'short_definition'" in msg for _, msg in f.errors)


def test_check_terminology_entries_flags_non_string_short_definition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A truthy non-string short_definition (e.g. a YAML list/mapping) must
    be reported, not silently accepted by the truthiness check alone
    (regression test for the gap flagged in PR #619 review)."""
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_terminology_entries(
        f, {"ABI": {"canonical_page": "owner.md", "short_definition": ["a", "b"]}}
    )
    assert any("short_definition must be a string" in msg for _, msg in f.errors)


def test_check_terminology_entries_allows_two_terms_sharing_a_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unlike topics.yaml's canonical_page, terminology canonical_page is not
    required to be unique -- ABI and API legitimately share one page."""
    (tmp_path / "owner.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    f = dc.Findings()
    dc._check_terminology_entries(
        f,
        {
            "ABI": {"canonical_page": "owner.md", "short_definition": "a"},
            "API": {"canonical_page": "owner.md", "short_definition": "b"},
        },
    )
    assert f.errors == []


def test_check_duplicate_term_definitions_flags_redefinition_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("# Owner\n\n**ABI** is the thing.\n")
    (tmp_path / "other.md").write_text(
        "# Other\n\n**ABI** is a binary contract explained again here.\n"
    )
    f = dc.Findings()
    terms = {"ABI": {"canonical_page": "owner.md", "short_definition": "x"}}
    dc._check_duplicate_term_definitions(f, terms)
    assert f.errors == []
    assert len(f.warnings) == 1
    assert "other.md" in f.warnings[0][1]


def test_check_duplicate_term_definitions_ignores_canonical_page_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("# Owner\n\n**ABI** is the thing.\n")
    f = dc.Findings()
    terms = {"ABI": {"canonical_page": "owner.md", "short_definition": "x"}}
    dc._check_duplicate_term_definitions(f, terms)
    assert f.warnings == []


def test_check_duplicate_term_definitions_ignores_mere_mention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Just mentioning or linking to a term on another page is not a
    redefinition -- only an actual bolded-term-plus-definition-connector
    pattern should fire, or ordinary correct usage would be flagged
    constantly."""
    monkeypatch.setattr(dc, "DOCS", tmp_path)
    (tmp_path / "owner.md").write_text("# Owner\n\n**ABI** is the thing.\n")
    (tmp_path / "other.md").write_text(
        "# Other\n\nSee [ABI](owner.md) for details on **ABI** compatibility.\n"
    )
    f = dc.Findings()
    terms = {"ABI": {"canonical_page": "owner.md", "short_definition": "x"}}
    dc._check_duplicate_term_definitions(f, terms)
    assert f.warnings == []

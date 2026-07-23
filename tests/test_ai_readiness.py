"""Smoke tests for scripts/check_ai_readiness.py.

These verify that the script imports, that its check functions run end-to-end
against the live repository tree, and that the documented invariants (no
errors) still hold.  We deliberately exercise the live tree rather than a
fixture so the script's expectations match reality.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_ai_readiness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_ai_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def car():
    return _load_module()


def test_script_imports(car):
    assert hasattr(car, "main")
    assert hasattr(car, "CHECKS")
    # All check names registered
    expected = {
        "file-size",
        "claude-md-coverage",
        "test-ratio",
        "future-annotations",
        "changekind-partition",
        "changekind-detector",
        "changekind-docs",
        "import-cycle-growth",
        "mypy-baseline",
        "examples-ground-truth",
        "mkdocs-nav-coverage",
        "banned-imports",
        "license-header",
    }
    assert expected <= set(car.CHECKS)


def test_examples_ground_truth_in_sync(car):
    f = car.Findings()
    car.check_examples_ground_truth(f)
    assert f.errors == [], f"examples/ground_truth.json out of sync: {f.errors}"


def test_no_banned_imports(car):
    f = car.Findings()
    car.check_banned_imports(f)
    assert f.errors == [], f"Banned-import violations: {f.errors}"


def test_changekind_partition_holds(car):
    """The partition invariant documented in CLAUDE.md must hold."""
    f = car.Findings()
    car.check_changekind_partition(f)
    assert f.errors == [], f"ChangeKind partition broken: {f.errors}"


def test_claude_md_coverage_holds(car):
    f = car.Findings()
    car.check_claude_md_coverage(f)
    assert f.errors == [], f"Missing CLAUDE.md files: {f.errors}"


def test_no_unapproved_import_cycle_growth(car):
    """The check's real invariant (CLAUDE.md "M1-3"): no *unapproved* SCC
    growth beyond IMPORT_CYCLE_ALLOWLIST's baseline — not literally "no
    cycles" (a large by-design CLI-registration SCC is already baselined)."""
    f = car.Findings()
    car.check_import_cycles(f)
    assert f.errors == [], f"Unapproved import-cycle growth detected: {f.errors}"


def test_adr_index_and_nav_sync_holds(car):
    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == [], f"ADR index/nav drift: {f.errors}"


def test_adr_index_and_nav_sync_catches_missing_index_nav_entry(
    car, tmp_path, monkeypatch
):
    """The ADR index page itself (not each individual ADR -- that
    requirement was relaxed, see test_adr_index_nav_sync_does_not_require_
    individual_adr_in_nav below) must be listed in mkdocs.yml nav, since
    every ADR is reachable only through it."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | Accepted |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Accepted\n", encoding="utf-8"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - Home: index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("ADR index itself" in msg for _, msg in f.errors), (
        f"expected a missing-index-from-nav error, got: {f.errors}"
    )


def test_adr_index_nav_sync_rejects_bare_filename_mention_as_link(
    car, tmp_path, monkeypatch
):
    """A bare mention of the ADR filename in prose or a code sample (not an
    actual Markdown link) must not satisfy the "linked from index.md"
    requirement -- MkDocs doesn't turn plain text into a navigable link, so
    the ADR would still be unreachable from the published index page even
    though its filename appears somewhere in the file."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "See `001-example.md` for details on this decision.\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Accepted\n", encoding="utf-8"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - Home: index.md\n  - ADR Index: development/adr/index.md\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("not linked from" in msg for _, msg in f.errors), (
        f"expected a not-linked-from-index error, got: {f.errors}"
    )


def test_adr_index_nav_sync_accepts_reference_style_index_link(
    car, tmp_path, monkeypatch
):
    """index.md may link an ADR using reference-style syntax
    ([001][adr-001] with a [adr-001]: 001-example.md definition) instead of
    an inline link -- MkDocs renders both identically, so both must satisfy
    the "linked from index.md" requirement."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001][adr-001] | Example | Accepted |\n\n[adr-001]: 001-example.md\n",
        encoding="utf-8",
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Accepted\n", encoding="utf-8"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - Home: index.md\n  - ADR Index: development/adr/index.md\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_does_not_require_individual_adr_in_nav(
    car, tmp_path, monkeypatch
):
    """Relaxed rule: an ADR linked from index.md is reachable, and the index
    itself is in nav -- an individual ADR entry is no longer required."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | Accepted |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Accepted\n", encoding="utf-8"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - Home: index.md\n  - ADR Index: development/adr/index.md\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_catches_missing_status(car, tmp_path, monkeypatch):
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text("# ADR-001\n\nNo status here.\n")
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("missing a Status" in msg for _, msg in f.errors)


def test_adr_index_nav_sync_accepts_heading_style_status(car, tmp_path, monkeypatch):
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n## Status\n\nAccepted — implemented.\n"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_finds_replacement_link_on_wrapped_status_line(
    car, tmp_path, monkeypatch
):
    """A Status paragraph that wraps across multiple physical lines (already
    real usage in this repo) must still have its replacement link found even
    when the link falls on a continuation line, not the first one
    (regression test for the gap flagged in PR #619 review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n"
        "| [002](002-example.md) | Example 2 | |\n",
        encoding="utf-8",
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded -- this decision was revisited\n"
        "and replaced by [ADR-002](002-example.md) after further review.\n"
    )
    (adr_dir / "002-example.md").write_text("# ADR-002\n\n**Status:** Accepted.\n")
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_catches_superseded_without_replacement_link(
    car, tmp_path, monkeypatch
):
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded, no pointer to what replaced it.\n"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("doesn't link to its replacement" in msg for _, msg in f.errors)


def test_adr_index_nav_sync_accepts_superseded_with_replacement_link(
    car, tmp_path, monkeypatch
):
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n"
        "| [002](002-example.md) | Example 2 | |\n",
        encoding="utf-8",
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded by [ADR-002](002-example.md).\n"
    )
    (adr_dir / "002-example.md").write_text("# ADR-002\n\n**Status:** Accepted.\n")
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_accepts_angle_bracket_replacement_link(
    car, tmp_path, monkeypatch
):
    """CommonMark's angle-bracket link destination form ([text](<url>)) is
    a valid, MkDocs-rendered link -- the `<`/`>` wrapper must not end up as
    part of the resolved path and cause a false "doesn't link to its
    replacement" error (regression test for the gap flagged in PR #619
    review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n"
        "| [002](002-example.md) | Example 2 | |\n",
        encoding="utf-8",
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded by [ADR-002](<002-example.md>).\n"
    )
    (adr_dir / "002-example.md").write_text("# ADR-002\n\n**Status:** Accepted.\n")
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_accepts_replacement_link_with_title(
    car, tmp_path, monkeypatch
):
    """A link may carry an optional title after the destination
    ([text](url "title")) -- the title text must not stay glued to the
    basename and prevent it from matching the ADR filename pattern
    (regression test for the gap flagged in PR #619 review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n"
        "| [002](002-example.md) | Example 2 | |\n",
        encoding="utf-8",
    )
    (adr_dir / "001-example.md").write_text(
        '# ADR-001\n\n**Status:** Superseded by [ADR-002](002-example.md "replacement").\n'
    )
    (adr_dir / "002-example.md").write_text("# ADR-002\n\n**Status:** Accepted.\n")
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_accepts_reference_style_replacement_link(
    car, tmp_path, monkeypatch
):
    """A reference-style replacement link ([ADR-002][replacement] plus a
    [replacement]: 002-example.md definition elsewhere in the file) is a
    valid, MkDocs-rendered link -- the inline-only scan previously missed
    it entirely (regression test for the gap flagged in PR #619 review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n"
        "| [002](002-example.md) | Example 2 | |\n",
        encoding="utf-8",
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded by [ADR-002][replacement].\n\n"
        "[replacement]: 002-example.md\n"
    )
    (adr_dir / "002-example.md").write_text("# ADR-002\n\n**Status:** Accepted.\n")
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert f.errors == []


def test_adr_index_nav_sync_rejects_image_syntax_as_replacement(
    car, tmp_path, monkeypatch
):
    """`![ADR-002](002-example.md)` is an image embed, not a navigable
    link -- it must not satisfy the replacement-link requirement even
    though its bracket/paren shape otherwise matches a real link
    (regression test for the gap flagged in PR #619 review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n"
        "| [002](002-example.md) | Example 2 | |\n",
        encoding="utf-8",
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded ![ADR-002](002-example.md).\n"
    )
    (adr_dir / "002-example.md").write_text("# ADR-002\n\n**Status:** Accepted.\n")
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("doesn't link to its replacement" in msg for _, msg in f.errors)


def test_adr_index_nav_sync_rejects_unrelated_link_as_replacement(
    car, tmp_path, monkeypatch
):
    """A "Superseded" status with a link to something that isn't another ADR
    file (e.g. a plan doc explaining the context) must not satisfy the
    replacement-link requirement -- any link at all was previously accepted
    (regression test for the gap flagged in PR #619 review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded, see "
        "[the plan doc](../plans/some-plan.md) for context.\n"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("doesn't link to its replacement" in msg for _, msg in f.errors)


def test_adr_index_nav_sync_rejects_adr_shaped_link_outside_adr_dir(
    car, tmp_path, monkeypatch
):
    """A link whose *basename* matches the ADR filename pattern but whose
    target resolves outside docs/development/adr/ (e.g. a coincidentally
    numbered file in a notes/ directory) must not satisfy the
    replacement-link requirement -- basename-only matching previously
    accepted it (regression test for the gap flagged in PR #619 review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    notes_dir = fake_docs / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "002-plan.md").write_text("# Plan\n", encoding="utf-8")
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded, see "
        "[the plan](../../notes/002-plan.md) for context.\n"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("doesn't link to its replacement" in msg for _, msg in f.errors)


def test_adr_index_nav_sync_rejects_self_link_as_replacement(
    car, tmp_path, monkeypatch
):
    """A "Superseded" status linking to its own file (e.g. copy-paste error,
    or a link intended for a different ADR that was never updated) must not
    satisfy the replacement-link requirement -- the target must be an ADR
    *other than* the one making the claim (regression test for the gap
    flagged in PR #619 review)."""
    fake_root = tmp_path
    fake_docs = fake_root / "docs"
    adr_dir = fake_docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "index.md").write_text(
        "| [001](001-example.md) | Example | |\n", encoding="utf-8"
    )
    (adr_dir / "001-example.md").write_text(
        "# ADR-001\n\n**Status:** Superseded by [this ADR](001-example.md).\n"
    )
    (fake_root / "mkdocs.yml").write_text(
        "nav:\n  - ADR Index: development/adr/index.md\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "ROOT", fake_root)
    monkeypatch.setattr(car, "DOCS", fake_docs)

    f = car.Findings()
    car.check_adr_index_and_nav_sync(f)
    assert any("doesn't link to its replacement" in msg for _, msg in f.errors)


def test_adr_status_text_joins_wrapped_lines(car):
    text = "# ADR-001\n\n**Status:** Superseded, replaced by\n[ADR-002](002-x.md).\n"
    assert car._adr_status_text(text) == (
        "Superseded, replaced by [ADR-002](002-x.md)."
    )


def test_adr_status_text_stops_at_blank_line(car):
    text = "# ADR-001\n\n**Status:** Accepted.\n\nMore prose that isn't status.\n"
    assert car._adr_status_text(text) == "Accepted."


def test_adr_status_text_stops_at_next_bold_field(car):
    text = "# ADR-001\n\n**Status:** Accepted.\n**Decision maker:** Someone.\n"
    assert car._adr_status_text(text) == "Accepted."


def test_adr_status_text_stops_at_heading(car):
    text = "# ADR-001\n\n**Status:** Accepted.\n## Context\n\nBody.\n"
    assert car._adr_status_text(text) == "Accepted."


def test_adr_status_text_rejects_empty_heading_style_status(car):
    """An empty `## Status` section immediately followed by the next
    heading (no actual status content) must be treated as a missing
    status, not silently accept that heading's own text as the status
    (regression test for the gap flagged in PR #619 review)."""
    text = "# ADR-001\n\n## Status\n\n## Context\n\nBody.\n"
    assert car._adr_status_text(text) is None


def test_no_hard_file_size_violations(car):
    """Files over ERROR_LINES must be in LARGE_FILE_ALLOWLIST."""
    f = car.Findings()
    car.check_file_sizes(f)
    # Allow warnings (allow-listed large files, soft-limit warnings) — but
    # any file-size ERROR means an un-allowlisted file blew past the hard
    # limit.
    assert f.errors == [], f"File-size hard-limit violations: {f.errors}"


def test_main_returns_zero_on_clean_tree(car, capsys):
    """End-to-end: running the script against the live tree should exit 0.

    We skip the slowest whole-tree scans here to avoid re-running them:
    - ``mypy-baseline`` needs mypy (exercised in the dedicated CI lane);
    - ``import-cycle-growth`` and ``banned-imports`` each re-walk every
      module's AST and are already asserted individually by
      ``test_no_unapproved_import_cycle_growth`` and ``test_no_banned_imports``.
      Running them again inside this end-to-end
      check just doubled the cost (it was the dominant unit-lane offender at
      ~14.6s under coverage) without adding coverage — ``scripts/`` isn't
      measured, and the full gate already runs standalone in the
      ``ai-readiness`` CI job. The remaining checks still verify that
      ``main()`` wires up the run and exits 0.
    """
    rc = car.main(
        [
            "--skip",
            "mypy-baseline",
            "--skip",
            "import-cycle-growth",
            "--skip",
            "banned-imports",
        ]
    )
    assert rc == 0, capsys.readouterr().out


def test_examples_readme_sync_in_sync(car):
    """The live examples/README.md catalog must agree with ground_truth.json."""
    f = car.Findings()
    car.check_examples_readme_sync(f)
    assert f.errors == [], f"examples/README.md out of sync: {f.errors}"


def _write_synthetic_catalog(tmp_path, *, case02_verdict_cell):
    """Write a minimal ground_truth.json + README.md pair into tmp_path.

    Two single-library cases (one BREAKING, one COMPATIBLE/addition). The
    caller controls case02's verdict cell so a test can inject row drift.
    """
    import json

    (tmp_path / "ground_truth.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "case01_foo": {"expected": "BREAKING", "category": "breaking"},
                    "case02_bar": {"expected": "COMPATIBLE", "category": "addition"},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "This directory contains **2 cases**.\n\n"
        "| BREAKING | 1 |\n"
        "| COMPATIBLE (addition) | 1 |\n\n"
        "| [01](case01_foo/README.md) | Foo | Breaking | 🔴 BREAKING |\n"
        f"| [02](case02_bar/README.md) | Bar | Addition | {case02_verdict_cell} |\n",
        encoding="utf-8",
    )


def test_examples_readme_sync_passes_on_correct_synthetic(car, tmp_path, monkeypatch):
    """A synthetic catalog whose rows match ground_truth yields no errors."""
    monkeypatch.setattr(car, "EXAMPLES", tmp_path)
    _write_synthetic_catalog(tmp_path, case02_verdict_cell="🟢 COMPATIBLE")
    f = car.Findings()
    car.check_examples_readme_sync(f)
    assert f.errors == [], f.errors


def test_examples_readme_sync_catches_swapped_row_verdict(car, tmp_path, monkeypatch):
    """A stale per-row verdict (counts unchanged) must fail — the drift the
    aggregate-count checks alone cannot see (Codex review, PR #318)."""
    monkeypatch.setattr(car, "EXAMPLES", tmp_path)
    # case02 is COMPATIBLE/addition in ground_truth, but its row claims BREAKING.
    # The distribution counts still tally, so only row-content parsing catches it.
    _write_synthetic_catalog(tmp_path, case02_verdict_cell="🔴 BREAKING")
    f = car.Findings()
    car.check_examples_readme_sync(f)
    assert any("case02_bar" in msg and "BREAKING" in msg for _, msg in f.errors), (
        f"expected a verdict-mismatch error for case02_bar, got {f.errors}"
    )


# ---------------------------------------------------------------------------
# M1-2: first-party-repository coverage synthetic tests
# ---------------------------------------------------------------------------


def test_file_sizes_covers_first_party_roots_beyond_abicheck(
    car, tmp_path, monkeypatch
):
    """An oversized script outside abicheck/ must fail the gate.

    Regression guard for the exact gap M1-2 describes: before first-party
    scanning covered `scripts/`/`eval/`/`validation/`/`action/`/the clang
    plugin's `tests/`, an oversized file there was invisible to this check.
    """
    fake_root = tmp_path / "scripts"
    fake_root.mkdir()
    (fake_root / "oversized.py").write_text(
        "\n".join(f"x{i} = {i}" for i in range(car.ERROR_LINES + 10)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(car, "FIRST_PARTY_PY_ROOTS", (fake_root,))
    monkeypatch.setattr(car, "LARGE_FILE_ALLOWLIST", frozenset())
    monkeypatch.setattr(car, "ROOT", tmp_path)
    f = car.Findings()
    car.check_file_sizes(f)
    assert any("oversized.py" in msg for _, msg in f.errors), f.errors


def test_file_sizes_allowlisted_file_only_warns(car, tmp_path, monkeypatch):
    fake_root = tmp_path / "scripts"
    fake_root.mkdir()
    (fake_root / "big.py").write_text(
        "\n".join(f"x{i} = {i}" for i in range(car.ERROR_LINES + 10)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(car, "FIRST_PARTY_PY_ROOTS", (fake_root,))
    monkeypatch.setattr(car, "LARGE_FILE_ALLOWLIST", frozenset({"scripts/big.py"}))
    monkeypatch.setattr(car, "ROOT", tmp_path)
    f = car.Findings()
    car.check_file_sizes(f)
    assert f.errors == []
    assert any("big.py" in msg for _, msg in f.warnings)


def test_agent_instructions_coverage_catches_missing_dir(car, tmp_path, monkeypatch):
    missing = tmp_path / "some-tree"
    missing.mkdir()
    monkeypatch.setattr(car, "REQUIRED_AGENT_INSTRUCTION_DIRS", (missing,))
    monkeypatch.setattr(car, "ROOT", tmp_path)
    f = car.Findings()
    car.check_agent_instructions_coverage(f)
    assert any("some-tree" in msg for _, msg in f.errors), f.errors


@pytest.mark.parametrize("filename", ["AGENTS.md", "CLAUDE.md"])
def test_agent_instructions_coverage_accepts_either_file(
    car, tmp_path, monkeypatch, filename
):
    covered = tmp_path / "some-tree"
    covered.mkdir()
    (covered / filename).write_text("# instructions\n", encoding="utf-8")
    monkeypatch.setattr(car, "REQUIRED_AGENT_INSTRUCTION_DIRS", (covered,))
    monkeypatch.setattr(car, "ROOT", tmp_path)
    f = car.Findings()
    car.check_agent_instructions_coverage(f)
    assert f.errors == []


def test_script_inventory_completeness_catches_unlisted_script(
    car, tmp_path, monkeypatch
):
    fake_scripts = tmp_path
    (fake_scripts / "CLAUDE.md").write_text(
        "## Inventory\n\n| Script | Purpose |\n|---|---|\n", encoding="utf-8"
    )
    (fake_scripts / "new_tool.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(car, "SCRIPTS", fake_scripts)
    monkeypatch.setattr(car, "ROOT", tmp_path)
    f = car.Findings()
    car.check_script_inventory_completeness(f)
    assert any("new_tool.py" in msg for _, msg in f.warnings), f.warnings


def test_script_inventory_completeness_passes_when_listed(car, tmp_path, monkeypatch):
    fake_scripts = tmp_path
    (fake_scripts / "CLAUDE.md").write_text(
        "## Inventory\n\n| `new_tool.py` | does a thing |\n", encoding="utf-8"
    )
    (fake_scripts / "new_tool.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(car, "SCRIPTS", fake_scripts)
    monkeypatch.setattr(car, "ROOT", tmp_path)
    f = car.Findings()
    car.check_script_inventory_completeness(f)
    assert f.warnings == []


def test_script_inventory_completeness_ignores_mentions_outside_inventory(
    car, tmp_path, monkeypatch
):
    """A script named only in prose *outside* the '## Inventory' section
    (e.g. another section's narrative, or a later '## Conventions' heading)
    must still warn — only an actual inventory row satisfies the check."""
    fake_scripts = tmp_path
    (fake_scripts / "CLAUDE.md").write_text(
        "## Inventory\n\n| Script | Purpose |\n|---|---|\n"
        "| `other_tool.py` | does another thing |\n\n"
        "## Conventions\n\nSee `new_tool.py` for an example of the pattern.\n",
        encoding="utf-8",
    )
    (fake_scripts / "new_tool.py").write_text("print('hi')\n", encoding="utf-8")
    (fake_scripts / "other_tool.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(car, "SCRIPTS", fake_scripts)
    monkeypatch.setattr(car, "ROOT", tmp_path)
    f = car.Findings()
    car.check_script_inventory_completeness(f)
    assert any("new_tool.py" in msg for _, msg in f.warnings), f.warnings
    assert not any("other_tool.py" in msg for _, msg in f.warnings), f.warnings


def test_generated_file_ownership_catches_stripped_marker(car, tmp_path, monkeypatch):
    stripped = tmp_path / "generated.md"
    stripped.write_text("# just content, no marker\n", encoding="utf-8")
    monkeypatch.setattr(
        car,
        "GENERATED_FILE_MARKERS",
        ((stripped, "generated by scripts/gen_thing.py", "gen_thing.py"),),
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", tmp_path / "no-such-docs-dir")
    f = car.Findings()
    car.check_generated_file_ownership(f)
    assert any("generated.md" in msg for _, msg in f.errors), f.errors


def test_generated_file_ownership_passes_with_marker_present(
    car, tmp_path, monkeypatch
):
    kept = tmp_path / "generated.md"
    kept.write_text(
        "<!-- generated by scripts/gen_thing.py -->\ncontent\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        car,
        "GENERATED_FILE_MARKERS",
        ((kept, "generated by scripts/gen_thing.py", "gen_thing.py"),),
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", tmp_path / "no-such-docs-dir")
    f = car.Findings()
    car.check_generated_file_ownership(f)
    assert f.errors == []


def test_test_ratio_recursive_discovery(car, tmp_path, monkeypatch):
    """A nested tests/subpkg/test_foo.py must count toward the ratio — a
    plain TESTS.glob("test_*.py") (non-recursive) would miss it."""
    fake_pkg = tmp_path / "abicheck"
    fake_pkg.mkdir()
    # 1 test file / N source files must fall strictly below MIN_TEST_RATIO
    # (20%) for the warning to fire — N=5 gives exactly 20% (not < 20%).
    n_source = max(car.MIN_SOURCE_FILES_FOR_RATIO, int(1 / car.MIN_TEST_RATIO) + 1)
    for i in range(n_source):
        (fake_pkg / f"mod{i}.py").write_text("x = 1\n", encoding="utf-8")

    fake_tests = tmp_path / "tests"
    nested = fake_tests / "subpkg"
    nested.mkdir(parents=True)
    (nested / "test_nested.py").write_text(
        "def test_x(): assert True\n", encoding="utf-8"
    )

    monkeypatch.setattr(car, "PKG", fake_pkg)
    monkeypatch.setattr(car, "TESTS", fake_tests)
    f = car.Findings()
    car.check_test_ratio(f)
    # One nested test file only (below MIN_TEST_RATIO for this source count)
    # would warn if uncounted, and would ALSO warn if counted at this size —
    # the real assertion is the message's numerator, not warn/no-warn.
    assert f.warnings, "expected a ratio warning for this tiny synthetic tree"
    assert "1 test files" in f.warnings[0][1], f.warnings


# ---------------------------------------------------------------------------
# M1-4: repo_facts.json / action-version-freshness synthetic tests
# ---------------------------------------------------------------------------


def test_action_version_freshness_catches_stale_reference(car, tmp_path, monkeypatch):
    import json

    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "0.5.0"}), encoding="utf-8"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text(
        "uses: abicheck/abicheck@v0.3.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", docs)
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert any("v0.3.0" in msg and "0.5.0" in msg for _, msg in f.errors), f.errors


def test_action_version_freshness_passes_when_current(car, tmp_path, monkeypatch):
    import json

    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "0.5.0"}), encoding="utf-8"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text(
        "uses: abicheck/abicheck@v0.5.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", docs)
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert f.errors == []


def test_action_version_freshness_exempts_adr_dir(car, tmp_path, monkeypatch):
    import json

    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "0.5.0"}), encoding="utf-8"
    )
    docs = tmp_path / "docs"
    adr_dir = docs / "development" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "001-historical.md").write_text(
        "uses: abicheck/abicheck@v0.3.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", docs)
    # _ACTION_VERSION_EXEMPT_DIRS is computed from DOCS at module-load time
    # (like REQUIRED_CLAUDE_MD_DIRS etc.), so monkeypatching DOCS alone
    # doesn't retroactively change it — patch it directly, same pattern used
    # for the other module-level dir tuples in this file.
    monkeypatch.setattr(car, "_ACTION_VERSION_EXEMPT_DIRS", (adr_dir,))
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert f.errors == []


def test_repo_facts_json_exists_and_is_fresh():
    """The committed repo_facts.json should be exactly what
    scripts/gen_repo_facts.py --check verifies — a lightweight structural
    sanity check that doesn't re-run the (slower) full script."""
    import json

    facts_path = ROOT / "repo_facts.json"
    assert facts_path.is_file(), (
        "repo_facts.json missing — run scripts/gen_repo_facts.py"
    )
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    for key in (
        "project_version",
        "latest_release",
        "example_cases",
        "fast_test_cases_collected",
        "canonical_python",
    ):
        assert key in facts, f"repo_facts.json missing {key!r}"

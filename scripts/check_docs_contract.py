#!/usr/bin/env python3
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

"""Documentation-operating-model gate: docs/AGENTS.md's "one fact, one owner"
contract, made machine-checkable.

Two kinds of checks:

  Ownership (ERROR — structural, deterministic, cheap):
    - every path a docs/_meta/topics.yaml topic references actually exists;
    - no two topics claim the same canonical_page;
    - a page's front-matter `canonical_for`/`summarizes` topic ids exist in
      topics.yaml, and `canonical_for` round-trips back to a topic that
      actually names this page as its canonical_page;
    - front matter, when present, has a well-formed schema (known doc_type/
      lifecycle values, list-typed fields actually lists).

  Duplication (WARN — advisory, not a structural ownership conflict):
    - a canonical_page with no front matter at all (rollout is incremental,
      see docs/AGENTS.md "Rollout status");
    - an identical, long (40+ word) paragraph/table/list block appearing
      verbatim in two or more manual (non-generated) pages — usually a sign
      one of the two should be a short summary-with-link instead of a second
      full explanation.

Run locally with:

    python scripts/check_docs_contract.py

Requires PyYAML (a core dependency, not dev-only), so — like
check_usecase_docs_sync.py — this runs after `pip install -e .`, not before
(unlike scripts/check_ai_readiness.py, which must stay pure-stdlib).
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TOPICS_FILE = DOCS / "_meta" / "topics.yaml"

_ALLOWED_DOC_TYPES = frozenset(
    {
        "hub",
        "tutorial",
        "how-to",
        "explanation",
        "reference",
        "case",
        "migration",
        "contributor",
    }
)
_ALLOWED_LEVELS = frozenset({"beginner", "intermediate", "advanced", "expert"})
_ALLOWED_LIFECYCLES = frozenset({"active", "migration", "historical"})

# Generated or non-prose trees excluded from the duplicate-paragraph scan —
# their content is either machine-generated (drift is caught by that
# generator's own --check) or structurally repetitive by design (per-case
# pages sharing a template).
_DUPLICATE_SCAN_EXCLUDE_PREFIXES = (
    "examples/case",
    "examples/by-verdict/",
    "examples/by-category/",
    "reference/detector-spec.md",
    "schemas/",
)
_DUPLICATE_SCAN_EXCLUDE_NAMES = frozenset({"CLAUDE.md", "AGENTS.md"})

_MIN_DUPLICATE_WORDS = 40

_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


class Findings:
    """Collects errors and warnings, grouped by check name (mirrors
    scripts/check_ai_readiness.py's Findings class for consistent output)."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def err(self, check: str, msg: str) -> None:
        self.errors.append((check, msg))

    def warn(self, check: str, msg: str) -> None:
        self.warnings.append((check, msg))

    def report(self) -> int:
        by_check: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: {"errors": [], "warnings": []}
        )
        for check, msg in self.errors:
            by_check[check]["errors"].append(msg)
        for check, msg in self.warnings:
            by_check[check]["warnings"].append(msg)

        for check, buckets in sorted(by_check.items()):
            print(f"\n=== {check} ===")
            for m in buckets["errors"]:
                print(f"  ERROR: {m}")
            for m in buckets["warnings"]:
                print(f"  WARN:  {m}")

        n_err, n_warn = len(self.errors), len(self.warnings)
        print(f"\ndocs-contract: {n_err} error(s), {n_warn} warning(s)")
        return 1 if n_err else 0


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        # Outside ROOT — only reachable when a test monkeypatches DOCS to a
        # tmp_path fixture; real runs always resolve under ROOT.
        return str(p)


def load_front_matter(path: Path) -> dict[str, object] | None:
    """Return the parsed YAML front-matter dict, or None if the file has
    none. Raises yaml.YAMLError on malformed front matter (caller decides
    how to report it)."""
    text = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return None
    data = yaml.safe_load(m.group(1))
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Ownership checks
# ---------------------------------------------------------------------------


def _load_topics(f: Findings) -> dict[str, dict[str, object]] | None:
    if not TOPICS_FILE.is_file():
        f.err("ownership", f"{_rel(TOPICS_FILE)}: file not found")
        return None
    try:
        data = yaml.safe_load(TOPICS_FILE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        f.err("ownership", f"{_rel(TOPICS_FILE)}: invalid YAML: {exc}")
        return None
    if not isinstance(data, dict) or "topics" not in data:
        f.err("ownership", f"{_rel(TOPICS_FILE)}: missing top-level 'topics' key")
        return None
    topics = data["topics"]
    if not isinstance(topics, dict):
        f.err("ownership", f"{_rel(TOPICS_FILE)}: 'topics' must be a mapping")
        return None
    return topics


def _check_referenced_paths_exist(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    for topic_id, entry in topics.items():
        if not isinstance(entry, dict) or "canonical_page" not in entry:
            f.err(
                "ownership",
                f"topic {topic_id!r}: missing required 'canonical_page' field",
            )
            continue
        for key in ("canonical_page", "worked_example", "reference_page"):
            value = entry.get(key)
            if value is None:
                continue
            if not (DOCS / str(value)).is_file():
                f.err(
                    "ownership",
                    f"topic {topic_id!r}: {key} {value!r} does not exist under docs/",
                )
        for key in ("task_pages", "allowed_summaries"):
            values = entry.get(key, [])
            if not isinstance(values, list):
                f.err("ownership", f"topic {topic_id!r}: {key} must be a list")
                continue
            for value in values:
                if not (DOCS / str(value)).is_file():
                    f.err(
                        "ownership",
                        f"topic {topic_id!r}: {key} entry {value!r} does not "
                        "exist under docs/",
                    )
        fact_sources = entry.get("fact_sources", [])
        if not isinstance(fact_sources, list):
            f.err("ownership", f"topic {topic_id!r}: fact_sources must be a list")
        else:
            for value in fact_sources:
                if not (ROOT / str(value)).exists():
                    f.err(
                        "ownership",
                        f"topic {topic_id!r}: fact_sources entry {value!r} "
                        "does not exist",
                    )


def _check_canonical_page_uniqueness(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for topic_id, entry in topics.items():
        if isinstance(entry, dict) and "canonical_page" in entry:
            owners[str(entry["canonical_page"])].append(topic_id)
    for page, topic_ids in owners.items():
        if len(topic_ids) > 1:
            f.err(
                "ownership",
                f"{page!r} is claimed as canonical_page by multiple topics: "
                f"{', '.join(sorted(topic_ids))} — a page can have at most "
                "one owning topic",
            )


def _permitted_summary_pages(entry: dict[str, object]) -> set[str]:
    """The set of docs/-relative pages a topic's registry entry permits to
    reference it via `summarizes` — its worked_example, reference_page, and
    every task_pages/allowed_summaries entry."""
    pages: set[str] = set()
    for key in ("worked_example", "reference_page"):
        value = entry.get(key)
        if value is not None:
            pages.add(str(value))
    for key in ("task_pages", "allowed_summaries"):
        values = entry.get(key, [])
        if isinstance(values, list):
            pages.update(str(v) for v in values)
    return pages


def _check_front_matter_schema(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    """Validate front matter on every manual page that has any, and
    cross-check `canonical_for`/`summarizes` against the topic registry."""
    for path in sorted(DOCS.rglob("*.md")):
        if path.name in _DUPLICATE_SCAN_EXCLUDE_NAMES:
            continue
        rel_to_docs = path.relative_to(DOCS).as_posix()
        try:
            fm = load_front_matter(path)
        except yaml.YAMLError as exc:
            f.err("front-matter", f"{_rel(path)}: invalid front-matter YAML: {exc}")
            continue
        if fm is None:
            continue

        doc_type = fm.get("doc_type")
        if doc_type is not None and doc_type not in _ALLOWED_DOC_TYPES:
            f.err(
                "front-matter",
                f"{_rel(path)}: doc_type {doc_type!r} not in "
                f"{sorted(_ALLOWED_DOC_TYPES)}",
            )
        level = fm.get("level")
        if level is not None and level not in _ALLOWED_LEVELS:
            f.err(
                "front-matter",
                f"{_rel(path)}: level {level!r} not in {sorted(_ALLOWED_LEVELS)}",
            )
        lifecycle = fm.get("lifecycle")
        if lifecycle is not None and lifecycle not in _ALLOWED_LIFECYCLES:
            f.err(
                "front-matter",
                f"{_rel(path)}: lifecycle {lifecycle!r} not in "
                f"{sorted(_ALLOWED_LIFECYCLES)}",
            )

        canonical_for = fm.get("canonical_for", [])
        if not isinstance(canonical_for, list):
            f.err("front-matter", f"{_rel(path)}: canonical_for must be a list")
            canonical_for = []
        for topic_id in canonical_for:
            entry = topics.get(topic_id)
            if entry is None:
                f.err(
                    "front-matter",
                    f"{_rel(path)}: canonical_for references unknown topic "
                    f"{topic_id!r} (not in {_rel(TOPICS_FILE)})",
                )
            elif str(entry.get("canonical_page")) != rel_to_docs:
                f.err(
                    "front-matter",
                    f"{_rel(path)}: claims canonical_for {topic_id!r}, but "
                    f"{_rel(TOPICS_FILE)} names "
                    f"{entry.get('canonical_page')!r} as that topic's "
                    "canonical_page",
                )

        summarizes = fm.get("summarizes", [])
        if not isinstance(summarizes, list):
            f.err("front-matter", f"{_rel(path)}: summarizes must be a list")
            summarizes = []
        for topic_id in summarizes:
            entry = topics.get(topic_id)
            if entry is None:
                f.err(
                    "front-matter",
                    f"{_rel(path)}: summarizes references unknown topic "
                    f"{topic_id!r} (not in {_rel(TOPICS_FILE)})",
                )
            elif rel_to_docs not in _permitted_summary_pages(entry):
                f.err(
                    "front-matter",
                    f"{_rel(path)}: claims summarizes {topic_id!r}, but is "
                    f"not registered as that topic's worked_example/"
                    f"task_pages/reference_page/allowed_summaries in "
                    f"{_rel(TOPICS_FILE)} — either add it there or drop the "
                    "summarizes claim",
                )


def _check_canonical_pages_declare_ownership(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    """Reverse direction of _check_front_matter_schema: a topic's registered
    canonical_page must itself claim that topic via canonical_for — if it has
    front matter at all. Missing front matter entirely is only a WARN (the
    schema is being rolled out incrementally, not required repo-wide yet)."""
    for topic_id, entry in topics.items():
        if not isinstance(entry, dict) or "canonical_page" not in entry:
            continue
        page_path = DOCS / str(entry["canonical_page"])
        if not page_path.is_file():
            continue  # already reported by _check_referenced_paths_exist
        try:
            fm = load_front_matter(page_path)
        except yaml.YAMLError:
            continue  # already reported by _check_front_matter_schema
        if fm is None:
            f.warn(
                "front-matter",
                f"{_rel(page_path)}: registered as canonical_page for topic "
                f"{topic_id!r} in {_rel(TOPICS_FILE)} but has no front "
                "matter yet",
            )
            continue
        canonical_for = fm.get("canonical_for", [])
        if isinstance(canonical_for, list) and topic_id not in canonical_for:
            f.err(
                "front-matter",
                f"{_rel(page_path)}: is topic {topic_id!r}'s canonical_page "
                f"in {_rel(TOPICS_FILE)}, but its front matter's "
                f"canonical_for does not list {topic_id!r}",
            )


# ---------------------------------------------------------------------------
# Duplicate-paragraph scan (advisory)
# ---------------------------------------------------------------------------


def _strip_front_matter(text: str) -> str:
    m = _FRONT_MATTER_RE.match(text)
    return text[m.end() :] if m else text


def _extract_blocks(text: str) -> list[str]:
    """Blank-line-delimited blocks, with fenced code removed and headings
    dropped. A multi-line table or list stays one block (no blank lines
    between rows/items), which is exactly what lets exact-duplicate tables
    and lists surface without any special-casing."""
    text = _strip_front_matter(text)
    text = _FENCE_RE.sub("", text)
    blocks = re.split(r"\n\s*\n", text)
    result = []
    for block in blocks:
        normalized = " ".join(block.split())
        if not normalized or normalized.startswith("#"):
            continue
        result.append(normalized)
    return result


def _iter_duplicate_scan_files() -> list[Path]:
    files = []
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS).as_posix()
        if path.name in _DUPLICATE_SCAN_EXCLUDE_NAMES:
            continue
        if any(rel.startswith(prefix) for prefix in _DUPLICATE_SCAN_EXCLUDE_PREFIXES):
            continue
        files.append(path)
    return files


def _check_duplicate_paragraphs(f: Findings) -> None:
    by_block: dict[str, set[str]] = defaultdict(set)
    for path in _iter_duplicate_scan_files():
        rel = _rel(path)
        for block in _extract_blocks(path.read_text(encoding="utf-8")):
            if len(block.split()) < _MIN_DUPLICATE_WORDS:
                continue
            by_block[block].add(rel)

    for block, files in by_block.items():
        if len(files) < 2:
            continue
        snippet = block if len(block) <= 100 else block[:97] + "..."
        f.warn(
            "duplicates",
            f"identical block ({len(block.split())} words) verbatim in "
            f"{', '.join(sorted(files))}: {snippet!r}",
        )


# ---------------------------------------------------------------------------


def main() -> int:
    f = Findings()
    topics = _load_topics(f)
    if topics is not None:
        _check_referenced_paths_exist(f, topics)
        _check_canonical_page_uniqueness(f, topics)
        _check_front_matter_schema(f, topics)
        _check_canonical_pages_declare_ownership(f, topics)
    _check_duplicate_paragraphs(f)
    return f.report()


if __name__ == "__main__":
    sys.exit(main())

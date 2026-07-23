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

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TOPICS_FILE = DOCS / "_meta" / "topics.yaml"
TERMINOLOGY_FILE = DOCS / "_meta" / "terminology.yaml"

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
    "reference/github-action-inputs.md",
    "schemas/",
)
_DUPLICATE_SCAN_EXCLUDE_NAMES = frozenset({"CLAUDE.md", "AGENTS.md"})

_MIN_DUPLICATE_WORDS = 40
# Tables get a much lower floor than prose (see _is_table_block): a short,
# copy-pasted reference table is exactly the accidental-duplication pattern
# this scan targets, not just long paragraphs.
_MIN_DUPLICATE_TABLE_WORDS = 10

_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$")


def _strip_fenced_code(text: str) -> str:
    """Remove fenced code blocks the way CommonMark actually delimits them:
    a closing fence must be alone on its own line (only leading whitespace
    before it), using the same delimiter character as the opener with at
    least as many repeats. A naive "find the next occurrence of 3+ of the
    same character anywhere" regex (the previous implementation) closes
    early on an inline backtick run embedded *within* a code line -- e.g. a
    code sample that itself shows ``` fence syntax -- silently leaking part
    of the block's real content into the "prose" the summarizes/duplicate/
    terminology checks scan (PR #619 review)."""
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = _FENCE_OPEN_RE.match(lines[i])
        if m is None:
            out.append(lines[i])
            i += 1
            continue
        fence = m.group(1)
        i += 1
        closer = re.compile(rf"^[ \t]{{0,3}}{fence[0]}{{{len(fence)},}}[ \t]*$")
        while i < n and closer.match(lines[i]) is None:
            i += 1
        i += 1  # skip the closing fence line itself (or EOF, harmlessly)
    return "\n".join(out)


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
    none. Raises yaml.YAMLError on malformed front-matter YAML, or
    ValueError if the front matter parses fine but isn't a mapping (e.g. a
    bare YAML list or scalar) — both left for the caller to report, rather
    than silently treating a non-mapping block as an empty-but-valid one."""
    text = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return None
    data = yaml.safe_load(m.group(1))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"front matter must be a YAML mapping, got {type(data).__name__}"
        )
    return data


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


def _resolves_under(base: Path, value: str) -> Path | None:
    """Join `value` onto `base` and return the resolved path, or None if
    `value` isn't a relative, in-tree, actually-existing path. Rejects an
    absolute `value` outright — the topics.yaml schema is docs-/repo-relative
    paths only, and pathlib's `/` operator would otherwise honor an absolute
    right-hand side outright (`Path("/docs") / "/etc/passwd" ==
    Path("/etc/passwd")`, silently discarding `base`) — checking only the
    *resolved* result would still wrongly accept a machine-local absolute
    path that happens to resolve under `base` on this checkout but not on any
    other.

    Existence of a `..`-bearing path can't be delegated to a single
    filesystem call on every platform: `Path.resolve()` (strict or not)
    lexically collapses `..` even through a phantom, nonexistent
    intermediate segment (`missing/../index.md` resolves straight to the
    real `index.md` even though `missing/` was never created) — and on
    Windows, even a plain `.exists()`/`os.stat()` on the raw (unresolved)
    path does the same lexical collapse as part of the OS's own path
    normalization, unlike POSIX where a literal, unresolved traversal
    through a nonexistent directory genuinely fails (CI: windows-latest,
    PR #619 — two different fix attempts each worked on POSIX and silently
    passed the phantom-component case on Windows anyway). So this walks
    `value`'s components by hand: each `..` is only honored if the
    accumulated path *so far* is a real, existing directory — the OS is
    never asked to interpret a path with an unresolved `..` still in it, so
    there's no per-platform lexical-normalization difference left to
    exploit."""
    if Path(value).is_absolute():
        return None
    current = base
    for part in Path(value).parts:
        if part == ".":
            continue
        if part == "..":
            if not current.is_dir():
                return None
            current = current.parent
        else:
            current = current / part
    if not current.exists():
        return None
    candidate = current.resolve()
    resolved_base = base.resolve()
    if candidate != resolved_base and resolved_base not in candidate.parents:
        return None
    return candidate


def _is_file_under(base: Path, value: str) -> bool:
    candidate = _resolves_under(base, value)
    return candidate is not None and candidate.is_file()


def _docs_relative_key(value: object) -> str:
    """Normalize a docs/-relative path value (e.g. a `canonical_page`
    entry) to its resolved, docs-relative POSIX form, so equivalent
    spellings (`concepts/x.md` vs `./concepts/x.md`) compare equal instead
    of silently bypassing the uniqueness/round-trip checks. Falls back to
    the raw string for a value that escapes docs/ or is malformed — that's
    already reported by `_check_referenced_paths_exist`, not this helper's
    job."""
    resolved = _resolves_under(DOCS, str(value))
    if resolved is None:
        return str(value)
    return resolved.relative_to(DOCS.resolve()).as_posix()


def _exists_under(base: Path, value: str) -> bool:
    candidate = _resolves_under(base, value)
    return candidate is not None and candidate.exists()


def _check_referenced_paths_exist(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    for topic_id, entry in topics.items():
        if not isinstance(entry, dict) or not entry.get("canonical_page"):
            f.err(
                "ownership",
                f"topic {topic_id!r}: missing required 'canonical_page' field",
            )
            continue
        for key in ("canonical_page", "worked_example", "reference_page"):
            value = entry.get(key)
            if value is None:
                continue
            if not _is_file_under(DOCS, str(value)):
                f.err(
                    "ownership",
                    f"topic {topic_id!r}: {key} {value!r} does not exist "
                    "as a file under docs/ (or escapes it via '..'/an "
                    "absolute path)",
                )
        for key in ("task_pages", "allowed_summaries"):
            values = entry.get(key, [])
            if not isinstance(values, list):
                f.err("ownership", f"topic {topic_id!r}: {key} must be a list")
                continue
            for value in values:
                if not _is_file_under(DOCS, str(value)):
                    f.err(
                        "ownership",
                        f"topic {topic_id!r}: {key} entry {value!r} does not "
                        "exist as a file under docs/ (or escapes it via "
                        "'..'/an absolute path)",
                    )
        fact_sources = entry.get("fact_sources", [])
        if not isinstance(fact_sources, list):
            f.err("ownership", f"topic {topic_id!r}: fact_sources must be a list")
        else:
            for value in fact_sources:
                if not _exists_under(ROOT, str(value)):
                    f.err(
                        "ownership",
                        f"topic {topic_id!r}: fact_sources entry {value!r} "
                        "does not exist under the repo root (or escapes it "
                        "via '..'/an absolute path)",
                    )


def _check_canonical_page_uniqueness(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for topic_id, entry in topics.items():
        if isinstance(entry, dict) and entry.get("canonical_page"):
            owners[_docs_relative_key(entry["canonical_page"])].append(topic_id)
    for page, topic_ids in owners.items():
        if len(topic_ids) > 1:
            f.err(
                "ownership",
                f"{page!r} is claimed as canonical_page by multiple topics: "
                # A topic id is a topics.yaml mapping key, so it need not be
                # a str (a malformed registry could use e.g. `123:`) -- str()
                # each one before sorted()/join(), which would otherwise
                # crash on a non-str/non-str comparison or join() input.
                f"{', '.join(sorted(str(t) for t in topic_ids))} — a page "
                "can have at most one owning topic",
            )


def _permitted_summary_pages(entry: dict[str, object]) -> set[str]:
    """The set of normalized docs/-relative pages a topic's registry entry
    permits to reference it via `summarizes` — its worked_example,
    reference_page, and every task_pages/allowed_summaries entry.
    Normalized through `_docs_relative_key` (not just `str()`) so an
    equivalent-but-differently-spelled registry entry (e.g.
    `./user-guide/scan-levels.md`) still matches a page's resolved
    `rel_to_docs`, the same way `canonical_page` comparisons already do."""
    pages: set[str] = set()
    for key in ("worked_example", "reference_page"):
        value = entry.get(key)
        if value is not None:
            pages.add(_docs_relative_key(value))
    for key in ("task_pages", "allowed_summaries"):
        values = entry.get(key, [])
        if isinstance(values, list):
            pages.update(_docs_relative_key(v) for v in values)
    return pages


#: (?<!!) excludes image syntax (`![alt](src)` / `![alt][label]`) -- an
#: image embed is not a navigable link, even though its bracket/paren shape
#: otherwise matches the same pattern as a real link.
_MD_LINK_TARGET_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
_BACKTICK_RUN_RE = re.compile(r"`+")
_MD_REF_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\[([^\]]*)\]")
_MD_REF_DEF_RE = re.compile(r"^\[([^\]]+)\]:\s*(\S+)", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_inline_code(text: str) -> str:
    """Remove CommonMark inline code spans, whose delimiter is a run of one
    or more backticks -- not just a single backtick. A span's content ends
    at the *next run of the same length*, which is why `` ``code with a `
    backtick`` `` uses a double-backtick delimiter: it lets the content
    contain a literal single backtick. Stripping only single-backtick spans
    (the previous implementation) left a link exposed as scannable "prose"
    when shown inside a longer-delimiter span (PR #619 review). An opening
    run with no matching same-length closer is left as literal text, per
    CommonMark."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        m = _BACKTICK_RUN_RE.match(text, i)
        if m is None:
            out.append(text[i])
            i += 1
            continue
        run_len = m.end() - i
        j = m.end()
        closer = None
        while j < n:
            m2 = _BACKTICK_RUN_RE.match(text, j)
            if m2 is None:
                j += 1
                continue
            if m2.end() - j == run_len:
                closer = m2
                break
            j = m2.end()
        if closer is None:
            out.append(text[i : m.end()])
            i = m.end()
        else:
            i = closer.end()
    return "".join(out)


def _resolve_href(path: Path, href: str) -> str | None:
    """Clean a Markdown link target (strip an optional title/anchor) and
    resolve it to a docs/-relative POSIX path, or None if it's external,
    absolute, or doesn't resolve under `DOCS`."""
    href = href.strip().split(" ", 1)[0].split("#", 1)[0]
    if not href or "://" in href or href.startswith(("mailto:", "/")):
        return None
    resolved = (path.parent / href).resolve()
    try:
        return resolved.relative_to(DOCS.resolve()).as_posix()
    except ValueError:
        return None


def _page_links_to(path: Path, target_rel_to_docs: str) -> bool:
    """True if `path`'s Markdown body contains a link (inline `[text](url)`
    or reference-style `[text][label]`/`[text][]` with a `[label]: url`
    definition) resolving to `target_rel_to_docs` (a docs/-relative POSIX
    path). The whole point of `summarizes` is "link back to the canonical
    page instead of restating it" — being a permitted summarizer (registered
    in topics.yaml) isn't the same as actually doing that, so this enforces
    the link exists. Fenced code blocks, inline code spans, and HTML
    comments are stripped first: a link shown inside a ``` fence or as
    inline code (e.g. `` `[owner](owner.md)` ``, showing the link syntax
    itself rather than a real link) is example text, and a link hidden
    inside `<!-- ... -->` is invisible in the rendered page -- neither is a
    navigable backlink, even though the raw regex would otherwise match
    both."""
    text = _strip_fenced_code(_strip_front_matter(path.read_text(encoding="utf-8")))
    text = _strip_inline_code(text)
    text = _HTML_COMMENT_RE.sub("", text)
    for m in _MD_LINK_TARGET_RE.finditer(text):
        if _resolve_href(path, m.group(1)) == target_rel_to_docs:
            return True
    # Reference-style links: [text][label] / [text][] -- resolve `label`
    # (or `text` for the collapsed [text][] form) against a `[label]: url`
    # definition anywhere in the document. CommonMark reference labels are
    # matched case-insensitively; the bare shortcut form ([label] with no
    # second bracket pair) is deliberately not handled here -- it's
    # indistinguishable from non-link bracketed prose (e.g. "[[nodiscard]]")
    # without a much heavier parser, and isn't used anywhere in this repo.
    definitions = {
        label.strip().casefold(): url for label, url in _MD_REF_DEF_RE.findall(text)
    }
    for link_text, label in _MD_REF_LINK_RE.findall(text):
        key = (label or link_text).strip().casefold()
        url = definitions.get(key)
        if url and _resolve_href(path, url) == target_rel_to_docs:
            return True
    return False


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
        except (yaml.YAMLError, ValueError) as exc:
            f.err("front-matter", f"{_rel(path)}: invalid front matter: {exc}")
            continue
        if fm is None:
            continue
        if fm.get("generated") is True:
            continue  # generated pages don't carry the hand-authored schema

        doc_type = fm.get("doc_type")
        if doc_type is not None:
            if not isinstance(doc_type, str):
                # `x not in a_frozenset_of_str` requires x to be hashable --
                # an unhashable value (a YAML list/mapping, e.g. a malformed
                # "doc_type: [how-to]") would otherwise raise TypeError
                # before this gate can report anything.
                f.err("front-matter", f"{_rel(path)}: doc_type must be a string")
            elif doc_type not in _ALLOWED_DOC_TYPES:
                f.err(
                    "front-matter",
                    f"{_rel(path)}: doc_type {doc_type!r} not in "
                    f"{sorted(_ALLOWED_DOC_TYPES)}",
                )
        level = fm.get("level")
        if level is not None:
            if not isinstance(level, str):
                f.err("front-matter", f"{_rel(path)}: level must be a string")
            elif level not in _ALLOWED_LEVELS:
                f.err(
                    "front-matter",
                    f"{_rel(path)}: level {level!r} not in {sorted(_ALLOWED_LEVELS)}",
                )
        audience = fm.get("audience")
        if audience is not None and not isinstance(audience, list):
            f.err("front-matter", f"{_rel(path)}: audience must be a list")
        depends_on = fm.get("depends_on")
        if depends_on is not None and not isinstance(depends_on, list):
            f.err("front-matter", f"{_rel(path)}: depends_on must be a list")
        lifecycle = fm.get("lifecycle")
        if lifecycle is not None:
            if not isinstance(lifecycle, str):
                f.err("front-matter", f"{_rel(path)}: lifecycle must be a string")
            elif lifecycle not in _ALLOWED_LIFECYCLES:
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
            if not isinstance(topic_id, str):
                f.err(
                    "front-matter",
                    f"{_rel(path)}: canonical_for entry {topic_id!r} must be "
                    "a topic-id string",
                )
                continue
            entry = topics.get(topic_id)
            if entry is None:
                f.err(
                    "front-matter",
                    f"{_rel(path)}: canonical_for references unknown topic "
                    f"{topic_id!r} (not in {_rel(TOPICS_FILE)})",
                )
            elif not isinstance(entry, dict):
                f.err(
                    "front-matter",
                    f"{_rel(path)}: canonical_for references topic "
                    f"{topic_id!r}, but its entry in {_rel(TOPICS_FILE)} is "
                    "not a mapping",
                )
            elif _docs_relative_key(entry.get("canonical_page")) != rel_to_docs:
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
            if not isinstance(topic_id, str):
                f.err(
                    "front-matter",
                    f"{_rel(path)}: summarizes entry {topic_id!r} must be a "
                    "topic-id string",
                )
                continue
            entry = topics.get(topic_id)
            if entry is None:
                f.err(
                    "front-matter",
                    f"{_rel(path)}: summarizes references unknown topic "
                    f"{topic_id!r} (not in {_rel(TOPICS_FILE)})",
                )
            elif not isinstance(entry, dict):
                f.err(
                    "front-matter",
                    f"{_rel(path)}: summarizes references topic {topic_id!r}, "
                    f"but its entry in {_rel(TOPICS_FILE)} is not a mapping",
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
            elif entry.get("canonical_page") and not _page_links_to(
                path, _docs_relative_key(entry["canonical_page"])
            ):
                f.err(
                    "front-matter",
                    f"{_rel(path)}: claims summarizes {topic_id!r}, but "
                    f"contains no Markdown link to that topic's "
                    f"canonical_page ({entry['canonical_page']!r}) — the "
                    "whole point of summarizes is to link back rather than "
                    "restate the topic on its own",
                )


def _check_canonical_pages_declare_ownership(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    """Reverse direction of _check_front_matter_schema: a topic's registered
    canonical_page must itself claim that topic via canonical_for — if it has
    front matter at all. Missing front matter entirely is only a WARN (the
    schema is being rolled out incrementally, not required repo-wide yet)."""
    for topic_id, entry in topics.items():
        if not isinstance(entry, dict) or not entry.get("canonical_page"):
            continue
        resolved = _resolves_under(DOCS, str(entry["canonical_page"]))
        if resolved is None or not resolved.is_file():
            continue  # already reported by _check_referenced_paths_exist
        page_path = resolved
        try:
            fm = load_front_matter(page_path)
        except (yaml.YAMLError, ValueError):
            continue  # already reported by _check_front_matter_schema
        if fm is None:
            f.warn(
                "front-matter",
                f"{_rel(page_path)}: registered as canonical_page for topic "
                f"{topic_id!r} in {_rel(TOPICS_FILE)} but has no front "
                "matter yet",
            )
            continue
        if fm.get("generated") is True:
            # Unlike _check_front_matter_schema's blanket generated skip
            # (which just means "don't enforce the hand-authored schema on
            # this page"), a topic's *canonical_page* specifically claims to
            # be the narrative owner -- a machine-generated page can't be
            # that by definition, so this is a real registry misconfiguration
            # (register it as reference_page instead), not something to wave
            # through silently.
            f.err(
                "ownership",
                f"{_rel(page_path)}: is topic {topic_id!r}'s canonical_page "
                f"in {_rel(TOPICS_FILE)}, but is marked generated: true -- a "
                "canonical_page must be hand-authored (register a generated "
                "page as reference_page instead)",
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
# Terminology registry checks
# ---------------------------------------------------------------------------


def _load_terminology(f: Findings) -> dict[str, dict[str, object]] | None:
    if not TERMINOLOGY_FILE.is_file():
        # Unlike topics.yaml, terminology.yaml has no hard floor of pilot
        # content required to exist -- absence is not reported as an error,
        # only its presence-and-well-formedness once it exists.
        return None
    try:
        data = yaml.safe_load(TERMINOLOGY_FILE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        f.err("terminology", f"{_rel(TERMINOLOGY_FILE)}: invalid YAML: {exc}")
        return None
    if not isinstance(data, dict) or "terms" not in data:
        f.err("terminology", f"{_rel(TERMINOLOGY_FILE)}: missing top-level 'terms' key")
        return None
    terms = data["terms"]
    if not isinstance(terms, dict):
        f.err("terminology", f"{_rel(TERMINOLOGY_FILE)}: 'terms' must be a mapping")
        return None
    return terms


def _check_terminology_entries(
    f: Findings, terms: dict[str, dict[str, object]]
) -> None:
    """Unlike topics.yaml's canonical_page, a term's canonical_page need not
    be unique -- two terms (e.g. ABI/API) may legitimately share a defining
    page. Only existence and required-field presence are checked here."""
    for term, entry in terms.items():
        if not isinstance(term, str):
            f.err("terminology", f"term {term!r}: term id must be a string")
            continue
        if not isinstance(entry, dict):
            f.err("terminology", f"term {term!r}: entry must be a mapping")
            continue
        canonical_page = entry.get("canonical_page")
        if not canonical_page:
            f.err(
                "terminology",
                f"term {term!r}: missing required 'canonical_page' field",
            )
        elif not isinstance(canonical_page, str):
            f.err(
                "terminology",
                f"term {term!r}: canonical_page must be a string, got "
                f"{type(canonical_page).__name__}",
            )
        elif not _is_file_under(DOCS, canonical_page):
            f.err(
                "terminology",
                f"term {term!r}: canonical_page {canonical_page!r} does not "
                "exist as a file under docs/ (or escapes it via '..'/an "
                "absolute path)",
            )
        short_definition = entry.get("short_definition")
        if not short_definition:
            f.err(
                "terminology",
                f"term {term!r}: missing required 'short_definition' field",
            )
        elif not isinstance(short_definition, str):
            f.err(
                "terminology",
                f"term {term!r}: short_definition must be a string, got "
                f"{type(short_definition).__name__}",
            )
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list):
            f.err("terminology", f"term {term!r}: aliases must be a list")


_DEFINITION_CONNECTORS = (
    r"is\b",
    r"means\b",
    r"refers to\b",
    r"stands for\b",
    r"—",
    r"--",
)


def _term_definition_re(term: str) -> re.Pattern[str]:
    connectors = "|".join(_DEFINITION_CONNECTORS)
    return re.compile(rf"\*\*{re.escape(term)}\*\*\s+(?:{connectors})")


def _check_duplicate_term_definitions(
    f: Findings, terms: dict[str, dict[str, object]]
) -> None:
    """WARN if a page other than a term's registered canonical_page appears
    to define it itself (a bolded term immediately followed by a definition
    connector, e.g. "**ABI** -- ..." or "**ABI** is ..."), rather than
    linking to the canonical definition. Deliberately narrow: this only
    fires on an actual define-the-term pattern, not on the term merely being
    mentioned or linked -- a broader "term appears on another page" check
    would flag ordinary, correct usage constantly."""
    for term, entry in terms.items():
        if not isinstance(term, str) or not isinstance(entry, dict):
            continue
        canonical_page = entry.get("canonical_page")
        if not canonical_page or not isinstance(canonical_page, str):
            continue
        canonical_key = _docs_relative_key(canonical_page)
        pattern = _term_definition_re(term)
        for path in _iter_duplicate_scan_files():
            if _docs_relative_key(str(path.relative_to(DOCS))) == canonical_key:
                continue
            text = _strip_front_matter(path.read_text(encoding="utf-8"))
            text = _strip_fenced_code(text)
            if pattern.search(text):
                f.warn(
                    "terminology",
                    f"{_rel(path)}: appears to define {term!r} itself "
                    f"(a bolded term followed by a definition connector) "
                    f"instead of linking to its canonical_page "
                    f"({entry['canonical_page']!r} in "
                    f"{_rel(TERMINOLOGY_FILE)})",
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
    text = _strip_fenced_code(text)
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


def _is_table_block(block: str) -> bool:
    """True if an (already whitespace-normalized) block is a Markdown table
    -- its first cell starts with `|`. Tables are exempt from the 40-word
    prose threshold below: a short severity/exit-code table copied verbatim
    is exactly the kind of accidental duplication this scan exists to catch,
    even at just a few rows."""
    return block.startswith("|")


def _check_duplicate_paragraphs(f: Findings) -> None:
    by_block: dict[str, set[str]] = defaultdict(set)
    for path in _iter_duplicate_scan_files():
        rel = _rel(path)
        for block in _extract_blocks(path.read_text(encoding="utf-8")):
            word_count = len(block.split())
            min_words = (
                _MIN_DUPLICATE_TABLE_WORDS
                if _is_table_block(block)
                else _MIN_DUPLICATE_WORDS
            )
            if word_count < min_words:
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
    argparse.ArgumentParser(description=__doc__).parse_args()
    f = Findings()
    topics = _load_topics(f)
    if topics is not None:
        _check_referenced_paths_exist(f, topics)
        _check_canonical_page_uniqueness(f, topics)
        _check_front_matter_schema(f, topics)
        _check_canonical_pages_declare_ownership(f, topics)
    terms = _load_terminology(f)
    if terms is not None:
        _check_terminology_entries(f, terms)
        _check_duplicate_term_definitions(f, terms)
    _check_duplicate_paragraphs(f)
    return f.report()


if __name__ == "__main__":
    sys.exit(main())

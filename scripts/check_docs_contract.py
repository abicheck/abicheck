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
      full explanation;
    - a manual page naming a retired CLI flag/command/file by its exact dead
      spelling outside its allowlist (see _RETIRED_SURFACES below).

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
# This script's own directory, so the sibling `findings_report` module below
# imports whether this file is run directly (Python adds it automatically) or
# loaded from its path by `tests/test_docs_contract.py` (Python doesn't) — same
# bootstrap `check_ai_readiness.py` uses for its own siblings.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from findings_report import Findings as _SharedFindings  # noqa: E402
from learning_ladder import check_learning_ladder  # noqa: E402
from pipeline_status_ledger import (  # noqa: E402
    check_pipeline_status_ledger,
    load_pipeline_status,
)

DOCS = ROOT / "docs"
#: The example-case tree whose per-case READMEs `gen_examples_docs.py`
#: publishes into `docs/reference/examples/`. Named separately from ROOT so
#: the retired-surface sweep can be pointed at a fixture tree in tests the
#: same way DOCS already is.
EXAMPLES = ROOT / "examples"
#: The user-flow catalogue. Each entry's `flow:` is a command a reader is
#: meant to be able to run, so it is documentation by another file extension
#: -- swept alongside the trees above, and pointable at a fixture tree the
#: same way.
SCENARIOS = ROOT / "tests" / "scenarios"

#: Trees outside `docs/` whose Markdown may be registered as a topic's
#: `task_pages`/`allowed_summaries` entry (ADR-058 / G36 P0.6). Only
#: `skills-src/shared/` qualifies: those fragments are genuine summary owners
#: that live outside the published tree by design.
#:
#: Deliberately the fragment directory, not `skills-src/` as a whole. Every
#: other Markdown in that tree would satisfy the registry *vacuously* — a
#: `native-*/SKILL.md` carries no `summarizes` front matter for the round-trip
#: to check, and `skills-src/CLAUDE.md` is excluded from front-matter scanning
#: outright. The same reasoning bars a non-Markdown target: no front matter
#: means nothing for the ownership check to contradict.
EXTERNAL_PAGE_ROOTS = ("skills-src/shared",)

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

# Non-prose trees excluded from the duplicate-paragraph scan that don't
# carry the generated-file marker comment (see _has_generated_marker below,
# which is the primary/generic exclusion mechanism) -- structurally
# repetitive by design (per-case pages sharing a template) rather than
# machine-generated. Kept as a narrow backstop, not the main mechanism: a
# hard-coded prefix list silently goes stale when a generated tree moves
# (this one did, from examples/ to reference/examples/ in ADR-051 Stage 4,
# which is why _has_generated_marker is now checked unconditionally too).
_DUPLICATE_SCAN_EXCLUDE_PREFIXES = ()
_DUPLICATE_SCAN_EXCLUDE_NAMES = frozenset({"CLAUDE.md", "AGENTS.md"})

_MIN_DUPLICATE_WORDS = 40
# Tables get a much lower floor than prose (see _is_table_block): a short,
# copy-pasted reference table is exactly the accidental-duplication pattern
# this scan targets, not just long paragraphs.
_MIN_DUPLICATE_TABLE_WORDS = 10

_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$")

#: Every generator in scripts/ marks its output with a leading HTML comment
#: instead of YAML front matter's `generated: true` -- a page carrying it has
#: no front matter at all, so a check that only inspects `fm.get("generated")`
#: never sees it. The wording isn't consistent across generators
#: (gen_detector_spec.py/gen_action_reference.py write "GENERATED by
#: scripts/..."; gen_examples_docs.py writes "DO NOT EDIT — generated by
#: scripts/..."), so match on the "generated by scripts/" substring both
#: share rather than anchoring to one exact phrasing (PR #619 review).
_GENERATED_MARKER_RE = re.compile(r"<!--.*generated by scripts/", re.IGNORECASE)


def _has_generated_marker(path: Path) -> bool:
    """True if `path` opens with a generated-file marker comment used by
    machine-generated docs pages in this repo."""
    text = path.read_text(encoding="utf-8")
    first_line = text.lstrip().split("\n", 1)[0]
    return bool(_GENERATED_MARKER_RE.search(first_line))


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


def _blank_fenced_code(text: str) -> str:
    """Line-count-preserving sibling of `_strip_fenced_code`: replaces each
    line of a fenced code block with an empty line instead of deleting it,
    so a match found in the surrounding prose still lands on its real
    source line number (`_strip_fenced_code` shifts everything after a
    stripped block upward, which is fine for the word-based duplicate scan
    but wrong for a diagnostic that reports `path:line`)."""
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
        out.append("")
        i += 1
        closer = re.compile(rf"^[ \t]{{0,3}}{fence[0]}{{{len(fence)},}}[ \t]*$")
        while i < n and closer.match(lines[i]) is None:
            out.append("")
            i += 1
        if i < n:
            out.append("")  # closing fence line itself
            i += 1
    return "\n".join(out)


def _blank_front_matter(text: str) -> str:
    """Line-count-preserving sibling of `_strip_front_matter` — replaces the
    front-matter block with the same number of blank lines instead of
    deleting it, so line numbers of the text that follows stay accurate."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return text
    consumed = text[: m.end()]
    return "\n" * consumed.count("\n") + text[m.end() :]


class Findings(_SharedFindings):
    """This gate's error/warning collector — the shared one, labelled for it.

    The collection/grouping/printing itself lives in ``findings_report.py``,
    shared with ``check_ai_readiness.py`` so both gates report identically
    without a second copy.
    """

    SUMMARY_LABEL = "docs-contract"


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        # Outside ROOT — only reachable when a test monkeypatches DOCS to a
        # tmp_path fixture; real runs always resolve under ROOT. `.as_posix()`
        # here too (not `str(p)`) so a warning message uses forward slashes
        # on every platform, including Windows CI, where `str(p)` would use
        # backslashes and break any test asserting a `"dir/file.md"`-shaped
        # substring against the message.
        return p.as_posix()


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


def _is_registered_page(value: str) -> bool:
    """True if a `task_pages`/`allowed_summaries` entry names a real file.

    Accepts a repo-relative path *outside* `docs/` the same way `fact_sources`
    already does (ADR-058 / G36 P0.6): `skills-src/shared/*.md` fragments are
    genuine summary owners of a registered topic, but they live outside the
    published tree by design -- they are the DRY source the `.agents/skills/`
    trees are generated from, not doc pages. Everything else about the
    `summarizes` round-trip below applies to them unchanged.

    The outside-`docs/` half is deliberately narrow: a Markdown file under
    `EXTERNAL_PAGE_ROOTS`, not any path that happens to exist. Accepting any
    real file let a registry entry naming, say, `abicheck/foo.py` satisfy
    both this existence check and the ownership round-trip — `load_front_matter`
    returns `None` for a `.py` file, so the round-trip had nothing to
    contradict and passed silently. A source file cannot own or summarize a
    documentation topic; the exception exists for the fragments, not for the
    repository at large.
    """
    if _is_file_under(DOCS, value):
        return True
    if not value.endswith(".md"):
        return False
    candidate = _resolves_under(ROOT, value)
    if candidate is None or not candidate.is_file():
        return False
    return any(
        (ROOT / tree).resolve() in candidate.parents for tree in EXTERNAL_PAGE_ROOTS
    )


def _page_key(value: object) -> str:
    """Normalized identity for a registry page entry or a scanned page:
    docs/-relative when it lives under `docs/`, repo-relative otherwise.

    One function for both sides of the `summarizes` round-trip, so a
    registry entry and the page that claims it compare equal regardless of
    which tree the page lives in."""
    text = str(value)
    docs_candidate = _resolves_under(DOCS, text)
    if docs_candidate is not None and docs_candidate.is_file():
        return docs_candidate.relative_to(DOCS.resolve()).as_posix()
    root_candidate = _resolves_under(ROOT, text)
    if root_candidate is not None and root_candidate.is_file():
        return root_candidate.relative_to(ROOT.resolve()).as_posix()
    return _docs_relative_key(text)


def _scanned_page_key(path: Path) -> str:
    """The identity of a page being scanned, matching `_page_key`'s output for
    the registry entry that names it: docs/-relative under `docs/`,
    repo-relative otherwise. Takes a real (absolute) Path, which
    `_page_key`'s string-and-registry-relative resolution deliberately
    rejects."""
    resolved = path.resolve()
    docs_root = DOCS.resolve()
    if docs_root == resolved or docs_root in resolved.parents:
        return resolved.relative_to(docs_root).as_posix()
    return resolved.relative_to(ROOT.resolve()).as_posix()


def _registered_external_pages(topics: dict[str, dict[str, object]]) -> list[Path]:
    """Every registered `task_pages`/`allowed_summaries` entry that resolves
    outside `docs/`. `_check_front_matter_schema` scans `DOCS.rglob("*.md")`,
    so without this these pages' `summarizes` claims would sit in the registry
    entirely unchecked.

    Registry-derived by design, and deliberately not widened to every file
    under `EXTERNAL_PAGE_ROOTS`: this feeds a `DOCS`-scoped scan whose callers
    patch `DOCS` alone, so pulling real repo files in through `ROOT` would
    make that scan depend on the working tree. The *unregistered* fragment
    that claims a topic is caught by
    `_check_external_pages_claim_only_registered_topics` instead.
    """
    out: set[Path] = set()
    for entry in topics.values():
        if not isinstance(entry, dict):
            continue
        for key in ("task_pages", "allowed_summaries"):
            values = entry.get(key, [])
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value)
                if _is_file_under(DOCS, text) or not _is_registered_page(text):
                    continue
                candidate = _resolves_under(ROOT, text)
                if candidate is not None and candidate.is_file():
                    out.add(candidate)
    return sorted(out)


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
                if not _is_registered_page(str(value)):
                    f.err(
                        "ownership",
                        f"topic {topic_id!r}: {key} entry {value!r} does not "
                        "exist as a file under docs/ or the repo root (or "
                        "escapes it via '..'/an absolute path)",
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
            pages.add(_page_key(value))
    for key in ("task_pages", "allowed_summaries"):
        values = entry.get(key, [])
        if isinstance(values, list):
            pages.update(_page_key(v) for v in values)
    return pages


#: (?<!!) excludes image syntax (`![alt](src)` / `![alt][label]`) -- an
#: image embed is not a navigable link, even though its bracket/paren shape
#: otherwise matches the same pattern as a real link.
_MD_LINK_TARGET_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
_BACKTICK_RUN_RE = re.compile(r"`+")
_MD_REF_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\[([^\]]*)\]")
#: CommonMark allows a link reference definition to be indented 0-3 spaces
#: (same as other block constructs) -- anchoring straight to column 0 would
#: miss a validly-indented definition, e.g. "  [owner]: owner.md" (PR #619
#: review).
_MD_REF_DEF_RE = re.compile(r"^[ \t]{0,3}\[([^\]]+)\]:\s*(\S+)", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_top_level_indented_code(text: str) -> str:
    """Blank out top-level indented (4-space) code blocks, line count intact.

    Deliberately narrower than `gen_agent_skills._indented_code_spans`, which
    tracks each open list item's content column so it can classify indents
    *inside* lists. That machinery does not belong in this gate: here the two
    error directions are asymmetric. Over-stripping would reject a real
    backlink and fail the build on a correct page; under-stripping only lets a
    backlink shown inside a list-nested example count, which is the behaviour
    that already shipped. So a run is treated as code only where the reading
    is unambiguous — blank-line separated, at top level, with no list open.
    """
    lines = text.split("\n")
    out: list[str] = []
    list_open = False
    in_code = False
    for index, line in enumerate(lines):
        indented = line.startswith(("    ", "\t"))
        if not line.strip():
            out.append(line)  # a blank line neither opens nor closes a block
            continue
        if in_code and indented:
            out.append("")
            continue
        in_code = False
        if re.match(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])\s", line):
            list_open = True
        elif not line.startswith((" ", "\t")):
            list_open = False
        blank_before = index == 0 or not lines[index - 1].strip()
        if indented and blank_before and not list_open:
            in_code = True
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


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
    """Clean a Markdown link target (strip an optional title/anchor, and an
    optional CommonMark angle-bracket destination wrapper -- `[text](<url>)`
    is valid Markdown, and MkDocs renders it as a normal link, so `<` and `>`
    must not end up as part of the resolved filesystem path) and resolve it
    to a docs/-relative POSIX path, or None if it's external, absolute, or
    doesn't resolve under `DOCS`."""
    href = href.strip()
    if href.startswith("<"):
        end = href.find(">")
        href = href[1:end] if end != -1 else href[1:]
    else:
        href = href.split(" ", 1)[0]
    href = href.split("#", 1)[0]
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
    the link exists. Fenced code blocks, top-level indented code blocks,
    inline code spans, and HTML
    comments are stripped first: a link shown inside a ``` fence or as
    inline code (e.g. `` `[owner](owner.md)` ``, showing the link syntax
    itself rather than a real link) is example text, and a link hidden
    inside `<!-- ... -->` is invisible in the rendered page -- neither is a
    navigable backlink, even though the raw regex would otherwise match
    both."""
    text = _strip_fenced_code(_strip_front_matter(path.read_text(encoding="utf-8")))
    text = _strip_top_level_indented_code(text)
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


def _check_external_pages_claim_only_registered_topics(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    """The page-to-registry direction for out-of-`docs/` fragments.

    `_check_front_matter_schema` performs that round-trip, but only over pages
    the registry already names — so a fragment declaring `summarizes: topic-x`
    that was never added to `topic-x` is invisible to it, which is precisely
    the violation it exists to catch. Scanned by tree here rather than by
    registry, so an unregistered claim cannot hide by being unregistered.
    """
    for tree in EXTERNAL_PAGE_ROOTS:
        for path in sorted((ROOT / tree).rglob("*.md")):
            try:
                fm = load_front_matter(path)
            except (yaml.YAMLError, ValueError) as exc:
                f.err("front-matter", f"{_rel(path)}: invalid front matter: {exc}")
                continue
            if not isinstance(fm, dict):
                continue
            raw_claims = fm.get("summarizes")
            if raw_claims is None:
                claims: list[object] = []
            elif isinstance(raw_claims, list):
                claims = raw_claims
            else:
                # Reported here rather than silently skipped. This tree-wide
                # scan is the *only* thing that visits an unregistered
                # fragment — `_check_front_matter_schema` walks pages the
                # registry names — so coercing a malformed value to an empty
                # list left both the bad type and the missing round-trip
                # unreported for exactly the files this scan exists to reach.
                f.err("front-matter", f"{_rel(path)}: summarizes must be a list")
                continue
            for topic_id in [str(c) for c in claims]:
                entry = topics.get(topic_id)
                if not isinstance(entry, dict):
                    f.err(
                        "front-matter",
                        f"{_rel(path)}: summarizes unknown topic {topic_id!r}",
                    )
                    continue
                # `isinstance` rather than `or []`, matching every other
                # iteration of these two keys in this file. A malformed
                # non-list value (`task_pages: 1`) is already reported as a
                # schema error by `_check_registry_integrity`; iterating it
                # here would raise `TypeError` first and take the whole gate
                # down with a traceback, losing that finding and every other
                # one alongside it.
                registered: list[str] = []
                for key in ("task_pages", "allowed_summaries"):
                    values = entry.get(key)
                    if isinstance(values, list):
                        registered.extend(str(v) for v in values)
                if not any(_page_key(v) == _scanned_page_key(path) for v in registered):
                    f.err(
                        "front-matter",
                        f"{_rel(path)}: claims to summarize {topic_id!r} but is "
                        f"not listed in that topic's task_pages/allowed_summaries "
                        "— a page cannot grant itself permission to restate a "
                        "topic",
                    )


def _check_external_summary_pages_claim_their_topics(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    """Enforce the registry-to-page direction for non-`docs/` entries.

    `_check_front_matter_schema` runs page-to-registry: a page's `summarizes`
    ids must round-trip. That check is vacuous for a page with no front matter
    (it `continue`s) or one whose front matter simply omits the claim — so an
    approved `skills-src/shared/*.md` file could be registered as a topic's
    summary, never claim the topic, never link its canonical page, and still
    pass. Inside `docs/` the front-matter schema is only being rolled out
    incrementally, so silence there is deliberate; a fragment registered under
    ADR-058's exception is opting in by construction and must claim what it
    was registered for.
    """
    # Sorted through `str`, not on the raw key: a malformed numeric topic id
    # alongside the normal string ones makes a bare `sorted` raise `TypeError`
    # comparing `int` to `str`, which would abort the gate before it could
    # report the registry errors it has already collected.
    for topic_id, entry in sorted(topics.items(), key=lambda item: str(item[0])):
        if not isinstance(entry, dict):
            continue
        for key in ("task_pages", "allowed_summaries"):
            values = entry.get(key, [])
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value)
                if _is_file_under(DOCS, text) or not _is_registered_page(text):
                    continue
                path = _resolves_under(ROOT, text)
                if path is None or not path.is_file():
                    continue  # existence is _check_referenced_paths_exist's job
                try:
                    fm = load_front_matter(path)
                except (yaml.YAMLError, ValueError):
                    continue  # reported by the front-matter scan
                if isinstance(fm, dict) and fm.get("generated") is True:
                    # `_check_front_matter_schema` skips a generated page
                    # wholesale, backlink requirement included — so this claim
                    # would otherwise switch off the very enforcement that
                    # makes registering an out-of-docs summary safe. It is
                    # also simply false: every `EXTERNAL_PAGE_ROOTS` tree is
                    # hand-authored source that generators read, never write.
                    f.err(
                        "front-matter",
                        f"{_rel(path)}: registered as topic {str(topic_id)!r}'s "
                        f"{key} entry but declares `generated: true` — these "
                        "trees are hand-authored sources, and the claim skips "
                        "the schema check that requires a canonical-page "
                        "backlink",
                    )
                    continue
                claimed = fm.get("summarizes") if isinstance(fm, dict) else None
                claimed = claimed if isinstance(claimed, list) else []
                if topic_id not in [str(c) for c in claimed]:
                    f.err(
                        "front-matter",
                        f"{_rel(path)}: registered as topic {topic_id!r}'s "
                        f"{key} entry but its front matter does not list "
                        f"{topic_id!r} under `summarizes` — an out-of-docs "
                        "summary page must claim the topic it is registered "
                        "for, or the round-trip passes vacuously",
                    )


def _check_front_matter_schema(
    f: Findings, topics: dict[str, dict[str, object]]
) -> None:
    """Validate front matter on every manual page that has any, and
    cross-check `canonical_for`/`summarizes` against the topic registry.

    Scans `docs/` plus every registered non-`docs/` summary page (ADR-058's
    `skills-src/shared/*.md` fragments), so a `summarizes` claim outside the
    published tree is enforced rather than merely recorded."""
    scanned = sorted(DOCS.rglob("*.md")) + _registered_external_pages(topics)
    for path in scanned:
        if path.name in _DUPLICATE_SCAN_EXCLUDE_NAMES:
            continue
        rel_to_docs = _scanned_page_key(path)
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
            elif _page_key(entry.get("canonical_page")) != rel_to_docs:
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
            ):  # canonical_page is always docs/-relative (enforced above)
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
            if _has_generated_marker(page_path):
                # A page with no front matter at all could either be an
                # ordinary page mid-rollout (WARN below) or a machine-
                # generated page (the marker-comment convention, not YAML
                # front matter's generated: true) -- the latter is the same
                # real ownership misconfiguration the fm.get("generated")
                # branch below catches, just via the other convention.
                f.err(
                    "ownership",
                    f"{_rel(page_path)}: is topic {topic_id!r}'s canonical_page "
                    f"in {_rel(TOPICS_FILE)}, but carries the generated-file "
                    "marker comment -- a canonical_page must be hand-authored "
                    "(register a generated page as reference_page instead)",
                )
                continue
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


def _term_definition_re(names: list[str]) -> re.Pattern[str]:
    """Build a regex that fires on a bolded-definition pattern for `names[0]`
    (the canonical term) *or any of its registered aliases* -- a page
    defining "**Application Binary Interface** is ..." owns the same
    registry entry as "**ABI**" and must be caught the same way, or the
    one-definition-owner rule silently misses every alias spelling."""
    connectors = "|".join(_DEFINITION_CONNECTORS)
    alternation = "|".join(re.escape(n) for n in names)
    return re.compile(rf"\*\*(?:{alternation})\*\*\s+(?:{connectors})")


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
        aliases = entry.get("aliases", [])
        names = (
            [term] + [a for a in aliases if isinstance(a, str)]
            if isinstance(aliases, list)
            else [term]
        )
        pattern = _term_definition_re(names)
        for path in _iter_duplicate_scan_files():
            if _docs_relative_key(str(path.relative_to(DOCS))) == canonical_key:
                continue
            text = _strip_front_matter(path.read_text(encoding="utf-8"))
            text = _strip_fenced_code(text)
            text = _strip_inline_code(text)
            text = _HTML_COMMENT_RE.sub("", text)
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
        if _has_generated_marker(path):
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


# Retired-surface registry: a literal identifier (file path, CLI/API name)
# that names something genuinely removed from the shipped product. Unlike
# a general topic word (e.g. "MCP" alone, which legitimately appears in
# historical framing all over contribute/adr and contribute/plans), each
# string below only ever makes sense today in a "this used to exist"
# sentence -- there is no live code, doc, or command it could otherwise be
# referring to. Deliberately narrower than a full "no present-tense claim
# about a retired capability" detector (which would need per-document
# semantic judgment this pure-text scan can't do) -- this catches exactly
# the class of bug found in a documentation review: a manual page in
# start/, learn/, use/, or reference/ still pointing at a since-deleted
# file or command as if it were live (docs/reference/mcp-tools-reference.md,
# scripts/gen_mcp_reference.py, --source-abi, and similar were all found
# and fixed this way before this check existed). contribute/adr,
# contribute/plans, and contribute/archive are skipped for the same reason
# _STALE_PROCESS_LANGUAGE_EXEMPT_PREFIXES skips them: they are allowed to
# discuss retired surfaces in their own historical-record capacity. Add an
# entry here whenever a PR deletes a CLI flag/command, a file, or a public
# API name that any doc might still reference by that exact spelling.
_RETIRED_SURFACES: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    (
        "abicheck-mcp (MCP server, removed by #684)",
        (
            "abicheck-mcp",
            "mcp_server.py",
            "gen_mcp_reference.py",
            "mcp-tools-reference.md",
            "abicheck[mcp]",
        ),
        frozenset(
            {"start/upgrading-to-0.6.md", "AGENTS.md", "contribute/known-gaps.md"}
        ),
    ),
    (
        "--source-abi / --source-graph (pre-ADR-043 `collect` command flags)",
        (
            "--source-abi-cache-dir",
            "--source-abi-cache",
            "--source-abi-scope",
            "--source-abi-extractor",
            "--source-abi",
            "--source-graph",
        ),
        frozenset(
            {"use/build-evidence-setup.md", "reference/environment.md", "AGENTS.md"}
        ),
    ),
    (
        "--gcc-options/--gcc-option/--gcc-path/--gcc-prefix (the whole legacy"
        " cross-toolchain family, superseded by --compiler-option/--compiler/"
        " --compiler-prefix -- the Action's own `gcc-options`/`gcc-path`/"
        " `gcc-prefix` inputs, without the leading dashes, are unaffected and"
        " still valid)",
        ("--gcc-options", "--gcc-option", "--gcc-path", "--gcc-prefix"),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                "use/github-action.md",
                # Names the retired spellings once, to point a reader at the
                # --compiler* replacements -- the page documenting the family.
                "use/dump-compare-flags.md",
            }
        ),
    ),
    (
        "--verify-runtime (the consumer-execution probe; it had already been"
        " reduced to a safety no-op, and the static --used-by scanner answers"
        " the same undefined-symbol question without executing anything)",
        ("--verify-runtime",),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--contract-evaluation (folded into --contract, which now both turns"
        " the ADR-049 evaluator on and selects its evidence domain; the"
        " former domain-less form is --contract auto)",
        ("--contract-evaluation",),
        frozenset({"AGENTS.md", "use/contract-evaluation.md"}),
    ),
    (
        "--show-impact (folded into --report-mode impact, which was already"
        " documented as its exact equivalent)",
        ("--show-impact",),
        frozenset(
            {
                "AGENTS.md",
                # A point-in-time design review: §3.3 describes the surface as
                # it was and recommends exactly this fold, so it names the
                # flag in its own historical-record capacity.
                "contribute/config-key-review.md",
            }
        ),
    ),
    (
        "aggregate --expect/--optional/--report-prefix (the expected-target"
        " set is declared by --manifest or --run-plan, or waived with"
        " --discovered-only; the report-filename prefix is fixed)",
        ("--report-prefix", "--expect", "--optional"),
        frozenset({"AGENTS.md"}),
    ),
    (
        "the four per-category --severity-<category> flags (hidden duplicates"
        " of .abicheck.yml's own severity: block, which is now their one"
        " spelling; --severity-preset stays as the coarse per-run override)",
        (
            "--severity-abi-breaking",
            "--severity-potential-breaking",
            "--severity-quality-issues",
            "--severity-addition",
        ),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--strict-suppressions/--require-justification/--public-symbol/"
        "--public-symbols-list/--show-redundant/--collapse-versioned-symbols"
        " (hidden duplicates of the suppression:/scope: config blocks, which"
        " are now their one spelling)",
        (
            "--strict-suppressions",
            "--require-justification",
            "--public-symbols-list",
            "--public-symbol",
            "--show-redundant",
            "--collapse-versioned-symbols",
        ),
        frozenset({"AGENTS.md"}),
    ),
    # `dump --public-header`/`--public-header-dir` are gone too (declaration
    # provenance comes from -H/--header itself now), but they are deliberately
    # NOT registered here: `scan --public-header-dir` is still live, and this
    # gate matches plain substrings, so a pattern that catches the retired
    # `dump` spelling necessarily catches the surviving `scan` one. The
    # executable guard for that pair is `tests/test_cli_contract.py`'s
    # per-command option-set snapshot.
    (
        "dump -p/--build-dir and --compile-db, and scan --compile-db"
        " (--build-info already takes a build dir, a compile_commands.json,"
        " or a pack -- the one flag for that operand; --compile-db-filter"
        " still scopes it)",
        ("--build-dir", "--compile-db"),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                # A point-in-time design review: it inventories the surface
                # as it was, in its own historical-record capacity.
                "contribute/config-key-review.md",
            }
        ),
    ),
    (
        "--policy-file (folded into --policy, which now takes a built-in"
        " profile name or a policy document -- a path, or a packaged built-in"
        " like 'security'; the Action's own `policy-file` input, without the"
        " leading dashes, is unaffected and still valid)",
        ("--policy-file",),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                "reference/github-action-inputs.md",
            }
        ),
    ),
    (
        "--secondary-format/--secondary-output (folded into --write"
        " FORMAT=PATH -- half the pair was a usage error either direction, so"
        " they were one option spelled as two)",
        ("--secondary-format", "--secondary-output"),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--old-ast-frontend/--new-ast-frontend (--ast-frontend is side-aware"
        " on compare: --ast-frontend old=castxml --ast-frontend new=clang,"
        " ADR-040 Lever 1's prefix convention)",
        ("--old-ast-frontend", "--new-ast-frontend"),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--include-dependencies (renamed --include-system-declarations: it"
        " restores the declarations a system/toolchain header contributed to"
        " the AST, which is unrelated to the DT_NEEDED library graph"
        " --follow-deps walks)",
        ("--include-dependencies",),
        frozenset({"AGENTS.md"}),
    ),
    (
        "project validate-use-cases --against/--against-new (resolving a"
        " manifest against a real library, and attributing a comparison's"
        " findings to the use cases that reach them, is compare --use-cases)",
        ("--against-new",),
        frozenset({"AGENTS.md"}),
    ),
    (
        "compare --stat/--recommend (CLI cleanup phase two, PR 1): --stat's"
        " one-line summary moved to the built-in --profile quick; the"
        " release recommendation is now unconditional in json/markdown/"
        " review output, so --recommend has nothing left to opt into",
        ("--stat", "--recommend"),
        frozenset(
            {
                "AGENTS.md",
                # A point-in-time design review, same reasoning as the
                # --show-impact entry above: §3.3 describes the surface as
                # it was, so it names the retired flags in its own
                # historical-record capacity rather than as live usage.
                "contribute/config-key-review.md",
                # Explicit migration notes naming the retired flag and its
                # replacement in the same breath ("--stat was removed ...
                # use --profile quick instead"), not stale live usage.
                "use/output-formats.md",
                "tests/scenarios/ci_gating.yaml",
                # The *left* column of a libabigail-to-abicheck migration
                # table: libabigail's own `--stat` flag (a different tool,
                # same spelling), mapped to abicheck's `--profile quick` in
                # the very next column -- not a stale abicheck mention.
                "use/from-libabigail.md",
            }
        ),
    ),
    (
        "project plan --gate-missing-required/--gate-unexpected-target (CLI"
        " cleanup phase two, PR 2 follow-up): the policy moved to"
        " .abicheck.yml's aggregate: gate: block, durable project config"
        " project plan sources instead of a per-invocation flag",
        ("--gate-missing-required", "--gate-unexpected-target"),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                # Explicit migration notes naming the retired flags and
                # their aggregate: gate: replacement in the same breath,
                # same reasoning as the --stat/--recommend entry above.
                "reference/project-targets-schema.md",
                "reference/run-plan-schema.md",
            }
        ),
    ),
    (
        "compare/compare-release --annotate/--annotate-additions (CLI cleanup"
        " phase two, PR E: the composite Action now renders annotations"
        " itself from the persisted `annotations` report field via its own"
        " `annotate`/`annotate-additions` inputs, so the CLI flags were"
        " removed entirely)",
        ("--annotate", "--annotate-additions"),
        frozenset(
            {
                "AGENTS.md",
                # Migration guidance: names the retired CLI spelling once,
                # to point a reader at the `annotate`/`annotate-additions`
                # Action inputs that replaced it.
                "use/annotations.md",
                # A point-in-time design review predating the removal: it
                # names the flags in their own historical-record capacity
                # (verifying stderr consistency, proposing the
                # --annotate-additions-could-be-inferred idea), same
                # reasoning as the --show-impact entry above.
                "contribute/config-key-review.md",
            }
        ),
    ),
    (
        "compare --old-bundle-facts (G38 Phase 17's single-invocation stored-"
        "BundleFacts flag, superseded by CLI cleanup phase two's PR I -- "
        "automatic operand classification from the `artifact_type` marker,"
        " see `bundle_compare_operand.py`)",
        ("--old-bundle-facts",),
        frozenset(
            {
                "AGENTS.md",
                # G38's own phased plan: the flag's design, its later shift
                # to a boolean, and the 2026-09-03 note recording it as
                # since-superseded -- all in this plan's own historical-
                # record capacity.
                "contribute/plans/g38-bundle-facts-model-and-multibuild-comparability.md",
                # This plan's index row for G38 and for CLI cleanup phase
                # two both name the flag to describe what shipped and was
                # later removed.
                "contribute/plans/index.md",
                # The removal's own plan: names the flag throughout as the
                # subject being deleted (before/after tables, the deletion
                # checklist, the worked example of the second-engine
                # problem it existed to retire).
                "contribute/plans/cli-cleanup-phase-two.md",
                # A worked example predating the removal, illustrating the
                # (now-superseded) `--bundle-facts-out`/`--old-bundle-facts`
                # round trip.
                "contribute/plans/learning-series-page-specs.md",
            }
        ),
    ),
    (
        "--exit-code-scheme and .abicheck.yml's top-level exit_code_scheme:"
        " key (ADR-064 / CLI cleanup phase two PR G2 -- there is no manual"
        " gate-algorithm override any more; the algorithm is fully"
        " determined by whether a severity setting is in effect. Note this"
        " is distinct from the still-live, purely-derived report field"
        " `gate.exit_code_scheme`/`scoped_exit_code_scheme`, which is not"
        " a settable surface and is not matched by these patterns)",
        ("--exit-code-scheme", "exit_code_scheme:"),
        frozenset(
            {
                # Historical "what changed" migration note explaining the
                # old scoped-severity fix, including that its manual pin
                # was later removed.
                "start/upgrading-to-0.6.md",
                # Explains why `gate.exit_code_scheme` carries no
                # `field_provenance` entry any more -- names the retired
                # flag/key as the thing that used to populate it.
                "reference/compatibility-evaluation-config.md",
                # The "no config key any more" explanation itself names
                # the retired key and flag.
                "reference/config-file.md",
                # The release-path exit-code section explains there is no
                # manual override any more, by naming what was removed.
                "reference/exit-codes.md",
                # "there is no separate `exit_code_scheme:` key" sentence
                # explaining the config file's own severity block.
                "learn/rollout-and-governance.md",
                # Same "no such key any more" explanation as config-file.md.
                "use/build-evidence-setup.md",
                # The rewritten "the two exit-code schemes" section states
                # there is no manual override any more, by naming it.
                "use/ci-gating.md",
                # Historical G22 changelog-style row: names the CLI as it
                # was designed at the time (an explicit --exit-code-scheme),
                # accurate to that point in history.
                "contribute/usecase-coverage-evaluation.md",
            }
        ),
    ),
)


def _retired_surface_scan_targets() -> list[tuple[Path, str]]:
    """Every page the retired-surface sweep reads, with its allowlist key.

    `docs/**/*.md` is the hand-authored narrative tree, keyed docs-relative
    (what `_RETIRED_SURFACES`'s allowlists already spell).

    `examples/case*/README.md` is here because it is the *generator source*
    for the published `docs/reference/examples/case*.md` pages: those carry
    the generated marker and are skipped below, so scanning only the output
    tree left a stale flag in a case README reproducing into a public page on
    the next `gen_examples_docs.py` run while this guard stayed green -- which
    is exactly what happened to Case 148's `--compile-db` recommendation
    (Codex review). Checking the source rather than the artifact is the same
    direction every other generated-file gate in this repo takes.

    `tests/scenarios/*.yaml` is here for the same reason one step further out:
    it is the repository's user-flow catalogue, and each entry's `flow:` is a
    command a reader is meant to be able to run. Its structural tests check
    that a flow *has* an automated counterpart, not that the command it prints
    still parses -- so a scenario kept advertising a removed `scan
    --compile-db` while both this sweep and those tests stayed green (Codex
    review). YAML rather than Markdown, but the same failure and the same fix.

    Keyed repo-relative (`examples/caseNN.../README.md`,
    `tests/scenarios/x.yaml`), which cannot collide with a docs-relative key,
    so an allowlist entry stays unambiguous about which tree it exempts.
    """
    targets = [(p, p.relative_to(DOCS).as_posix()) for p in sorted(DOCS.rglob("*.md"))]
    targets += [
        (p, f"examples/{p.relative_to(EXAMPLES).as_posix()}")
        for p in sorted(EXAMPLES.glob("case*/README.md"))
    ]
    targets += [
        (p, f"tests/scenarios/{p.name}") for p in sorted(SCENARIOS.glob("*.yaml"))
    ]
    return targets


def _check_retired_surfaces(f: Findings) -> None:
    """Flag a manual, non-historical page that still names a retired CLI
    flag/command/file by its exact dead spelling, as if it were live surface.
    Deliberately scans fenced code blocks too (unlike
    _check_stale_process_language, which blanks them) -- a stale command
    inside a ```bash example is exactly the worst place to miss one, since a
    reader is likely to copy-paste it verbatim. Exempts the same lifecycle/
    generated pages _check_stale_process_language does -- a page marked
    historical/migration is allowed to discuss a retired surface in its own
    historical-record capacity, same reasoning as the ADR/plans/archive
    directory exemption below. WARN-only: a hit needs a human read to add
    historical framing or an allowlist entry, not an automatic rewrite."""
    for path, rel in _retired_surface_scan_targets():
        if rel.startswith(_STALE_PROCESS_LANGUAGE_EXEMPT_PREFIXES):
            continue
        if _has_generated_marker(path):
            continue
        try:
            fm = load_front_matter(path)
        except (yaml.YAMLError, ValueError):
            fm = None  # front-matter errors are reported by another check
        if fm is not None:
            if fm.get("generated") is True:
                continue
            lifecycle = fm.get("lifecycle")
            if (
                isinstance(lifecycle, str)
                and lifecycle in _STALE_PROCESS_LANGUAGE_EXEMPT_LIFECYCLES
            ):
                continue
        text = None
        for surface_name, patterns, allowed_paths in _RETIRED_SURFACES:
            if rel in allowed_paths:
                continue
            if text is None:
                text = path.read_text(encoding="utf-8")
            # Longest-first, and skip a shorter pattern's match when it falls
            # entirely inside a longer pattern's already-reported span (e.g.
            # a bare "--source-abi" match sitting inside an already-flagged
            # "--source-abi-cache-dir" occurrence) -- one real dead-surface
            # mention should produce one warning, not one per overlapping
            # registry entry that happens to match the same text.
            reported_spans: list[tuple[int, int]] = []
            for pattern in sorted(patterns, key=len, reverse=True):
                search_from = 0
                while True:
                    idx = text.find(pattern, search_from)
                    if idx == -1:
                        break
                    end = idx + len(pattern)
                    search_from = end
                    # A flag pattern must match a whole token, so a retired
                    # `--compile-db` is found in "`--compile-db`" and
                    # "-p/--compile-db" but not inside the still-live
                    # `--compile-db-filter`. Registering the trailing space
                    # instead (the first attempt) matched only one of those
                    # three and let punctuation-delimited live references
                    # through the gate entirely (Codex review).
                    if pattern.startswith("--") and end < len(text):
                        nxt = text[end]
                        if nxt.isalnum() or nxt in "-_":
                            continue
                    if any(idx >= s and end <= e for s, e in reported_spans):
                        continue
                    reported_spans.append((idx, end))
                    line_no = text.count("\n", 0, idx) + 1
                    f.warn(
                        "retired-surfaces",
                        f"{_rel(path)}:{line_no}: {pattern!r} names a retired "
                        f"surface ({surface_name}) outside its allowed pages -- "
                        "add historical framing or an allowlist entry in "
                        "_RETIRED_SURFACES if this mention is intentional",
                    )


# Trees that are inherently historical/planning records rather than current
# user-facing narrative -- an ADR or a plan file legitimately says "this was
# temporary" or "not yet implemented as of this writing" about its own past
# state, so the phrases below describing an *unfinished current page* aren't
# a staleness signal there the way they are in start/, learn/, use/,
# reference/, or integration/. Mirrors docs/AGENTS.md's own Layout
# description of contribute/ as "governance-facing... archive, plans,
# ADRs" rather than the narrative/task tree this check targets.
_STALE_PROCESS_LANGUAGE_EXEMPT_PREFIXES = (
    "contribute/adr/",
    "contribute/plans/",
    "contribute/archive/",
)
_STALE_PROCESS_LANGUAGE_EXEMPT_LIFECYCLES = frozenset({"migration", "historical"})

# Deliberately phrase-level (not single common words like "temporary", which
# is ordinary technical vocabulary -- "temporary directory", "temporary
# override" -- and would be all false positives): each pattern targets a
# specific "this page/feature is currently unfinished" claim that goes stale
# the moment the described work actually ships, which is exactly what
# happened to the "(being updated in parallel)" parentheticals this check
# was added to catch (found in a documentation review, not caught by any
# existing gate).
_STALE_PROCESS_LANGUAGE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"being updated in parallel",
        r"currently being (?:implemented|written|built|worked on|updated)",
        r"work[- ]in[- ]progress",
        r"\bWIP\b",
        r"this (?:is|section is|page is) (?:currently )?temporary\b",
        r"\bTBD\b",
        r"\bTODO:",
    )
)


#: A ``key.subkey:`` token -- what a `.abicheck.yml` block's setting looks
#: like when it is written inline. Anchored to a whitespace/quote boundary so
#: it cannot match inside a URL, a Python attribute access, or a flag value.
_CONFIG_KEY_OPERAND_RE = re.compile(r"(?:^|[\s'\"])([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*):(?=\s|$)")

#: A shell line invoking the tool, including a backslash-continued one. The
#: subcommand list is deliberately explicit: `abicheck` alone also appears in
#: prose like "abicheck reads .abicheck.yml", which is not a command line.
_ABICHECK_COMMAND_RE = re.compile(
    r"^\s*(?:\$\s*)?abicheck\s+"
    r"(?:compare|scan|dump|aggregate|compat|deps|project|appcompat)\b"
)

#: The Action input that forwards raw argv. Same rule applies to its value:
#: a config key written there reaches Click as a positional operand.
_EXTRA_ARGS_RE = re.compile(r"^\s*extra-args:\s*(.+)$")


def _shell_command_lines(text: str) -> list[tuple[int, str]]:
    """Every ``abicheck <subcommand> ...`` invocation in *text*, with its
    backslash continuations joined, as ``(line_number, command)``."""
    out: list[tuple[int, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if _ABICHECK_COMMAND_RE.match(lines[i]):
            start = i + 1
            parts = [lines[i]]
            while parts[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                parts.append(lines[i])
            out.append((start, " ".join(p.rstrip().rstrip("\\") for p in parts)))
        i += 1
    return out


def _check_config_keys_as_cli_operands(f: Findings) -> None:
    """Flag a documented command line that passes a config key as argv.

    The failure this exists for: when a hidden per-run flag is demoted to a
    ``.abicheck.yml``-only setting, a mechanical rewrite of every mention
    (``--severity-addition error`` -> ``severity.addition: error``) is correct
    in the *prose* naming the key and wrong in every *command line* that used
    to pass the flag -- Click sees two unexpected positional operands and the
    example exits 64. Eight such examples shipped across five pages before a
    reviewer read one of them (Codex review), because nothing distinguishes
    the two contexts by eye.

    Scoped to actual invocations (and the Action's ``extra-args``, which is
    raw argv by another name), so prose and YAML config blocks -- where the
    same token is exactly right -- are untouched. WARN-only, matching the
    retired-surface sweep: the fix is a human decision about which spelling
    the passage meant.
    """
    for path, rel in _retired_surface_scan_targets():
        text = path.read_text(encoding="utf-8")
        for line_no, command in _shell_command_lines(text):
            for m in _CONFIG_KEY_OPERAND_RE.finditer(command):
                f.warn(
                    "config-key-as-cli-operand",
                    f"{_rel(path)}:{line_no}: {m.group(1)!r} is a "
                    ".abicheck.yml key, not a CLI operand -- this command "
                    "exits 64 (Click reads it as unexpected positional "
                    "arguments). Show a config file, or the flag that "
                    "really exists.",
                )
        for i, line in enumerate(text.splitlines(), start=1):
            em = _EXTRA_ARGS_RE.match(line)
            if em is None:
                continue
            for m in _CONFIG_KEY_OPERAND_RE.finditer(em.group(1)):
                f.warn(
                    "config-key-as-cli-operand",
                    f"{_rel(path)}:{i}: {m.group(1)!r} is a .abicheck.yml "
                    "key, but `extra-args` is raw argv -- it reaches Click "
                    "as unexpected positional arguments. Put it in the "
                    "repository's .abicheck.yml instead.",
                )
        del rel


def _check_stale_process_language(f: Findings) -> None:
    """Flag prose that describes the *page itself* (or the feature it
    documents) as unfinished/in-progress -- this class of claim is true only
    until the described work ships, then silently goes stale with nothing to
    catch it (docs-contract's other checks validate structure/links, not
    whether a page's own status claims are still accurate). WARN-only: a hit
    needs a human read to confirm staleness, not an automatic block."""
    for path in sorted(DOCS.rglob("*.md")):
        if path.name in _DUPLICATE_SCAN_EXCLUDE_NAMES:
            continue
        rel = path.relative_to(DOCS).as_posix()
        if rel.startswith(_STALE_PROCESS_LANGUAGE_EXEMPT_PREFIXES):
            continue
        if _has_generated_marker(path):
            continue
        try:
            fm = load_front_matter(path)
        except (yaml.YAMLError, ValueError):
            fm = None  # front-matter errors are reported by another check
        if fm is not None:
            if fm.get("generated") is True:
                continue
            lifecycle = fm.get("lifecycle")
            if (
                isinstance(lifecycle, str)
                and lifecycle in _STALE_PROCESS_LANGUAGE_EXEMPT_LIFECYCLES
            ):
                continue

        text = _blank_fenced_code(path.read_text(encoding="utf-8"))
        text = _blank_front_matter(text)
        for pattern in _STALE_PROCESS_LANGUAGE_PATTERNS:
            m = pattern.search(text)
            if m is None:
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            f.warn(
                "stale-process-language",
                f"{_rel(path)}:{line_no}: {m.group(0)!r} reads as an "
                "in-progress/unfinished status claim -- confirm it's still "
                "accurate, or drop it if the described work has shipped",
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
        _check_external_summary_pages_claim_their_topics(f, topics)
        _check_external_pages_claim_only_registered_topics(f, topics)
        _check_canonical_pages_declare_ownership(f, topics)
    terms = _load_terminology(f)
    if terms is not None:
        _check_terminology_entries(f, terms)
        _check_duplicate_term_definitions(f, terms)
    pipeline_status = load_pipeline_status(f)
    if pipeline_status is not None:
        check_pipeline_status_ledger(f, pipeline_status)
    check_learning_ladder(f)
    _check_duplicate_paragraphs(f)
    _check_stale_process_language(f)
    _check_retired_surfaces(f)
    _check_config_keys_as_cli_operands(f)
    return f.report()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Phase 2/3 of `docs/contribute/plans/abi-api-knowledge-and-corpus.md`.

Two things live here, deliberately in one leaf module so the generator
(`gen_abi_taxonomy_coverage.py`) and the gate (`check_ai_readiness.py`'s
`abi-taxonomy-coverage` check) cannot drift from each other:

1. **The taxonomy parser.** Phase 1 shipped its taxonomy as a *reviewable
   Markdown document* (`docs/contribute/abi-api-failure-taxonomy.md`), not as
   a JSON manifest -- see that plan's Phase 1 status paragraph for why. So the
   88 leaf ids are read back out of the document's own tables, keyed on the
   ``## N. Branch name (`branch-slug`)`` headings and the ``| `id` | ... |``
   rows underneath them. The parser is deliberately tolerant about the *last*
   column: a handful of Phase 1 rows spell the platforms/languages cell with
   an internal ``|`` (``| ELF | C, C++ |``) instead of the document's own
   ``ELF · C, C++`` convention, so trailing cells are rejoined rather than
   treated as a malformed row. Phase 1's output is frozen; this parser reads
   it as it is.

2. **The Phase 2/3 mapping.** `docs/_meta/abi-taxonomy-coverage.json` is the
   hand-maintained source of truth: per leaf, the `docs/learn/` page(s) that
   explain it, the `catalog/cases/` case(s) that demonstrate it, the
   `ChangeKind`(s) that would produce a finding for it, and its one Phase 3
   status. Nothing here re-derives a mapping by heuristic. The *only* derived
   facts are each kind's owning `abicheck/model/change_catalog/` module and
   its minimum evidence tier (`scripts/evidence_tiers.py`), both read from
   their own authoritative source so they cannot go stale in the JSON.

`validate()` is the gate: every taxonomy leaf must carry exactly one status
from the plan's fixed vocabulary, every referenced page/case/kind must exist,
and the recorded status must agree with the columns under the plan's own
first-match-wins ordering (a `COVERED` leaf with no case, or a
`MISSING_CASE` leaf that has one, is a contradiction, not a judgement call).

Pure stdlib, importable before ``pip install -e .``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Sibling-import guard, mirroring fact_detector_misuse.py's identical one for
# the identical reason: this module's own directory is on sys.path when it is
# run directly, but not when it is imported as `scripts.abi_taxonomy_coverage`.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = ROOT / "docs" / "contribute" / "abi-api-failure-taxonomy.md"
MAPPING_PATH = ROOT / "docs" / "_meta" / "abi-taxonomy-coverage.json"
LIMITATIONS_PATH = ROOT / "docs" / "learn" / "limitations.md"
KNOWN_GAPS_PATH = ROOT / "docs" / "contribute" / "known-gaps.md"
CHANGE_CATALOG_DIR = ROOT / "abicheck" / "model" / "change_catalog"
CATALOG_CASES_DIR = ROOT / "catalog" / "cases"
DOCS_DIR = ROOT / "docs"

SCHEMA_VERSION = 1

# The plan's Phase 3 vocabulary, in its normative evaluation order
# (first match wins). Order matters: it is what makes the assignment
# deterministic rather than a matter of taste.
STATUS_ORDER: tuple[str, ...] = (
    "NOT_APPLICABLE",
    "KNOWN_UNDETECTABLE",
    "NOT_IMPLEMENTED",
    "MISSING_CASE",
    "PARTIALLY_COVERED",
    "COVERED",
)

STATUS_MEANING: dict[str, str] = {
    "NOT_APPLICABLE": "outside abicheck's stated scope",
    "KNOWN_UNDETECTABLE": "real and explained, but no static evidence distinguishes it",
    "NOT_IMPLEMENTED": "no detector claims it at any evidence tier -- a tractable product gap",
    "MISSING_CASE": "a detector exists, but no catalog case demonstrates it",
    "PARTIALLY_COVERED": "detected and demonstrated, but only a narrower sub-case, or unexplained",
    "COVERED": "explained, demonstrated, and detected",
}

# Statuses whose assignment is a judgement the columns alone cannot express,
# so the mapping file must say why.
_REASON_REQUIRED = frozenset(STATUS_ORDER) - {"COVERED"}

_BRANCH_RE = re.compile(
    r"^##\s+\d+\.\s+(?P<title>.+?)\s+\(`(?P<slug>[a-z0-9-]+)`\)\s*$"
)
_ROW_RE = re.compile(r"^\|\s*`(?P<id>[a-z0-9-]+\.[a-z0-9-]+)`\s*\|(?P<rest>.*)$")


@dataclass(frozen=True)
class Branch:
    """One top-level taxonomy branch."""

    slug: str
    title: str
    number: int


@dataclass(frozen=True)
class Leaf:
    """One leaf mechanism, as Phase 1's document states it."""

    leaf_id: str
    branch: Branch
    mechanism: str
    description: str
    platforms: str


@dataclass
class Mapping:
    """One leaf's Phase 2 columns and Phase 3 status."""

    leaf_id: str
    learn_pages: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    catalog_cases: list[str] = field(default_factory=list)
    detector_kinds: list[str] = field(default_factory=list)
    status: str = ""
    reason: str = ""
    cases_demonstrate: bool = True
    limitations_ref: str = ""
    known_gaps_ref: str = ""


# ── Phase 1 taxonomy document ────────────────────────────────────────────────


def parse_taxonomy(path: Path | None = None) -> list[Leaf]:
    """Parse Phase 1's Markdown taxonomy into its leaf mechanisms.

    Rows are matched by their own leading ``| `<branch>.<leaf>` |`` cell, so
    prose paragraphs, the front matter, and the document's narrative sections
    are ignored without needing to model them.
    """
    text = (path or TAXONOMY_PATH).read_text(encoding="utf-8")
    leaves: list[Leaf] = []
    branch: Branch | None = None
    number = 0
    for line in text.splitlines():
        m = _BRANCH_RE.match(line)
        if m:
            number += 1
            branch = Branch(slug=m.group("slug"), title=m.group("title"), number=number)
            continue
        row = _ROW_RE.match(line)
        if not row:
            continue
        if branch is None:
            raise ValueError(f"taxonomy row before any branch heading: {line!r}")
        leaf_id = row.group("id")
        if not leaf_id.startswith(f"{branch.slug}."):
            raise ValueError(
                f"leaf id {leaf_id!r} does not belong to branch `{branch.slug}`"
            )
        cells = [c.strip() for c in row.group("rest").split("|")]
        # Drop the trailing empty cell produced by the row's closing pipe.
        if cells and cells[-1] == "":
            cells.pop()
        if len(cells) < 3:
            raise ValueError(
                f"taxonomy row for {leaf_id!r} has too few cells: {line!r}"
            )
        mechanism, description = cells[0], cells[1]
        # Phase 1's frozen output spells a few platform cells with an internal
        # pipe; rejoin rather than treating the row as malformed.
        platforms = " | ".join(cells[2:])
        leaves.append(
            Leaf(
                leaf_id=leaf_id,
                branch=branch,
                mechanism=mechanism,
                description=description,
                platforms=platforms,
            )
        )
    return leaves


# ── Phase 2/3 mapping file ───────────────────────────────────────────────────


def load_mapping(path: Path | None = None) -> dict[str, Mapping]:
    """Load `docs/_meta/abi-taxonomy-coverage.json` into `Mapping` objects."""
    raw: dict[str, Any] = json.loads((path or MAPPING_PATH).read_text(encoding="utf-8"))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {raw.get('schema_version')!r} "
            f"(this build supports {SCHEMA_VERSION})"
        )
    out: dict[str, Mapping] = {}
    for leaf_id, entry in raw["leaves"].items():
        out[leaf_id] = Mapping(
            leaf_id=leaf_id,
            learn_pages=list(entry.get("learn_pages", [])),
            topics=list(entry.get("topics", [])),
            catalog_cases=list(entry.get("catalog_cases", [])),
            detector_kinds=list(entry.get("detector_kinds", [])),
            status=entry.get("status", ""),
            reason=entry.get("reason", ""),
            cases_demonstrate=bool(entry.get("cases_demonstrate", True)),
            limitations_ref=entry.get("limitations_ref", ""),
            known_gaps_ref=entry.get("known_gaps_ref", ""),
        )
    return out


# ── Derived facts (never stored in the mapping file) ─────────────────────────


def kind_modules() -> dict[str, str]:
    """Map every `ChangeKind` value to its owning change-catalog module.

    Read from the taxonomy modules' own ``_E("kind", ...)`` entries rather
    than recorded in the mapping file, so a kind that moves between
    `symbols.py`/`types.py`/`platform.py`/`build.py`/`source.py` cannot leave
    a stale module name behind in this report.
    """
    entry_re = re.compile(r'_E\(\s*"([a-z0-9_]+)"')
    out: dict[str, str] = {}
    for name in ("symbols", "types", "platform", "build", "source"):
        path = CHANGE_CATALOG_DIR / f"{name}.py"
        for kind in entry_re.findall(path.read_text(encoding="utf-8")):
            out.setdefault(kind, name)
    return out


def kind_tiers() -> dict[str, str]:
    """Minimum evidence tier per `ChangeKind`, from `scripts/evidence_tiers.py`."""
    import evidence_tiers

    return dict(evidence_tiers.EVIDENCE_TIER_BY_KIND)


def _known_case_ids() -> set[str]:
    if not CATALOG_CASES_DIR.is_dir():
        return set()
    return {p.name for p in CATALOG_CASES_DIR.iterdir() if p.is_dir()}


def _known_topic_ids() -> set[str]:
    """Topic ids declared in `docs/_meta/topics.yaml`.

    Read with a small indentation-aware scan rather than PyYAML, since this
    module must stay importable before ``pip install -e .`` (same constraint
    `check_ai_readiness.py` holds itself to).
    """
    path = DOCS_DIR / "_meta" / "topics.yaml"
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^  ([a-z0-9-]+):\s*$", line)
        if m:
            ids.add(m.group(1))
    return ids


# ── The gate ─────────────────────────────────────────────────────────────────


def expected_status(m: Mapping) -> str:
    """The status the plan's first-match-wins order implies for *m*'s columns.

    The two judgement calls the columns cannot express -- "out of scope" and
    "no static evidence can distinguish this" -- are taken from the recorded
    status itself; every later rung is derived, so a hand-written status that
    contradicts its own mapping fails instead of quietly standing.
    """
    if m.status in ("NOT_APPLICABLE", "KNOWN_UNDETECTABLE"):
        return m.status
    if not m.detector_kinds:
        return "NOT_IMPLEMENTED"
    if not m.catalog_cases or not m.cases_demonstrate:
        return "MISSING_CASE"
    if not m.learn_pages or m.status == "PARTIALLY_COVERED":
        return "PARTIALLY_COVERED"
    return "COVERED"


def validate(
    leaves: list[Leaf] | None = None,
    mapping: dict[str, Mapping] | None = None,
) -> list[str]:
    """Return every structural error in the Phase 2/3 mapping (empty = clean)."""
    leaves = leaves if leaves is not None else parse_taxonomy()
    mapping = mapping if mapping is not None else load_mapping()
    errors: list[str] = []

    leaf_ids = [leaf.leaf_id for leaf in leaves]
    duplicates = {i for i in leaf_ids if leaf_ids.count(i) > 1}
    for dup in sorted(duplicates):
        errors.append(f"taxonomy declares leaf id `{dup}` more than once")

    known_kinds = kind_modules()
    known_cases = _known_case_ids()
    known_topics = _known_topic_ids()

    for leaf_id in leaf_ids:
        m = mapping.get(leaf_id)
        if m is None:
            errors.append(
                f"taxonomy leaf `{leaf_id}` has no entry in "
                f"{MAPPING_PATH.relative_to(ROOT)} -- every leaf needs exactly "
                "one Phase 3 status"
            )
            continue
        if m.status not in STATUS_ORDER:
            errors.append(
                f"leaf `{leaf_id}` has status {m.status!r}, which is not one of "
                f"{', '.join(STATUS_ORDER)}"
            )
            continue
        if m.status in _REASON_REQUIRED and not m.reason.strip():
            errors.append(
                f"leaf `{leaf_id}` is {m.status} but records no `reason` -- a "
                "non-COVERED status is a judgement that has to be stated"
            )
        want = expected_status(m)
        if want != m.status:
            errors.append(
                f"leaf `{leaf_id}` records status {m.status} but its own Phase 2 "
                f"columns imply {want} under the plan's first-match-wins order"
            )
        for page in m.learn_pages:
            if not (DOCS_DIR / page).is_file():
                errors.append(f"leaf `{leaf_id}` names missing learn page `{page}`")
        for topic in m.topics:
            if known_topics and topic not in known_topics:
                errors.append(
                    f"leaf `{leaf_id}` names topic `{topic}`, absent from "
                    "docs/_meta/topics.yaml"
                )
        for case in m.catalog_cases:
            if known_cases and case not in known_cases:
                errors.append(f"leaf `{leaf_id}` names missing catalog case `{case}`")
        for kind in m.detector_kinds:
            if kind not in known_kinds:
                errors.append(
                    f"leaf `{leaf_id}` names ChangeKind `{kind}`, which no "
                    "abicheck/model/change_catalog/ module declares"
                )
        if not m.cases_demonstrate and not m.catalog_cases:
            errors.append(
                f"leaf `{leaf_id}` sets `cases_demonstrate: false` with no cases "
                "mapped -- the flag exists to mark mapped cases as non-demonstrating"
            )
        if m.status == "KNOWN_UNDETECTABLE":
            errors.extend(_check_crossref(m, "limitations", LIMITATIONS_PATH))
        if m.status == "NOT_IMPLEMENTED":
            errors.extend(_check_crossref(m, "known_gaps", KNOWN_GAPS_PATH))

    for leaf_id in sorted(set(mapping) - set(leaf_ids)):
        errors.append(
            f"{MAPPING_PATH.relative_to(ROOT)} maps `{leaf_id}`, which is not a "
            "leaf in docs/contribute/abi-api-failure-taxonomy.md"
        )
    return errors


def _check_crossref(m: Mapping, which: str, target: Path) -> list[str]:
    ref = m.limitations_ref if which == "limitations" else m.known_gaps_ref
    label = f"{which}_ref"
    if not ref.strip():
        return [
            f"leaf `{m.leaf_id}` is {m.status} but records no `{label}` -- the "
            f"plan requires a cross-reference in {target.relative_to(ROOT)}"
        ]
    if not target.is_file() or ref not in target.read_text(encoding="utf-8"):
        return [
            f"leaf `{m.leaf_id}`'s `{label}` ({ref!r}) does not appear in "
            f"{target.relative_to(ROOT)}"
        ]
    return []


def status_counts(mapping: dict[str, Mapping]) -> dict[str, int]:
    """Leaf count per status, in the plan's own evaluation order."""
    return {s: sum(1 for m in mapping.values() if m.status == s) for s in STATUS_ORDER}

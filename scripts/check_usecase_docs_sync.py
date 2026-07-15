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

"""Catch drift between usecase-registry.yaml and its human-readable docs.

docs/development/usecase-registry.yaml is the machine-checked source of truth
for gap status (tests/test_usecase_registry.py enforces its internal
consistency). But the *human* narrative that summarizes it —
docs/development/usecase-coverage-evaluation.md's "Gaps that matter" table and
the "Proposed next steps" backlog, plus docs/development/plans/index.md's
remaining-vs-completed tables — is hand-maintained prose, so it can silently
drift once a registry entry's status changes underneath it (this happened for
real: G14 was marked `complete` in the registry while the eval doc still said
"planned" in two places, and G22 lingered in a "planned"/backlog table after
shipping).

This script is a narrow, structural sync check — not a generator. It does not
try to reconstruct hand-written prose from the registry; it only verifies that
a gap's *aggregate registry status* (done vs. still-open) agrees with where
that gap id appears in the human docs. Run locally with:

    python scripts/check_usecase_docs_sync.py

Requires the package's dev dependencies (PyYAML) to be installed, so it runs
as a step in the `ai-readiness` CI job *after* `pip install -e .`, not before
(unlike scripts/check_ai_readiness.py, which must stay pure-stdlib).
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docs" / "development" / "usecase-registry.yaml"
EVAL_DOC = ROOT / "docs" / "development" / "usecase-coverage-evaluation.md"
PLANS_INDEX = ROOT / "docs" / "development" / "plans" / "index.md"

#: statuses that mean "nothing left to do" for the purposes of this check.
_DONE_STATUSES = {"complete", "by_design_excluded"}

#: words used in the human docs' "Gaps that matter" table, bucketed the same way.
_DONE_WORDS = re.compile(r"✅\s*closed|by[- ]design excluded", re.IGNORECASE)
_OPEN_WORDS = re.compile(r"^(planned|partial|modeled)\b", re.IGNORECASE)

#: gap ids that are intentionally NOT 1:1 with a single registry `gap:` field
#: (large cross-cutting initiatives tracked in plans/index.md's "Initiative
#: plans" table, which is explicitly scoped as "not tied to a single registry
#: gap"). Skip these rather than guessing at a mapping.
_INITIATIVE_ONLY_GAPS = frozenset({"G19", "G24"})


_PLAN_GAP_RE = re.compile(r"/g(\d+)-[^/]+\.md$")


def _load_registry_gap_status() -> dict[str, str]:
    """Return {gap_id: "done" | "open"} aggregated across all registry entries
    that reference that gap. "done" requires every entry for that gap to be
    complete/by_design_excluded; if any entry is still open, the gap is "open".

    The gap id comes from the explicit `gap:` field when present; entries that
    reached `complete` sometimes drop `gap:` (it's only required for unfinished
    statuses — see tests/test_usecase_registry.py), so fall back to parsing the
    gap number out of `plan:`'s filename (`plans/g23-....md` -> G23).
    """
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    by_gap: dict[str, list[str]] = defaultdict(list)
    for case in data["use_cases"]:
        gap = case.get("gap")
        if not gap:
            plan = case.get("plan", "")
            m = _PLAN_GAP_RE.search(plan)
            if m:
                gap = f"G{m.group(1)}"
        if gap:
            by_gap[gap].append(case["status"])
    return {
        gap: "done" if all(s in _DONE_STATUSES for s in statuses) else "open"
        for gap, statuses in by_gap.items()
    }


def _check_eval_doc_gaps_table(gap_status: dict[str, str]) -> list[str]:
    """Cross-check every '| **Gxx** | <status words> | ...' row in the
    "Gaps that matter" table against the registry's aggregate status."""
    findings = []
    text = EVAL_DOC.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"\|\s*\*\*(G\d+)\*\*\s*\|\s*([^|]+)\|", line)
        if not m:
            continue
        gap, status_word = m.group(1), m.group(2).strip()
        if gap not in gap_status or gap in _INITIATIVE_ONLY_GAPS:
            continue
        registry_state = gap_status[gap]
        doc_is_done = bool(_DONE_WORDS.search(status_word))
        doc_is_open = bool(_OPEN_WORDS.match(status_word))
        if registry_state == "done" and doc_is_open:
            findings.append(
                f"{EVAL_DOC.name}: {gap} is complete/by_design_excluded in the "
                f"registry, but the 'Gaps that matter' table still says "
                f"{status_word!r}"
            )
        elif registry_state == "open" and doc_is_done:
            findings.append(
                f"{EVAL_DOC.name}: {gap} still has an open registry entry, "
                f"but the 'Gaps that matter' table says {status_word!r}"
            )
    return findings


def _check_backlog_table_excludes_done_gaps(
    gap_status: dict[str, str], doc: Path, table_header: str, end_marker: str | None
) -> list[str]:
    """Any 'Gxx' referenced inside a backlog/remaining-work table must still be
    open in the registry — a done gap lingering in a backlog table is exactly
    the G14/G22 bug this script exists to catch."""
    findings = []
    text = doc.read_text(encoding="utf-8")
    start = text.find(table_header)
    if start == -1:
        return [f"{doc.name}: expected section {table_header!r} not found"]
    section = text[start:]
    if end_marker:
        end = section.find(end_marker, len(table_header))
        if end != -1:
            section = section[:end]
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        for gap in re.findall(r"\bG\d+\b", line):
            if gap in _INITIATIVE_ONLY_GAPS:
                continue
            if gap_status.get(gap) == "done":
                findings.append(
                    f"{doc.name}: {table_header.strip('# ')!r} still lists "
                    f"{gap}, but it is complete/by_design_excluded in the registry"
                )
    return findings


def _check_open_gaps_appear_in_table(
    gap_status: dict[str, str],
    doc: Path,
    table_header: str,
    end_marker: str | None,
    table_label: str,
) -> list[str]:
    """Every gap that is still open in the registry must have a row in the
    given table — a gap can drift OUT of a backlog table just as easily as a
    done gap can drift in (found by external review, twice: G20 had three
    open registry entries and a real plan file, yet no row in plans/index.md's
    remaining-gaps table at all; the same blind spot existed for the eval
    doc's own "Gaps that matter" and "Proposed next steps" tables, since the
    only positive-presence check originally covered plans/index.md alone)."""
    text = doc.read_text(encoding="utf-8")
    start = text.find(table_header)
    if start == -1:
        return [f"{doc.name}: expected section {table_header!r} not found"]
    section = text[start:]
    if end_marker:
        end = section.find(end_marker, len(table_header))
        if end != -1:
            section = section[:end]
    listed = {
        gap
        for line in section.splitlines()
        if line.startswith("|")
        for gap in re.findall(r"\bG\d+\b", line)
    }
    findings = []
    for gap, state in gap_status.items():
        if state == "open" and gap not in _INITIATIVE_ONLY_GAPS and gap not in listed:
            findings.append(
                f"{doc.name}: {gap} is still open in the registry (status != "
                f"complete/by_design_excluded) but has no row in {table_label!r}"
            )
    return findings


def _check_completed_table_excludes_open_gaps(
    gap_status: dict[str, str], doc: Path, table_header: str
) -> list[str]:
    """Symmetric check: a gap listed as "done" history must actually be done."""
    findings = []
    text = doc.read_text(encoding="utf-8")
    start = text.find(table_header)
    if start == -1:
        return [f"{doc.name}: expected section {table_header!r} not found"]
    section = text[start:]
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        for gap in re.findall(r"\bG\d+\b", line):
            if gap in _INITIATIVE_ONLY_GAPS:
                continue
            if gap_status.get(gap) == "open":
                findings.append(
                    f"{doc.name}: {table_header.strip('# ')!r} lists {gap} as "
                    f"done/decided, but a registry entry for it is still open"
                )
    return findings


def all_findings() -> list[str]:
    """Run every check and return the combined findings list. Shared by
    main() and tests/test_usecase_docs_sync.py so the set of checks can't
    silently drift apart between the CLI gate and its pytest mirror."""
    gap_status = _load_registry_gap_status()
    findings: list[str] = []
    findings += _check_eval_doc_gaps_table(gap_status)
    findings += _check_backlog_table_excludes_done_gaps(
        gap_status, EVAL_DOC, "## Proposed next steps", "\n## "
    )
    findings += _check_backlog_table_excludes_done_gaps(
        gap_status,
        PLANS_INDEX,
        "| Gap | Plan | Registry use cases | Effort |",
        "Initiative plans",
    )
    findings += _check_completed_table_excludes_open_gaps(
        gap_status, PLANS_INDEX, "Completed or decided plans are retained"
    )
    findings += _check_open_gaps_appear_in_table(
        gap_status,
        PLANS_INDEX,
        "| Gap | Plan | Registry use cases | Effort |",
        "Initiative plans",
        "remaining use-case gaps table",
    )
    findings += _check_open_gaps_appear_in_table(
        gap_status,
        EVAL_DOC,
        "## Gaps that matter — current implementation status",
        "## Proposed next steps",
        "'Gaps that matter' table",
    )
    findings += _check_open_gaps_appear_in_table(
        gap_status,
        EVAL_DOC,
        "## Proposed next steps",
        "\n## ",
        "'Proposed next steps' table",
    )
    return findings


def main() -> int:
    findings = all_findings()

    if findings:
        print("usecase-docs-sync: DRIFT DETECTED\n")
        for f in findings:
            print(f"  - {f}")
        print(
            f"\n{len(findings)} drift finding(s). Update the human docs to match "
            f"docs/development/usecase-registry.yaml (the source of truth), or "
            f"fix the registry if it's the one that's stale."
        )
        return 1

    print("usecase-docs-sync: OK — human docs agree with usecase-registry.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())

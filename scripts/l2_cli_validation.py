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

"""What the full-CLI L2 harness *asserts* about a run's output.

Split out of `check_l2_cli_perf.py` once that file crossed the AI-readiness
`file-size` gate's 2000-line hard cap -- a mechanical extraction (unchanged
function bodies), not a redesign, and the same split
`tests/test_l2_cli_perf_contracts.py` already makes on the test side: this module
answers "was the work really done and is the result right", the parent answers
"how long did it take and does that regress".

Every check here runs strictly OUTSIDE the timed window. That is what lets them
be thorough: loading a snapshot through the product's own codec, or walking a
report's whole change set, would otherwise be charged to the measurement.

The one rule these all serve: a timing harness that checks only duration rewards
the worst regression available to it -- getting faster by quietly doing less. So
each function states what the run *promised* and fails when the promise is
unmet, however fast the run was.

Pure stdlib at import time; the two functions that need the product import it
lazily, so this module is importable before `pip install -e .`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# The same sys.path guard `check_l2_cli_perf.py` uses, for the same reason: this
# module must resolve its sibling whether it is loaded directly or as a
# `scripts.` submodule by a test that never imported its parent first.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if importlib.util.find_spec("l2_cli_fixture") is None:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_SCRIPTS_DIR) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))

import l2_cli_fixture as fixtures  # noqa: E402

#: Refuse to accept an output larger than this -- a runaway renderer filling the
#: runner's disk is a failure mode a perf lane can actually hit. Lives here, next
#: to the check that reads it, and is re-exported by the parent for its own
#: step-level output check.
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def _load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_l2_reached(
    report: dict[str, Any], *, sides: tuple[str, ...]
) -> list[str]:
    """The run really reached L2 on every named side, and did not degrade.

    Checks the *resolved* depth rather than trusting that ``--depth headers``
    was passed: a binary-only fallback still accepts the flag, and that fallback
    is faster, which is exactly why a timing harness must reject it.
    """
    problems: list[str] = []
    assurance = report.get("analysis_assurance") or {}
    if assurance.get("effective_depth") != "headers":
        problems.append(
            f"effective_depth={assurance.get('effective_depth')!r}, expected 'headers' "
            "-- the run did not actually perform L2 analysis"
        )
    if assurance.get("depth_satisfied") is not True:
        problems.append(f"depth_satisfied={assurance.get('depth_satisfied')!r}")
    for side in sides:
        depth = report.get(f"{side}_evidence_depth")
        if depth != "headers":
            problems.append(f"{side}_evidence_depth={depth!r}, expected 'headers'")
    scope = report.get("scope") or {}
    if not scope.get("public_headers_applied"):
        problems.append("scope.public_headers_applied is false -- no public scoping")
    if scope.get("fell_back"):
        problems.append("scope.fell_back is true -- public scoping degraded")
    return problems


def _validate_break_findings(report: dict[str, Any]) -> list[str]:
    kinds = {c.get("kind") for c in report.get("changes") or []}
    problems = []
    if report.get("verdict") != "BREAKING":
        problems.append(f"verdict={report.get('verdict')!r}, expected BREAKING")
    for family, alternatives in fixtures.EXPECTED_BREAK_KIND_FAMILIES.items():
        if not kinds & set(alternatives):
            problems.append(
                f"no {family}-family finding (expected one of {list(alternatives)}); "
                f"got {sorted(k for k in kinds if k)}"
            )
    return problems


def _validate_break_families_in_text(text: str, *, artifact: str) -> list[str]:
    """The same break-family expectation, applied to a *rendered* report.

    The JSON path checks `report["changes"]` kinds; a human renderer names the
    kind slug verbatim in its finding list (`- **func_removed**: Public function
    removed: shape_count ...`), so the same `EXPECTED_BREAK_KIND_FAMILIES` can
    be asserted against its text. That is the point: this scenario's claim is
    that one completed analysis feeds both exports, which is not checked by
    asking the rendered half only for the word "BREAKING" -- a renderer that
    kept the verdict metadata and dropped the finding list would have read as a
    pure speedup (Codex review).

    Deliberately the shared table rather than a second hand-written list: two
    expectations that can disagree is how the two artifacts would stop being
    checked against the same thing.
    """
    lowered = text.lower()
    return [
        f"the {artifact} export names no {family}-family finding (expected one of "
        f"{list(alternatives)}) -- the two exports do not describe the same analysis"
        for family, alternatives in fixtures.EXPECTED_BREAK_KIND_FAMILIES.items()
        if not any(alternative in lowered for alternative in alternatives)
    ]


#: Verdicts an identical pair may never produce. Deliberately a deny-list of the
#: two that assert a *difference*, not an allow-list of exactly ``COMPATIBLE``: a
#: risk-level observation that holds on BOTH sides is a true statement about the
#: fixture's surface, and demanding a bare ``COMPATIBLE`` would make the control
#: fail on the product being right (the generated fixture's public header
#: includes its own `detail/` dependency, so `private_header_leak` is reported on
#: each side identically -- verified by this check failing on it the first time).
_NOT_COMPATIBLE_VERDICTS = frozenset({"BREAKING", "SOURCE_BREAK", "API_BREAK"})


def _surface_state_kinds() -> frozenset[str]:
    """Kinds that describe the surface rather than assert a change between sides.

    Derived from the product's own `RISK_KINDS`/`QUALITY_KINDS` partition, not
    hand-listed: the control's question is "did anything claim a difference",
    and which kinds are non-difference observations is a fact the change
    registry already owns. Everything else -- breaking, API-break and addition
    alike -- is a manufactured difference on an identical pair.
    """
    from abicheck.checker_policy import QUALITY_KINDS, RISK_KINDS

    return frozenset(k.value for k in (*RISK_KINDS, *QUALITY_KINDS))


def _validate_unchanged(report: dict[str, Any]) -> list[str]:
    """The control: for an identical pair, *any* reported change is wrong.

    Checking only the two families the deliberate-break fixture produces is too
    narrow to be a control (Codex review): a regression emitting some *other*
    kind -- a parameter, visibility or platform finding -- or returning a
    non-compatible verdict left this scenario reading as successful while the
    product had started manufacturing findings on an unchanged comparison. The
    old/new sides here are built from identical sources, so the expectation is
    an empty change set and a compatible verdict, stated directly.
    """
    problems = []
    verdict = report.get("verdict")
    if verdict in _NOT_COMPATIBLE_VERDICTS:
        problems.append(f"verdict={verdict!r} on an identical pair")
    surface_state = _surface_state_kinds()
    manufactured = sorted(
        {
            kind
            for change in report.get("changes") or []
            if (kind := change.get("kind")) not in surface_state
        }
    )
    if manufactured:
        problems.append(
            f"{len(manufactured)} difference-asserting finding(s) on an "
            f"identical pair -- every one is a false positive: {manufactured}"
        )
    return problems


def _validate_snapshot(path: Path) -> list[str]:
    """A dumped snapshot carries real L2 content, not an empty shell.

    Loaded through the product's own ``load_snapshot`` rather than read as raw
    JSON: the on-disk form is a sectioned envelope, and checking the envelope's
    keys would pass for a snapshot whose sections are empty -- "a file was
    written" is precisely the wrong thing for this harness to accept as
    success.
    """
    from abicheck.serialization import load_snapshot

    problems: list[str] = []
    if not path.exists():
        return [f"no snapshot written at {path}"]
    size = path.stat().st_size
    if size > MAX_OUTPUT_BYTES:
        problems.append(f"snapshot is {size} bytes, over the {MAX_OUTPUT_BYTES} cap")
    snapshot = load_snapshot(str(path))
    names = {f.name for f in snapshot.functions} | {t.name for t in snapshot.types}
    for declaration in fixtures.EXPECTED_DECLARATIONS:
        if not any(declaration in name for name in names):
            problems.append(
                f"declaration {declaration!r} missing from the snapshot "
                "-- headers were lost or never parsed"
            )
    pack = snapshot.build_source
    graph = getattr(pack, "source_graph", None) if pack else None
    if graph is None:
        problems.append("no source graph: the header-graph attach never ran")
        return problems
    passes = graph.extractor_passes or {}
    for required in ("header_call_graph", "header_include_graph", "header_type_graph"):
        if not passes.get(required):
            problems.append(
                f"extractor pass {required!r} did not run or did not complete"
            )
    if graph.degraded_passes:
        problems.append(f"degraded extractor passes: {sorted(graph.degraded_passes)}")
    include = (graph.coverage or {}).get("include_edges") or {}
    if not include.get("collected"):
        problems.append("include graph was not collected")
    elif not include.get("count"):
        problems.append(
            "include graph collected zero edges -- the fixture's shared detail/ "
            "header should always produce at least one"
        )
    return problems


def _validate_audit(report: dict[str, Any]) -> list[str]:
    """``--no-baseline`` must read as an audit, not as a compatibility verdict."""
    problems = []
    if report.get("no_baseline") is not True:
        problems.append(f"no_baseline={report.get('no_baseline')!r}, expected True")
    if report.get("verdict") is not None:
        problems.append(
            f"verdict={report.get('verdict')!r} -- a no-baseline audit must not "
            "manufacture a compatibility verdict (there is nothing to compare to)"
        )
    outcome = report.get("run_outcome") or {}
    if outcome.get("compatibility") is not None:
        problems.append(
            f"run_outcome.compatibility={outcome.get('compatibility')!r}, expected null"
        )
    if not report.get("audit_report_schema_version"):
        problems.append("no audit_report_schema_version -- this is not an audit report")
    return problems


def _validate_audit_reached_l2(report: dict[str, Any]) -> list[str]:
    """An audit must also have *done* the L2 work, not just read as an audit.

    The audit-semantics checks above are about shape (no verdict, no
    compatibility), and a run whose header frontend failed and fell back to
    binary-only satisfies every one of them -- while being faster, which is the
    one direction this harness must never accept (Codex review). So the L2
    question is asked here too.

    It cannot be asked via :func:`_validate_l2_reached`: the audit report is a
    different document and publishes **no** ``analysis_assurance``, ``scope`` or
    ``*_evidence_depth`` block at all (verified against a real run -- its keys
    are ``audit_report_schema_version``, ``evidence_tiers``, ``comparison_scope``,
    ``run_outcome``, ... ). That absence is itself a product gap, recorded in
    `docs/contribute/known-gaps.md`: a reader of an audit report cannot tell
    whether the run reached L2 or silently fell back. What the document *does*
    publish is ``evidence_tiers``, the list of tiers the run actually consumed,
    and a binary-only fallback's list does not contain ``header`` -- so that is
    the proof used, rather than a field the report does not carry.
    """
    tiers = report.get("evidence_tiers")
    if not isinstance(tiers, list):
        return [f"evidence_tiers={tiers!r} -- cannot establish the audit reached L2"]
    if "header" not in tiers:
        return [
            f"evidence_tiers={tiers!r} carries no 'header' tier -- the audit did "
            "not perform L2 header analysis (a binary-only fallback, which is "
            "faster and wrong)"
        ]
    return []

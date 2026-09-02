#!/usr/bin/env python3
"""Structural validation for `docs/_meta/one-semantic-pipeline-status.yaml`
(ADR-063's "PR 0" machine-readable authority ledger).

Split out of `check_docs_contract.py` the same way `adr_status_sync.py`/
`engine_cli_boundary.py` were split out of `check_ai_readiness.py`: adding
this check inline pushed `check_docs_contract.py` past the AI-readiness
`file-size` gate's own 2000-line hard cap. Mechanical extraction, not a
redesign — every function/constant here is unchanged from its original
home, just given its own `Findings` `Protocol` (mirroring
`adr_status_sync.py`'s own decoupling) instead of importing the caller's
concrete `Findings` class.

Pure stdlib apart from `yaml` (the same dependency `check_docs_contract.py`
already has, so no new install requirement).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
PIPELINE_STATUS_FILE = DOCS / "_meta" / "one-semantic-pipeline-status.yaml"


class Findings(Protocol):
    """The error/warning sink `check_docs_contract.py`'s own `Findings`
    (a `findings_report.Findings` subclass) already satisfies structurally."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


#: The three-state progress vocabulary every status-bearing field in the
#: ledger uses (`primitive`/`producers`/`consumers`/`persistence`).
_PIPELINE_STATUS_STATES = frozenset({"not_started", "partial", "complete"})
#: `authority` names which representation actually decides behavior today --
#: see the ledger file's own header comment for what each value means.
_PIPELINE_AUTHORITY_VALUES = frozenset({"self", "legacy", "mixed"})
#: Fields every concept entry must carry. `persistence` is deliberately not
#: required -- only `facts` (the one concept with a genuine on-disk
#: durability question distinct from "is it produced/consumed") has it.
_PIPELINE_REQUIRED_CONCEPT_FIELDS = (
    "primitive",
    "producers",
    "consumers",
    "authority",
    "removal_gate",
)
#: Fields whose value must be one of `_PIPELINE_STATUS_STATES`.
_PIPELINE_STATUS_FIELDS = ("primitive", "producers", "consumers", "persistence")
#: The full roadmap concept inventory this ledger exists to track (ADR-063's
#: seven primitives plus the two identity systems D3 explicitly separates --
#: see the ledger file's own header). Checked as an exact set, not just "is
#: each present entry well-formed": a removed or misspelled key would
#: otherwise silently stop tracking part of the roadmap with zero findings,
#: exactly the class of drift this ledger exists to prevent (a real review
#: finding on PR #1019). Update this set in the same PR that adds, removes,
#: or renames a concept in the ledger file itself.
_PIPELINE_REQUIRED_CONCEPTS = frozenset(
    {
        "facts",
        "identity",
        "semantic_ir",
        "public_surface",
        "analysis_plan",
        "run_outcome",
        "sectioned_storage",
        "report_document",
        "l5_source_graph_identity",
    }
)
_PIPELINE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PIPELINE_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


def load_pipeline_status(f: Findings) -> dict[str, object] | None:
    """Parse and structurally validate the ledger. Unlike a file predating
    the `_meta/` "required registry" convention (e.g. `terminology.yaml`,
    which legitimately treats absence as optional), this ledger is required
    from the PR that introduced it onward (docs/AGENTS.md's `_meta/`
    "machine-consumed registries" contract, and this check is what makes it
    one): a deleted or renamed ledger must fail the gate, not silently skip
    all downstream validation (a real review finding on PR #1019 — nothing
    else references `PIPELINE_STATUS_FILE`, so without this, CI could not
    detect the ledger's complete loss)."""
    if not PIPELINE_STATUS_FILE.is_file():
        f.err(
            "pipeline-status-ledger",
            f"{_rel(PIPELINE_STATUS_FILE)}: file not found",
        )
        return None
    try:
        data = yaml.safe_load(PIPELINE_STATUS_FILE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        f.err(
            "pipeline-status-ledger",
            f"{_rel(PIPELINE_STATUS_FILE)}: invalid YAML: {exc}",
        )
        return None
    if not isinstance(data, dict):
        f.err(
            "pipeline-status-ledger",
            f"{_rel(PIPELINE_STATUS_FILE)}: top level must be a mapping",
        )
        return None
    return data


def check_pipeline_status_ledger(f: Findings, data: dict[str, object]) -> None:
    """Structural validation for the ADR-063 status ledger -- what makes it
    a genuinely machine-consumed `docs/_meta/` registry (docs/AGENTS.md's
    "Layout" section) rather than a third hand-maintained copy of the ADR/
    plan's own status prose that nothing checks (a real review finding on
    PR #1019). Deliberately does not attempt to verify a concept's status
    *claim* against the actual codebase -- that would require this pure-
    docs check to import `abicheck` itself, a much larger, separately-
    justified project. What it does enforce: the file parses, every
    required concept is present with exactly the required fields and
    values from the declared enums, and the header metadata is well-formed
    -- malformed structure (a typo'd status value, a missing field, a
    dropped or misspelled concept key) is caught immediately rather than
    silently read as `None`/absent by a future generator."""
    rel = _rel(PIPELINE_STATUS_FILE)
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int):
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'schema_version' must be an integer",
        )
    as_of_commit = data.get("as_of_commit")
    if not isinstance(as_of_commit, str) or not _PIPELINE_COMMIT_RE.match(as_of_commit):
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'as_of_commit' must be a short/long git sha (hex), "
            f"got {as_of_commit!r}",
        )
    as_of_date = data.get("as_of_date")
    if not isinstance(as_of_date, str) or not _PIPELINE_DATE_RE.match(as_of_date):
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'as_of_date' must be 'YYYY-MM-DD', got {as_of_date!r}",
        )
    concepts = data.get("concepts")
    if not isinstance(concepts, dict) or not concepts:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: missing or empty top-level 'concepts' mapping",
        )
        return
    missing = _PIPELINE_REQUIRED_CONCEPTS - set(concepts)
    if missing:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'concepts' is missing required entries "
            f"{sorted(missing)} -- update _PIPELINE_REQUIRED_CONCEPTS in "
            f"scripts/pipeline_status_ledger.py if a concept was deliberately "
            f"renamed or removed",
        )
    unknown = set(concepts) - _PIPELINE_REQUIRED_CONCEPTS
    if unknown:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'concepts' has entries not in the tracked inventory "
            f"{sorted(unknown)} -- either a typo, or add the new concept to "
            f"_PIPELINE_REQUIRED_CONCEPTS in scripts/pipeline_status_ledger.py "
            f"deliberately",
        )
    for name, entry in concepts.items():
        if not isinstance(entry, dict):
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}: must be a mapping",
            )
            continue
        for required in _PIPELINE_REQUIRED_CONCEPT_FIELDS:
            if required not in entry:
                f.err(
                    "pipeline-status-ledger",
                    f"{rel}: concepts.{name}: missing required field {required!r}",
                )
        for status_field in _PIPELINE_STATUS_FIELDS:
            if status_field not in entry:
                continue
            value = entry[status_field]
            if value not in _PIPELINE_STATUS_STATES:
                f.err(
                    "pipeline-status-ledger",
                    f"{rel}: concepts.{name}.{status_field}: {value!r} is not "
                    f"one of {sorted(_PIPELINE_STATUS_STATES)}",
                )
        authority = entry.get("authority")
        if "authority" in entry and authority not in _PIPELINE_AUTHORITY_VALUES:
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}.authority: {authority!r} is not "
                f"one of {sorted(_PIPELINE_AUTHORITY_VALUES)}",
            )
        removal_gate = entry.get("removal_gate")
        if "removal_gate" in entry and (
            not isinstance(removal_gate, str) or not removal_gate.strip()
        ):
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}.removal_gate: must be a non-empty string",
            )
        extra = set(entry) - set(_PIPELINE_REQUIRED_CONCEPT_FIELDS) - {"persistence"}
        if extra:
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}: unknown field(s) {sorted(extra)} -- "
                f"either a typo or the schema needs extending deliberately",
            )

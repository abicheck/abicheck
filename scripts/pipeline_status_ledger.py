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

import datetime
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
#: Fields every concept entry must carry.
_PIPELINE_REQUIRED_CONCEPT_FIELDS = (
    "primitive",
    "producers",
    "consumers",
    "authority",
    "removal_gate",
)
#: Fields whose value must be one of `_PIPELINE_STATUS_STATES`.
_PIPELINE_STATUS_FIELDS = ("primitive", "producers", "consumers", "persistence")
#: Concept -> extra field(s) that concept specifically must carry, beyond
#: `_PIPELINE_REQUIRED_CONCEPT_FIELDS`. Only `facts` has a genuine on-disk
#: durability question distinct from "is it produced/consumed", so only it
#: requires `persistence` -- but "not required anywhere" would let this one
#: real durability status silently go missing with zero findings (a real
#: review finding on PR #1019), so the requirement is concept-scoped rather
#: than dropped to "optional everywhere."
_PIPELINE_PER_CONCEPT_EXTRA_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "facts": ("persistence",),
}
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
#: The only ledger schema version this validator's field layout implements.
#: A future schema bump must update both this constant and the validation
#: logic together (or dispatch per-version), never silently accept a new
#: version number against the old field rules.
_PIPELINE_SUPPORTED_SCHEMA_VERSION = 1
#: The complete top-level key set this schema version defines. Checked as
#: an exact set the same way `_PIPELINE_REQUIRED_CONCEPTS`/
#: `_PIPELINE_REQUIRED_CONCEPT_FIELDS` are -- a misspelled top-level key
#: (`as_of_commmit`) or an unsupported one (`status: complete`) previously
#: produced zero findings, since every individual field was validated by
#: name via `.get()` with nothing checking the header's own key set (a real
#: review finding on PR #1019).
_PIPELINE_TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "as_of_commit", "as_of_date", "concepts"}
)
#: `\Z` (not a bare `$`), since a YAML block scalar (`as_of_commit: |`) is
#: parsed with its trailing newline intact and `$` matches immediately
#: before a final `\n` too -- `\Z` only matches the true end of string, so a
#: newline-terminated non-SHA value is correctly rejected (a real review
#: finding on PR #1019).
_PIPELINE_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_PIPELINE_COMMIT_RE = re.compile(r"\A[0-9a-f]{7,40}\Z")


class _DuplicateKeyError(Exception):
    """A mapping in the ledger repeats a key (e.g. two `schema_version:`
    entries, or two `concepts.facts:` entries after a bad merge). Distinct
    from `yaml.YAMLError` -- the document is syntactically valid YAML, PyYAML
    just resolves the repeat with silent last-value-wins semantics -- so it
    is reported as its own finding rather than folded into the "invalid
    YAML" message below."""


def _load_yaml_strict(text: str) -> object:
    """Parse *text* as YAML, raising `_DuplicateKeyError` for a duplicate
    mapping key anywhere in the document.

    Plain `yaml.safe_load` silently keeps only the last value for a repeated
    key (`{a: 1, a: 2}` -> `{"a": 2}`, no error) -- so a merge or manual edit
    that repeats `schema_version` or a `concepts.<name>` entry would let this
    "structural integrity gate for the authoritative ledger" (this module's
    own docstring) validate only the surviving copy and silently ignore a
    conflicting or invalid duplicate (a real review finding on PR #1019).
    `abicheck/dump_manifest.py`'s `_load_yaml_strict` establishes the same
    `SafeLoader`-subclass-with-duplicate-checking pattern for the identical
    reason; this is that pattern's `scripts/`-side sibling, not a new
    design, kept independent since `scripts/` does not depend on `abicheck/`.
    """

    class _StrictLoader(yaml.SafeLoader):
        pass

    def _construct_mapping(
        loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False
    ) -> dict[object, object]:
        seen: set[object] = set()
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            if key in seen:
                raise _DuplicateKeyError(
                    f"duplicate key {key!r} in the same mapping "
                    f"(line {key_node.start_mark.line + 1})"
                )
            seen.add(key)
            mapping[key] = loader.construct_object(value_node, deep=True)
        return mapping

    _StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
    )
    # `_StrictLoader` subclasses `yaml.SafeLoader` and only replaces its
    # mapping constructor with the duplicate-key check above; bandit's B506
    # flags every `Loader=` it cannot name-match against
    # `SafeLoader`/`CSafeLoader`, subclasses included.
    return yaml.load(text, Loader=_StrictLoader)  # nosec B506


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
        data = _load_yaml_strict(PIPELINE_STATUS_FILE.read_text(encoding="utf-8"))
    except _DuplicateKeyError as exc:
        f.err(
            "pipeline-status-ledger",
            f"{_rel(PIPELINE_STATUS_FILE)}: {exc}",
        )
        return None
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
    top_level_unknown = set(data) - _PIPELINE_TOP_LEVEL_FIELDS
    if top_level_unknown:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: unknown top-level field(s) "
            f"{sorted(top_level_unknown, key=repr)} -- either a typo or the "
            f"schema needs extending deliberately in "
            f"_PIPELINE_TOP_LEVEL_FIELDS",
        )
    schema_version = data.get("schema_version")
    # `bool` is an `int` subclass in Python, so `isinstance(True, int)` is
    # True -- excluded explicitly, or `schema_version: true` would pass this
    # check. This validator implements only the version-1 field layout, so
    # any other integer (a real future schema bump this module hasn't been
    # updated for) must fail rather than be silently accepted as if it were
    # version 1 (a real review finding on PR #1019).
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != _PIPELINE_SUPPORTED_SCHEMA_VERSION
    ):
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'schema_version' must be exactly "
            f"{_PIPELINE_SUPPORTED_SCHEMA_VERSION} (the only version this "
            f"validator implements), got {schema_version!r}",
        )
    as_of_commit = data.get("as_of_commit")
    if not isinstance(as_of_commit, str) or not _PIPELINE_COMMIT_RE.match(as_of_commit):
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'as_of_commit' must be a short/long git sha (hex), "
            f"got {as_of_commit!r}",
        )
    as_of_date = data.get("as_of_date")
    # The regex alone only checks the YYYY-MM-DD *shape* -- it accepts a
    # calendar-invalid value like "2026-02-30" (a real review finding on
    # PR #1019). `date.fromisoformat` is the actual calendar check, run
    # only once the shape is already confirmed (it accepts a wider set of
    # ISO 8601 forms on its own -- e.g. no dashes at all -- that the regex
    # exists specifically to keep out).
    if not isinstance(as_of_date, str) or not _PIPELINE_DATE_RE.match(as_of_date):
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'as_of_date' must be 'YYYY-MM-DD', got {as_of_date!r}",
        )
    else:
        try:
            datetime.date.fromisoformat(as_of_date)
        except ValueError:
            f.err(
                "pipeline-status-ledger",
                f"{rel}: 'as_of_date' is not a real calendar date: {as_of_date!r}",
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
        # `unknown` may hold non-string YAML keys (e.g. a bare `1:` -- valid
        # YAML, an int key) alongside real string ones; `sorted()` without a
        # key raises TypeError comparing an int to a str instead of
        # reporting a finding. `key=repr` orders any mix of hashable types
        # deterministically without that crash (a real review finding on
        # PR #1019; the identical fix applies to `extra` below).
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'concepts' has entries not in the tracked inventory "
            f"{sorted(unknown, key=repr)} -- either a typo, or add the new "
            f"concept to _PIPELINE_REQUIRED_CONCEPTS in "
            f"scripts/pipeline_status_ledger.py deliberately",
        )
    for name, entry in concepts.items():
        if not isinstance(entry, dict):
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}: must be a mapping",
            )
            continue
        required_fields = _PIPELINE_REQUIRED_CONCEPT_FIELDS + (
            _PIPELINE_PER_CONCEPT_EXTRA_REQUIRED_FIELDS.get(name, ())
        )
        for required in required_fields:
            if required not in entry:
                f.err(
                    "pipeline-status-ledger",
                    f"{rel}: concepts.{name}: missing required field {required!r}",
                )
        for status_field in _PIPELINE_STATUS_FIELDS:
            if status_field not in entry:
                continue
            value = entry[status_field]
            # A non-hashable value (a YAML list/mapping where a scalar was
            # expected, e.g. `primitive: [complete]`) must not reach the
            # `in` membership test below -- `x in frozenset` raises
            # `TypeError: unhashable type` for one, crashing the whole gate
            # instead of recording a finding (a real review finding on
            # PR #1019).
            if not isinstance(value, str) or value not in _PIPELINE_STATUS_STATES:
                f.err(
                    "pipeline-status-ledger",
                    f"{rel}: concepts.{name}.{status_field}: {value!r} is not "
                    f"one of {sorted(_PIPELINE_STATUS_STATES)}",
                )
        authority = entry.get("authority")
        if "authority" in entry and (
            not isinstance(authority, str)
            or authority not in _PIPELINE_AUTHORITY_VALUES
        ):
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
        allowed_extra = set(_PIPELINE_PER_CONCEPT_EXTRA_REQUIRED_FIELDS.get(name, ()))
        extra = set(entry) - set(_PIPELINE_REQUIRED_CONCEPT_FIELDS) - allowed_extra
        if extra:
            # See the `unknown` block above: `extra` may hold non-string
            # YAML keys too, so `sorted()` needs the same `key=repr` guard.
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}: unknown field(s) "
                f"{sorted(extra, key=repr)} -- either a typo or the schema "
                f"needs extending deliberately",
            )

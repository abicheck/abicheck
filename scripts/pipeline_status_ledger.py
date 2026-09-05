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
#: The four-state consolidation ladder every concept sits on
#: (`duplication-and-convergence-assessment.md`'s "The four-state status
#: model", accepted 2026-09-05). `authority` answers "which representation
#: decides *today*"; this answers "how far through the consolidation is
#: this concept", which `authority` cannot express at either end: it
#: separates a merely-defined primitive from a wired-but-non-deciding one
#: (`introduced` vs `wired`, both `authority: legacy`), and a concept that
#: decides from one whose replaced implementation is actually gone
#: (`authoritative` vs `retired`, both `authority: self`).
_PIPELINE_LIFECYCLE_VALUES = ("introduced", "wired", "authoritative", "retired")
#: Which lifecycle rungs each `authority` value admits. The two fields are
#: deliberately a refinement, not two independent opinions -- an entry
#: claiming `authority: self` while sitting at `wired` (or `legacy` while
#: claiming `authoritative`) is a self-contradiction the ledger should not
#: be able to record, which is exactly the drift this file exists to catch.
_PIPELINE_AUTHORITY_TO_LIFECYCLES: dict[str, frozenset[str]] = {
    "legacy": frozenset({"introduced", "wired"}),
    "mixed": frozenset({"wired"}),
    "self": frozenset({"authoritative", "retired"}),
}
#: Fields every concept entry must carry.
_PIPELINE_REQUIRED_CONCEPT_FIELDS = (
    "primitive",
    "producers",
    "consumers",
    "authority",
    "lifecycle",
    "removal_gate",
)
#: Optional-everywhere concept fields (present or absent, but validated
#: when present). Unlike `_PIPELINE_PER_CONCEPT_EXTRA_REQUIRED_FIELDS`
#: below, no concept is *required* to carry one: a concept with nothing
#: investigated-and-declined must not be forced to record an empty list,
#: which would be indistinguishable from a real one left un-updated.
_PIPELINE_OPTIONAL_CONCEPT_FIELDS = ("investigated_declined",)
#: The fields each `investigated_declined` entry must carry, and nothing
#: else. `leaves_open` is required rather than optional on purpose: the
#: whole reason this disposition is separate from the ladder is that a
#: declined *behavioral* change leaves the *implementation-consolidation*
#: item open, so an entry that does not say what stays open is the exact
#: shape of the loophole this field exists to close.
_PIPELINE_DECLINED_REQUIRED_FIELDS = ("item", "decided", "leaves_open", "tracked_as")
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
#: version number against the old field rules. Bumped to 2 when the
#: `lifecycle` ladder and the `investigated_declined` disposition were
#: added (see `_PIPELINE_LIFECYCLE_VALUES`): a version-1 document has no
#: `lifecycle` field at all, so accepting one under these rules would
#: report a missing required field rather than the real "this file predates
#: the schema" problem.
_PIPELINE_SUPPORTED_SCHEMA_VERSION = 2
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


class _LedgerYamlError(Exception):
    """Base for a structural mapping problem this loader's strict
    constructor detects (a duplicate key, or a key that cannot be a mapping
    key at all). Distinct from `yaml.YAMLError` in both cases -- the document
    is syntactically valid YAML -- so either is reported as its own finding
    rather than folded into the generic "invalid YAML" message below."""


class _DuplicateKeyError(_LedgerYamlError):
    """A mapping in the ledger repeats a key (e.g. two `schema_version:`
    entries, or two `concepts.facts:` entries after a bad merge). PyYAML
    resolves the repeat with silent last-value-wins semantics by default."""


class _UnhashableKeyError(_LedgerYamlError):
    """A mapping key resolved to a YAML sequence or mapping (`? [a, b]`),
    which Python cannot hash. This ledger's schema never uses one, so a
    document doing so is malformed structure, not a schema PyYAML itself
    would reject."""


def _load_yaml_strict(text: str) -> object:
    """Parse *text* as YAML, raising `_DuplicateKeyError`/`_UnhashableKeyError`
    for a duplicate or unhashable mapping key anywhere in the document.

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

    The duplicate check alone would crash on an *unhashable* key before ever
    reaching it: `key in seen`/`seen.add(key)`/`mapping[key] = ...` all
    require a hashable key, and syntactically valid YAML can supply a
    sequence or mapping as one (`? [a, b]\\n: value`), which
    `construct_object` returns as a `list`/`dict` -- neither hashable. An
    uncaught `TypeError` there would crash the whole docs-contract job
    instead of producing the promised `pipeline-status-ledger` finding (a
    real review finding on PR #1019), so hashability is checked explicitly,
    before either the duplicate check or the `dict` assignment can raise it
    as a raw `TypeError`.
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
            try:
                hash(key)
            except TypeError:
                raise _UnhashableKeyError(
                    f"key {key!r} at line {key_node.start_mark.line + 1} is "
                    f"not a scalar (a YAML sequence/mapping used as a "
                    f"mapping key) -- only hashable keys are supported"
                ) from None
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
    except _LedgerYamlError as exc:
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


def _iso_date_problem(value: object) -> str | None:
    """Describe why *value* is not a `YYYY-MM-DD` calendar date, or `None`.

    The shape regex and the calendar check are deliberately both applied,
    and reported separately: the regex alone accepts `2026-02-30`, while
    `date.fromisoformat` alone accepts wider ISO 8601 spellings (`20260905`,
    and on 3.11+ a full timestamp) that this schema does not use. Shared by
    `as_of_date` and every `investigated_declined[].decided`, so the two
    cannot drift apart -- each caller supplies its own field prefix.
    """
    if not isinstance(value, str) or not _PIPELINE_DATE_RE.match(value):
        return f"must be 'YYYY-MM-DD', got {value!r}"
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return f"is not a real calendar date: {value!r}"
    return None


def _check_lifecycle(
    f: Findings, rel: str, name: object, entry: dict[object, object]
) -> None:
    """Validate one concept's `lifecycle` rung and its consistency with the
    concept's own `authority` and status fields.

    The ladder (`introduced -> wired -> authoritative -> retired`) is a
    refinement of `authority`, not a second independent opinion about the
    same thing, so the cross-field rules here are what stop the ledger from
    recording a self-contradiction -- `authority: self` at `wired`, or a
    `retired` concept still reporting `consumers: partial`. Each rule is
    applied only when the field it reads is itself valid, so one bad value
    produces one finding rather than a cascade.
    """
    if "lifecycle" not in entry:
        # Already reported by the required-field loop; nothing to add.
        return
    lifecycle = entry["lifecycle"]
    # `x in tuple` is safe for an unhashable value (unlike `in frozenset`),
    # but the `isinstance` guard is kept explicit so the finding names the
    # real problem for a YAML list/mapping rather than silently reporting
    # "not one of ...".
    if not isinstance(lifecycle, str) or lifecycle not in _PIPELINE_LIFECYCLE_VALUES:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: concepts.{name}.lifecycle: {lifecycle!r} is not one of "
            f"{list(_PIPELINE_LIFECYCLE_VALUES)}",
        )
        return
    authority = entry.get("authority")
    allowed = (
        _PIPELINE_AUTHORITY_TO_LIFECYCLES.get(authority)
        if isinstance(authority, str)
        else None
    )
    if allowed is not None and lifecycle not in allowed:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: concepts.{name}: lifecycle {lifecycle!r} contradicts "
            f"authority {authority!r} -- authority {authority!r} admits only "
            f"{sorted(allowed)}. `authority` names which representation "
            f"decides today; `lifecycle` refines it, it does not disagree "
            f"with it",
        )
    rung = _PIPELINE_LIFECYCLE_VALUES.index(lifecycle)
    if rung >= _PIPELINE_LIFECYCLE_VALUES.index("wired"):
        if entry.get("primitive") == "not_started":
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}: lifecycle {lifecycle!r} requires a "
                f"primitive that exists, got primitive: 'not_started'",
            )
        if entry.get("consumers") == "not_started":
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}: lifecycle {lifecycle!r} means "
                f"something downstream reads this concept, but consumers is "
                f"'not_started' -- that is 'introduced'",
            )
    if lifecycle == "retired":
        incomplete = sorted(
            field
            for field in _PIPELINE_STATUS_FIELDS
            if field in entry and entry[field] != "complete"
        )
        if incomplete:
            f.err(
                "pipeline-status-ledger",
                f"{rel}: concepts.{name}: lifecycle 'retired' means the "
                f"replaced implementation is gone, so every status field must "
                f"be 'complete'; these are not: {incomplete}",
            )


def _check_investigated_declined(
    f: Findings, rel: str, name: object, entry: dict[object, object]
) -> None:
    """Validate the optional `investigated_declined` list, and enforce the
    one rule it exists for.

    `duplication-and-convergence-assessment.md`'s "The completion rule this
    plan was missing" identified a real loophole in how items here were
    closed: a migration *investigated and declined* for lack of a
    demonstrated benefit was then treated as equivalent to "the removal gate
    is closed." Declining a **behavioral** change on that basis is correct;
    it does not delete a second **implementation**. So a concept carrying
    any declined-investigation entry cannot sit at `retired` -- the rung
    that asserts the replaced implementation is actually gone.

    An entry is a record of something still open, not a permanent history
    log: once the consolidation it left open genuinely lands, the entry's
    `leaves_open` is no longer true and the entry belongs with the concept's
    narrative owner (the plan section or module docstring named by
    `tracked_as`), which is where the full reasoning lives anyway per
    docs/AGENTS.md's fact-owner/narrative-owner split.
    """
    if "investigated_declined" not in entry:
        return
    declined = entry["investigated_declined"]
    if not isinstance(declined, list) or not declined:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: concepts.{name}.investigated_declined: must be a "
            f"non-empty list (omit the field entirely when nothing was "
            f"investigated and declined -- an empty list is "
            f"indistinguishable from a real one left un-updated)",
        )
        return
    for index, item in enumerate(declined):
        where = f"{rel}: concepts.{name}.investigated_declined[{index}]"
        if not isinstance(item, dict):
            f.err("pipeline-status-ledger", f"{where}: must be a mapping")
            continue
        for field in _PIPELINE_DECLINED_REQUIRED_FIELDS:
            if field not in item:
                f.err(
                    "pipeline-status-ledger",
                    f"{where}: missing required field {field!r}",
                )
                continue
            value = item[field]
            if field == "decided":
                problem = _iso_date_problem(value)
                if problem is not None:
                    f.err("pipeline-status-ledger", f"{where}.decided: {problem}")
            elif not isinstance(value, str) or not value.strip():
                f.err(
                    "pipeline-status-ledger",
                    f"{where}.{field}: must be a non-empty string",
                )
        unknown = set(item) - set(_PIPELINE_DECLINED_REQUIRED_FIELDS)
        if unknown:
            # See the `unknown`/`extra` blocks in the caller: a YAML mapping
            # can carry non-string keys, so `sorted()` needs `key=repr`.
            f.err(
                "pipeline-status-ledger",
                f"{where}: unknown field(s) {sorted(unknown, key=repr)} -- "
                f"either a typo or the schema needs extending deliberately",
            )
    if entry.get("lifecycle") == "retired":
        f.err(
            "pipeline-status-ledger",
            f"{rel}: concepts.{name}: an investigated-and-declined "
            f"disposition is not a synonym for 'retired' -- declining a "
            f"behavioral change for lack of a demonstrated benefit does not "
            f"delete a second implementation, so the consolidation item "
            f"stays open (duplication-and-convergence-assessment.md, "
            f"'The completion rule this plan was missing')",
        )


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
    # The regex alone only checks the YYYY-MM-DD *shape* -- it accepts a
    # calendar-invalid value like "2026-02-30" (a real review finding on
    # PR #1019); `_iso_date_problem` applies the calendar check too, and is
    # shared with `investigated_declined[].decided` so both date fields
    # cannot drift apart.
    date_problem = _iso_date_problem(data.get("as_of_date"))
    if date_problem is not None:
        f.err(
            "pipeline-status-ledger",
            f"{rel}: 'as_of_date' {date_problem}",
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
        _check_lifecycle(f, rel, name, entry)
        _check_investigated_declined(f, rel, name, entry)
        allowed_extra = set(
            _PIPELINE_PER_CONCEPT_EXTRA_REQUIRED_FIELDS.get(name, ())
        ) | set(_PIPELINE_OPTIONAL_CONCEPT_FIELDS)
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

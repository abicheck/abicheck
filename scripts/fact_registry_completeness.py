#!/usr/bin/env python3
"""Real, repo-wide scan enforcing ADR-063 D7's fact/capability registry
(`abicheck/model/fact_registry.py`, Phase 5,
docs/contribute/plans/one-semantic-pipeline.md).

A leaf module imported by ``check_ai_readiness.py``, mirroring
``fact_field_readers.py``'s own extraction/registration pattern
(``check_ai_readiness.py`` is already past this repo's 2000-line
AI-readiness hard cap and only stays green through ``LARGE_FILE_ALLOWLIST``,
not a license to keep growing it).

**What this checks, and why each direction matters.** The Phase 5 plan's
own Tests section states three directions a completeness check must cover
(1-3 below); two Codex review rounds on this module's own introducing PR
found two more real gaps those three cannot catch (4-5) — each closes a
different way a registry could go silently stale:

1. Every ``Fact[T]``-typed model field (a ``<field>_fact`` sibling on a
   ``@dataclass`` under ``abicheck/model/``) has **exactly one**
   ``FactDefinition`` entry in ``FACT_REGISTRY`` — a converted field with no
   registry entry is exactly the "shipped the type, skipped the
   declaration" gap D7 exists to close.
2. Every registry entry names a real, still-existing ``<field>_fact``
   sibling on its declared ``owner`` — a stale entry (the field was
   renamed or removed) is caught here rather than silently describing
   nothing.
3. Every field that is *eligible* for conversion (per
   ``fact_registry.REFERENCE_FLAG_COVERAGE``'s case-(a) flag-backed
   inventory, or this scan's own case-(b) tri-state-annotation-plus-
   documented-ambiguity heuristic) but not yet converted must be named,
   explicitly, in ``fact_registry.KNOWN_UNCONVERTED_ELIGIBLE_FACTS`` — the
   identical allowlist-and-shrink discipline
   ``fact_field_readers.KNOWN_UNMIGRATED_READERS``/
   ``check_ai_readiness.IMPORT_CYCLE_ALLOWLIST`` already establish
   elsewhere in this codebase. A field the scan finds eligible but that is
   in *neither* set fails outright — the "field nobody has touched yet"
   case the plan's Design section names as the actual failure mode
   Phase 0's own history (PR #753's missing registry entry, invisible to
   every test that only audits fields already known to the registry) shows
   a registry-only check cannot catch. The allowlist itself must also stay
   honest: an entry naming a field that no longer exists, or that has
   since been converted, is a stale baseline entry and fails too — shrink
   it rather than leaving dead weight (the same rule
   ``IMPORT_CYCLE_ALLOWLIST`` states for itself in AGENTS.md).
4. Every registry entry with ``persisted=True`` is actually reachable from
   BOTH a real encode path (``storage/fact_codec.py``'s
   ``encode_fact_fields`` — either ``_TYPE_FACT_KEYS`` membership or its
   own hardcoded ``.get(...)`` call) AND a real decode path
   (``decode_record_facts``, or a same-shaped ``decode_fact(...)`` call in
   ``serialization.py`` for the one non-``RecordType``-owned field,
   ``Param.is_va_list_fact``) — checked independently, not as a combined
   occurrence count. Closes a real gap a Codex review round found
   directions 1-2 above miss entirely: they only compare model attribute
   *names* against registry keys, so a registry entry claiming
   ``persisted=True`` with no matching encode/decode wiring at all would
   still pass. A first draft of this direction counted total quoted
   occurrences of the ``fact_attr`` string across both files (``>= 2``) —
   a second review round correctly found that conflates "two encode-only
   references" or "an encoder key plus an unrelated occurrence" with real
   two-sided wiring, so a `persisted=True` fact could still silently lose
   its status on reload. `_encode_wired_fact_attrs()`/
   `_decode_wired_fact_attrs()` instead resolve each side from the real
   AST shape a genuine call site has (`_TYPE_FACT_KEYS` membership or an
   ``encode_fact_fields``-local ``.get(...)`` call for encode; a
   ``decode_fact(<expr>.get("<name>"), ...)`` call for decode) — still no
   full data-flow proof, but real enough to fail on the exact scenario
   both review rounds named (a new ``Fact[T]`` sibling + registry entry
   landing with no matching ``fact_codec.py``/``serialization.py``
   change, on either side).
5. For the six declaration classes ``backend_capabilities.py``'s own
   AST-verified ``FACT_ROWS`` matrix already tracks, a registry entry's
   ``producing_backends`` must agree with it — not merely name a real
   backend from the closed ``KNOWN_PRODUCING_BACKENDS`` vocabulary
   (Codex review: a fact could otherwise claim ``"elf"``, a real backend
   name, as a producer of a header-AST-only field no ELF parser actually
   populates). `_cross_check_against_backend_capabilities()` reuses that
   matrix as independently-verified ground truth rather than
   re-implementing its own parser scan — deliberately does **not** flag a
   claimed backend outside castxml/clang, since that matrix's own scope
   never covered a real third producer like DWARF in the first place; see
   that function's own docstring for the false positive an earlier draft
   found here.
6. A ``<field>_fact`` sibling only counts as a real ``Fact[T]`` value when
   its own annotation is genuinely ``Fact[...]``-shaped (``Fact[X]`` or
   ``Fact[X] | None``) — a field merely *named* with the ``_fact`` suffix
   (e.g. a hypothetical ``widget_fact: dict[str, object]``) is not itself
   evidence (Codex review). ``_model_fact_siblings()`` now parses each
   sibling's real annotation and only counts it when it matches that
   shape, and every registry entry's ``value_type`` must then string-match
   the ``X`` this scan actually finds on its sibling — a registry entry
   claiming ``value_type="bool"`` for a field whose real annotation is
   ``Fact[str]`` disagrees with the code it claims to describe.
7. Every key of ``fact_registry.REFERENCE_FLAG_COVERAGE`` (a
   ``*_facts_reliable`` flag name) must name a real field declared on
   ``AbiSnapshot`` (``abicheck/model/snapshot.py``) — direction 3 above
   only ever unions the *values* (the covered ``(owner, field)`` pairs)
   and silently discards the keys, so a typo'd or renamed flag name (e.g.
   ``clang_vtables_facts_reliable``) would keep passing as long as its
   covered pairs stay tracked, while the generated reference page and the
   eligibility inventory both go on advertising a flag that does not
   exist (Codex review).
8. Every ``KNOWN_UNCONVERTED_ELIGIBLE_FACTS`` entry must still name a
   real, currently-declared field under ``model/`` — the shrink-only loop
   direction 3 also enforces only ever checks whether the pair has
   *gained* a ``_fact`` sibling, so a legacy field renamed or removed
   before ever being converted would sit in the allowlist forever,
   advertising a gap that no longer exists in that form (Codex review).
   Deliberately narrower than re-running the full case-(a)/case-(b)
   eligibility heuristic against every entry: several entries (e.g. the
   three non-``Function`` ``source_header`` fields) are eligible by
   manual inspection rather than by the textual scan's own marker-comment
   heuristic (see ``fact_registry.py``'s own comment on that gap), so
   gating on "still scan-discoverable" would falsely flag those as stale;
   gating on "still exists as a real field at all" catches the concrete
   renamed/removed scenario the review named without that false-positive
   risk.

**Acknowledged, deliberately deferred gap (Codex review, not yet closed).**
A registry entry's ``suppressible``/``reportable`` flags are validated
claims in D7's own design, the same as ``persisted`` — but unlike
``persisted`` (direction 4 above, checked against real ``fact_codec.py``/
``serialization.py`` call sites), no consumer code reading a ``Fact[...]``
sibling for suppression or reporting purposes exists anywhere in this
codebase yet: ``fact_registry.py``'s own ``FactLifecycle`` docstring states
plainly that every entry today sits no higher than ``PERSISTED`` and that
"no detector has migrated ... this is intentional." A wiring check modeled
on direction 4 has nothing real to check against — every current entry
already declares ``reportable=True`` with zero report-schema wiring by
design, so a check requiring real wiring would fail the entire committed
registry rather than catching a genuine future regression. Extending this
module with a direction 4-shaped check for ``suppressible``/``reportable``
is real, valuable follow-up work, but it is gated on suppression/report
consumer wiring landing first (a separate, later phase per the plan's own
scope) — tracked here rather than attempted as a vacuous check today.

**Case (b) heuristic, stated precisely.** A field is a case-(b) candidate
when its annotation textually contains ``| None`` (or ``Optional[``) *and*
one of a fixed set of marker phrases appears in a comment line
immediately preceding its declaration (within the same class body) — the
same textual signal ``docs/AGENTS.md``'s own in-progress-status sweep and
``fact_field_readers.py``'s own scan both already rely on for "no type
inference, pure textual/structural signal, verified against real
call sites" scans in this codebase. This is deliberately narrower than
"every ``Optional`` field in ``model/``" — an ``Optional`` field with no
adjacent backend/schema-dependence comment is not claimed eligible here,
since nothing about its own declaration states an availability ambiguity
(the plan's own worked examples — ``RecordType.is_final``,
``Function.contract_attributes``, ``Variable.alignment_bits`` — all carry
exactly this kind of comment). A plain enum-typed field with no
``Optional`` shape (e.g. ``Variable.access``) is never found by this
heuristic at all — it can only be flagged via case (a)'s flag-backed
inventory, which is exactly why ``REFERENCE_FLAG_COVERAGE`` is checked
first and independently of the annotation-shape heuristic below.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"
MODEL_DIR = PKG / "model"
_SNAPSHOT_PATH = MODEL_DIR / "snapshot.py"

# This script's own directory, so `backend_capabilities` (below) resolves
# whether this module is run directly, loaded as a sibling import from
# check_ai_readiness.py, or loaded as `scripts.fact_registry_completeness`
# by a test that never imported either first -- mirroring
# fact_field_readers.py's own identical guard for its own sibling import.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend_capabilities import FACT_ROWS, Capability  # noqa: E402

#: Marker phrases this codebase already uses, verbatim, to document a
#: field's own backend/schema-dependent tri-state meaning (drawn directly
#: from the real comments on ``RecordType.is_final``/
#: ``Function.contract_attributes``/``Variable.alignment_bits`` and every
#: sibling field the Phase 5 plan's own inventory names) — case-insensitive
#: substring match against the comment lines immediately preceding a
#: field's declaration.
_CASE_B_MARKERS: tuple[str, ...] = (
    "tri-state",
    "does not know",
    "could not determine",
    "not captured",
    "not collected",
    "dumper/loader",
    "older snapshot",
    "legacy snapshot",
)


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

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
        # scan_model_dataclasses()/_model_fact_siblings() accept any
        # model_dir for testability (see tests/test_fact_registry_
        # completeness.py's synthetic-fixture tests) -- a path outside
        # ROOT (a tmp_path fixture) has no repo-relative spelling.
        return p.as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _annotation_text(node: ast.AnnAssign) -> str:
    try:
        return ast.unparse(node.annotation)
    except Exception:  # noqa: BLE001 - best-effort only
        return ""


def _is_optional_annotation(text: str) -> bool:
    return "| None" in text or "Optional[" in text


#: Matches ``Fact[<inner>]`` or ``Fact[<inner>] | None`` -- every real
#: ``<field>_fact`` sibling in this codebase is declared exactly one of
#: these two shapes (the field itself is always ``kw_only`` with a
#: ``None`` default, so the ``| None`` wrapper is near-universal, but the
#: bare form is accepted too since nothing about "is this really a
#: Fact[T]" depends on the sibling's own optionality). Greedy ``.*`` plus
#: anchored ``$`` makes this resolve to the *outermost* ``Fact[...]``, so
#: a nested-bracket inner type (``Fact[list[str]]``) still extracts
#: correctly -- verified directly against every real sibling annotation
#: in ``model/entities.py``/``model/declarations.py``.
_FACT_SHAPE_RE = re.compile(r"^Fact\[(?P<inner>.*)\](?:\s*\|\s*None)?$")


def _fact_annotation_inner_type(annotation_text: str) -> str | None:
    """If ``annotation_text`` is genuinely ``Fact[...]``-shaped, return its
    inner type ``X`` (whitespace-normalized); otherwise ``None``.

    A field merely *named* with the ``_fact`` suffix is not itself
    evidence it holds a real ``Fact[T]`` value (Codex review: a
    hypothetical ``widget_fact: dict[str, object]`` must not silently
    satisfy the "has a Fact sibling" checks the suffix-only scan this
    replaces used to accept unconditionally).
    """
    match = _FACT_SHAPE_RE.match(annotation_text.strip())
    if not match:
        return None
    return " ".join(match.group("inner").split())


def _preceding_comment_text(lines: list[str], lineno: int, lookback: int = 40) -> str:
    """Every ``#``-comment line immediately above ``lineno`` (1-indexed), joined.

    Stops at the first non-comment, non-blank line — a field's own
    documenting comment sits directly above it with nothing else between,
    the same assumption every hand-authored field comment in this codebase
    follows (verified against every real case (b) field this module's own
    docstring names).
    """
    collected: list[str] = []
    i = lineno - 2  # 0-indexed line above the field's own (1-indexed) line
    steps = 0
    while i >= 0 and steps < lookback:
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            collected.append(stripped)
        elif not stripped:
            i -= 1
            steps += 1
            continue
        else:
            break
        i -= 1
        steps += 1
    return " ".join(reversed(collected)).lower()


def _dataclass_field_names(class_node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for stmt in class_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
    return names


def _all_model_dataclass_field_pairs() -> set[tuple[str, str]]:
    """Every ``(owner, field)`` pair declared on any ``@dataclass`` under
    ``model/`` — not filtered by eligibility or ``_fact`` suffix, unlike
    ``_model_fact_siblings()``/``scan_model_dataclasses()``. The ground
    truth Direction 8 checks a ``KNOWN_UNCONVERTED_ELIGIBLE_FACTS`` entry
    against: does the legacy field it names still exist at all, under any
    name it was declared with (Codex review — a field renamed or removed
    before conversion must not be able to sit in the allowlist forever;
    the existing shrink-only loop above only ever checks "has this pair
    gained a ``_fact`` sibling", which a renamed/removed field trivially
    never will, so it would otherwise never be caught).
    """
    pairs: set[tuple[str, str]] = set()
    for path in sorted(MODEL_DIR.glob("**/*.py")):
        source = _read(path)
        if not source:
            continue
        try:
            tree = ast.parse(source, filename=_rel(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (
                    isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass"
                )
                for d in node.decorator_list
            ):
                continue
            for field_name in _dataclass_field_names(node):
                pairs.add((node.name, field_name))
    return pairs


def _abi_snapshot_field_names() -> set[str]:
    """Every field declared directly on ``AbiSnapshot`` in
    ``abicheck/model/snapshot.py``, via AST — the ground truth Direction 7
    validates ``REFERENCE_FLAG_COVERAGE``'s keys against. Returns an empty
    set (rather than raising) if the file is unreadable or the class isn't
    found, so a caller can treat that as "nothing to check against" the
    same way every other best-effort scan in this module does.
    """
    source = _read(_SNAPSHOT_PATH)
    if not source:
        return set()
    try:
        tree = ast.parse(source, filename=_rel(_SNAPSHOT_PATH))
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AbiSnapshot":
            return _dataclass_field_names(node)
    return set()


def scan_model_dataclasses(
    model_dir: Path = MODEL_DIR,
) -> dict[tuple[str, str], tuple[str, int]]:
    """Every case-(b) candidate ``(owner, field)`` found under ``model/``.

    Returns ``{(owner, field): (rel_path, lineno)}`` — a field whose own
    annotation is tri-state-shaped (``X | None``/``Optional[...]``) *and*
    whose immediately-preceding comment names one of ``_CASE_B_MARKERS``,
    scanned across every ``@dataclass`` in every ``model/**/*.py`` module,
    recursively (not only ``entities.py``/``declarations.py`` — the plan's
    own review-found correction: eligibility isn't restricted to any one
    filename pattern — and not only ``model/``'s immediate children: a
    nested subpackage like ``model/change_catalog/`` is real today, and a
    non-recursive scan would silently miss a dataclass declared there;
    Codex review).
    """
    found: dict[tuple[str, str], tuple[str, int]] = {}
    for path in sorted(model_dir.glob("**/*.py")):
        rel = _rel(path)
        source = _read(path)
        if not source:
            continue
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (
                    isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass"
                )
                for d in node.decorator_list
            ):
                continue
            owner = node.name
            field_names = _dataclass_field_names(node)
            for stmt in node.body:
                if not (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                ):
                    continue
                field_name = stmt.target.id
                if field_name.endswith("_fact"):
                    continue
                if f"{field_name}_fact" in field_names:
                    continue  # already converted
                annotation = _annotation_text(stmt)
                if not _is_optional_annotation(annotation):
                    continue
                comment = _preceding_comment_text(lines, stmt.lineno)
                if any(marker in comment for marker in _CASE_B_MARKERS):
                    found[(owner, field_name)] = (rel, stmt.lineno)
    return found


def _model_fact_siblings(
    model_dir: Path = MODEL_DIR,
) -> dict[tuple[str, str], tuple[str, str]]:
    """Every real, genuinely ``Fact[...]``-shaped ``<field>_fact`` sibling
    declared under ``model/``.

    Returns ``{(owner, field): (rel_path, inner_type)}`` (``field``
    without the ``_fact`` suffix, matching ``FactDefinition.field``;
    ``inner_type`` the ``X`` in the sibling's own ``Fact[X]``/
    ``Fact[X] | None`` annotation) — the ground truth "already converted"
    set, read via AST rather than importing every dataclass's live
    fields, so this stays independent of whether ``abicheck`` happens to
    import cleanly in the running environment. ``model_dir`` defaults to
    the real ``abicheck/model/`` but accepts any directory (mirroring
    ``scan_model_dataclasses``) for synthetic-fixture testability.

    A ``*_fact``-suffixed field whose annotation does **not** actually
    parse as ``Fact[...]``-shaped is not counted here at all (Codex
    review, direction 6 in this module's docstring) — suffix alone is not
    evidence, so a hypothetical ``widget_fact: dict[str, object]`` would
    previously have silently satisfied every completeness check a real
    ``Fact[T]`` sibling does.
    """
    siblings: dict[tuple[str, str], tuple[str, str]] = {}
    for path in sorted(model_dir.glob("**/*.py")):
        rel = _rel(path)
        source = _read(path)
        if not source:
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (
                    isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass"
                )
                for d in node.decorator_list
            ):
                continue
            for stmt in node.body:
                if not (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id.endswith("_fact")
                ):
                    continue
                inner = _fact_annotation_inner_type(_annotation_text(stmt))
                if inner is None:
                    continue
                field_name = stmt.target.id[: -len("_fact")]
                siblings[(node.name, field_name)] = (rel, inner)
    return siblings


#: Files that together carry every existing encode/decode wiring for a
#: persisted ``Fact[T]`` sibling — ``storage/fact_codec.py`` owns the
#: ``RecordType``-shaped ones (``_TYPE_FACT_KEYS``/``decode_record_facts``)
#: and the one hardcoded ``Param.is_va_list_fact`` encode line;
#: ``serialization.py`` owns that same field's ``decode_fact(...)`` call
#: site.
_FACT_CODEC_PATH: Path = PKG / "storage" / "fact_codec.py"
_SERIALIZATION_PATH: Path = PKG / "serialization.py"


def _string_constant(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_call_key(node: ast.expr) -> str | None:
    """If ``node`` is ``<expr>.get("<literal>", ...)``, return the literal."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        return _string_constant(node.args[0])
    return None


def _encode_wired_fact_attrs() -> set[str]:
    """Every ``fact_attr`` name ``fact_codec.encode_fact_fields()`` actually
    reaches — real encode-side evidence only (Codex review: the combined
    quoted-occurrence count this replaced could not tell an encode-only or
    decode-only reference apart from real wiring on both sides).

    Two shapes: membership in the ``_TYPE_FACT_KEYS`` tuple (the
    ``RecordType``-owned fields, looped over in the function body), and a
    literal ``.get("<name>")`` call directly inside ``encode_fact_fields``
    itself (the one hardcoded ``Param.is_va_list_fact`` line).
    """
    source = _read(_FACT_CODEC_PATH)
    if not source:
        return set()
    tree = ast.parse(source, filename=str(_FACT_CODEC_PATH))
    wired: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_TYPE_FACT_KEYS" for t in node.targets
        ):
            if isinstance(node.value, ast.Tuple):
                for elt in node.value.elts:
                    name = _string_constant(elt)
                    if name is not None:
                        wired.add(name)
        if isinstance(node, ast.FunctionDef) and node.name == "encode_fact_fields":
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    name = _get_call_key(call)
                    if name is not None:
                        wired.add(name)
    return wired


def _decode_wired_fact_attrs() -> set[str]:
    """Every ``fact_attr`` name some real ``decode_fact(...)`` call reaches,
    across both files that call it — ``storage/fact_codec.py``'s own
    ``decode_record_facts`` (the ``RecordType``-owned fields) and
    ``serialization.py``'s one direct call site (``Param.is_va_list_fact``,
    decoded inline rather than through ``decode_record_facts``).

    A field is decode-wired when ``decode_fact(...)``'s own first
    (positional) argument is itself a ``<expr>.get("<name>")`` call naming
    it — the shape every real call site in this codebase uses.
    """
    wired: set[str] = set()
    for path in (_FACT_CODEC_PATH, _SERIALIZATION_PATH):
        source = _read(path)
        if not source:
            continue
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "decode_fact"
                and node.args
            ):
                name = _get_call_key(node.args[0])
                if name is not None:
                    wired.add(name)
    return wired


#: The two backends `backend_capabilities.py`'s own `FACT_ROWS` actually
#: tracks a real (AST-verified) capability for — see that module's own
#: `test_matrix_claims_match_parser_source`. A `FactDefinition` naming any
#: other backend for a `(owner, field)` pair that matrix also covers is
#: claiming a producer that matrix's own real parser scan has no evidence
#: for.
_HEADER_AST_BACKENDS: frozenset[str] = frozenset({"castxml", "clang"})


def _cross_check_against_backend_capabilities(
    owner: str, field: str, producing_backends: tuple[str, ...]
) -> list[str]:
    """Cross-validate one registry entry's ``producing_backends`` against
    ``backend_capabilities.FACT_ROWS`` for the same ``(owner, field)``,
    when a row exists (Codex review: membership in the closed
    ``KNOWN_PRODUCING_BACKENDS`` vocabulary alone can't catch a real but
    *wrong* backend for one specific fact — e.g. ``"elf"`` naming a
    producer no ELF parser actually populates for a header-AST-owned
    field). Returns a list of human-readable problem strings (empty if
    the entry agrees with the matrix, or if no matching row exists — a
    fact outside the six declaration classes that matrix covers has
    nothing here to cross-check against).

    Real, not vacuous: ``FACT_ROWS`` itself is independently verified
    against the parsers' own AST by ``test_matrix_claims_match_parser_
    source`` in ``tests/test_backend_capability_matrix.py``, so agreeing
    with it is agreeing with real parser evidence, not another hand-typed
    claim.

    **Deliberately does not flag a claimed backend outside
    :data:`_HEADER_AST_BACKENDS`** (``dwarf``, ``pdb``, ``btf``/``ctf``,
    ``elf``/``pe``/``macho``) as wrong — a first draft of this function did,
    and it was a real false positive: ``RecordType.bases``/``vtable``/etc.
    genuinely are also produced by ``dwarf_snapshot.py``, a real third
    producer entirely outside ``backend_capabilities.py``'s own stated scope
    ("the L2 header-AST backend capability matrix"). That module's silence
    about a backend it was never built to track is not evidence the backend
    is wrong, so a claim naming one is neither confirmed nor denied here —
    a real, named limitation of this cross-check, not a gap it silently
    pretends to close.
    """
    row = next((r for r in FACT_ROWS if r.owner == owner and r.field == field), None)
    if row is None:
        return []
    problems: list[str] = []
    real: dict[str, bool] = {
        "castxml": row.castxml != Capability.NONE,
        "clang": row.clang != Capability.NONE,
    }
    claimed = set(producing_backends)
    for backend, has_real_capability in real.items():
        claims = backend in claimed
        if claims and not has_real_capability:
            problems.append(
                f"claims {backend!r} as a producer, but backend_capabilities.py's "
                f"AST-verified matrix says {backend}'s own capability for this "
                f"field is {row.castxml if backend == 'castxml' else row.clang}"
            )
        if has_real_capability and not claims:
            problems.append(
                f"does not name {backend!r} as a producer, but backend_capabilities.py's "
                f"AST-verified matrix says {backend} does populate this field"
            )
    return problems


def check_fact_registry_completeness(f: Findings) -> None:
    """ERROR on any of the eight directions this module's docstring states.

    Imports ``abicheck.model.fact_registry`` directly rather than
    re-parsing its data via AST — that module is a dependency-free leaf
    (D10) with no install-order concern the way a heavier ``abicheck``
    import might carry elsewhere in this file.
    """
    try:
        from abicheck.model.fact_registry import (
            FACT_REGISTRY,
            KNOWN_UNCONVERTED_ELIGIBLE_FACTS,
            REFERENCE_FLAG_COVERAGE,
        )
    except ImportError as exc:
        f.err(
            "fact-registry-completeness",
            f"could not import abicheck.model.fact_registry: {exc} — "
            "run `pip install -e .` first",
        )
        return

    siblings = _model_fact_siblings()  # {(owner, field): (rel_path, inner_type)}

    # Direction 1 + 2: every Fact[T]-typed field <-> exactly one registry entry.
    registry_keys = {(e.owner, e.field) for e in FACT_REGISTRY.entries.values()}
    for owner_field, (rel, _inner) in siblings.items():
        if owner_field not in registry_keys:
            owner, field_name = owner_field
            f.err(
                "fact-registry-completeness",
                f"{rel}: {owner}.{field_name}_fact has no FACT_REGISTRY "
                f"entry in abicheck/model/fact_registry.py — every "
                f"Fact[T]-typed model field needs exactly one "
                f"FactDefinition (ADR-063 D7)",
            )
    for entry in FACT_REGISTRY.entries.values():
        key = (entry.owner, entry.field)
        if key not in siblings:
            f.err(
                "fact-registry-completeness",
                f"FACT_REGISTRY entry {entry.id!r} names "
                f"{entry.owner}.{entry.fact_attr}, but no such field exists "
                f"under abicheck/model/ — stale registry entry (renamed or "
                f"removed field)",
            )

    # Direction 3: eligible-but-unconverted fields must be tracked, not silent.
    case_a: set[tuple[str, str]] = set()
    for pairs in REFERENCE_FLAG_COVERAGE.values():
        case_a.update(pairs)
    case_b = scan_model_dataclasses()

    eligible_unconverted = {p for p in case_a if p not in siblings} | {
        p for p in case_b if p not in siblings
    }

    for owner, field_name in sorted(eligible_unconverted):
        if (owner, field_name) not in KNOWN_UNCONVERTED_ELIGIBLE_FACTS:
            f.err(
                "fact-registry-completeness",
                f"{owner}.{field_name} is availability-ambiguous "
                f"(flag-backed or documented tri-state) but has no "
                f"{field_name}_fact sibling and is not named in "
                f"fact_registry.KNOWN_UNCONVERTED_ELIGIBLE_FACTS — either "
                f"convert it (ADR-063 D7/Phase 5) or add it to that "
                f"allowlist as a reviewed, tracked gap",
            )

    # KNOWN_UNCONVERTED_ELIGIBLE_FACTS must shrink-only: no stale entries.
    for owner, field_name in sorted(KNOWN_UNCONVERTED_ELIGIBLE_FACTS):
        if (owner, field_name) in siblings:
            f.err(
                "fact-registry-completeness",
                f"{owner}.{field_name} is listed in "
                f"KNOWN_UNCONVERTED_ELIGIBLE_FACTS but already has a "
                f"{field_name}_fact sibling — remove the stale allowlist "
                f"entry (shrink-only, per this repo's allowlist "
                f"convention)",
            )

    # Direction 8 (Codex review): an allowlist entry naming a field that has
    # since been renamed or removed (not just "already converted", the only
    # staleness the loop above catches) is a stale entry too — check every
    # entry still names a real, currently-declared model field.
    all_model_fields = _all_model_dataclass_field_pairs()
    if all_model_fields:
        for owner, field_name in sorted(KNOWN_UNCONVERTED_ELIGIBLE_FACTS):
            if (owner, field_name) in siblings:
                continue  # already reported above
            if (owner, field_name) not in all_model_fields:
                f.err(
                    "fact-registry-completeness",
                    f"{owner}.{field_name} is listed in "
                    f"KNOWN_UNCONVERTED_ELIGIBLE_FACTS but no such field "
                    f"exists under abicheck/model/ at all — likely renamed "
                    f"or removed before conversion; remove or update the "
                    f"stale allowlist entry",
                )

    # Direction 4: a persisted entry must actually be wired into BOTH a real
    # encode path and a real decode path — checked independently, not as a
    # combined occurrence count (Codex review: a count alone can't tell an
    # encode-only or decode-only reference apart from real wiring on both
    # sides).
    encode_wired = _encode_wired_fact_attrs()
    decode_wired = _decode_wired_fact_attrs()
    for entry in FACT_REGISTRY.entries.values():
        if not entry.persisted:
            continue
        missing = [
            side
            for side, wired in (("encode", encode_wired), ("decode", decode_wired))
            if entry.fact_attr not in wired
        ]
        if missing:
            f.err(
                "fact-registry-completeness",
                f"{entry.id}: registered with persisted=True, but "
                f"{entry.fact_attr!r} has no real {' or '.join(missing)} "
                f"wiring in storage/fact_codec.py/serialization.py (see "
                f"_TYPE_FACT_KEYS/decode_record_facts, or the "
                f"Param.is_va_list_fact pattern for a non-RecordType "
                f"owner) — this snapshot field would silently fail to "
                f"round-trip",
            )

    # Direction 5: for the six declaration classes backend_capabilities.py's
    # own AST-verified FACT_ROWS matrix already tracks, a registry entry's
    # producing_backends must agree with it, not merely name a real backend
    # from the closed vocabulary (Codex review).
    for entry in FACT_REGISTRY.entries.values():
        problems = _cross_check_against_backend_capabilities(
            entry.owner, entry.field, entry.producing_backends
        )
        for problem in problems:
            f.err(
                "fact-registry-completeness",
                f"{entry.id}: {problem}",
            )

    # Direction 6: a registry entry's value_type must match the real inner
    # type its Fact[...] sibling annotation actually declares (Codex review
    # — a suffix-matched-but-wrong-shaped field is already excluded from
    # `siblings` by _model_fact_siblings() itself; this additionally catches
    # a real Fact[...] sibling whose payload type disagrees with what the
    # registry claims, e.g. value_type="bool" for a real Fact[str]).
    for entry in FACT_REGISTRY.entries.values():
        found = siblings.get((entry.owner, entry.field))
        if found is None:
            continue  # already reported as a stale entry above
        rel, inner_type = found
        if inner_type != entry.value_type:
            f.err(
                "fact-registry-completeness",
                f"{rel}: {entry.id} registers value_type={entry.value_type!r}, "
                f"but its real {entry.fact_attr} annotation is "
                f"Fact[{inner_type}] — the registry disagrees with the code "
                f"it claims to describe",
            )

    # Direction 7: every REFERENCE_FLAG_COVERAGE key must name a real field
    # declared on AbiSnapshot (Codex review — direction 3 above only unions
    # the covered (owner, field) *values* and silently discards the keys, so
    # a typo'd/renamed flag name would keep passing as long as its covered
    # pairs stay tracked).
    snapshot_fields = _abi_snapshot_field_names()
    if snapshot_fields:
        for flag in REFERENCE_FLAG_COVERAGE:
            if flag not in snapshot_fields:
                f.err(
                    "fact-registry-completeness",
                    f"fact_registry.REFERENCE_FLAG_COVERAGE names {flag!r}, "
                    f"but AbiSnapshot (abicheck/model/snapshot.py) has no "
                    f"such field — stale or typo'd reliability-flag key",
                )


if __name__ == "__main__":

    class _CLIFindings:
        def __init__(self) -> None:
            self.errors: list[tuple[str, str]] = []
            self.warnings: list[tuple[str, str]] = []

        def err(self, check: str, msg: str) -> None:
            self.errors.append((check, msg))

        def warn(self, check: str, msg: str) -> None:
            self.warnings.append((check, msg))

    findings = _CLIFindings()
    check_fact_registry_completeness(findings)
    for check, msg in findings.errors:
        print(f"ERROR [{check}] {msg}")
    for check, msg in findings.warnings:
        print(f"WARN  [{check}] {msg}")
    sys.exit(1 if findings.errors else 0)

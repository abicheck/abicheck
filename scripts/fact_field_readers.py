#!/usr/bin/env python3
"""Real, repo-wide scan for unmigrated readers of a `Fact[T]`-bridged legacy
field (ADR-063 Phase 0, docs/contribute/plans/one-semantic-pipeline.md).

A leaf module imported by ``check_ai_readiness.py``, mirroring
``engine_cli_boundary.py``'s own extraction (``check_ai_readiness.py`` is
already past the 2000-line hard cap and only stays green through
``LARGE_FILE_ALLOWLIST``, which is not a license to keep growing it).

**Why this exists.** `RecordType.bases`/`virtual_bases`/`vtable` and
`Param.is_va_list` each carry a `Fact[...]` sibling recording whether the
value is a real determination, a known-imprecise heuristic, or genuinely
uncollected/unsupported/failed -- but the legacy field itself stays a
plain, fully-populated value (`[]`/`False` when unavailable) for backward
compatibility. A reader that accesses the legacy field directly, without
ever consulting `.status`, cannot tell "confirmed empty" apart from "no
evidence" -- the exact ambiguity `Fact[T]` exists to make representable.

The plan doc's own Design section tried enumerating every such reader by
hand and repeatedly missed one: a fifth, then a tenth call site kept
turning up in later review rounds, always outside the `diff_*.py` glob a
first-draft check would have scoped to. Auditing this codebase directly
while building this check (rather than trusting that hand list) found
three more the plan's own table doesn't name yet
(`buildsource/header_graph.py`'s inheritance-edge emitter,
`buildsource/source_extractors/base.py`'s L4/L5 entity-identity fingerprint,
and `idioms.py`'s factory/non-virtual-destructor idiom detectors) --
confirming the plan's own conclusion: a hand-maintained allowlist is the
wrong invariant. This check is the real one: a plain, repo-wide AST scan
for a direct attribute read of one of the five names below, with every
*currently known* reader recorded in `KNOWN_UNMIGRATED_READERS` (an
allowlist-and-shrink baseline, exactly `IMPORT_CYCLE_ALLOWLIST`'s own
convention -- it may only shrink, and a genuinely new entry needs the same
review bar AGENTS.md sets for that allowlist) -- so the *next* hand-missed
call site fails this gate instead of silently joining the ambiguity.

**Exemption is function-scoped, not module-scoped (Codex review, fresh
evidence).** A first draft of this check exempted whole modules
(`dwarf_snapshot.py`, `dumper_layout_backfill.py`) on the theory that they
are pure producer/merge code. That is false for *two of their own
functions*: `dwarf_snapshot._DwarfSnapshotBuilder._filter_types_by_
reachability` reads `bases`/`virtual_bases` to decide which types survive
into the exported snapshot (a real compatibility-relevant decision, not a
value computation), and `dumper_layout_backfill._fields_corroborate` read
`bases`/`virtual_bases`/`vtable` to decide whether two records structurally
match (also a decision, not a merge; since deleted -- the backfill now pairs
records through `model/debug_type_match.py`, which reads none of them). A whole-module exemption hid both
from this scan entirely, and would silently hide the next such function
added to either file. `EXEMPT_FUNCTIONS` is keyed by qualified function
name (`Class.method` for a method, tracked through nested `def`s) so only
the specific bridge/producer functions are exempt; the two decision
functions above are real `KNOWN_UNMIGRATED_READERS` entries like any other.

**Baseline keys include the enclosing function AND the read's own source
text, not a bare occurrence ordinal (Codex review, two rounds, fresh
evidence both times).** A first draft keyed `"<rel>::<attr>::<occurrence>"`
-- purely a top-to-bottom rank among reads of that attribute in that file.
Scoping the occurrence counter to `(qualname, attr)` (the enclosing
function, not just the file) closed the file-wide version of the
collision, but not a narrower one a second round found with fresh
evidence: `diff_param_qualifiers.py`'s `if not p_old.is_va_list and
p_new.is_va_list:` has two DIFFERENT reads (`p_old.is_va_list`,
`p_new.is_va_list`) on one line, in the same function -- a purely
positional rank can't distinguish them, or protect against a future,
unrelated third read inheriting one's rank once it's migrated away. Keys
now also include the read's own exact source text
(`ast.get_source_segment`), so a collision needs the new read to be
*textually identical* to the one it would replace, not merely occupy the
same rank in the same function.

**No type inference.** "On a value whose declared type resolves to
`RecordType`/`Param`" (the Design section's own phrasing) would need a real
type checker; this script runs before `pip install` and must stay pure
stdlib. Instead it matches the five attribute *names* anywhere in
`abicheck/` — verified empirically (by running exactly this scan against
the whole package before writing the baseline below) to have zero
cross-class collisions today: every single hit is genuinely a `RecordType`/
`Param` access. A future addition to this codebase reusing one of these
names on an unrelated class would need re-auditing, the same caveat this
codebase already accepts elsewhere for a structural, non-type-checking scan
(e.g. `backend_capabilities.py`'s own AST-based evidence reader).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Protocol

# This script's own directory, so the sibling `fact_field_readers_scope`
# module below imports whether this file is run directly (Python adds its
# own directory automatically) or loaded as `scripts.fact_field_readers`
# by a test that never imported `check_ai_readiness.py` first (the only
# other thing in this tree that already inserts this same directory) --
# mirroring `fact_detector_misuse.py`'s own identical sys.path guard for
# the identical reason (that file's own split-out sibling module).
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from fact_field_readers_scan import (  # noqa: E402  # noqa: E402  # noqa: E402
    FACT_BRIDGED_ATTRS as FACT_BRIDGED_ATTRS,
    FACT_BRIDGED_CLASS_NAMES as FACT_BRIDGED_CLASS_NAMES,
    ReaderScan,
    ReaderScanContext,
)
from fact_field_readers_scope import (  # noqa: E402
    _enclosing_qualnames,
    _itemgetter_alias_keys,
    _lexical_function_parents,
    _locally_bound_names,
    _operator_attrgetter_aliases,
    _parent_map,
    walk_tree,
)

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"

#: `FACT_BRIDGED_ATTRS`/`FACT_BRIDGED_CLASS_NAMES` live in
#: `fact_field_readers_scan.py` (the per-node matcher) and are re-exported
#: from `fact_field_readers_scan` import above.

#: Permanently exempt, keyed `"<rel>::<qualname>"` (qualname is the
#: function's own name, dotted through an enclosing class -- `Class.method`
#: for a method, tracked through nested `def`s the same way Python's own
#: `__qualname__` is): the field's own dataclass `__post_init__`
#: omission-bridge implementation (`model/`), and the specific DWARF-
#: producer/DWARF-backfill functions that *compute or combine* the raw
#: legacy value itself. Function-scoped, not module-scoped -- see this
#: module's own docstring for the real bug a whole-module exemption caused
#: (two genuine decision functions living in these same two files were
#: hidden from the scan entirely). None of these make a compatibility
#: decision from the field -- they are where the value (and, since
#: ADR-063 Phase 0's second slice, its `Fact[...]` status) comes from, the
#: role `RecordType(bases=...)`'s own keyword construction plays at every
#: producer (which this attribute-*read* scan can't see at all, since it
#: only looks at reads on an existing instance, not constructor keywords).
#: Unlike `KNOWN_UNMIGRATED_READERS` below, this set does not shrink --
#: there is no "migrated" state for a bridge or a producer to reach.
#: Empty since ADR-063 Phase 10 retired the five legacy fields to read-only
#: views (model/fact.py's RetiredBridgeField): the bridges read their InitVar
#: parameters and the DWARF/backfill producers read the facts.
EXEMPT_FUNCTIONS: frozenset[str] = frozenset()

#: Every currently-known unmigrated semantic reader, keyed
#: `"<rel>::<qualname>::<attr>::<outer-expr>::<expr-text>::<occurrence>"` --
#: see this module's own docstring for the exact key shape and the
#: successive collision classes it had to close.
#:
#: **Empty as of ADR-063 Phase 0's detector-migration completion.** Every
#: reader this baseline ever recorded (the plan doc's own "nine distinct
#: modules, ten call sites" table, the primary detectors --
#: `diff_layout.py`/`diff_types.py`/`diff_vtable_layout.py`/
#: `diff_param_qualifiers.py`/`diff_cxx_rules.py` -- and every additional
#: reader this check's own construction found:
#: `buildsource/header_graph.py`, `buildsource/source_extractors/base.py`,
#: `idioms.py`, `contract_evidence_collect.py`, `diff_time64.py`,
#: `diff_stdlib_impl.py`, `diff_cpp_patterns.py`, `dumper_scoping.py`,
#: `export_surface.py`, `internal_leak.py`, `surface.py`, `surface_graph.py`,
#: `type_reachability.py`, and the two decision functions living inside
#: otherwise-exempt producer modules --
#: `dwarf_snapshot._DwarfSnapshotBuilder._filter_types_by_reachability` and
#: `dumper_layout_backfill._fields_corroborate`, since deleted) has migrated to read the
#: `Fact[...]` sibling via `model.resolved_fact_value()` instead of the bare
#: legacy attribute -- see `docs/contribute/plans/one-semantic-pipeline.md`
#: Phase 0's own "Detector migration -- landed" note. This stays a real,
#: live, repo-wide scan (not a `diff_*.py` glob), not a stub: a genuinely
#: new direct read of one of the five bridged fields anywhere in
#: `abicheck/` still fails this gate on sight, with nothing left to hide
#: behind. `KNOWN_UNMIGRATED_READERS` remains the mechanism's name (and
#: stays a `frozenset[str]`, not deleted) since a future producer/detector
#: change could legitimately reintroduce a reviewed, temporarily-unmigrated
#: site the same allowlist-and-shrink way `IMPORT_CYCLE_ALLOWLIST` does.
KNOWN_UNMIGRATED_READERS: frozenset[str] = frozenset()


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _imported_class_aliases(tree: ast.Module) -> dict[str, str]:
    """Map every local name *tree* binds to one of `FACT_BRIDGED_CLASS_NAMES`
    -- either via an `import ... as` (`from abicheck.model import
    RecordType as RT` maps `"RT" -> "RecordType"`), a simple whole-tree
    name assignment (`RT = RecordType` maps the same way; a *chained*
    assignment, `RT = Alias = RecordType`, maps every plain-name target
    identically, since they all receive the same RHS; an annotated
    assignment `RT: type = RecordType` maps identically too; a further
    `RT2 = RT` chains to `"RecordType"` too, resolved to a fixed point the
    same way `fact_detector_misuse._fact_aliases` chains local aliases),
    or a *qualified* class reference (`import abicheck.model as model; RT
    = model.RecordType` -- resolved immediately by attribute name alone,
    matching the identical name-only stance the `import ... as` branch
    above already takes for *its* qualifying source module) -- back to
    its real name. All are real, found by Codex review with fresh
    evidence: a positional class pattern on such an alias, `case
    RT(_, _, _, _, _, []):`, is invisible to a bare-name check against the
    literal `RecordType`/`Param` spellings, whichever way the alias was
    established -- the chained-assignment, annotated-assignment, and
    qualified-reference shapes are the same gap the plain-`ast.Assign` fix
    already closed, just reached through a differently-shaped assignment
    (or a differently-typed AST node) that a check keyed on the original
    shape alone never visits. An import/assignment with no local rename
    needs no entry -- the bare name already matches directly. Whole-tree,
    not function-scoped: every mechanism here is almost always module
    level, and scanning the whole tree is the same
    over-approximating-is-safe stance this module already takes
    elsewhere.
    """
    aliases: dict[str, str] = {}
    assign_candidates: list[tuple[str, str]] = []

    def _register_assign(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
        elif (
            isinstance(value, ast.Attribute) and value.attr in FACT_BRIDGED_CLASS_NAMES
        ):
            # `RT = model.RecordType` -- resolves immediately, the same
            # way an `import ... as` alias does, since the qualifying
            # module name is never checked either way.
            aliases[target] = value.attr

    for node in walk_tree(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in FACT_BRIDGED_CLASS_NAMES and local != alias.name:
                    aliases[local] = alias.name
        elif isinstance(node, ast.Assign):
            # Every plain-`Name` target, not only a lone one -- a chained
            # assignment (`RT = Alias = RecordType`) gives every target
            # the identical RHS (Codex review, fresh evidence: the
            # single-target restriction wrongly excluded this ordinary,
            # unrelated shape too).
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _register_assign(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _register_assign(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _register_assign(node.target.id, node.value)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in aliases:
                continue
            if ref in FACT_BRIDGED_CLASS_NAMES:
                aliases[local] = ref
                changed = True
            elif ref in aliases:
                aliases[local] = aliases[ref]
                changed = True
    return aliases


def _builtins_getattr_aliases(
    tree: ast.Module,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(getattr_names, builtins_module_names)``: every local name
    *tree* binds to the real `getattr` builtin (always includes the bare
    `"getattr"` itself, plus any `from builtins import getattr as X`, plus
    a plain assignment chain such as `read_attr = getattr` --
    `read_attr2 = read_attr` chains too, resolved to a fixed point, and a
    *chained* assignment (`read1 = read2 = getattr`) marks every plain-
    name target the same way -- plus a *qualified* assignment such as
    `read_attr = builtins.getattr`, plus an *annotated* assignment of
    either shape, e.g. `read_attr: Callable[..., object] = getattr`), and
    every local name bound to the `builtins` module itself (`import
    builtins`, `import builtins as b`, or a plain assignment alias of an
    already-known one, `b = builtins` -- resolved to a fixed point the
    same way a `getattr` alias chain already is) -- used to recognize
    `builtins.getattr(...)`/`b.getattr(...)` alongside a bare call. All
    are real (Codex review, fresh evidence, five rounds: `import
    builtins; builtins.getattr(rec, "bases")`, `from builtins import
    getattr as read_attr`, `read_attr = getattr; read_attr(rec,
    "bases")`, combining the qualified-call recognition with the
    plain-assignment chaining as `read_attr = builtins.getattr;
    read_attr(rec, "bases")`, the annotated-assignment spelling of either
    -- `read_attr: Callable[..., object] = getattr` -- a chained
    assignment, `read1 = read2 = getattr` -- and a plain assignment alias
    of the `builtins` module itself, `b = builtins` -- are all invisible
    to a scan that only matches the literal bare callee `getattr`).
    Whole-tree, matching `_imported_class_aliases`'s own scope for the
    identical reason -- and the plain-assignment chaining mirrors that
    function's own fixed-point resolution of `RT = RecordType`/`RT2 = RT`
    exactly, just for the builtin callable (and, now, the `builtins`
    module name itself) instead of a class name; the annotated-assignment
    and chained-assignment branches mirror that same function's own
    `ast.AnnAssign`/multi-target `ast.Assign` handling.
    """
    getattr_names = {"getattr"}
    builtins_names: set[str] = set()
    assign_candidates: list[tuple[str, str]] = []
    # `local = <module-name>.getattr` -- resolved once, after the walk
    # below has finished collecting every `import builtins` occurrence,
    # since (unlike the plain-name candidates) this needs the *complete*
    # `builtins_names` set to know whether `<module-name>` really is one.
    qualified_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
        elif (
            isinstance(value, ast.Attribute)
            and value.attr == "getattr"
            and isinstance(value.value, ast.Name)
        ):
            qualified_candidates.append((target, value.value.id))

    for node in walk_tree(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    builtins_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == "getattr":
                    getattr_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            # Every plain-`Name` target, not only a lone one -- a chained
            # assignment (`read1 = read2 = getattr`) gives every target
            # the identical RHS (Codex review, fresh evidence: the
            # single-target restriction wrongly excluded this ordinary,
            # unrelated shape too, the identical gap fixed in
            # `_imported_class_aliases`'s own `ast.Assign` branch).
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    # `b = builtins` -- a plain assignment alias of the `builtins` module
    # itself, resolved to a fixed point the same way a `getattr` alias
    # chain already is, reusing the identical `assign_candidates` list
    # (Codex review, fresh evidence: `import builtins; b = builtins`
    # then `b.getattr(rec, "bases")` was invisible, since `builtins_names`
    # was only ever populated from a real `import` statement). Resolved
    # *before* `qualified_candidates` below, so `b.getattr(...)` is
    # recognized through the now-expanded `builtins_names` too.
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in builtins_names:
                continue
            if ref in builtins_names:
                builtins_names.add(local)
                changed = True
    for local, base in qualified_candidates:
        if base in builtins_names:
            getattr_names.add(local)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in getattr_names:
                continue
            if ref in getattr_names:
                getattr_names.add(local)
                changed = True
    return frozenset(getattr_names), frozenset(builtins_names)


def _builtins_symbol_aliases(
    tree: ast.Module, symbol: str, builtins_names: frozenset[str]
) -> frozenset[str]:
    """Return every local name *tree* binds to the real *symbol* builtin
    (e.g. `"vars"`) -- always includes the bare *symbol* itself, plus any
    `from builtins import <symbol> as X`, plus a plain assignment chain
    (`read_map = vars; read_map2 = read_map` chains too, resolved to a
    fixed point), plus a *qualified* assignment such as `read_map =
    builtins.vars` given the caller's already-resolved *builtins_names*
    (Codex review, fresh evidence: `import builtins; builtins.vars(rec)
    ["bases"]` and `read_map = vars; read_map(rec).get("bases")` were
    both invisible to `_is_mapping_receiver()`'s bare `"vars"` check).

    A generalized sibling of `_builtins_getattr_aliases()`'s own
    identical alias-resolution mechanism for `getattr` specifically --
    taking *builtins_names* as a parameter rather than re-deriving it
    (the caller already computed it via that function, and `vars`'s own
    aliasing needs no second, independent `import builtins` collection)
    keeps this to the *symbol*-specific half of that mechanism only,
    without a third hand-duplicated copy of the shared `import
    builtins`/module-alias machinery `_builtins_getattr_aliases()` itself
    already owns. `_builtins_getattr_aliases()` is deliberately left
    unchanged rather than refactored to share this helper -- it is
    already hardened across five review rounds (see its own docstring),
    and generalizing it risks reopening one of them for no benefit,
    since it already returns exactly the `builtins_names` this function
    needs as an input.
    """
    symbol_names = {symbol}
    assign_candidates: list[tuple[str, str]] = []
    qualified_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
        elif (
            isinstance(value, ast.Attribute)
            and value.attr == symbol
            and isinstance(value.value, ast.Name)
        ):
            qualified_candidates.append((target, value.value.id))

    for node in walk_tree(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == symbol:
                    symbol_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    for local, base in qualified_candidates:
        if base in builtins_names:
            symbol_names.add(local)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in symbol_names:
                continue
            if ref in symbol_names:
                symbol_names.add(local)
                changed = True
    return frozenset(symbol_names)


def _unbound_getattribute_receiver_aliases(tree: ast.Module) -> frozenset[str]:
    """Return every local name *tree* binds to the real `object` or `type`
    builtin -- always includes the bare `"object"`/`"type"` themselves,
    plus any `from builtins import object as O`/`from builtins import type
    as T`, plus a plain assignment chain (`O = object; O2 = O` chains too,
    resolved to a fixed point, mirroring `_builtins_getattr_aliases()`'s
    own `getattr`-alias chaining exactly).

    Used by the unbound `__getattribute__` recognition branch (Codex
    review, fresh evidence): `from builtins import object as O;
    O.__getattribute__(rec, "bases")` is the identical dynamic read as the
    unaliased `object.__getattribute__(rec, "bases")` spelling, but the
    receiver-name check there originally matched only the two literal
    strings `"object"`/`"type"`, missing this ordinary import-alias form
    entirely.
    """
    names: set[str] = {"object", "type"}
    assign_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))

    for node in walk_tree(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name in ("object", "type"):
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in names:
                continue
            if ref in names:
                names.add(local)
                changed = True
    return frozenset(names)


def _unbound_getattribute_method_aliases(
    tree: ast.Module, object_type_names: frozenset[str]
) -> frozenset[str]:
    """Return every local name *tree* binds to the unbound method itself
    -- `object.__getattribute__`/`type.__getattribute__` (or an alias of
    `object`/`type` from *object_type_names*) assigned to a plain name,
    plus any further plain-assignment chain from there, resolved to a
    fixed point (mirroring `_builtins_symbol_aliases()`'s own qualified-
    candidate mechanism).

    Used by the unbound `__getattribute__` recognition branch (Codex
    review, fresh evidence): `read_attr = object.__getattribute__;
    read_attr(rec, "bases")` performs the identical unbound-method read as
    `object.__getattribute__(rec, "bases")`, but the call-matching branch
    there requires the callee itself to still be an `ast.Attribute` (`X.
    __getattribute__(...)`) -- it has no notion of the method having been
    lifted out to a bare name first, which `_unbound_getattribute_
    receiver_aliases()` (this function's sibling, tracking aliases of the
    *receiver* `object`/`type` themselves) does not cover either, since
    the alias here is of the *method*, not of `object`/`type`.
    """
    names: set[str] = set()
    assign_candidates: list[tuple[str, str]] = []
    qualified_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
        elif (
            isinstance(value, ast.Attribute)
            and value.attr == "__getattribute__"
            and isinstance(value.value, ast.Name)
        ):
            qualified_candidates.append((target, value.value.id))

    for node in walk_tree(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    for local, base in qualified_candidates:
        if base in object_type_names:
            names.add(local)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in names:
                continue
            if ref in names:
                names.add(local)
                changed = True
    return frozenset(names)


def _mapping_receiver_aliases(
    tree: ast.Module, vars_names: frozenset[str], builtins_names: frozenset[str]
) -> frozenset[str]:
    """Return every local name *tree* binds to an instance's own mapping
    receiver -- `vars(rec)` (any spelling *vars_names* already resolves),
    `builtins.vars(rec)` (a qualified call through a real `builtins`
    alias, given the caller's already-resolved *builtins_names*), or
    `X.__dict__` -- plus any further plain-name assignment chain from
    there, resolved to a fixed point (mirroring every other alias helper
    in this module).

    Used to close a real gap (Codex review, fresh evidence): `fields =
    vars(rec); return fields["bases"]` / `fields = rec.__dict__; return
    fields.get("bases")` both read the identical normalized legacy value
    `_is_mapping_receiver()`'s direct forms already recognize, but neither
    is visible to it once the mapping is stored in an intermediate
    variable first -- the same "no alias tracking" gap this module's other
    alias-resolution helpers already close for `getattr`/`vars`/
    `attrgetter` themselves, applied here to the *result* of calling one
    of them instead.

    Deliberately name-only, like every alias source this module tracks:
    `fields = vars(rec)` binds `fields` to *some* instance's `__dict__`
    without this module ever knowing which instance -- but that's already
    all `_is_mapping_receiver()`'s direct forms need, since the question
    is only "is this expression structurally a read through an instance's
    own mapping," never "which instance." Not gated on `_shadowed()`
    during collection (a shadowed `vars`/`builtins` at the *assignment's*
    own point would make this over-collect) -- matching the identical,
    established stance `_builtins_getattr_aliases()`/`_operator_
    attrgetter_aliases()` already take for their own alias collection:
    shadowing is checked only where a resolved name is actually
    *consumed*, not during collection.
    """
    names: set[str] = set()
    assign_candidates: list[tuple[str, str]] = []

    def _add_candidate(target: str, value: ast.expr | None) -> None:
        if value is None:
            return
        if isinstance(value, ast.Name):
            assign_candidates.append((target, value.id))
            return
        if (
            isinstance(value, ast.Call)
            and len(value.args) == 1
            and (
                (isinstance(value.func, ast.Name) and value.func.id in vars_names)
                or (
                    isinstance(value.func, ast.Attribute)
                    and value.func.attr == "vars"
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id in builtins_names
                )
            )
        ):
            names.add(target)
            return
        if isinstance(value, ast.Attribute) and value.attr == "__dict__":
            names.add(target)

    for node in walk_tree(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _add_candidate(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            _add_candidate(node.target.id, node.value)
    changed = True
    while changed:
        changed = False
        for local, ref in assign_candidates:
            if local in names:
                continue
            if ref in names:
                names.add(local)
                changed = True
    return frozenset(names)


def unmigrated_fact_reader_sites(
    tree: ast.Module, rel: str, source: str = ""
) -> list[tuple[str, int, str, str]]:
    """Return one ``(allowlist_key, lineno, attr, qualname)`` per attribute
    read of a `Fact`-bridged field found in *tree* (already parsed from
    *rel*, whose raw text is *source*).

    Only `ast.Load` context counts -- a `Store`/`Del` (an assignment like
    `storage/fact_codec.py`'s legacy-schema backfill `record.vtable = []`)
    is writing the field, not reading it as if it were unambiguous, and is
    not the failure mode this check exists to catch.

    A `getattr(obj, "vtable", ...)` call with the attribute name as a
    literal string constant is a dynamic equivalent of `obj.vtable` and is
    detected too (Codex review, fresh evidence: `diff_cpp_patterns.
    _is_empty_record` reads `vtable` exactly this way, invisible to a scan
    that only matches `ast.Attribute` nodes). A non-literal second
    argument (`getattr(obj, name, ...)`) can't be resolved statically and
    is out of scope, the same "no type inference" limit this module's own
    docstring already states for the attribute case.

    A structural-pattern-matching read (`case RecordType(bases=[]):`) is
    detected too (Codex review, fresh evidence): Python represents a class
    pattern's keyword attributes as `ast.MatchClass.kwd_attrs` (a
    `list[str]`, paired positionally with `kwd_patterns`) rather than as
    an `ast.Attribute` or a `getattr()` call, so it is invisible to both
    branches above -- `case RecordType(bases=[]):` reads `bases` exactly
    as much as `rec.bases` does, and would have collapsed unavailable and
    confirmed-empty the same way. Each matched keyword's own pattern node
    supplies the location; the whole class pattern's source text
    (`RecordType(bases=[])`, not just the one keyword) is the key's
    `expr-text`, since a `MatchClass` node has no location of its own for
    a single keyword. Verified empirically to have zero existing hits in
    `abicheck/` today -- no match/case statement currently patterns on any
    of these five fields.

    A *positional* class pattern (`case RecordType(_, _, _, _, _, []):`)
    is also flagged, though it can't be resolved to a specific field name
    (Codex review, fresh evidence): a positional pattern's Nth element
    binds to `cls.__match_args__[N]`, generated by `@dataclass` in
    declaration order -- deriving that order correctly and keeping it in
    sync with `model/entities.py`/`declarations.py` would need real
    introspection this pure-AST, pre-`pip install` script can't do (the
    same "no type inference" limit already stated below). Rather than
    silently missing this shape the way the keyword-only fix above still
    would have, ANY non-empty positional pattern on a `MatchClass` whose
    `cls` resolves (by bare name, following an import alias -- see below)
    to `RecordType`/`Param` (`FACT_BRIDGED_CLASS_NAMES`) is reported with a
    synthetic `<positional>` attr, on the conservative-by-design principle
    this whole module already applies: a false positive here (a positional
    pattern that happens to touch none of the five bridged fields) costs a
    reviewed baseline entry; a false negative would be silent.

    **An import alias is resolved before that name check (Codex review,
    fresh evidence).** `from abicheck.model import RecordType as RT` then
    `case RT(_, _, _, _, _, []):` names the identical class, but a bare
    `node.cls.id in FACT_BRIDGED_CLASS_NAMES` check rejects `"RT"` outright
    -- invisible to the positional-pattern fix above despite being exactly
    the shape it exists to catch. `_imported_class_aliases()` maps every
    such local alias back to its real name (whole-tree, since an import is
    visible for its entire enclosing scope regardless of where a later
    pattern uses it), and the positional-pattern check resolves through it
    before testing membership.

    **The key also fingerprints the *containing expression*, not only the
    read's own bare expression (Codex review, third round, fresh
    evidence).** Two DIFFERENT call sites sharing the same bare attribute
    spelling -- `old_decision(rec.bases)` and, elsewhere in the same
    function, `keep(rec.bases)` -- previously produced keys differing only
    by occurrence ordinal (`...::rec.bases::1`, `...::rec.bases::2`).
    Migrating `old_decision` away and adding an unrelated third read
    (`unrelated_new_decision(rec.bases)`) anywhere in the same function
    re-numbers the survivors from scratch in encounter order -- `keep`
    silently drops to rank 1 (colliding with `old_decision`'s vacated key,
    harmless since `keep` was already reviewed) but the *new* read then
    lands on rank 2, silently inheriting `keep`'s own baseline entry. Purely
    positional disambiguation among textually-identical bare reads can
    never close this: two different call sites are not the same site no
    matter what they're numbered. Fixed by including each read's
    *outermost containing expression* (climbing every enclosing `ast.expr`
    via a one-pass parent map, stopping at the first statement boundary --
    :func:`_outermost_containing_expr`) in the key alongside the read's own
    bare expression text: `old_decision(rec.bases)` and `keep(rec.bases)`
    now differ at that component regardless of ordinal, closing the common
    case without an ordinal at all. **Deliberately the containing
    *expression*, not the containing *statement*** -- a first version of
    this fix climbed to the nearest `ast.stmt` instead, which for a
    compound statement (`if <test>: <body>`) pulls in the entire body,
    not just the condition: `diff_param_qualifiers.py`'s `if not p_old.
    is_va_list and p_new.is_va_list: changes.append(make_change(...))`
    produced an unwieldy, body-dependent key that would silently break the
    moment anything *inside that body* changed, regardless of whether the
    read itself did. Stopping at the outermost *expression* instead gives
    `not p_old.is_va_list and p_new.is_va_list` -- the whole boolean test,
    still shared by both reads, but none of the body -- so the bare
    expression text (kept, not replaced) is still what tells `p_old.
    is_va_list` and `p_new.is_va_list` apart from each other, exactly as
    the previous round already fixed. A genuinely duplicated expression
    (the identical containing expression appearing twice, with identical
    bare-read text, in one function) still falls back to the ordinal, an
    accepted, narrow residual matching this module's own "false positive
    over false negative" stance throughout.

    **A local alias of the `getattr` builtin itself is resolved too (Codex
    review, fresh evidence).** `read_attr = getattr` then `read_attr(rec,
    "bases")` is the identical dynamic read as `getattr(rec, "bases")`, but
    `_builtins_getattr_aliases()` originally only ever collected the bare
    name and a `from builtins import getattr as X` import -- a plain
    assignment chain was invisible. Fixed by extending that function with
    the same fixed-point assignment-chaining `_imported_class_aliases`
    already does for a class alias (`RT = RecordType`, `RT2 = RT`), just
    for the builtin callable instead of a class name -- see that function's
    own docstring.

    **An augmented assignment is treated as an implicit read of its target
    (Codex review, fresh evidence).** `rec.bases += inherited` updates a
    bridged field, but Python represents the *target* Attribute node with
    `ast.Store` context even though the operation reads the field's
    existing value first, to combine it with the right-hand side, before
    writing the result back -- an ordinary `Store`/`Del` (a plain
    `record.vtable = []` overwrite, this function's own opening paragraph)
    genuinely never reads, but an `AugAssign` target always does. The
    Load-only restriction above therefore missed this shape entirely: the
    target Attribute node is still visited independently by `ast.walk`
    (it's a child of the `AugAssign`), but its `Store` context skips the
    ordinary attribute branch too, so nothing caught it. Fixed with a
    dedicated `ast.AugAssign` branch matching the target's own attribute
    name, keyed on the target Attribute node itself (not the whole
    `AugAssign` statement) so its site/text line up with an ordinary
    attribute read at the same position.

    **Two more standard dynamic-attribute-reading forms are detected too
    (Codex review, fresh evidence).** `operator.attrgetter("bases")(rec)`
    (or the bare `attrgetter(...)` spelling reached via `from operator
    import attrgetter`, or an `import operator as op`/`from operator import
    attrgetter as ag` alias of either -- resolved via `_operator_attrgetter_
    aliases()`, the identical import-alias mechanism `_builtins_getattr_
    aliases()` already provides for `getattr`/`builtins`) and `rec.
    __getattribute__("bases")`/`object.__getattribute__(rec, "bases")` both
    read `rec.bases` exactly as much as the attribute/`getattr()` forms
    above do -- `getattr()` is itself defined in terms of `__getattribute__`,
    and `attrgetter` is the standard-library callable-returning equivalent.
    `attrgetter` additionally accepts *any number* of positional field
    names and reads every one of it -- `attrgetter("size_bits", "bases")
    (rec)` reads `bases` too, not only the first argument (Codex review,
    fresh evidence) -- so every literal, string-constant argument matching
    a bridged name is inspected and reported independently, handled as its
    own top-level case rather than folded into the single-attribute chain
    below. Both dynamic forms stay scoped to the same "no type inference"
    limit as the rest of this scan: only a literal-string field name is
    recognized per argument (`attrgetter("a.b")`, which chains a *second*
    attribute access, is out of scope, same as a non-literal `getattr()`
    default). Matched at the point of *construction*, not only an
    immediate call -- see `_is_attrgetter_constructor_call()`'s own
    docstring for why this also catches a getter assigned to an
    intermediate variable or handed to another function as a callback,
    without needing dedicated alias tracking the way `getattr` itself
    does.

    **A local binding that shadows `getattr`/`builtins` is excluded from
    the bare-name builtin match (Codex review, fresh evidence).** `def
    f(getattr, rec): return getattr(rec, "bases")` -- an ordinary,
    unrelated function parameter reusing the name `getattr` -- was
    unconditionally treated as the real `getattr` builtin regardless of
    what the enclosing function actually bound it to, blocking a valid,
    unrelated change with a misleading error. Fixed with a new
    `_locally_bound_names()` (this function's own parameters plus any
    ordinary same-function assignment target, scoped narrower than
    `fact_detector_misuse.py`'s own exhaustive shadowing machinery --
    see that helper's own docstring for exactly what's covered and why)
    consulted at each `getattr`/`builtins` match site: a name shadowed in
    the call's own enclosing function is excluded from the match.
    """
    qualnames = _enclosing_qualnames(tree)
    parents = _parent_map(tree)
    class_aliases = _imported_class_aliases(tree)
    getattr_names, builtins_names = _builtins_getattr_aliases(tree)
    vars_names = _builtins_symbol_aliases(tree, "vars", builtins_names)
    mapping_receiver_names = _mapping_receiver_aliases(tree, vars_names, builtins_names)
    # `dict` is a real, always-in-scope builtin (the identical "no import
    # required" category `getattr` itself is in), so its own alias family
    # is resolved the same reusable way `vars`'s already is -- no new
    # collector needed.
    dict_names = _builtins_symbol_aliases(tree, "dict", builtins_names)
    object_type_names = _unbound_getattribute_receiver_aliases(tree)
    unbound_getattribute_names = _unbound_getattribute_method_aliases(
        tree, object_type_names
    )
    attrgetter_names, operator_names, getitem_names, itemgetter_names = (
        _operator_attrgetter_aliases(tree)
    )
    itemgetter_alias_keys = _itemgetter_alias_keys(
        tree, itemgetter_names, operator_names
    )
    locally_bound, recognized_alias_scopes = _locally_bound_names(tree)
    lexical_parents = _lexical_function_parents(tree)

    ctx = ReaderScanContext(
        qualnames=qualnames,
        parents=parents,
        class_aliases=class_aliases,
        getattr_names=getattr_names,
        builtins_names=builtins_names,
        vars_names=vars_names,
        mapping_receiver_names=mapping_receiver_names,
        dict_names=dict_names,
        object_type_names=object_type_names,
        unbound_getattribute_names=unbound_getattribute_names,
        attrgetter_names=attrgetter_names,
        operator_names=operator_names,
        getitem_names=getitem_names,
        itemgetter_names=itemgetter_names,
        itemgetter_alias_keys=itemgetter_alias_keys,
        locally_bound=locally_bound,
        recognized_alias_scopes=recognized_alias_scopes,
        lexical_parents=lexical_parents,
    )
    # Every per-node reader form (and the `_shadowed`/`_is_mapping_receiver`
    # logic they share) lives in `fact_field_readers_scan.ReaderScan`.
    matches = ReaderScan(tree, source, ctx).run()
    matches.sort(key=lambda m: (m[0], m[1]))
    occurrence: dict[tuple[str, str, str, str], int] = {}
    sites: list[tuple[str, int, str, str]] = []
    for lineno, _col, attr, qualname, outer_text, text in matches:
        occ_key = (qualname, attr, outer_text, text)
        occurrence[occ_key] = occurrence.get(occ_key, 0) + 1
        key = f"{rel}::{qualname}::{attr}::{outer_text}::{text}::{occurrence[occ_key]}"
        sites.append((key, lineno, attr, qualname))
    return sites


def check_fact_field_readers(f: Findings) -> None:
    """ERROR if a function outside `EXEMPT_FUNCTIONS` reads a `Fact`-bridged
    legacy field (`RecordType.bases`/`virtual_bases`/`vtable`/
    `vptr_offset_bits`, `Param.is_va_list`) directly, without the read
    being a previously reviewed, `KNOWN_UNMIGRATED_READERS`-baselined site.

    Real, repo-wide AST scan, not a `diff_*.py` glob -- see this module's
    own docstring for why a glob (or any other hand-maintained scope) is
    exactly what let a real reader keep going unnoticed across several
    review rounds. New violations are rejected outright; every
    *currently* known one is recorded in `KNOWN_UNMIGRATED_READERS`
    (docs/contribute/plans/one-semantic-pipeline.md, Phase 0's Design
    section) rather than silently passing.
    """
    for path in sorted(PKG.rglob("*.py")):
        rel = _rel(path)
        source = _read(path)
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        for key, lineno, attr, qualname in unmigrated_fact_reader_sites(
            tree, rel, source
        ):
            if f"{rel}::{qualname}" in EXEMPT_FUNCTIONS:
                continue
            if key in KNOWN_UNMIGRATED_READERS:
                continue
            f.err(
                "fact-field-readers",
                f"{rel}:{lineno}: reads `{attr}` directly without ever "
                "consulting its Fact[...] sibling's .status anywhere in "
                "this reader -- this collapses 'confirmed empty/false' "
                "and 'no evidence' onto the same value; either migrate "
                "this reader's own logic to read the Fact[...] sibling's "
                ".value/.status instead of the raw legacy field (a "
                "preceding .status check that still reads the legacy "
                "field afterward is NOT recognized as compliant -- this "
                "scan has no control-flow analysis and flags the direct "
                "read regardless of what precedes it), or add its stable "
                "key to KNOWN_UNMIGRATED_READERS in "
                "scripts/fact_field_readers.py if it's a genuinely new, "
                "reviewed baseline entry (see "
                "docs/contribute/plans/one-semantic-pipeline.md Phase 0)",
            )

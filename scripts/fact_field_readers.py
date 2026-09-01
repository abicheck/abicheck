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
value computation), and `dumper_layout_backfill._fields_corroborate` reads
`bases`/`virtual_bases`/`vtable` to decide whether two records structurally
match (also a decision, not a merge). A whole-module exemption hid both
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
from fact_field_readers_scope import (  # noqa: E402
    _attrgetter_matched_name,
    _enclosing_qualnames,
    _is_attrgetter_constructor_call,
    _is_itemgetter_constructor_call,
    _itemgetter_alias_keys,
    _itemgetter_matched_name,
    _lexical_function_parents,
    _locally_bound_names,
    _operator_attrgetter_aliases,
    _outermost_containing_expr,
    _parent_map,
    _target_bound_names,
)

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"

#: The five `Fact[T]`-bridged legacy field names this phase converted
#: (ADR-063 Phase 0's Scope section: `RecordType.bases`/`virtual_bases`/
#: `vtable`/`vptr_offset_bits`, `Param.is_va_list`). `vptr_offset_bits` was
#: first left out of this set on the theory that it was "already
#: meaningfully `None`... never itself ambiguous" -- wrong (Codex review,
#: fresh evidence): `model/entities.py` gives it the identical
#: `_OMITTED_VPTR_OFFSET_BITS` sentinel-based omission bridge the other
#: four fields use (`RecordType()` backfills `Fact.not_collected()`;
#: `RecordType(vptr_offset_bits=None)` backfills `Fact.present(None)` --
#: two different Facts for the identical `None` legacy value), so a direct
#: read has exactly the same unavailable-vs-confirmed ambiguity.
FACT_BRIDGED_ATTRS: frozenset[str] = frozenset(
    {"bases", "virtual_bases", "vtable", "is_va_list", "vptr_offset_bits"}
)

#: The two dataclasses `FACT_BRIDGED_ATTRS` fields live on
#: (`model/entities.py`'s `RecordType`, `model/declarations.py`'s
#: `Param`). Used only by the positional-class-pattern branch below --
#: every other branch matches purely on attribute *name*, with no need to
#: know which class it belongs to.
FACT_BRIDGED_CLASS_NAMES: frozenset[str] = frozenset({"RecordType", "Param"})

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
EXEMPT_FUNCTIONS: frozenset[str] = frozenset(
    {
        "abicheck/model/entities.py::RecordType.__post_init__",
        "abicheck/model/declarations.py::Param.__post_init__",
        "abicheck/dwarf_snapshot.py::_DwarfSnapshotBuilder._finalize_vptr_offsets",
        "abicheck/dumper_layout_backfill.py::_backfilled_record",
    }
)

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
#: `dumper_layout_backfill._fields_corroborate`) has migrated to read the
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

    for node in ast.walk(tree):
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

    for node in ast.walk(tree):
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

    for node in ast.walk(tree):
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

    for node in ast.walk(tree):
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

    for node in ast.walk(tree):
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

    for node in ast.walk(tree):
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

    def _shadowed(call_node: ast.expr, name: str) -> bool:
        # A lambda parameter shadows innermost of all, checked directly
        # against the call's real AST ancestry rather than through the
        # qualname system (Codex review, fresh evidence): `lambda getattr,
        # rec: getattr(rec, "bases")` -- an unrelated, ordinary lambda
        # parameter reusing the builtin-looking name -- was still treated
        # as the real builtin, since `_enclosing_qualnames()`/`_locally_
        # bound_names()` deliberately don't model a lambda as its own
        # scope at all (see their own docstrings) -- a lambda's body
        # shares its *enclosing* function's qualname, so `getattr` was
        # never recorded as bound anywhere the qualname-based check below
        # could see. Rather than widening the qualname/scope machinery
        # itself (a materially larger change touching three functions'
        # worth of established, narrower-by-design modeling), this walks
        # the call's own true ancestor chain via `parents` -- exact by
        # construction, so it can never misattribute a shadow to a call
        # genuinely outside the lambda, even one sharing the same line/
        # qualname the coarser model below would conflate them under.
        #
        # Typed `ast.expr` rather than `ast.Call` (Codex review, fresh
        # evidence): every existing call site here happens to pass a real
        # `ast.Call`, but a bare-name mapping-receiver alias
        # (`_mapping_receiver_aliases()` below) needs to shadow-check an
        # `ast.Name` node instead -- and this function only ever consults
        # `.lineno` and walks `parents`, both common to any expression, so
        # the narrower `ast.Call` annotation was never load-bearing.
        node: ast.AST = call_node
        # Set only while ascending through a generator clause's own
        # subtree -- carried forward across the next hop (from the
        # `ast.comprehension` clause object up to its owning `ListComp`/
        # etc.) since that clause object, not `.iter`/`.ifs` themselves,
        # is what a comprehension's own `generators[k]` actually holds.
        # `comprehension_gen_clause` identifies *which* generator the
        # call originates from (`None` when it's reached directly through
        # the comprehension's own `elt`/`key`/`value`, i.e. after every
        # generator's target is bound); `comprehension_via_iter`
        # distinguishes that generator's own `.iter` (evaluates before
        # *that* generator's own target is bound) from its `.ifs` (runs
        # after). See the comprehension branch below for how these two
        # combine into the exact binding-order rule.
        comprehension_gen_clause: ast.comprehension | None = None
        comprehension_via_iter = False
        while id(node) in parents:
            child = node
            node = parents[id(node)]
            if isinstance(node, ast.comprehension):
                if child is node.iter:
                    comprehension_gen_clause = node
                    comprehension_via_iter = True
                elif child in node.ifs:
                    comprehension_gen_clause = node
                    comprehension_via_iter = False
                else:
                    comprehension_gen_clause = None
                continue
            if isinstance(node, ast.Lambda):
                # Only a call reached from the lambda's own *body* is
                # shadowed by its parameters -- a default value is a
                # child of `node.args`, not `node.body`, and evaluates at
                # lambda-*creation* time, in the enclosing scope, before
                # the lambda's own parameters exist at all (Codex review,
                # fresh evidence): `lambda getattr=getattr(rec, "bases"):
                # getattr` -- the default reads the real builtin, but was
                # still treated as shadowed by the lambda's own `getattr`
                # parameter, the identical def-time-vs-body-time
                # distinction `_enclosing_qualnames`'s own default/
                # annotation handling already draws for a named `def`.
                # `child is node.body` is exact rather than merely
                # line-based: it's true only on the hop that ascends
                # directly out of the body subtree (however deeply the
                # call is nested inside it), and false for every hop
                # coming from `node.args` instead.
                if child is node.body:
                    lambda_args = (
                        *node.args.posonlyargs,
                        *node.args.args,
                        *node.args.kwonlyargs,
                        *((node.args.vararg,) if node.args.vararg else ()),
                        *((node.args.kwarg,) if node.args.kwarg else ()),
                    )
                    if any(arg.arg == name for arg in lambda_args):
                        return True
            elif isinstance(
                node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                # A comprehension's own `for` target shadows innermost
                # too, checked here rather than through the qualname
                # system for the identical reason a lambda parameter is
                # (Codex review, fresh evidence, a real regression in an
                # earlier revision of this same fix): a comprehension
                # genuinely introduces its own new scope in Python 3, so
                # recording its target against the coarser, function-only
                # qualname model `_locally_bound_names()` uses elsewhere
                # would shadow every call anywhere later in the *whole
                # enclosing function*, not just calls genuinely inside the
                # comprehension -- `[x for getattr in funcs]` followed by
                # an unrelated, later `getattr(rec, "bases")` in the same
                # function must still be flagged.
                #
                # **Binding order across *multiple* generators matters
                # too, not just the outermost one (Codex review, fresh
                # evidence): a blanket check over every generator's own
                # target wrongly shadowed a call in a *later* generator's
                # own iterable by that same generator's not-yet-bound
                # target.** `[x for x in xs for getattr in getattr(rec,
                # "bases")]` -- the second generator's own iterable
                # (`getattr(rec, "bases")`) evaluates *before* that same
                # generator's own target exists, exactly the way the
                # first generator's iterable evaluates before ANY target
                # exists -- so only the *earlier* generators' targets
                # (index strictly less than the one whose iterable the
                # call is reached through) may shadow it. A generator's
                # own `.ifs` filter, by contrast, runs *after* that
                # generator's own target is bound, so a filter shadows
                # against every generator up to and including its own.
                # And the comprehension's own final `elt`/`key`/`value`
                # (reached with no intervening generator clause at all,
                # `comprehension_gen_clause is None`) runs after every
                # generator's target is bound, so it shadows against all
                # of them. Determined via `node.generators.index(...)`
                # rather than tracking an index during the ascent itself,
                # since the clause object identifies its own position
                # unambiguously and this keeps the ascent loop's own
                # state (`comprehension_gen_clause`/`comprehension_
                # via_iter`) uniform across every comprehension kind.
                if comprehension_gen_clause is None:
                    shadowing_generators = node.generators
                else:
                    gen_index = node.generators.index(comprehension_gen_clause)
                    shadowing_generators = node.generators[
                        : gen_index if comprehension_via_iter else gen_index + 1
                    ]
                if any(
                    bound_name == name
                    for generator in shadowing_generators
                    for bound_name in _target_bound_names(generator.target)
                ):
                    return True
        qualname = qualnames.get(call_node.lineno, "<module>")
        # Walk the call's entire lexical scope chain, not just its own
        # innermost function (Codex review, fresh evidence): a nested
        # function that binds no parameter of its own can still have the
        # name shadowed via an ordinary Python closure over an *enclosing*
        # function's own parameter -- see `_lexical_function_parents`'s
        # own docstring for the exact repro.
        while True:
            if name in locally_bound.get(qualname, ()):
                return True
            # A recognized alias-source import (`from operator import
            # attrgetter as ag`) resolves the name definitively at this
            # scope -- stop here, unshadowed, rather than continuing to
            # walk outward and potentially finding a completely
            # unrelated same-named binding in an enclosing scope (Codex
            # review, fresh evidence: see `_locally_bound_names()`'s own
            # docstring for the exact repro this closes).
            if name in recognized_alias_scopes.get(qualname, ()):
                return False
            if qualname == "<module>":
                return False
            qualname = lexical_parents.get(qualname, "<module>")

    def _is_mapping_receiver(value: ast.expr) -> bool:
        """True if *value* is `vars(rec)` (a bare-name call resolved
        through `vars_names` -- covers a real `vars` alias too, e.g.
        `read_map = vars; read_map(rec)`, not just the literal spelling,
        gated on `_shadowed()` for whichever name actually matched),
        `builtins.vars(rec)` (a qualified call through a real `builtins`
        alias, Codex review, fresh evidence: `import builtins; builtins.
        vars(rec)["bases"]` was invisible to the bare-name check alone),
        or `rec.__dict__` (an attribute access, nothing for a local
        binding to shadow) -- shared by both the subscript
        (`vars(rec)["bases"]`) and `.get()` (`vars(rec).get("bases")`,
        Codex review, fresh evidence) mapping-read forms, so the two
        can't independently drift on what counts as "an instance's own
        mapping". Also true for a bare name already resolved to one of
        those forms via `_mapping_receiver_aliases()` (Codex review, fresh
        evidence): `fields = vars(rec); fields["bases"]` / `fields = rec.
        __dict__; fields.get("bases")` were both invisible before, since
        neither is directly `vars(rec)`-shaped or `X.__dict__`-shaped at
        the point this function actually inspects it.

        A walrus used directly as the mapping expression itself --
        `(fields := vars(rec))["bases"]` -- unwraps to its own `.value`
        before any of the checks below run (Codex review, fresh evidence):
        the alias `fields` is a real, useful binding for a *later* read,
        but this specific expression reads the field right here, in the
        very statement that introduces the alias, and none of the checks
        below recognize an `ast.NamedExpr` node directly. Unwrapping once,
        at the top, composes for free with every existing shape (`vars(
        rec)`, `builtins.vars(rec)`, `X.__dict__`, an already-resolved
        alias name) rather than needing a duplicate NamedExpr-aware copy of
        each."""
        if isinstance(value, ast.NamedExpr):
            value = value.value
        if isinstance(value, ast.Call) and len(value.args) == 1:
            if (
                isinstance(value.func, ast.Name)
                and value.func.id in vars_names
                and not _shadowed(value, value.func.id)
            ):
                return True
            if (
                isinstance(value.func, ast.Attribute)
                and value.func.attr == "vars"
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id in builtins_names
                and not _shadowed(value, value.func.value.id)
            ):
                return True
        if isinstance(value, ast.Attribute) and value.attr == "__dict__":
            return True
        return (
            isinstance(value, ast.Name)
            and value.id in mapping_receiver_names
            and not _shadowed(value, value.id)
        )

    def _expr_text(node: ast.AST) -> str:
        outer = _outermost_containing_expr(node, parents)
        text = ast.get_source_segment(source, outer) if source else None
        return text or "<unavailable>"

    matches: list[tuple[int, int, str, str, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.MatchClass):
            # `case RecordType(bases=[]):` -- structural pattern matching
            # reads a keyword attribute (`kwd_attrs`, a list[str], paired
            # positionally with `kwd_patterns`) without ever producing an
            # `ast.Attribute` or a `getattr()` call (Codex review, fresh
            # evidence): invisible to both branches below.
            # A MatchClass pattern node is not itself an `ast.expr` (it's a
            # `pattern`), so `_expr_text` on it degenerates to the identical
            # `class_text` -- there's no larger *expression* to climb into
            # here, and the whole class pattern is already the right
            # granularity for both key components.
            class_text = (
                ast.get_source_segment(source, node) if source else None
            ) or "<unavailable>"
            qualname = qualnames.get(node.lineno, "<module>")
            for kwd_attr, kwd_pattern in zip(node.kwd_attrs, node.kwd_patterns):
                if kwd_attr not in FACT_BRIDGED_ATTRS:
                    continue
                matches.append(
                    (
                        kwd_pattern.lineno,
                        kwd_pattern.col_offset,
                        kwd_attr,
                        qualname,
                        class_text,
                        class_text,
                    )
                )
            cls_name: str | None = None
            if isinstance(node.cls, ast.Name):
                cls_name = node.cls.id
            elif isinstance(node.cls, ast.Attribute):
                cls_name = node.cls.attr
            resolved_cls_name = (
                class_aliases.get(cls_name, cls_name) if cls_name else None
            )
            if node.patterns and resolved_cls_name in FACT_BRIDGED_CLASS_NAMES:
                # A positional pattern can't be resolved to a specific
                # field name without real `__match_args__` introspection
                # (see this function's own docstring) -- report it
                # unconditionally rather than silently missing it.
                matches.append(
                    (
                        node.lineno,
                        node.col_offset,
                        "<positional>",
                        qualname,
                        class_text,
                        class_text,
                    )
                )
            continue
        if (
            isinstance(node, ast.Call)
            and _is_attrgetter_constructor_call(node, attrgetter_names, operator_names)
            and not _shadowed(node, _attrgetter_matched_name(node))
        ):
            # `operator.attrgetter("bases")` (or a resolved alias of either
            # name) *constructs* a getter that will read `bases` off
            # whatever it's later called with -- reported at the point of
            # construction, not only when it's called *immediately*
            # (`operator.attrgetter("bases")(rec)`). Matching only the
            # doubly-called shape missed the equally common callback
            # spelling entirely (Codex review, fresh evidence):
            # `sorted(records, key=operator.attrgetter("bases"))` and
            # `map(attrgetter("bases"), records)` both construct the
            # identical getter, just hand it to another function instead
            # of calling it themselves -- the read still happens, on
            # whatever `sorted`/`map` eventually calls it with. Matching
            # the constructor call directly, regardless of how its result
            # is used, closes this the same conservative-by-design way
            # every other branch here does: a false positive here (a
            # constructed-but-never-called getter) costs a reviewed
            # baseline entry; a false negative would be silent. Handled as
            # its own top-level case (like `MatchClass` above), not folded
            # into the single-attribute chain below, since `attrgetter`
            # accepts *any number* of positional field names and reads
            # every one of them (Codex review, fresh evidence:
            # `attrgetter("size_bits", "bases")(rec)` reads `bases` too,
            # not only the first argument) -- each literal, string-constant
            # argument matching a bridged name is its own real read,
            # reported independently. A non-literal argument stays out of
            # scope, the same "no type inference" limit the plain `getattr`
            # case already accepts for a non-literal default. A *dotted*
            # argument (`attrgetter("bases.foo")`) is recognized on its
            # first component only -- reading `field.partition(".")` off
            # the literal string needs no type inference, unlike resolving
            # what a *later* component's own receiver type actually is, so
            # only the first component is ever matched/reported (Codex
            # review, fresh evidence).
            # Fingerprinted the same way every other reader form is
            # (Codex review, fresh evidence): `outer_text` climbs to the
            # read's own *outermost containing expression* via
            # `_expr_text()`, distinct from `text`, the constructor call's
            # own bare source -- an earlier revision used the call's own
            # bare text for both slots, so `old_decision(attrgetter(
            # "bases")(rec))` and `keep(attrgetter("bases")(rec))` produced
            # the identical key. Migrating the first reader while adding an
            # unrelated new one at the same rank would then have silently
            # reused the vacated key, the exact collision
            # `_outermost_containing_expr()` exists to close for every
            # other form. `_outermost_containing_expr()` still climbs
            # through an immediate outer call (`ast.Call` is itself an
            # `ast.expr`), so the doubly-called shape's `outer_text` is
            # unaffected by matching the inner constructor call instead of
            # the outer one.
            text = (
                ast.get_source_segment(source, node) if source else None
            ) or "<unavailable>"
            outer_text = _expr_text(node)
            qualname = qualnames.get(node.lineno, "<module>")
            for call_arg in node.args:
                if not (
                    isinstance(call_arg, ast.Constant)
                    and isinstance(call_arg.value, str)
                ):
                    continue
                field = call_arg.value
                if field in FACT_BRIDGED_ATTRS:
                    matched_field = field
                else:
                    # A *dotted* attrgetter path (`attrgetter("bases.foo")`)
                    # chains a second `getattr()` off whatever the first
                    # component reads -- `attrgetter`'s own documented
                    # behavior (Codex review, fresh evidence). Unlike a
                    # *second* component, which really would need type
                    # inference to resolve (the runtime type of `rec.bases`
                    # is not known here), the *first* component is read
                    # directly off the literal argument text itself, via a
                    # single string split -- no inference involved, the
                    # identical "read the literal argument" step the
                    # non-dotted case already does. Only the first
                    # component is ever reported; a match on a later
                    # component stays out of scope, preserving the
                    # docstring's "no type inference" limit for exactly the
                    # part that would actually need it.
                    first, sep, _rest = field.partition(".")
                    if sep and first in FACT_BRIDGED_ATTRS:
                        matched_field = first
                    else:
                        continue
                matches.append(
                    (
                        node.lineno,
                        node.col_offset,
                        matched_field,
                        qualname,
                        outer_text,
                        text,
                    )
                )
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Call)
            and _is_itemgetter_constructor_call(
                node.func, itemgetter_names, operator_names
            )
            and not _shadowed(node.func, _itemgetter_matched_name(node.func))
            and len(node.args) == 1
            and _is_mapping_receiver(node.args[0])
        ):
            # `operator.itemgetter("bases")(vars(rec))` -- the
            # `attrgetter`-shaped constructor spelling of the identical
            # subscript read, for the *bare* or `operator`-qualified
            # `itemgetter` (`itemgetter_names`/`operator_names`, resolved
            # the same way `attrgetter`'s own aliasing already is) (Codex
            # review, fresh evidence). Matched only at the outer, immediate
            # call -- unlike `attrgetter`'s own wider "match wherever
            # constructed" stance, this requires the constructed getter to
            # be called directly on a real mapping receiver, the identical
            # `_is_mapping_receiver()` gate every other subscript-reading
            # form here already applies, since an ungated `itemgetter(...)`
            # constructor match would also fire for a completely unrelated
            # mapping's own "bases" key -- see `_is_itemgetter_constructor_
            # call()`'s own docstring for why the two forms don't share one
            # stance.
            #
            # **Every constructor argument is inspected, not only a lone
            # one, mirroring `attrgetter`'s own multi-key handling above
            # (Codex review, fresh evidence).** `operator.itemgetter(
            # "foo", "bases")(vars(rec))` returns a getter that reads
            # *both* requested keys as a tuple -- Python's own documented
            # `itemgetter` behavior -- so requiring exactly one
            # constructor argument silently missed the second, bridged
            # key. Handled as its own top-level case (like `attrgetter`
            # above), not folded into the single-attribute chain below,
            # for the identical reason: each literal, string-constant
            # argument matching a bridged name is its own real read,
            # reported independently. A non-literal argument stays out of
            # scope, the same "no type inference" limit every other form
            # here already accepts.
            text = (
                ast.get_source_segment(source, node) if source else None
            ) or "<unavailable>"
            outer_text = _expr_text(node)
            qualname = qualnames.get(node.lineno, "<module>")
            for call_arg in node.func.args:
                if not (
                    isinstance(call_arg, ast.Constant)
                    and isinstance(call_arg.value, str)
                    and call_arg.value in FACT_BRIDGED_ATTRS
                ):
                    continue
                matches.append(
                    (
                        node.lineno,
                        node.col_offset,
                        call_arg.value,
                        qualname,
                        outer_text,
                        text,
                    )
                )
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.NamedExpr)
            and isinstance(node.func.value, ast.Call)
            and _is_itemgetter_constructor_call(
                node.func.value, itemgetter_names, operator_names
            )
            and not _shadowed(
                node.func.value, _itemgetter_matched_name(node.func.value)
            )
            and len(node.args) == 1
            and _is_mapping_receiver(node.args[0])
        ):
            # `(get := operator.itemgetter("bases"))(vars(rec))` -- a
            # walrus used directly as the call's own callee, immediately
            # invoking the getter it just constructed, rather than binding
            # `get` for a *later* call (already handled by the plain-Name
            # alias branch below via `itemgetter_alias_keys`) (Codex
            # review, fresh evidence). Mirrors the identical `getattr`
            # walrus-callee handling above (`(read := getattr)(rec,
            # "bases")`): checked against the walrus's own `.value` (the
            # itemgetter constructor call actually being invoked), not its
            # `.target` (the alias name being bound, irrelevant to whether
            # *this* call is a bridged-field read). Every constructor
            # argument is inspected, the identical multi-key handling the
            # immediate-construction-and-call branch above already applies.
            text = (
                ast.get_source_segment(source, node) if source else None
            ) or "<unavailable>"
            outer_text = _expr_text(node)
            qualname = qualnames.get(node.lineno, "<module>")
            for call_arg in node.func.value.args:
                if not (
                    isinstance(call_arg, ast.Constant)
                    and isinstance(call_arg.value, str)
                    and call_arg.value in FACT_BRIDGED_ATTRS
                ):
                    continue
                matches.append(
                    (
                        node.lineno,
                        node.col_offset,
                        call_arg.value,
                        qualname,
                        outer_text,
                        text,
                    )
                )
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in itemgetter_alias_keys
            and not _shadowed(node, node.func.id)
            and len(node.args) == 1
            and _is_mapping_receiver(node.args[0])
        ):
            # `get = operator.itemgetter("bases"); get(vars(rec))` -- the
            # constructed getter stored in a variable before being called,
            # rather than called immediately at the point of construction
            # (Codex review, fresh evidence). `itemgetter_alias_keys`
            # (`_itemgetter_alias_keys()`) already resolved which
            # variables hold such a getter and what literal keys it was
            # built with; every one of those keys is checked here the
            # identical way the immediate-call branch above checks the
            # constructor's own arguments directly.
            text = (
                ast.get_source_segment(source, node) if source else None
            ) or "<unavailable>"
            outer_text = _expr_text(node)
            qualname = qualnames.get(node.lineno, "<module>")
            for alias_key in itemgetter_alias_keys[node.func.id]:
                if alias_key not in FACT_BRIDGED_ATTRS:
                    continue
                matches.append(
                    (
                        node.lineno,
                        node.col_offset,
                        alias_key,
                        qualname,
                        outer_text,
                        text,
                    )
                )
            continue
        record_node: ast.expr
        if (
            isinstance(node, ast.Attribute)
            and node.attr in FACT_BRIDGED_ATTRS
            and isinstance(node.ctx, ast.Load)
        ):
            attr = node.attr
            record_node = node
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Attribute)
            and node.target.attr in FACT_BRIDGED_ATTRS
        ):
            # `rec.bases += inherited` -- Python marks the target `ast.
            # Store`, even though the operation reads the field's existing
            # value before combining it with the right-hand side (Codex
            # review, fresh evidence). The Load-only restriction above
            # therefore misses it entirely: the target Attribute node is
            # still visited independently by `ast.walk` (it's a child of
            # this AugAssign), but its `ctx` is `Store`, so the branch
            # above skips it too -- this is the only place this implicit
            # read is caught, keyed on the target attribute itself (not
            # the whole AugAssign statement) so its site/text line up with
            # an ordinary attribute read.
            attr = node.target.attr
            record_node = node.target
        elif (
            isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id in getattr_names
                    and not _shadowed(node, node.func.id)
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getattr"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in builtins_names
                    and not _shadowed(node, node.func.value.id)
                )
                or (
                    # `(read := getattr)(rec, "bases")` -- a walrus used
                    # directly as the call's own callee (Codex review,
                    # fresh evidence): `read` is a real, useful alias for
                    # a *later* call too (already tracked by
                    # `_builtins_getattr_aliases()`'s own `ast.NamedExpr`
                    # branch), but this specific call reads the field
                    # right here, in the very expression that introduces
                    # the alias -- checked against the walrus's own
                    # `.value` (what is actually being called), not its
                    # `.target` (the alias name being bound, irrelevant to
                    # whether *this* call is a getattr read).
                    isinstance(node.func, ast.NamedExpr)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in getattr_names
                    and not _shadowed(node, node.func.value.id)
                )
            )
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattribute__"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            # `rec.__getattribute__("bases")` -- the bound-method spelling
            # of the same dynamic read `getattr(rec, "bases")` performs,
            # and the one every object's own `getattr()` implementation is
            # defined in terms of (Codex review, fresh evidence).
            attr = node.args[0].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattribute__"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in object_type_names
            and not _shadowed(node, node.func.value.id)
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            # `object.__getattribute__(rec, "bases")` -- the unbound-method
            # spelling used to bypass an instance's own overridden
            # `__getattribute__`, reading `rec.bases` exactly the same way.
            # `object_type_names` also covers an import alias of either
            # builtin (`from builtins import object as O; O.
            # __getattribute__(rec, "bases")` -- Codex review, fresh
            # evidence), not just the two literal spellings. `object`/
            # `type`/an alias of either are ordinary names here, so a
            # parameter shadowing one (`def f(object, rec): return object.
            # __getattribute__(rec, "bases")`) must not match -- the same
            # exclusion the getattr/attrgetter branches above already
            # apply (Codex review, fresh evidence).
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in unbound_getattribute_names
            and not _shadowed(node, node.func.id)
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            # `read_attr = object.__getattribute__; read_attr(rec,
            # "bases")` -- the unbound method itself lifted out to a
            # plain local (or a chain from there) before being called,
            # rather than called directly off `object`/`type`/an alias of
            # either the way the branch just above matches (Codex review,
            # fresh evidence). Reads `rec.bases` exactly the same way; a
            # local shadowing the alias name is excluded the same way
            # every other dynamic-read branch here already is.
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and node.slice.value in FACT_BRIDGED_ATTRS
            and _is_mapping_receiver(node.value)
        ):
            # `vars(rec)["bases"]` / `rec.__dict__["bases"]` -- both read
            # the normalized legacy value the same way `rec.bases` does,
            # through the instance's own `__dict__` mapping rather than
            # attribute-lookup machinery (Codex review, fresh evidence).
            # Only a literal string key is in scope -- a computed key
            # (`vars(rec)[name]`) can't be resolved statically, the
            # identical "no type inference" limit every other dynamic form
            # here already accepts.
            attr = node.slice.value
            record_node = node
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Subscript)
            and isinstance(node.target.slice, ast.Constant)
            and isinstance(node.target.slice.value, str)
            and node.target.slice.value in FACT_BRIDGED_ATTRS
            and _is_mapping_receiver(node.target.value)
        ):
            # `rec.__dict__["bases"] += values` / `vars(rec)["bases"] +=
            # values` -- the identical implicit-read shape the dedicated
            # `ast.Attribute`-target `AugAssign` branch above already
            # covers for `rec.bases += inherited`, applied to the mapping
            # forms instead (Codex review, fresh evidence). Python marks
            # an `AugAssign` target `ast.Store` regardless of shape, so
            # the ordinary Subscript branch above (which requires `ast.
            # Load`) never matches this target either, even though the
            # operation reads the field's existing value first. Keyed on
            # the target Subscript node itself, not the whole `AugAssign`
            # statement, so its site/text line up with an ordinary
            # subscript read at the same position -- mirroring the
            # attribute-target branch's own `record_node = node.target`
            # choice exactly.
            attr = node.target.slice.value
            record_node = node.target
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_mapping_receiver(node.func.value)
            and len(node.args) >= 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            # `vars(rec).get("bases")` / `rec.__dict__.get("bases")` --
            # the `dict.get()` spelling of the identical mapping read the
            # subscript branch above already catches, with the same
            # optional-default shape `getattr()`'s own second argument
            # already has (Codex review, fresh evidence: reads the exact
            # same normalized legacy value, invisible to the subscript
            # branch since neither is an `ast.Subscript`). An optional
            # second argument (the default) is accepted but not
            # inspected, matching how `getattr()`'s own third argument
            # is treated elsewhere in this module.
            attr = node.args[0].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getitem__"
            and _is_mapping_receiver(node.func.value)
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            # `vars(rec).__getitem__("bases")` -- the explicit dunder-
            # method spelling of the identical subscript read
            # `vars(rec)["bases"]` already catches, the same bound-method
            # relationship `rec.__getattribute__("bases")` already has to
            # `rec.bases` elsewhere in this module (Codex review, fresh
            # evidence).
            attr = node.args[0].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getitem__"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in dict_names
            and not _shadowed(node, node.func.value.id)
            and len(node.args) == 2
            and _is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            # `dict.__getitem__(vars(rec), "bases")` -- the *unbound*-
            # method spelling of the bound `vars(rec).__getitem__("bases")`
            # form just above, the identical relationship
            # `object.__getattribute__(rec, "bases")` already has to
            # `rec.__getattribute__("bases")` elsewhere in this module
            # (Codex review, fresh evidence). `dict_names` covers an
            # import alias of `dict` too (`from builtins import dict as
            # D; D.__getitem__(vars(rec), "bases")`), reusing
            # `_builtins_symbol_aliases()`'s already-generic mechanism
            # rather than a fourth hand-duplicated alias collector.
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in dict_names
            and not _shadowed(node, node.func.value.id)
            and len(node.args) >= 2
            and _is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            # `dict.get(vars(rec), "bases")` -- the *unbound*-method
            # spelling of the bound `vars(rec).get("bases")` form above,
            # the identical relationship the unbound `dict.__getitem__`
            # branch already has to its own bound sibling (Codex review,
            # fresh evidence). An optional third argument (the default)
            # is accepted but not inspected, matching the bound form's
            # own identical treatment.
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getitem"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in operator_names
            and not _shadowed(node, node.func.value.id)
            and len(node.args) == 2
            and _is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            # `operator.getitem(vars(rec), "bases")` -- the standard-
            # library callable spelling of the identical subscript read,
            # via a real `operator` module alias (`operator_names`, the
            # same resolved set `attrgetter`'s own module-qualified form
            # already uses) (Codex review, fresh evidence).
            attr = node.args[1].value
            record_node = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in getitem_names
            and not _shadowed(node, node.func.id)
            and len(node.args) == 2
            and _is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            # `from operator import getitem as gi; gi(vars(rec),
            # "bases")` -- the bare-name spelling of the identical
            # standard-library callable read, via a real `getitem` import
            # alias (`getitem_names`, resolved the same way `attrgetter_
            # names` already is) rather than the qualified `operator.
            # getitem(...)` form the branch above matches (Codex review,
            # fresh evidence).
            attr = node.args[1].value
            record_node = node
        else:
            continue
        text = (
            ast.get_source_segment(source, record_node) if source else None
        ) or "<unavailable>"
        qualname = qualnames.get(record_node.lineno, "<module>")
        matches.append(
            (
                record_node.lineno,
                record_node.col_offset,
                attr,
                qualname,
                _expr_text(record_node),
                text,
            )
        )
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

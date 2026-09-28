#!/usr/bin/env python3
"""The per-node matching half of ``fact_field_readers.py``'s
`fact-field-readers` AI-readiness check (ADR-063 Phase 0,
docs/contribute/plans/one-semantic-pipeline.md).

Split out of ``fact_field_readers.unmigrated_fact_reader_sites`` purely to
break up what had grown into one very complex function (cyclomatic
complexity 179 per radon): its three nested closures (``_shadowed``,
``_is_mapping_receiver``, ``_expr_text``) and every branch of its
``ast.walk`` dispatch are methods of :class:`ReaderScan` here, one per
reader form, evaluated in the identical order with the identical
conditions -- a behavior-preserving refactor, not a redesign. The
reasoning behind each form lives in ``unmigrated_fact_reader_sites``'s own
docstring and in the comments carried over verbatim below.

The alias/shadowing *collection* (``_builtins_getattr_aliases`` and
siblings) stays in ``fact_field_readers.py``; it computes a
:class:`ReaderScanContext` once per tree and hands it in, so this module
depends on nothing there (``fact_field_readers.py`` re-exports
``FACT_BRIDGED_ATTRS``/``FACT_BRIDGED_CLASS_NAMES`` from here).

Imported directly by ``fact_field_readers.py``; not meant to be used
standalone. Pure stdlib.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, fields
from pathlib import Path

# Same sys.path guard as ``fact_field_readers.py`` itself, so the sibling
# scope module resolves however this file is loaded.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from fact_field_readers_scope import (  # noqa: E402
    _attrgetter_matched_name,
    _is_attrgetter_constructor_call,
    _is_itemgetter_constructor_call,
    _itemgetter_matched_name,
    _outermost_containing_expr,
    _target_bound_names,
)

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


#: ``(lineno, col_offset, attr, qualname, outer_text, text)`` -- one raw
#: match before ``unmigrated_fact_reader_sites`` sorts and ranks it.
ReaderMatch = tuple[int, int, str, str, str, str]


@dataclass(frozen=True)
class ReaderScanContext:
    """Every per-tree lookup table the scan consults, computed once by
    ``fact_field_readers.unmigrated_fact_reader_sites``."""

    qualnames: Mapping[int, str]
    parents: Mapping[int, ast.AST]
    class_aliases: Mapping[str, str]
    getattr_names: Collection[str]
    builtins_names: Collection[str]
    vars_names: Collection[str]
    mapping_receiver_names: Collection[str]
    dict_names: Collection[str]
    object_type_names: Collection[str]
    unbound_getattribute_names: Collection[str]
    attrgetter_names: Collection[str]
    operator_names: Collection[str]
    getitem_names: Collection[str]
    itemgetter_names: Collection[str]
    itemgetter_alias_keys: Mapping[str, Collection[str]]
    locally_bound: Mapping[str, Collection[str]]
    recognized_alias_scopes: Mapping[str, Collection[str]]
    lexical_parents: Mapping[str, str]


def _lambda_params_shadow(node: ast.Lambda, child: ast.AST, name: str) -> bool:

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
    if child is not node.body:
        return False
    lambda_args = (
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *((node.args.vararg,) if node.args.vararg else ()),
        *((node.args.kwarg,) if node.args.kwarg else ()),
    )
    return any(arg.arg == name for arg in lambda_args)


def _comprehension_targets_shadow(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    gen_clause: ast.comprehension | None,
    via_iter: bool,
    name: str,
) -> bool:

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
    if gen_clause is None:
        shadowing_generators = node.generators
    else:
        gen_index = node.generators.index(gen_clause)
        shadowing_generators = node.generators[
            : gen_index if via_iter else gen_index + 1
        ]
    return any(
        bound_name == name
        for generator in shadowing_generators
        for bound_name in _target_bound_names(generator.target)
    )


class ReaderScan:
    """One scan of one parsed module; :meth:`run` returns its raw matches."""

    def __init__(self, tree: ast.Module, source: str, ctx: ReaderScanContext) -> None:
        self.tree = tree
        self.source = source
        # Flattened onto the instance so the carried-over branch bodies
        # read exactly as they did inside the original closure.
        for field in fields(ctx):
            setattr(self, field.name, getattr(ctx, field.name))
        self.matches: list[ReaderMatch] = []
        self._single_read_forms: tuple[
            Callable[[ast.AST], tuple[str, ast.expr] | None], ...
        ] = (
            self._read_attribute,
            self._read_augassign_attribute,
            self._read_getattr_call,
            self._read_bound_getattribute,
            self._read_unbound_getattribute,
            self._read_getattribute_alias,
            self._read_mapping_subscript,
            self._read_augassign_mapping_subscript,
            self._read_mapping_get,
            self._read_mapping_getitem,
            self._read_dict_getitem,
            self._read_dict_get,
            self._read_operator_getitem,
            self._read_getitem_alias,
        )

    def run(self) -> list[ReaderMatch]:
        for node in ast.walk(self.tree):
            self._visit(node)
        return self.matches

    def _visit(self, node: ast.AST) -> None:
        # Order matters and mirrors the original if/continue chain: the
        # first form that claims a node wins.
        if (
            self._handle_match_class(node)
            or self._handle_attrgetter(node)
            or self._handle_itemgetter_call(node)
            or self._handle_itemgetter_walrus_call(node)
            or self._handle_itemgetter_alias_call(node)
        ):
            return
        for form in self._single_read_forms:
            found = form(node)
            if found is not None:
                attr, record_node = found
                self._record(attr, record_node)
                return

    def _record(self, attr: str, record_node: ast.expr) -> None:
        text = (
            ast.get_source_segment(self.source, record_node) if self.source else None
        ) or "<unavailable>"
        qualname = self.qualnames.get(record_node.lineno, "<module>")
        self.matches.append(
            (
                record_node.lineno,
                record_node.col_offset,
                attr,
                qualname,
                self._expr_text(record_node),
                text,
            )
        )

    def _shadowed(self, call_node: ast.expr, name: str) -> bool:
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
        if self._ancestry_shadows(call_node, name):
            return True
        return self._scope_chain_shadows(call_node, name)

    def _ancestry_shadows(self, call_node: ast.expr, name: str) -> bool:
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
        while id(node) in self.parents:
            child = node
            node = self.parents[id(node)]
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
                if _lambda_params_shadow(node, child, name):
                    return True
            elif isinstance(
                node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                if _comprehension_targets_shadow(
                    node, comprehension_gen_clause, comprehension_via_iter, name
                ):
                    return True
        return False

    def _scope_chain_shadows(self, call_node: ast.expr, name: str) -> bool:
        qualname = self.qualnames.get(call_node.lineno, "<module>")
        # Walk the call's entire lexical scope chain, not just its own
        # innermost function (Codex review, fresh evidence): a nested
        # function that binds no parameter of its own can still have the
        # name shadowed via an ordinary Python closure over an *enclosing*
        # function's own parameter -- see `_lexical_function_parents`'s
        # own docstring for the exact repro.
        while True:
            if name in self.locally_bound.get(qualname, ()):
                return True
            # A recognized alias-source import (`from operator import
            # attrgetter as ag`) resolves the name definitively at this
            # scope -- stop here, unshadowed, rather than continuing to
            # walk outward and potentially finding a completely
            # unrelated same-named binding in an enclosing scope (Codex
            # review, fresh evidence: see `_locally_bound_names()`'s own
            # docstring for the exact repro this closes).
            if name in self.recognized_alias_scopes.get(qualname, ()):
                return False
            if qualname == "<module>":
                return False
            qualname = self.lexical_parents.get(qualname, "<module>")

    def _is_mapping_receiver(self, value: ast.expr) -> bool:
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
                and value.func.id in self.vars_names
                and not self._shadowed(value, value.func.id)
            ):
                return True
            if (
                isinstance(value.func, ast.Attribute)
                and value.func.attr == "vars"
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id in self.builtins_names
                and not self._shadowed(value, value.func.value.id)
            ):
                return True
        if isinstance(value, ast.Attribute) and value.attr == "__dict__":
            return True
        return (
            isinstance(value, ast.Name)
            and value.id in self.mapping_receiver_names
            and not self._shadowed(value, value.id)
        )

    def _expr_text(self, node: ast.AST) -> str:
        outer = _outermost_containing_expr(node, self.parents)
        text = ast.get_source_segment(self.source, outer) if self.source else None
        return text or "<unavailable>"

    def _handle_match_class(self, node: ast.AST) -> bool:
        if not (isinstance(node, ast.MatchClass)):
            return False
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
            ast.get_source_segment(self.source, node) if self.source else None
        ) or "<unavailable>"
        qualname = self.qualnames.get(node.lineno, "<module>")
        for kwd_attr, kwd_pattern in zip(node.kwd_attrs, node.kwd_patterns):
            if kwd_attr not in FACT_BRIDGED_ATTRS:
                continue
            self.matches.append(
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
            self.class_aliases.get(cls_name, cls_name) if cls_name else None
        )
        if node.patterns and resolved_cls_name in FACT_BRIDGED_CLASS_NAMES:
            # A positional pattern can't be resolved to a specific
            # field name without real `__match_args__` introspection
            # (see this function's own docstring) -- report it
            # unconditionally rather than silently missing it.
            self.matches.append(
                (
                    node.lineno,
                    node.col_offset,
                    "<positional>",
                    qualname,
                    class_text,
                    class_text,
                )
            )
        return True

    def _handle_attrgetter(self, node: ast.AST) -> bool:
        if not (
            isinstance(node, ast.Call)
            and _is_attrgetter_constructor_call(
                node, self.attrgetter_names, self.operator_names
            )
            and not self._shadowed(node, _attrgetter_matched_name(node))
        ):
            return False
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
            ast.get_source_segment(self.source, node) if self.source else None
        ) or "<unavailable>"
        outer_text = self._expr_text(node)
        qualname = self.qualnames.get(node.lineno, "<module>")
        for call_arg in node.args:
            if not (
                isinstance(call_arg, ast.Constant) and isinstance(call_arg.value, str)
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
            self.matches.append(
                (
                    node.lineno,
                    node.col_offset,
                    matched_field,
                    qualname,
                    outer_text,
                    text,
                )
            )
        return True

    def _handle_itemgetter_call(self, node: ast.AST) -> bool:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Call)
            and _is_itemgetter_constructor_call(
                node.func, self.itemgetter_names, self.operator_names
            )
            and not self._shadowed(node.func, _itemgetter_matched_name(node.func))
            and len(node.args) == 1
            and self._is_mapping_receiver(node.args[0])
        ):
            return False
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
            ast.get_source_segment(self.source, node) if self.source else None
        ) or "<unavailable>"
        outer_text = self._expr_text(node)
        qualname = self.qualnames.get(node.lineno, "<module>")
        for call_arg in node.func.args:
            if not (
                isinstance(call_arg, ast.Constant)
                and isinstance(call_arg.value, str)
                and call_arg.value in FACT_BRIDGED_ATTRS
            ):
                continue
            self.matches.append(
                (
                    node.lineno,
                    node.col_offset,
                    call_arg.value,
                    qualname,
                    outer_text,
                    text,
                )
            )
        return True

    def _handle_itemgetter_walrus_call(self, node: ast.AST) -> bool:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.NamedExpr)
            and isinstance(node.func.value, ast.Call)
            and _is_itemgetter_constructor_call(
                node.func.value, self.itemgetter_names, self.operator_names
            )
            and not self._shadowed(
                node.func.value, _itemgetter_matched_name(node.func.value)
            )
            and len(node.args) == 1
            and self._is_mapping_receiver(node.args[0])
        ):
            return False
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
            ast.get_source_segment(self.source, node) if self.source else None
        ) or "<unavailable>"
        outer_text = self._expr_text(node)
        qualname = self.qualnames.get(node.lineno, "<module>")
        for call_arg in node.func.value.args:
            if not (
                isinstance(call_arg, ast.Constant)
                and isinstance(call_arg.value, str)
                and call_arg.value in FACT_BRIDGED_ATTRS
            ):
                continue
            self.matches.append(
                (
                    node.lineno,
                    node.col_offset,
                    call_arg.value,
                    qualname,
                    outer_text,
                    text,
                )
            )
        return True

    def _handle_itemgetter_alias_call(self, node: ast.AST) -> bool:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.itemgetter_alias_keys
            and not self._shadowed(node, node.func.id)
            and len(node.args) == 1
            and self._is_mapping_receiver(node.args[0])
        ):
            return False
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
            ast.get_source_segment(self.source, node) if self.source else None
        ) or "<unavailable>"
        outer_text = self._expr_text(node)
        qualname = self.qualnames.get(node.lineno, "<module>")
        for alias_key in self.itemgetter_alias_keys[node.func.id]:
            if alias_key not in FACT_BRIDGED_ATTRS:
                continue
            self.matches.append(
                (
                    node.lineno,
                    node.col_offset,
                    alias_key,
                    qualname,
                    outer_text,
                    text,
                )
            )
        return True

    def _read_attribute(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Attribute)
            and node.attr in FACT_BRIDGED_ATTRS
            and isinstance(node.ctx, ast.Load)
        ):
            return None
        return node.attr, node

    def _read_augassign_attribute(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Attribute)
            and node.target.attr in FACT_BRIDGED_ATTRS
        ):
            return None
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
        return node.target.attr, node.target

    def _read_getattr_call(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id in self.getattr_names
                    and not self._shadowed(node, node.func.id)
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getattr"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in self.builtins_names
                    and not self._shadowed(node, node.func.value.id)
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
                    and node.func.value.id in self.getattr_names
                    and not self._shadowed(node, node.func.value.id)
                )
            )
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            return None
        return node.args[1].value, node

    def _read_bound_getattribute(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattribute__"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            return None
        # `rec.__getattribute__("bases")` -- the bound-method spelling
        # of the same dynamic read `getattr(rec, "bases")` performs,
        # and the one every object's own `getattr()` implementation is
        # defined in terms of (Codex review, fresh evidence).
        return node.args[0].value, node

    def _read_unbound_getattribute(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattribute__"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.object_type_names
            and not self._shadowed(node, node.func.value.id)
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            return None
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
        return node.args[1].value, node

    def _read_getattribute_alias(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.unbound_getattribute_names
            and not self._shadowed(node, node.func.id)
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            return None
        # `read_attr = object.__getattribute__; read_attr(rec,
        # "bases")` -- the unbound method itself lifted out to a
        # plain local (or a chain from there) before being called,
        # rather than called directly off `object`/`type`/an alias of
        # either the way the branch just above matches (Codex review,
        # fresh evidence). Reads `rec.bases` exactly the same way; a
        # local shadowing the alias name is excluded the same way
        # every other dynamic-read branch here already is.
        return node.args[1].value, node

    def _read_mapping_subscript(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and node.slice.value in FACT_BRIDGED_ATTRS
            and self._is_mapping_receiver(node.value)
        ):
            return None
        # `vars(rec)["bases"]` / `rec.__dict__["bases"]` -- both read
        # the normalized legacy value the same way `rec.bases` does,
        # through the instance's own `__dict__` mapping rather than
        # attribute-lookup machinery (Codex review, fresh evidence).
        # Only a literal string key is in scope -- a computed key
        # (`vars(rec)[name]`) can't be resolved statically, the
        # identical "no type inference" limit every other dynamic form
        # here already accepts.
        return node.slice.value, node

    def _read_augassign_mapping_subscript(
        self, node: ast.AST
    ) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Subscript)
            and isinstance(node.target.slice, ast.Constant)
            and isinstance(node.target.slice.value, str)
            and node.target.slice.value in FACT_BRIDGED_ATTRS
            and self._is_mapping_receiver(node.target.value)
        ):
            return None
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
        return node.target.slice.value, node.target

    def _read_mapping_get(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and self._is_mapping_receiver(node.func.value)
            and len(node.args) >= 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            return None
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
        return node.args[0].value, node

    def _read_mapping_getitem(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getitem__"
            and self._is_mapping_receiver(node.func.value)
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in FACT_BRIDGED_ATTRS
        ):
            return None
        # `vars(rec).__getitem__("bases")` -- the explicit dunder-
        # method spelling of the identical subscript read
        # `vars(rec)["bases"]` already catches, the same bound-method
        # relationship `rec.__getattribute__("bases")` already has to
        # `rec.bases` elsewhere in this module (Codex review, fresh
        # evidence).
        return node.args[0].value, node

    def _read_dict_getitem(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getitem__"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.dict_names
            and not self._shadowed(node, node.func.value.id)
            and len(node.args) == 2
            and self._is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            return None
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
        return node.args[1].value, node

    def _read_dict_get(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.dict_names
            and not self._shadowed(node, node.func.value.id)
            and len(node.args) >= 2
            and self._is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            return None
        # `dict.get(vars(rec), "bases")` -- the *unbound*-method
        # spelling of the bound `vars(rec).get("bases")` form above,
        # the identical relationship the unbound `dict.__getitem__`
        # branch already has to its own bound sibling (Codex review,
        # fresh evidence). An optional third argument (the default)
        # is accepted but not inspected, matching the bound form's
        # own identical treatment.
        return node.args[1].value, node

    def _read_operator_getitem(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getitem"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.operator_names
            and not self._shadowed(node, node.func.value.id)
            and len(node.args) == 2
            and self._is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            return None
        # `operator.getitem(vars(rec), "bases")` -- the standard-
        # library callable spelling of the identical subscript read,
        # via a real `operator` module alias (`operator_names`, the
        # same resolved set `attrgetter`'s own module-qualified form
        # already uses) (Codex review, fresh evidence).
        return node.args[1].value, node

    def _read_getitem_alias(self, node: ast.AST) -> tuple[str, ast.expr] | None:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.getitem_names
            and not self._shadowed(node, node.func.id)
            and len(node.args) == 2
            and self._is_mapping_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in FACT_BRIDGED_ATTRS
        ):
            return None
        # `from operator import getitem as gi; gi(vars(rec),
        # "bases")` -- the bare-name spelling of the identical
        # standard-library callable read, via a real `getitem` import
        # alias (`getitem_names`, resolved the same way `attrgetter_
        # names` already is) rather than the qualified `operator.
        # getitem(...)` form the branch above matches (Codex review,
        # fresh evidence).
        return node.args[1].value, node

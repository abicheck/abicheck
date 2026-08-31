# AGENTS.md — `abicheck/model/`

## Purpose

This package owns the *shapes* every other responsibility package agrees on:
what an ABI entity, a captured binary/debug fact, and a whole snapshot are,
per ADR-061 D1. It is the innermost ring — it answers "what is this fact"
and never "how was it produced", "does it differ", "does it matter", or
"how is it rendered".

**One deliberate, ADR-061 D9-sanctioned exception: `change_catalog/`.** D9's
own migration table draws a data/algorithm split for the change registry —
"declarative `model/change_catalog`; classification algorithms in
`policy`" — and that is the line this package actually holds to, not a
blanket carve-out. `ChangeKindMeta.default_verdict`/`policy_overrides`/
`impact`/`description_template` are declarative *data* (a lookup table and
report-template strings), not the *code* that walks them to decide or
render — `checker_policy.py`'s `policy_kind_sets()` and friends do that
walking, and are slated to move to a `policy` package themselves once
Phase 2-4 reaches them (still flat today). Read "never answers... does it
matter... how is it rendered" as about *algorithms*, not about *any field
whose name sounds policy/report-shaped* — the catalog itself is exactly the
kind of "what is this fact" (which `ChangeKind` defaults to which verdict,
under which override) D1 already assigns here.

## Permitted imports

Per ADR-061 D1, `model/` may import **nothing** first-party except the public
root surfaces (`abicheck.api_types`, `abicheck.errors`) and, transitionally,
the flat leaf modules classified `model` in `architecture/modules.yaml`.
Importing `extract`, `compare`, `policy`, `workflows`, `report` or
`frontends` is a defect, not a case for an exception list —
`scripts/check_architecture.py` enforces it.

That constraint is what the `*_facts` modules exist for: a `*_metadata.py`
parser used to own both the dataclass and the code that fills it in, so
anything holding an `AbiSnapshot` field type dragged in an extractor. The
dataclass halves live here; each parser imports and re-exports its own
types, so `from abicheck.elf_metadata import ElfMetadata` still resolves.

## Where a change goes

| Change | Module |
|---|---|
| A new enum value describing an entity | `vocabulary.py` |
| A field on a function, variable, or parameter | `declarations.py` |
| A field on a record, enum, or type field | `entities.py` |
| A comparability fingerprint or dependency ledger field | `extraction_contract.py` |
| A new snapshot-level field or layer attachment | `snapshot.py` |
| A new ELF/PE/Mach-O/DWARF/SYCL/kABI fact | the matching `*_facts.py` |
| A snapshot-aware surface predicate | `stdlib_surface.py` |
| An L5 source-graph value field, node-id spelling, or schema vocabulary entry (`NODE_KINDS`/`EDGE_KINDS`) | `source_graph.py` |
| An L5 `GraphNode`/`GraphEdge` field, or the ADR-046 fact-merge machinery | `graph_facts.py` |
| An L5 confidence label or `*_NODE_KINDS`/`*_EDGE_KINDS` family vocabulary set | `graph_vocabulary.py` |
| A decl/type node-id normalization rule | `graph_identity.py` |
| A `EntityResolver`/canonical-identity resolution rule | `entity_resolver.py`, `entity_identity.py` |
| An already-built L5 graph node/edge public/internal/consumer-compiled classification predicate | `source_graph_query.py` |

`__init__.py` is the supported import surface and stays a re-export list
with `__all__` — no logic, no new names that are not owned by a submodule.

## Rules that are easy to get wrong

- **Adding a field to `AbiSnapshot` is a storage event.** `storage`/
  `serialization.py` must round-trip it and the schema version must move;
  a field that only exists in memory reads as data loss on reload.
- **Never add `frozen=True` to a fact class carrying `@cached_property`**
  (`ElfMetadata`, `PeMetadata`, `MachoMetadata`): the cache needs a
  writable instance `__dict__`.
- **Append new fields at the end, keyword-only where a default is needed.**
  These dataclasses are public API; inserting mid-list silently changes what
  every positional caller — including callers this repository cannot see —
  constructs.
- **`AbiSnapshot.index()` is idempotent and does not rebuild.** A caller
  mutating the collections in place must reset `_func_by_mangled`,
  `_var_by_mangled` and `_type_by_name` to `None` together.

### Fixed

- **`dumper_hybrid._merge_enum_type` silently reverted its own clang
  backfill** for `EnumType.is_scoped`/`deprecated` once those fields became
  `Fact[T]`-bridged: it applied its updates with a bare
  `dataclasses.replace(e, **updates)`, so `__post_init__` saw the stale
  castxml-side sibling and resolved in its favour — a clang-supplied
  deprecation message read back as a *confirmed* "not deprecated". Now uses
  `replace_with_fact_sync()`, like the other four merge paths.
  `tests/test_fact_bridged_replace_guard.py` (new) states the rule for
  every call site instead of leaving it to the next reader's grep: a
  repo-wide AST scan flagging any `dataclasses.replace()` that writes a
  registry-known bridged field without its sibling, **including the
  `**kwargs` spelling** in which no field name appears in the source — the
  exact spelling that hid this one from a name-based sweep.
- **A legacy non-header snapshot claimed facts its producer never
  collected.** Every `*_facts_reliable` flag resolves `True` when
  `from_headers` is `False` (the producer it describes never ran), which
  correctly answers "is this value a wrong placeholder" and wrongly answers
  "did anyone observe it" — so a pre-conversion DWARF/PDB/symbols-only
  document's `deprecated: null`, `is_restrict: false` and `access:
  "public"` were bridged to `PRESENT`, while the *fresh* equivalent of the
  same snapshot reports `NOT_COLLECTED`. `apply_case_a_fact_backfill` now
  also downgrades a fact whose producers (read from `FACT_REGISTRY`, not a
  second hand-maintained list) are header-AST-only when the document is not
  `from_headers`. It narrows the claim, never the value: a non-resting
  value is left alone, since discarding it would lose real data.
